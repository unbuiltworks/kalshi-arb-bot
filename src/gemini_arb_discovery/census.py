"""Runner for the Gemini-wide liquidity census. Read-only."""
from __future__ import annotations

import argparse
import json
import os
import time

from src.gemini_arb_discovery import stats as STATS
from src.gemini_arb_discovery import universe as UN


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="analysis/gemini_arb_discovery/census")
    ap.add_argument("--books-per-category", type=int, default=40)
    ap.add_argument("--tape-per-category", type=int, default=25)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    disc = UN.fetch_all_events()
    events = UN.active_events(disc["events"])
    out = {
        "at": time.time(),
        "discovery": {k: disc[k] for k in ("pages", "errors", "total_claimed")},
        "active_events": len(events),
        "active_contracts": sum(len(UN.active_contracts(e)) for e in events),
        "quotes_full_coverage": STATS.quote_census(events),
        "books_sampled": STATS.book_census(events,
                                           per_category=a.books_per_category),
        "tape_sampled": STATS.tape_census(events,
                                          per_category=a.tape_per_category),
    }
    with open(os.path.join(a.out, "census.json"), "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
