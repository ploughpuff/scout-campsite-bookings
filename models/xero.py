"""
xero.py - Handle all Xero accounting API operations (contacts and invoices).

Auth is the standard OAuth2 authorization-code flow: the initial token set is
obtained once by running scripts/xero_bootstrap.py (Xero only allows plain-HTTP
redirect URIs on localhost, so the consent step happens on a PC, not the NAS).
This module then keeps the connection alive by refreshing the token, which
needs no redirect URI.

Xero rotates the refresh token on every refresh and invalidates the old one,
so the new token set is persisted atomically before it is used. A module-level
threading.Lock guards refresh; this is sufficient because gunicorn runs a
single worker. If workers are ever increased, this must become an OS-level
file lock or the connection will break on a lost update.
"""

import json
import logging
import threading
from datetime import date, datetime, timedelta

import requests

from config import (
    XERO_ACCOUNT_CODE,
    XERO_BRANDING_THEME,
    XERO_CLIENT_ID,
    XERO_CLIENT_SECRET,
    XERO_CONTACT_MAP_PATH,
    XERO_INVOICE_DUE_DAYS,
    XERO_TAX_TYPE,
    XERO_TOKEN_PATH,
)
from models import outbox
from models.json_utils import atomic_write_json
from models.net import RETRYABLE_STATUSES, Permanent, Retryable
from models.pricing import ChargeLine, charge_lines
from models.schemas import BookingData, LeaderData, LiveBooking
from models.utils import get_pretty_date_str, now_uk, parse_iso_datetime

logger = logging.getLogger("app_logger")

XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"
XERO_API = "https://api.xero.com/api.xro/2.0"

# Xero being down must not hang gunicorn's single worker indefinitely
REQUEST_TIMEOUT = 20

class XeroError(Exception):
    """Xero API failure with a message safe to flash to the user."""


class XeroUnavailableError(XeroError):
    """Xero never gave a usable answer, so the same call is still worth making.

    The distinction only matters to the outbox handler below, which has to sort
    a failure into Retryable or Permanent. Without it every failure looks alike
    and a DNS blip would block queued work that a minute's wait would fix.
    """


class XeroNotConnectedError(XeroError):
    """No valid token - the one-time bootstrap needs (re-)running."""

    def __init__(self, msg=None):
        super().__init__(
            msg or "Xero connection expired or revoked - re-run scripts/xero_bootstrap.py"
        )


class XeroTokenManager:
    """Loads, refreshes, and persists the rotating Xero OAuth2 token set."""

    def __init__(self, token_path):
        self.token_path = token_path
        self._lock = threading.Lock()

    def _load(self) -> dict | None:
        if not self.token_path.exists():
            return None
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            logger.error("Failed to read Xero token file: %s", e)
            return None

    def has_token(self) -> bool:
        """True if a token file exists and holds a refresh token"""
        token = self._load()
        return bool(token and token.get("refresh_token"))

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing (and persisting) if near expiry"""
        with self._lock:
            token = self._load()
            if not token or not token.get("refresh_token"):
                raise XeroNotConnectedError(
                    "Xero is not connected - run scripts/xero_bootstrap.py first"
                )

            expires_at = parse_iso_datetime(token.get("expires_at") or "")
            if not isinstance(expires_at, datetime) or now_uk() >= expires_at:
                token = self._refresh(token)

            return token["access_token"]

    def _refresh(self, token: dict) -> dict:
        #
        ## Xero rotates the refresh token on use, so the moment this request is
        ## sent the copy on disk may already be spent. If the reply never
        ## arrives - read timeout, TLS reset, container killed - the token is
        ## dead server-side and the replacement is lost, which can only be
        ## fixed by re-running scripts/xero_bootstrap.py on a PC. The window
        ## cannot be closed, but it can be made visible: leave a mark before
        ## sending, clear it on success, and let status() report the doubt
        ## instead of claiming a healthy connection.
        self._mark_refresh_started(token)

        try:
            resp = requests.post(
                XERO_TOKEN_URL,
                auth=(XERO_CLIENT_ID, XERO_CLIENT_SECRET),
                data={"grant_type": "refresh_token", "refresh_token": token["refresh_token"]},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            raise XeroUnavailableError(f"Could not reach Xero to refresh token: {e}") from e

        if resp.status_code != 200:
            if "invalid_grant" in resp.text:
                raise XeroNotConnectedError()
            raise XeroError(f"Xero token refresh failed [{resp.status_code}]: {resp.text}")

        #
        ## A 200 with a body we cannot use is still a failed refresh, and used
        ## to escape as a bare ValueError or KeyError past every XeroError
        ## handler in the app and onto the generic 500 page.
        try:
            fresh = resp.json()
            new_token = {
                "access_token": fresh["access_token"],
                "refresh_token": fresh["refresh_token"],
                # Refresh 30s early so a token never expires mid-request
                "expires_at": (now_uk() + timedelta(seconds=fresh["expires_in"] - 30)).isoformat(),
                "tenant_id": token.get("tenant_id"),
                "tenant_name": token.get("tenant_name"),
                "last_refreshed": now_uk().isoformat(),
                "refresh_started_at": None,
            }
        except (ValueError, KeyError, TypeError) as e:
            raise XeroError(f"Xero token refresh returned an unusable response: {e}") from e

        # The old refresh token is now dead - persist before using
        atomic_write_json(new_token, self.token_path)
        logger.info("Xero token refreshed")
        return new_token

    def _mark_refresh_started(self, token: dict) -> None:
        """Note that a refresh is in flight, so a lost reply leaves a trace."""
        try:
            marked = {**token, "refresh_started_at": now_uk().isoformat()}
            atomic_write_json(marked, self.token_path)
        except OSError as e:
            #
            ## Only the warning is lost, not the refresh. Carry on.
            logger.warning("Could not mark Xero refresh as started: %s", e)

    def get_tenant_id(self) -> str:
        """Tenant ID selected during bootstrap"""
        token = self._load()
        if not token or not token.get("tenant_id"):
            raise XeroNotConnectedError("No Xero tenant ID - re-run scripts/xero_bootstrap.py")
        return token["tenant_id"]

    def status(self) -> dict:
        """Connection summary for the admin page. Reads the file only - no API call."""
        token = self._load() or {}

        #
        ## A refresh that started and never finished means the stored refresh
        ## token was probably spent server-side and its replacement lost. The
        ## file still looks connected, so say plainly that it may not be.
        in_doubt = bool(token.get("refresh_started_at"))

        return {
            "configured": bool(XERO_CLIENT_ID and XERO_CLIENT_SECRET),
            "connected": bool(token.get("refresh_token")) and not in_doubt,
            "refresh_in_doubt": in_doubt,
            "tenant_name": token.get("tenant_name"),
            "expires_at": token.get("expires_at"),
            "last_refreshed": token.get("last_refreshed"),
        }


token_manager = XeroTokenManager(XERO_TOKEN_PATH)


def _request(method: str, path: str, params: dict = None, json_body: dict = None) -> dict:
    """Make an authenticated Xero API call, raising XeroError on any failure"""
    headers = {
        "Authorization": f"Bearer {token_manager.get_access_token()}",
        "Xero-tenant-id": token_manager.get_tenant_id(),
        "Accept": "application/json",
    }
    try:
        resp = requests.request(
            method,
            f"{XERO_API}/{path}",
            headers=headers,
            params=params,
            json=json_body,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise XeroUnavailableError(f"Could not reach Xero: {e}") from e

    if not resp.ok:
        #
        ## Same rule as net.py: a 5xx is Xero having a bad day and a 408/429 is
        ## Xero asking us to wait, both worth repeating. Anything else in 4xx is
        ## a refusal that will refuse again just as fast.
        detail = f"Xero {method} {path} failed [{resp.status_code}]: {_error_detail(resp)}"
        if resp.status_code >= 500 or resp.status_code in RETRYABLE_STATUSES:
            raise XeroUnavailableError(detail)
        raise XeroError(detail)

    return resp.json() if resp.content else {}


def _error_detail(resp) -> str:
    """Pull the most useful message out of a Xero error response"""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300]

    # Validation errors are nested per element
    for element in data.get("Elements", []):
        errors = [ve.get("Message") for ve in element.get("ValidationErrors", [])]
        if errors:
            return "; ".join(errors)

    return data.get("Detail") or data.get("Message") or data.get("Title") or resp.text[:300]


def test_connection() -> str:
    """Live check against Xero - returns the connected organisation name"""
    try:
        resp = requests.get(
            XERO_CONNECTIONS_URL,
            headers={"Authorization": f"Bearer {token_manager.get_access_token()}"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        raise XeroUnavailableError(f"Could not reach Xero: {e}") from e

    if not resp.ok:
        raise XeroError(f"Xero connections check failed [{resp.status_code}]")

    tenant_id = token_manager.get_tenant_id()
    for conn in resp.json():
        if conn.get("tenantId") == tenant_id:
            return conn.get("tenantName", "unknown organisation")

    raise XeroError("Connected to Xero, but the saved tenant is no longer authorised")


#
## Group name -> Xero ContactID mapping, so each group is only matched once
def _load_contact_map() -> dict:
    if not XERO_CONTACT_MAP_PATH.exists():
        return {}
    try:
        return json.loads(XERO_CONTACT_MAP_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        logger.error("Failed to read Xero contact map: %s", e)
        return {}


def _map_key(group_name: str) -> str:
    return group_name.strip().lower()


def get_mapped_contact(group_name: str) -> str | None:
    """Return the remembered Xero ContactID for a group, if any"""
    entry = _load_contact_map().get(_map_key(group_name))
    return entry.get("contact_id") if entry else None


def get_contact_mapping(group_name: str) -> dict | None:
    """Return the full saved mapping entry {contact_id, contact_name} for a group"""
    return _load_contact_map().get(_map_key(group_name))


# Legacy URL form: needs no org shortcode, Xero redirects into the new UI
XERO_CONTACT_URL = "https://go.xero.com/Contacts/View/{contact_id}"


def get_contact_urls(group_names) -> dict[str, str]:
    """Xero contact page URLs keyed by group name, for groups with a saved mapping"""
    contact_map = _load_contact_map()
    urls = {}
    for name in group_names:
        entry = contact_map.get(_map_key(name))
        if entry:
            urls[name] = XERO_CONTACT_URL.format(contact_id=entry["contact_id"])
    return urls


def save_contact_mapping(group_name: str, contact_id: str, contact_name: str):
    """Remember which Xero contact a group maps to"""
    contact_map = _load_contact_map()
    contact_map[_map_key(group_name)] = {"contact_id": contact_id, "contact_name": contact_name}
    atomic_write_json(contact_map, XERO_CONTACT_MAP_PATH)


def count_contact_mappings() -> int:
    """Number of saved group -> Xero contact mappings, for the admin page"""
    return len(_load_contact_map())


#
## Branding theme (name -> ID, resolved once per process)
_branding_cache: dict[str, str] = {}


def get_branding_theme_id() -> str | None:
    """Resolve the configured branding theme name to its Xero ID, or None if unset"""
    name = XERO_BRANDING_THEME.strip()
    if not name:
        return None
    if name in _branding_cache:
        return _branding_cache[name]

    try:
        data = _request("GET", "BrandingThemes")
    except XeroError as e:
        raise XeroError(
            f"{e} (branding theme lookup needs the settings permission - "
            "re-run scripts/xero_bootstrap.py if this is a permissions error)"
        ) from e

    for theme in data.get("BrandingThemes", []):
        if theme.get("Name", "").strip().lower() == name.lower():
            _branding_cache[name] = theme["BrandingThemeID"]
            return theme["BrandingThemeID"]

    raise XeroError(f"Branding theme [{name}] not found in this Xero organisation")


#
## Contacts
def find_contact_by_name(group_name: str) -> dict | None:
    """Exact (case-insensitive) contact lookup by name"""
    escaped = group_name.replace("\\", "\\\\").replace('"', '\\"')
    data = _request("GET", "Contacts", params={"where": f'Name.ToLower()=="{escaped.lower()}"'})
    contacts = data.get("Contacts", [])
    return contacts[0] if contacts else None


def search_contacts(term: str) -> list[dict]:
    """Fuzzy contact search, used to offer near-matches before creating a new contact"""
    # Xero rejects searchTerm over 50 chars with a 400
    term = term[:50].strip()
    data = _request("GET", "Contacts", params={"searchTerm": term, "summaryOnly": "true"})
    return [
        {
            "contact_id": c.get("ContactID"),
            "name": c.get("Name"),
            "email": c.get("EmailAddress", ""),
        }
        for c in data.get("Contacts", [])
    ]


def _split_name(name: str) -> tuple[str, str]:
    parts = name.strip().split(None, 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (name.strip(), "")


def create_contact(group_name: str, leader: LeaderData) -> dict:
    """Create a new Xero contact for a group, with the leader as the primary person"""
    first, last = _split_name(leader.name)
    contact = {
        "Name": group_name.strip(),
        "FirstName": first,
        "LastName": last,
        "EmailAddress": leader.email,
        "Phones": [{"PhoneType": "MOBILE", "PhoneNumber": leader.phone}],
    }
    branding_theme_id = get_branding_theme_id()
    if branding_theme_id:
        # Default sales branding for the contact (e.g. the site's own theme)
        contact["BrandingTheme"] = {"BrandingThemeID": branding_theme_id}

    data = _request("PUT", "Contacts", json_body={"Contacts": [contact]})
    contact = data["Contacts"][0]
    logger.info("Created Xero contact [%s] for group [%s]", contact["ContactID"], group_name)
    return contact


#
## Invoices
def build_invoice_description(b: BookingData) -> str:
    """Human-readable line item description built from the booking"""
    arriving = get_pretty_date_str(b.arriving)
    departing = get_pretty_date_str(b.departing)
    if b.nightly_group_sizes:
        people = "/".join(str(n) for n in b.nightly_size_list()) + " people (per night)"
    else:
        people = f"{b.group_size} people"
    desc = f"{b.group_name} - {b.event_type} booking, {arriving} to {departing}, {people}"
    if b.facilities:
        desc += f". Facilities: {', '.join(b.facilities)}"
    return desc


def _line_date_str(dt: date) -> str:
    """Line item date, e.g. '12th June 2026'"""
    day = dt.day
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix} {dt.strftime('%B %Y')}"


def _line(description: str, quantity: int, unit_pence: int) -> dict:
    return {
        "Description": description,
        "Quantity": float(quantity),
        "UnitAmount": round(unit_pence / 100, 2),
        "AccountCode": XERO_ACCOUNT_CODE,
        "TaxType": XERO_TAX_TYPE,
    }


def _describe(line: ChargeLine) -> str:
    """Line item wording, e.g. 'Roxby Hut - 5th August 2026 (4 nights)'"""
    desc = f"{line.label} - {_line_date_str(line.night)}"
    if line.per_night and line.quantity > 1:
        desc += f" ({line.quantity} nights)"
    return desc


def _itemised_lines(b: BookingData) -> tuple[list[dict], int]:
    """Per-night/per-facility line items priced from the pricing config.

    Returns (lines, total_pence); ([], 0) when the pricing config can't
    itemise this booking.
    """
    lines = charge_lines(b)
    if not lines:
        return [], 0

    return (
        [_line(_describe(line), line.quantity, line.unit_pence) for line in lines],
        sum(line.total_pence for line in lines),
    )


def build_invoice_line_items(rec: LiveBooking) -> list[dict]:
    """Itemised lines when they reproduce the booking's cost estimate exactly.

    A manually overridden cost estimate (or changed rates) won't match the
    itemisation, so fall back to a single line for the stored total - the
    invoice must always agree with what the app shows.
    """
    lines, total = _itemised_lines(rec.booking)
    if lines and total == rec.tracking.cost_estimate:
        return lines

    if lines:
        logger.info(
            "Itemised total [%s] != cost estimate [%s] for %s - using single-line invoice",
            total,
            rec.tracking.cost_estimate,
            rec.booking.id,
        )
    return [_line(build_invoice_description(rec.booking), 1, rec.tracking.cost_estimate)]


def find_invoice_by_reference(booking_id: str) -> dict | None:
    """Look for an existing (non-voided) invoice carrying this booking's reference"""
    data = _request("GET", "Invoices", params={"where": f'Reference=="{booking_id}"'})
    for inv in data.get("Invoices", []):
        if inv.get("Status") not in ("VOIDED", "DELETED"):
            return {
                "invoice_id": inv["InvoiceID"],
                "invoice_number": inv.get("InvoiceNumber", ""),
                "due_date": (inv.get("DueDateString") or "")[:10],
            }
    return None


def get_invoice(invoice_id: str) -> dict:
    """Fetch the raw invoice record (Status, AmountPaid, InvoiceNumber, ...)"""
    data = _request("GET", f"Invoices/{invoice_id}")
    return data["Invoices"][0]


def assert_invoice_amendable(inv: dict) -> None:
    """Raise XeroError unless the invoice is AUTHORISED with no money against it"""
    number = inv.get("InvoiceNumber", "?")
    status = inv.get("Status")
    if status != "AUTHORISED":
        raise XeroError(
            f"Invoice {number} is {status} - only unpaid AUTHORISED invoices can be amended"
        )
    paid = inv.get("AmountPaid") or 0
    credited = inv.get("AmountCredited") or 0
    if paid or credited:
        raise XeroError(
            f"Invoice {number} has £{paid + credited:.2f} paid/credited against it - "
            f"amend it in Xero with a credit note; this app cannot do that"
        )


def update_invoice(rec: LiveBooking, invoice_id: str) -> dict:
    """Replace the line items on an existing unpaid AUTHORISED invoice.

    Xero keeps the invoice number; only the LineItems change. The invoice is
    fetched first so an already-paid or voided invoice can never be touched.
    """
    if rec.tracking.cost_estimate <= 0:
        raise XeroError(f"Refusing to amend invoice to a zero amount for {rec.booking.id}")

    inv = get_invoice(invoice_id)
    assert_invoice_amendable(inv)

    # POST updates in place; omitted fields are retained, LineItems fully replaced
    _request(
        "POST",
        f"Invoices/{invoice_id}",
        json_body={
            "Invoices": [{"InvoiceID": invoice_id, "LineItems": build_invoice_line_items(rec)}]
        },
    )
    number = inv.get("InvoiceNumber", "")
    logger.info("Xero invoice [%s] amended for booking [%s]", number, rec.booking.id)
    return {
        "invoice_id": invoice_id,
        "invoice_number": number,
        "due_date": (inv.get("DueDateString") or "")[:10],
    }


def create_invoice(rec: LiveBooking, contact_id: str) -> dict:
    """Create an AUTHORISED sales invoice in Xero for the booking's cost estimate"""
    if rec.tracking.cost_estimate <= 0:
        raise XeroError(f"Refusing to raise a zero-amount invoice for {rec.booking.id}")

    today = now_uk().date()
    due_date = today + timedelta(days=XERO_INVOICE_DUE_DAYS)
    invoice = {
        "Type": "ACCREC",
        "Status": "AUTHORISED",
        "Contact": {"ContactID": contact_id},
        "Date": today.isoformat(),
        "DueDate": due_date.isoformat(),
        "Reference": rec.booking.id,
        "LineAmountTypes": "NoTax" if XERO_TAX_TYPE == "NONE" else "Exclusive",
        "LineItems": build_invoice_line_items(rec),
    }
    branding_theme_id = get_branding_theme_id()
    if branding_theme_id:
        invoice["BrandingThemeID"] = branding_theme_id

    # PUT is create-only in Xero, so a retry can never mutate an existing invoice
    data = _request("PUT", "Invoices", json_body={"Invoices": [invoice]})
    created = data["Invoices"][0]
    logger.info(
        "Xero invoice [%s] raised for booking [%s]", created.get("InvoiceNumber"), rec.booking.id
    )
    return {
        "invoice_id": created["InvoiceID"],
        "invoice_number": created.get("InvoiceNumber", ""),
        "due_date": due_date.isoformat(),
    }


def get_invoice_pdf(invoice_id: str) -> bytes:
    """Fetch the rendered invoice PDF (uses the invoice's branding theme)"""
    headers = {
        "Authorization": f"Bearer {token_manager.get_access_token()}",
        "Xero-tenant-id": token_manager.get_tenant_id(),
        "Accept": "application/pdf",
    }
    try:
        resp = requests.get(
            f"{XERO_API}/Invoices/{invoice_id}", headers=headers, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as e:
        raise XeroError(f"Could not fetch invoice PDF: {e}") from e

    if not resp.ok:
        raise XeroError(f"Invoice PDF fetch failed [{resp.status_code}]")
    return resp.content


def get_online_invoice_url(invoice_id: str) -> str | None:
    """Xero's shareable view/pay link for the invoice; None if unavailable"""
    try:
        data = _request("GET", f"Invoices/{invoice_id}/OnlineInvoice")
        return data["OnlineInvoices"][0]["OnlineInvoiceUrl"]
    except (XeroError, KeyError, IndexError) as e:
        logger.warning("No online invoice URL for [%s]: %s", invoice_id, e)
        return None


#
## Marking an invoice as sent
##
## The app emails the invoice itself rather than asking Xero to, so Xero has no
## way of knowing it went out and leaves SentToContact false. Its own list then
## reads "Invoice not sent", and - the part that actually costs money - it holds
## back the payment reminders it would otherwise send on an overdue invoice.
def mark_sent_follow_on(invoice_id: str, invoice_number: str) -> list[dict]:
    """The queue entry that tells Xero this invoice went out, once it really has.

    Handed to the mailer rather than done here on purpose. Telling Xero an
    invoice was sent when our email was refused would set its reminders chasing
    a leader over something they never received, so this only ever runs off the
    back of a delivery the far end accepted.
    """
    return [
        {
            "kind": "xero_email",
            "payload": {"invoice_id": invoice_id, "invoice_number": invoice_number},
        }
    ]


@outbox.handler("xero_email")
def _handle_mark_sent(item) -> None:
    """Flag an invoice as sent in Xero, so it stops reading "Invoice not sent".

    Idempotent for free: setting the flag true a second time is the same call,
    which is what a retry after an answer we never read needs it to be.
    """
    invoice_id = item.payload["invoice_id"]

    try:
        #
        ## POST updates in place and omitted fields are retained, so this
        ## touches nothing but the flag - the line items are not resent.
        _request(
            "POST",
            f"Invoices/{invoice_id}",
            json_body={"Invoices": [{"InvoiceID": invoice_id, "SentToContact": True}]},
        )
    except XeroUnavailableError as e:
        raise Retryable(str(e)) from e
    except XeroError as e:
        #
        ## Caught second: XeroUnavailableError is one of these too.
        raise Permanent(str(e)) from e

    number = item.payload.get("invoice_number") or invoice_id
    logger.info("Xero invoice [%s] marked as sent", number)
