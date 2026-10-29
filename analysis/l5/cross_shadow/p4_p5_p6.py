"""P4 BUF-HOU forensic, P5 adverse selection, P6 trajectory fidelity.

    python3 analysis/l5/cross_shadow/p4_p5_p6.py

SHADOW ONLY. With zero depth-supported crosses, P5 is run over the rows
that passed EVERY pre-registered condition except depth -- labelled
DEPTH_UNKNOWN throughout, never as fills. That still answers the question
the owner asked (does paying the ask buy certainty or buy adverse
selection) without asserting an execution that cannot be supported.
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
OUT = HERE / "p4_p5_p6.json"
BUF = "aec-nfl-buf-hou-2026-09-13"
HORIZONS = (10, 30, 60)


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


def load():
    chunks = [json.loads(l) for l in gzip.open(SESSIONS, "rt")]
    by = defaultdict(lambda: {"decisions": [], "orders": []})
    for c in chunks:
        ds = c.get("decisions") or []
        if not ds:
            continue
        slug = str(ds[0].get("slug"))
        by[slug]["decisions"].extend(ds)
        ids = [d.get("decision_id") for d in ds]
        idx = {d.get("decision_id"): d for d in ds}
        for o in (c.get("orders") or []):
            parts = str(o.get("stage") or "").split("_")
            k = next((int(p) for p in parts if p.isdigit()), None)
            d = idx.get(ids[k - 1]) if k and 1 <= k <= len(ids) else None
            by[slug]["orders"].append({**o, "d": d, "t": _ep((d or {}).get("t_utc"))})
    for m in by.values():
        m["decisions"].sort(key=lambda d: (str(d.get("t_utc") or ""),
                                           int(d.get("decision_idx") or 0)))
        m["orders"].sort(key=lambda o: o["t"] or 0)
    return dict(by)


def ask_series(orders):
    """Every opposite-ask the engine recorded, with its time. Sparse."""
    out = []
    for o in orders:
        if o["t"] is None:
            continue
        rec = o.get("recorder_book") or {}
        v = _f(rec.get("opp_ask_authenticated"))
        if v is not None:
            out.append((o["t"], v, _f(rec.get("authenticated_age_s"))))
        v = _f(o.get("fresh_opp_ask"))
        if v is not None:
            out.append((o["t"], v, None))
    # sort on time only: the third element is an optional age and a
    # tuple sort would compare None against a float when two observations
    # share a timestamp
    return sorted(out, key=lambda r: r[0])


def ask_after(series, t, dt, tol=15.0):
    """The ask observed closest to t+dt, or None. No interpolation: an
    unobserved book is UNKNOWN, not a straight line between two points."""
    best = None
    for ts, v, _a in series:
        gap = abs(ts - (t + dt))
        if gap <= tol and (best is None or gap < best[0]):
            best = (gap, v)
    return best[1] if best else None


def main() -> int:
    mk = load()

    # ---------------- P4: BUF-HOU, every COMPLETE attempt
    buf = mk.get(BUF) or {"decisions": [], "orders": []}
    series = ask_series(buf["orders"])
    rows = []
    for o in buf["orders"]:
        if o.get("relation") != "complete":
            continue
        d = o.get("d") or {}
        inv = d.get("inventory_before") or {}
        up, dn = _f(inv.get("up"), 0.0), _f(inv.get("dn"), 0.0)
        held = "up" if up > dn else "dn"
        avg = _d(inv.get("avg_up") if held == "up" else inv.get("avg_dn"))
        rec = o.get("recorder_book") or {}
        rb = o.get("rest_book") or {}
        ask = _d(rec.get("opp_ask_authenticated"))
        maker = _d(o.get("px"))
        pair_maker = (avg + maker) if (avg is not None and maker is not None) else None
        pair_taker = (avg + ask) if (avg is not None and ask is not None) else None
        quals = {}
        for thr in cs.THRESHOLDS:
            st, why = cs.qualify(
                held_residual=D(str(up - dn)), model_relation="complete",
                quote_age_s=_f(rec.get("authenticated_age_s")),
                depth=None, want_qty=_d(o.get("qty"), D("0")),
                pair_cost=pair_taker, threshold=thr,
                risk_gates_pass=True, horizon_pass=True,
                evidence_ok=avg is not None)
            quals[str(thr)] = f"{st}:{why}"
        rows.append({
            "t_utc": d.get("t_utc"), "stage": o.get("stage"),
            "residual": round(up - dn, 4),
            "held_side": held, "held_avg_cost": str(avg),
            "maker_quote": str(maker), "qty": o.get("qty"),
            "opposite_ask": str(ask), "ask_depth": "NOT_RECORDED",
            "pair_at_maker": (str(pair_maker.quantize(D("0.0001")))
                              if pair_maker is not None else None),
            "pair_at_taker": (str(pair_taker.quantize(D("0.0001")))
                              if pair_taker is not None else None),
            "maker_rest_s": o.get("rest_s"),
            "maker_price_touched": (_f(rb.get("max_touch"), -1)
                                    >= _f(o.get("px"), 1e9)),
            "outcome": o.get("outcome"),
            "shadow_qualification_by_threshold": quals,
        })
    n_fresh = sum(1 for r in rows
                  if "observed_ask_depth_not_recorded" in json.dumps(
                      r["shadow_qualification_by_threshold"]))
    p4 = {
        "market": BUF, "confirmed_realized_usd": "-2.5896",
        "complete_attempts": len(rows),
        "attempts_reaching_the_depth_test": n_fresh,
        "attempts_blocked_earlier": len(rows) - n_fresh,
        "note": "a case study, not evidence by itself. No policy in the "
                "pre-registered matrix eliminates this residual, because "
                "every attempt stops at a missing or stale authenticated "
                "quote or at unrecorded depth -- BEFORE any price test.",
        "rows": rows,
    }

    # ---------------- P5: adverse selection on DEPTH_UNKNOWN candidates
    adverse = []
    for slug, m in mk.items():
        s = ask_series(m["orders"])
        for o in m["orders"]:
            if o.get("relation") != "complete" or o["t"] is None:
                continue
            rec = o.get("recorder_book") or {}
            age = _f(rec.get("authenticated_age_s"))
            ask = _f(rec.get("opp_ask_authenticated"))
            if age is None or ask is None or age > cs.MAX_QUOTE_AGE_S:
                continue
            row = {"market": slug, "t_utc": (o.get("d") or {}).get("t_utc"),
                   "label": "DEPTH_UNKNOWN_not_a_fill",
                   "ask_at_decision": ask}
            for h in HORIZONS:
                v = ask_after(s, o["t"], h)
                row[f"ask_at_plus_{h}s"] = v
                row[f"delta_plus_{h}s"] = (round(v - ask, 4)
                                           if v is not None else None)
            adverse.append(row)
    p5 = {"candidates_fresh_quote_depth_unknown": len(adverse),
          "markets": len({a["market"] for a in adverse}),
          "rule": "no interpolation: an unobserved book is UNKNOWN, never a "
                  "line between two points",
          "by_horizon": {}}
    for h in HORIZONS:
        deltas = [a[f"delta_plus_{h}s"] for a in adverse
                  if a[f"delta_plus_{h}s"] is not None]
        p5["by_horizon"][f"+{h}s"] = {
            "observed": len(deltas), "unobserved": len(adverse) - len(deltas),
            "distribution_of_ask_change": osc.distribution(deltas),
            "share_ask_got_cheaper": (round(sum(1 for d in deltas if d < 0)
                                            / len(deltas), 4)
                                      if deltas else None),
            "share_ask_got_dearer": (round(sum(1 for d in deltas if d > 0)
                                           / len(deltas), 4)
                                     if deltas else None)}
    p5["rows"] = adverse

    # ---------------- P6: trajectory, unchanged by a study with no fills
    p6 = {
        "claim_not_made": "no claim is made that L5 crosses; that is "
                          "unobservable",
        "shadow_fills_available_to_change_the_trajectory": 0,
        "consequence": "with zero depth-supported crosses the shadow "
                       "trajectory is IDENTICAL to production's, so the "
                       "P6 comparison has nothing to measure and reports "
                       "UNKNOWN rather than 'no change'",
        "l5_reference": {"median_switch_interval_s": 9,
                         "median_switch_pair": 0.99,
                         "share_switch_pair_above_1_00": 0.4716,
                         "median_window_matched_fraction": 0.625},
    }

    doc = {"labels": "SHADOW ONLY; no order placed; production untouched",
           "preregistration_sha256": (
               "7e31c447eaa1028c546787b23bdc16a7925478d9d9e0e7d11635ebb7"
               "caea3dd0"),
           "P4_buf_hou": p4, "P5_adverse_selection": p5,
           "P6_trajectory_fidelity": p6}
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
    print(json.dumps({k: v for k, v in p4.items() if k != "rows"}, indent=1))
    print(json.dumps({k: v for k, v in p5.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
