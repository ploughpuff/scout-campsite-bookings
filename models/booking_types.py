"""
booking_types.py - Contains the class BookingType.

"""

from enum import Enum

from config import get_field_mappings


class BookingType(Enum):
    """Class BookingType for functions to generate next booking ID."""

    DISTRICT_DAY_VISIT = "district_day_visit"
    SCHOOL = "school"

    @property
    def prefix(self):
        """Find the PREFIX from the field mappings JSON file"""
        return get_field_mappings()["booking_types"][self.value]["prefix"]

    @property
    def field_map(self):
        """Find the field linst from the field mappings JSON file"""
        return get_field_mappings()["booking_types"][self.value]["fields"]

    @classmethod
    def from_label(cls, label: str):
        """Match the passed text string with the ENUM and return that BookingType"""
        for bt in cls:
            if bt.value == label:
                return bt
        raise ValueError(f"No BookingType with label '{label}'")


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
