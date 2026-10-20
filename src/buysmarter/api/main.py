"""FastAPI application entry point."""

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

try:
    from fastapi import FastAPI, Request, Response, status
    from fastapi.responses import JSONResponse
    from redis.exceptions import RedisError
except ModuleNotFoundError as exc:
    sys.exit(f"{exc.name} is missing: the HTTP API needs `pip install buysmarter[server]`")

from buysmarter import __version__, logsetup, telemetry
from buysmarter.api.recommend import router
from buysmarter.config import get_settings
from buysmarter.db.base import db_reachable
from buysmarter.jobs import get_redis
from buysmarter.worker import broker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logsetup.setup_logging()
    await broker.startup()
    yield
    await broker.shutdown()


app = FastAPI(title="buy-smarter", version=__version__, lifespan=lifespan)
app.include_router(router)
if os.environ.get("BUYSMARTER_METRICS", "1") != "0":
    telemetry.instrument_app(app)


@app.exception_handler(RedisError)
async def redis_unavailable(request: Request, exc: RedisError) -> JSONResponse:
    logger.warning("redis unavailable on %s: %r", request.url.path, exc)
    return JSONResponse(
        {"detail": "queue unavailable, retry later"},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


async def redis_reachable() -> bool:
    try:
        return bool(await get_redis().ping())
    except RedisError:
        return False


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: no dependencies, so a database outage does not restart the pod."""
    return {"status": "ok"}


@app.get("/ready")
async def ready(response: Response) -> dict[str, str]:
    """Readiness probe: 503 until both the database and Redis (the job queue) answer."""
    if not await db_reachable(get_settings().database_url):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "database unavailable"}
    if not await redis_reachable():
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "redis unavailable"}
    return {"status": "ok"}
