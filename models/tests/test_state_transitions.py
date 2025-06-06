"""
test_state_transition.py
"""

# pylint: disable=all

from typing import get_args

import pytest

from models.bookings import status_transitions
from models.schemas import TrackingData


def get_valid_statuses():
    return list(get_args(TrackingData.__annotations__["status"]))


def test_status_transitions_valid():
    valid_statuses = get_valid_statuses()

    for source, destinations in status_transitions.items():
        assert source in valid_statuses, f"Invalid source status: {source}"
        for dest in destinations:
            assert dest in valid_statuses, f"Invalid destination status: {source} -> {dest}"
