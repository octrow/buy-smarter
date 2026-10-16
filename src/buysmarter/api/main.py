"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status

from buysmarter import __version__, logsetup
from buysmarter.config import get_settings
from buysmarter.db.base import db_reachable


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logsetup.setup_logging()
    yield


app = FastAPI(title="buy-smarter", version=__version__, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: no dependencies, so a database outage does not restart the pod."""
    return {"status": "ok"}


@app.get("/ready")
async def ready(response: Response) -> dict[str, str]:
    """Readiness probe: 503 until the database answers."""
    if await db_reachable(get_settings().database_url):
        return {"status": "ok"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "database unavailable"}
