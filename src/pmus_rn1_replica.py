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
# SIZE DOWN TO FIT (owner 2026-11-25: "If two contracts exceed available
# capacity but one fits, evaluate one instead of refusing the entire
# addition"). With 1, an opening or extension bid that would breach the
# per-market unpaired cap or the per-market spend cap at the full clip is
# tried at each smaller whole size down to the venue minimum; the largest
# size that fits is bid. The caps themselves are unchanged. 0 = refuse the
# whole bid, as before.
FIT_QTY = os.getenv("PMUS_RN1_FIT_QTY", "0").strip() == "1"
# A BALANCED POSITION IS A FRESH ENTRY (owner 2026-11-25: "Separate
# fresh-entry restrictions from genuine same-side inventory extensions").
# Without this, a market holding 3 YES and 3 NO (every pair complete,
# nothing unpaired) is treated as an extension: its next bid is capped at
# the LIFETIME average price of that side + EXTEND_MAX_UP, so after the
# market moves the bot cannot quote it again. With 1, equal YES and NO is
# role "open" with no such ceiling; the ceiling applies only to the side
# that is already heavier (a real same-side addition). Every cap, the
# cooldown and the pair-cost limit are unchanged. 0 = as before.
BALANCED_IS_OPEN = os.getenv("PMUS_RN1_BALANCED_IS_OPEN",
                             "0").strip() == "1"
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
#: OWNER 2026-11-22 (issue #251): profit first. "Trading P&L before
#: incentives must be the primary economic objective"; pair completion is
#: incidental; "Opposite-side orders must independently pass economic and
#: risk checks"; "Where a reliable fair value cannot be established, fail
#: closed on additional risk"; "Reward assumptions must not turn
#: demonstrably negative trading economics into a falsely attractive
#: opportunity."
#:
#: THE MARGINAL TEST, for EVERY bid -- opening, same-side add and
#: opposite-side bid alike. Per contract, in price units:
#:
#:   trade edge = fair value of the side
#:                - bid - maker fee at the bid
#:                - adverse selection (what a resting bid loses, on average,
#:                  by being the one that got filled)
#:   risk       = RISK_STEP_TICKS x clips of this side already unpaired
#:                (only for a bid that ADDS to the net position)
#:   incentive  = a verified, qualifying reward/rebate credit -- capped at
#:                the risk allowance, so it can pay for carrying inventory
#:                but can never make a negative trade edge pass
#:
#:   quoted only when   trade edge >= 0   and   trade edge >= risk - incentive
#:
#: No fresh fair value -> no bid of any kind. A bid is never rested deeper
#: to pass: a bid far behind the touch fills only on the move it is meant
#: to avoid. The pair cap, price band, residual, per-market and campaign
#: caps are limits and stay exactly as they are; this adds a test, it does
#: not remove one. With it on, a lighter-side bid is no longer quoted just
#: because the pair would be profitable.
#:
#: ALLOWANCES ARE COUNTED IN STEPS OF AT LEAST ONE CENT, not in the market's
#: tick. NFL markets tick in quarter cents (live slate, 22 Nov): "1 tick"
#: there would be a quarter of a cent, a fraction of any measured
#: adverse selection. One step = max(tick, EDGE_UNIT_USD).
#:
#: WHERE 1 STEP OF ADVERSE SELECTION COMES FROM (analysis/l5/markout, this
#: bot's own 1,016 fills of 14-18 Nov, estimated from its own two-sided
#: quotes): in the liquid, internationally priced sports the mid 30 s
#: after a fill was within half a cent of where it stood when the bid was
#: placed (college football +0.5c [+0.1,+0.9] against the fill, n 248;
#: NBA -0.1c); in thin sports with no international price it was 2-4c
#: worse (ITF -2.7c, table tennis -3.0c, Dota 2 -3.9c) -- and those have no
#: fair value here, so they get no bid at all. The reference trader's
#: resting fills lose 0.2-0.5c to later sellers and keep about 0.3c.
#: Every fill is now measured (FILL_MARKOUT); these stay SETTINGS until
#: the gated controller's own fills say otherwise.
EDGE_GATE = os.getenv("PMUS_RN1_EDGE_GATE", "1").strip() != "0"
ADVERSE_TICKS = _i("PMUS_RN1_ADVERSE_TICKS", "1")
RISK_STEP_TICKS = _i("PMUS_RN1_RISK_STEP_TICKS", "1")
EDGE_UNIT_USD = _d("PMUS_RN1_EDGE_UNIT_USD", "0.01")
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
            "edge_gate": EDGE_GATE, "adverse_ticks": ADVERSE_TICKS,
            "risk_step_ticks": RISK_STEP_TICKS,
            "edge_unit_usd": str(EDGE_UNIT_USD),
            "extend_ev": EXTEND_EV,
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


def edge(*, side: str, px: Any, tick: Any, fair_yes: Any,
         adds_exposure: bool, unpaired_clips: int = 0,
         fee_rate: Any = ZERO, adverse_ticks: Any = None,
         risk_step_ticks: Any = None, incentive_ticks: Any = 0
         ) -> Dict[str, Any]:
    """The marginal economics of ONE more bid at `px` on `side`. Pure.

    {"ok", "why", "fair", "px", "fee", "adverse", "trade_edge", "risk",
     "incentive", "net_edge"} -- every figure per contract, in price units,
    as strings. `incentive` is the credit actually applied (never more than
    `risk`). `ok` is False with why "no_fair" when there is no fair value.
    The "ticks" of every allowance are steps of max(tick, EDGE_UNIT_USD).
    """
    t = max(D(str(tick)), EDGE_UNIT_USD)       # one allowance step
    p = D(str(px))
    if fair_yes is None:
        return {"ok": False, "why": "no_fair", "px": str(p)}
    fy = D(str(fair_yes))
    if not (ZERO < fy < ONE):
        return {"ok": False, "why": "no_fair", "px": str(p)}
    fair_s = fy if side == "YES" else ONE - fy
    adv = t * D(str(ADVERSE_TICKS if adverse_ticks is None
                    else adverse_ticks))
    f = fee(p, D(str(fee_rate)))
    trade = fair_s - p - f - max(ZERO, adv)
    risk = ZERO
    if adds_exposure:
        risk = t * D(str(RISK_STEP_TICKS if risk_step_ticks is None
                         else risk_step_ticks)) * max(0, int(unpaired_clips))
    inc = min(max(ZERO, t * D(str(incentive_ticks or 0))), risk)
    net = trade - risk + inc
    why = None
    if trade < ZERO:
        why = "edge_negative"        # incentives are not consulted at all
    elif net < ZERO:
        why = "edge_below_risk"
    return {"ok": why is None, "why": why, "fair": str(fair_s),
            "px": str(p), "fee": str(f), "adverse": str(max(ZERO, adv)),
            "trade_edge": str(trade), "risk": str(risk),
            "incentive": str(inc), "net_edge": str(net),
            "unpaired_clips": int(unpaired_clips),
            "adds_exposure": bool(adds_exposure), "step": str(t)}


def marginal_ev(*, fair: Any, px: Any, qty: Any, fill_rate: Any,
                fee: Any = ZERO, gate_adverse: Any = ZERO,
                adverse: Any = ZERO, risk: Any = ZERO,
                reward_usd_per_hour: Any = 0.0,
                rebate: Any = ZERO) -> Dict[str, Any]:
    """Combined marginal net value of ONE proposed opening/extension bid,
    in dollars per resting hour. Pure. (Engine flag PMUS_RN1_MARGINAL_EV.)

      per contract filled = fair - px - fee - adverse - risk + rebate
      trading per hour    = fill_rate x qty x per contract filled
      total per hour      = trading per hour + incremental reward per hour

    `adverse` is the adverse-selection cost charged per filled contract;
    `gate_adverse` is the marginal test's own allowance. A bid whose
    trade edge at the marginal test's allowance (fair - px - fee -
    gate_adverse) is negative is refused whatever its reward: rewards never
    rescue a negative trade edge. Otherwise it is accepted only when the
    TOTAL is positive, so a zero standalone reward does not reject an
    attractive bid and a positive reward cannot carry a negative total.
    `rebate` is per contract; callers pass 0 unless a rebate is sourced."""
    f, p, q = D(str(fair)), D(str(px)), D(str(qty))
    fe, adv, gadv = D(str(fee)), D(str(adverse)), D(str(gate_adverse))
    rk, rb = D(str(risk)), D(str(rebate))
    trade_edge = f - p - fe - gadv
    per_contract = f - p - fe - adv - rk + rb
    fills_h = float(fill_rate) * float(q)
    trading_h = fills_h * float(per_contract)
    rew_h = max(0.0, float(reward_usd_per_hour or 0.0))
    total = trading_h + rew_h
    why = None
    if trade_edge < ZERO:
        why = "trade_edge_negative"
    elif total <= 0.0:
        why = "marginal_ev_not_positive"
    return {"ok": why is None, "why": why, "fair": str(f), "px": str(p),
            "qty": str(q), "fee": str(fe), "adverse": str(adv),
            "risk": str(rk), "rebate": str(rb),
            "trade_edge": str(trade_edge),
            "per_contract": str(per_contract),
            "fills_per_hour": round(fills_h, 6),
            "trading_usd_per_hour": round(trading_h, 6),
            "reward_usd_per_hour": round(rew_h, 6),
            "ev_usd_per_hour": round(total, 6)}


def concentration_risk(*, tick: Any, unpaired_clips: int,
                       risk_step_ticks: Any = None) -> D:
    """Concentration/risk charge per contract for an exposure-adding bid,
    exactly as `edge` computes it (one step = max(tick, EDGE_UNIT_USD),
    times RISK_STEP_TICKS, times the unpaired clips already held). Pure."""
    t = max(D(str(tick)), EDGE_UNIT_USD)
    return t * D(str(RISK_STEP_TICKS if risk_step_ticks is None
                     else risk_step_ticks)) * max(0, int(unpaired_clips))


def marginal_ev_no_fair(*, px: Any, qty: Any, fill_rate: Any,
                        fill_loss: Any, fee: Any = ZERO, risk: Any = ZERO,
                        reward_usd_per_hour: Any = 0.0,
                        rebate: Any = ZERO) -> Dict[str, Any]:
    """Marginal net value of ONE opening/extension bid in a market with NO
    fair value, in dollars per resting hour. Pure. (Engine flag
    PMUS_RN1_MARGINAL_EV_NO_FAIR.)

      cost per contract filled = fill_loss + fee + risk - rebate
      total per hour = reward per hour - fill_rate x qty x cost per contract

    THERE IS NO TRADE-EDGE TERM: without a fair value there is nothing to
    measure the price against, and none is invented (not the venue's own
    mid, not a model). `fill_loss` (RN1_FILL_LOSS for the placement and
    competition, the same settings cost the market-level net estimate
    charges) is the adverse-selection proxy for what a fill costs. A bid is
    accepted only when the TOTAL is positive, so only a qualifying
    incremental reward larger than its expected fill cost carries a bid;
    with no reward every bid is refused. `rebate` is per contract; callers
    pass 0 unless a rebate is sourced."""
    p, q = D(str(px)), D(str(qty))
    fe, ls, rk, rb = (D(str(fee)), max(ZERO, D(str(fill_loss))),
                      D(str(risk)), D(str(rebate)))
    cost = ls + fe + rk - rb
    fills_h = float(fill_rate) * float(q)
    cost_h = fills_h * float(cost)
    rew_h = max(0.0, float(reward_usd_per_hour or 0.0))
    total = rew_h - cost_h
    why = None if total > 0.0 else "marginal_ev_not_positive"
    return {"ok": why is None, "why": why, "basis": "no_fair",
            "fair": None, "px": str(p), "qty": str(q), "fee": str(fe),
            "adverse": str(ls), "risk": str(rk), "rebate": str(rb),
            "trade_edge": None, "per_contract": str(-cost),
            "fills_per_hour": round(fills_h, 6),
            "trading_usd_per_hour": round(-cost_h, 6),
            "reward_usd_per_hour": round(rew_h, 6),
            "ev_usd_per_hour": round(total, 6)}


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
            fresh = p[s] == 0 or (p[s] == p[o] and bool(
                k.get("balanced_is_open", BALANCED_IS_OPEN)))
            role = "open" if fresh else "extend"
            last = lf.get(s)
            if last is not None and now - float(last) < cool:
                out["why_" + s] = "refill_cooldown"
                continue
            # Opt-in: let the independent marginal edge gate price additions.
            # Without that gate, retain the legacy average-cost ceiling.
            independent = bool(k.get("edge_gate")) and bool(k.get(
                "independent_adds", os.getenv("PMUS_RN1_INDEPENDENT_ADDS", "0") == "1"))
            if p[s] > 0 and not fresh and not independent:
                avg_s = cs[s] / p[s]
                if px > avg_s + up:
                    px = _floor(avg_s + up, t)  # don't chase it up
            unpaired = p[s] - p[o]
            if unpaired > ZERO and k.get("extend_ev") \
                    and not k.get("edge_gate"):
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
            if k.get("fit_qty", FIT_QTY):
                # largest whole size, down to the venue minimum, that keeps
                # both per-market caps (unpaired, spend incl. reserve)
                def _fits(qq: D) -> bool:
                    na = (cs[s] + px * qq) / (p[s] + qq)
                    if max(ZERO, p[s] + qq - p[o]) * na > rcap:
                        return False
                    qa = p[s] + qq - p[o]
                    res = max(ZERO, qa) * max(ZERO, pcap - na)
                    return spent + px * qq + res <= mcap
                qq = qty
                while qq > mq and not _fits(qq):
                    qq = max(mq, qq - ONE)
                if _fits(qq):
                    qty = qq
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
        if k.get("edge_gate"):
            # THE MARGINAL TEST (see EDGE_GATE). A bid on the lighter side
            # reduces the net position, so it carries no concentration
            # charge; it still has to be worth buying on its own.
            adds = role != "complete"
            held = max(ZERO, p[s] - p[o])
            clips = int((held / qty0).to_integral_value(
                rounding="ROUND_CEILING")) if (adds and qty0 > 0) else 0
            adv = k.get("adverse_ticks")
            if isinstance(adv, Mapping):
                adv = adv.get(role, adv.get("default"))
            # THE FAIR VALUE EACH SIDE IS JUDGED AGAINST is the one LEAST
            # favourable to it over the engine's stability period: a YES
            # bid against the lowest recent fair, a NO bid against the
            # highest (see the engine's intl_fair_band). A caller that
            # passes only `fair_yes` gets that single value for both.
            _fy = k.get("fair_yes")
            if _fy is not None:
                _fy = k.get("fair_yes_lo" if s == "YES" else "fair_yes_hi",
                            _fy)
            ev = edge(side=s, px=px, tick=t, fair_yes=_fy,
                      adds_exposure=adds, unpaired_clips=clips,
                      fee_rate=frate, adverse_ticks=adv,
                      risk_step_ticks=k.get("risk_step_ticks"),
                      incentive_ticks=k.get("incentive_ticks", 0))
            ev["role"] = role
            out.setdefault("edge", {})[s] = ev
            if not ev["ok"]:
                # HYSTERESIS (owner 2026-11-23: "retains economically
                # acceptable orders without allowing stale or unprofitable
                # quotes to persist"). A NEW bid needs the full test. A bid
                # ALREADY RESTING on this side is kept at its own price
                # while it misses the test by no more than `keep_tol_usd`:
                # the fair value sitting on a half-cent boundary otherwise
                # cancels and re-places the same order every few seconds
                # and gives up its place in the queue. It is still judged
                # against a fair value read now (none = pulled), still has
                # to be worth more than it costs, and the caller limits how
                # long a bid may be kept this way.
                rp = (k.get("keep_px") or {}).get(s)
                tol = D(str(k.get("keep_tol_usd") or 0))
                kept = None
                if rp is not None and tol > ZERO:
                    rp = D(str(rp))
                    if pmin <= rp <= pmax and rp < touch + t:
                        ev2 = edge(side=s, px=rp, tick=t, fair_yes=_fy,
                                   adds_exposure=adds, unpaired_clips=clips,
                                   fee_rate=frate, adverse_ticks=adv,
                                   risk_step_ticks=k.get("risk_step_ticks"),
                                   incentive_ticks=k.get("incentive_ticks",
                                                         0))
                        if ev2.get("net_edge") is not None and \
                                D(ev2["net_edge"]) >= -tol and \
                                D(ev2["trade_edge"]) >= -tol and \
                                D(ev2["fair"]) - rp - D(ev2["fee"]) > ZERO:
                            kept = ev2
                if kept is None:
                    out["why_" + s] = ev["why"]
                    continue
                # a resting price that passes the full test on its own is
                # simply kept; only a bid inside the tolerance is on the
                # caller's clock
                within = not kept["ok"]
                kept.update(role=role, kept=within, missed=ev["why"])
                out["edge"][s] = kept
                px = rp
                why = "kept_within_tolerance" if within \
                    else "resting_price_passes"
        out[s] = {"px": px, "qty": qty, "role": role, "why": why}
        if why == "kept_within_tolerance":
            out[s]["kept"] = True
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
            ev = out.get("edge") or {}
            if k.get("ev_priority") and all(
                    (ev.get(sd) or {}).get("net_edge") is not None
                    for sd in live):
                # ECONOMIC PRIORITY (engine PMUS_RN1_MARGINAL_EV): drop the
                # bid worth less per fill (net edge x size), not the dearer
                # one; ties keep the old rule.
                val = {sd: D(ev[sd]["net_edge"]) * out[sd]["qty"]
                       for sd in live}
                lo = min(val.values())
                low = [sd for sd in live if val[sd] == lo]
                if len(low) == 1:
                    drop = low[0]
        if k.get("fit_pair_cap"):
            # SIZE DOWN BEFORE DROPPING (engine PMUS_RN1_FIT_ALL_LEVELS):
            # the dropped bid is first tried at each smaller whole size
            # down to the venue minimum; the cap itself is unchanged.
            def _over() -> bool:
                return spent + sum(out[sd]["px"] * out[sd]["qty"]
                                   for sd in live) > mcap
            q0 = out[drop]["qty"]
            while _over() and out[drop]["qty"] - ONE >= mq:
                out[drop]["qty"] -= ONE
            if not _over():
                if out[drop]["qty"] != q0:
                    out[drop]["why"] = "per_market_cap_sized"
                return out
            out[drop]["qty"] = q0
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
