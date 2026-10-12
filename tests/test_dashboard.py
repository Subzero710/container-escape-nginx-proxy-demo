import asyncio
import importlib.util
import re
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apex_cockpit", ROOT / "ckpit/app/main.py")
cockpit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cockpit)


def responses(request, backend_failure=False, target_health="up", target_error=""):
    host, path = request.url.host, request.url.path
    if host == "backend":
        if backend_failure:
            raise httpx.ConnectError("backend inaccessible", request=request)
        return httpx.Response(200, json={"status": "healthy", "database": "ok", "redis": "ok"})
    if host == "grafana":
        return httpx.Response(200, json={"database": "ok"})
    if host == "prometheus" and path == "/api/v1/targets":
        return httpx.Response(200, json={"status": "success", "data": {"activeTargets": [{
            "labels": {"job": "backend"},
            "scrapeUrl": "http://backend:8000/metrics",
            "health": target_health,
            "lastError": target_error,
        }]}})
    if host == "n8n":
        return httpx.Response(302, headers={"Location": "/signin"})
    return httpx.Response(200, text="ok")


def get_checks(handler):
    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await cockpit.collect_status(client)
    return asyncio.run(collect())


def test_all_components_healthy_and_n8n_redirect_is_reachable():
    checks = get_checks(responses)
    assert len(checks) == 8
    assert all(check["state"] == "healthy" for check in checks)
    assert next(check for check in checks if check["label"] == "n8n")["detail"] == "HTTP 302"


def test_backend_failure_does_not_invent_database_or_redis_health():
    checks = get_checks(lambda request: responses(request, backend_failure=True))
    by_name = {check["label"]: check for check in checks}
    assert by_name["Backend /health"]["state"] == "failed"
    assert by_name["PostgreSQL backend"]["state"] == "unavailable"
    assert by_name["Redis backend"]["state"] == "unavailable"
    assert by_name["Grafana /api/health"]["state"] == "healthy"


def test_prometheus_server_healthy_does_not_imply_backend_target_up():
    checks = get_checks(lambda request: responses(request, target_health="down", target_error="connection refused"))
    by_name = {check["label"]: check for check in checks}
    assert by_name["Prometheus /-/healthy"]["state"] == "healthy"
    assert by_name["Prometheus → backend"]["state"] == "failed"
    assert "connection refused" in by_name["Prometheus → backend"]["detail"]


def test_backend_reports_independent_dependency_failure():
    def handler(request):
        if request.url.host == "backend":
            return httpx.Response(503, json={"status": "unhealthy", "database": "ok", "redis": "error"})
        return responses(request)
    by_name = {check["label"]: check for check in get_checks(handler)}
    assert by_name["PostgreSQL backend"]["state"] == "healthy"
    assert by_name["Redis backend"]["state"] == "failed"


def test_invalid_target_response_is_reported():
    def handler(request):
        if request.url.path == "/api/v1/targets":
            return httpx.Response(200, json={"status": "success", "data": []})
        return responses(request)
    target = get_checks(handler)[-1]
    assert target["state"] == "failed"
    assert "invalide" in target["detail"]


def test_rendered_dashboard_escapes_errors_and_uses_relative_static_url():
    mock = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: responses(request, target_health="down", target_error="<script>alert(1)</script>")
    ))
    with TestClient(cockpit.app) as client:
        cockpit.app.state.http = mock
        response = client.get("/")
        assert response.status_code == 200
        assert "Apex Stack Cockpit" in response.text
        assert "<script>alert(1)</script>" not in response.text
        assert "&lt;script&gt;" in response.text
        assert 'href="/static/style.css"' in response.text
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC", response.text)
        for service in cockpit.PUBLIC_URLS:
            assert service["url"] in response.text
        assert client.get("/static/style.css").status_code == 200
    asyncio.run(mock.aclose())
