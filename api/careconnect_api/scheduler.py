"""APScheduler bootstrap for in-process background jobs.

Currently runs:

* daily medical-risk triage at ``settings.triage_cron_*`` (default 02:00)
* Google Calendar read-only reminder poll every ``settings.gcal_poll_seconds``
  (default 60s)

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
from apscheduler.triggers.interval import IntervalTrigger

from .calendar_poller import poll_google_calendars
from .settings import settings
from .triage.runner import run_for_all


log = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler()


def start_scheduler() -> None:
    """Register jobs and start the scheduler."""
    scheduler.add_job(
        run_for_all,
        CronTrigger(hour=settings.triage_cron_hour, minute=settings.triage_cron_minute),
        id="daily_triage",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        replace_existing=True,
    )
    scheduler.add_job(
        poll_google_calendars,
        IntervalTrigger(seconds=max(15, int(settings.gcal_poll_seconds))),
        id="google_calendar_poll",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=55,
        replace_existing=True,
    )
    scheduler.start()
    log.info(
        "scheduler started (daily_triage at %02d:%02d, google_calendar_poll every %ss)",
        settings.triage_cron_hour,
        settings.triage_cron_minute,
        settings.gcal_poll_seconds,
    )


def stop_scheduler() -> None:
    """Shut the scheduler down without waiting for in-flight jobs to finish."""
    scheduler.shutdown(wait=False)
    log.info("scheduler stopped")
