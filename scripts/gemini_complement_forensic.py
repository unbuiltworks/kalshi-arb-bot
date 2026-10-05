"""P0 COMPLEMENT REACH FORENSIC. Research only. No orders, no new policy.

WHY THE TWO NUMBERS DISAGREE, established before any decomposition: they
measure different things.

  * the hindsight 11/17 (median best sequential $0.93) counts MARKETABLE
    availability -- could we have CROSSED to get the complement;
  * the 2.04% `complement_reach_rate` in complement_reachability.json counts
    THEORETICAL PASSIVE QUOTE FILLS -- that artifact's own labels say
    "the arms change only a COMPLETE price or how long a quote rests" and
    "every fill is THEORETICAL". It is a maker-fill rate, not an
    availability rate.

So "2.04% vs 65%" is substantially a definitional gap, and this forensic
measures the thing the engine would actually do: hold a real first leg and
look for a MARKETABLE complement, at 1 Hz, with measured latency.

THE SERIES IS THE 1 Hz BOOK, not the 8-point checkpoint grid. p_snapshot
gives ~300 observations per window, which is what a survival curve at
X = 1, 5, 10, 30, 60, 120s needs; the checkpoint grid cannot resolve it.

THE FIRST LEG IS CAUSAL AND UNOPTIMIZED: the already-frozen underdog rule
(first instant at or after T+15 where both sides fill q=1 and the cheaper
side is at or below its ceiling). No per-window path is chosen, no
threshold is fitted, and the first leg is never moved to make a pair
reachable.

MEASURED LATENCY, NOT ZERO: the reaction budget is this programme's own
observed ack latency -- 134-197ms across the underdog cohort's five live
fills -- so an opportunity must still be executable one observation after
it is detected.
"""
from __future__ import annotations

import argparse
import collections
import glob
import gzip
import json
import statistics
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

LABEL = "GEMINI_COMPLEMENT_REACH_FORENSIC_V1"
SIZES = (1, 5, 10, 25)
TAKER = D("0.07")
FEE_Q = D("0.01")
#: measured p90 ack latency from our own live fills
REACTION_LATENCY_S = 0.197
#: survival horizons, seconds after the first fill
HORIZONS = (1, 5, 10, 30, 60, 120)
#: the frozen underdog first-leg rule's ceiling
FIRST_LEG_CEILING = D("0.60")
MIN_CHECKPOINT_S = 15.0


def fee(p: D, q: D = D("1")) -> D:
    r = TAKER * q * p * (D("1") - p)
    return r.quantize(FEE_Q) if r > 0 else D("0")


def vwap(levels: Any, size: D) -> Optional[D]:
    need, cost, got = size, D("0"), D("0")
    for lv in (levels or []):
        px, q = D(str(lv[0])), D(str(lv[1]))
        take = min(need, q)
        cost += px * take
        got += take
        need -= take
        if need <= 0:
            break
    return (cost / size) if got >= size else None


def load(pattern: str):
    rows = []
    for f in sorted(glob.glob(pattern)):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except Exception:                              # noqa: BLE001
                    continue
    t0: Dict[str, float] = {}
    strike: Dict[str, str] = {}
    exp: Dict[str, float] = {}
    for r in rows:
        if r.get("kind") in ("window", "window_poll") \
                and r.get("duration") == "BTC05M":
            m = r.get("market")
            cp = r.get("availableAt")
            if m and cp and m not in t0:
                from datetime import datetime
                try:
                    t0[m] = datetime.fromisoformat(
                        cp.replace("Z", "+00:00")).timestamp()
                except Exception:                              # noqa: BLE001
                    pass
            if m and r.get("strike") and m not in strike:
                strike[m] = r["strike"]
            if m and r.get("expiry") and m not in exp:
                from datetime import datetime
                try:
                    exp[m] = datetime.fromisoformat(
                        r["expiry"].replace("Z", "+00:00")).timestamp()
                except Exception:                              # noqa: BLE001
                    pass
    # the 1 Hz book series
    ser: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        if r.get("kind") != "p_snapshot":
            continue
        m = r.get("market")
        b, a = r.get("bids") or [], r.get("asks") or []
        if not m or not b or not a:
            continue
        row = {"t": int(r["t_ns"]) / 1e9}
        for s in SIZES:
            row["UP_q%d" % s] = vwap(a, D(s))
            row["DOWN_q%d" % s] = vwap(
                [[str(D("1") - D(str(x[0]))), x[1]] for x in b], D(s))
        row["up_levels"], row["dn_levels"] = len(a), len(b)
        ser[m].append(row)
    for m in ser:
        ser[m].sort(key=lambda x: x["t"])
    # settlement
    settle: Dict[str, str] = {}
    for r in rows:
        if r.get("kind") == "settlement":
            ev = r.get("raw_event") or {}
            for c in (ev.get("contracts") or []):
                if str(c.get("label", "")).lower() == "up":
                    sd = c.get("resolutionSide")
                    if sd in ("yes", "no") and r.get("ticker"):
                        settle[r["ticker"]] = sd
    return ser, t0, strike, exp, settle


def first_leg(series: List[Dict[str, Any]], t_open: float
              ) -> Optional[Dict[str, Any]]:
    """The FROZEN underdog rule, causally. Never moved to help a pair."""
    for row in series:
        if row["t"] - t_open < MIN_CHECKPOINT_S:
            continue
        up, dn = row.get("UP_q1"), row.get("DOWN_q1")
        if up is None or dn is None:
            continue
        side, px = (("UP", up) if up < dn else
                    ("DOWN", dn) if dn < up else (None, None))
        if side is None or px > FIRST_LEG_CEILING:
            continue
        return {"side": side, "price": px, "t": row["t"],
                "tte_s": round(row["t"] - t_open, 3),
                "fee": fee(px)}
    return None


def forensic(m: str, series, t_open: float, settle: Optional[str],
             expiry: Optional[float]) -> Dict[str, Any]:
    fl = first_leg(series, t_open)
    out: Dict[str, Any] = {"market": m, "settlement_up_side": settle}
    if fl is None:
        out["classification"] = "NO_CAUSAL_FIRST_LEG"
        return out
    other = "DOWN" if fl["side"] == "UP" else "UP"
    basis = fl["price"] + fl["fee"]
    out["first_leg"] = {"side": fl["side"], "price": str(fl["price"]),
                        "fee": str(fl["fee"]),
                        "t_plus_s": fl["tte_s"],
                        "inventory_after": {fl["side"]: "1", other: "0"},
                        "directional_residual": "1 " + fl["side"]}
    # walk forward only
    post = [r for r in series if r["t"] >= fl["t"]]
    opps: Dict[int, List[Dict[str, Any]]] = {s: [] for s in SIZES}
    for s in SIZES:
        key = "%s_q%d" % (other, s)
        cur = None
        for r in post:
            c = r.get(key)
            lock = (None if c is None
                    else basis + c * D(s) / D(s) + fee(c))
            ok = lock is not None and lock < D("1")
            if ok and cur is None:
                cur = {"start_t": r["t"],
                       "start_after_fill_s": round(r["t"] - fl["t"], 3),
                       "best_lock": str(lock), "complement": str(c),
                       "levels": r["dn_levels"] if other == "DOWN"
                       else r["up_levels"]}
            elif ok:
                if D(cur["best_lock"]) > lock:
                    cur["best_lock"], cur["complement"] = str(lock), str(c)
            elif cur is not None:
                cur["end_t"] = r["t"]
                cur["duration_s"] = round(r["t"] - cur["start_t"], 3)
                opps[s].append(cur)
                cur = None
        if cur is not None:
            cur["end_t"] = post[-1]["t"]
            cur["duration_s"] = round(post[-1]["t"] - cur["start_t"], 3)
            cur["ran_to_end_of_series"] = True
            opps[s].append(cur)
    out["sub_dollar_opportunities"] = {
        "q%d" % s: {"n": len(v), "windows_total_seconds": round(
            sum(x["duration_s"] for x in v), 1),
            "first_after_fill_s": (v[0]["start_after_fill_s"] if v
                                   else None),
            "best_lock": (min((D(x["best_lock"]) for x in v),
                              default=None)),
            "episodes": v[:6]} for s, v in opps.items()}
    # DECOMPOSITION, on the q1 series
    q1 = opps[1]
    survivable = [o for o in q1 if o["duration_s"] >= REACTION_LATENCY_S]
    if not q1:
        cls = "A_NEVER_REACHED"
    elif not survivable:
        cls = "C_REACHED_TOO_BRIEFLY"
    else:
        cls = "E_REACHED_EXECUTABLE_MISSED"
    # D: price existed at q1 but not at depth
    if q1 and not opps[5]:
        cls = cls + "+D_REACHED_NO_DEPTH_ABOVE_Q1"
    out["classification"] = cls
    out["q1_opportunities_surviving_latency"] = len(survivable)
    out["max_residual_while_waiting"] = "1 " + fl["side"]
    if settle in ("yes", "no"):
        won = ((fl["side"] == "UP" and settle == "yes")
               or (fl["side"] == "DOWN" and settle == "no"))
        out["uncompleted_settlement_pnl"] = str(
            (D("1") if won else D("0")) - fl["price"] - fl["fee"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes",
                    default="analysis/gemini/btc5m_snap/btc5m_rec_*.jsonl.gz")
    ap.add_argument("--out", default=("analysis/gemini/updown/"
                                      "COMPLEMENT_FORENSIC.json"))
    a = ap.parse_args(argv)
    ser, t0, strike, exp, settle = load(a.tapes)
    rows = []
    for m, s in sorted(ser.items()):
        if m not in t0 or len(s) < 30:
            continue
        tk = m[5:-3]
        rows.append(forensic(m, s, t0[m], settle.get(tk), exp.get(m)))
    have = [r for r in rows if r.get("first_leg")]
    # SURVIVAL CURVE, independent windows
    surv = {}
    for X in HORIZONS:
        n = sum(1 for r in have
                if (r["sub_dollar_opportunities"]["q1"]["first_after_fill_s"]
                    is not None)
                and r["sub_dollar_opportunities"]["q1"][
                    "first_after_fill_s"] <= X)
        surv["within_%ds" % X] = {"windows": n, "of": len(have),
                                  "pct": (round(100.0 * n / len(have), 2)
                                          if have else None)}
    ever = sum(1 for r in have
               if r["sub_dollar_opportunities"]["q1"]["n"] > 0)
    firsts = [r["sub_dollar_opportunities"]["q1"]["first_after_fill_s"]
              for r in have
              if r["sub_dollar_opportunities"]["q1"]["first_after_fill_s"]
              is not None]
    rep = {
        "label": LABEL,
        "status": ("FORENSIC. No orders, no new policy, no threshold "
                   "fitting, no per-window path chosen."),
        "why_the_two_numbers_disagree": (
            "the hindsight 11/17 counts MARKETABLE availability; the 2.04% "
            "complement_reach_rate counts THEORETICAL PASSIVE QUOTE fills "
            "-- that artifact's own labels say the arms change only a "
            "COMPLETE price or how long a quote rests and that every fill "
            "is theoretical. A maker-fill rate and an availability rate are "
            "not the same quantity, and most of the apparent gap is that."),
        "series": "1 Hz p_snapshot book, ~300 obs/window",
        "first_leg_rule": ("the FROZEN underdog rule, causal: first instant "
                           "at or after T+15 with both sides filling q1 and "
                           "the cheaper side at or below 0.60. Never moved "
                           "to make a pair reachable."),
        "reaction_latency_s": REACTION_LATENCY_S,
        "latency_basis": ("this programme's own observed ack latency, "
                          "134-197ms across the underdog cohort's five live "
                          "fills"),
        "windows_examined": len(rows),
        "windows_with_a_causal_first_leg": len(have),
        "windows_with_no_causal_first_leg": len(rows) - len(have),
        "windows_where_a_sub_dollar_complement_EVER_became_marketable": ever,
        "ever_pct": (round(100.0 * ever / len(have), 2) if have else None),
        "survival_curve_P_first_sub_dollar_lock_within_X_s": surv,
        "first_lock_time_distribution_s": (
            {"n": len(firsts), "min": min(firsts),
             "median": statistics.median(firsts), "max": max(firsts)}
            if firsts else None),
        "classification_counts": dict(collections.Counter(
            r["classification"] for r in rows)),
        "per_window": rows,
    }
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=2,
              sort_keys=True, default=str)
    print(json.dumps({k: rep[k] for k in (
        "label", "windows_examined", "windows_with_a_causal_first_leg",
        "windows_where_a_sub_dollar_complement_EVER_became_marketable",
        "ever_pct", "survival_curve_P_first_sub_dollar_lock_within_X_s",
        "classification_counts", "first_lock_time_distribution_s")},
        sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
