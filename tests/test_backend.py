import importlib.util
import os
from pathlib import Path

import psycopg
import pytest
import redis
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def services():
    # Ces tests utilisent des serveurs réels ; aucune base de substitution.
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["REDIS_URL"] = os.environ["TEST_REDIS_URL"]
    os.environ["CACHE_TTL_SECONDS"] = "5"
    spec = importlib.util.spec_from_file_location("apex_backend", ROOT / "backend/app/main.py")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    worker_spec = importlib.util.spec_from_file_location("apex_worker", ROOT / "worker/worker.py")
    worker = importlib.util.module_from_spec(worker_spec)
    worker_spec.loader.exec_module(worker)
    with TestClient(backend.app) as client:
        yield backend, worker, client
        with backend.connect_database() as connection:
            connection.execute("DELETE FROM items WHERE message LIKE %s", ("integration-test:%",))
        backend.cache.delete(backend.CACHE_KEY)


def test_backend_identity_and_real_dependency_health(services):
    _, _, client = services
    assert client.get("/").json() == {
        "service": "backend", "url": "https://backend.apex.tld", "status": "ok",
        "database": "postgres", "cache": "redis",
    }
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "healthy", "database": "ok", "redis": "ok"}


def test_create_persisted_item_and_read_through_cache(services):
    backend, _, client = services
    backend.cache.delete(backend.CACHE_KEY)
    message = "integration-test: apostrophe ' ; DROP TABLE items; --"
    response = client.post("/items", json={"message": message})
    assert response.status_code == 201
    item = response.json()
    with backend.connect_database() as connection:
        persisted = connection.execute("SELECT message FROM items WHERE id = %s", (item["id"],)).fetchone()
    assert persisted["message"] == message
    first = client.get("/items")
    assert first.status_code == 200
    assert any(row["id"] == item["id"] for row in first.json())
    assert 0 < backend.cache.ttl(backend.CACHE_KEY) <= 5
    before = backend.CACHE_HITS._value.get()
    assert client.get("/items").json() == first.json()
    assert backend.CACHE_HITS._value.get() == before + 1


@pytest.mark.parametrize("message", ["", "   ", "x" * 501])
def test_invalid_messages_are_rejected(services, message):
    assert services[2].post("/items", json={"message": message}).status_code == 422


def test_worker_writes_to_the_same_real_table(services):
    backend, worker, client = services
    worker.initialize_database()
    item_id = worker.insert_tick()
    try:
        backend.cache.delete(backend.CACHE_KEY)
        items = client.get("/items").json()
        assert any(item["id"] == item_id and item["message"].startswith("Worker tick") for item in items)
    finally:
        with backend.connect_database() as connection:
            connection.execute("DELETE FROM items WHERE id = %s", (item_id,))
        backend.cache.delete(backend.CACHE_KEY)


def test_failed_redis_invalidation_rolls_back_sql_write(services, monkeypatch):
    backend, _, client = services
    message = "integration-test: rollback on Redis failure"
    def failed_delete(*args, **kwargs):
        raise redis.exceptions.ConnectionError("Redis unavailable")
    monkeypatch.setattr(backend.cache, "delete", failed_delete)
    assert client.post("/items", json={"message": message}).status_code == 503
    with backend.connect_database() as connection:
        result = connection.execute("SELECT COUNT(*) AS count FROM items WHERE message = %s", (message,)).fetchone()
    assert result["count"] == 0


def test_health_and_metrics_report_database_failure(services, monkeypatch):
    backend, _, client = services
    def failed_database():
        raise psycopg.OperationalError("PostgreSQL unavailable")
    monkeypatch.setattr(backend, "connect_database", failed_database)
    health = client.get("/health")
    assert health.status_code == 503
    assert health.json() == {"status": "unhealthy", "database": "error", "redis": "ok"}
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "backend_database_up 0.0" in metrics.text


def test_redis_failure_is_reported_without_returning_items(services, monkeypatch):
    backend, _, client = services
    def failed_redis(*args, **kwargs):
        raise redis.exceptions.ConnectionError("Redis unavailable")
    monkeypatch.setattr(backend.cache, "get", failed_redis)
    assert client.get("/items").status_code == 503
    monkeypatch.setattr(backend.cache, "ping", failed_redis)
    health = client.get("/health")
    assert health.status_code == 503
    assert health.json()["redis"] == "error"


def test_cors_allows_only_the_public_frontend(services):
    client = services[2]
    headers = {"Origin": "https://frontend.apex.tld", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    response = client.options("/items", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://frontend.apex.tld"
    headers["Origin"] = "https://unrelated.apex.tld"
    assert client.options("/items", headers=headers).status_code == 400


def test_prometheus_metrics_expose_real_dependency_gauges(services):
    response = services[2].get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "backend_http_requests_total" in response.text
    assert "backend_database_up 1.0" in response.text
    assert "backend_redis_up 1.0" in response.text
