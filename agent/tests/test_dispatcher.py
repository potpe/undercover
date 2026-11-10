import time

from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError

from agent import dispatcher


def run_stand_in(claim_ids, concurrency, work, on_error=None):
    return dispatcher.run_dispatch(
        run_id=1,
        run_kind="dispatch",
        concurrency=concurrency,
        harness=dispatcher.Harness(
            work=work,
            on_error=on_error or (lambda item, error: {"status": "failed"}),
        ),
        claim_ids=claim_ids,
    )


def exercise_concurrency(concurrency):
    active = 0
    maximum = 0
    started = []

    def work(item):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        started.append(item["claim_id"])
        time.sleep(0.005)
        active -= 1
        return {"claim_id": item["claim_id"], "status": "done"}

    result = run_stand_in(
        [f"CLM-{number}" for number in range(1, 26)], concurrency, work
    )
    return result, maximum, started


def test_send_fanout_never_exceeds_n_and_collects_every_result():
    for concurrency in (1, 3, 25):
        result, maximum, started = exercise_concurrency(concurrency)

        assert len(result["results"]) == 25
        assert maximum <= concurrency
        assert len(started) == 25


def check_transient_error(error):
    calls = 0
    failures = []

    def work(item):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return {"claim_id": item["claim_id"], "status": "done"}

    result = run_stand_in(
        ["CLM-1001"], 1, work, lambda item, error: failures.append(error)
    )
    return calls, failures, result


def test_rate_limit_and_timeout_retry_without_error_settlement(monkeypatch):
    monkeypatch.setattr("langgraph.pregel._retry.time.sleep", lambda seconds: None)
    for error in (ModelRateLimitError("429"), ModelTimeoutError("timeout")):
        calls, failures, result = check_transient_error(error)

        assert calls == 2
        assert failures == []
        assert result["results"][0]["status"] == "done"


def test_exhausted_transient_retries_use_error_handler(monkeypatch):
    monkeypatch.setattr("langgraph.pregel._retry.time.sleep", lambda seconds: None)
    calls = 0

    def work(item):
        nonlocal calls
        calls += 1
        raise ModelRateLimitError("Still rate limited")

    def leave(item, error):
        assert isinstance(error, ModelRateLimitError)
        return {"claim_id": item["claim_id"], "status": "in_progress"}

    result = run_stand_in(["CLM-1001"], 1, work, leave)

    assert calls == 3
    assert result["results"] == [{"claim_id": "CLM-1001", "status": "in_progress"}]


def test_first_ordinary_failure_is_sent_back_once_in_same_dispatch(monkeypatch):
    monkeypatch.setattr("langgraph.pregel._retry.time.sleep", lambda seconds: None)
    calls = 0

    def work(item):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Invalid provider response")
        return {"claim_id": item["claim_id"], "status": "done"}

    def fail(item, error):
        return {"claim_id": item["claim_id"], "status": "retry"}

    result = run_stand_in(["CLM-1001"], 1, work, fail)

    assert calls == 2
    assert [entry["status"] for entry in result["results"]] == ["retry", "done"]


def test_error_handler_does_not_stop_other_claims(monkeypatch):
    monkeypatch.setattr("langgraph.pregel._retry.time.sleep", lambda seconds: None)

    def work(item):
        if item["claim_id"] == "CLM-1001":
            raise RuntimeError("Bad claim")
        return {"claim_id": item["claim_id"], "status": "done"}

    def fail(item, error):
        return {"claim_id": item["claim_id"], "status": "left_for_handler"}

    result = run_stand_in(["CLM-1001", "CLM-1002"], 2, work, fail)

    assert {entry["status"] for entry in result["results"]} == {
        "left_for_handler",
        "done",
    }
