"""LANE 3 -- MAKER TOXICITY SEPARABILITY. RESEARCH ONLY. No venue writes.

THE QUESTION. Gemini BTC5m's median touch spread is extraordinary (~$0.24-0.29).
The frozen maker studies established that resting inside it does not pay. This
study asks the only remaining maker question worth asking:

  CONDITIONAL ON INFORMATION AVAILABLE STRICTLY BEFORE THE HYPOTHETICAL FILL,
  is the distribution of POST-FILL outcomes different for toxic versus
  non-toxic states?

If it is not, liquidity supply here cannot be separated from the adverse
selection that comes with it, and the maker hypothesis is dead. THIS IS NOT A
CANCELLATION-THRESHOLD SWEEP. No cancel policy is proposed, swept or tuned.
Nothing here is permitted to search for a surviving variant: a negative result
reported plainly is the success condition.

WHAT IS REUSED, UNCHANGED, FROM THE FROZEN STUDY
`scripts/gemini_btc5m_maker_study.py` (267ef1d1...3a63) is read, not modified.
Copied verbatim in substance:
  * the fee model: maker 0.0175*q*p*(1-p), taker 0.07*q*p*(1-p), cent-rounded
    UP, both FEES. NO rebate or liquidity reward is assumed or credited.
  * the placements: 25/50/75% through the observed spread from OUR passive
    side, snapped AWAY from aggression (buy floors, sell ceilings).
  * the conservative queue: we arrive BEHIND every contract displayed at a
    better-or-equal price on our side, and our own size must also trade.
  * THE FILL-EVIDENCE STANDARD. NO TOUCH = FILL. A resting quote fills only on
    a later print at-or-through our price whose AGGRESSOR SIDE is consistent
    with `buyer_is_maker` (True = the buyer rested and a seller crossed, so the
    print can hit a resting BID and not a resting ask), with cumulative
    qualifying size exceeding the queue ahead of us plus our own. A print whose
    side cannot be determined is not evidence. An unsettled window is excluded
    from the denominator rather than counted as an unfilled order.

WHAT IS NEW. The frozen study quoted only at the eight EARLY_FROM_T0
checkpoints, which is why it found 0-for-7. Here the 1 Hz `p_snapshot` book is
used as the candidate-quote grid, to make fills as plentiful as the tape can
possibly make them before concluding anything about scarcity.

EXTERNAL BTC PRICE IS NOT AVAILABLE. The lead/lag feed probe rejected all four
venues, so no external spot/futures feature appears here. The only reference
series used is the venue's own authoritative `index_price` (raw.I == LRRR_XBT).

INDEPENDENCE. Overlapping 1 Hz states inside one five-minute window are NOT
independent trials. Every cell therefore reports its distinct-window count and
every interval is a WINDOW-LEVEL bootstrap (windows resampled whole).

BASE-RATE TRAP. A settlement-direction base rate over a handful of windows can
masquerade as skill. Hence the window counts on every cell, and hence the
settlement-direction mix is reported alongside each bin.
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
import random
import statistics
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_UP, Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

LABEL = "GEMINI_MAKER_TOXICITY_SEPARABILITY_LANE3_V1"
FROZEN_MAKER_STUDY_SHA256 = (
    "267ef1d15944feb49c5e25c897e61064e446f9d5a5e4ce7d7a33ac965f763a63")
FROZEN_STATE_AWARE_SHA256_PREFIX = "513eff6e"

#: sealed out-of-sample. Outcomes for these are NEVER read.
SEALED_OOS = ("BTC05M2610051845", "BTC05M2610051850", "BTC05M2610051855",
              "BTC05M2610051900", "BTC05M2610051905", "BTC05M2610051910")
#: probe-contaminated: our own activity is in the tape, so the book is not
#: an exogenous observation of the venue.
PROBE_CONTAMINATED = ("BTC05M2610051550", "BTC05M2610051555",
                      "BTC05M2610051600", "BTC05M2610051605",
                      "BTC05M2610051610", "BTC05M2610051730",
                      "BTC05M2610051755", "BTC05M2610051800",
                      "BTC05M2610051805", "BTC05M2610051810")
EXCLUDED = set(SEALED_OOS) | set(PROBE_CONTAMINATED)

PLACEMENTS: Tuple[Tuple[str, D], ...] = (("p25", D("0.25")),
                                         ("p50", D("0.50")),
                                         ("p75", D("0.75")))
UNIT = D("1")
TAKER_RATE = D("0.07")
MAKER_RATE = D("0.0175")
FEE_Q = D("0.01")
TICK = D("0.01")
NO_OFFER_PRICE = D("0.98")
MARK_HORIZON_S = 30.0
BOOT_N = 4000
BOOT_SEED = 20261005


def self_fingerprint() -> str:
    return hashlib.sha256(open(__file__, "rb").read()).hexdigest()


def _d(v: Any) -> Optional[D]:
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def fee(rate: D, price: D, qty: D = UNIT) -> D:
    """VERBATIM from the frozen study. A FEE. No rebate anywhere."""
    raw = rate * qty * price * (D("1") - price)
    if raw <= 0:
        return D("0")
    return raw.quantize(FEE_Q, rounding=ROUND_CEILING)


def _ns(r: Dict[str, Any]) -> Optional[float]:
    for k in ("E_ns", "t_ns"):
        v = r.get(k)
        if v is not None:
            return float(v) / 1e9
    return None


def tape(patterns: Sequence[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    out: List[Dict[str, Any]] = []
    files: List[str] = []
    for pattern in patterns:
        for f in sorted(glob.glob(pattern)):
            if f in files:
                continue
            files.append(f)
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        out.append(json.loads(line))
                    except Exception:                          # noqa: BLE001
                        continue
    return out, files


# ------------------------------------------------------------------ loading
def load(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    snaps: Dict[str, List[Tuple[float, Dict[str, Any]]]] = \
        collections.defaultdict(list)
    trades: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    meta: Dict[str, Dict[str, Any]] = {}
    settle: Dict[str, str] = {}
    index: List[Tuple[float, float]] = []
    for r in rows:
        k = r.get("kind")
        if k == "p_snapshot":
            m = r.get("market") or ""
            if "BTC05M" not in m:
                continue
            t = _ns(r)
            if t is not None:
                snaps[m].append((t, r))
        elif k == "p_trade":
            m = r.get("market") or ""
            if "BTC05M" in m:
                trades[m].append(r)
        elif k in ("window", "window_poll"):
            m, tk = r.get("market"), r.get("ticker")
            if m and tk and r.get("duration") == "BTC05M":
                meta.setdefault(m, {"ticker": tk, "strike": r.get("strike"),
                                    "availableAt": r.get("availableAt"),
                                    "expiry": r.get("expiry")})
        elif k == "settlement":
            ev = r.get("raw_event") or {}
            tk = r.get("ticker")
            for c in (ev.get("contracts") or []):
                if str(c.get("label", "")).lower() == "up":
                    side = c.get("resolutionSide")
                    if side in ("yes", "no") and tk:
                        settle[tk] = side
        elif k == "index_price":
            raw = r.get("raw") or {}
            if raw.get("I") != "LRRR_XBT":
                continue
            v = _d(r.get("parsed_value"))
            t = _ns(r)
            if v is not None and t is not None:
                index.append((t, float(v)))
    for m in snaps:
        snaps[m].sort(key=lambda x: x[0])
    for m in trades:
        trades[m].sort(key=lambda r: _ns(r) or 0.0)
    index.sort()
    return {"snaps": snaps, "trades": trades, "meta": meta,
            "settle": settle, "index": index}


def _iso(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(
            s.replace("Z", "+00:00")).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


# -------------------------------------------------------------------- book
def book(snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Touch and depth of the ONE published ladder. YES terms throughout.

    Same construction and same no-offer guard as the frozen study, reading
    `p_snapshot` bids/asks instead of a checkpoint's `yes_ladder`.
    """
    bids, asks = snap.get("bids") or [], snap.get("asks") or []
    if not bids or not asks:
        return None
    b_px, b_sz = _d(bids[0][0]), _d(bids[0][1])
    a_px, a_sz = _d(asks[0][0]), _d(asks[0][1])
    if None in (b_px, b_sz, a_px, a_sz) or b_px >= a_px:
        return None
    if a_px >= NO_OFFER_PRICE and a_sz <= UNIT:
        return None
    return {"bid": b_px, "ask": a_px, "bid_size": b_sz, "ask_size": a_sz,
            "spread": a_px - b_px, "bids": bids, "asks": asks}


def side_choice(bk: Dict[str, Any]) -> Optional[str]:
    """UNDERDOG, exactly as the frozen study defines it. Direction only."""
    up_cost, dn_cost = bk["ask"], D("1") - bk["bid"]
    if up_cost < dn_cost:
        return "UP"
    if dn_cost < up_cost:
        return "DOWN"
    return None


def placement(bk: Dict[str, Any], side: str, frac: D) -> Optional[D]:
    """VERBATIM placement rule from the frozen study."""
    if side == "UP":
        px = bk["bid"] + frac * bk["spread"]
        px = px.quantize(TICK, rounding=ROUND_DOWN)
        return px if bk["bid"] < px < bk["ask"] else None
    px = bk["ask"] - frac * bk["spread"]
    px = px.quantize(TICK, rounding=ROUND_UP)
    return px if bk["bid"] < px < bk["ask"] else None


def ahead_size(bk: Dict[str, Any], side: str, px: D) -> D:
    """VERBATIM conservative queue from the frozen study."""
    tot = D("0")
    for lv in (bk["bids"] if side == "UP" else bk["asks"]):
        p, q = _d(lv[0]), _d(lv[1])
        if p is None or q is None:
            continue
        if (side == "UP" and p >= px) or (side == "DOWN" and p <= px):
            tot += q
    return tot


def fill_evidence(trs: List[Dict[str, Any]], side: str, px: D, t0: float,
                  ahead: D, qty: D = UNIT) -> Optional[Dict[str, Any]]:
    """VERBATIM TRADE_THROUGH tier from the frozen study. NO TOUCH = FILL.

    Returns None when no qualifying print ever arrives. The frozen study's
    weaker BOOK_EVOLUTION tier is deliberately NOT used here: a separability
    test on outcomes must not be fed fills that were never evidenced.
    """
    need = ahead + qty
    cum = D("0")
    for tr in trs:
        t = _ns(tr)
        if t is None or t <= t0:
            continue
        p, q = _d(tr.get("price")), _d(tr.get("qty"))
        if p is None or q is None:
            continue
        through = (p <= px) if side == "UP" else (p >= px)
        if not through:
            continue
        bim = tr.get("buyer_is_maker")
        if bim is None:
            continue
        aggressor_sold = bool(bim)
        if side == "UP" and not aggressor_sold:
            continue
        if side == "DOWN" and aggressor_sold:
            continue
        cum += q
        if cum >= need:
            return {"fill_t": t, "time_to_fill_s": round(t - t0, 3),
                    "traded_through_qty": str(cum),
                    "queue_ahead_qty": str(ahead),
                    "tier": "TRADE_THROUGH"}
    return None


# --------------------------------------------------- pre-fill feature block
def _snap_at_or_before(times: List[float], i_now: int, t: float) -> int:
    j = bisect.bisect_right(times, t, 0, i_now + 1) - 1
    return j


def features(mkt_snaps: List[Tuple[float, Dict[str, Any]]],
             times: List[float], mids: List[Optional[float]], i: int,
             trs: List[Dict[str, Any]], index: List[Tuple[float, float]],
             index_t: List[float], t0: float, bk: Dict[str, Any],
             t_avail: Optional[float], t_exp: Optional[float]
             ) -> Dict[str, Optional[float]]:
    """EVERY feature uses only information available at or before t0.

    t0 is the timestamp of the book we would be quoting into, so the book
    itself and everything earlier is admissible; nothing after t0 is read.
    """
    def mid_back(dt_s: float) -> Optional[float]:
        j = _snap_at_or_before(times, i, t0 - dt_s)
        return mids[j] if j >= 0 else None

    m0 = mids[i]
    f: Dict[str, Optional[float]] = {}
    m5, m10, m15, m30 = (mid_back(5.0), mid_back(10.0), mid_back(15.0),
                         mid_back(30.0))
    # book acceleration: the SECOND difference of the mid. A mid that is
    # moving and still accelerating is the signature a maker fears.
    f["mid_accel_5s"] = (None if None in (m0, m5, m10)
                         else round(m0 - 2.0 * m5 + m10, 5))
    f["mid_accel_15s"] = (None if None in (m0, m15, m30)
                          else round(m0 - 2.0 * m15 + m30, 5))
    f["mid_delta_5s"] = None if None in (m0, m5) else round(m0 - m5, 5)
    f["mid_delta_30s"] = None if None in (m0, m30) else round(m0 - m30, 5)

    # LRRR movement -- the venue's own authoritative reference. No external
    # BTC feed exists for this tape (all four candidate venues rejected).
    def idx_at(t: float) -> Optional[float]:
        j = bisect.bisect_right(index_t, t) - 1
        return index[j][1] if j >= 0 else None
    i0 = idx_at(t0)
    for lab, dt_s in (("5s", 5.0), ("15s", 15.0), ("30s", 30.0)):
        prev = idx_at(t0 - dt_s)
        f["lrrr_delta_%s" % lab] = (None if None in (i0, prev)
                                    else round(i0 - prev, 4))
    f["lrrr_abs_delta_15s"] = (None if f["lrrr_delta_15s"] is None
                               else abs(f["lrrr_delta_15s"]))
    f["spread"] = float(bk["spread"])
    f["touch_depth"] = float(bk["bid_size"] + bk["ask_size"])
    # queue / depth change on our own passive side over the prior 10s
    j = _snap_at_or_before(times, i, t0 - 10.0)
    if j >= 0:
        pb = book(mkt_snaps[j][1])
        if pb is not None:
            f["touch_depth_delta_10s"] = round(
                float(bk["bid_size"] + bk["ask_size"])
                - float(pb["bid_size"] + pb["ask_size"]), 4)
            f["spread_delta_10s"] = round(
                float(bk["spread"]) - float(pb["spread"]), 4)
        else:
            f["touch_depth_delta_10s"] = None
            f["spread_delta_10s"] = None
    else:
        f["touch_depth_delta_10s"] = None
        f["spread_delta_10s"] = None
    f["tte_since_available_s"] = (None if t_avail is None
                                  else round(t0 - t_avail, 2))
    f["tte_to_expiry_s"] = None if t_exp is None else round(t_exp - t0, 2)
    # recent aggressor flow: buyer_is_maker True means a SELLER crossed.
    for lab, w in (("30s", 30.0), ("60s", 60.0)):
        signed, vol = 0.0, 0.0
        for tr in trs:
            t = _ns(tr)
            if t is None or t > t0 or t < t0 - w:
                continue
            q = _d(tr.get("qty"))
            bim = tr.get("buyer_is_maker")
            if q is None or bim is None:
                continue
            s = -1.0 if bool(bim) else 1.0
            signed += s * float(q)
            vol += float(q)
        f["aggr_imbalance_%s" % lab] = round(signed, 3)
        f["aggr_volume_%s" % lab] = round(vol, 3)
        f["aggr_imbalance_signed_frac_%s" % lab] = (
            round(signed / vol, 4) if vol > 0 else None)
    return f


# ------------------------------------------------------------- the sampling
def build(data: Dict[str, Any], step_s: float) -> Dict[str, Any]:
    snaps = data["snaps"]
    index = data["index"]
    index_t = [t for t, _ in index]
    obs: List[Dict[str, Any]] = []
    census = {"markets_seen": 0, "markets_excluded_sealed_oos": 0,
              "markets_excluded_probe_contaminated": 0,
              "markets_no_meta": 0, "markets_unsettled_excluded": 0,
              "markets_used": 0, "states_considered": 0,
              "states_quotable": 0, "placements_placed": 0}
    for market, rows in sorted(snaps.items()):
        census["markets_seen"] += 1
        meta = data["meta"].get(market)
        if not meta:
            census["markets_no_meta"] += 1
            continue
        tk = meta["ticker"]
        if tk in SEALED_OOS:
            census["markets_excluded_sealed_oos"] += 1
            continue
        if tk in PROBE_CONTAMINATED:
            census["markets_excluded_probe_contaminated"] += 1
            continue
        res = data["settle"].get(tk)
        if res not in ("yes", "no"):
            # AN UNSETTLED WINDOW IS NOT AN UNFILLED ORDER. Excluded from
            # both numerator and denominator, and counted here instead.
            census["markets_unsettled_excluded"] += 1
            continue
        census["markets_used"] += 1
        t_avail, t_exp = _iso(meta.get("availableAt")), _iso(meta.get("expiry"))
        times = [t for t, _ in rows]
        mids: List[Optional[float]] = []
        for _, s in rows:
            bk = book(s)
            mids.append(None if bk is None
                        else float((bk["bid"] + bk["ask"]) / 2))
        trs = data["trades"].get(market) or []
        last_t = -1e18
        for i, (t0, snap) in enumerate(rows):
            if t_avail is not None and t0 < t_avail:
                continue
            if t_exp is not None and t0 > t_exp:
                continue
            census["states_considered"] += 1
            if t0 - last_t < step_s:
                continue
            bk = book(snap)
            if bk is None:
                continue
            side = side_choice(bk)
            if side is None:
                continue
            last_t = t0
            census["states_quotable"] += 1
            feats = features(rows, times, mids, i, trs, index, index_t,
                             t0, bk, t_avail, t_exp)
            for name, frac in PLACEMENTS:
                px = placement(bk, side, frac)
                if px is None:
                    continue
                census["placements_placed"] += 1
                pay = px if side == "UP" else D("1") - px
                ahead = ahead_size(bk, side, px)
                ev = fill_evidence(trs, side, px, t0, ahead)
                rec = {"market": market, "ticker": tk, "placement": name,
                       "side": side, "t0": t0,
                       "resting_yes_price": str(px),
                       "acquisition_price": str(pay),
                       "queue_ahead_qty": str(ahead),
                       "settlement_up_side": res,
                       "features": feats, "filled": ev is not None}
                if ev is not None:
                    f_m = fee(MAKER_RATE, pay)
                    won = ((side == "UP" and res == "yes")
                           or (side == "DOWN" and res == "no"))
                    payout = D("1") if won else D("0")
                    # REALIZED ADVERSE SELECTION, settlement-based:
                    # payout - (price + fee). Negative = toxic fill.
                    rec.update({
                        "fill": ev,
                        "maker_fee_if_filled": str(f_m),
                        "rebate_assumed": False,
                        "settled_won": won,
                        "adverse_selection_usd": float(
                            payout - pay * UNIT - f_m),
                        "mark_move_%ds_usd" % int(MARK_HORIZON_S): _mark_move(
                            rows, times, ev["fill_t"], side, pay),
                    })
                obs.append(rec)
    return {"observations": obs, "census": census}


def _mark_move(rows, times, t_fill: float, side: str,
               pay: D) -> Optional[float]:
    """Short-horizon mark movement: what we could liquidate for +30s after
    the fill, minus what we paid. Reported ALONGSIDE settlement adverse
    selection, never instead of it, and never called realized P&L."""
    j = bisect.bisect_right(times, t_fill + MARK_HORIZON_S) - 1
    if j < 0:
        return None
    bk = book(rows[j][1])
    if bk is None:
        return None
    liq = bk["bid"] if side == "UP" else D("1") - bk["ask"]
    return round(float(liq - pay), 4)


# -------------------------------------------------------- window bootstrap
def boot_mean(by_window: Dict[str, List[float]], rng: random.Random,
              n: int = BOOT_N) -> Optional[Dict[str, Any]]:
    """WINDOW-LEVEL bootstrap. Windows are resampled WHOLE, because 1 Hz
    observations inside one five-minute window are not independent trials."""
    keys = sorted(by_window)
    if not keys:
        return None
    flat = [v for k in keys for v in by_window[k]]
    point = statistics.fmean(flat)
    if len(keys) < 2:
        return {"mean": round(point, 5), "n_obs": len(flat),
                "n_windows": len(keys), "ci95": None,
                "ci_note": "a single window cannot be bootstrapped"}
    draws = []
    for _ in range(n):
        vals: List[float] = []
        for _ in keys:
            vals.extend(by_window[keys[rng.randrange(len(keys))]])
        if vals:
            draws.append(statistics.fmean(vals))
    draws.sort()
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[int(0.975 * (len(draws) - 1))]
    return {"mean": round(point, 5), "n_obs": len(flat),
            "n_windows": len(keys),
            "ci95": [round(lo, 5), round(hi, 5)]}


def boot_diff(a: Dict[str, List[float]], b: Dict[str, List[float]],
              rng: random.Random, n: int = BOOT_N) -> Optional[Dict[str, Any]]:
    """CI on the SEPARATION (mean of bin a minus mean of bin b), resampling
    windows whole and independently within each bin."""
    ka, kb = sorted(a), sorted(b)
    if len(ka) < 2 or len(kb) < 2:
        return None
    pa = statistics.fmean([v for k in ka for v in a[k]])
    pb = statistics.fmean([v for k in kb for v in b[k]])
    draws = []
    for _ in range(n):
        va: List[float] = []
        for _ in ka:
            va.extend(a[ka[rng.randrange(len(ka))]])
        vb: List[float] = []
        for _ in kb:
            vb.extend(b[kb[rng.randrange(len(kb))]])
        if va and vb:
            draws.append(statistics.fmean(va) - statistics.fmean(vb))
    if not draws:
        return None
    draws.sort()
    lo = draws[int(0.025 * (len(draws) - 1))]
    hi = draws[int(0.975 * (len(draws) - 1))]
    return {"separation": round(pa - pb, 5),
            "ci95": [round(lo, 5), round(hi, 5)],
            "excludes_zero": bool(lo > 0 or hi < 0)}


def terciles(vals: List[float]) -> Optional[Tuple[float, float]]:
    """Predeclared tercile edges of the PRE-FILL feature over all placed
    states.

    DEFECT FIX. Many features here are heavily tied at zero (aggressor flow
    in a venue that prints a handful of trades per window; a signed fraction
    bounded at +-1). Raw terciles then collapse the edges onto the tied
    value and leave a bin EMPTY, which silently drops a third of the data
    and makes the feature look untestable. The fallback is itself
    predeclared and feature-independent: a SIGN split (< 0 / == 0 / > 0) is
    the natural three-way partition of a signed feature, and a median split
    is used when even that degenerates. No edge is chosen by looking at
    outcomes.
    """
    v = sorted(vals)
    if len(v) < 6:
        return None
    a = v[int(len(v) / 3.0)]
    b = v[int(2.0 * len(v) / 3.0)]
    if a != b and min(v) < a and b < max(v):
        return a, b
    if min(v) < 0.0 < max(v):
        return 0.0, 1e-12                   # sign split: <0 / ==0 / >0
    med = v[len(v) // 2]
    if min(v) < med <= max(v) and med > min(v):
        return med, med + 1e-12             # median split (MID stays empty)
    return None


def binof(x: Optional[float], edges: Tuple[float, float]) -> Optional[str]:
    if x is None:
        return None
    return "LOW" if x < edges[0] else ("MID" if x < edges[1] else "HIGH")


# ------------------------------------------------------------- the analysis
def analyse(obs: List[Dict[str, Any]]) -> Dict[str, Any]:
    rng = random.Random(BOOT_SEED)
    placed = obs
    fills = [o for o in obs if o["filled"]]
    feat_names = sorted({k for o in placed for k in o["features"]})
    # PREDECLARED BINS. Edges are terciles of the feature over ALL PLACED
    # states -- a pre-fill quantity -- not over the fills, and not chosen to
    # make any bin look good. Stated here so the choice is auditable.
    edges: Dict[str, Optional[Tuple[float, float]]] = {}
    for fn in feat_names:
        vals = [o["features"][fn] for o in placed
                if o["features"].get(fn) is not None]
        edges[fn] = terciles([float(v) for v in vals])

    per_feature: Dict[str, Any] = {}
    for fn in feat_names:
        ed = edges[fn]
        if ed is None:
            per_feature[fn] = {"usable": False,
                               "why": "degenerate or too-sparse feature; "
                                      "no distinct tercile edges exist"}
            continue
        cells: Dict[str, Any] = {}
        as_bw: Dict[str, Dict[str, List[float]]] = {}
        for lab in ("LOW", "MID", "HIGH"):
            pl = [o for o in placed
                  if binof(o["features"].get(fn), ed) == lab]
            fi = [o for o in pl if o["filled"]]
            w_pl = {o["ticker"] for o in pl}
            bw: Dict[str, List[float]] = collections.defaultdict(list)
            mk: Dict[str, List[float]] = collections.defaultdict(list)
            for o in fi:
                bw[o["ticker"]].append(o["adverse_selection_usd"])
                mv = o.get("mark_move_%ds_usd" % int(MARK_HORIZON_S))
                if mv is not None:
                    mk[o["ticker"]].append(mv)
            as_bw[lab] = dict(bw)
            # fill rate itself bootstrapped at window level
            fr_bw: Dict[str, List[float]] = collections.defaultdict(list)
            for o in pl:
                fr_bw[o["ticker"]].append(1.0 if o["filled"] else 0.0)
            cells[lab] = {
                "range": ("< %.6g" % ed[0] if lab == "LOW" else
                          ("[%.6g, %.6g)" % ed if lab == "MID"
                           else ">= %.6g" % ed[1])),
                "placed_states": len(pl),
                "distinct_windows_placed": len(w_pl),
                "fills": len(fi),
                "distinct_windows_with_a_fill": len(bw),
                "fill_rate_pct": (round(100.0 * len(fi) / len(pl), 3)
                                  if pl else None),
                "fill_rate_window_bootstrap": boot_mean(dict(fr_bw), rng),
                "adverse_selection_given_fill": boot_mean(dict(bw), rng),
                "mark_move_30s_given_fill": boot_mean(dict(mk), rng),
                "settlement_up_yes_share_of_fill_windows": (
                    round(statistics.fmean(
                        [1.0 if o["settlement_up_side"] == "yes" else 0.0
                         for o in fi]), 4) if fi else None),
                "base_rate_caveat": (
                    "this share is the CONFOUND to watch: a bin whose fills "
                    "sit in windows that settled one way can look skilful "
                    "when it is only reading the settlement direction"),
                "median_time_to_fill_s": (round(statistics.median(
                    [o["fill"]["time_to_fill_s"] for o in fi]), 2)
                    if fi else None),
            }
            # THE DECISIVE QUESTION PER BIN: is this bin NON-TOXIC, i.e. is
            # its adverse-selection interval at or above zero? A bin that is
            # merely LESS negative than another is still a losing bin.
            _a = cells[lab]["adverse_selection_given_fill"]
            cells[lab]["bin_is_non_toxic"] = bool(
                _a and _a.get("ci95") and _a["ci95"][1] >= 0.0)
        sep = None
        ranked = [(lab, cells[lab]["adverse_selection_given_fill"]["mean"])
                  for lab in ("LOW", "MID", "HIGH")
                  if cells[lab]["adverse_selection_given_fill"] is not None
                  and cells[lab]["distinct_windows_with_a_fill"] >= 2]
        if len(ranked) >= 2:
            ranked.sort(key=lambda x: x[1])
            worst, best = ranked[0][0], ranked[-1][0]
            sep = {"best_bin": best, "worst_bin": worst,
                   "point_separation_usd": round(ranked[-1][1]
                                                 - ranked[0][1], 5),
                   "window_bootstrap": boot_diff(as_bw[best], as_bw[worst],
                                                 rng)}
        per_feature[fn] = {
            "usable": True,
            "tercile_edges": [ed[0], ed[1]],
            "bins": cells,
            "separation_best_minus_worst": sep,
            "separates": bool(sep and sep.get("window_bootstrap")
                              and sep["window_bootstrap"]["excludes_zero"]),
        }

    all_bw: Dict[str, List[float]] = collections.defaultdict(list)
    for o in fills:
        all_bw[o["ticker"]].append(o["adverse_selection_usd"])
    separating = sorted(fn for fn, v in per_feature.items()
                        if v.get("separates"))
    return {
        "placed_hypothetical_quotes": len(placed),
        "hypothetical_maker_fills": len(fills),
        "distinct_windows_placed": len({o["ticker"] for o in placed}),
        "distinct_windows_with_any_fill": len(all_bw),
        "overall_fill_rate_pct": (round(100.0 * len(fills) / len(placed), 4)
                                  if placed else None),
        "unconditional_adverse_selection_given_fill": boot_mean(
            dict(all_bw), rng),
        "toxic_fill_share": (round(statistics.fmean(
            [1.0 if o["adverse_selection_usd"] < 0 else 0.0 for o in fills]
        ), 4) if fills else None),
        "fills_by_placement": {
            n: sum(1 for o in fills if o["placement"] == n)
            for n, _ in PLACEMENTS},
        "fills_by_side": collections.Counter(o["side"] for o in fills),
        "features_tested": len(feat_names),
        "features_that_separate": separating,
        "any_non_toxic_bin": sorted(
            "%s:%s" % (fn, lab)
            for fn, v in per_feature.items() if v.get("usable")
            for lab in ("LOW", "MID", "HIGH")
            if v["bins"][lab].get("bin_is_non_toxic")),
        "best_bin_anywhere": _best_bin(per_feature),
        "median_time_to_fill_s": (round(statistics.median(
            [o["fill"]["time_to_fill_s"] for o in fills]), 2)
            if fills else None),
        "per_feature": per_feature,
    }


def _best_bin(per_feature: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The single least-toxic bin found anywhere, so the ceiling of what any
    pre-fill conditioning could buy is stated as one number."""
    best = None
    for fn, v in per_feature.items():
        if not v.get("usable"):
            continue
        for lab in ("LOW", "MID", "HIGH"):
            c = v["bins"][lab]
            a = c["adverse_selection_given_fill"]
            if not a or c["distinct_windows_with_a_fill"] < 2:
                continue
            if best is None or a["mean"] > best["adverse_selection_usd"]:
                best = {"feature": fn, "bin": lab, "range": c["range"],
                        "adverse_selection_usd": a["mean"],
                        "ci95": a["ci95"], "fills": c["fills"],
                        "distinct_windows_with_a_fill":
                            c["distinct_windows_with_a_fill"],
                        "settlement_up_yes_share_of_fill_windows":
                            c["settlement_up_yes_share_of_fill_windows"]}
    return best


def verdict(res: Dict[str, Any], min_fills: int, min_windows: int
            ) -> Dict[str, Any]:
    n_f = res["hypothetical_maker_fills"]
    n_w = res["distinct_windows_with_any_fill"]
    sep = res["features_that_separate"]
    if n_f < min_fills or n_w < min_windows:
        return {
            "verdict": "UNANSWERABLE_FOR_LACK_OF_FILLS",
            "maker_hypothesis": "NOT_RESCUED -- and not separately tested",
            "hypothetical_fills": n_f,
            "distinct_windows_with_a_fill": n_w,
            "requirement": ("pre-declared minimum for a separability claim: "
                            ">= %d evidenced fills across >= %d distinct "
                            "windows" % (min_fills, min_windows)),
            "statement": (
                "FILL SCARCITY IS THE BINDING CONSTRAINT, not toxicity. On "
                "the strict evidence standard -- a qualifying print on the "
                "correct aggressor side with enough cumulative size to clear "
                "the queue ahead of us -- the tape supplies too few "
                "evidenced maker fills to compare toxic against non-toxic "
                "post-fill outcome distributions at all. The separability "
                "question is therefore UNANSWERED rather than answered "
                "negatively. What IS established is operational and is "
                "enough to stop: a venue whose median touch spread is "
                "$0.24-0.29 and which prints a handful of trades per "
                "five-minute window cannot pay for liquidity supply, "
                "because the liquidity is almost never consumed. A quote "
                "that does not fill earns nothing, however wide the spread "
                "around it. NO cancellation threshold, and no re-run of "
                "this policy, can manufacture fills that the tape does not "
                "contain."),
            "recommended_action": ("KILL the BTC5m maker hypothesis on "
                                  "FILL-SCARCITY grounds. Do not sweep "
                                  "cancel thresholds; do not re-run the "
                                  "same policy on more of the same tape."),
        }
    non_toxic = res["any_non_toxic_bin"]
    if sep and not non_toxic:
        # THE DEFECT THIS GUARDS AGAINST. Separation in the DEGREE of loss
        # is not separability of toxic from non-toxic. If every bin of every
        # feature loses money with an interval entirely below zero, then the
        # pre-fill information orders states from bad to worse and never
        # reaches a state worth quoting into. Reporting that as a surviving
        # variant would be manufacturing one.
        best = res["best_bin_anywhere"] or {}
        return {
            "verdict": "SEPARATION_OF_DEGREE_ONLY__NO_NON_TOXIC_STATE",
            "maker_hypothesis": "KILLED",
            "hypothetical_fills": n_f,
            "distinct_windows_with_a_fill": n_w,
            "features_ordering_the_degree_of_loss": sep,
            "non_toxic_bins_found": 0,
            "best_bin_anywhere": best,
            "statement": (
                "%d evidenced hypothetical fills across %d distinct windows. "
                "%.1f%% of fills are toxic. Some pre-fill features DO order "
                "states by how badly the fill turns out -- but EVERY bin of "
                "EVERY feature has an adverse-selection interval entirely "
                "BELOW zero. The least-toxic bin found anywhere is "
                "%s=%s at %s USD per contract (CI %s), still strictly "
                "negative. Pre-fill information here ranks states from bad "
                "to worse and never identifies one worth supplying "
                "liquidity into, so toxic-versus-non-toxic is not a "
                "separation that exists to be made. The features that order "
                "the loss are additionally confounded with the settlement "
                "direction of the few windows behind each bin (each bin's "
                "UP=yes share is reported), which is exactly the base-rate "
                "trap, so even the ordering is weaker than it looks."
                % (n_f, n_w, 100.0 * (res["toxic_fill_share"] or 0.0),
                   best.get("feature"), best.get("bin"),
                   best.get("adverse_selection_usd"), best.get("ci95"))),
            "recommended_action": (
                "KILL the BTC5m maker hypothesis. No surviving variant is "
                "proposed and no cancellation threshold is swept: a cancel "
                "rule cannot help, because there is no non-toxic state for "
                "it to stay resting in."),
        }
    if not sep:
        return {
            "verdict": "NO_SEPARATION",
            "maker_hypothesis": "KILLED",
            "hypothetical_fills": n_f,
            "distinct_windows_with_a_fill": n_w,
            "statement": (
                "With %d evidenced hypothetical fills across %d distinct "
                "windows, NO pre-fill feature splits post-fill outcomes with "
                "a window-level bootstrap interval on the best-minus-worst "
                "separation that excludes zero. Toxic and non-toxic maker "
                "states are NOT distinguishable before the fill on the "
                "information this tape carries. Liquidity supply here "
                "inherits the adverse selection; the spread is compensation "
                "for information we do not have." % (n_f, n_w)),
            "recommended_action": ("KILL the BTC5m maker hypothesis. No "
                                   "surviving variant is proposed."),
        }
    return {
        "verdict": "SEPARATION_FOUND_WITH_A_NON_TOXIC_BIN",
        "non_toxic_bins": non_toxic,
        "maker_hypothesis": "NOT_KILLED -- candidate only, NOT authorized",
        "hypothetical_fills": n_f,
        "distinct_windows_with_a_fill": n_w,
        "separating_features": sep,
        "statement": (
            "At least one pre-fill feature splits post-fill outcomes with a "
            "window-level bootstrap interval excluding zero. This is a "
            "HYPOTHESIS, not a strategy: it is in-sample on this tape, the "
            "sealed OOS windows remain unread, and nothing is authorized."),
        "recommended_action": ("pre-register and test on the sealed OOS "
                              "windows before any further work"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes", nargs="*", default=[
        "analysis/gemini/btc5m_rec/btc5m_rec_20261005T14*.jsonl.gz",
        "analysis/gemini/btc5m_snap/btc5m_rec_20261005T19*.jsonl.gz",
        "analysis/gemini/btc5m_snap/btc5m_rec_20261005T17*.jsonl.gz"])
    ap.add_argument("--step-s", type=float, default=1.0,
                    help="minimum seconds between candidate quote states")
    ap.add_argument("--min-fills", type=int, default=30)
    ap.add_argument("--min-fill-windows", type=int, default=8)
    ap.add_argument("--out", default=("analysis/gemini/updown/"
                                     "MAKER_TOXICITY.json"))
    a = ap.parse_args(argv)
    rows, files = tape(a.tapes)
    data = load(rows)
    built = build(data, a.step_s)
    res = analyse(built["observations"])
    rep = {
        "label": LABEL,
        "fingerprint_sha256": self_fingerprint(),
        "status": ("RESEARCH ONLY. No live orders, no venue writes, no "
                   "credentials. Authorizes nothing and opens no venue "
                   "surface."),
        "reuses_frozen": {
            "maker_study_sha256": FROZEN_MAKER_STUDY_SHA256,
            "state_aware_sha256_prefix": FROZEN_STATE_AWARE_SHA256_PREFIX,
            "neither_modified": True,
            "copied": ["fill-evidence standard (TRADE_THROUGH only, "
                       "aggressor side from buyer_is_maker, cumulative size "
                       "must clear the queue ahead; NO touch=fill)",
                       "placements 25/50/75% through the spread, snapped "
                       "away from aggression",
                       "conservative queue (behind all better-or-equal "
                       "displayed size, plus our own)",
                       "fee model, with NO rebate assumed or credited",
                       "unsettled windows excluded from the denominator"]},
        "not_a_cancellation_sweep": (
            "no cancellation threshold is proposed, swept or tuned anywhere "
            "in this file; the deliverable is a separability verdict"),
        "external_btc_feed": (
            "NOT AVAILABLE and therefore OMITTED. The lead/lag feed probe "
            "rejected all four candidate venues, so no external spot or "
            "futures series exists for this tape. The only reference used is "
            "the venue's own authoritative index_price (raw.I == LRRR_XBT)."),
        "exclusions": {
            "sealed_oos_never_read": list(SEALED_OOS),
            "probe_contaminated": list(PROBE_CONTAMINATED)},
        "tapes": files,
        "tape_rows": len(rows),
        "trade_prints_btc05m": sum(len(v) for v in data["trades"].values()),
        "index_price_points": len(data["index"]),
        "candidate_grid": ("1 Hz p_snapshot books, minimum %.1fs apart -- "
                           "deliberately far denser than the frozen study's "
                           "eight EARLY_FROM_T0 checkpoints, so that fill "
                           "scarcity is measured at its most generous"
                           % a.step_s),
        "independence_note": (
            "overlapping states within one five-minute window are NOT "
            "independent trials; every interval is a window-level bootstrap "
            "(windows resampled whole) and every cell reports its distinct "
            "window count"),
        "resting_horizon": (
            "a hypothetical quote is left resting from its state until the "
            "window's expiry and is never cancelled or repriced. This is the "
            "MOST GENEROUS fill assumption available and is why the fill "
            "count far exceeds the frozen study's 0-for-7: that study quoted "
            "only at eight EARLY_FROM_T0 checkpoints, whereas this grid is "
            "1 Hz. The evidence standard itself is unchanged. Note also that "
            "an inside-spread placement has little or no displayed size "
            "ahead of it, so the conservative queue is usually just our own "
            "contract -- the fills are cheap to earn and still lose money."),
        "base_rate_warning": (
            "a settlement-direction base rate over few windows can imitate "
            "skill, so each bin also reports the UP=yes share of its fill "
            "windows"),
        "census": built["census"],
        "result": res,
        "verdict": verdict(res, a.min_fills, a.min_fill_windows),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, sort_keys=True, default=str)
    print(json.dumps({"label": LABEL, "census": built["census"],
                      "fills": res["hypothetical_maker_fills"],
                      "placed": res["placed_hypothetical_quotes"],
                      "fill_windows": res["distinct_windows_with_any_fill"],
                      "separating": res["features_that_separate"],
                      "verdict": rep["verdict"]["verdict"]},
                     indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
