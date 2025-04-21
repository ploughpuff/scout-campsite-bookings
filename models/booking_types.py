"""
booking_types.py - Contains the class BookingType.

"""

import re
from enum import Enum


class BookingType(Enum):
    """Class BookingType for functions to generate next booking ID."""

    DISTRICT_DAY_VISIT = ("district_day_visit", "CDS")
    SCHOOL = ("school", "SCH")

    def __init__(self, label, prefix):
        self.label = label
        self.prefix = prefix

    def __str__(self):
        return self.label


def gen_next_booking_id(existing_ids, booking_type: BookingType, year):
    """
    Generate the next available booking ID for a given booking type and year.
    IDs are in the format: PREFIX-YYYY-XXXX (e.g. SCH-2025-0003)
    Ensures the generated ID does not already exist in existing_ids.
    """

    prefix = booking_type.prefix
    pattern = rf"{prefix}-{year}-(\d+)"

    nums = [
        int(match.group(1)) for bid in existing_ids if (match := re.match(pattern, bid))
    ]

    next_number = max(nums, default=0) + 1

    while True:
        candidate_id = f"{prefix}-{year}-{next_number:04d}"
        if candidate_id not in existing_ids:
            return candidate_id
        next_number += 1


def get_booking_type_choices():
    """
    Return list of tuples: [(name, label), ...]
    Useful for dropdowns or form options.
    """
    return [(bt.name, bt.label) for bt in BookingType]


def parse_booking_type(value) -> BookingType | None:
    """
    Convert a string to a BookingType enum if valid.
    Returns None if invalid or missing.

    Args:
        value (str): The string to convert.

    Returns:
        BookingType or None
    """
    try:
        return BookingType[value]
    except (KeyError, TypeError):
        return None
