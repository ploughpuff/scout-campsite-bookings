"""schemas.py"""

from datetime import date, datetime, timedelta
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from config import UK_TZ
from models.utils import (
    now_uk,
)

SCHEMA_VERSION = 4


class LeaderData(BaseModel):
    """This class encapsulates the leader's personal data."""

    name: str
    email: str
    phone: str
    address: Optional[str] = None


class BookingData(BaseModel):
    """Class encapsulates the booking data which is kept and archived"""

    id: str = Field(frozen=True)
    original_sheet_md5: str = Field(frozen=True)
    group_type: str
    group_name: str
    group_size: int
    # Per-night headcounts keyed by ISO date of the night's start; None means
    # group_size applies to every night. group_size stays the peak headcount.
    nightly_group_sizes: Optional[Dict[str, int]] = None
    event_type: str
    submitted: datetime = Field(frozen=True)
    arriving: datetime
    departing: datetime
    facilities: List[str]
    # Xero references are financial audit data, kept here so they survive archiving
    xero_invoice_id: Optional[str] = None
    xero_invoice_number: Optional[str] = None

    @field_validator("nightly_group_sizes")
    @classmethod
    def validate_nightly_group_sizes(cls, value):
        """Empty dict collapses to None; keys must be ISO dates, sizes at least 1."""
        if not value:
            return None
        for key, size in value.items():
            date.fromisoformat(key)  # raises ValueError on a bad key
            if size < 1:
                raise ValueError(f"nightly size for {key} must be at least 1, got {size}")
        return value

    def num_overnights(self) -> int:
        """Nights spent on site; 0 for day/eve bookings."""
        return (self.departing.date() - self.arriving.date()).days

    def size_for_night(self, night: date) -> int:
        """Headcount for the night starting on the given date."""
        return (self.nightly_group_sizes or {}).get(night.isoformat(), self.group_size)

    def nightly_size_list(self) -> List[int]:
        """Per-night headcounts in stay order; empty for day/eve bookings."""
        return [
            self.size_for_night(self.arriving.date() + timedelta(days=i))
            for i in range(self.num_overnights())
        ]

    @field_validator(
        "submitted",
        "arriving",
        "departing",
        mode="before",
    )
    @classmethod
    def ensure_uk_timezone(cls, value):
        """This class method ensured the datetime object has a UK timezone."""
        if isinstance(value, str):
            dt = datetime.fromisoformat(value)
        elif isinstance(value, datetime):
            dt = value
        else:
            return value  # Leave None or unexpected types alone

        return dt.astimezone(UK_TZ) if dt.tzinfo else dt.replace(tzinfo=UK_TZ)


class TrackingData(BaseModel):
    """Class holding tracking and private data which is discarded once archived"""

    status: Literal["New", "Pending", "Confirmed", "Invoice", "Completed", "Archived", "Cancelled"]
    cost_estimate: int
    notes: str
    bookers_comment: Optional[str] = None
    google_calendar_id: Optional[str] = None
    pending_email_sent: Optional[datetime] = None
    confirm_email_sent: Optional[datetime] = None
    cancel_email_sent: Optional[datetime] = None
    pend_question: Optional[str] = None
    cancel_reason: Optional[str] = None

    @field_validator(
        "pending_email_sent",
        "confirm_email_sent",
        "cancel_email_sent",
        mode="before",
    )
    @classmethod
    def ensure_uk_timezone(cls, value):
        """This class method ensured the datetime object has a UK timezone."""
        if isinstance(value, str):
            dt = datetime.fromisoformat(value)
        elif isinstance(value, datetime):
            dt = value
        else:
            return value  # Leave None or unexpected types alone

        return dt.astimezone(UK_TZ) if dt.tzinfo else dt.replace(tzinfo=UK_TZ)


class LiveBooking(BaseModel):
    """A composite class that binds SiteData with LeaderData, keeping them tightly coupled."""

    booking: BookingData
    leader: LeaderData
    tracking: TrackingData

    def is_valid(self) -> bool:
        """Helper function to check if if a data set is valid"""
        try:
            self.__class__.model_validate(self.model_dump())
            return True
        except ValidationError:
            return False

    def get_problematic_data(self):
        """Helper function to cehck if a data set is problematic"""
        try:
            self.__class__.model_validate(self.model_dump())
            return None
        except ValidationError as e:
            return e.errors()


class LiveData(BaseModel):
    """Live data that contains the active bookings"""

    schema_version: int = Field(default=SCHEMA_VERSION)
    updated: datetime = Field(default_factory=now_uk)
    next_idx: int = Field(default=1)
    items: List[LiveBooking] = Field(default_factory=list)


class ArchiveData(BaseModel):
    """Holds expired or archived bookings"""

    schema_version: int = Field(default=SCHEMA_VERSION)
    updated: datetime = Field(default_factory=now_uk)
    items: List[BookingData] = Field(default_factory=list)
