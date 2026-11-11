"""Run the pre-registered bounded complement-cross shadow.

    python3 analysis/l5/cross_shadow/run.py

Spec: PREREGISTRATION.md, frozen at commit 135f5d79 before this file
existed. SHADOW ONLY -- no order is placed, production is untouched.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import l5_cross_shadow as cs                         # noqa: E402
from src import l5_oscillation as osc                         # noqa: E402

HERE = Path(__file__).resolve().parent
SESSIONS = ROOT / "analysis/l5/streak_shadow/live_sessions.jsonl.gz"
DEPTH = HERE / "recovered_depth.jsonl"       # optional; absent => no depth
OUT = HERE / "cross_shadow.json"
PREREG_SHA = "7e31c447eaa1028c546787b23bdc16a7925478d9d9e0e7d11635ebb7caea3dd0"

REALIZED = {"aec-setkameua-sapole-salvas-2026-09-13": D("-1.0779"),
            "aec-nfl-buf-hou-2026-09-13": D("-2.5896"),
            "aec-mlb-lad-mia-2026-09-13": D("6.0034")}


def _ep(t):
    try:
        return datetime.fromisoformat(str(t)).timestamp()
    except Exception:                                        # noqa: BLE001
        return None


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _d(x, d=None):
    try:
        return D(str(x))
    except Exception:                                        # noqa: BLE001
        return d


def load_depth():
    """Depth recovered from the recorder's own status log, if a harvest
    exists. Absent by default -- the probe does not persist depth on its
    order rows, which is the whole reason this file may be empty."""
    out = defaultdict(list)
    if not DEPTH.exists():
        return dict(out), 0
    n = 0
    for line in DEPTH.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        t = _ep(r.get("t_utc"))
        if t is None:
            continue
        out[str(r.get("slug"))].append((t, _d(r.get("ask_qty")),
                                        _d(r.get("ask"))))
        n += 1
    for k in out:
        out[k].sort()
    return dict(out), n


def depth_at(depths, slug, t, window_s=cs.MAX_QUOTE_AGE_S):
    """Observed ask depth contemporaneous with a moment, or None.

    The window is the pre-registered 5 s. A nearby-but-older observation is
    NOT contemporaneous and returns None, which becomes
    UNKNOWN_EXECUTABILITY rather than a fill.
    """
    best = None
    for ts, qty, _ask in depths.get(slug, ()):
        if abs(ts - t) <= window_s and qty is not None:
            if best is None or abs(ts - t) < best[0]:
                best = (abs(ts - t), qty)
    return best[1] if best else None


def winner_of(slug, ds):
    if slug not in REALIZED:
        return None
    last = ds[-1].get("inventory_before") or {}
    up, dn = _d(last.get("up"), D("0")), _d(last.get("dn"), D("0"))
    spent = _d(last.get("spent"), D("0"))
    r = REALIZED[slug]
    if abs((up - spent) - r) <= D("0.01"):
        return "up"
    if abs((dn - spent) - r) <= D("0.01"):
        return "dn"
    return None


def main() -> int:
    depths, n_depth = load_depth()
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
            by_market[slug]["orders"].append({**o, "d": d})
    for m in by_market.values():
        m["decisions"].sort(key=lambda d: (str(d.get("t_utc") or ""),
                                           int(d.get("decision_idx") or 0)))

    # ---- every REAL production COMPLETE decision, with its book
    cands = []
    for slug, m in by_market.items():
        w = winner_of(slug, m["decisions"])
        for o in m["orders"]:
            if o.get("relation") != "complete":
                continue
            d = o.get("d") or {}
            inv = d.get("inventory_before") or {}
            up, dn = _f(inv.get("up"), 0.0), _f(inv.get("dn"), 0.0)
            held = "up" if up > dn else "dn"
            avg = _d(inv.get("avg_up") if held == "up" else inv.get("avg_dn"))
            rec = o.get("recorder_book") or {}
            age = _f(rec.get("authenticated_age_s"))
            ask = _d(rec.get("opp_ask_authenticated"))
            t = _ep(d.get("t_utc"))
            qty = _d(o.get("qty"), D("0"))
            cands.append({
                "market": slug, "t_utc": d.get("t_utc"), "t": t,
                "held_side": held, "held_residual": D(str(up - dn)),
                "held_avg_cost": avg, "want_qty": qty,
                "maker_px": _d(o.get("px")),
                "quote_age_s": age, "ask": ask,
                "depth": depth_at(depths, slug, t) if t else None,
                "pair_cost": (avg + ask) if (avg is not None
                                             and ask is not None) else None,
                "actually_filled": _f(o.get("filled_qty"), 0.0) > 0,
                "winner": w,
                "evidence_ok": bool(avg is not None and qty > 0),
            })

    # ---- THE MATRIX: 4 thresholds x 4 timings, all reported
    grid = {}
    for thr in cs.THRESHOLDS:
        for tt in cs.TIMINGS:
            label = f"thr={thr}|T{tt}"
            outcomes, reasons, econ = Counter(), Counter(), []
            for c in cands:
                # timing arm: T>0 means the maker attempt is allowed first,
                # then the cross must STILL qualify on a book observation
                # that far ahead. Where no such observation exists the row
                # is UNKNOWN, per the pre-registration -- not a cross.
                if tt > 0:
                    later = depth_at(depths, c["market"],
                                     (c["t"] or 0) + tt) if c["t"] else None
                    if later is None:
                        outcomes[cs.UNKNOWN_EXEC] += 1
                        reasons[f"no_book_observation_at_T{tt}"] += 1
                        continue
                    if c["actually_filled"]:
                        outcomes[cs.REFUSED] += 1
                        reasons["maker_filled_within_the_wait"] += 1
                        continue
                    depth = later
                else:
                    depth = c["depth"]
                st, why = cs.qualify(
                    held_residual=c["held_residual"],
                    model_relation="complete",
                    quote_age_s=c["quote_age_s"], depth=depth,
                    want_qty=c["want_qty"], pair_cost=c["pair_cost"],
                    threshold=thr, risk_gates_pass=True, horizon_pass=True,
                    evidence_ok=c["evidence_ok"])
                outcomes[st] += 1
                reasons[why] += 1
                if st == cs.SUPPORTED:
                    econ.append(cs.cross_economics(
                        contracts=c["want_qty"], taker_price=c["ask"],
                        maker_price=c["maker_px"],
                        held_avg_cost=c["held_avg_cost"],
                        winner=c["winner"], held_side=c["held_side"]))
            grid[label] = {
                "opportunities": len(cands),
                "outcomes": dict(outcomes),
                "first_stop_reasons": dict(reasons.most_common()),
                "supported_fills": outcomes[cs.SUPPORTED],
                "markets_with_a_supported_fill": len(
                    {e["contracts"] for e in econ}) and len(econ),
                "net_locked_pair_pnl": str(sum(
                    (D(e["net_locked_pair_pnl"]) for e in econ), D("0"))),
                "confirmed_fees": str(sum(
                    (D(e["confirmed_taker_fee"]) for e in econ), D("0"))),
                "fee_status": cs.FEE_STATUS_CONFIRMED,
            }

    # ---- the break-even table, which needs no depth at all
    breakeven = []
    for q in (D("1.00"), D("1.15"), D("1.72"), D("1.92"), D("5"), D("10"),
              D("25"), D("100")):
        for p in (D("0.20"), D("0.35"), D("0.50"), D("0.65"), D("0.80")):
            fee = cs.taker_fee(q, p)
            reb = cs.maker_rebate(q, p)
            # net > 0 needs q*(1-pair) > fee + rebate_forgone
            max_pair = D("1") - ((fee + reb) / q)
            breakeven.append({
                "contracts": str(q), "taker_price": str(p),
                "confirmed_fee": str(fee), "maker_rebate_forgone": str(reb),
                "cost_per_contract": str(((fee + reb) / q).quantize(
                    D("0.0001"))),
                "max_pair_cost_for_a_net_positive_cross": str(
                    max_pair.quantize(D("0.0001"))),
                "arms_that_can_ever_be_net_positive": [
                    str(t) for t in cs.THRESHOLDS if t <= max_pair],
            })

    doc = {
        "labels": "SHADOW ONLY; no order placed; production untouched",
        "preregistration_sha256": PREREG_SHA,
        "fee_status": cs.FEE_STATUS_CONFIRMED,
        "fee_source": cs.FEE_SOURCE,
        "depth_evidence": {
            "orders_in_corpus_carrying_observed_ask_depth": 0,
            "depth_observations_recovered_from_recorder_logs": n_depth,
            "note": "the probe's recorder_book capture persists yes_bid, "
                    "yes_ask, opp_ask_authenticated, authenticated_age_s and "
                    "both_sides_served -- and DROPS the bid_qty/ask_qty the "
                    "recorder already computes and serves. Depth existed at "
                    "decision time and was not written down.",
        },
        "corpus": {"complete_decisions": len(cands),
                   "markets": len({c["market"] for c in cands})},
        "matrix": grid,
        "breakeven_no_depth_required": breakeven,
    }
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
    print(json.dumps(doc["depth_evidence"], indent=1))
    print(json.dumps(doc["corpus"], indent=1))
    for k, v in grid.items():
        print(f"{k:<18} supported={v['supported_fills']:<4} "
              f"{dict(v['outcomes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
