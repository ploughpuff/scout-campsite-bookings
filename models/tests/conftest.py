"""
conftest.py
"""

import json
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def set_test_env():
    """Sets the APP_ENV environment variable to TEST which is used in decorators"""
    os.environ["APP_ENV"] = "test"
    yield
    del os.environ["APP_ENV"]  # Optionally clean up after the test


BASE_DIR = Path(__file__).resolve().parent
FIELD_MAPPING_PATH = Path(BASE_DIR) / "config" / "field_mappings.json"


def pytest_sessionstart(session):  # pylint: disable=unused-argument
    """Write a dummy field_mappings,json file to root so pytest can use it"""
    if not FIELD_MAPPING_PATH.exists():
        FIELD_MAPPING_PATH.parent.mkdir(parents=True, exist_ok=True)
        dummy_data = {
            "booking_types": {
                "district_day_visit": {
                    "prefix": "ABC",
                    "fields": {
                        "leader": {
                            "name": "name_of_lead_person",
                            "email": "email_address",
                            "phone": "mobile_number_for_lead_person",
                        },
                        "site": {
                            "group_name": "somewhere_scout_group",
                            "group_size": "number_of_people",
                        },
                    },
                },
                "school": {
                    "prefix": "SCH",
                    "fields": {
                        "leader": {
                            "name": "name_of_teacher",
                            "email": "email_address",
                            "phone": "mobile_number_for_teacher",
                        },
                        "site": {
                            "group_name": "school_name",
                            "group_size": "number_of_people",
                        },
                    },
                },
            },
            "sheets": [
                {
                    "name": "Fake Test Data",
                    "use": True,
                    "id": "0123456789ABCDEF0123456789ABCDEF0123456789AB",
                    "range": "2025!A:E",
                    "booking_type": "district_day_visit",
                },
            ],
        }

        FIELD_MAPPING_PATH.write_text(json.dumps(dummy_data), encoding="utf-8")
