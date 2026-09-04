"""test_health.py - the endpoint the container healthcheck depends on."""

import os

# Set before importing app: app.py decides at import time whether to start the
# background scheduler, and a test run must not reach out to Google Sheets.
# conftest's autouse fixture is per-test, which is too late for that.
os.environ["APP_ENV"] = "test"

import app as flask_app  # pylint: disable=wrong-import-position


def test_health_reports_the_build():
    """/health answers 200 with the build identity the compose healthcheck needs."""
    with flask_app.app.test_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert set(body) == {"ok", "version", "commit", "built"}
