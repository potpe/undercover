import pytest

from agent import __main__ as cli


class Response:
    def raise_for_status(self):
        return None


class Client:
    calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, path, json):
        self.calls.append((path, json))
        return Response()


def test_cli_starts_one_named_claim_without_a_run(monkeypatch):
    Client.calls = []
    monkeypatch.setattr(cli, "load_dotenv", lambda override: None)
    monkeypatch.setattr(cli, "backend", Client)
    monkeypatch.setattr("sys.argv", ["agent", "CLM-1001"])

    cli.main()

    path, payload = Client.calls[0]
    assert path == "/api/claims/CLM-1001/run"
    assert payload["thread_id"].startswith("cli-")


def test_cli_without_a_claim_id_shows_usage_and_does_nothing(monkeypatch):
    Client.calls = []
    monkeypatch.setattr(cli, "load_dotenv", lambda override: None)
    monkeypatch.setattr(cli, "backend", Client)
    monkeypatch.setattr("sys.argv", ["agent"])

    with pytest.raises(SystemExit):
        cli.main()

    assert Client.calls == []
