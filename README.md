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

## Build, deploy & releases

The image is built by GitHub Actions on every push to `main` and published to
**`ghcr.io/ploughpuff/scout-campsite-bookings:latest`**. Nothing is built on the
NAS: `/volume1/docker/scout-campsite-bookings/` holds only `docker-compose.yml`
and `docker-mnt/` (data, config and email templates), with no source tree.

### Deploying a change

1. Push to `main` and wait for **Build and publish image** to go green
   (`gh run watch`). A red run publishes nothing - the build job needs pytest
   and pylint to pass first.
2. From WSL run `bash scripts/deploy.sh` (or `powershell scripts\deploy.ps1`
   from Windows). Both do the same thing over SSH:

   ```sh
   cd /volume1/docker/scout-campsite-bookings
   sudo docker compose pull      # fetch the newly built image
   sudo docker compose up -d     # recreate the container with it
   ```

   A plain `restart` is **not** enough - it reuses the image already on disk, so
   you must `pull` first.
3. The script prints the container status and the running build's `/health`.

The GHCR package is public, so the NAS needs no `docker login`.

### What build am I running?

```sh
curl -s http://192.168.1.123:8080/health
# {"ok":true,"version":"v2.3.0","commit":"0cf11ad","built":"2026-09-04T20:38:34Z"}
```

Use the NAS's IP, not `jam`: that name is an SSH-config alias, and under WSL it
resolves to `::1` (localhost), so `http://jam:8080` quietly hits nothing.

The Admin page shows the same thing under **Running Version**. The values are
baked into the image at build time (Docker build args set by the workflow), so
they describe the image, not the host; a container run straight from a checkout
reports `dev` / `unknown`.

`version` is the **nearest release tag** (`git describe --tags --abbrev=0`), so
a `:latest` image built from `main` still shows `v2.2.2` rather than only a hash.
The commit alongside it is what identifies the exact build - and it is also the
cache buster appended to every static asset URL (`styles.css?v=4d1c86f`), which
is why the version alone can't do that job: Flask serves static files with a
week of max-age, and the version stands still between tags.

### Cutting a release

```sh
bash scripts/bump-tag.sh patch     # or minor / major
```

That creates and pushes the next `vX.Y.Z`, which publishes
`ghcr.io/ploughpuff/scout-campsite-bookings:2.3.0` and `:2.3`, and makes that
build report `v2.3.0` as its version. Push your commits to `main` **first** (the
script refuses otherwise): a tag push does not move `:latest`, which only tracks
`main`, so tagging an unpushed commit gives you a release image `main` lacks.

### The compose file

`docker-compose.yml` in this repo is the master copy; the one on the NAS is a
manual copy of it (there is no checkout on the NAS to `git pull`). If you change
ports, mounts or the healthcheck here, copy the file across and
`sudo docker compose up -d` again.

## Release history

Versions deployed to the NAS before the release log moved to git tags:

- v1.4.3 - 25 May 2025
- v1.4.5 - 19 Jun 2025 - Fixed Day Visits missing leader address
- v2.0.0 - 6 Jul 2025 - Moved to schema v2, added cost estimate, facilities
- v2.0.1 - 6 Aug 2025 - Fixed manual pricing being overwritten by the estimator
- v2.0.2 - 26 Aug 2025 - Better handle group name not found in field mappings
- v2.0.3 - 29 Aug 2025 - Fixed cost estimate covering only one day
- v2.0.4 - 4 Sep 2025 - Improved archive old bookings function
- v2.0.5 - 6 Nov 2025 - del_cal_event() handles already-deleted entries
- v2.1.0 - 2 Feb 2026 - Added STATS section to the Admin page
- v2.1.1 - 8 Jun 2026 - 10 minor bug fixes

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

## The outbox

Every email and calendar change is queued in `data/outbox.json` first and only
leaves once the far end has confirmed it. A third scheduler job drains the queue
on every 30-second tick, so work stranded by an outage resumes within half a
minute of the network coming back. The Admin page lists anything outstanding,
with **Try now** and **Discard** per item.

This exists because of what happened in June 2026. The archive sweep asked
Google to delete six calendar events, DNS was down each time, the delete failed
silently, and the bookings were archived anyway - and since the archive keeps
only `BookingData`, the `google_calendar_id` went with them. Six events were
left on the calendar with nothing anywhere pointing at them.

Two rules keep that from recurring:

- **A queued payload is self-contained.** It carries everything needed to carry
  the action out, with no lookup back into a live booking, because the record
  may be edited or archived long before the queue drains. A calendar delete
  carries the event id *and* the booking id; an email carries the message,
  already rendered.
- **Handlers are idempotent.** A failure we could not read might still have
  landed, so a retry has to cope with either. Calendar events are found by the
  `booking_id` stamped on them rather than by a stored id, and an event that is
  already gone counts as done.

Work that may only happen *after* something else has been confirmed rides along
as a follow-on: an item can carry a `then` list, and each entry is queued in its
place once the item succeeds. Marking a Xero invoice as sent is why this exists -
see below.

Failures are sorted into two kinds. *Retryable* means we never got a usable
answer - DNS, timeout, TLS, 5xx - and the item backs off (1 min, doubling, capped
at 6 hours) and tries again. *Permanent* means the far end answered and refused -
a bad address, a rejected login - and the item is **blocked**: kept, never
retried, and shown on the Admin page. Anything still failing after 48 hours
blocks too. Nothing is ever dropped silently; discarding is a manual decision.

Every outbound call also carries a timeout. Without one a black-holed socket
blocks its caller forever, and when that caller is the single scheduler thread
every background job stops for good - the thread stays alive, so nothing
restarts it. The Admin page reports a job that has overrun, which only a request
thread can notice: the thread that would otherwise spot it is the one that is
stuck.

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

Once that email has actually been delivered - not merely queued - the invoice is
flagged as **sent** in Xero (`SentToContact`). Xero holds back its payment
reminders on an invoice it believes was never sent, and its list reads "Invoice
not sent" until this lands. It is chained off the outbox rather than done beside
the email on purpose: if the message is refused (a bad leader address), Xero is
never told it went out, so its reminders cannot chase a leader over an invoice
they never received. A `xero_email` item briefly appears on the Admin page
between the two.

Reminders also have to be switched on in Xero itself, and the contact needs an
email address there - neither is anything this app sets.

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
