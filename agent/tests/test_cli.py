import os

import pytest

from agent import __main__ as cli
from agent import improver_api, main


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


def test_cli_starts_one_named_claim_without_a_run(monkeypatch, capsys):
    Client.calls = []
    monkeypatch.setattr(cli, "load_dotenv", lambda override: None)
    monkeypatch.setattr(cli, "backend", Client)
    monkeypatch.setattr(cli, "run_with_context", lambda claim, thread: f"{claim} done")
    monkeypatch.setattr("sys.argv", ["agent", "CLM-1001"])

    cli.main()

    path, payload = Client.calls[0]
    assert path == "/api/internal/claims/CLM-1001/work"
    assert payload["thread_id"].startswith("cli-")
    assert capsys.readouterr().out == "CLM-1001 done\n"


def test_cli_without_a_claim_id_shows_usage_and_does_nothing(monkeypatch):
    Client.calls = []
    monkeypatch.setattr(cli, "load_dotenv", lambda override: None)
    monkeypatch.setattr(cli, "backend", Client)
    monkeypatch.setattr("sys.argv", ["agent"])

    with pytest.raises(SystemExit):
        cli.main()

    assert Client.calls == []


def test_cli_loads_key_before_creating_backend_client(monkeypatch, capsys):
    headers = []

    class KeyClient(Client):
        def __init__(self):
            headers.append(os.environ.get("UNDERCOVER_API_KEY"))

    def load_dotenv(override):
        monkeypatch.setenv(
            "UNDERCOVER_API_KEY", "undercover-e2e-agent-key-32-characters"
        )

    monkeypatch.setattr(
        cli,
        "load_dotenv",
        load_dotenv,
    )
    monkeypatch.setattr(cli, "backend", KeyClient)
    monkeypatch.setattr(cli, "run_with_context", lambda claim, thread: "done")
    monkeypatch.setattr("sys.argv", ["agent", "CLM-1001"])
    cli.main()
    assert headers == ["undercover-e2e-agent-key-32-characters"]
    assert capsys.readouterr().out == "done\n"


def test_agent_backend_clients_read_key_when_created(monkeypatch):
    monkeypatch.setenv("UNDERCOVER_API_KEY", "first-key")
    with main.backend() as client:
        assert client.headers["X-API-Key"] == "first-key"
    monkeypatch.setenv("UNDERCOVER_API_KEY", "second-key")
    with main.backend() as client:
        assert client.headers["X-API-Key"] == "second-key"

    assert improver_api.backend is main.backend
