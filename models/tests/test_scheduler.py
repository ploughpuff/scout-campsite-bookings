"""
test_scheduler.py - The unattended jobs and the plumbing they needed.
"""

# pylint: disable=all
import json
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import MagicMock

import models.bookings as bookings_module
from config import UK_TZ
from models.bookings import Bookings
from models.json_utils import _migrate_v5_to_v6, save_json
from models.run_state import RunState, load_run_state, save_run_state
from models.scheduler import Scheduler, next_daily_run
from models.schemas import ArchiveData, LiveData


#
## next_daily_run - the bit that has to survive the clocks changing
def _uk(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UK_TZ)


def test_next_daily_run_is_later_today_when_the_hour_is_still_ahead():
    assert next_daily_run(_uk("2026-08-10T01:30:00"), 3) == _uk("2026-08-10T03:00:00")


def test_next_daily_run_rolls_to_tomorrow_once_the_hour_has_passed():
    assert next_daily_run(_uk("2026-08-10T03:00:01"), 3) == _uk("2026-08-11T03:00:00")


def test_next_daily_run_is_exclusive_of_now():
    """Landing exactly on the hour must move on, or the job runs in a tight loop."""
    assert next_daily_run(_uk("2026-08-10T03:00:00"), 3) == _uk("2026-08-11T03:00:00")


def _real_hours_between(earlier: datetime, later: datetime) -> float:
    """Elapsed hours. Subtracting two datetimes that share a tzinfo ignores the
    zone and compares wall clocks, which is exactly what we must not do here."""
    return (later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)).total_seconds() / 3600


def test_next_daily_run_keeps_uk_wall_clock_across_spring_forward():
    """Clocks go forward 29 Mar 2026. 03:00 has to stay 03:00 in Chelmsford,
    so only 23 real hours pass between that run and the one before it."""
    target = next_daily_run(_uk("2026-03-28T04:00:00"), 3)

    assert target == _uk("2026-03-29T03:00:00")
    assert target.astimezone(UK_TZ).hour == 3
    assert _real_hours_between(_uk("2026-03-28T03:00:00"), target) == 23


def test_next_daily_run_keeps_uk_wall_clock_across_autumn_back():
    """Clocks go back 25 Oct 2026, so 25 real hours pass and the hour still holds."""
    target = next_daily_run(_uk("2026-10-24T04:00:00"), 3)

    assert target.astimezone(UK_TZ).hour == 3
    assert _real_hours_between(_uk("2026-10-24T03:00:00"), target) == 25


def test_next_daily_run_uses_uk_time_not_the_containers_utc():
    """The container runs UTC. In summer, 03:00 UK is 02:00 UTC."""
    target = next_daily_run(_uk("2026-08-10T01:00:00"), 3)
    assert target.astimezone(UK_TZ).hour == 3
    assert target.utctimetuple().tm_hour == 2


#
## The scheduler loop
class FakeBookings:
    """Just enough of Bookings for the scheduler."""

    def __init__(self, pull_error=None, archive_result=None):
        self.pulls = 0
        self.status_calls = 0
        self.archives = 0
        self.pull_error = pull_error
        self.archive_result = archive_result

    def pull_from_sheets(self):
        self.pulls += 1
        if self.pull_error:
            raise self.pull_error
        return 2

    def auto_update_statuses(self):
        self.status_calls += 1
        return ["BK-1 moved"]

    def auto_archive_old_bookings(self):
        self.archives += 1
        return self.archive_result


def test_pull_job_also_rolls_statuses_forward():
    fake = FakeBookings()
    Scheduler(fake).pull()

    assert (fake.pulls, fake.status_calls) == (1, 1)


def test_a_failing_job_does_not_kill_the_thread():
    """Nobody is watching at 03:00, so the loop has to outlive a bad run."""
    fake = FakeBookings(pull_error=RuntimeError("Google said no"))
    scheduler = Scheduler(fake)

    scheduler._guard("pull", scheduler.pull)  # must not raise

    assert fake.pulls == 1


def test_archive_job_tolerates_page_traffic_getting_there_first():
    fake = FakeBookings(archive_result=None)
    Scheduler(fake).archive()  # None means "already run today" - must not blow up

    assert fake.archives == 1


def test_start_is_idempotent():
    scheduler = Scheduler(FakeBookings(), pull_interval_minutes=60, archive_hour=3)
    try:
        scheduler.start()
        scheduler.start()  # second call warns rather than starting a rival thread
        assert scheduler._thread is not None
    finally:
        scheduler.stop()


#
## run_state
def test_run_state_round_trips(tmp_path, monkeypatch):
    import models.run_state as run_state_module

    path = tmp_path / "run_state.json"
    monkeypatch.setattr(run_state_module, "RUN_STATE_FILE_PATH", path)

    state = RunState()
    state.record_pull(added=3)
    state.record_archive(datetime(2026, 8, 10).date(), {"archived": 1, "deleted": 2})
    save_run_state(state)

    loaded = load_run_state()
    assert loaded.pull_added == 3
    assert loaded.pull_ok is True
    assert loaded.archived_on == datetime(2026, 8, 10).date()
    assert (loaded.archived_count, loaded.deleted_count) == (1, 2)


def test_unreadable_run_state_does_not_stop_the_app(tmp_path, monkeypatch):
    """It is bookkeeping. Losing it costs one extra sweep, not a startup failure."""
    import models.run_state as run_state_module

    path = tmp_path / "run_state.json"
    path.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(run_state_module, "RUN_STATE_FILE_PATH", path)

    assert load_run_state().pulled_at is None


def test_failed_pull_keeps_the_last_good_time_and_records_why():
    state = RunState()
    state.record_pull(added=1)
    good_time = state.pulled_at

    state.record_pull_failure("HttpError: 503")

    assert state.pulled_at == good_time  # age keeps climbing from the last success
    assert state.pull_ok is False
    assert "503" in state.pull_error


#
## save_json no longer burns a backup when nothing changed
def test_save_json_skips_writing_identical_content(tmp_path):
    path = tmp_path / "bookings.json"
    data = LiveData(next_idx=7)

    assert save_json(data, path) is True
    first = path.read_text(encoding="utf-8")

    assert save_json(data, path) is False  # unchanged - skipped
    assert path.read_text(encoding="utf-8") == first
    assert list(tmp_path.glob("bookings-*.json")) == []  # and no backup burned


def test_save_json_writes_and_backs_up_a_real_change(tmp_path):
    path = tmp_path / "bookings.json"
    save_json(LiveData(next_idx=7), path)

    assert save_json(LiveData(next_idx=8), path) is True
    assert len(list(tmp_path.glob("bookings-*.json"))) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["next_idx"] == 8


def test_save_json_rewrites_a_file_that_drifted_from_its_checksum(tmp_path):
    """A file edited behind our back must be repaired, not skipped."""
    path = tmp_path / "bookings.json"
    save_json(LiveData(next_idx=7), path)
    path.write_text('{"tampered": true}', encoding="utf-8")

    assert save_json(LiveData(next_idx=7), path) is True
    assert json.loads(path.read_text(encoding="utf-8"))["next_idx"] == 7


#
## schema v6 migration
def test_migration_drops_updated_from_the_live_file():
    data = _migrate_v5_to_v6(
        {"schema_version": 5, "updated": "2026-08-10T21:17:56+01:00", "next_idx": 4, "items": []}
    )

    assert "updated" not in data
    assert data["schema_version"] == 6
    assert data["next_idx"] == 4


def test_migration_leaves_the_archive_files_updated_alone():
    """archive.updated still means something - only the live file loses it."""
    data = _migrate_v5_to_v6(
        {"schema_version": 5, "updated": "2026-08-10T00:04:36+01:00", "items": [], "deleted_md5s": []}
    )

    assert data["updated"] == "2026-08-10T00:04:36+01:00"
    assert data["schema_version"] == 6


#
## age() is the scheduler's heartbeat
def test_age_reports_never_before_the_first_pull(monkeypatch):
    monkeypatch.setattr(bookings_module, "save_json", lambda data, path: None)
    manager = Bookings()
    manager.run_state = RunState()

    assert manager.age() == "NEVER"


def test_age_counts_from_the_last_pull(monkeypatch):
    from models.utils import now_uk

    monkeypatch.setattr(bookings_module, "save_json", lambda data, path: None)
    manager = Bookings()
    manager.run_state = RunState(pulled_at=now_uk() - timedelta(hours=2))

    assert manager.age().startswith("2h")


#
## pull_from_sheets - the one entry point for the button and the scheduler
@pytest.fixture
def pull_manager(monkeypatch):
    """A manager whose saves and run-state writes are captured, not performed."""
    saved, states = [], []
    monkeypatch.setattr(bookings_module, "save_json", lambda data, path: saved.append(path.name))
    monkeypatch.setattr(bookings_module, "save_run_state", lambda state: states.append(state))

    manager = Bookings()
    manager.live = LiveData(items=[])
    manager.archive = ArchiveData(items=[])
    manager.run_state = RunState()
    return manager, saved, states


def _sheet(rows):
    from models.utils import now_uk

    return {"updated": now_uk(), "data": [{"sheet_data": rows, "group_type": "g", "contains": None}]}


def test_pull_records_a_successful_run(pull_manager, monkeypatch):
    manager, _, states = pull_manager
    monkeypatch.setattr(bookings_module, "get_sheet_data", lambda: _sheet([]))

    assert manager.pull_from_sheets() == 0
    assert manager.run_state.pulled_at is not None
    assert manager.run_state.pull_ok is True
    assert states  # persisted, so the age survives a restart


def test_empty_pull_writes_no_bookings_file(pull_manager, monkeypatch):
    """The whole point of moving last-pulled out of bookings.json: an hourly
    pull that finds nothing must not rewrite the file and rotate a backup."""
    manager, saved, _ = pull_manager
    monkeypatch.setattr(bookings_module, "get_sheet_data", lambda: _sheet([]))

    manager.pull_from_sheets()

    assert saved == []


def test_failed_pull_is_recorded_and_re_raised(pull_manager, monkeypatch):
    manager, _, states = pull_manager

    def boom():
        raise RuntimeError("Google said no")

    monkeypatch.setattr(bookings_module, "get_sheet_data", boom)

    with pytest.raises(RuntimeError):
        manager.pull_from_sheets()

    assert manager.run_state.pull_ok is False
    assert "Google said no" in manager.run_state.pull_error
    assert states  # written even though the pull failed, or 3am failures vanish


def test_one_bad_row_does_not_lose_the_rest_of_the_pull(pull_manager, monkeypatch):
    """Unattended, an exception here would mean no new bookings until someone noticed."""
    manager, _, _ = pull_manager
    monkeypatch.setattr(bookings_module, "get_sheet_data", lambda: _sheet([{"bad": 1}, {"good": 2}]))

    good = object()
    calls = []

    def fake_create(row, md5, group_type, contains):
        calls.append(row)
        if "bad" in row:
            raise ValueError("unparseable timestamp")
        rec = MagicMock()
        rec.booking.id = "OK-1"
        rec.tracking.notes = ""
        return rec

    monkeypatch.setattr(manager, "create_rec_from_sheet_row", fake_create)

    added = manager.pull_from_sheets()

    assert len(calls) == 2  # it carried on past the bad row
    assert added == 1
