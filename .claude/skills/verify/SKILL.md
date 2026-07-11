---
name: verify
description: Run the Flask app locally against dev data to verify changes end-to-end.
---

# Verify scout-campsite-bookings locally

## Launch

```bash
.venv/Scripts/python.exe app.py   # run_in_background; serves http://127.0.0.1:5000
```

- `APP_ENV=development` (from `.env`) → uses the repo's local `data/` dir, NOT the
  live NAS data in `docker-mnt/`. Safe to run locally.
- `XERO_ENABLED=False` in dev; Xero API calls are off, but file-based bits
  (`data/xero_contacts.json`, gitignored) can be seeded by hand for testing.
- Startup prints env config then "Running on http://127.0.0.1:5000".
- App logs (incl. 500 tracebacks) go to `data/app.log`, not stdout.

## Drive

- All bookings table: `curl http://127.0.0.1:5000/bookings`
- Booking detail: `curl http://127.0.0.1:5000/booking/<ID>` — IDs like SCH-26-0020
  are in `data/bookings.json` (dev dataset has both chargeable and FREE bookings).

## Gotchas

- Page loads call `auto_update_statuses()` which can rewrite `data/bookings.json`
  (dev copy — acceptable, but statuses may shift between requests).
- Known pre-existing dev breakage: `/booking/<id>` 500s with
  `'NoneType' object is not iterable` at `booking.html` `{% for facility in
  bookable_facilities %}` because dev `config/field_mappings.json` lacks
  `bookable_facilities`. Not caused by your change.
- `/admin/archive_old_bookings` really archives old dev bookings — avoid unless
  that's what you're testing.
