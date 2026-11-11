"""P31 -- MIRROR_RAW under STRICT / PLAUSIBLE / COPYABILITY, and the
critical comparison L5 actual -> MIRROR_RAW -> CLONE_MODEL.

Assembles the hypothetical resting orders from the realtime observer's tape
(each L5 action translated to a PMUS q=1 intent, actionable at OUR RECEIPT
time), follows them against the authenticated trade tape, and reports the
three bounds separately.

The aggressor mapping is applied ONLY if `prove_dir_semantics` returns PROVEN
on rows carrying their own book. Rows recorded before the recorder began
stamping the book are excluded from the proof and are not retro-fitted against
a cached one.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sys
from decimal import Decimal as D
from typing import Any, Dict, List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from analysis.pmus import p30_auth_trade_harvest as H   # noqa: E402
from src import pmus_mirror_lifecycle as M              # noqa: E402

OBS = "data/l5_pmus_realtime/l5_pmus_realtime.jsonl"


def _ep(s: str) -> float:
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def orders_from_observer(path: str = OBS) -> List[dict]:
    """Every realtime L5 action as a hypothetical resting order.

    `actionable_from` is our RECEIPT time. An order cannot rest before we knew
    to place it, and using L5's event time would import exactly the hindsight
    this whole exercise exists to avoid.
    """
    out = []
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") != "l5_action" or r.get("backlog"):
            continue
        cop = r.get("copyability") or {}
        px = cop.get("l5_price") or (r.get("l5") or {}).get("l5_price")
        side = (r.get("l5") or {}).get("pmus_side")
        if px is None or side is None:
            continue
        ws = _ep(r["window_start_utc"])
        bbo = (r.get("pmus") or {}).get("bbo") or {}
        out.append({
            "market": (r.get("pmus") or {}).get("slug"),
            "side": side, "price": str(px),
            "actionable_from": _ep(r["receipt_utc"]),
            "window_end": ws + 900,
            "queue_ahead": (bbo.get("bid_shares") if side == "UP"
                            else bbo.get("ask_shares")),
            "copyable": cop.get("klass") == "MAKER_COPYABLE",
            "l5_relation": (r.get("l5") or {}).get("relation_actual"),
            "window": r["window_start_utc"][11:16],
            "action_key": r.get("action_key"),
        })
    return out


def l5_actual(path: str = OBS) -> Dict[str, Any]:
    rels = collections.Counter()
    sides = collections.Counter()
    n = 0
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") != "l5_action":
            continue
        n += 1
        rels[(r.get("l5") or {}).get("relation_actual")] += 1
        sides[(r.get("l5") or {}).get("pmus_side")] += 1
    return {"actions": n, "relations": dict(rels), "sides": dict(sides),
            "p_extend": (round(rels.get("extend", 0) / n, 3) if n else None),
            "p_extend_historical_btc15m": 0.66,
            "sells": 0}


def clone_actual(path: str = OBS) -> Dict[str, Any]:
    acts = collections.Counter()
    rels = collections.Counter()
    holds = collections.Counter()
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") != "clone_tick":
            continue
        acts[r.get("action")] += 1
        if r.get("action") == "QUOTE":
            rels[r.get("relation")] += 1
        elif r.get("reason"):
            holds[r.get("reason")] += 1
    return {"ticks": dict(acts), "quote_relations": dict(rels),
            "hold_reasons": dict(holds),
            "note": "advances inventory only on fill evidence; the feed "
                    "supplies none, so it stays at OPEN by construction"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    rows = H.load_rows()
    onrow = {s: [r for r in v if r.get("bid_at_trade") is not None]
             for s, v in rows.items()}
    onrow = {s: v for s, v in onrow.items() if v}
    proof = H.prove_dir_semantics(onrow, {})
    norm = H.proven_normalizer(proof)

    orders = orders_from_observer()
    tape_markets = set(rows)
    usable = [o for o in orders if o["market"] in tape_markets]
    classified = M.scan(usable, rows, normalize=norm)
    b = M.bounds(classified)

    # per-window evidence coverage
    per_w = collections.defaultdict(lambda: collections.Counter())
    for c in classified:
        w = next((o["window"] for o in usable
                  if o["market"] == c["market"]
                  and abs(o["actionable_from"] - c["actionable_from"]) < 1e-6),
                 "?")
        per_w[w][c["klass"]] += 1
        per_w[w]["side_" + c["side"]] += 1

    both_sides = 0
    for w, c in per_w.items():
        inter_up = inter_dn = 0
        for cc in classified:
            pass
        both_sides += 0

    rep = {
        "label": "PMUS_MIRROR_BOUNDS_V1",
        "aggressor_proof": proof,
        "tape": {"markets": {s: len(v) for s, v in rows.items()},
                 "rows_with_on_row_book": sum(len(v) for v in onrow.values()),
                 "rows_total": sum(len(v) for v in rows.values())},
        "orders": {"realtime_total": len(orders),
                   "with_authenticated_tape": len(usable),
                   "windows_with_tape": sorted(
                       {o["window"] for o in usable})},
        "per_window_classes": {w: dict(c) for w, c in sorted(per_w.items())},
        "bounds": b,
        "comparison": {
            "ACTUAL_L5": l5_actual(),
            "MIRROR_RAW": {k: b["bounds"][k] for k in M.BOUNDS},
            "CLONE_MODEL": clone_actual(),
            "note": "three separate accounts; never summed",
        },
    }
    if a.out:
        json.dump(rep, open(a.out, "w"), indent=1, default=str)
    print(json.dumps({k: rep[k] for k in
                      ("label", "aggressor_proof", "tape", "orders",
                       "per_window_classes")}, indent=1, default=str))
    print("\n=== BOUNDS ===")
    print(json.dumps(b["classes"], indent=1, default=str))
    for k in M.BOUNDS:
        print("\n--", k)
        print(json.dumps(b["bounds"][k], indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
