"""PREREGISTERED BTC5m directional study, anchored to the CHAINED strike.

FROZEN BEFORE ANY PROFITABILITY NUMBER WAS INSPECTED. Its self-sha256 is
printed on every run; if it differs from the one recorded in
PREREGISTRATION_CHAINED_D.md the preregistration is void.

THE CAUSAL STATE. The venue sets each BTC5m strike to the PREVIOUS window's
settlementValue -- confirmed to the cent on four consecutive windows in the
cohort-1 audit, and confirmed again here per window against the tape's own
`strike_type: reference`. So the question each window asks is whether the
published reference ends above where the last window ended, and the natural
state is

    D_t = LRRR_XBT(t) - strike(current window)

with the strike read from the authoritative contract field and NEVER
inferred, and LRRR_XBT read from the contemporaneously published index
stream, taking the last value AT OR BEFORE t. A value older than
MAX_REFERENCE_AGE_S is STALE and the observation is dropped rather than
differenced -- a stale reference is not the reference.

D_t IS NOT Z. The LRRR statistic's internal mechanics are still NOT
IDENTIFIED, so nothing here is normalised by a volatility or a time scale.

THE QUESTION IS NOT WHICH SIDE WINS. It is whether any causal state produces
a settlement probability far enough from the CONTEMPORANEOUS EXECUTABLE
PRICE to clear the venue's actual fee. Both sides are evaluated
symmetrically: UP from the YES ask ladder, DOWN from the derived NO ask
ladder (1 - YES bid), which is what buying NO really consumes here.

WHAT IS FORBIDDEN, and asserted in code:
  * no future information in any feature -- the settlement side, the
    eventual disappearance of a side and any later price are outcomes;
  * no threshold search. The bins and the candidate rules are predeclared
    constants. There is no argmax over cutoffs and no best-cell output;
  * no tuning on cohort 1. Those five windows are excluded by ticker;
  * the last OOS_FRACTION of windows is SEALED and unreadable without
    --unseal-oos, which exists only to test ONE already-frozen candidate;
  * NO_TRADE windows stay in every per-window and per-day denominator.

INCORPORATION LAG is measured separately and is descriptive: does D_t at a
checkpoint predict the executable price CHANGE after that checkpoint? The
predictor is causal; the outcome is a later PRICE, never a settlement. If
the price already reflects D_t there is no lead to trade, whatever the
settlement statistics say.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import glob
import gzip
import hashlib
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

LABEL = "GEMINI_BTC5M_CHAINED_D_STUDY_V1_FROZEN"

# ---------------------------------------------------------------- CONSTANTS
#: fixed early checkpoints, seconds since the window's own t0 (availableAt)
CHECKPOINTS: Tuple[int, ...] = (15, 30, 45, 60, 90, 120, 150, 180)
#: predeclared executable sizes, in contracts
SIZE_LEVELS: Tuple[int, ...] = (1, 5, 10, 25, 50, 100)
#: D_t bins, in BASIS POINTS of the strike. Fixed; not fitted.
D_BINS_BPS: Tuple[float, ...] = (-100.0, -50.0, -25.0, -10.0, -5.0, -2.0,
                                 0.0, 2.0, 5.0, 10.0, 25.0, 50.0, 100.0)
#: executable-price bins
ASK_BINS: Tuple[float, ...] = (0.0, 0.10, 0.20, 0.30, 0.40, 0.50,
                               0.60, 0.70, 0.80, 0.90, 1.0)
#: the venue's PUBLISHED taker rate, reconciled against cohort 1's own
#: charged fees (0.0155/0.0168/0.0161/0.0135 predicted, 0.02 charged at cent
#: rounding) -- so it is a schedule we have now seen applied, not an
#: assumption. Breakeven for a buy at p is p + rate*p*(1-p).
TAKER_RATE = D("0.07")
#: cent rounding, as the venue applied it
FEE_QUANTUM = D("0.01")
#: a reference older than this is STALE and the observation is dropped
MAX_REFERENCE_AGE_S = 10.0
#: the last share of windows is sealed for out-of-sample
OOS_FRACTION = 0.40
#: cohort-1 windows. Probe-contaminated and excluded from every statistic.
EXCLUDED_TICKERS = (
    "BTC05M2610051550", "BTC05M2610051555", "BTC05M2610051600",
    "BTC05M2610051605", "BTC05M2610051610",
)
#: THE PREDECLARED CANDIDATE RULES. A fixed, small, enumerated set. The
#: selection rule is also predeclared: among candidates whose DISCOVERY
#: EV_per_all_windows exceeds MIN_EV_PER_WINDOW_USD at size 1, the one with
#: the highest opportunity_rate is carried to OOS -- exactly one, tested
#: once. Ties break toward the earlier checkpoint, then toward UP.
CANDIDATES: Tuple[Dict[str, Any], ...] = tuple(
    {"name": "c%02d_%s_%s%s" % (cp, side, ("ge" if sign > 0 else "le"),
                                str(abs(thr)).replace(".", "p")),
     "checkpoint_s": cp, "side": side, "d_bps_sign": sign,
     "d_bps_threshold": thr, "max_price": 0.90, "min_size": 1}
    for cp in (15, 30, 60, 120)
    for side in ("UP", "DOWN")
    for sign, thr in ((1, 5.0), (1, 25.0), (-1, 5.0), (-1, 25.0))
)
MIN_EV_PER_WINDOW_USD = D("0.01")


def self_fingerprint() -> str:
    return hashlib.sha256(
        open(__file__, "rb").read()).hexdigest()


FUTURE_WORDS = ("won", "resolution", "settlement", "settled", "eventual",
                "winner", "disappear", "future", "payout")


def assert_no_leakage(feat: Dict[str, Any]) -> None:
    """A causal feature dict may not even MENTION an outcome."""
    for k in feat:
        low = k.lower()
        for w in FUTURE_WORDS:
            if w in low:
                raise AssertionError(
                    "causal feature %r names the future (%r)" % (k, w))


# ------------------------------------------------------------------- inputs
def tape_rows(pattern: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for f in sorted(glob.glob(pattern)):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except Exception:                              # noqa: BLE001
                    continue
    return out


def _dec(v: Any) -> Optional[D]:
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def reference_series(rows: List[Dict[str, Any]]) -> List[Tuple[float, D]]:
    ser: List[Tuple[float, D]] = []
    for r in rows:
        if r.get("kind") != "index_price":
            continue
        t = r.get("parsed_event_time")
        if t is None:
            continue
        v = _dec(r.get("parsed_value") or (r.get("raw") or {}).get("p"))
        if v is not None:
            ser.append((float(t), v))
    ser.sort()
    return ser


def reference_at(ser, t: float) -> Dict[str, Any]:
    """The last value published AT OR BEFORE t. Never read forward."""
    if not ser:
        return {"value": None, "why": "no reference rows"}
    i = bisect.bisect_right([x[0] for x in ser], t) - 1
    if i < 0:
        return {"value": None, "why": "nothing published before t"}
    age = t - ser[i][0]
    return {"value": ser[i][1], "age_s": round(age, 3),
            "stale": age > MAX_REFERENCE_AGE_S}


def windows(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """BTC5m windows with an AUTHORITATIVE strike, plus their checkpoints.

    LOADER CORRECTION, 2026-10-05, authorized as loader-only. THE DEFECT: this
    read the strike from `kind == "window"` rows alone. The venue sets
    strike.value AT availableAt, and the recorder's metadata poll re-reads the
    contract every cycle -- emitting the FIRST read as `window` and every
    re-read as `window_poll`. The first read therefore carries strike: null by
    construction, so this loader found a strike for 2 of 18 tickers on a tape
    whose window_poll rows carry one for 18, and the study ran on ~1-2 windows
    while reporting 0 trades across 32 candidates. That was a measurement
    failure, not a test of the hypothesis.

    The strike taken is EXACTLY what the venue supplied -- the FIRST non-null
    reading, with its row kind, strike_type and the row's own t_ns kept as
    provenance, matching the corrected census rule. Nothing is inferred and a
    window with no authoritative strike FAILS CLOSED (it simply has no
    `strike` key, and observations() already returns [] for it).

    NOTHING ELSE CHANGED: no candidate rule, checkpoint, D definition, bin,
    threshold, fee model, capacity calculation, selection criterion,
    discovery/OOS split or OOS seal.
    """
    w: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if r.get("duration") != "BTC05M":
            continue
        tk = r.get("ticker")
        if not tk or tk in EXCLUDED_TICKERS:
            continue
        if r.get("kind") in ("window", "window_poll"):
            k = _dec(r.get("strike"))
            if k is None:
                continue
            d = w.setdefault(tk, {"ticker": tk, "checkpoints": []})
            if d.get("strike") is not None:
                continue              # keep the FIRST non-null venue reading
            d["strike"] = k
            d["strike_type"] = r.get("strike_type")
            d["strike_source"] = ("contract.strike.value, venue-supplied, "
                                  "from a `%s` row" % r.get("kind"))
            d["strike_observed_t_ns"] = r.get("t_ns")
            d["strike_availableAt"] = r.get("availableAt")
            d["strike_inferred"] = False
            # t0_epoch IS NOT RESET HERE. The original assigned None on every
            # window row; with 9,200 poll rows that would erase a t0_epoch a
            # checkpoint had already supplied, and the checkpoint branch is
            # the only authority on it.
            d.setdefault("t0_epoch", None)
            d["expiry"] = r.get("expiry")
            d["resolved_index"] = r.get("resolved_index")
        elif r.get("kind") == "checkpoint":
            d = w.setdefault(tk, {"ticker": tk, "checkpoints": []})
            d["checkpoints"].append(r)
            if r.get("t0_epoch") is not None:
                d["t0_epoch"] = float(r["t0_epoch"])
            if r.get("expiry_epoch") is not None:
                d["expiry_epoch"] = float(r["expiry_epoch"])
    return w


def settlements(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Terminal outcome per ticker. AN OUTCOME, never a feature."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") != "settlement":
            continue
        tk = r.get("ticker")
        ev = r.get("raw_event") or {}
        side = None
        for c in (ev.get("contracts") or []):
            if str(c.get("label", "")).lower() == "up":
                side = c.get("resolutionSide") or c.get("resolution_side")
        val = _dec((r.get("settlement") or {}).get("value"))
        if tk and (side is not None or val is not None):
            out[tk] = {"up_resolution_side": side, "settlement_value": val,
                       "status": ev.get("status")}
    return out


# ---------------------------------------------------------- executable book
def vwap(levels: List[List[str]], size: D) -> Dict[str, Any]:
    """Cumulative VWAP to fill `size`, or unfillable. NEVER a touch price."""
    need, cost, got = size, D("0"), D("0")
    for lvl in (levels or []):
        px, qty = _dec(lvl[0]), _dec(lvl[1])
        if px is None or qty is None:
            continue
        take = min(need, qty)
        cost += px * take
        got += take
        need -= take
        if need <= 0:
            break
    if got < size:
        return {"fillable": False, "available": str(got)}
    return {"fillable": True, "vwap": cost / size, "available": str(got)}


def book_state(cp: Dict[str, Any], size: D) -> Dict[str, Any]:
    """UP and DOWN executable VWAPs at this size, symmetrically."""
    yes = cp.get("yes_ladder") or {}
    no = cp.get("no_ladder_derived") or {}
    up = vwap(yes.get("asks"), size)
    dn = vwap(no.get("asks"), size)
    up_bid = vwap(yes.get("bids"), size)
    out = {
        "structural_book_state": cp.get("book_state"),
        "book_integrity": cp.get("book_integrity"),
        "quote_age_s": cp.get("quote_age_s"),
        "UP": up, "DOWN": dn,
        "economic_two_sided": bool(up.get("fillable")
                                   and dn.get("fillable")),
        "yes_levels_asks": (cp.get("yes_levels") or {}).get("asks"),
        "yes_levels_bids": (cp.get("yes_levels") or {}).get("bids"),
    }
    if up.get("fillable") and dn.get("fillable"):
        out["combined_cost"] = str(up["vwap"] + dn["vwap"])
        out["leader"] = ("UP" if up["vwap"] > dn["vwap"]
                         else ("DOWN" if dn["vwap"] > up["vwap"] else "TIED"))
        out["spread_proxy"] = (str(up["vwap"] - up_bid["vwap"])
                               if up_bid.get("fillable") else None)
    return out


def fee_for(price: D, size: D) -> D:
    """The venue's charged taker fee, at the cent rounding it applied."""
    raw = TAKER_RATE * price * (D("1") - price) * size
    q = raw.quantize(FEE_QUANTUM)
    return q if q > 0 else D("0")


def breakeven(price: D) -> D:
    return price + TAKER_RATE * price * (D("1") - price)


# ------------------------------------------------------------ observations
def observations(w: Dict[str, Any], ser, size: D) -> List[Dict[str, Any]]:
    """One causal row per (window, checkpoint). No outcome inside."""
    out: List[Dict[str, Any]] = []
    k = w.get("strike")
    t0 = w.get("t0_epoch")
    if k is None or t0 is None:
        return out
    by_cp = {}
    for cp in w["checkpoints"]:
        if cp.get("checkpoint_family") != "EARLY_FROM_T0":
            continue
        c = cp.get("checkpoint_s")
        if c in CHECKPOINTS and c not in by_cp:
            by_cp[c] = cp
    seen_d: List[Tuple[int, D]] = []
    for c in sorted(by_cp):
        cp = by_cp[c]
        t = t0 + float(c)
        ref = reference_at(ser, t)
        if ref.get("value") is None or ref.get("stale"):
            continue
        d_usd = ref["value"] - k
        d_bps = float(d_usd / k * D("10000"))
        bk = book_state(cp, size)
        feat: Dict[str, Any] = {
            "checkpoint_s": c,
            "D_usd": str(d_usd),
            "D_bps": round(d_bps, 4),
            "abs_D_bps": round(abs(d_bps), 4),
            "D_sign": (1 if d_usd > 0 else (-1 if d_usd < 0 else 0)),
            "reference_age_s": ref.get("age_s"),
            "reference_value": str(ref["value"]),
            "strike": str(k),
            "quote_age_s": bk.get("quote_age_s"),
            "structural_book_state": bk.get("structural_book_state"),
            "book_integrity": bk.get("book_integrity"),
            "economic_two_sided": bk.get("economic_two_sided"),
            "leader": bk.get("leader"),
            "combined_cost": bk.get("combined_cost"),
            "spread_proxy": bk.get("spread_proxy"),
            "yes_levels_asks": bk.get("yes_levels_asks"),
            "yes_levels_bids": bk.get("yes_levels_bids"),
            "UP_vwap": (str(bk["UP"]["vwap"]) if bk["UP"].get("fillable")
                        else None),
            "DOWN_vwap": (str(bk["DOWN"]["vwap"])
                          if bk["DOWN"].get("fillable") else None),
            "UP_available": bk["UP"].get("available"),
            "DOWN_available": bk["DOWN"].get("available"),
            # trajectory, causal: only checkpoints AT OR BEFORE this one
            "D_bps_first": (round(float(seen_d[0][1] / k * D("10000")), 4)
                            if seen_d else None),
            "D_bps_step": (round(d_bps - float(seen_d[-1][1] / k
                                               * D("10000")), 4)
                           if seen_d else None),
            "D_bps_momentum": (round(d_bps - float(seen_d[0][1] / k
                                                   * D("10000")), 4)
                               if seen_d else None),
            "D_sign_flips_so_far": sum(
                1 for i in range(1, len(seen_d))
                if (seen_d[i][1] > 0) != (seen_d[i - 1][1] > 0)),
            "n_prior_observations": len(seen_d),
        }
        assert_no_leakage(feat)
        out.append({"ticker": w["ticker"], "features": feat})
        seen_d.append((c, d_usd))
    return out


# ------------------------------------------------------------------ tables
def cell_table(obs: List[Dict[str, Any]], outcomes: Dict[str, Dict],
               side: str, size: D) -> Dict[str, Any]:
    """Settlement probability vs the contemporaneous executable price.

    Cells are (checkpoint, D_bps bin, executable-price bin). FIXED bins.
    """
    cells: Dict[str, Dict[str, Any]] = {}
    for o in obs:
        f = o["features"]
        px = _dec(f["UP_vwap"] if side == "UP" else f["DOWN_vwap"])
        if px is None:
            continue
        res = outcomes.get(o["ticker"]) or {}
        up_side = res.get("up_resolution_side")
        if up_side not in ("yes", "no"):
            continue
        wins = ((side == "UP" and up_side == "yes")
                or (side == "DOWN" and up_side == "no"))
        db = _bin(f["D_bps"], D_BINS_BPS)
        pb = _bin(float(px), ASK_BINS)
        key = "cp%03d|D%s|P%s" % (f["checkpoint_s"], db, pb)
        c = cells.setdefault(key, {
            "checkpoint_s": f["checkpoint_s"], "D_bin_bps": db,
            "price_bin": pb, "n": 0, "wins": 0, "windows": set(),
            "px_sum": D("0"), "fee_sum": D("0"), "pnl_sum": D("0")})
        c["n"] += 1
        c["wins"] += 1 if wins else 0
        c["windows"].add(o["ticker"])
        fee = fee_for(px, size)
        c["px_sum"] += px * size
        c["fee_sum"] += fee
        c["pnl_sum"] += ((D("1") * size) if wins else D("0")) - px * size - fee
    out = {}
    for k, c in sorted(cells.items()):
        n = c["n"]
        avg_px = c["px_sum"] / size / n
        out[k] = {
            "checkpoint_s": c["checkpoint_s"], "D_bin_bps": c["D_bin_bps"],
            "price_bin": c["price_bin"],
            "observations": n, "distinct_windows": len(c["windows"]),
            "settlement_win_pct": round(100.0 * c["wins"] / n, 2),
            "avg_executable_vwap": str(avg_px),
            "breakeven_pct_with_fees": round(float(breakeven(avg_px)) * 100,
                                             2),
            "edge_pp_win_minus_breakeven": round(
                100.0 * c["wins"] / n - float(breakeven(avg_px)) * 100, 2),
            "fees_usd": str(c["fee_sum"]),
            "ev_per_trade_usd": str((c["pnl_sum"] / n).quantize(D("0.0001"))),
            "wilson95": wilson(c["wins"], n),
        }
    return out


def _bin(v: float, edges) -> str:
    for i in range(len(edges) - 1):
        if edges[i] <= v < edges[i + 1]:
            return "[%s,%s)" % (edges[i], edges[i + 1])
    return "<%s" % edges[0] if v < edges[0] else ">=%s" % edges[-1]


def wilson(k: int, n: int) -> Optional[List[float]]:
    if n == 0:
        return None
    import math
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round(100 * (c - half) / d, 2), round(100 * (c + half) / d, 2)]


def evaluate_candidate(cand: Dict[str, Any], obs, outcomes,
                       n_windows: int) -> Dict[str, Any]:
    """One PREDECLARED rule. NO_TRADE windows stay in the denominator."""
    size = D(str(cand["min_size"]))
    side = cand["side"]
    traded: List[Tuple[str, D]] = []
    taken = set()
    for o in obs:
        f = o["features"]
        if f["checkpoint_s"] != cand["checkpoint_s"]:
            continue
        if o["ticker"] in taken:
            continue
        if not f["economic_two_sided"]:
            continue
        if f["book_integrity"] != "OK":
            continue
        px = _dec(f["UP_vwap"] if side == "UP" else f["DOWN_vwap"])
        if px is None or float(px) > cand["max_price"]:
            continue
        d = f["D_bps"]
        if cand["d_bps_sign"] > 0 and not d >= cand["d_bps_threshold"]:
            continue
        if cand["d_bps_sign"] < 0 and not d <= -cand["d_bps_threshold"]:
            continue
        res = outcomes.get(o["ticker"]) or {}
        up_side = res.get("up_resolution_side")
        if up_side not in ("yes", "no"):
            continue
        wins = ((side == "UP" and up_side == "yes")
                or (side == "DOWN" and up_side == "no"))
        pnl = ((D("1") * size) if wins else D("0")) - px * size \
            - fee_for(px, size)
        traded.append((o["ticker"], pnl))
        taken.add(o["ticker"])
    n_tr = len(traded)
    total = sum((p for _, p in traded), D("0"))
    # drawdown over the window sequence, in order
    peak, dd, run = D("0"), D("0"), D("0")
    for _, p in traded:
        run += p
        peak = max(peak, run)
        dd = min(dd, run - peak)
    return {
        "candidate": cand,
        "trades": n_tr,
        "windows_available": n_windows,
        "opportunity_rate_per_window": (round(n_tr / n_windows, 4)
                                        if n_windows else None),
        "ev_per_trade_usd": (str((total / n_tr).quantize(D("0.0001")))
                             if n_tr else None),
        "ev_per_all_windows_usd": (str((total / n_windows)
                                       .quantize(D("0.0001")))
                                   if n_windows else None),
        "total_usd": str(total),
        "max_drawdown_usd": str(dd),
        "no_trade_windows": n_windows - n_tr,
        "denominator_note": ("per-window figures divide by ALL windows with "
                             "a settlement, NO_TRADE included"),
    }


def capacity(obs_by_size: Dict[int, List[Dict[str, Any]]]
             ) -> Dict[str, Any]:
    """EXECUTABLE CAPACITY. How much can each side really absorb?

    An earlier draft of this function returned a placeholder that counted
    the same thing at every size and said so in a `note` -- which would
    have read as a capacity curve while measuring nothing. It is a real
    curve now: at each predeclared size, how often BOTH sides fill, how
    often each side alone fills, and what the fill costs.
    """
    out: Dict[str, Any] = {}
    for s_ in SIZE_LEVELS:
        obs = obs_by_size.get(s_) or []
        n = len(obs)
        if not n:
            out["size_%d" % s_] = {"observations": 0}
            continue
        up_ok = [o for o in obs if o["features"]["UP_vwap"]]
        dn_ok = [o for o in obs if o["features"]["DOWN_vwap"]]
        both = [o for o in obs if o["features"]["economic_two_sided"]]
        def avg(rows, key):
            vals = [_dec(r["features"][key]) for r in rows]
            vals = [v for v in vals if v is not None]
            return (str((sum(vals, D("0")) / len(vals))
                        .quantize(D("0.0001"))) if vals else None)
        out["size_%d" % s_] = {
            "observations": n,
            "up_fillable": len(up_ok),
            "down_fillable": len(dn_ok),
            "economic_two_sided": len(both),
            "economic_two_sided_pct": round(100.0 * len(both) / n, 2),
            "avg_up_vwap_when_fillable": avg(up_ok, "UP_vwap"),
            "avg_down_vwap_when_fillable": avg(dn_ok, "DOWN_vwap"),
            "avg_combined_cost_when_two_sided": avg(both, "combined_cost"),
            "notional_per_trade_usd_at_avg_up": (
                None if not up_ok else
                str((_dec(avg(up_ok, "UP_vwap")) or D("0")) * D(str(s_)))),
        }
    return out


def incorporation(obs_by_size: Dict[int, List[Dict[str, Any]]]
                  ) -> Dict[str, Any]:
    """DOES D_t LEAD THE EXECUTABLE PRICE?

    For each window, pair the causal D_bps at checkpoint c with the LATER
    change in the UP executable VWAP from c to the next checkpoint. The
    predictor is causal; the outcome is a PRICE, never a settlement. If the
    price already embeds D_t the correlation is ~0 and there is no lead to
    trade, whatever the settlement tables say.
    """
    obs = obs_by_size.get(1) or []
    by_w: Dict[str, Dict[int, Dict[str, Any]]] = collections.defaultdict(dict)
    for o in obs:
        by_w[o["ticker"]][o["features"]["checkpoint_s"]] = o["features"]
    rows = []
    for tk, cps in by_w.items():
        ks = sorted(cps)
        for i in range(len(ks) - 1):
            a, b = cps[ks[i]], cps[ks[i + 1]]
            pa, pb = _dec(a["UP_vwap"]), _dec(b["UP_vwap"])
            if pa is None or pb is None:
                continue
            rows.append({"ticker": tk, "from_s": ks[i], "to_s": ks[i + 1],
                         "D_bps_at_from": a["D_bps"],
                         "D_bps_step_into_from": a["D_bps_step"],
                         "up_vwap_from": str(pa), "up_vwap_to": str(pb),
                         "up_price_change": float(pb - pa)})
    def corr(xs, ys):
        n = len(xs)
        if n < 3:
            return None
        mx, my = sum(xs) / n, sum(ys) / n
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        if sxx <= 0 or syy <= 0:
            return None
        return round(sxy / (sxx ** 0.5 * syy ** 0.5), 4)
    per_cp = {}
    for cp in CHECKPOINTS:
        sub = [r for r in rows if r["from_s"] == cp]
        per_cp["from_%03ds" % cp] = {
            "n": len(sub),
            "corr_D_level_vs_next_price_change": corr(
                [r["D_bps_at_from"] for r in sub],
                [r["up_price_change"] for r in sub]),
            "corr_D_step_vs_next_price_change": corr(
                [r["D_bps_step_into_from"] for r in sub
                 if r["D_bps_step_into_from"] is not None],
                [r["up_price_change"] for r in sub
                 if r["D_bps_step_into_from"] is not None]),
        }
    return {
        "per_checkpoint": per_cp,
        "n_pairs": len(rows),
        "reading": ("a correlation near zero means the executable price "
                    "already embeds D_t and there is NO informational lead "
                    "to trade; a positive one means the price moves AFTER "
                    "the reference does, which is the only version of this "
                    "that could pay"),
        "label": ("DESCRIPTIVE. The outcome is a later price, not a "
                  "settlement, and nothing here is a trading rule."),
    }


# -------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes",
                    default="analysis/gemini/btc5m_rec/btc5m_rec_*.jsonl.gz")
    ap.add_argument("--tranche", default="T1")
    ap.add_argument("--unseal-oos", action="store_true",
                    help="read the SEALED partition; ONLY to test one "
                         "already-frozen candidate, once")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    rows = tape_rows(a.tapes)
    ser = reference_series(rows)
    wins = windows(rows)
    outcomes = settlements(rows)

    settled = sorted([tk for tk in wins
                      if (outcomes.get(tk) or {}).get("up_resolution_side")
                      in ("yes", "no")])
    n_seal = int(len(settled) * OOS_FRACTION)
    oos = set(settled[len(settled) - n_seal:]) if n_seal else set()
    use = [tk for tk in settled if (tk in oos) == bool(a.unseal_oos)]

    obs_by_size: Dict[int, List[Dict[str, Any]]] = {}
    for s in SIZE_LEVELS:
        acc: List[Dict[str, Any]] = []
        for tk in use:
            acc.extend(observations(wins[tk], ser, D(str(s))))
        obs_by_size[s] = acc

    tables = {}
    for s in SIZE_LEVELS:
        for side in ("UP", "DOWN"):
            tables["size%d_%s" % (s, side)] = cell_table(
                obs_by_size[s], outcomes, side, D(str(s)))

    cands = [evaluate_candidate(c, obs_by_size[c["min_size"]], outcomes,
                                len(use)) for c in CANDIDATES]

    # chaining check: is each strike the PREVIOUS window's settlementValue?
    chain = []
    for i in range(1, len(settled)):
        prev, cur = settled[i - 1], settled[i]
        pv = (outcomes.get(prev) or {}).get("settlement_value")
        k = wins[cur].get("strike")
        if pv is None or k is None:
            continue
        chain.append({"window": cur, "strike": str(k),
                      "previous_settlement_value": str(pv),
                      "matches_to_the_cent": bool(
                          abs(float(pv) - float(k)) < 0.01)})

    rep = {
        "label": LABEL,
        "analysis_fingerprint_sha256": self_fingerprint(),
        "tranche": a.tranche,
        "preregistered": ("written and fingerprinted BEFORE any "
                          "profitability number from the prospective "
                          "recorder was inspected; run UNCHANGED per "
                          "tranche"),
        "primary_question": ("does any causal state produce a settlement "
                             "probability far enough from the "
                             "contemporaneous executable price to clear the "
                             "venue's actual fees -- symmetrically for UP "
                             "and DOWN"),
        "causal_state": ("D_t = published LRRR_XBT(t) - the current "
                         "window's authoritative strike, which the venue "
                         "sets to the PREVIOUS window's settlementValue"),
        "Z_status": ("NOT IDENTIFIED. D_t is an observed difference, not a "
                     "normalised state; no sigma and no time scale are "
                     "applied."),
        "no_threshold_optimization": ("bins and candidates are predeclared "
                                      "constants; there is no search, no "
                                      "argmax over cutoffs and no best-cell "
                                      "output"),
        "selection_rule": ("among candidates whose DISCOVERY "
                           "ev_per_all_windows exceeds %s at size 1, carry "
                           "the highest opportunity_rate to OOS -- exactly "
                           "one, tested once" % MIN_EV_PER_WINDOW_USD),
        "excluded_tickers": list(EXCLUDED_TICKERS),
        "exclusion_reason": ("cohort-1 probe-contaminated windows; its 1-of-5 "
                             "UP outcome informs no threshold here"),
        "oos_seal": ("the last %d of %d settled windows are SEALED%s"
                     % (n_seal, len(settled),
                        " and were READ because --unseal-oos was passed"
                        if a.unseal_oos else " and were NOT read")),
        "windows_settled_total": len(settled),
        "windows_used": len(use),
        "reference_series_points": len(ser),
        "max_reference_age_s": MAX_REFERENCE_AGE_S,
        "fee_model": {
            "taker_rate": str(TAKER_RATE),
            "rounding": "quantized to the cent, as the venue applied it",
            "reconciled": ("against cohort 1's own charged fees: 0.0155 / "
                           "0.0168 / 0.0161 / 0.0135 predicted, $0.02 "
                           "charged each"),
            "breakeven": "p + 0.07*p*(1-p)",
        },
        "checkpoints_s": list(CHECKPOINTS),
        "size_levels": list(SIZE_LEVELS),
        "observations_by_size": {str(s): len(obs_by_size[s])
                                 for s in SIZE_LEVELS},
        "tables": tables,
        "candidates": cands,
        "capacity": capacity(obs_by_size),
        "incorporation_lag": incorporation(obs_by_size),
        "chaining_check": chain,
        "chaining_label": ("SETTLEMENT MECHANICS, not a trading signal. It "
                           "says what the strike IS, which is why D_t is "
                           "anchored to it."),
        "accounting": ("historical replay only. No order was sent and "
                       "nothing here is realized."),
    }
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, sort_keys=True, default=str)
    print(json.dumps({
        "label": LABEL,
        "fingerprint": rep["analysis_fingerprint_sha256"],
        "tranche": a.tranche,
        "windows_settled_total": len(settled),
        "windows_used": len(use),
        "windows_sealed_oos": n_seal,
        "observations_size1": len(obs_by_size[1]),
        "reference_series_points": len(ser),
        "chaining_confirmed": sum(1 for c in chain
                                  if c["matches_to_the_cent"]),
        "chaining_checked": len(chain),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
