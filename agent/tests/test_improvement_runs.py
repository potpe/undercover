import traceback
from collections import Counter
from contextvars import ContextVar

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.types import Command
from sqlalchemy import select

from agent import dispatcher, improver, main, service
from agent.tests.test_improver import StubModel, local_api, proposal
from backend.database import connect, runs_table
from backend.improvement import (
    create_round,
    get_round,
    record_improver_usage,
    stop_round,
)
from backend.main import app
from backend.models import InternalProposalDecision


def stub_claims(monkeypatch):
    local_api(monkeypatch)
    monkeypatch.setattr(
        dispatcher,
        "backend",
        lambda: TestClient(
            app,
            headers={
                "X-API-Key": "undercover-e2e-agent-key-32-characters",
            },
        ),
    )

    def claim(claim_id, thread_id, run_id, run_kind):
        service.post_backend(
            f"/api/claims/{claim_id}/decision",
            {
                "decision": "decline",
                "payout": 0,
                "reason": "The policy excludes this loss.",
                "section": "Section 9",
                "decided_by": "UnderCover Agent (configuration version 1)",
            },
        )
        return "Declined"

    monkeypatch.setattr(service, "claim_run", claim)

    def unexpected_error(item, error):
        raise AssertionError("".join(traceback.format_exception(error))) from error

    monkeypatch.setattr(service, "handle_work_error", unexpected_error)


def round_runs(round_id):
    with connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                select(runs_table).where(runs_table.c.round_id == round_id)
            ).mappings()
        ]


def test_baseline_reuses_three_qualifying_runs_and_candidate_pins_new_version(
    client, monkeypatch
):
    stub_claims(monkeypatch)
    first = create_round("HND-01")["round_id"]
    service.acquire_slot(f"improver round {first}")
    try:
        service.baseline(first)
    except RuntimeError as error:
        raise AssertionError(round_runs(first)) from error
    finally:
        service._run_slot.release()
    baseline_runs = round_runs(first)
    assert Counter(row["evaluation_set"] for row in baseline_runs) == {
        "evaluation": 1,
        "held_out": 2,
    }
    assert all(
        row["status"] == "completed" and row["configuration_version"] == 1
        for row in baseline_runs
    )
    assert get_round(first)["progress"] == 3
    stop_round(first)
    second = create_round("HND-01")["round_id"]
    service.baseline(second)
    assert round_runs(second) == []
    assert get_round(second)["progress"] == 3
    model = StubModel(
        responses=[
            proposal(),
            AIMessage(
                content="",
                tool_calls=[{"id": "evaluate", "name": "run_evaluation", "args": {}}],
            ),
            AIMessage(content="Finished"),
        ]
    )
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    service.invoke_improver(second, None)
    pending = get_round(second)["proposals"][0]
    service.resume_round(
        second,
        InternalProposalDecision(
            proposal_id=pending["proposal_id"],
            decision="approve",
            reason="",
            handler_id="HND-01",
        ),
        BackgroundTasks(),
    )
    service.round_in_background(
        second, Command(resume={"decisions": [{"type": "approve"}]})
    )
    candidate_runs = round_runs(second)
    assert Counter(row["evaluation_set"] for row in candidate_runs) == {
        "evaluation": 1,
        "held_out": 2,
    }
    assert all(
        row["configuration_version"] == 2
        and row["round_purpose"] == "candidate"
        and row["concurrency"] == 25
        for row in candidate_runs
    )
    assert get_round(second)["status"] == "finished"
    assert get_round(second)["verdict"] == "rolled_back"


def test_baseline_executes_only_the_missing_qualifying_evaluation(client, monkeypatch):
    local_api(monkeypatch)
    first = create_round("HND-01")["round_id"]
    for evaluation_set in ("evaluation", "held_out"):
        run = client.post(
            "/api/internal/evaluations",
            json={
                "concurrency": 25,
                "configuration_version": 1,
                "model_name": "openai/gpt-6-luna",
                "reasoning_effort": "low",
                "round_id": first,
                "round_purpose": "baseline",
                "evaluation_set": evaluation_set,
                "tiny": False,
            },
        ).json()
        client.post(f"/api/internal/runs/{run['run_id']}/finish")
    stop_round(first)
    second = create_round("HND-01")["round_id"]
    calls = []
    monkeypatch.setattr(
        service,
        "evaluate_round_set",
        lambda *args: calls.append(args) or "completed",
    )

    service.baseline(second)

    assert calls == [(second, 1, "held_out", "baseline")]
    assert get_round(second)["progress"] == 3


def test_running_evaluation_stops_at_budget_without_extra_claim_attempts(
    client, monkeypatch
):
    stub_claims(monkeypatch)
    client.put(
        "/api/settings",
        json={
            key: value
            for key, value in {
                **client.get("/api/settings").json(),
                "evaluation_concurrency": 1,
            }.items()
            if key != "models_allowed"
        },
    )
    round_id = create_round("HND-01")["round_id"]
    started = []

    def spend_limit(claim_id, thread_id, run_id, run_kind):
        started.append(claim_id)
        record_improver_usage(
            round_id,
            {
                "call_id": "spend",
                "model_name": "stub",
                "reasoning_effort": "default",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "cost": 0.5,
            },
        )
        service.post_backend(
            f"/api/claims/{claim_id}/decision",
            {
                "decision": "decline",
                "payout": 0,
                "reason": "Not covered",
                "section": "Section 9",
                "decided_by": "UnderCover Agent (configuration version 1)",
            },
        )
        return "Declined"

    monkeypatch.setattr(service, "claim_run", spend_limit)
    assert (
        service.evaluate_round_set(round_id, 1, "evaluation", "baseline") == "stopped"
    )
    assert len(started) == 1
    run = client.get(f"/api/internal/runs/{round_runs(round_id)[0]['run_id']}").json()
    assert run["status"] == "stopped"
    assert all(item["attempts"] == 0 for item in run["claims"])


def test_round_evaluation_uses_a_fresh_context_and_keeps_both_trace_tags(monkeypatch):
    inherited = ContextVar("improver-context", default="not inherited")
    inherited.set("outer improver span")
    payloads = []
    observed = []
    monkeypatch.setattr(
        service.improver_api,
        "check_budget",
        lambda _: {"settings": {"evaluation_concurrency": 7}},
    )
    monkeypatch.setattr(
        service,
        "get_backend",
        lambda _: {
            "version": 2,
            "model_name": "openai/gpt-6-luna",
            "reasoning_effort": "low",
        },
    )

    def create_run(path, payload):
        payloads.append((path, payload))
        return {"run_id": 42}

    monkeypatch.setattr(service, "post_backend", create_run)
    monkeypatch.setattr(
        service,
        "execute_stored_run",
        lambda _: observed.append(inherited.get()) or "completed",
    )

    assert service.evaluate_round_set(9, 2, "held_out", "candidate") == "completed"
    assert observed == ["not inherited"]
    assert payloads == [
        (
            "/api/internal/evaluations",
            {
                "concurrency": 7,
                "configuration_version": 2,
                "model_name": "openai/gpt-6-luna",
                "reasoning_effort": "low",
                "round_id": 9,
                "round_purpose": "candidate",
                "evaluation_set": "held_out",
                "tiny": False,
            },
        )
    ]
    assert main.version_tags({"round_id": 9}, run_id=42, run_kind="evaluation") == [
        "evaluation-42",
        "round-9",
    ]
