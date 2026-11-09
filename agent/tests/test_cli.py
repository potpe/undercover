from agent import __main__ as cli


def test_cli_starts_a_capped_run_without_running_a_model(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"run_id": 7}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, path, json):
            calls.append((path, json))
            return Response()

    monkeypatch.setattr(cli, "load_dotenv", lambda override: None)
    monkeypatch.setattr(cli, "backend", Client)
    monkeypatch.setattr("sys.argv", ["agent", "--claim-cap", "5"])

    cli.main()

    assert len(calls) == 1
    path, payload = calls[0]
    assert path == "/api/runs"
    assert payload["claim_cap"] == 5
    assert payload["thread_id"].startswith("cli-")
