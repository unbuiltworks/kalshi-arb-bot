#!/usr/bin/env python3
"""Current engine vs book-driven completion, on ONE recorded sequence.

THE SEQUENCE. `L5_SHADOW_RESIDUAL_BOOK_SAMPLE`, one shadow variant, which
samples the complement book every 5 s while inventory is unmatched, driven
by `UNMATCHED_INVENTORY_NOT_ORDER_STATE`. The BOOKS are real, authenticated
WS snapshots with their own `stream_age_s`. Both policies see exactly the
same rows; only the trigger differs.

  * CURRENT  -- evaluates at the measured action cadence (~34 s), the
    submit-to-submit interval from C8's two uninterrupted windows.
  * BOOK     -- evaluates at `BC.DECIDE_EVERY_S` (5 s), the tape's own rate.

Both run the SAME `l5_book_completion.decide`, so the comparison isolates
the trigger and nothing else. Running the current engine's own executor path
here would compare two different pricing functions and attribute the
difference to the trigger.

WHAT THIS CANNOT DO, STATED FIRST.

1. THE FIRST LEG'S COST IS NOT ON THIS TAPE. It is swept across the range
   our real campaigns actually opened at (own 0.24-0.46), and every result
   is reported per assumed cost. A single number here would be a choice
   dressed as a measurement.
2. THE INVENTORY IS MODELLED. This is a shadow variant, so the residual
   quantities are not venue positions. The decision counts are therefore
   about the BOOK's opportunity set, not about money we held.
3. A DECISION IS NOT A FILL. Accepted decisions are counted, and separately
   counted under the queue test (queue ahead <= 50x the quantity needed).
   Expected P&L is reported ONLY for queue-reachable decisions and ONLY as
   locked pair economics -- $1.00 redemption minus the pair's own cost --
   which is the one quantity that does not depend on direction.
"""
from __future__ import annotations

import glob
import json
import pathlib
import sys
from decimal import Decimal as D

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_book_completion as BC          # noqa: E402
TAPES = "analysis/l5/residual_exit/preserved/btc15m_20261026_*.ndjson"
VARIANT = "RESIDUAL_URGENCY"
ACTION_CADENCE_S = 34.0
QUEUE_MULT = D("50")
#: the own prices our real campaigns opened first legs at (C6/C7/C8 tapes).
FIRST_LEG_OWN = ("0.24", "0.31", "0.40", "0.46")


def rows_for(path):
    out = []
    for line in open(path):
        r = json.loads(line)
        if not str(r.get("label", "")).endswith("RESIDUAL_BOOK_SAMPLE"):
            continue
        b = json.loads(r["body"])
        if b.get("shadow_variant") != VARIANT:
            continue
        reach = b.get("reachability") or {}
        top = reach.get("opposite_top_of_book") or {}
        mq = reach.get("maker_quote") or {}
        bk = ((b.get("book") or {}).get("row") or {})
        bids, asks = bk.get("bids") or [], bk.get("asks") or []
        if not bids or not asks:
            continue
        resid = D(str(b.get("residual") or 0))
        if resid == 0:
            continue
        out.append({
            "t": float(b["t"]),
            "held_side": str(b.get("held_side") or ""),
            "need": abs(resid),
            "bid": D(str(bids[0][0])), "ask": D(str(asks[0][0])),
            "bid_shares": str(bids[0][1]), "ask_shares": str(asks[0][1]),
            "age": str((b.get("book") or {}).get("sync", {}).get(
                "row_age_s", 0.0)),
            "queue": D(str(mq.get("queue_ahead_bound") or 0)),
            "join_own": D(str(top.get("maker_join_own_px") or 0)),
        })
    out.sort(key=lambda r: r["t"])
    return out


def run(rows, cadence, first_own):
    ws = rows[0]["t"]
    last = None
    seen = 0
    accepted = []
    reasons = {}
    for r in rows:
        if last is not None and (r["t"] - last) < cadence:
            continue
        last = r["t"]
        seen += 1
        d = BC.decide(
            market="replay", held_side=r["held_side"],
            held_qty=str(r["need"]),
            held_cost_usd=str(r["need"] * D(first_own)),
            deficient_qty="0", deficient_cost_usd="0",
            book={"bid": str(r["bid"]), "ask": str(r["ask"]),
                  "stream_age_s": "0.1",
                  "bid_shares": r["bid_shares"],
                  "ask_shares": r["ask_shares"]},
            now_t=r["t"], window_start=ws, resting=(),
            residual_budget_usd="3")
        reasons[d["reason"]] = reasons.get(d["reason"], 0) + 1
        if d.get("ok"):
            reach = r["queue"] <= QUEUE_MULT * r["need"]
            surplus = (D(1) - D(d["pair_own_cost"])) * r["need"]
            accepted.append({"t": r["t"] - ws, "reachable": reach,
                             "pair": d["pair_own_cost"],
                             "surplus": surplus, "queue": str(r["queue"])})
    reach = [a for a in accepted if a["reachable"]]
    return {
        "evaluations": seen,
        "accepted": len(accepted),
        "accepted_queue_reachable": len(reach),
        "locked_pair_surplus_if_reachable_filled_usd": str(
            round(sum(a["surplus"] for a in reach), 4)),
        "top_reasons": sorted(reasons.items(), key=lambda kv: -kv[1])[:4],
    }


def main() -> int:
    out = {}
    for f in sorted(glob.glob(str(ROOT / TAPES))):
        win = pathlib.Path(f).stem
        rows = rows_for(f)
        if len(rows) < 4:
            out[win] = {"usable_samples": len(rows)}
            continue
        per = {}
        for own in FIRST_LEG_OWN:
            per[own] = {
                "CURRENT_action_cadence_34s": run(rows, ACTION_CADENCE_S, own),
                "BOOK_cadence_5s": run(rows, BC.DECIDE_EVERY_S, own),
            }
        out[win] = {"usable_samples": len(rows),
                    "span_s": round(rows[-1]["t"] - rows[0]["t"], 1),
                    "by_assumed_first_leg_own_px": per}
    print(json.dumps({
        "windows": out,
        "labels": {
            "not_a_fill": ("accepted decisions are not fills. "
                           "`accepted_queue_reachable` applies the generous "
                           "queue test (queue ahead <= 50x need); the "
                           "surplus figure is conditional on those filling "
                           "and is NOT realized, settled or expected."),
            "inventory": "modelled shadow inventory, not venue positions",
            "first_leg": ("swept over the own prices our real campaigns "
                          "opened at; the tape does not carry it"),
        },
    }, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
