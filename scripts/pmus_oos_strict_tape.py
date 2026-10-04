"""Restrict the harvested OOS tape to the windows the replay may score.

Two jobs, both mechanical:

  1. keep only `book`/`trade` rows for BTC 15-minute markets, because
     `pmus_favorite_dca.load` indexes strictly by those two kinds;
  2. keep only windows whose start is STRICTLY AFTER the frozen development
     cutoff.

The cutoff is READ from `pmus_oos_extract_and_inventory.DEV_CUTOFF_SLUG`, the
module that already owns it, rather than retyped here -- so the OOS/DEVELOPMENT
boundary cannot drift from the development freeze. The 28 development windows
are refused, not relabelled.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.pmus_favorite_dca import window_start                # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "inv", os.path.join(_HERE, "pmus_oos_extract_and_inventory.py"))
INV = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(INV)

MARKET = "btc-updown-15m"


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: pmus_oos_strict_tape.py <harvested tape> <out>",
              file=sys.stderr)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    cut = INV.dev_cutoff_epoch()
    starts: dict = {}
    kept = at_or_before = 0
    with open(src) as f, open(dst, "w") as o:
        for line in f:
            r = json.loads(line)
            if r.get("kind") not in ("book", "trade"):
                continue
            slug = r.get("slug") or ""
            if MARKET not in slug:
                continue
            st = starts.get(slug)
            if st is None:
                st = starts[slug] = window_start(slug)
            if st > cut:
                o.write(line)
                kept += 1
            else:
                at_or_before += 1
    print(json.dumps({
        "dev_cutoff_slug": INV.DEV_CUTOFF_SLUG,
        "dev_cutoff_epoch": cut,
        "rows_kept_oos": kept,
        "rows_refused_at_or_before_cutoff": at_or_before,
        "slugs_seen": len(starts),
        "slugs_oos": sum(1 for v in starts.values() if v > cut),
        "slugs_refused": sum(1 for v in starts.values() if v <= cut),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
