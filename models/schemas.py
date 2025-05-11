"""schemas.py"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from config import UK_TZ
from models.utils import (
    now_uk,
)


class LeaderData(BaseModel):
    """This class encapsulates the leader's personal data."""

    name: str
    email: str
    phone: str


class BookingData(BaseModel):
    """Class encapsulates the booking data which is kept and archived"""

    id: str = Field(frozen=True)
    original_sheet_md5: str = Field(frozen=True)
    group_type: str
    group_name: str
    group_size: int
    submitted: datetime = Field(frozen=True)
    arriving: datetime
    departing: datetime

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
    invoice: bool
    notes: str
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

    updated: datetime = Field(default_factory=now_uk)
    next_idx: int = Field(default=1)
    items: List[LiveBooking] = Field(default_factory=list)


class ArchiveData(BaseModel):
    """Holds expired or archived bookings"""

    updated: datetime = Field(default_factory=now_uk)
    items: List[BookingData] = Field(default_factory=list)
