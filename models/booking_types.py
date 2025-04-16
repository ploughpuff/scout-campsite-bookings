from enum import Enum
from datetime import datetime
import re

class BookingType(Enum):
    DISTRICT_DAY_VISIT = ("district_day_visit", "RDV")
    SCHOOL = ("school", "RPE")

    def __init__(self, label, prefix):
        self.label = label
        self.prefix = prefix

    def __str__(self):
        return self.label


def generate_next_booking_id(existing_ids, booking_type: BookingType, year=None):
    """
    Generate the next available booking ID for a given booking type and year.
    IDs are in the format: PREFIX-YYYY-XXXX (e.g. SCH-2025-0003)
    Ensures the generated ID does not already exist in existing_ids.
    """
    if not year:
        year = datetime.now().year

    prefix = booking_type.prefix
    pattern = rf"{prefix}-{year}-(\d+)"

    nums = [
        int(match.group(1))
        for bid in existing_ids
        if (match := re.match(pattern, bid))
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
