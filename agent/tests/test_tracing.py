from contextlib import contextmanager

from agent import main, tracing


def test_missing_langfuse_keys_disable_tracing(monkeypatch):
    for key in ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_BASE_URL"):
        monkeypatch.delenv(key, raising=False)

    with tracing.trace_claim("CLM-1001", "thread-1") as callbacks:
        assert callbacks == []


def test_claim_tracing_uses_stable_claim_thread_session(monkeypatch):
    events = []
    for key in tracing.LANGFUSE_KEYS:
        monkeypatch.setenv(key, "test-value")

    class FakeLangfuse:
        @contextmanager
        def start_as_current_observation(self, **kwargs):
            events.append(kwargs)
            yield

        def flush(self):
            events.append({"flushed": True})

    class FakeHandler:
        pass

    @contextmanager
    def fake_propagate_attributes(**kwargs):
        events.append(kwargs)
        yield

    monkeypatch.setattr(tracing, "get_client", FakeLangfuse)
    monkeypatch.setattr(tracing, "CallbackHandler", FakeHandler)
    monkeypatch.setattr(tracing, "propagate_attributes", fake_propagate_attributes)

    with tracing.trace_claim("CLM-1003", "run-1:CLM-1003:attempt-1") as callbacks:
        assert len(callbacks) == 1
        assert isinstance(callbacks[0], FakeHandler)

    assert {"session_id": "CLM-1003:run-1:CLM-1003:attempt-1"} in events
    with tracing.trace_claim("CLM-1003", "run-1:CLM-1003:attempt-1"):
        pass
    with tracing.trace_claim("CLM-1003", "run-1:CLM-1003:attempt-2"):
        pass

    sessions = [event["session_id"] for event in events if "session_id" in event]
    assert sessions == [
        "CLM-1003:run-1:CLM-1003:attempt-1",
        "CLM-1003:run-1:CLM-1003:attempt-1",
        "CLM-1003:run-1:CLM-1003:attempt-2",
    ]
    assert events[-1] == {"flushed": True}


def test_claim_config_tags_each_run_and_leaves_direct_work_untagged():
    dispatch = main.claim_config("thread", [], 7, "dispatch")
    evaluation = main.claim_config("thread", [], 8, "evaluation")
    direct = main.claim_config("thread", [], None, "dispatch")

    assert dispatch.get("metadata") == {"langfuse_tags": ["dispatch-7"]}
    assert evaluation.get("metadata") == {"langfuse_tags": ["evaluation-8"]}
    assert direct.get("metadata") == {}
