"""
test_xero.py
"""

# pylint: disable=all
import json
import logging
from datetime import timedelta

import pytest

import models.bookings as bookings_module
import models.xero as xero
from models.bookings import Bookings
from models.json_utils import load_json
from models.schemas import (
    ArchiveData,
    BookingData,
    LeaderData,
    LiveBooking,
    LiveData,
    TrackingData,
)
from models.utils import now_uk
from models.xero import XeroError, XeroNotConnectedError, XeroTokenManager


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)
        self.content = self.text.encode()
        self.ok = status_code < 400

    def json(self):
        return self._payload


@pytest.fixture
def live_booking():
    return LiveBooking(
        booking=BookingData(
            id="CDS-2026-0001",
            original_sheet_md5="abc123",
            group_type="Chelmsford District Scouts",
            group_name="3rd Chelmsford",
            group_size=24,
            event_type="overnight",
            submitted="2026-05-02T09:30:00",
            arriving="2026-06-12T16:00:00",
            departing="2026-06-14T10:00:00",
            facilities=["Main Field"],
        ),
        leader=LeaderData(name="Jane Smith", email="jane@example.com", phone="0777123456"),
        tracking=TrackingData(status="Invoice", cost_estimate=12345, notes=""),
    )


@pytest.fixture
def manager(live_booking, monkeypatch):
    """A Bookings instance with one Invoice-status booking and no disk/Flask side effects"""
    m = Bookings.__new__(Bookings)
    m.logger = logging.getLogger("test")
    m.live = LiveData(items=[live_booking])
    m.archive = ArchiveData(items=[])

    monkeypatch.setattr(bookings_module, "flash", lambda *a, **k: None)
    monkeypatch.setattr(bookings_module, "save_json", lambda *a, **k: None)
    monkeypatch.setattr(bookings_module, "update_calendar_entry", lambda *a, **k: None)
    monkeypatch.setattr(bookings_module, "is_xero_enabled", lambda: True)
    return m


#
## Token manager
def _write_token(path, expires_delta_secs=-10):
    path.write_text(
        json.dumps(
            {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": (now_uk() + timedelta(seconds=expires_delta_secs)).isoformat(),
                "tenant_id": "tenant-1",
                "tenant_name": "Demo Company (UK)",
            }
        ),
        encoding="utf-8",
    )


def test_refresh_persists_rotated_token_before_returning(tmp_path, monkeypatch):
    token_path = tmp_path / "xero_token.json"
    _write_token(token_path, expires_delta_secs=-10)  # expired

    def fake_post(url, auth=None, data=None, timeout=None):
        assert data["refresh_token"] == "old-refresh"
        return FakeResponse(
            200,
            {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 1800},
        )

    monkeypatch.setattr(xero.requests, "post", fake_post)

    tm = XeroTokenManager(token_path)
    assert tm.get_access_token() == "new-access"

    on_disk = json.loads(token_path.read_text())
    assert on_disk["refresh_token"] == "new-refresh"
    assert on_disk["tenant_id"] == "tenant-1"
    assert on_disk["last_refreshed"]


def test_valid_token_not_refreshed(tmp_path, monkeypatch):
    token_path = tmp_path / "xero_token.json"
    _write_token(token_path, expires_delta_secs=600)  # still valid

    def fail_post(*a, **k):
        raise AssertionError("should not refresh a valid token")

    monkeypatch.setattr(xero.requests, "post", fail_post)
    assert XeroTokenManager(token_path).get_access_token() == "old-access"


def test_invalid_grant_raises_not_connected(tmp_path, monkeypatch):
    token_path = tmp_path / "xero_token.json"
    _write_token(token_path)

    monkeypatch.setattr(
        xero.requests,
        "post",
        lambda *a, **k: FakeResponse(400, {"error": "invalid_grant"}),
    )
    with pytest.raises(XeroNotConnectedError):
        XeroTokenManager(token_path).get_access_token()


def test_missing_token_raises_not_connected(tmp_path):
    with pytest.raises(XeroNotConnectedError):
        XeroTokenManager(tmp_path / "nope.json").get_access_token()


#
## Schema migration
def test_v2_bookings_file_migrates_to_v3(tmp_path):
    path = tmp_path / "bookings.json"
    path.write_text(
        json.dumps(
            {"schema_version": 2, "updated": now_uk().isoformat(), "next_idx": 5, "items": []}
        ),
        encoding="utf-8",
    )
    data = load_json(path, LiveData)
    assert data.schema_version == 3
    assert data.next_idx == 5


def test_v2_archive_file_migrates_to_v3(tmp_path):
    path = tmp_path / "archive.json"
    path.write_text(
        json.dumps({"schema_version": 2, "updated": now_uk().isoformat(), "items": []}),
        encoding="utf-8",
    )
    assert load_json(path, ArchiveData).schema_version == 3


def test_unknown_schema_version_still_raises(tmp_path):
    path = tmp_path / "bookings.json"
    path.write_text(json.dumps({"schema_version": 99, "items": []}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        load_json(path, LiveData)


#
## Contact mapping and contact API
def test_contact_mapping_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(xero, "XERO_CONTACT_MAP_PATH", tmp_path / "xero_contacts.json")
    assert xero.get_mapped_contact("3rd Chelmsford") is None

    xero.save_contact_mapping("3rd Chelmsford", "cid-1", "3rd Chelmsford Scout Group")
    assert xero.get_mapped_contact("  3RD CHELMSFORD ") == "cid-1"  # normalised key
    assert xero.count_contact_mappings() == 1


def test_ensure_leader_already_present_makes_no_update(monkeypatch, live_booking):
    calls = []

    def fake_request(method, path, params=None, json_body=None):
        calls.append((method, path))
        return {
            "Contacts": [
                {"ContactID": "cid-1", "EmailAddress": "JANE@example.com", "ContactPersons": []}
            ]
        }

    monkeypatch.setattr(xero, "_request", fake_request)
    note = xero.ensure_leader_on_contact("cid-1", live_booking.leader)
    assert "already" in note
    assert calls == [("GET", "Contacts/cid-1")]


def test_ensure_leader_added_resends_existing_persons(monkeypatch, live_booking):
    posted = {}
    existing_person = {
        "FirstName": "Old",
        "LastName": "Person",
        "EmailAddress": "old@example.com",
        "IncludeInEmails": False,
    }

    def fake_request(method, path, params=None, json_body=None):
        if method == "GET":
            return {
                "Contacts": [
                    {
                        "ContactID": "cid-1",
                        "EmailAddress": "office@example.com",
                        "ContactPersons": [existing_person],
                    }
                ]
            }
        posted.update(json_body)
        return {}

    monkeypatch.setattr(xero, "_request", fake_request)
    note = xero.ensure_leader_on_contact("cid-1", live_booking.leader)
    assert "added" in note

    persons = posted["Contacts"][0]["ContactPersons"]
    assert existing_person in persons  # replace-on-write: existing list re-sent
    new_person = persons[-1]
    assert new_person["EmailAddress"] == "jane@example.com"
    assert new_person["IncludeInEmails"] is True


def test_ensure_leader_skipped_at_person_limit(monkeypatch, live_booking):
    def fake_request(method, path, params=None, json_body=None):
        assert method == "GET", "must not POST when at the person limit"
        return {
            "Contacts": [
                {
                    "ContactID": "cid-1",
                    "EmailAddress": "office@example.com",
                    "ContactPersons": [
                        {"EmailAddress": f"p{i}@example.com"} for i in range(5)
                    ],
                }
            ]
        }

    monkeypatch.setattr(xero, "_request", fake_request)
    assert "NOT added" in xero.ensure_leader_on_contact("cid-1", live_booking.leader)


#
## Branding theme
def test_branding_theme_resolved_and_cached(monkeypatch):
    monkeypatch.setattr(xero, "XERO_BRANDING_THEME", "Riffhams")
    monkeypatch.setattr(xero, "_branding_cache", {})
    calls = []

    def fake_request(method, path, params=None, json_body=None):
        calls.append(path)
        return {
            "BrandingThemes": [
                {"Name": "Standard", "BrandingThemeID": "std-1"},
                {"Name": "Riffhams", "BrandingThemeID": "riff-1"},
            ]
        }

    monkeypatch.setattr(xero, "_request", fake_request)
    assert xero.get_branding_theme_id() == "riff-1"
    assert xero.get_branding_theme_id() == "riff-1"
    assert calls == ["BrandingThemes"]  # second call served from cache


def test_branding_theme_not_found_raises(monkeypatch):
    monkeypatch.setattr(xero, "XERO_BRANDING_THEME", "Riffhams")
    monkeypatch.setattr(xero, "_branding_cache", {})
    monkeypatch.setattr(xero, "_request", lambda *a, **k: {"BrandingThemes": []})
    with pytest.raises(XeroError):
        xero.get_branding_theme_id()


def test_branding_theme_unset_is_none(monkeypatch):
    monkeypatch.setattr(xero, "XERO_BRANDING_THEME", "")
    assert xero.get_branding_theme_id() is None


def test_create_contact_and_invoice_carry_branding(monkeypatch, live_booking):
    monkeypatch.setattr(xero, "XERO_BRANDING_THEME", "Riffhams")
    monkeypatch.setattr(xero, "_branding_cache", {"Riffhams": "riff-1"})
    sent = []

    def fake_request(method, path, params=None, json_body=None):
        sent.append((method, path, json_body))
        if path == "Contacts":
            return {"Contacts": [{"ContactID": "cid-1", "Name": "3rd Chelmsford"}]}
        return {"Invoices": [{"InvoiceID": "inv-guid", "InvoiceNumber": "INV-0050"}]}

    monkeypatch.setattr(xero, "_request", fake_request)

    xero.create_contact("3rd Chelmsford", live_booking.leader)
    contact = sent[-1][2]["Contacts"][0]
    assert contact["BrandingTheme"] == {"BrandingThemeID": "riff-1"}

    xero.create_invoice(live_booking, "cid-1")
    invoice = sent[-1][2]["Invoices"][0]
    assert invoice["BrandingThemeID"] == "riff-1"


#
## Invoice payload
def test_create_invoice_payload(monkeypatch, live_booking):
    sent = {}

    def fake_request(method, path, params=None, json_body=None):
        sent["method"] = method
        sent["path"] = path
        sent.update(json_body)
        return {"Invoices": [{"InvoiceID": "inv-guid", "InvoiceNumber": "INV-0042"}]}

    monkeypatch.setattr(xero, "_request", fake_request)
    result = xero.create_invoice(live_booking, "cid-1")

    assert result == {"invoice_id": "inv-guid", "invoice_number": "INV-0042"}
    assert (sent["method"], sent["path"]) == ("PUT", "Invoices")

    inv = sent["Invoices"][0]
    assert inv["Type"] == "ACCREC"
    assert inv["Status"] == "AUTHORISED"
    assert inv["Reference"] == "CDS-2026-0001"
    assert inv["Contact"] == {"ContactID": "cid-1"}
    line = inv["LineItems"][0]
    assert line["UnitAmount"] == 123.45  # pence -> pounds
    assert "3rd Chelmsford" in line["Description"]


def test_line_items_split_per_night_when_config_matches(monkeypatch, live_booking):
    # 2 nights (12th-14th) x 24 people x 150p = 7200p
    monkeypatch.setattr(
        xero,
        "FIELD_MAPPINGS_DICT",
        {"charges": {"overnight": {"unit": "per_person", "rates": {"Chelmsford District Scouts": 150}}}},
    )
    live_booking.tracking.cost_estimate = 7200

    lines = xero.build_invoice_line_items(live_booking)
    assert len(lines) == 2
    assert all(l["Quantity"] == 24.0 and l["UnitAmount"] == 1.50 for l in lines)
    assert lines[0]["Description"] == "Camping overnight - 12th June 2026"
    assert lines[1]["Description"] == "Camping overnight - 13th June 2026"


def test_line_items_include_flat_facility_surcharge(monkeypatch, live_booking):
    monkeypatch.setattr(
        xero,
        "FIELD_MAPPINGS_DICT",
        {
            "charges": {
                "overnight": {"unit": "per_person", "rates": {"Chelmsford District Scouts": 150}},
                "roxby_hut": {"unit": "per_group", "rates": {"Chelmsford District Scouts": 500}},
            },
            "facility_charges": {"Main Field": "roxby_hut"},
        },
    )
    live_booking.tracking.cost_estimate = 7700  # 7200 + 500 flat

    lines = xero.build_invoice_line_items(live_booking)
    assert len(lines) == 3
    assert lines[2] == {
        "Description": "Main Field",
        "Quantity": 1.0,
        "UnitAmount": 5.00,
        "AccountCode": xero.XERO_ACCOUNT_CODE,
        "TaxType": xero.XERO_TAX_TYPE,
    }


def test_line_items_fall_back_to_single_line_on_manual_override(monkeypatch, live_booking):
    monkeypatch.setattr(
        xero,
        "FIELD_MAPPINGS_DICT",
        {"charges": {"overnight": {"unit": "per_person", "rates": {"Chelmsford District Scouts": 150}}}},
    )
    live_booking.tracking.cost_estimate = 5000  # manual override, != 7200 itemised

    lines = xero.build_invoice_line_items(live_booking)
    assert len(lines) == 1
    assert lines[0]["Quantity"] == 1.0
    assert lines[0]["UnitAmount"] == 50.00


def test_create_invoice_refuses_zero_amount(live_booking):
    live_booking.tracking.cost_estimate = 0
    with pytest.raises(XeroError):
        xero.create_invoice(live_booking, "cid-1")


#
## Orchestration: Bookings.raise_xero_invoice
def test_already_invoiced_completes_without_new_invoice(manager, live_booking, monkeypatch):
    live_booking.booking.xero_invoice_id = "inv-guid"
    live_booking.booking.xero_invoice_number = "INV-0042"

    def fail(*a, **k):
        raise AssertionError("no Xero call expected")

    monkeypatch.setattr(xero, "create_invoice", fail)

    result = manager.raise_xero_invoice("CDS-2026-0001")
    assert result["ok"] is True
    assert live_booking.tracking.status == "Completed"


def test_happy_path_raises_emails_and_completes(manager, live_booking, monkeypatch):
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: "cid-1")
    monkeypatch.setattr(xero, "ensure_leader_on_contact", lambda c, l: "Leader already on contact")
    monkeypatch.setattr(xero, "find_invoice_by_reference", lambda b: None)
    monkeypatch.setattr(
        xero,
        "create_invoice",
        lambda rec, cid: {"invoice_id": "inv-guid", "invoice_number": "INV-0042"},
    )
    emailed = []
    monkeypatch.setattr(xero, "email_invoice", lambda iid: emailed.append(iid))

    result = manager.raise_xero_invoice("CDS-2026-0001")

    assert result["ok"] is True
    assert live_booking.booking.xero_invoice_id == "inv-guid"
    assert live_booking.booking.xero_invoice_number == "INV-0042"
    assert live_booking.tracking.status == "Completed"
    assert emailed == ["inv-guid"]
    assert "INV-0042" in live_booking.tracking.notes


def test_xero_error_leaves_booking_untouched(manager, live_booking, monkeypatch):
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: "cid-1")

    def boom(*a, **k):
        raise XeroError("Xero is down")

    monkeypatch.setattr(xero, "ensure_leader_on_contact", boom)

    result = manager.raise_xero_invoice("CDS-2026-0001")
    assert result["ok"] is False
    assert live_booking.tracking.status == "Invoice"
    assert live_booking.booking.xero_invoice_id is None


def test_email_failure_still_completes(manager, live_booking, monkeypatch):
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: "cid-1")
    monkeypatch.setattr(xero, "ensure_leader_on_contact", lambda c, l: "note")
    monkeypatch.setattr(xero, "find_invoice_by_reference", lambda b: None)
    monkeypatch.setattr(
        xero,
        "create_invoice",
        lambda rec, cid: {"invoice_id": "inv-guid", "invoice_number": "INV-0042"},
    )

    def boom(iid):
        raise XeroError("email failed")

    monkeypatch.setattr(xero, "email_invoice", boom)

    result = manager.raise_xero_invoice("CDS-2026-0001")
    assert result["ok"] is True
    assert live_booking.booking.xero_invoice_number == "INV-0042"
    assert live_booking.tracking.status == "Completed"
    assert "FAILED" in live_booking.tracking.notes


def test_unmatched_contact_asks_user(manager, live_booking, monkeypatch):
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: None)
    monkeypatch.setattr(xero, "find_contact_by_name", lambda g: None)
    monkeypatch.setattr(
        xero,
        "search_contacts",
        lambda g: [{"contact_id": "cid-9", "name": "3rd Chelmsford SG", "email": ""}],
    )

    result = manager.raise_xero_invoice("CDS-2026-0001")
    assert result["ok"] is False
    assert result["needs_contact"] is True
    assert result["candidates"][0]["contact_id"] == "cid-9"
    assert live_booking.tracking.status == "Invoice"


def test_dry_run_changes_nothing(manager, live_booking, monkeypatch):
    monkeypatch.setattr(bookings_module, "is_xero_enabled", lambda: False)

    def fail(*a, **k):
        raise AssertionError("no Xero call expected when disabled")

    monkeypatch.setattr(xero, "get_mapped_contact", fail)

    result = manager.raise_xero_invoice("CDS-2026-0001")
    assert result["ok"] is False
    assert live_booking.tracking.status == "Invoice"
    assert live_booking.booking.xero_invoice_id is None


def test_wrong_status_rejected(manager, live_booking):
    live_booking.tracking.status = "Confirmed"
    assert manager.raise_xero_invoice("CDS-2026-0001")["ok"] is False
    assert live_booking.tracking.status == "Confirmed"
