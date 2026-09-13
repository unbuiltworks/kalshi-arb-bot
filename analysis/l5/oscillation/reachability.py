"""P0/P1/P3 (our side): did the complement ever become reachable?

    python3 analysis/l5/oscillation/reachability.py

For every real production FIRST FILL, reconstruct the rest of that market
from the book observations the engine itself recorded on its orders, and
classify the market R1/R2/R3/R4. Read-only; nothing is submitted.

THE OPPOSITE-ASK SERIES is sparse by construction -- it exists only where
the engine had an order resting -- so "minimum observed complement ask" is
a floor over WHAT WE SAW, never over what the market did. Every number
below carries that limitation, and a market with too few observations is
R4 rather than being pushed into R3.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_oscillation as osc                          # noqa: E402

HERE = Path(__file__).resolve().parent
SESSIONS = ROOT / "analysis/l5/streak_shadow/live_sessions.jsonl.gz"
OUT = HERE / "reachability.json"

REALIZED = {"aec-setkameua-sapole-salvas-2026-09-13": D("-1.0779"),
            "aec-nfl-buf-hou-2026-09-13": D("-2.5896"),
            "aec-mlb-lad-mia-2026-09-13": D("6.0034")}
PAIR_TARGETS = (D("0.97"), D("0.98"), D("0.99"), D("1.00"))
MIN_OBS = 3                     # below this the market cannot be classified


def _ep(t) -> float:
    try:
        return datetime.fromisoformat(str(t)).timestamp()
    except Exception:                                        # noqa: BLE001
        return 0.0


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _dd(x, d=None):
    try:
        return D(str(x))
    except Exception:                                        # noqa: BLE001
        return d


def load():
    chunks = [json.loads(l) for l in gzip.open(SESSIONS, "rt")]
    by_market = defaultdict(lambda: {"decisions": [], "orders": []})
    for c in chunks:
        ds = c.get("decisions") or []
        if not ds:
            continue
        slug = str(ds[0].get("slug"))
        by_market[slug]["decisions"].extend(ds)
        ids = [d.get("decision_id") for d in ds]
        idx = {d.get("decision_id"): d for d in ds}
        for o in (c.get("orders") or []):
            parts = str(o.get("stage") or "").split("_")
            k = next((int(p) for p in parts if p.isdigit()), None)
            d = idx.get(ids[k - 1]) if k and 1 <= k <= len(ids) else None
            by_market[slug]["orders"].append(
                {**o, "t": _ep((d or {}).get("t_utc")),
                 "t_utc": (d or {}).get("t_utc")})
    for m in by_market.values():
        m["decisions"].sort(key=lambda d: (str(d.get("t_utc") or ""),
                                           int(d.get("decision_idx") or 0)))
        m["orders"].sort(key=lambda o: o["t"])
    return dict(by_market)


def opposite_asks(orders, after_t):
    """Every contemporaneous opposite-side ask the engine recorded after a
    moment, with its timestamp. Three sources, all first-party, kept
    separate from each other only in provenance -- the minimum is taken
    over their union because each is an ask the engine actually saw."""
    out = []
    for o in orders:
        if o["t"] < after_t:
            continue
        for key, src in (("fresh_opp_ask", "fresh"),
                         ("l5_touch_px", "l5_touch")):
            v = _f(o.get(key))
            if v is not None:
                out.append((o["t"], v, src))
        rb = o.get("rest_book") or {}
        v = _f(rb.get("min_opp_ask"))
        if v is not None:
            out.append((o["t"], v, "rest_book"))
        rec = o.get("recorder_book") or {}
        v = _f(rec.get("opp_ask_authenticated"))
        if v is not None:
            out.append((o["t"], v, "recorder_authenticated"))
    return sorted(out)


def main() -> int:
    markets = load()
    rows = []
    for slug, m in markets.items():
        ds, orders = m["decisions"], m["orders"]
        # the FIRST FILL: the first decision whose inventory_before is
        # non-zero marks it, and the fill itself is the delta before it
        first = None
        prev = None
        for d in ds:
            inv = d.get("inventory_before") or {}
            up, dn = _f(inv.get("up"), 0.0), _f(inv.get("dn"), 0.0)
            if prev is not None and (up + dn) > 0 and (prev[0] + prev[1]) == 0:
                side = "up" if up > 0 else "dn"
                qty = up if side == "up" else dn
                first = {"d": d, "side": side, "qty": qty,
                         "px": _f(inv.get("spent"), 0.0) / qty if qty else None}
                break
            prev = (up, dn)
        if first is None:
            continue
        d0, t0 = first["d"], _ep(first["d"].get("t_utc"))
        held = first["side"]
        held_avg = _dd(first["px"])
        # book AT the first fill: the earliest order observation at/after t0
        obs = opposite_asks(orders, t0)
        at_fill = next((o for o in orders if o["t"] >= t0), None)
        rec = (at_fill or {}).get("recorder_book") or {}
        yes_bid, yes_ask = _f(rec.get("yes_bid")), _f(rec.get("yes_ask"))
        opp_ask0 = _f((at_fill or {}).get("fresh_opp_ask"))
        spread = (yes_ask - yes_bid) if (yes_ask and yes_bid) else None
        pair_at_ask = (float(held_avg) + opp_ask0) if (held_avg is not None
                                                       and opp_ask0) else None
        # the rest of the market
        min_ask = min((v for _, v, _ in obs), default=None)
        min_pair = ((float(held_avg) + min_ask)
                    if (held_avg is not None and min_ask is not None) else None)
        t_to = {}
        for tgt in PAIR_TARGETS:
            hit = next((t for t, v, _ in obs
                        if held_avg is not None
                        and (held_avg + _dd(v)) <= tgt), None)
            t_to[str(tgt)] = round(hit - t0, 1) if hit else None
        # did our own maker COMPLETE price ever get reached?
        completes = [o for o in orders if o.get("relation") == "complete"
                     and o["t"] >= t0]
        reached = any(
            _f((o.get("rest_book") or {}).get("max_touch"), -1)
            >= _f(o.get("px"), 1e9) for o in completes)
        opp_filled = any(o.get("relation") == "complete"
                         and _f(o.get("filled_qty"), 0.0) > 0 for o in orders)
        last = ds[-1].get("inventory_before") or {}
        up, dn = _f(last.get("up"), 0.0), _f(last.get("dn"), 0.0)
        tot = up + dn
        matched_frac = (2 * min(up, dn) / tot) if tot else 0.0
        realized = REALIZED.get(slug)

        if len(obs) < MIN_OBS or held_avg is None:
            cls = "R4_insufficient_evidence"
        elif pair_at_ask is not None and pair_at_ask <= 1.0:
            cls = "R1_complement_immediately_reachable"
        elif min_pair is not None and min_pair <= 1.0:
            cls = "R2_complement_became_reachable_later"
        else:
            cls = "R3_complement_never_reachable"

        rows.append({
            "market": slug,
            "first_fill_t_utc": d0.get("t_utc"),
            "state_at_first_fill": d0.get("state"),
            "tte_bucket": str(d0.get("state") or "||").split("|")[1],
            "held_side": held, "first_fill_price": str(held_avg),
            "first_fill_qty": first["qty"],
            "yes_bid_at_fill": yes_bid, "yes_ask_at_fill": yes_ask,
            "spread_at_fill": spread,
            "opposite_ask_at_fill": opp_ask0,
            "pair_at_opposite_ask": (round(pair_at_ask, 4)
                                     if pair_at_ask else None),
            "distance_to_par": (round(pair_at_ask - 1.0, 4)
                                if pair_at_ask else None),
            "book_observations_after_first_fill": len(obs),
            "min_observed_complement_ask": min_ask,
            "min_observed_pair_cost": (round(min_pair, 4)
                                       if min_pair else None),
            "seconds_until_pair_at_or_below": t_to,
            "our_maker_complete_price_ever_reached": reached,
            "any_opposite_side_fill": opp_filled,
            "matched_fraction_at_settlement": round(matched_frac, 4),
            "terminal_residual": round(abs(up - dn), 4),
            "confirmed_realized_usd": str(realized) if realized else "UNSETTLED",
            "class": cls,
        })

    rows.sort(key=lambda r: r["market"])
    counts = defaultdict(int)
    for r in rows:
        counts[r["class"]] += 1

    # --- P1: univariate only, with sample counts on every line
    def uni(field, pred=lambda r: r["class"].startswith(("R1", "R2"))):
        good, bad = [], []
        for r in rows:
            v = r.get(field)
            if v is None or r["class"].startswith("R4"):
                continue
            (good if pred(r) else bad).append(float(v))
        return {"n_reachable": len(good), "n_never": len(bad),
                "reachable": osc.distribution(good),
                "never": osc.distribution(bad)}

    p1 = {f: uni(f) for f in
          ("first_fill_price", "spread_at_fill", "opposite_ask_at_fill",
           "pair_at_opposite_ask", "distance_to_par", "first_fill_qty",
           "min_observed_complement_ask")}

    doc = {
        "labels": "read-only reconstruction from the engine's own recorded "
                  "book observations; the ask series is SPARSE (it exists "
                  "only where an order was resting), so every minimum is a "
                  "floor over what we SAW",
        "corpus": {"markets_with_a_first_fill": len(rows),
                   "classes": dict(counts)},
        "rows": rows,
        "P1_univariate": p1,
    }
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
    print(json.dumps(doc["corpus"], indent=1))
    for r in rows:
        print(f"{r['market']:<42} {r['class']:<40} pair@ask="
              f"{r['pair_at_opposite_ask']} min_pair={r['min_observed_pair_cost']} "
              f"obs={r['book_observations_after_first_fill']} "
              f"opp_fill={r['any_opposite_side_fill']} "
              f"matched={r['matched_fraction_at_settlement']} "
              f"realized={r['confirmed_realized_usd']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
