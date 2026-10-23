"""taskiq worker: runs recommend jobs off a Redis stream and writes the outcome to the job record.

    taskiq worker buysmarter.worker:broker --ack-type when_executed --workers 1 --max-async-tasks 2

``when_executed``: a message is acknowledged only after the job ran, so the job of a worker that
died mid-run is reclaimed by another one (``XAUTOCLAIM`` after twice the job timeout).
"""

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

try:
    import redis  # noqa: F401  (jobs.py needs it)
    from taskiq import AsyncBroker, InMemoryBroker, TaskiqEvents, TaskiqState
    from taskiq.instrumentation import TaskiqInstrumentor
    from taskiq_redis import RedisStreamBroker
except ModuleNotFoundError as exc:
    sys.exit(f"{exc.name} is missing: the worker needs `pip install buysmarter[server]`")

from buysmarter import logsetup, telemetry
from buysmarter.cli.report import (
    REPORT_TITLE,
    render_clarification_markdown,
    render_markdown,
    render_no_new_pick_markdown,
    render_no_substance_markdown,
)
from buysmarter.config import get_settings
from buysmarter.jobs import Job, JobStatus, JobStore, RecommendOutcome, get_redis
from buysmarter.services.auto_learn import auto_learn, mark_learn_failed, should_auto_learn
from buysmarter.services.recommend import (
    NoNewPick,
    NoSubstanceMatch,
    RecommendResult,
    recommend,
)
from buysmarter.services.understanding import NeedsClarification

# ``RecommendOutcome.reason`` of a job that stopped at a question.
NEEDS_CLARIFICATION = "needs_clarification"

logger = logging.getLogger(__name__)

# A message unacked this long belongs to a dead worker. Twice the job timeout: a job still within
# its time is never handed to a second worker.
_IDLE_TIMEOUT_FACTOR = 2
_CLAIM_LOCK_S = 60
QUEUE = "buysmarter:recommend"

# A category learn: its own time limit (an opus run with web search), and a Redis slot that keeps
# the same category from being queued again while one is queued or running (the slot outlives
# the limit, then a dead worker's slot expires by itself).
LEARN_TIMEOUT_S = 600
_LEARN_SLOT_TTL_S = LEARN_TIMEOUT_S + 60
_LEARN_SLOT_PREFIX = "buysmarter:learn:"

# Before the broker exists: carries the trace from POST /recommend into the job.
TaskiqInstrumentor().instrument()


def _make_broker() -> AsyncBroker:
    if os.environ.get("BUYSMARTER_BROKER") == "memory":  # tests: in-process, no Redis
        return InMemoryBroker()
    settings = get_settings()
    return RedisStreamBroker(
        url=settings.redis_url,
        queue_name=QUEUE,
        consumer_group_name="buysmarter-workers",
        idle_timeout=settings.job_timeout_s * _IDLE_TIMEOUT_FACTOR * 1000,
        unacknowledged_lock_timeout=_CLAIM_LOCK_S,
    )


broker = _make_broker()


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _startup(state: TaskiqState) -> None:
    if not broker.is_worker_process:  # the in-memory test broker fires worker events in-process
        return
    settings = get_settings()
    logsetup.setup_logging()
    telemetry.instrument_worker(settings.worker_metrics_port)
    idle_s = settings.job_timeout_s * _IDLE_TIMEOUT_FACTOR
    state.nudger = asyncio.create_task(_nudge_reclaim(idle_s / 2))


# ponytail: taskiq-redis 1.2.4 runs XAUTOCLAIM only after a non-empty XREADGROUP, so on an idle
# queue a dead worker's job waits for the next message. A periodic no-op message is that next
# message; drop it (and reclaim_nudge) once a release ships the time-based reclaim of
# https://github.com/taskiq-python/taskiq-redis/pull/129.
@broker.task(task_name="buysmarter.reclaim_nudge")
async def reclaim_nudge() -> None:
    """No-op: its delivery makes the broker look for unacknowledged jobs."""


async def _nudge_reclaim(period_s: float) -> None:
    while True:
        await asyncio.sleep(period_s)
        await reclaim_nudge.kiq()


@broker.task(task_name="buysmarter.recommend")
async def recommend_job(job_id: str) -> None:
    await run_job(JobStore(get_redis()), job_id)


async def run_job(store: JobStore, job_id: str) -> None:
    """Run one job unless it is gone or already finished (a redelivered message)."""
    job = await store.get(job_id)
    if job is None or not await store.start(job_id):
        logger.info("job %s: nothing to run (expired or already finished)", job_id)
        return
    telemetry.JOB_WAIT.record((datetime.now(UTC) - job.created_at).total_seconds())
    status, result, error, run = await _execute(job)
    await store.finish(job_id, status, result=result, error=error)
    telemetry.JOBS.add(1, {"status": status.value})
    logger.info("job %s: %s", job_id, status.value)
    if run is not None and get_settings().auto_learn:  # the job is over: learning never delays it
        await _dispatch_learning(store, run, use_llm=job.request.use_llm)


@broker.task(task_name="buysmarter.learn_category")
async def learn_category_job(product_type: str) -> None:
    """Learn one category within ``LEARN_TIMEOUT_S``; a failure is marked and logged."""
    try:
        await asyncio.wait_for(auto_learn(product_type), LEARN_TIMEOUT_S)
    except TimeoutError:
        logger.warning("learning of %r timed out after %ss", product_type, LEARN_TIMEOUT_S)
        await mark_learn_failed(product_type)


async def _dispatch_learning(store: JobStore, result: RecommendResult, *, use_llm: bool) -> None:
    """Queue the learning of a new category (decision 12); never raises into the finished job."""
    c = result.constraints
    try:
        if not await should_auto_learn(
            c.product_type,
            intent=c.intent,
            use_llm=use_llm,
            knowledge_missing=result.knowledge_missing,
        ):
            return
        slot = _LEARN_SLOT_PREFIX + c.product_type.strip().lower()
        if not await store.redis.set(slot, "1", nx=True, ex=_LEARN_SLOT_TTL_S):
            return
        await learn_category_job.kiq(c.product_type)
    except Exception:
        logger.exception("could not queue the learning of %r", c.product_type)


async def _execute(
    job: Job,
) -> tuple[JobStatus, dict[str, Any] | None, str | None, RecommendResult | None]:
    """The job's outcome and, when it recommended, the run itself (for auto-learning)."""
    req = job.request
    timeout_s = get_settings().job_timeout_s
    try:
        result = await asyncio.wait_for(
            recommend(
                req.query,
                city=req.city,
                budget=req.budget,
                use_llm=req.use_llm,
                trusted_only=req.trusted_only,
                sources=req.sources,
                answer=req.answer,
                clarify_round=req.clarify_round,
                no_clarify=req.no_clarify,
            ),
            timeout_s,
        )
    except NeedsClarification as exc:  # not a ValueError: a question, not "nothing found"
        outcome = RecommendOutcome(
            report_markdown=render_clarification_markdown(exc.clarification),
            reason=NEEDS_CLARIFICATION,
            clarification=exc.clarification,
        )
        return JobStatus.NO_MATCH, outcome.model_dump(mode="json"), None, None
    except NoSubstanceMatch as exc:
        outcome = RecommendOutcome(
            report_markdown=render_no_substance_markdown(exc), reason=exc.message()
        )
        return JobStatus.NO_MATCH, outcome.model_dump(mode="json"), None, None
    except NoNewPick as exc:
        outcome = RecommendOutcome(
            report_markdown=render_no_new_pick_markdown(exc),
            reason=str(exc),
            rejected_reasons=exc.rejected_reasons,
            used_listings=exc.used_listings,
        )
        return JobStatus.NO_MATCH, outcome.model_dump(mode="json"), None, None
    except ValueError as exc:  # the ranking engine's "no candidates passed hard filters"
        outcome = RecommendOutcome(report_markdown=f"{REPORT_TITLE}\n\n{exc}\n", reason=str(exc))
        return JobStatus.NO_MATCH, outcome.model_dump(mode="json"), None, None
    except TimeoutError:
        logger.warning("job %s: timed out after %ss", job.id, timeout_s)
        return JobStatus.FAILED, None, "timeout", None
    except Exception:
        logger.exception("job %s: pipeline failed", job.id)
        return JobStatus.FAILED, None, "internal", None
    outcome = RecommendOutcome.from_result(result, render_markdown(result, req.top))
    return JobStatus.DONE, outcome.model_dump(mode="json"), None, result
