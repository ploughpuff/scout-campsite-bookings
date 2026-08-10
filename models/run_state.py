"""
run_state.py - Bookkeeping for the work the app does on its own.

Deliberately kept out of bookings.json. That file is backed up on every write
and only 50 backups are kept, so stamping a "last pulled" time into it meant an
idle pull burned a backup slot for no change in content - the ring covered
three weeks when it should cover months. This file carries the scheduler's own
state instead: it is tiny, rewritten in place, and never backed up.

Losing it is harmless. A missing or unreadable file reads back as "never run",
which costs one extra archive sweep and nothing else, so it is loaded
defensively rather than being allowed to stop the app from starting.
"""

import json
import logging
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ValidationError

from config import RUN_STATE_FILE_PATH
from models.json_utils import atomic_write_json
from models.utils import now_uk

logger = logging.getLogger("app_logger")


class RunState(BaseModel):
    """When the unattended jobs last ran, and how they got on."""

    #
    ## Stamped on every successful pull, whether or not it found anything, so
    ## the "Bookings Last Retrieved" age doubles as the scheduler's heartbeat.
    pulled_at: Optional[datetime] = None
    pull_ok: bool = True
    pull_added: int = 0
    pull_error: Optional[str] = None

    archived_on: Optional[date] = None
    archived_count: int = 0
    deleted_count: int = 0

    def record_pull(self, added: int) -> None:
        """Note a successful pull."""
        self.pulled_at = now_uk()
        self.pull_ok = True
        self.pull_added = added
        self.pull_error = None

    def record_pull_failure(self, error: str) -> None:
        """Note a failed pull, leaving pulled_at showing the last good one."""
        self.pull_ok = False
        self.pull_error = error

    def record_archive(self, on: date, result: dict) -> None:
        """Note an archive sweep."""
        self.archived_on = on
        self.archived_count = result.get("archived", 0)
        self.deleted_count = result.get("deleted", 0)


def load_run_state() -> RunState:
    """Read the run state, falling back to a blank one if it is missing or bad."""
    if not RUN_STATE_FILE_PATH.exists():
        return RunState()

    try:
        return RunState.model_validate(
            json.loads(RUN_STATE_FILE_PATH.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, ValidationError) as exc:
        logger.warning("Could not read %s (%s) - starting fresh.", RUN_STATE_FILE_PATH.name, exc)
        return RunState()


def save_run_state(state: RunState) -> None:
    """Write the run state. Never raises: this is bookkeeping, not booking data."""
    try:
        RUN_STATE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(state.model_dump(mode="json"), RUN_STATE_FILE_PATH)
    except OSError as exc:
        logger.warning("Could not write %s: %s", RUN_STATE_FILE_PATH.name, exc)
