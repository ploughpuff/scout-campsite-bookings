# scout-campsite-bookings

## Pricing

Prices live in `docker-mnt/config/pricing.json` (template:
`config.example/pricing.json`). Every charge - the event itself and each
chargeable facility - is priced by one rule:

    cost = rate x (headcount if per_person) x (nights if per_night)

All amounts are in **pence**. Set both flags per charge, so a flat nightly
charge (`per_person: false, per_night: true`, e.g. the Roxby Hut) and a
per-head single visit (`per_person: true, per_night: false`, e.g. a day visit)
are both expressible. A day or evening booking spans no nights, so a
`per_night` rate is charged once for that single occasion.

The `facilities` block is also the list of facilities the booking form offers,
in the order given; a facility carries a surcharge only if it has a `rates`
block. Every rates block must name every group type in `field_mappings.json` -
the app refuses to start otherwise, since a missing rate would silently
invoice nothing.

Changing a rate does **not** reprice existing bookings: stored estimates only
recalculate when a booking is edited. `python scripts/check_repricing.py` is a
read-only dry run showing which bookings a config change would affect.

## Unattended jobs

A background thread in the Flask process pulls new booking forms every
`PULL_INTERVAL_MINUTES` (default 60) and runs the archive sweep at
`ARCHIVE_AT_HOUR` (default 03:00, **UK local time** - the container runs UTC, so
this is not the same as 3am UTC in summer). Set `SCHEDULER_ENABLED=False` to
turn both off and go back to pressing **Pull Now**.

It runs in-process rather than as a cron container because the app holds every
booking in memory and rewrites `bookings.json` wholesale - a separate process
writing that file would be overwritten the moment the app next saved.

The archive sweep is still triggered by page traffic as well. Both paths go
through the same once-a-day gate, so whichever happens first does the work and a
sweep missed while the NAS was off still runs on your next visit.

`data/run_state.json` records when each job last ran and how it got on; the
Admin page shows it, and "Bookings Last Retrieved" on the bookings page is the
heartbeat - if the pull dies, that age just keeps climbing. Nothing about a
failed pull is silent: it is logged, recorded, and shown on the Admin page,
and the thread survives to try again.

Saving skips the write entirely when a file would come out unchanged, so an
hourly pull that finds nothing costs no disk and does not rotate a backup off
the end of the 50 kept.

## Xero invoicing

When a booking with money owed passes its departure date it moves to `Invoice`
status. The booking page then offers **Raise Invoice in Xero**, which creates
an AUTHORISED sales invoice via the Xero API (reference = booking ID), emails
it from the app to the leader's address on the booking (invoice PDF attached,
plus Xero's view/pay-online link), and marks the booking `Completed`. Line
items are itemised from the pricing config where they reproduce the booking's
cost estimate exactly; a manually overridden estimate falls back to one line
for the total. A flat nightly facility charge bills as a single line with the
night count as its quantity, e.g. `Roxby Hut - 5th August 2026 (4 nights)`.

A group's first invoice always shows a confirmation page to link the group to
a Xero contact: pick from likely matches, search Xero by another name, or
create a new contact. The choice is remembered (`data/xero_contacts.json`),
so it happens once per group. Existing Xero contacts are never modified. The
invoice email template lives at `email_templates/invoice_email.html` (and the
production copy in `docker-mnt/email_templates/`).

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
3. On a PC, run `~/.venvs/scout-campsite-bookings/bin/python
   scripts/xero_bootstrap.py`, sign in, and pick the organisation (use the free
   **Demo Company** for testing first). This writes
   `docker-mnt/config/xero_token.json`. WSL works: the script prints the consent
   URL if it can't open a browser, and WSL2 forwards `localhost:8400` so the
   Windows browser reaches the callback.
4. Restart the container and check the Admin page shows Xero as connected.

The app keeps the connection alive by refreshing the token. If it reports the
connection expired (refresh token unused for 60+ days), re-run the bootstrap
script - never re-copy an old token file, as Xero rotates refresh tokens.

The **Enable Xero** toggle in the nav bar works like the email toggle: when
off, the raise-invoice button only reports what it *would* do and changes
nothing.
