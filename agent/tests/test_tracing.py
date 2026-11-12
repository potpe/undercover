from contextlib import nullcontext

from agent import tracing


def test_round_stretches_share_the_round_session(monkeypatch):
    for key in tracing.LANGFUSE_KEYS:
        monkeypatch.setenv(key, "stub")
    sessions = []

    class FakeLangfuse:
        def __init__(self):
            self.flush_count = 0

        def flush(self):
            self.flush_count += 1

    class FakeCallbackHandler:
        pass

    langfuse = FakeLangfuse()
    monkeypatch.setattr(tracing, "get_client", lambda: langfuse)
    monkeypatch.setattr(tracing, "CallbackHandler", FakeCallbackHandler)
    monkeypatch.setattr(
        tracing,
        "propagate_attributes",
        lambda **attributes: sessions.append(attributes) or nullcontext(),
    )

    for _ in range(2):
        with tracing.trace_round(14) as callbacks:
            assert len(callbacks) == 1

    assert sessions == [
        {"session_id": "Improvement 14", "tags": ["round-14"]},
        {"session_id": "Improvement 14", "tags": ["round-14"]},
    ]
    assert langfuse.flush_count == 2
