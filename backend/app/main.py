import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime

import psycopg
import redis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("uvicorn.error")
DATABASE_URL = os.environ["DATABASE_URL"]
CACHE_TTL_SECONDS = int(os.environ["CACHE_TTL_SECONDS"])
if CACHE_TTL_SECONDS <= 0:
    raise ValueError("CACHE_TTL_SECONDS must be positive")

cache = redis.Redis.from_url(
    os.environ["REDIS_URL"],
    decode_responses=True,
    socket_connect_timeout=3,
    socket_timeout=3,
)
CACHE_KEY = "apex:items:latest"
SCHEMA_LOCK = 861157001
CREATE_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS items (
    id BIGSERIAL PRIMARY KEY,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

HTTP_REQUESTS = Counter(
    "backend_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
HTTP_DURATION = Histogram(
    "backend_http_request_duration_seconds", "HTTP duration", ["method", "path"]
)
ITEMS_CREATED = Counter("backend_items_created_total", "Items created by the API")
CACHE_HITS = Counter("backend_cache_hits_total", "Item cache hits")
CACHE_MISSES = Counter("backend_cache_misses_total", "Item cache misses")
DATABASE_UP = Gauge("backend_database_up", "PostgreSQL connectivity: 1 or 0")
REDIS_UP = Gauge("backend_redis_up", "Redis connectivity: 1 or 0")


def connect_database():
    return psycopg.connect(
        DATABASE_URL,
        connect_timeout=3,
        options="-c statement_timeout=5000",
        row_factory=dict_row,
    )


def initialize_database():
    with connect_database() as connection:
        # Le backend et le worker peuvent démarrer simultanément.
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK,))
        connection.execute(CREATE_ITEMS_TABLE)


@asynccontextmanager
async def lifespan(application):
    initialize_database()
    cache.ping()
    try:
        yield
    finally:
        cache.close()


app = FastAPI(title="Apex Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://frontend.apex.tld"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class ItemCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=500)


class Item(BaseModel):
    id: int
    message: str
    created_at: datetime


@app.middleware("http")
async def collect_metrics(request: Request, call_next):
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = route.path if route is not None else "unmatched"
        if path != "/metrics":
            method = request.method if request.method in {"GET", "POST", "OPTIONS"} else "OTHER"
            HTTP_REQUESTS.labels(method, path, str(status)).inc()
            HTTP_DURATION.labels(method, path).observe(time.perf_counter() - started)


@app.exception_handler(psycopg.Error)
async def database_error(request: Request, exception: psycopg.Error):
    logger.error("PostgreSQL request failed: %s", exception)
    return JSONResponse(status_code=503, content={"detail": "PostgreSQL unavailable"})


@app.exception_handler(redis.exceptions.RedisError)
async def redis_error(request: Request, exception: redis.exceptions.RedisError):
    logger.error("Redis request failed: %s", exception)
    return JSONResponse(status_code=503, content={"detail": "Redis unavailable"})


@app.get("/")
def index():
    return {
        "service": "backend",
        "url": "https://backend.apex.tld",
        "status": "ok",
        "database": "postgres",
        "cache": "redis",
    }


def dependency_status():
    database_status = "ok"
    redis_status = "ok"
    try:
        with connect_database() as connection:
            connection.execute("SELECT 1").fetchone()
    except psycopg.Error:
        database_status = "error"
    try:
        if not cache.ping():
            redis_status = "error"
    except redis.exceptions.RedisError:
        redis_status = "error"
    DATABASE_UP.set(int(database_status == "ok"))
    REDIS_UP.set(int(redis_status == "ok"))
    healthy = database_status == redis_status == "ok"
    return {
        "status": "healthy" if healthy else "unhealthy",
        "database": database_status,
        "redis": redis_status,
    }


@app.get("/health")
def health():
    result = dependency_status()
    return JSONResponse(
        status_code=200 if result["status"] == "healthy" else 503,
        content=result,
    )


@app.get("/items", response_model=list[Item])
def list_items():
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        CACHE_HITS.inc()
        return json.loads(cached)
    CACHE_MISSES.inc()
    with connect_database() as connection:
        rows = connection.execute(
            "SELECT id, message, created_at FROM items ORDER BY id DESC LIMIT 20"
        ).fetchall()
    items = [Item.model_validate(row).model_dump(mode="json") for row in rows]
    cache.setex(CACHE_KEY, CACHE_TTL_SECONDS, json.dumps(items))
    return items


@app.post("/items", response_model=Item, status_code=201)
def create_item(item: ItemCreate):
    with connect_database() as connection:
        row = connection.execute(
            "INSERT INTO items (message) VALUES (%s) RETURNING id, message, created_at",
            (item.message,),
        ).fetchone()
        # Une panne Redis fait échouer la transaction avant son commit.
        cache.delete(CACHE_KEY)
    ITEMS_CREATED.inc()
    return row


@app.get("/metrics", include_in_schema=False)
def metrics():
    dependency_status()
    return Response(content=generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})
