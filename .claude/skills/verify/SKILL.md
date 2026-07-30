---
name: verify
description: Run the Flask app locally against dev data to verify changes end-to-end.
---

# Verify scout-campsite-bookings locally

## Launch

```bash
~/.venvs/scout-campsite-bookings/bin/python app.py   # run_in_background; serves http://127.0.0.1:5000
```

- The venv lives outside the repo, on the WSL filesystem — `/mnt/d` is a CIFS
  mount with no symlink support and slow small-file I/O. Rebuild it with
  `uv venv --python 3.13 ~/.venvs/scout-campsite-bookings` +
  `uv pip install --python ~/.venvs/scout-campsite-bookings/bin/python -r requirements.txt`.

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
- Known pre-existing dev breakage: saving field edits on a Confirmed/Invoice
  booking 500s at `update_calendar_entry` because dev `config/` has no Google
  `credentials.json`. The edits ARE applied in memory before the crash (reload
  the page to see them); only the calendar step dies. Not caused by your change.
  (The old `bookable_facilities` 500 note is obsolete - dev config now has it.)
- `/admin/archive_old_bookings` really archives old dev bookings — avoid unless
  that's what you're testing.
