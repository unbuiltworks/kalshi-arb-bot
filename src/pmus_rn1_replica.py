"""Scaled-down RN1 behavioural replica: the per-market quoting decision.

Owner 2026-11-21: "We want a small-capital behavioral replica of RN1, not a
newly designed sports trading strategy." This module is PURE: given one
market's book and our inventory it returns the resting maker bids RN1's
observable behaviour implies. The sports engine (pmus_sports_two_sided) owns
every side effect -- placement, cancel, fills, locks, halts, caps, watchdog
registry -- and calls `quotes()` when PMUS_TWO_SIDED_RN1=1.

RN1 behaviour reproduced (calibration: analysis/sports/rn1_replica_calibration.json,
from 30 days of RN1's public fills; OBSERVABLE unless marked ESTIMATED):

  * resting maker bids on BOTH outcomes of a live in-game market (88% of RN1
    dollars maker; in-game taker buys lost after fees) -> post-only only.
  * BUY only, never sell, hold to settlement (no SELL/merge rows at all).
  * replenish after a fill: a side re-quotes once REFILL_COOLDOWN_S has passed
    since its last fill (same-side gap p25 54 s, p50 228 s).
  * accumulate rather than hedge at once: the heavier side may keep buying
    (82,891 extend events; extend price - running avg p50 0.00, p75 +0.07)
    while its unmatched cost stays inside RESIDUAL_CAP_USD.
  * complete pairs when the combined cost is profitable: the lighter side is
    bid only where (its price + the heavier side's average) <= PAIR_CAP. RN1
    itself completed at any cost (median 0.995); its completions at >= 1.04
    lost $1.52M and those < 0.96 made $2.39M, so the replica keeps RN1's
    patient "bid the other side and wait for the swing" but only inside the
    profitable band (owner: "favor completing pairs when the combined
    execution cost is profitable"). A completion that is not yet profitable
    rests DEEPER, at the price that would be.
  * bounded unmatched inventory: RESIDUAL_CAP_USD per market.
  * price band: RN1 lost at entry prices < 0.20 (-29.6% under 0.05, -2.4% at
    0.05-0.20); PX_MIN / PX_MAX keep the replica out of those cells.
  * dynamic repricing as the game evolves: targets follow the live touch;
    the engine re-sets a resting bid that is >= REPRICE_TICKS off target.

ESTIMATED (not visible in fills): how far behind the touch RN1 rests
(DEPTH_TICKS), its resting size. Those are settings here, not calibrated facts.

Scaled down, decision logic kept: QTY contracts per order (RN1 median fill
155 shares), PER_MARKET_USD per market, 2-5 markets (engine MAX_MARKETS).
"""
from __future__ import annotations

import os
from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Dict, Mapping, Optional

ONE, ZERO = D(1), D(0)


def _d(name: str, default: str) -> D:
    try:
        return D(str(os.getenv(name, default)).strip())
    except Exception:                                    # noqa: BLE001
        return D(default)


def _f(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def _i(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return int(default)


ON = os.getenv("PMUS_TWO_SIDED_RN1", "0").strip() == "1"
QTY = _d("PMUS_RN1_QTY", "2")            # owner 2026-11-22: frequency before size
DEPTH_TICKS = _i("PMUS_RN1_DEPTH_TICKS", "1")            # ESTIMATED
PX_MIN = _d("PMUS_RN1_PX_MIN", "0.20")
PX_MAX = _d("PMUS_RN1_PX_MAX", "0.92")
PAIR_CAP = _d("PMUS_RN1_PAIR_CAP", "0.97")
RESIDUAL_CAP_USD = _d("PMUS_RN1_RESIDUAL_CAP_USD", "3")
PER_MARKET_USD = _d("PMUS_RN1_PER_MARKET_USD", "8")
EXTEND_MAX_UP = _d("PMUS_RN1_EXTEND_MAX_UP", "0.02")
REFILL_COOLDOWN_S = _f("PMUS_RN1_REFILL_COOLDOWN_S", "60")
REPRICE_TICKS = _i("PMUS_RN1_REPRICE_TICKS", "1")
# CAMPAIGN-WIDE bounds across every RN1 market (owner 2026-11-22: they were
# only implied by MAX_MARKETS x per-market caps, but lone-leg markets can
# exceed MAX_MARKETS). Committed = filled cost + resting orders + the cost
# reserved to complete every unpaired leg at PAIR_CAP.
#: OWNER 2026-11-22: reward-aware inventory accumulation; "the next contract
#: should have a better risk-adjusted expected return, accounting for
#: inventory concentration, reward eligibility, and current market
#: information" -- not a fixed lower-price rule. An ADD to a side that is
#: already unpaired must clear a marginal-edge test against the
#: international fair value:
#:
#:   fair(side) - bid  >=  tick * (EXTEND_EDGE_TICKS
#:                                 + EXTEND_STEP_TICKS * clips already unpaired
#:                                 + extra ticks from the engine)
#:
#: where the engine's extra ticks add the market's jump risk (a goal moves a
#: soccer price 20 ticks at once; Serie A 22 Nov: a resting add was filled
#: 21.5c under water) and subtract a credit when the market carries a
#: verified paying liquidity programme. No fresh fair value -> no add. The
#: bid is never rested deeper to pass the test: a bid far behind the touch
#: fills only on the jump it is meant to avoid. Opening bids from flat and
#: completions are unchanged. All tick values are ESTIMATED settings, to be
#: calibrated from settled fills, not measured constants.
EXTEND_EV = os.getenv("PMUS_RN1_EXTEND_EV", "1").strip() != "0"
EXTEND_EDGE_TICKS = _i("PMUS_RN1_EXTEND_EDGE_TICKS", "1")
EXTEND_STEP_TICKS = _i("PMUS_RN1_EXTEND_STEP_TICKS", "1")
TOTAL_SPEND_USD = _d("PMUS_RN1_TOTAL_SPEND_USD", "16")
TOTAL_UNMATCHED_USD = _d("PMUS_RN1_TOTAL_UNMATCHED_USD", "6")
# Maker fee per contract = rate * p * (1 - p). PMUS order previews show
# makerCommissionsBasisPoints=0 (2026-11-21), so the default is 0; a nonzero
# schedule is charged against PAIR_CAP for openings AND completions.
MAKER_FEE_RATE = _d("PMUS_RN1_MAKER_FEE_RATE", "0")


def cfg() -> Dict[str, Any]:
    return {"qty": str(QTY), "depth_ticks": DEPTH_TICKS,
            "px_min": str(PX_MIN), "px_max": str(PX_MAX),
            "pair_cap": str(PAIR_CAP),
            "residual_cap_usd": str(RESIDUAL_CAP_USD),
            "per_market_usd": str(PER_MARKET_USD),
            "extend_max_up": str(EXTEND_MAX_UP),
            "refill_cooldown_s": REFILL_COOLDOWN_S,
            "reprice_ticks": REPRICE_TICKS,
            "maker_fee_rate": str(MAKER_FEE_RATE),
            "total_spend_usd": str(TOTAL_SPEND_USD),
            "total_unmatched_usd": str(TOTAL_UNMATCHED_USD)}


def _floor(x: D, tick: D) -> D:
    return (x / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def fee(px: D, rate: D) -> D:
    """Maker fee per contract at price px."""
    return rate * px * (ONE - px)


def side_bid(side: str, yes_bid: D, yes_ask: D) -> D:
    """Best bid for an outcome: YES = yes_bid, NO = 1 - yes_ask."""
    return yes_bid if side == "YES" else ONE - yes_ask


def quotes(*, yes_bid: Any, yes_ask: Any, tick: Any,
           pos: Mapping[str, Any], cost: Mapping[str, Any],
           last_fill_t: Optional[Mapping[str, Any]] = None,
           now: float = 0.0, min_qty: Any = 1,
           c: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """{"YES": {...} | None, "NO": {...} | None}; each live entry is
    {"px", "qty", "role" (open|extend|complete), "why"} and a None side
    carries its reason under "why_<SIDE>". Never a sell, never a taker."""
    k = dict(c or {})
    qty0 = D(str(k.get("qty", QTY)))
    depth = int(k.get("depth_ticks", DEPTH_TICKS))
    pmin = D(str(k.get("px_min", PX_MIN)))
    pmax = D(str(k.get("px_max", PX_MAX)))
    pcap = D(str(k.get("pair_cap", PAIR_CAP)))
    rcap = D(str(k.get("residual_cap_usd", RESIDUAL_CAP_USD)))
    mcap = D(str(k.get("per_market_usd", PER_MARKET_USD)))
    up = D(str(k.get("extend_max_up", EXTEND_MAX_UP)))
    cool = float(k.get("refill_cooldown_s", REFILL_COOLDOWN_S))
    frate = D(str(k.get("maker_fee_rate", MAKER_FEE_RATE)))
    t = D(str(tick))
    yb, ya = D(str(yes_bid)), D(str(yes_ask))
    mq = D(str(min_qty))
    p = {s: D(str(pos.get(s) or 0)) for s in ("YES", "NO")}
    cs = {s: D(str(cost.get(s) or 0)) for s in ("YES", "NO")}
    lf = dict(last_fill_t or {})
    spent = cs["YES"] + cs["NO"]
    out: Dict[str, Any] = {"YES": None, "NO": None}
    if not (ZERO < yb < ya < ONE):
        out["why"] = "no_two_sided_book"
        return out
    for s, o in (("YES", "NO"), ("NO", "YES")):
        touch = side_bid(s, yb, ya)
        px = _floor(touch - depth * t, t)
        role, why = None, None
        if p[o] > p[s]:
            # COMPLETING the lighter side: profitable pair only, patient
            avg_o = cs[o] / p[o]
            need = p[o] - p[s]
            ceiling = _floor(pcap - avg_o, t)
            while ceiling > ZERO and ceiling + fee(ceiling, frate) + avg_o \
                    > pcap:
                ceiling -= t                    # fee-inclusive pair cap
            if px > ceiling:
                px = ceiling                    # rest where it WOULD pay
            qty = min(qty0, need)
            role = "complete"
            if qty < mq:
                qty = mq                        # venue minimum; tiny overfill
        else:
            # OPEN (flat) or EXTEND (heavier / equal side): accumulate,
            # bounded by unmatched cost; replenish after a cool-down
            qty = qty0
            role = "open" if p[s] == 0 else "extend"
            last = lf.get(s)
            if last is not None and now - float(last) < cool:
                out["why_" + s] = "refill_cooldown"
                continue
            if p[s] > 0:
                avg_s = cs[s] / p[s]
                if px > avg_s + up:
                    px = _floor(avg_s + up, t)  # don't chase it up
            unpaired = p[s] - p[o]
            if unpaired > ZERO and k.get("extend_ev"):
                # marginal economics of adding to an already unpaired side
                fy = k.get("fair_yes")
                if fy is None:
                    out["why_" + s] = "extend_no_fair"
                    continue
                fair_s = D(str(fy)) if s == "YES" else ONE - D(str(fy))
                clips = int((unpaired / qty0).to_integral_value(
                    rounding="ROUND_CEILING")) if qty0 > 0 else 1
                ticks = int(k.get("extend_edge_ticks", EXTEND_EDGE_TICKS)) \
                    + int(k.get("extend_step_ticks",
                                EXTEND_STEP_TICKS)) * clips \
                    + int(k.get("extend_extra_ticks", 0))
                need = t * max(1, ticks)
                if fair_s - px < need:
                    out["why_" + s] = "extend_marginal_edge"
                    out.setdefault("extend_ev", {})[s] = {
                        "fair": str(fair_s), "px": str(px),
                        "edge": str(fair_s - px), "need": str(need),
                        "unpaired_clips": clips}
                    continue
            # unpaired cost if this fills: excess shares at the new average
            new_avg = (cs[s] + px * qty) / (p[s] + qty)
            unm_after = max(ZERO, p[s] + qty - p[o]) * new_avg
            if unm_after > rcap:
                out["why_" + s] = "residual_cap"
                continue
        if px < pmin or px > pmax:
            out["why_" + s] = "price_band"
            continue
        if px >= touch + t:
            out["why_" + s] = "would_cross"
            continue
        if role != "complete":
            # an opening/extension must leave room to COMPLETE what it
            # adds: filled cost + this bid + the reserve to pair every
            # unpaired share at PAIR_CAP. A completion is never refused by
            # the market cap -- it reduces risk and its cost was reserved.
            q_after = p[s] + qty - p[o]
            avg_after = (cs[s] + px * qty) / (p[s] + qty)
            reserve = max(ZERO, q_after) * max(ZERO, pcap - avg_after)
            if spent + px * qty + reserve > mcap:
                out["why_" + s] = "per_market_cap"
                continue
        out[s] = {"px": px, "qty": qty, "role": role, "why": why}
    # SIMULTANEOUS QUOTES (owner 2026-11-21): when neither side is a
    # completion, both resting bids can fill together, so their pair --
    # fees included -- must also fit PAIR_CAP. Step the dearer side down a
    # tick at a time; a side pushed out of the band is dropped (the other
    # still rests alone, as RN1 often does).
    y, n = out["YES"], out["NO"]
    if y and n and y["role"] != "complete" and n["role"] != "complete":
        def pair_cost():
            return (y["px"] + fee(y["px"], frate) +
                    n["px"] + fee(n["px"], frate))
        while out["YES"] and out["NO"] and pair_cost() > pcap:
            dear = y if y["px"] >= n["px"] else n
            dear["px"] = dear["px"] - t
            dear["why"] = "pair_cap_repriced"
            if dear["px"] < pmin:
                side = "YES" if dear is y else "NO"
                out[side] = None
                out["why_" + side] = "pair_cap"
    # PER-MARKET cap on BOTH resting bids together (owner 2026-11-22:
    # filled inventory and resting commitments count together -- both can
    # fill). A completion keeps priority; the other side is dropped.
    live = [sd for sd in ("YES", "NO") if out[sd]]
    if len(live) == 2 and spent + sum(out[sd]["px"] * out[sd]["qty"]
                                      for sd in live) > mcap:
        drop = next((sd for sd in live if out[sd]["role"] != "complete"),
                    None)
        if drop is None or all(out[sd]["role"] != "complete" for sd in live):
            drop = max(live, key=lambda sd: out[sd]["px"])
        out[drop] = None
        out["why_" + drop] = "per_market_cap"
    return out


def committed(pos: Mapping[str, Any], cost: Mapping[str, Any],
              resting: Any = (), pcap: Optional[D] = None) -> Dict[str, D]:
    """One market's WORST-CASE committed dollars, exactly: over every way its
    resting bids could fill (none / YES side only / NO side only / both),
    the cash it would have spent plus the cost reserved to complete what is
    then unpaired at PAIR_CAP (`spend`), and the unpaired cost it would hold
    (`unmatched`). `resting` holds (side, px, qty_left, is_completion)
    tuples; the flag is accepted for callers but the worst case does not
    depend on it (a resting bid fills or it does not).

    LIVE-TEST 2026-11-22: the earlier formula left out the completion
    reserve of a resting EXTENSION (YES 2 filled @0.44 + YES 2 resting @0.44:
    true worst case $3.88, counted $2.82) and double-counted a lighter-side
    opening bid that acts as the completion."""
    pc = PAIR_CAP if pcap is None else D(str(pcap))
    base_q = {s: D(str(pos.get(s) or 0)) for s in ("YES", "NO")}
    base_c = {s: D(str(cost.get(s) or 0)) for s in ("YES", "NO")}
    add_q = {"YES": ZERO, "NO": ZERO}
    add_c = {"YES": ZERO, "NO": ZERO}
    for side, px, left, _is_c in resting:
        sd = "YES" if str(side).upper() == "YES" else "NO"
        add_q[sd] += D(str(left))
        add_c[sd] += D(str(px)) * D(str(left))
    worst_s = worst_u = ZERO
    for fy in (False, True):
        for fn in (False, True):
            q = {"YES": base_q["YES"] + (add_q["YES"] if fy else ZERO),
                 "NO": base_q["NO"] + (add_q["NO"] if fn else ZERO)}
            c = {"YES": base_c["YES"] + (add_c["YES"] if fy else ZERO),
                 "NO": base_c["NO"] + (add_c["NO"] if fn else ZERO)}
            unm = unmatched_usd(q, c)
            reserve = ZERO
            if q["YES"] != q["NO"]:
                h = "YES" if q["YES"] > q["NO"] else "NO"
                avg = c[h] / q[h]
                reserve = abs(q["YES"] - q["NO"]) * max(ZERO, pc - avg)
            worst_s = max(worst_s, c["YES"] + c["NO"] + reserve)
            worst_u = max(worst_u, unm)
    return {"spend": worst_s, "unmatched": worst_u}


def unmatched_usd(pos: Mapping[str, Any], cost: Mapping[str, Any]) -> D:
    """Cost of the unpaired shares (heavier side at its average)."""
    y, n = D(str(pos.get("YES") or 0)), D(str(pos.get("NO") or 0))
    if y == n:
        return ZERO
    s = "YES" if y > n else "NO"
    q = abs(y - n)
    return q * D(str(cost.get(s) or 0)) / D(str(pos.get(s) or 1))
