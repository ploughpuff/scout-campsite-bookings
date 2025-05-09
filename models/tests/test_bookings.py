"""
test_bookings.py
"""

# pylint: disable=all
from datetime import datetime, timezone

import pytest

from models.bookings import Bookings, SiteData
from models.schemas import ArchiveData, LeaderData, LiveData, SiteData, SitePlusLeader

valid_leader = {"name": "Alice", "email": "alice@example.com", "phone": "07123456789"}

valid_site_data = {
    "idx": 1,
    "id": "abc123",
    "original_sheet_md5": "somehash",
    "group_type": "chelmsford_district",
    "group_name": "1st Scouts",
    "group_size": 20,
    "status": "Confirmed",
    "invoice": True,
    "notes": "All good.",
    "submitted": datetime.now(timezone.utc).isoformat(),
    "arriving": datetime.now(timezone.utc).isoformat(),
    "departing": datetime.now(timezone.utc).isoformat(),
}

live_data = {
    "updated": datetime.now(timezone.utc).isoformat(),
    "next_idx": 1,
    "bookings": [
        {"site": valid_site_data, "leader": valid_leader},
        {"site": valid_site_data, "leader": valid_leader},
    ],
}

archive_data = {
    "updated": datetime.now(timezone.utc).isoformat(),
    "bookings": [valid_site_data, valid_site_data],
}


@pytest.fixture
def test_booking_manager():
    test_site = LiveData(**live_data)
    test_archive = ArchiveData(**archive_data)

    # Create instance and inject test data
    manager = Bookings()
    manager.set_test_data(test_site, test_archive)
    return manager


def test_find_md5_in_data(test_booking_manager):
    assert test_booking_manager._find_booking_by_md5("somehash") is True
    assert test_booking_manager._find_booking_by_md5("def456") is False
