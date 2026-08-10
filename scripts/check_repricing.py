"""Dry-run report: what every live booking would cost under the current pricing config.

Read-only. Prints the bookings whose stored estimate no longer matches, so the
new numbers can be eyeballed before anything is changed.

    python scripts/check_repricing.py [path/to/bookings.json]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# pylint: disable=wrong-import-position
from models.pricing import charge_lines, estimate_cost  # noqa: E402
from models.schemas import BookingData  # noqa: E402


def main(path: str) -> None:
    """Print a per-booking comparison of stored vs recalculated cost."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    changed = 0
    for item in data["items"]:
        booking = BookingData.model_validate(item["booking"])
        stored = item["tracking"]["cost_estimate"]
        fresh = estimate_cost(booking)
        if stored == fresh:
            continue

        changed += 1
        status = item["tracking"]["status"]
        invoice = item["booking"].get("xero_invoice_number") or "-"
        print(
            f"{booking.id:16} {status:10} invoice={invoice:9} "
            f"{booking.num_overnights()}n x {booking.group_size:>3}p  "
            f"£{stored / 100:>8.2f} -> £{fresh / 100:>8.2f}  ({(fresh - stored) / 100:+.2f})"
        )
        for line in charge_lines(booking):
            print(
                f"{'':16}   {line.label:22} {line.quantity:>4} x "
                f"£{line.unit_pence / 100:>6.2f} = £{line.total_pence / 100:>8.2f}"
            )

    print(f"\n{changed} of {len(data['items'])} bookings would change.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/bookings.json")
