"""
scheduler.py - The unattended jobs, run inside the Flask process.

In-process on purpose. The app keeps every booking in memory and rewrites
bookings.json wholesale, so a separate cron process writing that file would
have its work overwritten the moment the app next saved. gunicorn runs a single
worker (see the lock comment at the top of xero.py), so there is exactly one of
these threads and no leader election to get wrong.

Three jobs:

  - pull from the booking forms, hourly, so new requests are already on the
    page when you open it rather than waiting for someone to press Pull Now
  - archive sweep, once a day at 03:00 UK time
  - drain the outbox, every tick, so outbound work stranded by a network
    outage resumes within half a minute of the network coming back

Page traffic still triggers the archive sweep as well. Both routes go through
Bookings.auto_archive_old_bookings(), which does the work at most once a day, so
whichever happens first wins and a sweep missed while the NAS was off still
happens on your next visit.
"""

import logging
import threading
from datetime import datetime, timedelta

from config import ARCHIVE_AT_HOUR, PULL_INTERVAL_MINUTES, UK_TZ
from models import outbox
from models.utils import now_uk

logger = logging.getLogger("app_logger")

#
## How long to sleep between checks. Short enough that stop() is responsive and
## a clock jump can't strand us, long enough to be invisible.
TICK_SECONDS = 30.0

#
## How long a job may take before it is worth complaining about. Comfortably
## more than a pull that is merely slow, comfortably less than the hour until
## the next one, so an overrun is always visible before it starts costing pulls.
JOB_BUDGET_SECONDS = 300.0


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
        #
        ## What this thread is in the middle of, as (name, started_at), or None
        ## when it is idle. Written only by the scheduler thread and read by
        ## request threads, which is safe for a single tuple assignment.
        self._running = None

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

            #
            ## Every tick. Items that are not due yet are skipped by the queue
            ## itself, so an empty or waiting outbox costs one list comprehension
            ## - and work stranded by an outage restarts within 30 seconds of the
            ## network coming back.
            self._guard("outbox drain", self.drain_outbox)

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

    def drain_outbox(self) -> None:
        """Push whatever outbound work is due. Quiet when there is none."""
        tally = outbox.drain()
        if any(tally.values()):
            logger.info(
                "Outbox drain: %d sent, %d to retry, %d blocked",
                tally["sent"],
                tally["retry"],
                tally["blocked"],
            )

    def stuck_job(self):
        """The job that has overrun its budget, as (name, seconds), or None.

        Deliberately readable from a request thread: a job blocked on a dead
        socket cannot notice its own hang, because the loop that would notice is
        the thing that is blocked. Asking from outside is the only way to see it.
        """
        running = self._running
        if running is None:
            return None

        what, started = running
        elapsed = (now_uk() - started).total_seconds()
        if elapsed < JOB_BUDGET_SECONDS:
            return None
        return (what, elapsed)

    def _guard(self, what: str, job) -> None:
        """Run a job, swallowing anything it throws.

        Nobody is watching at 03:00, so a failure has to be survivable: the
        thread stays alive for the next attempt, the traceback goes to the log,
        and a failed pull is already recorded in run_state for the Admin page.
        """
        started = now_uk()
        self._running = (what, started)
        try:
            job()
        except Exception:  # pylint: disable=broad-except
            logger.exception("Scheduled %s failed", what)
        finally:
            self._running = None
            elapsed = (now_uk() - started).total_seconds()
            if elapsed >= JOB_BUDGET_SECONDS:
                #
                ## It finished, so nothing is stuck now - but a job that takes
                ## this long is one timeout away from blocking every later tick,
                ## and the log is where that pattern becomes visible.
                logger.warning("Scheduled %s took %.0fs", what, elapsed)
