from threading import Barrier
from time import perf_counter
from types import SimpleNamespace

import pytest

from backend import judge


def response():
    return SimpleNamespace(
        answers={
            "grade": SimpleNamespace(
                score=1.51, probabilities={"0": 0.01, "1": 0.49, "2": 0.48, "3": 0.02}
            ),
            "same_grounds": SimpleNamespace(noul=0.75),
            "same_section": SimpleNamespace(noul=0.25),
        },
        usage=SimpleNamespace(cost=0.001),
    )


def test_judge_uses_most_likely_letter_and_reported_cost(monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            self.alpha = SimpleNamespace(decisions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            calls.append(kwargs)
            return response()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(judge, "OpenRouter", Client)
    result = judge.grade_claim(
        {
            "handler": {
                "decision": "approve",
                "reason": "Covered",
                "reference": "Section 3",
            },
            "agent": {
                "decision": "approve",
                "reason": "Covered",
                "section": "Section 3",
            },
        }
    )
    assert result["grade"] == 1.51
    assert result["letter"] == "C"
    assert result["same_grounds"] == 0.75
    assert result["same_section"] == 0.25
    assert result["judge_cost"] == 0.001
    assert calls[0]["model"] == "typesafe/jev-1.13"
    assert calls[0]["server_url"] == judge.JUDGE_ORIGIN
    assert calls[0]["timeout_ms"] == 10_000
    assert calls[0]["retries"].strategy == "none"
    assert set(calls[0]["questions"]) == {"grade", "same_grounds", "same_section"}


def create_evaluation(client):
    return client.post(
        "/api/internal/evaluations",
        json={
            "concurrency": 5,
            "configuration_version": None,
            "round_id": None,
            "round_purpose": None,
            "model_name": "openai/gpt-6-luna",
            "reasoning_effort": "low",
            "evaluation_set": "evaluation",
            "tiny": True,
        },
    ).json()


def test_judging_handles_undecided_failed_and_graded_claims(client, monkeypatch):
    run = create_evaluation(client)
    for index, item in enumerate(run["claims"][:2]):
        client.post(
            f"/api/claims/{item['claim_id']}/decision",
            json={
                "decision": "decline",
                "payout": 0,
                "reason": f"Not covered {index}",
                "policy_section": "Section 9",
                "decided_by": "UnderCover Agent",
            },
        )

    def grade(state):
        if state["agent"]["reason"] == "Not covered 1":
            raise TimeoutError("Judge timed out")
        return {
            "grade": 2.0,
            "letter": "B",
            "same_grounds": 0.9,
            "same_section": 0.8,
            "judge_cost": 0.001,
        }

    monkeypatch.setattr(judge, "grade_claim", grade)
    client.post(f"/api/internal/runs/{run['run_id']}/finish")
    result = client.get(f"/api/evaluations/{run['run_id']}").json()
    rows = result["claim_comparisons"]
    assert sum(row["letter"] == "F" for row in rows) == 3
    assert sum(row["letter"] == "B" for row in rows) >= 1
    assert sum(row["grade"] is None for row in rows) == 1
    graded = [row["grade"] for row in rows if row["grade"] is not None]
    assert result["measures"]["grade"] == pytest.approx(sum(graded) / len(graded))
    assert result["measures"]["judge_cost"] == pytest.approx(
        sum(row["judge_cost"] for row in rows)
    )


def test_parallel_judge_calls_finish_together(client, monkeypatch):
    run = create_evaluation(client)
    for item in run["claims"]:
        client.post(
            f"/api/claims/{item['claim_id']}/decision",
            json={
                "decision": "decline",
                "payout": 0,
                "reason": "Not covered",
                "policy_section": "Section 9",
                "decided_by": "UnderCover Agent",
            },
        )
    barrier = Barrier(5)

    def work(value):
        barrier.wait(timeout=2)
        return {
            "grade": 2.0,
            "letter": "B",
            "same_grounds": 0.9,
            "same_section": 0.8,
            "judge_cost": 0.001,
        }

    started = perf_counter()
    monkeypatch.setattr(judge, "grade_claim", work)
    result = client.post(f"/api/internal/runs/{run['run_id']}/finish")
    assert result.status_code == 200
    detail = client.get(f"/api/evaluations/{run['run_id']}").json()
    assert detail["measures"]["grade"] == 2.0
    assert perf_counter() - started < 2


def test_free_mode_leaves_all_claims_ungraded(client, monkeypatch):
    run = create_evaluation(client)
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")

    def forbidden(state):
        raise AssertionError("Free mode must not call the judge")

    monkeypatch.setattr(judge, "grade_claim", forbidden)
    client.post(f"/api/internal/runs/{run['run_id']}/finish")
    detail = client.get(f"/api/evaluations/{run['run_id']}").json()
    assert detail["measures"]["grade"] is None
    assert all(row["grade"] is None for row in detail["claim_comparisons"])


def test_all_failed_judgments_have_no_average_and_are_not_retried(client, monkeypatch):
    run = create_evaluation(client)
    for item in run["claims"]:
        client.post(
            f"/api/claims/{item['claim_id']}/decision",
            json={
                "decision": "decline",
                "payout": 0,
                "reason": "Not covered",
                "policy_section": "Section 9",
                "decided_by": "UnderCover Agent",
            },
        )
    calls = []

    def unavailable(state):
        calls.append(state)
        raise TimeoutError("Unavailable")

    monkeypatch.setattr(judge, "grade_claim", unavailable)
    client.post(f"/api/internal/runs/{run['run_id']}/finish")
    client.post(f"/api/internal/runs/{run['run_id']}/finish")
    assert len(calls) == 5
    detail = client.get(f"/api/evaluations/{run['run_id']}").json()
    assert detail["measures"]["grade"] is None
    assert detail["measures"]["judge_cost"] == 0
