"""Recommend jobs in Redis: the request, the job record, request dedupe and the client rate limit.

The HTTP API creates a job and enqueues its id; the worker (``buysmarter.worker``) runs it and
writes the outcome back. Redis is the single source of truth for a job's state.
"""

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from buysmarter.config import get_settings
from buysmarter.ranking.models import QueryConstraints, ScoredCandidate
from buysmarter.services.recommend import ALL_SOURCES, RecommendResult, UsedListing

JOB_TTL_S = 24 * 3600
DEDUPE_TTL_S = 15 * 60  # prices do not move within 15 minutes; a retry gets the same job
_PREFIX = "buysmarter"
MAX_QUERY_LEN = 500
MAX_TOP = 20
DEFAULT_TOP = 8


class RecommendRequest(BaseModel):
    """One recommend run, as the API accepts it and the worker replays it."""

    query: str = Field(min_length=1, max_length=MAX_QUERY_LEN, examples=["ирригатор до 40000"])
    city: str | None = Field(default=None, max_length=100, examples=["Алматы"])
    budget: int | None = Field(default=None, gt=0, description="Maximum price, KZT")
    sources: list[str] = Field(
        default_factory=list,
        description=f"Marketplaces from {', '.join(ALL_SOURCES)}; empty = every enabled one",
    )
    trusted_only: bool = False
    use_llm: bool = Field(default=False, description="LLM query parsing; needs a provider key")
    top: int = Field(default=DEFAULT_TOP, ge=1, le=MAX_TOP)


class Axis(BaseModel):
    name: str
    score: float
    max_score: float
    explanation: str


class Offer(BaseModel):
    """One ranked product, as the API shows it."""

    title: str
    source: str
    url: str | None
    price: int
    rating: float | None
    review_count: int | None
    score: float
    confidence: float
    pros: list[str]
    cons: list[str]
    axes: list[Axis]

    @classmethod
    def from_scored(cls, scored: ScoredCandidate) -> "Offer":
        c = scored.candidate
        return cls(
            title=c.title,
            source=c.source,
            url=c.url,
            price=c.price,
            rating=c.rating,
            review_count=c.review_count,
            score=scored.total_score,
            confidence=scored.confidence,
            pros=scored.pros,
            cons=scored.cons,
            axes=[Axis(**a.model_dump()) for a in scored.axes],
        )


class RecommendOutcome(BaseModel):
    """A finished job's result: the same markdown report as the CLI and MCP, plus structured data.

    A public mapping of ``RecommendResult``, so internal models can change without breaking clients.
    """

    report_markdown: str
    best: Offer | None = None
    alternatives: dict[str, Offer] = Field(default_factory=dict)
    constraints: QueryConstraints | None = None
    used_listings: list[UsedListing] = Field(default_factory=list)
    city_defaulted: bool = False
    reason: str | None = Field(default=None, description="Why nothing was recommended (no_match)")

    @classmethod
    def from_result(cls, result: RecommendResult, report_markdown: str) -> "RecommendOutcome":
        rec = result.recommendation
        return cls(
            report_markdown=report_markdown,
            best=Offer.from_scored(rec.best),
            alternatives={role: Offer.from_scored(s) for role, s in rec.alternatives.items()},
            constraints=result.constraints,
            used_listings=result.used_listings,
            city_defaulted=result.city_defaulted,
        )


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    NO_MATCH = "no_match"
    FAILED = "failed"


TERMINAL = frozenset({JobStatus.DONE, JobStatus.NO_MATCH, JobStatus.FAILED})


@dataclass(frozen=True)
class Job:
    id: str
    status: JobStatus
    request: RecommendRequest
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


def dedupe_key(request: RecommendRequest) -> str:
    """Same request modulo case, spacing and source order → same key."""
    query = " ".join(request.query.lower().split())
    normalized = request.model_copy(update={"query": query, "sources": sorted(request.sources)})
    return hashlib.sha256(normalized.model_dump_json().encode()).hexdigest()


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _when(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


class JobStore:
    """Job records (hash per job), dedupe slots and rate-limit counters on one Redis client."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def create(self, request: RecommendRequest, *, dedupe: str) -> Job:
        job_id = uuid.uuid4().hex
        fields = {
            "status": JobStatus.QUEUED.value,
            "request": request.model_dump_json(),
            "created_at": _now(),
            "dedupe": dedupe,
        }
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(_job_key(job_id), mapping=fields)
            pipe.expire(_job_key(job_id), JOB_TTL_S)
            await pipe.execute()
        return _to_job(job_id, fields)

    async def get(self, job_id: str) -> Job | None:
        fields = await self.redis.hgetall(_job_key(job_id))
        return _to_job(job_id, fields) if fields else None

    async def start(self, job_id: str) -> bool:
        """Mark running; False when the job is gone or already finished (a redelivered message)."""
        status = await self.redis.hget(_job_key(job_id), "status")
        if status is None or JobStatus(status) in TERMINAL:
            return False
        await self.redis.hset(
            _job_key(job_id), mapping={"status": JobStatus.RUNNING.value, "started_at": _now()}
        )
        return True

    async def finish(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        fields = {"status": status.value, "finished_at": _now()}
        if result is not None:
            fields["result"] = json.dumps(result, ensure_ascii=False)
        if error is not None:
            fields["error"] = error
        await self.redis.hset(_job_key(job_id), mapping=fields)
        if status is JobStatus.FAILED:
            await self._release_dedupe(job_id)

    async def claim_dedupe(self, key: str, job_id: str) -> str | None:
        """Take the dedupe slot for ``job_id``; return the job already holding it, if any."""
        if await self.redis.set(_dedupe_key(key), job_id, nx=True, ex=DEDUPE_TTL_S):
            return None
        return await self.redis.get(_dedupe_key(key))

    async def rate_limit(self, client: str, *, limit: int, window_s: int) -> int | None:
        """Count one job for ``client``; seconds until the window resets once over ``limit``."""
        # ponytail: fixed window (a burst of 2x limit across a boundary); sliding window if abused.
        window = int(time.time()) // window_s
        key = f"{_PREFIX}:rl:{client}:{window}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_s)
            count, _ = await pipe.execute()
        if count <= limit:
            return None
        return max(1, (window + 1) * window_s - int(time.time()))

    async def _release_dedupe(self, job_id: str) -> None:
        dedupe = await self.redis.hget(_job_key(job_id), "dedupe")
        if dedupe and await self.redis.get(_dedupe_key(dedupe)) == job_id:
            await self.redis.delete(_dedupe_key(dedupe))


def _job_key(job_id: str) -> str:
    return f"{_PREFIX}:job:{job_id}"


def _dedupe_key(key: str) -> str:
    return f"{_PREFIX}:dedupe:{key}"


def _to_job(job_id: str, fields: dict[str, str]) -> Job:
    return Job(
        id=job_id,
        status=JobStatus(fields["status"]),
        request=RecommendRequest.model_validate_json(fields["request"]),
        created_at=datetime.fromisoformat(fields["created_at"]),
        started_at=_when(fields.get("started_at")),
        finished_at=_when(fields.get("finished_at")),
        result=json.loads(fields["result"]) if fields.get("result") else None,
        error=fields.get("error"),
    )
