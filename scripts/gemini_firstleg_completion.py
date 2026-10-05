"""P0 FIRST-LEG -> COMPLETION STUDY. Research only. No orders, no policy.

TARGET VARIABLE is not settlement direction and not first-leg P&L. It is:

    does a fee-inclusive sub-$1 complement become EXECUTABLE later in the
    same window, after measured reaction latency and before the series ends?

CAUSAL CANDIDATE GENERATION: every 1 Hz observation from T+15 onward where
the side is executable at q=1 generates a candidate first leg, for BOTH
sides independently. The frozen underdog entry is one row among them, not
the design. No per-window best entry is chosen and no threshold is swept
against P&L.

LEAKAGE: candidates inside one window are NOT independent trials. Every
rate is reported with its independent-window count, and uncertainty is a
window-level (cluster) bootstrap, never an observation-level one.

SPLIT: chronological. Discovery = earliest 60% of windows by expiry; the
remainder is SEALED and not read by this script at all.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import statistics
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

sys.path.insert(0, "scripts")
from gemini_complement_forensic import (  # noqa: E402
    REACTION_LATENCY_S, SIZES, fee, load, vwap)

LABEL = "GEMINI_FIRSTLEG_COMPLETION_V1"
MIN_T_PLUS_S = 15.0
#: a candidate must leave real room for a complement to arrive at all
MIN_REMAINING_S = 30.0
DISCOVERY_FRACTION = 0.60
BOOT = 4000
RNG = random.Random(20261005)


def lrrr_series(rows: List[Dict[str, Any]]) -> List[tuple]:
    out = []
    for r in rows:
        if r.get("kind") != "index_price":
            continue
        raw = r.get("raw") or {}
        if raw.get("I") != "LRRR_XBT":
            continue
        v = r.get("parsed_value")
        if v in (None, ""):
            continue
        out.append((int(r["t_ns"]) / 1e9, D(str(v))))
    out.sort(key=lambda x: x[0])
    return out


def at_or_before(ser: List[tuple], t: float) -> Optional[D]:
    best = None
    for ts, v in ser:
        if ts <= t:
            best = v
        else:
            break
    return best


def candidates(m: str, series, t_open: float, strike: Optional[D],
               lrrr: List[tuple], t_end: float) -> List[Dict[str, Any]]:
    """Every causal first-leg state, both sides, no selection."""
    rows: List[Dict[str, Any]] = []
    # pre-index the series for trajectory/oscillation features
    for i, row in enumerate(series):
        t = row["t"]
        if t - t_open < MIN_T_PLUS_S or t_end - t < MIN_REMAINING_S:
            continue
        up, dn = row.get("UP_q1"), row.get("DOWN_q1")
        if up is None or dn is None:
            continue
        pair = up + dn
        under = "UP" if up < dn else "DOWN" if dn < up else None
        # trajectories use only prior observations
        prior30 = [r for r in series[:i + 1] if t - r["t"] <= 30.0]
        pair_then = None
        if prior30 and prior30[0].get("UP_q1") is not None \
                and prior30[0].get("DOWN_q1") is not None:
            pair_then = prior30[0]["UP_q1"] + prior30[0]["DOWN_q1"]
        prior60 = [r for r in series[:i + 1] if t - r["t"] <= 60.0]
        switches = 0
        prev = None
        for r in prior60:
            u, d = r.get("UP_q1"), r.get("DOWN_q1")
            if u is None or d is None:
                continue
            s = "UP" if u < d else "DOWN" if d < u else None
            if s and prev and s != prev:
                switches += 1
            if s:
                prev = s
        ref = at_or_before(lrrr, t)
        ref_30 = at_or_before(lrrr, t - 30.0)
        d_usd = (ref - strike) if (ref is not None and strike is not None) \
            else None
        base = {
            "market": m, "t": t,
            "t_plus_s": round(t - t_open, 3),
            "remaining_s": round(t_end - t, 3),
            "pair_cost_q1": str(pair),
            "spread_proxy": str(pair - D("1")),
            "up_levels": row["up_levels"], "dn_levels": row["dn_levels"],
            "strike": (str(strike) if strike is not None else None),
            "lrrr": (str(ref) if ref is not None else None),
            "d_usd": (str(d_usd) if d_usd is not None else None),
            "d_bps": (str((d_usd / strike * D("10000")).quantize(D("0.01")))
                      if (d_usd is not None and strike) else None),
            "d_sign": (None if d_usd is None
                       else ("+" if d_usd > 0 else "-" if d_usd < 0 else "0")),
            "lrrr_delta_30s_usd": (str(ref - ref_30)
                                   if (ref is not None and ref_30 is not None)
                                   else None),
            "pair_delta_30s": (str(pair - pair_then)
                               if pair_then is not None else None),
            "side_switches_60s": switches,
            "depth_q5_both": bool(row.get("UP_q5") is not None
                                  and row.get("DOWN_q5") is not None),
        }
        for side in ("UP", "DOWN"):
            px = up if side == "UP" else dn
            other = "DOWN" if side == "UP" else "UP"
            c = dict(base)
            c.update({
                "side": side,
                "price": str(px),
                "opposite_price": str(dn if side == "UP" else up),
                "fee": str(fee(px)),
                "is_underdog": (under == side),
                "is_favored": (under is not None and under != side),
                "toward_strike": _toward(side, base["lrrr_delta_30s_usd"]),
            })
            # TARGET + outcome, strictly forward of t + latency
            c.update(_outcome(series, i, px, other, t))
            rows.append(c)
    return rows


def _toward(side: str, delta: Optional[str]) -> Optional[bool]:
    """Is the reference moving in the direction this side needs?"""
    if delta is None:
        return None
    dd = D(delta)
    if dd == 0:
        return None
    return (dd > 0) if side == "UP" else (dd < 0)


def _outcome(series, i: int, px: D, other: str, t: float) -> Dict[str, Any]:
    basis = px + fee(px)
    out: Dict[str, Any] = {"basis": str(basis)}
    for s in SIZES:
        key = "%s_q%d" % (other, s)
        first_t, best = None, None
        for r in series[i + 1:]:
            if r["t"] < t + REACTION_LATENCY_S:
                continue
            c = r.get(key)
            if c is None:
                continue
            lock = basis + c + fee(c)
            if lock < D("1"):
                if first_t is None:
                    first_t = r["t"]
                if best is None or lock < best:
                    best = lock
        out["completes_q%d" % s] = first_t is not None
        out["seconds_to_complement_q%d" % s] = (
            round(first_t - t, 3) if first_t is not None else None)
        out["effective_pair_cost_q%d" % s] = (str(best) if best else None)
    return out


def cluster_ci(by_window: Dict[str, List[bool]]) -> Dict[str, Any]:
    """Window-level bootstrap. Observations within a window move together."""
    keys = list(by_window)
    if not keys:
        return {"rate": None, "ci95": None, "windows": 0}
    flat = [x for k in keys for x in by_window[k]]
    rate = sum(flat) / len(flat)
    boots = []
    for _ in range(BOOT):
        pick = [RNG.choice(keys) for _ in keys]
        f = [x for k in pick for x in by_window[k]]
        if f:
            boots.append(sum(f) / len(f))
    boots.sort()
    lo = boots[int(0.025 * len(boots))]
    hi = boots[int(0.975 * len(boots)) - 1]
    return {"rate": round(100 * rate, 2),
            "ci95": [round(100 * lo, 2), round(100 * hi, 2)],
            "windows": len(keys), "observations": len(flat),
            "windows_with_any": sum(1 for k in keys if any(by_window[k]))}


def table(rows, feature, binner, target="completes_q1"):
    buckets: Dict[str, Dict[str, List[bool]]] = collections.defaultdict(
        lambda: collections.defaultdict(list))
    for r in rows:
        b = binner(r)
        if b is None:
            continue
        buckets[b][r["market"]].append(bool(r[target]))
    return {"feature": feature,
            "bins": {k: cluster_ci(v) for k, v in sorted(buckets.items())}}


def hurdle(rows, size=1):
    """Economic hurdle RECALCULATED for this population of first legs.

    completed lock gain  = 1 - effective pair cost
    failed leg loss      = settlement payout - basis
    hurdle               = L / (G + L)
    """
    g, l = [], []
    for r in rows:
        if r.get(("completes_q%d" % size)):
            ep = r.get("effective_pair_cost_q%d" % size)
            if ep:
                g.append(D("1") - D(ep))
        else:
            sp = r.get("settlement_payout")
            if sp is not None:
                l.append(D(str(sp)) - D(r["basis"]))
    if not g or not l:
        return None
    G = sum(g) / len(g)
    L = -(sum(l) / len(l))
    if G + L <= 0:
        return None
    return {"mean_gain_per_completed_lock": str(G.quantize(D("0.0001"))),
            "mean_loss_per_failed_leg": str((-L).quantize(D("0.0001"))),
            "required_completion_rate_pct": round(
                float(L / (G + L)) * 100, 2),
            "n_completed": len(g), "n_failed": len(l)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes",
                    default="analysis/gemini/btc5m_snap/btc5m_rec_*.jsonl.gz")
    ap.add_argument("--out", default=("analysis/gemini/updown/"
                                      "FIRSTLEG_COMPLETION.json"))
    a = ap.parse_args(argv)

    ser, t0, strike, exp, settle = load(a.tapes)
    import glob as _g, gzip as _gz
    raw = []
    for f in sorted(_g.glob(a.tapes)):
        with _gz.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    raw.append(json.loads(line))
                except Exception:                              # noqa: BLE001
                    continue
    lr = lrrr_series(raw)

    complete = []
    for m, s in sorted(ser.items()):
        tk = m[5:-3]
        if m not in t0 or m not in exp or len(s) < 30:
            continue
        if strike.get(m) is None or settle.get(tk) is None:
            continue
        complete.append((exp[m], m, s))
    complete.sort()
    n_disc = max(1, int(round(DISCOVERY_FRACTION * len(complete))))
    disc, oos = complete[:n_disc], complete[n_disc:]

    rows = []
    for _, m, s in disc:
        tk = m[5:-3]
        st = D(str(strike[m]))
        lrw = [x for x in lr if t0[m] - 120 <= x[0] <= exp[m] + 5]
        cs = candidates(m, s, t0[m], st, lrw, min(exp[m], s[-1]["t"]))
        up_wins = settle[tk] == "yes"
        for c in cs:
            won = (c["side"] == "UP") == up_wins
            c["settlement_payout"] = "1" if won else "0"
        rows.extend(cs)

    frozen = [r for r in rows if r["is_underdog"]]
    res: Dict[str, Any] = {
        "label": LABEL,
        "reaction_latency_s": REACTION_LATENCY_S,
        "split": {"complete_windows": len(complete),
                  "discovery_windows": [m for _, m, _ in disc],
                  "oos_windows_sealed": len(oos),
                  "oos_markets_not_read": [m for _, m, _ in oos]},
        "candidate_population": {
            "observations": len(rows),
            "independent_windows": len({r["market"] for r in rows}),
            "per_window_median": (statistics.median(
                collections.Counter(r["market"] for r in rows).values())
                if rows else None)},
        "base_rate_all_candidates": cluster_ci(_bw(rows)),
        "base_rate_underdog_only": cluster_ci(_bw(frozen)),
        "base_rate_favored_only": cluster_ci(_bw(
            [r for r in rows if r["is_favored"]])),
        "hurdle_all_candidates": hurdle(rows),
        "hurdle_underdog_only": hurdle(frozen),
        "hurdle_favored_only": hurdle([r for r in rows if r["is_favored"]]),
        "relationships": [
            table(rows, "first_leg_price", lambda r: _band(
                r["price"], ["0.20", "0.35", "0.50", "0.65", "0.80"])),
            table(rows, "t_plus_s", lambda r: _band(
                str(r["t_plus_s"]), ["60", "120", "180", "240"])),
            table(rows, "pair_cost_q1", lambda r: _band(
                r["pair_cost_q1"], ["1.00", "1.10", "1.20", "1.35"])),
            table(rows, "abs_d_bps", lambda r: None if r["d_bps"] is None
                  else _band(str(abs(D(r["d_bps"]))), ["1", "3", "6", "12"])),
            table(rows, "d_sign", lambda r: r["d_sign"]),
            table(rows, "underdog_vs_favored",
                  lambda r: "underdog" if r["is_underdog"]
                  else "favored" if r["is_favored"] else None),
            table(rows, "reference_moving_toward_side",
                  lambda r: None if r["toward_strike"] is None
                  else ("toward" if r["toward_strike"] else "away")),
            table(rows, "side_switches_60s", lambda r: _band(
                str(r["side_switches_60s"]), ["1", "2", "4"])),
            table(rows, "pair_delta_30s",
                  lambda r: None if r["pair_delta_30s"] is None
                  else ("widening" if D(r["pair_delta_30s"]) > 0
                        else "tightening" if D(r["pair_delta_30s"]) < 0
                        else "flat")),
        ],
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, sort_keys=True, default=str)
    print(json.dumps(res, indent=2, sort_keys=True, default=str))
    return 0


def _bw(rows, target="completes_q1"):
    bw: Dict[str, List[bool]] = collections.defaultdict(list)
    for r in rows:
        bw[r["market"]].append(bool(r[target]))
    return bw


def _band(v: str, edges: List[str]) -> str:
    x = D(v)
    prev = None
    for e in edges:
        if x < D(e):
            return ("<%s" % e) if prev is None else "%s-%s" % (prev, e)
        prev = e
    return ">=%s" % edges[-1]


if __name__ == "__main__":
    raise SystemExit(main())
