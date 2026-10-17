"""Recommend jobs over HTTP: POST queues a run, GET polls it; GET /history reads price history."""

import secrets
from datetime import datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from pydantic import BaseModel

from buysmarter import telemetry
from buysmarter.config import get_settings
from buysmarter.jobs import (
    Job,
    JobStatus,
    JobStore,
    RecommendOutcome,
    RecommendRequest,
    dedupe_key,
    get_redis,
    rate_limit,
)
from buysmarter.services.collect import select_sources
from buysmarter.services.recommend import ProductHistory, product_history
from buysmarter.worker import recommend_job

_JOB_ID_MAX_LEN = 64
HistoryTerm = Annotated[
    str, Query(min_length=2, max_length=100, description="Title or URL substring")
]


class JobAccepted(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str


class JobView(BaseModel):
    job_id: str
    status: JobStatus
    request: RecommendRequest
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: RecommendOutcome | None = None
    error: str | None = None

    @classmethod
    def of(cls, job: Job) -> "JobView":
        return cls(
            job_id=job.id,
            status=job.status,
            request=job.request,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            result=job.result,
            error=job.error,
        )


def get_store() -> JobStore:
    return JobStore(get_redis())


Store = Annotated[JobStore, Depends(get_store)]


async def require_api_key(x_api_key: str | None = Header(default=None)) -> str | None:
    """With BUYSMARTER_API_KEYS set, every route here needs one of those keys in X-API-Key."""
    keys = [k.strip() for k in get_settings().api_keys.split(",") if k.strip()]
    if keys and not any(secrets.compare_digest(x_api_key or "", k) for k in keys):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid X-API-Key")
    return x_api_key


ApiKey = Annotated[str | None, Depends(require_api_key)]
router = APIRouter(
    dependencies=[Depends(require_api_key)],
    responses={401: {"description": "BUYSMARTER_API_KEYS is set and X-API-Key is not one of them"}},
)


def _accepted(job: Job, response: Response) -> JobAccepted:
    url = f"/recommend/{job.id}"
    response.headers["Location"] = url
    return JobAccepted(job_id=job.id, status=job.status, status_url=url)


def _client_id(request: Request, api_key: str | None) -> str:
    # ponytail: behind an ingress client.host is the proxy; trust X-Forwarded-For once set up.
    return f"key:{api_key}" if api_key else f"ip:{request.client.host if request.client else '-'}"


@router.post(
    "/recommend",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["recommend"],
    responses={
        200: {"model": JobAccepted, "description": "Same request within 15 minutes: that job"},
        429: {"description": "Too many new jobs for this client; see Retry-After"},
        503: {"description": "The queue (Redis) is unavailable"},
    },
)
async def create_job(
    body: RecommendRequest,
    request: Request,
    response: Response,
    store: Store,
    api_key: ApiKey,
) -> JobAccepted:
    """Queue a recommend run. Poll ``status_url`` until the status is done, no_match or failed."""
    settings = get_settings()
    try:
        enabled = select_sources(settings.api_sources.split(","))
        body = body.model_copy(update={"sources": select_sources(body.sources, allowed=enabled)})
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    key = dedupe_key(body)
    if existing := await store.find_dedupe(key):
        response.status_code = status.HTTP_200_OK
        return _accepted(existing, response)
    client = _client_id(request, api_key)
    limit, window = settings.rate_limit_jobs, settings.rate_limit_window_s
    if retry_after := await rate_limit(store.redis, client, limit=limit, window_s=window):
        telemetry.RATE_LIMITED.add(1)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many new jobs; retry later",
            headers={"Retry-After": str(retry_after)},
        )
    job = await store.create(body, dedupe=key)
    if (holder := await store.claim_dedupe(key, job.id)) and (raced := await store.get(holder)):
        response.status_code = status.HTTP_200_OK  # an identical request won the race
        return _accepted(raced, response)
    await recommend_job.kiq(job.id)
    return _accepted(job, response)


@router.get(
    "/recommend/{job_id}", tags=["recommend"], responses={404: {"description": "No such job"}}
)
async def get_job(
    job_id: Annotated[str, Path(max_length=_JOB_ID_MAX_LEN)], store: Store
) -> JobView:
    """A job's status; ``result`` once it is done or no_match, ``error`` when it failed."""
    job = await store.get(job_id)
    if job is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "no such job (finished jobs expire after 24h)"
        )
    return JobView.of(job)


@router.get("/history", tags=["history"])
async def history(q: HistoryTerm) -> list[ProductHistory]:
    """Price history of tracked products matching ``q``; empty without Postgres."""
    return await product_history(q)
