from backend.runs import run_usage


def model_call(client, claim_id, run_id, cost):
    return client.post(
        f"/api/claims/{claim_id}/model-calls",
        json={
            "run_id": run_id,
            "model_name": "openai/gpt-6-luna",
            "reasoning_effort": "low",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cost": cost,
        },
    )


def work_period(client, claim_id, run_id, seconds, finished_at):
    return client.post(
        f"/api/claims/{claim_id}/work-periods",
        json={
            "run_id": run_id,
            "thread_id": f"thread-{claim_id}",
            "started_at": "2026-11-10T12:00:00Z",
            "finished_at": finished_at,
            "seconds": seconds,
        },
    )


def test_usage_ledgers_are_exact_and_isolated_by_run(client):
    assert model_call(client, "CLM-1001", 1, 0.0125).status_code == 200
    assert model_call(client, "CLM-1001", 1, 0.0025).status_code == 200
    assert model_call(client, "CLM-1002", 2, 0.5).status_code == 200
    assert model_call(client, "CLM-1003", None, 0.25).status_code == 200

    assert (
        work_period(
            client, "CLM-1001", 1, 2.5, "2026-11-10T12:00:02.500000Z"
        ).status_code
        == 200
    )
    assert (
        work_period(client, "CLM-1001", 1, 1.5, "2026-11-10T12:10:00Z").status_code
        == 200
    )
    assert (
        work_period(client, "CLM-1002", 2, 20, "2026-11-10T12:00:20Z").status_code
        == 200
    )

    assert run_usage(1) == {
        "model_cost": 0.015,
        "prompt_tokens": 200,
        "completion_tokens": 40,
        "total_tokens": 240,
        "active_seconds": 4.0,
        "first_started_at": "2026-11-10T12:00:00+00:00",
        "last_finished_at": "2026-11-10T12:10:00+00:00",
    }
    assert run_usage(2)["model_cost"] == 0.5
    assert run_usage(None)["model_cost"] == 0.25


def test_usage_rejects_unknown_claims_and_requires_every_input(client):
    unknown = model_call(client, "CLM-9999", None, 0.1)
    missing = client.post(
        "/api/claims/CLM-1001/model-calls",
        json={"run_id": None, "model_name": "openai/gpt-6-luna"},
    )

    assert unknown.status_code == 404
    assert missing.status_code == 422
