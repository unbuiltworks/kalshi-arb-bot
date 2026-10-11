"""L5_WINDOW_FORENSICS_V1 -- one BTC15m window, preregistered and scored blind.

OWNER 2026-09-17. Observation only: no client, no submit path, no order can
originate here.

WHAT MAKES THIS DIFFERENT FROM THE RECORDER. L5_BOOK_CONTEXT_V1 records what
happened. This *commits to a prediction first*. At every decision tick it
writes the surface the frozen hypothesis says should be resting -- side, price,
quantity, both outcomes -- and it writes it BEFORE any L5 action in that
interval is known. The prediction is never revised. Scoring happens afterwards
against rows that already exist on disk.

    FITTING AFTER SEEING THE WINDOW WOULD MAKE THE RESULT MEANINGLESS. The
    prediction rows carry their own timestamp and are emitted in file order
    ahead of the executions they are scored against, so the ordering is
    checkable by anyone reading the tape rather than trusted.

CAPTURE BEGINS BEFORE THE WINDOW OPENS (`--lead-s`, default 120) and continues
to window_end + 300s so recycling can be observed.

THREE ATTRIBUTION CLASSES, NEVER PROMOTED INTO ONE ANOTHER:

    OBSERVED_L5_PARENT            the chain names L5 as the order's owner.
    L5_COMPATIBLE_BOOK_LIQUIDITY  anonymous size resting at a price/side L5
                                  later executed at. It MAY be his. It is not
                                  evidence that it is.
    INFERRED_SURFACE              what our hypothesis predicted. Ours, not his.

THE PRIMARY PREREGISTERED TEST, fixed before the first window runs:

    Was liquidity compatible with L5's eventual parent side/price/quantity
    already resting in the recorded book BEFORE its first chain-proven
    execution?

THE PROCEDURE IS FROZEN ACROSS WINDOWS. Subsequent windows repeat it
identically; nothing is retuned between them during the initial prospective
sample.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.l5_shadow import venues as V
from src.l5_chain_observer import observer as OB
from src.l5_book_context import ladder, summarize, size_at, outcome_tokens
from src.l5_passive_surface_shadow import SPEC
from analysis.l5.causal import order_id_forensics as OF

LABEL = "L5_WINDOW_FORENSICS_V1"
TICK = 0.01
DUR = 900

OBSERVED = "OBSERVED_L5_PARENT"
COMPATIBLE = "L5_COMPATIBLE_BOOK_LIQUIDITY"
INFERRED = "INFERRED_SURFACE"

#: PREREGISTERED. Frozen before the first window; identical for every window.
PROCEDURE = {
    "lead_s": 120, "follow_s": 300,
    "book_interval_s": 1.0, "prints_interval_s": 3.0,
    "activity_interval_s": 3.0, "prediction_interval_s": 5.0,
    "recycle_marks_s": (5, 15, 30, 60, 120, 300),
    "primary_test": ("was liquidity compatible with L5's eventual parent "
                     "side/price/quantity already resting in the recorded book "
                     "BEFORE its first chain-proven execution?"),
    "prediction_rule": ("from L5_PASSIVE_SURFACE_SPEC_V1: a ladder at "
                        "ask-1/-2/-3 ticks on BOTH outcomes, base lot 100"),
    "frozen": "the procedure does not change between windows",
}


def predict(joint: Dict[str, dict]) -> List[dict]:
    """The INFERRED_SURFACE for this instant. Emitted before the next L5
    action is known, and never revised."""
    out = []
    for oc, b in joint.items():
        bids, asks = ladder(b, "bid"), ladder(b, "ask")
        bb = max((p for p, _ in bids), default=None)
        aa = min((p for p, _ in asks), default=None)
        if aa is None:
            continue
        for k in SPEC["ladder_ticks"]:
            px = round(aa - k * TICK, 4)
            if px < 0.01:
                continue
            out.append({"attribution": INFERRED, "outcome": oc, "side": "BUY",
                        "price": px, "ticks_behind_ask": k,
                        "qty_base_lot": 100.0,
                        "displayed_size_now": size_at(b, "bid", px),
                        "touch": {"bid": bb, "ask": aa}})
    return out


def run_window(start: int, out_dir: str, *, lead_s: float, follow_s: float
               ) -> Optional[str]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    slug = "btc-updown-15m-%d" % start
    path = d / ("window_%d.jsonl" % start)
    fh = open(path, "a", encoding="utf-8")

    def emit(row: dict) -> None:
        row.setdefault("label", LABEL)
        row.setdefault("slug", slug)
        row.setdefault("L5_OBSERVATION_CLOCK", time.time())
        fh.write(json.dumps(row, default=str) + "\n")
        fh.flush()

    end = start + DUR
    emit({"kind": "preregistration", "window_start": start, "window_end": end,
          "procedure": PROCEDURE, "spec": SPEC,
          "attribution_classes": [OBSERVED, COMPATIBLE, INFERRED],
          "note": ("written BEFORE the window opens. Every prediction row "
                   "below precedes, in file order, the executions it is "
                   "scored against.")})

    m = None
    deadline = end + follow_s
    t_books = t_prints = t_act = t_pred = 0.0
    seen_fills: set = set()
    prints_seen: set = set()
    receipts: Dict[str, Any] = {}
    inv = {"Up": 0.0, "Down": 0.0}
    n_pred = n_books = n_parent = 0

    while time.time() < deadline:
        now = time.time()
        if m is None:
            m = outcome_tokens(slug)
            if m:
                emit({"kind": "market_resolved", "market": m})
            else:
                time.sleep(2.0)
                continue

        # ---- books, both outcomes, one row
        if now - t_books >= PROCEDURE["book_interval_s"]:
            t_books = now
            joint = {}
            for oc, tok in (m.get("token_by_outcome") or {}).items():
                b = V.pm_book(str(tok))
                if b:
                    joint[oc] = b
            if joint:
                n_books += 1
                emit({"kind": "book_full_joint",
                      "tte_s": round(end - now, 1),
                      "pre_window": now < start,
                      "outcomes": {oc: {
                          "token": str((m["token_by_outcome"] or {}).get(oc)),
                          "MARKET_STATE_CLOCK": b.get("source_ts"),
                          "bids": ladder(b, "bid"), "asks": ladder(b, "ask"),
                          "summary": summarize(b)} for oc, b in joint.items()}})
                # ---- the frozen prediction, BEFORE any later action is known
                if now - t_pred >= PROCEDURE["prediction_interval_s"]:
                    t_pred = now
                    n_pred += 1
                    emit({"kind": "surface_prediction",
                          "decision_time": now, "tte_s": round(end - now, 1),
                          "pre_window": now < start,
                          "predictions": predict(joint),
                          "note": "FROZEN at this timestamp; never revised."})

        # ---- public prints
        if now - t_prints >= PROCEDURE["prints_interval_s"]:
            t_prints = now
            tr = V.pm_trades(m.get("condition_id")) or []
            fresh = []
            for x in tr:
                k = "%s|%s|%s|%s" % (x["t"], x["price"], x["size"],
                                     x.get("asset"))
                if k not in prints_seen:
                    prints_seen.add(k)
                    fresh.append({"t": float(x["t"]), "price": str(x["price"]),
                                  "size": str(x["size"]), "side": x.get("side"),
                                  "asset": x.get("asset")})
            if fresh:
                emit({"kind": "print_tape", "n": len(fresh), "prints": fresh})

        # ---- L5 activity and the chain behind it
        if now - t_act >= PROCEDURE["activity_interval_s"]:
            t_act = now
            fills = OB.activity_fills(limit=200)
            if fills is None:
                emit({"kind": "activity_read_failed"})
            else:
                for tx, e in fills.items():
                    for i, f in enumerate(e["fills"]):
                        if str(f.get("slug")) != slug:
                            continue
                        key = (tx, i, f.get("price"), f.get("size"),
                               f.get("outcome"))
                        if key in seen_fills:
                            continue
                        seen_fills.add(key)
                        oc = "Up" if str(f.get("outcome")) == "Up" else "Down"
                        try:
                            q, px = float(f.get("size") or 0), float(f.get("price") or 0)
                        except (TypeError, ValueError):
                            q = px = 0.0
                        before = dict(inv)
                        inv[oc] += q
                        if tx not in receipts:
                            rc = OF.rpc("eth_getTransactionReceipt", [tx])
                            receipts[tx] = OF.l5_orders_in(rc) if rc else []
                        n_parent += 1
                        emit({"kind": "l5_parent_execution",
                              "attribution": OBSERVED,
                              "L5_EVENT_CLOCK": float(f.get("ts") or 0),
                              "public_feed_clock": now,
                              "publication_lag_s": round(
                                  now - float(f.get("ts") or 0), 2),
                              "tx": tx, "outcome": oc, "price": px, "qty": q,
                              "parents": [{
                                  "order_hash": x["order_hash"],
                                  "maker_asset_id": str(x["maker_asset_id"]),
                                  "taker_asset_id": str(x["taker_asset_id"]),
                                  "maker_amount_filled": str(x["maker_amount_filled"]),
                                  "taker_amount_filled": str(x["taker_amount_filled"]),
                                  "fee": str(x["fee"])} for x in receipts[tx]],
                              "receipt_available": bool(receipts[tx]),
                              "inventory_before": before,
                              "inventory_after": dict(inv),
                              "residual_after": round(inv["Up"] - inv["Down"], 6)})
        time.sleep(0.2)

    emit({"kind": "window_end", "window_start": start,
          "books": n_books, "predictions": n_pred,
          "parent_executions": n_parent,
          "final_inventory": dict(inv)})
    fh.close()
    return str(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--windows", type=int, default=3)
    ap.add_argument("--lead-s", type=float, default=PROCEDURE["lead_s"])
    ap.add_argument("--follow-s", type=float, default=PROCEDURE["follow_s"])
    ap.add_argument("--out-dir", default="data/l5_window_forensics")
    a = ap.parse_args()
    done = 0
    while done < a.windows:
        now = int(time.time())
        nxt = ((now // DUR) + 1) * DUR
        wait = nxt - a.lead_s - now
        if wait > 0:
            print("waiting %.0fs for window %d (capture starts %ds early)"
                  % (wait, nxt, a.lead_s), flush=True)
            time.sleep(wait)
        p = run_window(nxt, a.out_dir, lead_s=a.lead_s, follow_s=a.follow_s)
        print("WINDOW_DONE %s -> %s" % (nxt, p), flush=True)
        done += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
