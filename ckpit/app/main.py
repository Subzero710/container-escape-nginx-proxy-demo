import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIRECTORY = Path(__file__).parent
PUBLIC_URLS = [
    {"name": "Frontend", "url": "https://frontend.apex.tld"},
    {"name": "Backend", "url": "https://backend.apex.tld"},
    {"name": "Grafana", "url": "https://grafana.apex.tld"},
    {"name": "n8n", "url": "https://n8n.apex.tld"},
    {"name": "ckpit", "url": "https://ckpit.apex.tld"},
]


@asynccontextmanager
async def lifespan(application):
    async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
        application.state.http = client
        yield


app = FastAPI(title="Apex Stack Cockpit", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIRECTORY / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIRECTORY / "templates")


async def probe(client, label, url, json_body=False):
    result = {"label": label, "url": url, "state": "failed", "data": None}
    try:
        response = await client.get(url)
    except httpx.RequestError as exception:
        result["detail"] = f"Requête échouée : {type(exception).__name__}"
        return result
    result["state"] = "healthy" if 200 <= response.status_code < 400 else "failed"
    result["detail"] = f"HTTP {response.status_code}"
    if json_body:
        try:
            data = response.json()
        except ValueError:
            result.update(state="failed", detail="Réponse JSON invalide")
            return result
        if not isinstance(data, dict):
            result.update(state="failed", detail="Objet JSON attendu")
            return result
        result["data"] = data
    return result


def backend_component(backend, field, label):
    data = backend["data"]
    if data is None or field not in data:
        return {
            "label": label,
            "state": "unavailable",
            "detail": "Non mesuré : réponse /health absente ou invalide",
        }
    if data[field] == "ok":
        return {"label": label, "state": "healthy", "detail": "Connexion vérifiée par /health"}
    if data[field] == "error":
        return {"label": label, "state": "failed", "detail": "Échec de connexion signalé par /health"}
    return {"label": label, "state": "unavailable", "detail": "Valeur /health non reconnue"}


async def collect_status(client):
    frontend, backend, n8n, grafana, prometheus, target = await asyncio.gather(
        probe(client, "Frontend", "http://frontend:80"),
        probe(client, "Backend /health", "http://backend:8000/health", json_body=True),
        probe(client, "n8n", "http://n8n:5678"),
        probe(client, "Grafana /api/health", "http://grafana:3000/api/health", json_body=True),
        probe(client, "Prometheus /-/healthy", "http://prometheus:9090/-/healthy"),
        probe(client, "Prometheus → backend", "http://prometheus:9090/api/v1/targets?state=active", json_body=True),
    )
    if backend["data"] is not None:
        data = backend["data"]
        if data.get("status") != "healthy" or data.get("database") != "ok" or data.get("redis") != "ok":
            backend.update(state="failed", detail="Le backend signale un état non sain ou invalide")
    database = backend_component(backend, "database", "PostgreSQL backend")
    redis = backend_component(backend, "redis", "Redis backend")
    if grafana["data"] is not None:
        if grafana["data"].get("database") != "ok":
            grafana.update(state="failed", detail="Grafana ne confirme pas sa base en état ok")

    if target["state"] == "healthy":
        payload = target["data"]
        valid = (
            payload is not None
            and payload.get("status") == "success"
            and isinstance(payload.get("data"), dict)
            and isinstance(payload["data"].get("activeTargets"), list)
        )
        if not valid:
            target.update(state="failed", detail="Réponse de l'API targets invalide")
        else:
            targets = [
                item for item in payload["data"]["activeTargets"]
                if isinstance(item, dict)
                and isinstance(item.get("labels"), dict)
                and item["labels"].get("job") == "backend"
                and item.get("scrapeUrl") == "http://backend:8000/metrics"
            ]
            if not targets:
                target.update(state="failed", detail="Target backend:8000 absente")
            elif all(item.get("health") == "up" for item in targets):
                target["detail"] = "Scrape du backend confirmé UP par Prometheus"
            else:
                errors = "; ".join(str(item["lastError"]) for item in targets if item.get("lastError"))
                target.update(state="failed", detail="Target backend non UP" + (": " + errors if errors else ""))
    return [frontend, backend, database, redis, n8n, grafana, prometheus, target]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    checks = await collect_status(request.app.state.http)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "public_urls": PUBLIC_URLS,
            "checks": checks,
            "rendered_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        },
    )


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "ckpit"}
