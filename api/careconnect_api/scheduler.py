"""APScheduler bootstrap for in-process background jobs.

Currently runs the daily medical-risk triage at ``settings.triage_cron_*``
(default 02:00 server-local). The Java original used Spring's ``@Scheduled``
on the same cron — see :mod:`careconnect_api.triage.runner` for the porting
notes.

Hooked into the FastAPI lifespan in :mod:`careconnect_api.main`:
:func:`start_scheduler` runs before ``yield``; :func:`stop_scheduler` runs
after. Single-process only — if we ever scale to multiple uvicorn workers
this needs to move to a leader-elected sidecar (or APScheduler's
SQLAlchemyJobStore with ``coalesce``+``max_instances=1``).
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .settings import settings
from .triage.runner import run_for_all


log = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler()


def start_scheduler() -> None:
    """Register the daily-triage job and start the scheduler."""
    scheduler.add_job(
        run_for_all,
        CronTrigger(hour=settings.triage_cron_hour, minute=settings.triage_cron_minute),
        id="daily_triage",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "scheduler started (daily_triage at %02d:%02d)",
        settings.triage_cron_hour, settings.triage_cron_minute,
    )


def stop_scheduler() -> None:
    """Shut the scheduler down without waiting for in-flight jobs to finish."""
    scheduler.shutdown(wait=False)
    log.info("scheduler stopped")
