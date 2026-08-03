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
import models.utils as utils_module
from models.schemas import (
    SCHEMA_VERSION,
    ArchiveData,
    BookingData,
    LeaderData,
    LiveBooking,
    LiveData,
    TrackingData,
)
from models.utils import estimate_cost, now_uk
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
    monkeypatch.setattr(bookings_module, "is_email_enabled", lambda: True)
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
def test_v2_bookings_file_migrates_to_current(tmp_path):
    path = tmp_path / "bookings.json"
    path.write_text(
        json.dumps(
            {"schema_version": 2, "updated": now_uk().isoformat(), "next_idx": 5, "items": []}
        ),
        encoding="utf-8",
    )
    data = load_json(path, LiveData)
    assert data.schema_version == SCHEMA_VERSION
    assert data.next_idx == 5


def test_v2_archive_file_migrates_to_current(tmp_path):
    path = tmp_path / "archive.json"
    path.write_text(
        json.dumps({"schema_version": 2, "updated": now_uk().isoformat(), "items": []}),
        encoding="utf-8",
    )
    assert load_json(path, ArchiveData).schema_version == SCHEMA_VERSION


def test_v3_bookings_file_migrates_to_current(tmp_path):
    path = tmp_path / "bookings.json"
    path.write_text(
        json.dumps(
            {"schema_version": 3, "updated": now_uk().isoformat(), "next_idx": 7, "items": []}
        ),
        encoding="utf-8",
    )
    data = load_json(path, LiveData)
    assert data.schema_version == SCHEMA_VERSION
    assert data.next_idx == 7


def test_v4_archive_file_migrates_to_current(tmp_path):
    """A v4 archive predates tombstones, so it must come back with an empty list
    rather than failing to validate."""
    path = tmp_path / "archive.json"
    path.write_text(
        json.dumps({"schema_version": 4, "updated": now_uk().isoformat(), "items": []}),
        encoding="utf-8",
    )
    data = load_json(path, ArchiveData)
    assert data.schema_version == SCHEMA_VERSION
    assert data.deleted_md5s == []


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

    assert result["invoice_id"] == "inv-guid"
    assert result["invoice_number"] == "INV-0042"
    assert result["due_date"]  # ISO date, today + XERO_INVOICE_DUE_DAYS
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
    monkeypatch.setattr(xero, "find_invoice_by_reference", lambda b: None)
    monkeypatch.setattr(
        xero,
        "create_invoice",
        lambda rec, cid: {
            "invoice_id": "inv-guid",
            "invoice_number": "INV-0042",
            "due_date": "2026-07-20",
        },
    )
    monkeypatch.setattr(xero, "get_invoice_pdf", lambda iid: b"%PDF-fake")
    monkeypatch.setattr(xero, "get_online_invoice_url", lambda iid: "https://in.xero.com/abc")
    emailed = []

    def fake_send(rec, number, online_url=None, pdf_bytes=None, due_date_iso=None):
        emailed.append((number, online_url, pdf_bytes, due_date_iso))
        return True

    monkeypatch.setattr(bookings_module, "send_invoice_email", fake_send)

    result = manager.raise_xero_invoice("CDS-2026-0001")

    assert result["ok"] is True
    assert live_booking.booking.xero_invoice_id == "inv-guid"
    assert live_booking.booking.xero_invoice_number == "INV-0042"
    assert live_booking.tracking.status == "Completed"
    assert emailed == [("INV-0042", "https://in.xero.com/abc", b"%PDF-fake", "2026-07-20")]
    assert "emailed to leader: jane@example.com" in live_booking.tracking.notes


def test_xero_error_leaves_booking_untouched(manager, live_booking, monkeypatch):
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: "cid-1")

    def boom(*a, **k):
        raise XeroError("Xero is down")

    monkeypatch.setattr(xero, "find_invoice_by_reference", boom)

    result = manager.raise_xero_invoice("CDS-2026-0001")
    assert result["ok"] is False
    assert live_booking.tracking.status == "Invoice"
    assert live_booking.booking.xero_invoice_id is None


def test_email_failure_still_completes(manager, live_booking, monkeypatch):
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: "cid-1")
    monkeypatch.setattr(xero, "find_invoice_by_reference", lambda b: None)
    monkeypatch.setattr(
        xero,
        "create_invoice",
        lambda rec, cid: {"invoice_id": "inv-guid", "invoice_number": "INV-0042", "due_date": ""},
    )
    monkeypatch.setattr(xero, "get_invoice_pdf", lambda iid: b"%PDF-fake")
    monkeypatch.setattr(xero, "get_online_invoice_url", lambda iid: None)
    monkeypatch.setattr(bookings_module, "send_invoice_email", lambda *a, **k: False)

    result = manager.raise_xero_invoice("CDS-2026-0001")
    assert result["ok"] is True
    assert live_booking.booking.xero_invoice_number == "INV-0042"
    assert live_booking.tracking.status == "Completed"
    assert "FAILED" in live_booking.tracking.notes


def test_email_skipped_when_email_disabled(manager, live_booking, monkeypatch):
    monkeypatch.setattr(bookings_module, "is_email_enabled", lambda: False)
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: "cid-1")
    monkeypatch.setattr(xero, "find_invoice_by_reference", lambda b: None)
    monkeypatch.setattr(
        xero,
        "create_invoice",
        lambda rec, cid: {"invoice_id": "inv-guid", "invoice_number": "INV-0042", "due_date": ""},
    )

    def fail(*a, **k):
        raise AssertionError("no email or PDF fetch expected when email disabled")

    monkeypatch.setattr(xero, "get_invoice_pdf", fail)
    monkeypatch.setattr(bookings_module, "send_invoice_email", fail)

    result = manager.raise_xero_invoice("CDS-2026-0001")
    assert result["ok"] is True
    assert live_booking.tracking.status == "Completed"
    assert "SKIPPED" in live_booking.tracking.notes


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


def test_exact_match_still_asks_first_time(manager, live_booking, monkeypatch):
    """An exact name match must not bind silently - it heads the candidate list"""
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: None)
    monkeypatch.setattr(
        xero,
        "find_contact_by_name",
        lambda g: {"ContactID": "cid-exact", "Name": "3rd Chelmsford", "EmailAddress": "x@y.z"},
    )
    monkeypatch.setattr(
        xero,
        "search_contacts",
        lambda g: [
            {"contact_id": "cid-exact", "name": "3rd Chelmsford", "email": "x@y.z"},
            {"contact_id": "cid-9", "name": "3rd Chelmsford SG", "email": ""},
        ],
    )

    result = manager.raise_xero_invoice("CDS-2026-0001")
    assert result["needs_contact"] is True
    ids = [c["contact_id"] for c in result["candidates"]]
    assert ids == ["cid-exact", "cid-9"]  # exact first, deduped
    assert live_booking.tracking.status == "Invoice"
    assert live_booking.booking.xero_invoice_id is None


def test_dry_run_changes_nothing(manager, live_booking, monkeypatch):
    monkeypatch.setattr(bookings_module, "is_xero_enabled", lambda: False)

    def fail(*a, **k):
        raise AssertionError("no Xero call expected when disabled")

    monkeypatch.setattr(xero, "get_mapped_contact", fail)

    result = manager.raise_xero_invoice("CDS-2026-0001")
    assert result["ok"] is False
    assert live_booking.tracking.status == "Invoice"
    assert live_booking.booking.xero_invoice_id is None


#
## Bookings.link_xero_contact (link ahead of invoicing, Xero name wins)
def test_link_renames_group_to_xero_contact_name(manager, live_booking, monkeypatch):
    live_booking.tracking.status = "Confirmed"
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: None)
    saved = {}
    monkeypatch.setattr(
        xero, "save_contact_mapping", lambda g, cid, name: saved.update({g: (cid, name)})
    )

    result = manager.link_xero_contact(
        "CDS-2026-0001", contact_id="cid-1", contact_name="3rd Chelmsford Scout Group"
    )

    assert result["ok"] is True
    assert live_booking.booking.group_name == "3rd Chelmsford Scout Group"  # renamed
    assert "group_name changed from [3rd Chelmsford]" in live_booking.tracking.notes
    assert saved == {"3rd Chelmsford Scout Group": ("cid-1", "3rd Chelmsford Scout Group")}
    assert live_booking.tracking.status == "Confirmed"  # status untouched
    assert live_booking.booking.xero_invoice_id is None  # no invoice raised


def test_link_same_name_does_not_rename(manager, live_booking, monkeypatch):
    live_booking.tracking.status = "New"
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: None)
    monkeypatch.setattr(xero, "save_contact_mapping", lambda *a: None)

    result = manager.link_xero_contact(
        "CDS-2026-0001", contact_id="cid-1", contact_name="3rd Chelmsford"
    )

    assert result["ok"] is True
    assert live_booking.booking.group_name == "3rd Chelmsford"
    assert "group_name changed" not in live_booking.tracking.notes


def test_link_create_contact_path(manager, live_booking, monkeypatch):
    live_booking.tracking.status = "Confirmed"
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: None)
    monkeypatch.setattr(
        xero,
        "create_contact",
        lambda g, leader: {"ContactID": "cid-new", "Name": "3rd Chelmsford"},
    )
    saved = {}
    monkeypatch.setattr(
        xero, "save_contact_mapping", lambda g, cid, name: saved.update({g: cid})
    )

    result = manager.link_xero_contact("CDS-2026-0001", create_contact=True)
    assert result["ok"] is True
    assert saved == {"3rd Chelmsford": "cid-new"}
    assert "Xero contact created" in live_booking.tracking.notes


def test_link_without_choice_asks_user(manager, live_booking, monkeypatch):
    live_booking.tracking.status = "Confirmed"
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: None)
    monkeypatch.setattr(xero, "find_contact_by_name", lambda g: None)
    monkeypatch.setattr(
        xero, "search_contacts", lambda g: [{"contact_id": "c", "name": "x", "email": ""}]
    )

    result = manager.link_xero_contact("CDS-2026-0001")
    assert result["needs_contact"] is True
    assert result["candidates"]
    assert live_booking.booking.group_name == "3rd Chelmsford"  # unchanged


def test_link_rejected_for_completed_booking(manager, live_booking):
    live_booking.tracking.status = "Completed"
    assert manager.link_xero_contact("CDS-2026-0001", contact_id="cid-1")["ok"] is False


def test_link_noop_when_already_mapped(manager, live_booking, monkeypatch):
    live_booking.tracking.status = "Confirmed"
    monkeypatch.setattr(xero, "get_mapped_contact", lambda g: "cid-existing")

    def fail(*a, **k):
        raise AssertionError("no Xero calls expected for an already-linked group")

    monkeypatch.setattr(xero, "create_contact", fail)
    result = manager.link_xero_contact("CDS-2026-0001", create_contact=True)
    assert result["ok"] is True
    assert "Xero contact created" not in live_booking.tracking.notes


def test_wrong_status_rejected(manager, live_booking):
    live_booking.tracking.status = "Confirmed"
    assert manager.raise_xero_invoice("CDS-2026-0001")["ok"] is False
    assert live_booking.tracking.status == "Confirmed"


#
## Per-night group sizes
CHARGES = {
    "charges": {"overnight": {"unit": "per_person", "rates": {"Chelmsford District Scouts": 150}}}
}
NIGHTLY = {"2026-06-12": 20, "2026-06-13": 15}  # fixture stay is 12th-14th June


def test_estimate_cost_with_nightly_sizes(monkeypatch):
    monkeypatch.setattr(utils_module, "FIELD_MAPPINGS_DICT", CHARGES)
    cost = estimate_cost(
        "overnight", 2, "Chelmsford District Scouts", 20, [], nightly_sizes=[20, 15]
    )
    assert cost == 150 * (20 + 15)


def test_estimate_cost_uniform_unchanged_without_nightly_sizes(monkeypatch):
    monkeypatch.setattr(utils_module, "FIELD_MAPPINGS_DICT", CHARGES)
    assert estimate_cost("overnight", 2, "Chelmsford District Scouts", 24, []) == 150 * 2 * 24


def test_estimate_cost_nightly_sizes_apply_to_per_person_facility(monkeypatch):
    charges = {
        "charges": {
            "overnight": {"unit": "per_person", "rates": {"Chelmsford District Scouts": 150}},
            "hall": {"unit": "per_person", "rates": {"Chelmsford District Scouts": 50}},
        },
        "facility_charges": {"Main Field": "hall"},
    }
    monkeypatch.setattr(utils_module, "FIELD_MAPPINGS_DICT", charges)
    cost = estimate_cost(
        "overnight", 2, "Chelmsford District Scouts", 20, ["Main Field"], nightly_sizes=[20, 15]
    )
    assert cost == (150 + 50) * (20 + 15)


def test_nightly_size_helpers(live_booking):
    b = live_booking.booking
    assert b.num_overnights() == 2
    assert b.nightly_size_list() == [24, 24]  # uniform fallback

    b.nightly_group_sizes = dict(NIGHTLY)
    assert b.nightly_size_list() == [20, 15]


def test_nightly_group_sizes_validation():
    with pytest.raises(Exception):
        BookingData.model_validate(
            {
                "id": "x",
                "original_sheet_md5": "x",
                "group_type": "g",
                "group_name": "g",
                "group_size": 10,
                "nightly_group_sizes": {"2026-06-12": 0},  # < 1 rejected
                "event_type": "overnight",
                "submitted": "2026-05-02T09:30:00",
                "arriving": "2026-06-12T16:00:00",
                "departing": "2026-06-14T10:00:00",
                "facilities": [],
            }
        )


def test_itemised_lines_use_nightly_sizes(monkeypatch, live_booking):
    monkeypatch.setattr(xero, "FIELD_MAPPINGS_DICT", CHARGES)
    live_booking.booking.nightly_group_sizes = dict(NIGHTLY)
    live_booking.tracking.cost_estimate = 150 * (20 + 15)

    lines = xero.build_invoice_line_items(live_booking)
    assert len(lines) == 2
    assert (lines[0]["Quantity"], lines[1]["Quantity"]) == (20.0, 15.0)
    assert all(l["UnitAmount"] == 1.50 for l in lines)


def test_build_invoice_description_shows_per_night_counts(live_booking):
    live_booking.booking.nightly_group_sizes = dict(NIGHTLY)
    assert "20/15 people (per night)" in xero.build_invoice_description(live_booking.booking)


#
## Invoice update (amend in place)
def _authorised_invoice(paid=0, credited=0, status="AUTHORISED"):
    return {
        "InvoiceID": "inv-guid",
        "InvoiceNumber": "INV-0042",
        "Status": status,
        "AmountPaid": paid,
        "AmountCredited": credited,
        "DueDateString": "2026-07-20T00:00:00",
    }


def test_update_invoice_replaces_line_items(monkeypatch, live_booking):
    sent = []

    def fake_request(method, path, params=None, json_body=None):
        sent.append((method, path, json_body))
        if method == "GET":
            return {"Invoices": [_authorised_invoice()]}
        return {"Invoices": [_authorised_invoice()]}

    monkeypatch.setattr(xero, "_request", fake_request)
    result = xero.update_invoice(live_booking, "inv-guid")

    assert result == {
        "invoice_id": "inv-guid",
        "invoice_number": "INV-0042",
        "due_date": "2026-07-20",
    }
    method, path, body = sent[-1]
    assert (method, path) == ("POST", "Invoices/inv-guid")
    posted = body["Invoices"][0]
    assert posted["InvoiceID"] == "inv-guid"
    assert posted["LineItems"]  # full replacement line items
    assert "Status" not in posted  # only line items change


def test_update_invoice_blocks_paid_invoice(monkeypatch, live_booking):
    posts = []

    def fake_request(method, path, params=None, json_body=None):
        if method == "POST":
            posts.append(path)
        return {"Invoices": [_authorised_invoice(paid=25.0)]}

    monkeypatch.setattr(xero, "_request", fake_request)
    with pytest.raises(XeroError, match="credit note"):
        xero.update_invoice(live_booking, "inv-guid")
    assert posts == []


def test_update_invoice_blocks_non_authorised(monkeypatch, live_booking):
    posts = []

    def fake_request(method, path, params=None, json_body=None):
        if method == "POST":
            posts.append(path)
        return {"Invoices": [_authorised_invoice(status="VOIDED")]}

    monkeypatch.setattr(xero, "_request", fake_request)
    with pytest.raises(XeroError, match="VOIDED"):
        xero.update_invoice(live_booking, "inv-guid")
    assert posts == []


def test_update_invoice_refuses_zero_amount(monkeypatch, live_booking):
    def fail(*a, **k):
        raise AssertionError("no Xero call expected for a zero amount")

    monkeypatch.setattr(xero, "_request", fail)
    live_booking.tracking.cost_estimate = 0
    with pytest.raises(XeroError):
        xero.update_invoice(live_booking, "inv-guid")


#
## Orchestration: Bookings.amend_xero_invoice
@pytest.fixture
def invoiced_manager(manager, live_booking, monkeypatch):
    """The manager fixture with its booking already invoiced and Completed"""
    live_booking.tracking.status = "Completed"
    live_booking.booking.xero_invoice_id = "inv-guid"
    live_booking.booking.xero_invoice_number = "INV-0042"
    monkeypatch.setattr(manager, "_estimate_cost", lambda b: 5250)
    return manager


def test_amend_happy_path(invoiced_manager, live_booking, monkeypatch):
    updated = []
    monkeypatch.setattr(
        xero,
        "update_invoice",
        lambda rec, iid: updated.append((rec, iid))
        or {"invoice_id": "inv-guid", "invoice_number": "INV-0042", "due_date": "2026-07-20"},
    )
    monkeypatch.setattr(xero, "get_invoice_pdf", lambda iid: b"%PDF-fake")
    monkeypatch.setattr(xero, "get_online_invoice_url", lambda iid: "https://in.xero.com/abc")
    emailed = []
    monkeypatch.setattr(
        bookings_module, "send_invoice_email", lambda *a, **k: emailed.append(a) or True
    )

    assert invoiced_manager.amend_xero_invoice("CDS-2026-0001", dict(NIGHTLY)) is True

    # Xero saw the candidate with the new sizes, before the record was mutated
    assert updated[0][0].booking.nightly_group_sizes == NIGHTLY
    assert updated[0][1] == "inv-guid"

    assert live_booking.booking.group_size == 20  # peak of 20/15
    assert live_booking.booking.nightly_group_sizes == NIGHTLY
    assert live_booking.tracking.cost_estimate == 5250
    assert live_booking.tracking.status == "Completed"
    assert "amended" in live_booking.tracking.notes
    assert "emailed to leader: jane@example.com" in live_booking.tracking.notes
    assert emailed


def test_amend_uniform_nights_collapse_to_group_size(invoiced_manager, live_booking, monkeypatch):
    monkeypatch.setattr(
        xero,
        "update_invoice",
        lambda rec, iid: {"invoice_id": "inv-guid", "invoice_number": "INV-0042", "due_date": ""},
    )
    monkeypatch.setattr(xero, "get_invoice_pdf", lambda iid: b"%PDF-fake")
    monkeypatch.setattr(xero, "get_online_invoice_url", lambda iid: None)
    monkeypatch.setattr(bookings_module, "send_invoice_email", lambda *a, **k: True)

    assert (
        invoiced_manager.amend_xero_invoice(
            "CDS-2026-0001", {"2026-06-12": 18, "2026-06-13": 18}
        )
        is True
    )
    assert live_booking.booking.group_size == 18
    assert live_booking.booking.nightly_group_sizes is None  # uniform needs no breakdown


def test_amend_xero_error_leaves_booking_untouched(invoiced_manager, live_booking, monkeypatch):
    def boom(*a, **k):
        raise XeroError("Invoice INV-0042 has £25.00 paid/credited against it")

    monkeypatch.setattr(xero, "update_invoice", boom)

    assert invoiced_manager.amend_xero_invoice("CDS-2026-0001", dict(NIGHTLY)) is False
    assert live_booking.booking.group_size == 24
    assert live_booking.booking.nightly_group_sizes is None
    assert live_booking.tracking.cost_estimate == 12345
    assert live_booking.tracking.notes == ""


def test_amend_calendar_failure_still_saves_amendment(invoiced_manager, live_booking, monkeypatch):
    monkeypatch.setattr(
        xero,
        "update_invoice",
        lambda rec, iid: {"invoice_id": "inv-guid", "invoice_number": "INV-0042", "due_date": ""},
    )
    monkeypatch.setattr(xero, "get_invoice_pdf", lambda iid: b"%PDF-fake")
    monkeypatch.setattr(xero, "get_online_invoice_url", lambda iid: None)
    monkeypatch.setattr(bookings_module, "send_invoice_email", lambda *a, **k: True)

    def boom(rec):
        raise RuntimeError("Google Calendar is down")

    monkeypatch.setattr(bookings_module, "update_calendar_entry", boom)
    saved = []
    monkeypatch.setattr(bookings_module, "save_json", lambda *a, **k: saved.append(True))

    # Xero already holds the amendment - a calendar failure must not lose it
    assert invoiced_manager.amend_xero_invoice("CDS-2026-0001", dict(NIGHTLY)) is True
    assert live_booking.tracking.cost_estimate == 5250
    assert saved


def test_amend_email_failure_still_saves_amendment(invoiced_manager, live_booking, monkeypatch):
    monkeypatch.setattr(
        xero,
        "update_invoice",
        lambda rec, iid: {"invoice_id": "inv-guid", "invoice_number": "INV-0042", "due_date": ""},
    )
    monkeypatch.setattr(xero, "get_invoice_pdf", lambda iid: b"%PDF-fake")
    monkeypatch.setattr(xero, "get_online_invoice_url", lambda iid: None)
    monkeypatch.setattr(bookings_module, "send_invoice_email", lambda *a, **k: False)

    assert invoiced_manager.amend_xero_invoice("CDS-2026-0001", dict(NIGHTLY)) is True
    assert live_booking.tracking.cost_estimate == 5250
    assert "FAILED" in live_booking.tracking.notes


def test_amend_rejected_without_invoice_or_wrong_status(invoiced_manager, live_booking):
    live_booking.booking.xero_invoice_id = None
    assert invoiced_manager.amend_xero_invoice("CDS-2026-0001", dict(NIGHTLY)) is False

    live_booking.booking.xero_invoice_id = "inv-guid"
    live_booking.tracking.status = "Invoice"
    assert invoiced_manager.amend_xero_invoice("CDS-2026-0001", dict(NIGHTLY)) is False
    assert live_booking.booking.group_size == 24


def test_amend_no_change_is_a_noop(invoiced_manager, live_booking, monkeypatch):
    def fail(*a, **k):
        raise AssertionError("no Xero call expected when nothing changed")

    monkeypatch.setattr(xero, "update_invoice", fail)
    monkeypatch.setattr(invoiced_manager, "_estimate_cost", lambda b: 12345)

    # Same uniform headcount as the booking already has
    assert (
        invoiced_manager.amend_xero_invoice(
            "CDS-2026-0001", {"2026-06-12": 24, "2026-06-13": 24}
        )
        is False
    )
    assert live_booking.tracking.notes == ""


def test_amend_dry_run_changes_nothing(invoiced_manager, live_booking, monkeypatch):
    monkeypatch.setattr(bookings_module, "is_xero_enabled", lambda: False)

    def fail(*a, **k):
        raise AssertionError("no Xero call expected when disabled")

    monkeypatch.setattr(xero, "update_invoice", fail)

    assert invoiced_manager.amend_xero_invoice("CDS-2026-0001", dict(NIGHTLY)) is False
    assert live_booking.booking.group_size == 24
    assert live_booking.booking.nightly_group_sizes is None
    assert live_booking.tracking.cost_estimate == 12345


#
## modify_fields and per-night sizes
def test_modify_fields_accepts_nightly_sizes(manager, live_booking, monkeypatch):
    live_booking.tracking.status = "New"
    monkeypatch.setattr(manager, "_estimate_cost", lambda b: 5250)

    assert manager.modify_fields(
        "CDS-2026-0001",
        {"booking": {"nightly_group_sizes": dict(NIGHTLY), "group_size": 20}},
    )
    assert live_booking.booking.nightly_group_sizes == NIGHTLY
    assert live_booking.booking.group_size == 20
    assert live_booking.tracking.cost_estimate == 5250
    assert "nightly_group_sizes changed" in live_booking.tracking.notes


def test_date_change_clears_nightly_sizes(manager, live_booking, monkeypatch):
    live_booking.tracking.status = "New"
    live_booking.booking.nightly_group_sizes = dict(NIGHTLY)
    monkeypatch.setattr(manager, "_estimate_cost", lambda b: 5250)

    assert manager.modify_fields(
        "CDS-2026-0001", {"booking": {"departing": "2026-06-15T10:00:00"}}
    )
    assert live_booking.booking.nightly_group_sizes is None
    assert "nightly group sizes cleared - dates changed" in live_booking.tracking.notes
