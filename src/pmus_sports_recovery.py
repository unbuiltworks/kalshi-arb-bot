"""Inventory recovery decision engine for the sports two-sided bot.

OWNER 2026-11-15: "A first-leg fill must never be forgotten, left unmanaged,
or automatically treated as a loss." Every unmatched position is evaluated
continuously and ONE decision function compares every recovery action.

VENUE FACT THAT SHAPES THIS (PMUS nets positions per market): buying NO
while long YES CLOSES YES -- the venue books it as a sale with realizedPnl
on the trade row (l5_live_execution's reconcile notes, the settlement
auditor). So SELL_TAKER (sell YES at the YES bid) and COMPLETE_TAKER (buy
NO at the NO ask = 1 - YES bid) are the SAME order with the SAME economics;
SELL_MAKER and COMPLETE_MAKER likewise. They are scored as aliases, never
as different trades.

VALUATION. Everything is measured relative to HOLDING the unmatched leg at
the market's mid. No directional skill is assumed (CLAUDE.md: residual
exposure has no demonstrated edge), so holding is worth 0 in expectation
but carries a RISK COST that grows with age, lateness and the binary's
variance m(1-m). The lead leg's purchase price is SUNK: it never changes a
decision, except through a hard worst-case lock-loss limit that guards
against bad data, not against regret.

    taker  = qty * (risk - halfspread - fee)
    maker  = qty * P(fill) * (comp_mid - p - adverse + risk)
    hold   = 0

Martingale guards: the complement is never larger than the unmatched
quantity (no flip, no averaging), ADD_COMPLEMENTARY_INVENTORY beyond that
is always rejected, and no action may exceed the hard limits. Every number
is a model input logged with the decision; the constants are deliberately
simple and are to be calibrated from the recovery records the bot keeps.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, Decimal as D
from typing import Any, Dict, Mapping, Optional

ONE = D(1)

ACTIONS = ("COMPLETE_MAKER", "REPRICE_COMPLETION", "COMPLETE_TAKER",
           "SELL_MAKER", "SELL_TAKER", "PARTIAL_REBALANCE",
           "ADD_COMPLEMENTARY_INVENTORY", "HOLD", "HALT_NEW_EXPOSURE")

DEFAULTS = {
    # risk cost per contract = RISK_K * m(1-m) * urgency
    "risk_k": D("0.04"),
    # urgency = (1 + age / age_scale_s) * (late_mult if late)
    "age_scale_s": D("300"),
    "late_mult": D("3"),
    # maker fill probabilities over the decision horizon
    "p_fill_at_bid": D("0.35"),
    "p_fill_inside": D("0.60"),
    "p_fill_decay_per_tick": D("0.6"),
    # expected adverse move on a maker fill, in ticks
    "adverse_ticks": D("1"),
    # hard worst-case realized loss per contract when locking (bad-data guard)
    "max_lock_loss": D("0.15"),
    # minimum improvement (USD per contract) before moving a resting order
    "reprice_min_usd": D("0.005"),
    "taker_fee_rate": D("0.06"),
}


def taker_fee_pc(px: D, qty: D, rate: D) -> D:
    if qty <= 0:
        return D(0)
    raw = rate * qty * px * (ONE - px)
    return raw.quantize(D("0.01"), rounding=ROUND_CEILING) / qty


def complement_book(lead: str, bid: D, ask: D) -> Dict[str, D]:
    """The complement side's own bid/ask/mid, from the YES book."""
    if lead == "YES":                    # complement is NO
        cb, ca = ONE - ask, ONE - bid
    else:                                # complement is YES
        cb, ca = bid, ask
    return {"bid": cb, "ask": ca, "mid": (cb + ca) / 2}


def decide(*, lead: str, lead_px: D, need: D, bid: Optional[D],
           ask: Optional[D], tick: D, age_s: float, late: bool,
           resting_px: Optional[D] = None, taker_allowed: bool = False,
           taker_chunk: Optional[D] = None, min_qty: D = ONE,
           params: Optional[Mapping[str, D]] = None) -> Dict[str, Any]:
    """One decision for one unmatched position. Pure."""
    p = dict(DEFAULTS)
    p.update(params or {})
    out: Dict[str, Any] = {"action": "HOLD", "px": None, "qty": D(0),
                           "scores": {}, "reason": ""}
    if need <= 0:
        out["reason"] = "flat"
        return out
    if bid is None or ask is None or ask <= bid:
        # liquidity gone or book unusable: keep whatever rests, add nothing
        out["action"] = "KEEP" if resting_px is not None else "HOLD"
        out["px"] = resting_px
        out["reason"] = "no_usable_book"
        return out
    cb = complement_book(lead, D(bid), D(ask))
    yes_mid = (D(bid) + D(ask)) / 2
    m = yes_mid if lead == "YES" else ONE - yes_mid      # P(lead wins)
    urgency = (ONE + D(str(age_s)) / p["age_scale_s"]) * \
        (p["late_mult"] if late else ONE)
    risk = p["risk_k"] * m * (ONE - m) * urgency
    lock_cap = ONE + p["max_lock_loss"] - D(lead_px)     # max complement px
    scores: Dict[str, Any] = {}

    # --- taker / sell-taker (the same venue order)
    tq = need if taker_chunk is None else min(need, taker_chunk)
    if taker_allowed and tq >= min_qty and cb["ask"] <= lock_cap:
        fee = taker_fee_pc(cb["ask"], tq, p["taker_fee_rate"])
        v = tq * (risk - (cb["ask"] - cb["mid"]) - fee)
        name = "COMPLETE_TAKER" if tq == need else "PARTIAL_REBALANCE"
        scores[name] = {"value": v, "px": cb["ask"], "qty": tq, "fee_pc": fee}
        scores["SELL_TAKER"] = {"alias_of": name, "value": v}
    else:
        scores["COMPLETE_TAKER"] = {"value": None, "why": (
            "not_authorized" if not taker_allowed else
            "over_lock_loss_cap" if cb["ask"] > lock_cap else "below_min")}

    # --- maker candidates: inside the spread, at the bid, one tick under
    cands = []
    if cb["ask"] - cb["bid"] > tick:
        cands.append((cb["bid"] + tick, p["p_fill_inside"]))
    cands.append((cb["bid"], p["p_fill_at_bid"]))
    cands.append((cb["bid"] - tick, p["p_fill_at_bid"] *
                  p["p_fill_decay_per_tick"]))
    best_mk = None
    for px, pf in cands:
        if px < tick or px > lock_cap or px >= cb["ask"]:
            continue
        v = need * pf * (cb["mid"] - px - p["adverse_ticks"] * tick + risk)
        if best_mk is None or v > best_mk["value"]:
            best_mk = {"value": v, "px": px, "qty": need, "p_fill": pf}
    if best_mk is not None:
        scores["COMPLETE_MAKER"] = best_mk
        scores["SELL_MAKER"] = {"alias_of": "COMPLETE_MAKER",
                                "value": best_mk["value"]}
    # never more than the unmatched quantity: no flip, no averaging down
    scores["ADD_COMPLEMENTARY_INVENTORY"] = {
        "value": None, "why": "rejected: beyond the unmatched quantity it "
                              "is a new opposite position, not a recovery"}
    scores["HOLD"] = {"value": D(0)}

    pick, best = "HOLD", D(0)
    for name in ("COMPLETE_TAKER", "PARTIAL_REBALANCE", "COMPLETE_MAKER"):
        s = scores.get(name) or {}
        v = s.get("value")
        if v is not None and v > best:
            pick, best = name, v
    out["scores"] = {k: {kk: (str(vv) if isinstance(vv, D) else vv)
                         for kk, vv in s.items()} for k, s in scores.items()}
    out["risk_per_contract"] = str(risk)
    out["urgency"] = str(urgency)
    if pick == "HOLD":
        out["action"] = "HOLD"
        out["reason"] = "no action beats holding"
        return out
    s = scores[pick]
    out["px"], out["qty"] = s["px"], s["qty"]
    out["action"] = pick
    if pick == "COMPLETE_MAKER" and resting_px is not None:
        if resting_px == s["px"]:
            out["action"] = "KEEP"            # preserve queue priority
            out["reason"] = "resting at the chosen price"
        else:
            gain = need * (s["px"] - resting_px).copy_abs() * s["p_fill"]
            if gain < need * p["reprice_min_usd"] and \
                    resting_px <= lock_cap and resting_px < cb["ask"]:
                out["action"], out["px"] = "KEEP", resting_px
                out["reason"] = "improvement below reprice_min"
            else:
                out["action"] = "REPRICE_COMPLETION"
    return out


def halt_new_exposure(unmatched_cost: D, limit: D) -> bool:
    """HALT_NEW_EXPOSURE: no new first legs while unmatched cost is high."""
    return limit > 0 and unmatched_cost >= limit
