"""Reconcile ONE ET day's maker-rebate payout against the accrual we committed.

    python -m src.gemini_execution.maker_rebate_reconcile \
        --day 2026-09-21 --out analysis/gemini/maker_rebate_reconcile_<ts>.json

THE ACCRUAL IS NOT RECOMPUTED TO FIT. The eighteen EP016 fills are declared
here as a frozen list with their provenance, and the dollar figure comes from
`rebate_program.daily_accrual` -- the same code path that was committed in
advance at d28f289 -- rather than from a constant typed in after seeing the
venue's answer. If the venue disagrees, the disagreement is the finding.

THREE DELTAS, NOT ONE. The venue publishes qualifying fill count and
qualifying volume beside the dollars, so a dollar match reached with the wrong
fill count shows up as the coincidence it is.

CREDITED IS NOT ACCRUED. A rebate enters economics only when the payout row's
status is PAID. A row in any other status is something we have read, not money
we have been paid, and it is never netted against an accrual or merged into
trading P&L.

READ-ONLY. Two GETs and one POST, all enumerated in `maker_rebate_api`; no
order, cancel or transfer path is reachable from here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from src.gemini_execution import maker_rebate_api as API
from src.gemini_execution import rebate_program as RP

LABEL = "GEMINI_MAKER_REBATE_RECONCILE_V1"

#: The eighteen COHORT-1 (EP016) fills, in order, one contract each, all maker
#: at zero reported fee. Source: analysis/gemini/EVIDENCE_PACKET_016_COHORT1.md
#: -- four windows on ET day 2026-09-21 (UTC 2026-09-22 02:30-03:15).
EP016_FILLS: List[Dict[str, str]] = [
    {"window": "0230", "side": "NO", "price": "0.45"},
    {"window": "0230", "side": "NO", "price": "0.40"},
    {"window": "0230", "side": "YES", "price": "0.45"},
    {"window": "0230", "side": "YES", "price": "0.37"},
    {"window": "0230", "side": "YES", "price": "0.33"},
    {"window": "0230", "side": "NO", "price": "0.33"},
    {"window": "0230", "side": "NO", "price": "0.19"},   # below the $0.20 band
    {"window": "0245", "side": "YES", "price": "0.37"},
    {"window": "0245", "side": "YES", "price": "0.34"},
    {"window": "0300", "side": "YES", "price": "0.42"},
    {"window": "0300", "side": "YES", "price": "0.43"},
    {"window": "0300", "side": "NO", "price": "0.54"},
    {"window": "0300", "side": "NO", "price": "0.40"},
    {"window": "0300", "side": "YES", "price": "0.36"},
    {"window": "0300", "side": "YES", "price": "0.25"},
    {"window": "0315", "side": "NO", "price": "0.52"},
    {"window": "0315", "side": "YES", "price": "0.39"},
    {"window": "0315", "side": "YES", "price": "0.32"},
]

#: what the accrual was stated to be BEFORE the payout was read, so a silent
#: drift in the accrual code is visible rather than absorbed
COMMITTED = {"n_fills": 18, "n_eligible": 17,
             "qualifying_volume_usd": "6.67",
             "accrued_usd": "0.08332590", "would_pay_usd": "0.08"}


def our_accrual() -> Dict[str, Any]:
    a = RP.daily_accrual([{"contracts": "1", "price": f["price"]}
                          for f in EP016_FILLS])
    drift = {k: (str(a.get(k)), v) for k, v in COMMITTED.items()
             if str(a.get(k)) != str(v)}
    a["matches_committed"] = not drift
    a["drift_from_committed"] = drift or None
    a["provenance"] = ("analysis/gemini/EVIDENCE_PACKET_016_COHORT1.md, "
                       "eighteen maker fills, one contract each")
    return a


def pick_payout(payouts: Mapping[str, Any], day: str) -> Optional[Dict[str, Any]]:
    """The payout row covering an ET calendar day.

    Matched on the row's own dates rather than on position in the list, and a
    row is returned only when its date actually contains `day` -- picking "the
    most recent row" would silently reconcile against the wrong day.
    """
    rows = payouts.get("rows") or []
    for r in rows:
        for k in ("paid_at", "created_at"):
            v = str(r.get(k) or "")
            if v.startswith(day):
                return dict(r, matched_on=k)
    return None


async def run(day: str) -> Dict[str, Any]:
    acc = our_accrual()
    venue = await API.read_all(limit=100, date_from=day, date_to=day)
    payouts = venue.get("payouts") or {}
    row = pick_payout(payouts, day) if payouts.get("readable") else None
    rec = API.reconcile(row, acc)
    return {
        "label": LABEL,
        "utc": datetime.now(timezone.utc).isoformat(),
        "et_day": day,
        "accrual": acc,
        "venue_read": venue,
        "payout_row": row,
        "reconciliation": rec,
        "labels": ("accrued and credited are separate lines. Nothing here is "
                   "trading P&L, and nothing here may enter model selection."),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-09-21",
                    help="ET calendar day, YYYY-MM-DD")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = asyncio.run(run(a.day))
    text = json.dumps(rep, indent=2, sort_keys=True, default=str)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text + "\n")
    print(text)
    r = rep.get("reconciliation") or {}
    print("VERDICT %s | credited=%s | deltas=%s"
          % (r.get("verdict"), r.get("credited"), r.get("deltas")),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
