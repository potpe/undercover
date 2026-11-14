import json
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mangum import Mangum
from sqlalchemy import event
from sqlalchemy.pool import NullPool

from backend import database, main


def function_url_event(path: str) -> dict[str, Any]:
    method = "GET"
    route_key = f"{method} {path}"
    return {
        "version": "2.0",
        "routeKey": route_key,
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "undercover.example"},
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "lambda-url",
            "domainName": "undercover.example",
            "domainPrefix": "undercover",
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
                "userAgent": "test",
            },
            "requestId": "test-request",
            "routeKey": route_key,
            "stage": "$default",
            "time": "14/Nov/2026:00:00:00 +0000",
            "timeEpoch": 1794614400000,
        },
        "isBase64Encoded": False,
    }


def test_lambda_handler_serves_health_without_startup_seeding(monkeypatch):
    def unexpected_seed():
        raise AssertionError("Lambda health request must not run startup seeding")

    monkeypatch.setattr(main, "initialize_database", unexpected_seed)
    response = main.handler(function_url_event("/api/health"), cast(Any, {}))

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"status": "ok"}


def test_dashboard_fallback_serves_spa_and_safe_static_files(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<main>UnderCover</main>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.info('ok')", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be exposed", encoding="utf-8")
    leak_path = dist / "leak.txt"
    leak_path.write_text("placeholder", encoding="utf-8")
    resolved_outside = outside.resolve()
    actual_resolve = Path.resolve

    def resolve_without_symlink_privileges(path, *args, **kwargs):
        if path == leak_path:
            return resolved_outside
        return actual_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_without_symlink_privileges)

    test_app = FastAPI()
    test_app.router.routes.extend(
        route
        for route in main.app.router.routes
        if getattr(route, "name", None) != "dashboard_fallback"
    )
    assert main.register_dashboard_fallback(test_app, dist)
    fallback_indexes = [
        index
        for index, route in enumerate(test_app.router.routes)
        if getattr(route, "name", None) == "dashboard_fallback"
    ]
    api_indexes = [
        index
        for index, route in enumerate(test_app.router.routes)
        if getattr(route, "path", "").startswith("/api/")
    ]
    assert len(fallback_indexes) == 1
    assert fallback_indexes[0] > max(api_indexes)

    handler = Mangum(test_app, lifespan="off")
    root = handler(function_url_event("/"), cast(Any, {}))
    deep_link = handler(function_url_event("/claims/CLM-1001"), cast(Any, {}))
    asset = handler(function_url_event("/assets/app.js"), cast(Any, {}))
    unknown_api = handler(function_url_event("/api/not-a-route"), cast(Any, {}))
    symlink = handler(function_url_event("/leak.txt"), cast(Any, {}))
    traversal = handler(function_url_event("/%2e%2e/outside.txt"), cast(Any, {}))

    assert root["statusCode"] == deep_link["statusCode"] == 200
    assert root["body"] == deep_link["body"] == "<main>UnderCover</main>"
    assert asset["statusCode"] == 200
    assert asset["body"] == "console.info('ok')"
    assert unknown_api["statusCode"] == 404
    assert json.loads(unknown_api["body"]) == {"detail": "Not Found"}
    assert symlink["statusCode"] == traversal["statusCode"] == 404
    assert "must not be exposed" not in symlink["body"] + traversal["body"]
    assert "/{path}" not in test_app.openapi()["paths"]


def test_dashboard_fallback_is_not_registered_without_a_build(tmp_path):
    test_app = FastAPI()
    routes_before = list(test_app.routes)

    assert not main.register_dashboard_fallback(test_app, tmp_path / "missing")
    assert test_app.routes == routes_before
    assert not main.register_dashboard_fallback(test_app, tmp_path)
    assert test_app.routes == routes_before


def test_production_dashboard_fallback_follows_api_routes_when_built():
    fallback_indexes = [
        index
        for index, route in enumerate(main.app.router.routes)
        if getattr(route, "name", None) == "dashboard_fallback"
    ]
    if main.DASHBOARD_DIST.is_dir() and (main.DASHBOARD_DIST / "index.html").is_file():
        assert len(fallback_indexes) == 1
        api_indexes = [
            index
            for index, route in enumerate(main.app.router.routes)
            if getattr(route, "path", "").startswith("/api/")
        ]
        assert fallback_indexes[0] > max(api_indexes)
    else:
        assert fallback_indexes == []


def test_lambda_postgres_requests_open_and_close_fresh_connections(
    monkeypatch,
):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "undercover-test")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://user:password@db.example/undercover"
    )
    real_create_engine = database.create_engine
    engine_options = []
    engines = []
    connection_counts = {"opened": 0, "closed": 0}

    def instrumented_engine(url, **kwargs):
        engine_options.append((url, kwargs))
        engine = real_create_engine("sqlite://", poolclass=kwargs["poolclass"])
        event.listen(engine, "connect", opened)
        event.listen(engine, "close", closed)
        engines.append(engine)
        return engine

    monkeypatch.setattr(database, "create_engine", instrumented_engine)
    database.cached_engine.cache_clear()
    app = FastAPI()

    @app.get("/connection")
    def connection_check():
        with database.connect() as connection:
            return {"value": connection.exec_driver_sql("SELECT 1").scalar_one()}

    def opened(_connection, _record):
        connection_counts["opened"] += 1

    def closed(_connection, _record):
        connection_counts["closed"] += 1

    with TestClient(app) as client:
        assert client.get("/connection").json() == {"value": 1}
        assert client.get("/connection").json() == {"value": 1}

    url, options = engine_options[0]
    assert url.startswith("postgresql+psycopg://")
    assert options["connect_args"] == {
        "options": "-c search_path=undercover",
        "prepare_threshold": None,
    }
    assert options["poolclass"] is NullPool
    assert isinstance(engines[0].pool, NullPool)
    assert connection_counts == {"opened": 2, "closed": 2}
    engines[0].dispose()
    database.cached_engine.cache_clear()


def test_postgres_engine_keeps_bounded_pool_outside_lambda(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    calls = []

    def fake_engine(url, **kwargs):
        calls.append((url, kwargs))
        return object()

    monkeypatch.setattr(database, "create_engine", fake_engine)

    database.create_db_engine("postgresql://postgres.example:secret@localhost/postgres")

    url, options = calls[0]
    assert url.startswith("postgresql+psycopg://")
    assert options["connect_args"] == {"options": "-c search_path=undercover"}
    assert options["pool_size"] == 1
    assert options["max_overflow"] == 1
    assert "poolclass" not in options


def test_sqlite_engine_keeps_default_pool_outside_lambda(tmp_path, monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    engine = database.create_db_engine(f"sqlite:///{tmp_path / 'offline.db'}")
    try:
        assert engine.dialect.name == "sqlite"
        assert not isinstance(engine.pool, NullPool)
    finally:
        engine.dispose()
