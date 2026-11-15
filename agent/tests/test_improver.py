import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
)
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.types import Command

from agent import improver, improver_api, main, service
from backend.improvement import (
    create_round,
    get_round,
    record_improver_usage,
    stop_round,
)
from backend.main import app
from backend.models import DispatchRequest, InternalProposalDecision


class StubModel(FakeMessagesListChatModel):
    model_name: str = "stub-improver"
    reasoning_effort: str = "medium"

    def bind_tools(self, tools, **kwargs):
        return self


def proposal(instructions="Use general policy rules."):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": f"proposal-{instructions}",
                "name": "propose_change",
                "args": {
                    "instructions": instructions,
                    "model": "openai/gpt-6-luna",
                    "reasoning_effort": "low",
                    "reason": "Improve accuracy",
                },
            }
        ],
    )


def recipe_proposal(recipe, call_id="recipe"):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": call_id,
                "name": "propose_recipe",
                "args": {"recipe": recipe, "reason": "Make the process clearer"},
            }
        ],
    )


def local_api(monkeypatch):
    def request(method, path, body=None):
        result = TestClient(
            app,
            headers={
                "X-API-Key": "undercover-e2e-agent-key-32-characters",
            },
        ).request(method, path, json=body)
        response = httpx.Response(
            result.status_code,
            json=result.json(),
            request=httpx.Request(method, f"http://testserver{path}"),
        )
        response.raise_for_status()
        return response.json()

    monkeypatch.setattr(improver_api, "request", request)
    monkeypatch.setattr(
        service,
        "backend",
        lambda: TestClient(
            app,
            headers={
                "X-API-Key": "undercover-e2e-agent-key-32-characters",
            },
        ),
    )
    monkeypatch.setattr(
        main,
        "backend",
        lambda: TestClient(
            app,
            headers={
                "X-API-Key": "undercover-e2e-agent-key-32-characters",
            },
        ),
    )
    monkeypatch.setattr(
        service, "post_backend", lambda path, body=None: request("POST", path, body)
    )
    monkeypatch.setattr(service, "get_backend", lambda path: request("GET", path))


def test_durable_approval_creates_no_version_until_resumed(client, monkeypatch):
    local_api(monkeypatch)
    model = StubModel(responses=[proposal(), AIMessage(content="Finished")])
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    record = create_round("HND-01")
    round_id = record["round_id"]
    service.invoke_improver(round_id, None)
    waiting = get_round(round_id)
    assert waiting["stage"] == "configuration_approval"
    assert waiting["proposals"][0]["status"] == "pending"
    assert len(client.get("/api/configuration").json()["versions"]) == 1

    tasks = BackgroundTasks()
    service.resume_round(
        round_id,
        InternalProposalDecision(
            proposal_id=waiting["proposals"][0]["proposal_id"],
            decision="approve",
            reason="",
            handler_id="HND-01",
        ),
        tasks,
    )
    service.round_in_background(
        round_id, Command(resume={"decisions": [{"type": "approve"}]})
    )
    final = get_round(round_id)
    assert final["status"] == "finished"
    assert final["candidate_version"] == 2
    assert final["proposals"][0]["decided_by"] == "HND-01"
    assert final["verdict"] == "rolled_back"
    assert client.get("/api/configuration").json()["current_version"] == 1


def test_rejection_reaches_the_durable_improver_and_it_can_propose_again(
    client, monkeypatch
):
    local_api(monkeypatch)
    model = StubModel(
        responses=[
            proposal(),
            proposal("A clearer general rule."),
            AIMessage(content="Finished"),
        ]
    )
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    round_id = create_round("HND-01")["round_id"]
    service.invoke_improver(round_id, None)
    first = get_round(round_id)["proposals"][0]
    reason = "Explain the evidence without naming a claim"
    service.resume_round(
        round_id,
        InternalProposalDecision(
            proposal_id=first["proposal_id"],
            decision="reject",
            reason=reason,
            handler_id="HND-01",
        ),
        BackgroundTasks(),
    )
    service.round_in_background(
        round_id, Command(resume={"decisions": [{"type": "reject", "message": reason}]})
    )
    current = get_round(round_id)
    assert [item["status"] for item in current["proposals"]] == ["rejected", "pending"]
    assert current["proposals"][0]["decision_reason"] == reason
    with service.improver_checkpointer() as saver:
        graph = improver.make_improver(current, "Recipe", saver, lambda _: {})
        state = graph.get_state({"configurable": {"thread_id": current["thread_id"]}})
        assert reason in str(state.values["messages"])
    assert len(client.get("/api/configuration").json()["versions"]) == 1


@pytest.mark.parametrize("run_kind", ["dispatch", "evaluation", "improver"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_approval_refused_while_run_active_records_nothing(
    client, monkeypatch, run_kind, decision
):
    local_api(monkeypatch)
    model = StubModel(responses=[proposal()])
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    round_id = create_round("HND-01")["round_id"]
    service.invoke_improver(round_id, None)
    waiting = get_round(round_id)
    if run_kind == "dispatch":
        active = service.start_dispatch(
            DispatchRequest(concurrency=1), BackgroundTasks()
        )
        active_name = f"dispatch {active.run_id}"
    elif run_kind == "evaluation":
        active = service.start_evaluation(
            service.EvaluationRequest(
                concurrency=1, evaluation_set="evaluation", tiny=True
            ),
            BackgroundTasks(),
        )
        active_name = f"evaluation {active.run_id}"
    else:
        service.acquire_slot(f"improver round {round_id}")
        active = None
        active_name = f"improver round {round_id}"
    try:
        with pytest.raises(Exception, match=f"{active_name} is running"):
            service.start_round(
                service.StartRoundRequest(handler_id="HND-01"), BackgroundTasks()
            )
        with pytest.raises(Exception, match=f"{active_name} is running"):
            service.resume_round(
                round_id,
                InternalProposalDecision(
                    proposal_id=waiting["proposals"][0]["proposal_id"],
                    decision=decision,
                    reason="Use a general rule" if decision == "reject" else "",
                    handler_id="HND-01",
                ),
                BackgroundTasks(),
            )
        assert get_round(round_id)["proposals"][0]["status"] == "pending"
        with service.improver_checkpointer() as saver:
            record = get_round(round_id)
            graph = improver.make_improver(
                record,
                improver_api.read_recipe(round_id)["recipe"],
                saver,
                lambda _: {},
            )
            state = graph.get_state(
                {"configurable": {"thread_id": record["thread_id"]}}
            )
        assert len(state.tasks[0].interrupts) == 1
    finally:
        if active is not None:
            service.post_backend(
                f"/api/internal/runs/{active.run_id}/stop", {"error": "test stop"}
            )
        service._run_slot.release()


def test_waiting_round_releases_the_slot_for_dispatch_and_evaluation(
    client, monkeypatch
):
    local_api(monkeypatch)
    model = StubModel(responses=[proposal()])
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    record = create_round("HND-01")
    service.post_backend(
        f"/api/internal/rounds/{record['round_id']}/stage",
        {"stage": "working", "progress": 3},
    )
    service.acquire_slot(f"improver round {record['round_id']}")

    service.round_in_background(record["round_id"])

    assert get_round(record["round_id"])["stage"] == "configuration_approval"
    assert not service._run_slot.locked()
    dispatch = service.start_dispatch(DispatchRequest(concurrency=1), BackgroundTasks())
    try:
        assert service._run_slot.locked()
    finally:
        service.post_backend(
            f"/api/internal/runs/{dispatch.run_id}/stop", {"error": "test stop"}
        )
        service._run_slot.release()
    evaluation = service.start_evaluation(
        service.EvaluationRequest(
            concurrency=1, evaluation_set="evaluation", tiny=True
        ),
        BackgroundTasks(),
    )
    try:
        assert evaluation.run_id != dispatch.run_id
        assert service._run_slot.locked()
    finally:
        service._run_slot.release()


def test_invalid_model_is_rejected_without_handler_proposal(client, monkeypatch):
    local_api(monkeypatch)
    invalid = proposal()
    invalid.tool_calls[0]["args"]["model"] = "not-allowed"
    model = StubModel(responses=[invalid, proposal()])
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    round_id = create_round("HND-01")["round_id"]
    service.invoke_improver(round_id, None)
    assert len(get_round(round_id)["proposals"]) == 1
    assert (
        get_round(round_id)["proposals"][0]["payload"]["model"] == "openai/gpt-6-luna"
    )
    with service.improver_checkpointer() as saver:
        record = get_round(round_id)
        graph = improver.make_improver(record, "Recipe", saver, lambda _: {})
        state = graph.get_state({"configurable": {"thread_id": record["thread_id"]}})
    assert "choose from models_allowed" in str(state.values["messages"])


def test_start_and_resume_preserve_backend_conflicts_and_release_slot(
    client, monkeypatch
):
    local_api(monkeypatch)
    record = create_round("HND-01")
    with pytest.raises(HTTPException) as start_error:
        service.start_round(
            service.StartRoundRequest(handler_id="HND-01"), BackgroundTasks()
        )
    assert getattr(start_error.value, "status_code", None) == 409
    assert not service._run_slot.locked()
    with pytest.raises(HTTPException) as resume_error:
        service.resume_round(
            record["round_id"],
            InternalProposalDecision(
                proposal_id=999, decision="approve", reason="", handler_id="HND-01"
            ),
            BackgroundTasks(),
        )
    assert getattr(resume_error.value, "status_code", None) == 409
    assert not service._run_slot.locked()


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_restart_resumes_decision_recorded_before_checkpoint_resume(
    client, monkeypatch, decision
):
    local_api(monkeypatch)
    monkeypatch.setattr(
        improver,
        "make_model",
        lambda record: StubModel(responses=[proposal(), AIMessage(content="Finished")]),
    )
    round_id = create_round("HND-01")["round_id"]
    service.invoke_improver(round_id, None)
    waiting = get_round(round_id)
    service.post_backend(
        f"/api/internal/rounds/{round_id}/decision",
        {
            "proposal_id": waiting["proposals"][0]["proposal_id"],
            "decision": decision,
            "reason": "Use a clearer rule" if decision == "reject" else "",
            "handler_id": "HND-01",
        },
    )
    monkeypatch.setattr(
        improver,
        "make_model",
        lambda record: StubModel(responses=[AIMessage(content="Finished")]),
    )
    service.recover_work()
    final = get_round(round_id)
    assert final["status"] == "finished"
    assert final["candidate_version"] == (2 if decision == "approve" else None)
    assert not service._run_slot.locked()


def test_recipe_wait_survives_reconstruction_and_only_approval_changes_recipe(
    client, monkeypatch
):
    local_api(monkeypatch)
    recipe_message = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "recipe",
                "name": "propose_recipe",
                "args": {
                    "recipe": "Read the mistakes, then propose a general rule.",
                    "reason": "Make improvement clearer",
                },
            }
        ],
    )
    monkeypatch.setattr(
        improver, "make_model", lambda record: StubModel(responses=[recipe_message])
    )
    round_id = create_round("HND-01")["round_id"]
    service.invoke_improver(round_id, None)
    assert client.get("/api/recipes").json()["current_version"] == 1
    service.recover_work()
    assert get_round(round_id)["stage"] == "recipe_approval"
    proposal_id = get_round(round_id)["proposals"][0]["proposal_id"]
    monkeypatch.setattr(
        improver,
        "make_model",
        lambda record: StubModel(responses=[AIMessage(content="Finished")]),
    )
    service.resume_round(
        round_id,
        InternalProposalDecision(
            proposal_id=proposal_id, decision="approve", reason="", handler_id="HND-01"
        ),
        BackgroundTasks(),
    )
    service.round_in_background(
        round_id, Command(resume={"decisions": [{"type": "approve"}]})
    )
    history = client.get("/api/recipes").json()
    assert history["current_version"] == 2
    assert history["versions"][1]["approved_by"] == "HND-01"
    assert history["versions"][1]["diff"][-1] == {
        "kind": "added",
        "text": "Read the mistakes, then propose a general rule.",
    }
    assert get_round(round_id)["recipe_version"] == 1
    assert get_round(round_id)["status"] == "finished"


def test_improver_records_only_reported_usage_and_stops_before_next_call(
    client, monkeypatch
):
    local_api(monkeypatch)
    reply = proposal()
    reply.response_metadata = {
        "token_usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30,
            "cost": 0.5,
        }
    }
    model = StubModel(responses=[reply, AIMessage(content="Must not run")])
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    round_id = create_round("HND-01")["round_id"]
    service.invoke_improver(round_id, None)
    assert get_round(round_id)["cost"] == 0.5
    pending = get_round(round_id)["proposals"][0]
    service.resume_round(
        round_id,
        InternalProposalDecision(
            proposal_id=pending["proposal_id"],
            decision="reject",
            reason="Improve this",
            handler_id="HND-01",
        ),
        BackgroundTasks(),
    )
    service.round_in_background(
        round_id,
        Command(resume={"decisions": [{"type": "reject", "message": "Improve this"}]}),
    )
    assert get_round(round_id)["status"] == "finished"
    assert model.i == 1


def test_model_configuration_and_four_tools_follow_the_brief(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub-not-a-real-key")
    record = create_round("HND-01")
    selected = improver.make_model(record)
    assert selected.model_name == "openai/gpt-6-luna"
    assert selected.reasoning_effort == "medium"
    assert selected.model_kwargs["parallel_tool_calls"] is False
    assert selected.model_dump()["max_tokens"] == 7152
    assert improver.MAX_MODEL_CALLS == 30
    assert {
        tool.name for tool in improver.make_tools(record["round_id"], lambda _: {})
    } == {"read_results", "propose_change", "run_evaluation", "propose_recipe"}
    record["settings"]["models_allowed"] = [
        {
            "model_name": "openrouter/free",
            "reasoning_efforts": ["default"],
            "input_price": 0,
            "output_price": 0,
        }
    ]
    free = improver.make_model(record)
    assert free.model_name == "openrouter/free"
    assert free.reasoning_effort is None


def test_construction_wraps_the_recipe_and_uses_the_round_saver(client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "stub-not-a-real-key")
    record = create_round("HND-01")
    saver = object()
    captured = {}

    def fake_create_agent(**settings):
        captured.update(settings)
        return "graph"

    monkeypatch.setattr(improver, "create_agent", fake_create_agent)
    assert (
        improver.make_improver(
            record, "Read, evaluate, and improve.", saver, lambda _: {}
        )
        == "graph"
    )

    assert (
        "You improve UnderCover's claim agent configuration."
        in captured["system_prompt"]
    )
    assert "# Your recipe\nRead, evaluate, and improve." in captured["system_prompt"]
    assert captured["checkpointer"] is saver
    assert any(
        isinstance(item, ModelCallLimitMiddleware) for item in captured["middleware"]
    )
    assert any(
        isinstance(item, HumanInTheLoopMiddleware) for item in captured["middleware"]
    )


def test_recipe_rejection_returns_to_the_same_checkpoint(client, monkeypatch):
    local_api(monkeypatch)
    model = StubModel(
        responses=[
            recipe_proposal("Add a general claim-handling step.", "first"),
            recipe_proposal("Review the results before proposing.", "second"),
        ]
    )
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    round_id = create_round("HND-01")["round_id"]
    service.invoke_improver(round_id, None)
    first = get_round(round_id)["proposals"][0]
    reason = "Keep the recipe concise and tied to the evaluation evidence"

    service.resume_round(
        round_id,
        InternalProposalDecision(
            proposal_id=first["proposal_id"],
            decision="reject",
            reason=reason,
            handler_id="HND-01",
        ),
        BackgroundTasks(),
    )
    service.round_in_background(
        round_id, Command(resume={"decisions": [{"type": "reject", "message": reason}]})
    )

    current = get_round(round_id)
    assert [item["status"] for item in current["proposals"]] == [
        "rejected",
        "pending",
    ]
    assert current["proposals"][0]["decision_reason"] == reason
    assert client.get("/api/recipes").json()["current_version"] == 1
    with service.improver_checkpointer() as saver:
        graph = improver.make_improver(current, "Recipe", saver, lambda _: {})
        state = graph.get_state({"configurable": {"thread_id": current["thread_id"]}})
    assert reason in str(state.values["messages"])


def test_call_cap_finishes_a_round_without_fabricating_a_proposal(client, monkeypatch):
    local_api(monkeypatch)
    monkeypatch.setattr(improver, "MAX_MODEL_CALLS", 2)
    replies: list[BaseMessage] = [
        AIMessage(
            content="",
            tool_calls=[{"id": f"read-{index}", "name": "read_results", "args": {}}],
        )
        for index in range(3)
    ]
    model = StubModel(responses=replies)
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    round_id = create_round("HND-01")["round_id"]
    service.invoke_improver(round_id, None)
    assert model.i == 2
    assert get_round(round_id)["status"] == "finished"
    assert get_round(round_id)["candidate_version"] is None
    assert get_round(round_id)["proposals"] == []


def test_stopped_round_does_not_call_the_improver_model(client, monkeypatch):
    local_api(monkeypatch)
    model = StubModel(responses=[AIMessage(content="Must not run")])
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    round_id = create_round("HND-01")["round_id"]
    stop_round(round_id)

    with pytest.raises(httpx.HTTPStatusError):
        service.invoke_improver(round_id, None)
    assert model.i == 0


def test_round_stopped_before_its_stretch_releases_the_slot_without_work(
    client, monkeypatch
):
    local_api(monkeypatch)
    model = StubModel(responses=[AIMessage(content="Must not run")])
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    assert not service._run_slot.locked()

    record = service.start_round(
        service.StartRoundRequest(handler_id="HND-01"), BackgroundTasks()
    )
    assert service._run_slot.locked()
    stop_round(record.round_id)
    service.round_in_background(record.round_id)

    assert get_round(record.round_id)["status"] == "stopped"
    assert client.get("/api/evaluations").json() == []
    assert model.i == 0
    assert not service._run_slot.locked()


def test_unaffordable_baseline_finishes_without_claims_or_improver_calls(
    client, monkeypatch
):
    local_api(monkeypatch)
    model = StubModel(responses=[AIMessage(content="Must not run")])
    monkeypatch.setattr(improver, "make_model", lambda record: model)
    round_id = create_round("HND-01")["round_id"]
    record_improver_usage(
        round_id,
        {
            "call_id": "exhaust-budget",
            "model_name": "stub",
            "reasoning_effort": "default",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "cost": 0.5,
        },
    )
    service.acquire_slot(f"improver round {round_id}")

    service.round_in_background(round_id)

    finished = get_round(round_id)
    assert finished["status"] == "finished"
    assert finished["candidate_version"] is None
    assert "409 Conflict" in finished["finish_reason"]
    assert client.get("/api/evaluations").json() == []
    assert model.i == 0
    assert not service._run_slot.locked()
