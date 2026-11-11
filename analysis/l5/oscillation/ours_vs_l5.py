"""P3 + P5: our trajectory against L5's, and the selective taker test.

    python3 analysis/l5/oscillation/ours_vs_l5.py

P3 compares TRAJECTORY, not action labels: side-switch rate, run length,
matched-fraction and residual through time, size-normalised.

P5 asks one thing, and only after P4 has established what L5 pays: at real
production states where inventory exists, the model wants the opposite
side, and a CONTEMPORANEOUS executable ask exists, would taking the
complement have put us INSIDE L5's empirical switch pair-cost
distribution? Maker/taker stays UNKNOWN for L5; this compares economic
price. Fees stay UNKNOWN unless source-confirmed.
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

from src import l5_oscillation as osc                          # noqa: E402

HERE = Path(__file__).resolve().parent
SESSIONS = ROOT / "analysis/l5/streak_shadow/live_sessions.jsonl.gz"
L5 = HERE / "l5_switches.json"
OUT = HERE / "ours_vs_l5.json"

STRICT_AGE_S = 5.0              # a quote older than this is not contemporaneous
PAIR_TARGETS = ("0.97", "0.98", "0.99", "1.00")
FEES = "unknown_venue_does_not_report_per_fill"


def _f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _ep(t):
    try:
        return datetime.fromisoformat(str(t)).timestamp()
    except Exception:                                        # noqa: BLE001
        return 0.0


def our_fills(ds):
    """Our real fills, from inventory deltas, in order."""
    out, prev = [], None
    for d in ds:
        inv = d.get("inventory_before") or {}
        cur = (_f(inv.get("up"), 0.0), _f(inv.get("dn"), 0.0),
               _f(inv.get("spent"), 0.0))
        if prev is not None:
            du, dd = cur[0] - prev[0], cur[1] - prev[1]
            ds_ = cur[2] - prev[2]
            if du > 1e-9 and dd <= 1e-9:
                out.append({"side": "Up", "qty": du,
                            "px": ds_ / du if du else 0.0, "d": d})
            elif dd > 1e-9 and du <= 1e-9:
                out.append({"side": "Down", "qty": dd,
                            "px": ds_ / dd if dd else 0.0, "d": d})
        prev = cur
    return out


def main() -> int:
    chunks = [json.loads(l) for l in gzip.open(SESSIONS, "rt")]
    by_market = defaultdict(lambda: {"decisions": [], "orders": []})
    for c in chunks:
        dsx = c.get("decisions") or []
        if not dsx:
            continue
        slug = str(dsx[0].get("slug"))
        by_market[slug]["decisions"].extend(dsx)
        ids = [d.get("decision_id") for d in dsx]
        idx = {d.get("decision_id"): d for d in dsx}
        for o in (c.get("orders") or []):
            parts = str(o.get("stage") or "").split("_")
            k = next((int(p) for p in parts if p.isdigit()), None)
            d = idx.get(ids[k - 1]) if k and 1 <= k <= len(ids) else None
            by_market[slug]["orders"].append({**o, "d": d})
    for m in by_market.values():
        m["decisions"].sort(key=lambda d: (str(d.get("t_utc") or ""),
                                           int(d.get("decision_idx") or 0)))

    # ---------------- P3: our trajectory
    ours, our_sw = [], []
    for slug, m in by_market.items():
        fills = our_fills(m["decisions"])
        if not fills:
            continue
        rows = [{"outcome": f["side"], "side": "BUY", "price": f["px"],
                 "quantity": f["qty"],
                 "timestamp": _ep(f["d"].get("t_utc")),
                 "seconds_to_settlement": 0} for f in fills]
        s = osc.window_summary(rows)
        s["market"] = slug
        ours.append(s)
        for x in osc.switches(rows):
            x["market"] = slug
            our_sw.append(x)

    l5 = json.loads(L5.read_text())
    p3 = {
        "note": "L5 windows are 5-15 minute crypto settlements and ours are "
                "multi-hour sports markets, so SWITCHES PER FILL is the "
                "comparable statistic and switches per minute is not. Both "
                "are reported; only the first is compared.",
        "markets_with_fills_ours": len(ours),
        "windows_l5": l5["P3_l5_trajectory"]["windows"],
        "switch_rate_per_fill": {
            "L5": l5["P3_l5_trajectory"]["switch_rate_per_fill"],
            "OURS": osc.distribution([o["switch_rate_per_fill"] for o in ours])},
        "median_run_length": {
            "L5": l5["P3_l5_trajectory"]["median_run_length"],
            "OURS": osc.distribution([o["median_run_length"] for o in ours])},
        "matched_fraction_at_settlement": {
            "L5": l5["P3_l5_trajectory"]["matched_fraction_at_settlement"],
            "OURS": osc.distribution([o["matched_fraction"] for o in ours])},
        "fills_per_window": {
            "L5": l5["P3_l5_trajectory"]["fills_per_window"],
            "OURS": osc.distribution([o["fills"] for o in ours])},
        "windows_with_zero_switches": {
            "L5": f"{l5['P3_l5_trajectory']['windows_with_zero_switches']}"
                  f"/{l5['P3_l5_trajectory']['windows']}",
            "OURS": f"{sum(1 for o in ours if o['switches'] == 0)}/{len(ours)}"},
        "our_switch_pair_prices": {
            "last_pair": osc.distribution([s["last_pair"] for s in our_sw]),
            "avg_pair": osc.distribution(
                [s["avg_pair"] for s in our_sw if s["avg_pair"] is not None]),
            "n_switches": len(our_sw),
        },
        "per_market": ours,
    }

    # ---------------- P5: selective taker, against L5's own distribution
    l5_avg = l5["P4_switch_pair_price"]["avg_pair"]["distribution"]
    l5_bands = l5["P4_switch_pair_price"]["avg_pair"]["bands"]["shares"]
    cands = []
    for slug, m in by_market.items():
        for o in m["orders"]:
            if o.get("relation") != "complete":
                continue
            d = o.get("d") or {}
            inv = d.get("inventory_before") or {}
            up, dn = _f(inv.get("up"), 0.0), _f(inv.get("dn"), 0.0)
            if up + dn <= 0:
                continue
            held = "up" if up > dn else "dn"
            avg = _f(inv.get("avg_up") if held == "up" else inv.get("avg_dn"))
            if avg is None:
                continue
            rec = o.get("recorder_book") or {}
            age = _f(rec.get("authenticated_age_s"))
            ask = _f(rec.get("opp_ask_authenticated"))
            if ask is None or age is None or age > STRICT_AGE_S:
                continue
            cands.append({"market": slug, "t_utc": str(d.get("t_utc")),
                          "held_side": held, "held_avg_cost": round(avg, 4),
                          "executable_ask": ask,
                          "taker_pair_cost": round(avg + ask, 4),
                          "our_maker_px": _f(o.get("px")),
                          "our_maker_pair_cost": round(avg + _f(o.get("px"), 0.0), 4),
                          "depth": (rec.get("opp_ask_qty")
                                    or "unknown_not_recorded"),
                          "quote_age_s": age,
                          "actually_filled": _f(o.get("filled_qty"), 0.0) > 0})
    pairs = [c["taker_pair_cost"] for c in cands]
    p5 = {
        "rule": {
            "contemporaneous": f"authenticated quote age <= {STRICT_AGE_S}s; "
                               "stale quotes are DROPPED, never assumed",
            "fees": FEES,
            "depth": "reported where the venue recorded it; otherwise UNKNOWN "
                     "-- no size is assumed",
            "maker_taker_for_l5": "UNKNOWN; this compares ECONOMIC PRICE only",
        },
        "candidates": len(cands),
        "markets": len({c["market"] for c in cands}),
        "taker_pair_cost": osc.distribution(pairs),
        "bands": osc.band_histogram(pairs),
        "our_maker_pair_cost": osc.distribution(
            [c["our_maker_pair_cost"] for c in cands]),
        "at_or_below": {t: {"n": sum(1 for p in pairs if p <= float(t)),
                            "share": (round(sum(1 for p in pairs
                                                if p <= float(t)) / len(pairs), 4)
                                      if pairs else None)}
                        for t in PAIR_TARGETS},
        "inside_l5_distribution": {
            "l5_avg_pair_median": l5_avg["p50"],
            "l5_share_above_1_00": l5_bands[">1.00"],
            "our_taker_share_at_or_below_l5_median": (
                round(sum(1 for p in pairs if p <= l5_avg["p50"]) / len(pairs), 4)
                if pairs else None),
            "our_taker_share_at_or_below_l5_p25": (
                round(sum(1 for p in pairs if p <= l5_avg["p25"]) / len(pairs), 4)
                if pairs else None),
        },
        "sample": cands[:40],
    }

    doc = {"labels": "read-only; production unchanged",
           "P3_trajectory": p3, "P5_selective_taker": p5}
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
    print(json.dumps({k: v for k, v in p3.items() if k != "per_market"},
                     indent=1))
    print(json.dumps({k: v for k, v in p5.items() if k != "sample"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
