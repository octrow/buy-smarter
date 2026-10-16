"""taskiq worker: runs recommend jobs off a Redis stream and writes the outcome to the job record.

    taskiq worker buysmarter.worker:broker --ack-type when_executed --workers 1 --max-async-tasks 2

``when_executed``: a message is acknowledged only after the job ran, so the job of a worker that
died mid-run is reclaimed by another one (``XAUTOCLAIM`` after ``_IDLE_TIMEOUT_MS``).
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

JOB_TIMEOUT_S = 300
# Longer than the job timeout: a job still within its time is never handed to a second worker.
_IDLE_TIMEOUT_MS = 10 * 60 * 1000
_CLAIM_LOCK_S = 60
QUEUE = "buysmarter:recommend"

# Before the broker exists: carries the trace from POST /recommend into the job.
TaskiqInstrumentor().instrument()


def _make_broker() -> AsyncBroker:
    if os.environ.get("BUYSMARTER_BROKER") == "memory":  # tests: in-process, no Redis
        return InMemoryBroker()
    return RedisStreamBroker(
        url=get_settings().redis_url,
        queue_name=QUEUE,
        consumer_group_name="buysmarter-workers",
        idle_timeout=_IDLE_TIMEOUT_MS,
        unacknowledged_lock_timeout=_CLAIM_LOCK_S,
    )


broker = _make_broker()


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _startup(_: TaskiqState) -> None:
    if not broker.is_worker_process:  # the in-memory test broker fires worker events in-process
        return
    logsetup.setup_logging()
    telemetry.instrument_worker(get_settings().worker_metrics_port)


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
            JOB_TIMEOUT_S,
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
        logger.warning("job %s: timed out after %ss", job.id, JOB_TIMEOUT_S)
        return JobStatus.FAILED, None, "timeout"
    except Exception:
        logger.exception("job %s: pipeline failed", job.id)
        return JobStatus.FAILED, None, "internal"
    outcome = RecommendOutcome.from_result(result, render_markdown(result, req.top))
    return JobStatus.DONE, outcome.model_dump(mode="json"), None
