"""
test_schemas.py
"""

# pylint: disable=all
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from config import UK_TZ
from models.booking_types import BookingType
from models.schemas import ArchiveData, LeaderData, LiveData, SiteData, SitePlusLeader

valid_leader = {"name": "Alice", "email": "alice@example.com", "phone": "07123456789"}

valid_site = {
    "idx": 1,
    "id": "abc123",
    "original_sheet_md5": "somehash",
    "booking_type": BookingType.SCHOOL,
    "group_name": "1st Scouts",
    "group_size": 20,
    "status": "Confirmed",
    "invoice": True,
    "notes": "All good.",
    "submitted": datetime.now(timezone.utc).isoformat(),
    "arriving": datetime.now(timezone.utc).isoformat(),
    "departing": datetime.now(timezone.utc).isoformat(),
}


def test_valid_leader_data():
    leader = LeaderData(**valid_leader)
    assert leader.name == "Alice"


def test_invalid_leader_data_missing_field():
    bad_data = valid_leader.copy()
    del bad_data["email"]
    with pytest.raises(Exception):
        LeaderData(**bad_data)


def test_valid_site_data():
    site = SiteData(**valid_site)
    assert site.group_name == "1st Scouts"
    assert site.booking_type == BookingType.SCHOOL


def test_invalid_site_data_missing_required():
    bad_data = valid_site.copy()
    del bad_data["group_name"]
    with pytest.raises(Exception):
        SiteData(**bad_data)


def test_invalid_site_data_bad_enum():
    bad_data = valid_site.copy()
    bad_data["booking_type"] = "not_a_type"
    with pytest.raises(Exception):
        SiteData(**bad_data)


def test_site_data_timezone_assignment_from_naive_string():
    bad_data = valid_site.copy()
    naive_time = datetime.now().replace(microsecond=0)
    bad_data["submitted"] = naive_time.isoformat()  # No tzinfo
    site = SiteData(**bad_data)
    assert site.submitted.tzinfo == UK_TZ


def test_site_data_timezone_preserved_from_aware_string():
    aware_time = datetime.now(ZoneInfo("Europe/London")).replace(microsecond=0).isoformat()
    data = valid_site.copy()
    data["submitted"] = aware_time
    site = SiteData(**data)
    assert site.submitted.tzinfo is not None
    assert site.submitted.tzinfo.utcoffset(site.submitted) == UK_TZ.utcoffset(site.submitted)


def test_site_plus_leader_valid():
    site = SiteData(**valid_site)
    leader = LeaderData(**valid_leader)
    bundle = SitePlusLeader(site=site, leader=leader)
    assert bundle.site.group_name == "1st Scouts"
    assert bundle.leader.email == "alice@example.com"


def test_live_data_default_structure():
    live = LiveData()
    assert isinstance(live.bookings, list)
    assert isinstance(live.updated, datetime)
    assert live.next_idx == 1


def test_archive_data_default_structure():
    archive = ArchiveData()
    assert isinstance(archive.bookings, list)
    assert isinstance(archive.updated, datetime)


def test_site_data_validation_helpers():
    site = SiteData(**valid_site)
    assert site.is_valid() is True
    assert site.get_problematic_data() is None


def test_site_data_validation_failure():
    bad_data = valid_site.copy()
    bad_data["group_size"] = "twenty"  # Invalid group_size (should be an int)

    # Expecting ValidationError when passing bad data
    with pytest.raises(ValidationError):
        SiteData(**bad_data)

    # Using try/except with specific exception
    bad_data["group_size"] = "twenty"  # Invalid group_size
    try:
        site = SiteData(**bad_data)
    except ValidationError:
        pass  # Expected ValidationError
    else:
        assert site.is_valid() is False
