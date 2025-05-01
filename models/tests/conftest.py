"""
conftest.py
"""

import os

import pytest


@pytest.fixture(autouse=True)
def set_test_env():
    """Sets the APP_ENV environment variable to TEST which is used in decorators"""
    os.environ["APP_ENV"] = "test"
    yield
    del os.environ["APP_ENV"]  # Optionally clean up after the test
