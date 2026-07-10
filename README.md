# scout-campsite-bookings

## Xero invoicing

When a booking with money owed passes its departure date it moves to `Invoice`
status. The booking page then offers **Raise Invoice in Xero**, which creates
an AUTHORISED sales invoice via the Xero API (reference = booking ID), has
Xero email it to the leader, and marks the booking `Completed`. Group names
are matched to Xero contacts automatically; the first time a group cannot be
matched you are asked to pick an existing contact or confirm creating a new
one, and the choice is remembered (`data/xero_contacts.json`).

### One-time setup

1. Create a free app at <https://developer.xero.com/app/manage> (type
   "Web app") with redirect URI `http://localhost:8400/callback`. Note the
   client ID and generate a client secret.
2. Set `XERO_ENABLED=True`, `XERO_CLIENT_ID` and `XERO_CLIENT_SECRET` in the
   app's env file (`docker-mnt/config/.env.production` on the NAS). Optional:
   `XERO_ACCOUNT_CODE` (default 200), `XERO_TAX_TYPE` (default NONE),
   `XERO_INVOICE_DUE_DAYS` (default 30), and `XERO_BRANDING_THEME` (a branding
   theme *name*, e.g. `Riffhams` - applied to invoices and set as the default
   sales theme on newly created contacts; blank = org default).
3. On a PC, run `python scripts/xero_bootstrap.py`, sign in, and pick the
   organisation (use the free **Demo Company** for testing first). This writes
   `docker-mnt/config/xero_token.json`.
4. Restart the container and check the Admin page shows Xero as connected.

The app keeps the connection alive by refreshing the token. If it reports the
connection expired (refresh token unused for 60+ days), re-run the bootstrap
script - never re-copy an old token file, as Xero rotates refresh tokens.

The **Enable Xero** toggle in the nav bar works like the email toggle: when
off, the raise-invoice button only reports what it *would* do and changes
nothing.
