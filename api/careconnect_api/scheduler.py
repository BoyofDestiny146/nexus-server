"""APScheduler bootstrap for in-process background jobs.

Currently runs:

* per-client Care & Wellness due-check every ``settings.assessment_due_check_seconds``
  (default 300s). Sales is not on this job.
* Google Calendar read-only reminder poll every ``settings.gcal_poll_seconds``
  (default 60s)

Hooked into the FastAPI lifespan in :mod:`careconnect_api.main`:
:func:`start_scheduler` runs before ``yield``; :func:`stop_scheduler` runs
after. Single-process only — if we ever scale to multiple uvicorn workers
this needs to move to a leader-elected sidecar (or APScheduler's
SQLAlchemyJobStore with ``coalesce``+``max_instances=1``). Redis + in-process
locks additionally prevent double-runs of the same client.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .assessment_scheduler import tick_due_assessments
from .calendar_poller import poll_google_calendars
from .settings import settings


log = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler()


def start_scheduler() -> None:
    """Register jobs and start the scheduler."""
    scheduler.add_job(
        tick_due_assessments,
        IntervalTrigger(seconds=max(60, int(settings.assessment_due_check_seconds))),
        id="assessment_due_check",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=55,
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
        "scheduler started (assessment_due_check every %ss, google_calendar_poll every %ss)",
        settings.assessment_due_check_seconds,
        settings.gcal_poll_seconds,
    )


def stop_scheduler() -> None:
    """Shut the scheduler down without waiting for in-flight jobs to finish."""
    scheduler.shutdown(wait=False)
    log.info("scheduler stopped")
