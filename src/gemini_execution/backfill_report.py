#!/usr/bin/env python3
"""Backfill both families' expired tapes and set them side by side."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from src.gemini_execution import backfill as BF
from src.gemini_execution import contract_cohort as CC


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=48)
    ap.add_argument("--out", default="analysis/gemini/backfill_15m_vs_5m.json")
    a = ap.parse_args(argv)
    out = {"label": "GEMINI_BACKFILL_V1",
           "utc": datetime.now(timezone.utc).isoformat(),
           "by_cohort": {}}
    for co in (CC.BTC15M, CC.BTC5M):
        r = BF.backfill(co, windows=a.windows)
        out["by_cohort"][co.name] = r
        p = r.get("pooled") or {}
        print("%-7s windows=%-3s readable=%-3s traded=%-3s (%s) prints=%-6s "
              "two_sided=%-5s alt=%-6s bal=%s" % (
                  co.name, r["windows_requested"], r["windows_readable"],
                  r["windows_with_any_print"], r["traded_share"],
                  p.get("prints"), p.get("two_sided_share"),
                  p.get("median_alternation_rate"),
                  p.get("median_side_balance")), flush=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
