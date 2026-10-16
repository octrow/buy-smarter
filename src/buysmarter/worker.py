"""taskiq worker: runs recommend jobs off a Redis stream and writes the outcome to the job record.

    taskiq worker buysmarter.worker:broker --ack-type when_executed --workers 1 --max-async-tasks 2

``when_executed``: a message is acknowledged only after the job ran, so the job of a worker that
died mid-run is reclaimed by another one (``XAUTOCLAIM`` after twice the job timeout).
"""

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

from taskiq import AsyncBroker, InMemoryBroker, TaskiqEvents, TaskiqState
from taskiq.instrumentation import TaskiqInstrumentor
from taskiq_redis import RedisStreamBroker

from buysmarter import logsetup, telemetry
from buysmarter.cli.report import render_markdown, render_no_substance_markdown
from buysmarter.config import get_settings
from buysmarter.jobs import Job, JobStatus, JobStore, RecommendOutcome, get_redis
from buysmarter.services.recommend import NoSubstanceMatch, recommend

logger = logging.getLogger(__name__)

# A message unacked this long belongs to a dead worker. Twice the job timeout: a job still within
# its time is never handed to a second worker.
_IDLE_TIMEOUT_FACTOR = 2
_CLAIM_LOCK_S = 60
QUEUE = "buysmarter:recommend"

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
# message; drop this once RedisStreamBroker.listen() reclaims on empty reads too.
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
    status, result, error = await _execute(job)
    await store.finish(job_id, status, result=result, error=error)
    telemetry.JOBS.add(1, {"status": status.value})
    logger.info("job %s: %s", job_id, status.value)


async def _execute(job: Job) -> tuple[JobStatus, dict[str, Any] | None, str | None]:
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
            ),
            timeout_s,
        )
    except NoSubstanceMatch as exc:
        outcome = RecommendOutcome(
            report_markdown=render_no_substance_markdown(exc), reason=exc.message()
        )
        return JobStatus.NO_MATCH, outcome.model_dump(mode="json"), None
    except ValueError as exc:  # the ranking engine's "no candidates passed hard filters"
        outcome = RecommendOutcome(
            report_markdown=f"# buy-smarter recommendation\n\n{exc}\n", reason=str(exc)
        )
        return JobStatus.NO_MATCH, outcome.model_dump(mode="json"), None
    except TimeoutError:
        logger.warning("job %s: timed out after %ss", job.id, timeout_s)
        return JobStatus.FAILED, None, "timeout"
    except Exception:
        logger.exception("job %s: pipeline failed", job.id)
        return JobStatus.FAILED, None, "internal"
    outcome = RecommendOutcome.from_result(result, render_markdown(result, req.top))
    return JobStatus.DONE, outcome.model_dump(mode="json"), None
