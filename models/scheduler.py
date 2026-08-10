"""
scheduler.py - The unattended jobs, run inside the Flask process.

In-process on purpose. The app keeps every booking in memory and rewrites
bookings.json wholesale, so a separate cron process writing that file would
have its work overwritten the moment the app next saved. gunicorn runs a single
worker (see the lock comment at the top of xero.py), so there is exactly one of
these threads and no leader election to get wrong.

Two jobs:

  - pull from the booking forms, hourly, so new requests are already on the
    page when you open it rather than waiting for someone to press Pull Now
  - archive sweep, once a day at 03:00 UK time

Page traffic still triggers the archive sweep as well. Both routes go through
Bookings.auto_archive_old_bookings(), which does the work at most once a day, so
whichever happens first wins and a sweep missed while the NAS was off still
happens on your next visit.
"""

import logging
import threading
from datetime import datetime, timedelta

from config import ARCHIVE_AT_HOUR, PULL_INTERVAL_MINUTES, UK_TZ
from models.utils import now_uk

logger = logging.getLogger("app_logger")

#
## How long to sleep between checks. Short enough that stop() is responsive and
## a clock jump can't strand us, long enough to be invisible.
TICK_SECONDS = 30.0


def next_daily_run(now: datetime, hour: int) -> datetime:
    """The next time it is `hour` o'clock in the UK, strictly after `now`.

    Worked out in UK local time rather than by adding 24 hours to the last run,
    because the container runs UTC and the clocks change twice a year: 03:00
    has to mean 03:00 in Chelmsford, not a fixed offset from UTC. Adding a day
    to an aware datetime is wall-clock arithmetic, so the hour survives the
    change. 03:00 is also safe from the spring-forward gap, which swallows
    01:00-02:00.
    """
    local = now.astimezone(UK_TZ)
    target = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target


class Scheduler:
    """Owns the background thread. One per app."""

    def __init__(self, bookings, pull_interval_minutes=None, archive_hour=None):
        self.bookings = bookings
        if pull_interval_minutes is None:
            pull_interval_minutes = PULL_INTERVAL_MINUTES
        self.pull_interval = timedelta(minutes=pull_interval_minutes)
        self.archive_hour = ARCHIVE_AT_HOUR if archive_hour is None else archive_hour
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> None:
        """Start the background thread. Daemon, so it never holds up a shutdown."""
        if self._thread is not None:
            logger.warning("Scheduler already started - ignoring")
            return

        self._thread = threading.Thread(target=self.run, name="scheduler", daemon=True)
        self._thread.start()
        logger.info(
            "Scheduler started: pull every %s min, archive sweep at %02d:00 UK",
            int(self.pull_interval.total_seconds() // 60),
            self.archive_hour,
        )

    def stop(self) -> None:
        """Ask the thread to finish. Used by the tests."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=TICK_SECONDS + 5)

    def run(self) -> None:
        """The loop. Never raises: a job that throws must not kill the thread."""
        #
        ## Pull straight away on startup, so a restart doubles as a refresh.
        next_pull = now_uk()
        next_archive = next_daily_run(now_uk(), self.archive_hour)

        while not self._stop.is_set():
            now = now_uk()

            if now >= next_pull:
                self._guard("pull", self.pull)
                next_pull = now_uk() + self.pull_interval

            if now >= next_archive:
                self._guard("archive sweep", self.archive)
                next_archive = next_daily_run(now_uk(), self.archive_hour)

            self._stop.wait(TICK_SECONDS)

    def pull(self) -> None:
        """Fetch new booking forms, then roll any departed bookings forward."""
        added = self.bookings.pull_from_sheets()
        if added:
            logger.info("Scheduled pull added %d booking(s)", added)

        for message in self.bookings.auto_update_statuses():
            logger.info("%s", message)

    def archive(self) -> None:
        """Run the daily sweep, unless page traffic already did it today."""
        result = self.bookings.auto_archive_old_bookings()
        if result is None:
            logger.info("Archive sweep already run today - skipped")
        else:
            logger.info(
                "Archive sweep: %d archived, %d deleted", result["archived"], result["deleted"]
            )

    def _guard(self, what: str, job) -> None:
        """Run a job, swallowing anything it throws.

        Nobody is watching at 03:00, so a failure has to be survivable: the
        thread stays alive for the next attempt, the traceback goes to the log,
        and a failed pull is already recorded in run_state for the Admin page.
        """
        try:
            job()
        except Exception:  # pylint: disable=broad-except
            logger.exception("Scheduled %s failed", what)
