#!/usr/bin/env python3
"""Pull Stage B2 window evidence out of a saved Railway log payload.

The live heartbeat is ~80KB per record, so reading these logs directly is
expensive and almost all noise. This keeps only the records that carry
settlement and order-lifecycle truth, and prints them compactly.

    python3 extract_campaign_evidence.py <saved-log.json> [slug-substring]

Reads ONLY what the service already emitted. It computes nothing, and it never
relabels an unsettled figure as realized: L5_WINDOW_ACCOUNTING is submitted/
filled/cancelled truth plus MODELLED pair economics, and L5_LIVE_SETTLED is
the venue's own settlement line. They are printed separately for that reason.

Field paths were taken from `window_record` and `pair_economics` in
src/l5_window_accounting.py rather than guessed; the first version of this
script guessed and silently printed nothing useful.
"""
from __future__ import annotations

import json
import re
import sys

WANT = ("L5_WINDOW_ACCOUNTING", "L5_LIVE_SETTLED", "L5_LIVE_WINDOW",
        "L5_LIVE_DISARMED", "L5_LIVE_ALARM", "SERVICE_ROLE_MISMATCH",
        "L5_AUTHORIZATION_PREFLIGHT")

#: dotted paths, because window_record nests. Missing paths are skipped.
KEEP = {
    "L5_WINDOW_ACCOUNTING": (
        "market", "phase",
        "orders.submitted", "orders.acknowledged", "orders.terminal",
        "orders.filled_orders", "orders.partial_fills", "orders.cancelled",
        "orders.rejected",
        "contracts.filled_total", "contracts.first_leg_filled",
        "contracts.complement_filled",
        "execution.maker_orders", "execution.taker_orders",
        "execution.actual_fees_usd",
        "pair_economics.pairs_completed", "pair_economics.matched_qty",
        "pair_economics.pair_edge_usd", "pair_economics.defensive_loss_usd",
        "pair_economics.defensive_pairs", "pair_economics.worst_pair_cost",
        "pair_economics.residual_qty", "pair_economics.unpriceable_legs",
        "pair_economics.pairs",
        "incentives.rebate_credited_usd", "incentives.rebate_pending_usd",
        "incentives.eligibility"),
    "L5_LIVE_SETTLED": (
        "market", "venue_state", "up_qty", "dn_qty", "up_cost", "dn_cost",
        "matched_qty", "matched_cost", "matched_redeem", "matched_pnl",
        "residual_side", "residual_qty", "residual_cost", "residual_redeem",
        "residual_pnl", "settlement_pnl", "cost_total", "redemption_total",
        "realized_today_after", "committed_released", "retired"),
    "L5_LIVE_WINDOW": ("market", "decisions", "preflight", "armed"),
    "L5_LIVE_DISARMED": ("market", "why", "detail", "claim_taken"),
    "L5_LIVE_ALARM": ("kind",),
    "SERVICE_ROLE_MISMATCH": ("expected", "got"),
    "L5_AUTHORIZATION_PREFLIGHT": ("market", "ok", "fail", "unknown"),
}


def dig(obj, path: str):
    """Walk a dotted path. Returns the sentinel `dig` itself when absent."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return dig
        cur = cur[part]
    return cur


def body(msg: str) -> tuple:
    """(label, parsed-or-None). Heartbeats truncate, so parsing can fail."""
    # the label carries digits -- L5_* -- which an [A-Z_]+ class drops.
    m = re.match(r"^([A-Z0-9_]+)\s+(\{.*)$", msg, re.S)
    if not m:
        return (None, None)
    try:
        return (m.group(1), json.loads(m.group(2)))
    except Exception:
        return (m.group(1), None)


def main(argv: list) -> int:
    if not 2 <= len(argv) <= 3:
        sys.stderr.write(__doc__ or "")
        return 2
    want_slug = argv[2] if len(argv) == 3 else None
    with open(argv[1]) as fh:
        doc = json.load(fh)

    seen = set()
    n = 0
    for row in doc.get("deploy") or ():
        ts = str(row.get("timestamp") or "")[:19]
        label, obj = body(str(row.get("message") or ""))
        if label not in WANT:
            continue
        if obj is None:
            print("%s  %s  <UNPARSEABLE: record truncated>" % (ts, label))
            continue
        mkt = str(obj.get("market") or "")
        if want_slug and want_slug not in mkt:
            continue
        out = {}
        for p in KEEP.get(label, ()):
            v = dig(obj, p)
            if v is not dig:
                out[p] = v
        if not out:
            continue
        key = (label, json.dumps(out, sort_keys=True, default=str))
        if key in seen:
            continue
        seen.add(key)
        n += 1
        print("%s  %s" % (ts, label))
        print("    " + json.dumps(out, indent=2, default=str
                                  ).replace("\n", "\n    "))
    if not n:
        print("no matching evidence records in %s" % argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
