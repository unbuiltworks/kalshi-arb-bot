"""BTC5m LOSS-MANAGEMENT / INVENTORY-RECOVERY harness. RESEARCH ONLY.

It sends nothing, arms nothing, and changes nothing: not the authorized
underdog cohort, not the recorder, not the frozen analyzers
(f2800e3e...960e90b, b2f6d50a...142f9bc6), and no prospective/OOS boundary.
No live SELL, hedge, added inventory or recovery sizing is authorized by its
existence.

METHODOLOGY PORTED FROM THE REAL FILE. `_hourly_stop_backtest.py` lives in
unbuiltworks/kalshi-longshot-fader (c004adca), not in this repository; it was
read before this harness was finished rather than reconstructed from the
brief. Four of its conventions are adopted verbatim in spirit, and three of
them are STRICTER than my first pass:

  1. TRIGGER ON THE ADVERSE EXTREME, FILL WORSE AND LATER. The original
     triggers on a minute's yes_ask HIGH and fills at that minute's CLOSE --
     "the price we act on is worse than the one that tripped the rule". This
     tape has one observation per checkpoint rather than an OHLC bar, so the
     faithful analogue is: trigger at the observation that breaches, fill at
     the NEXT observation's liquidation price, and never better than the
     trigger. A same-instant fill at the triggering touch is the flattering
     version and is not used.
  2. A NO-OFFER IS NOT A PRICE. The original drops asks of 0.9999 with no
     book. Here a 0.98/0.99 level of size 1 is the same artifact and is
     refused rather than read as a 98c quote.
  3. AN EXPLICIT POPULATION LEDGER. Its `Population` class counts raw,
     excluded-by-reason and included rows and verifies they reconcile, so a
     silent drop cannot shrink the denominator. Reproduced below.
  4. SAVED vs COST DECOMPOSITION. Not just the net: how much a stop saved on
     the disasters against how much it cost on the positions that reverted.

NONE of its thresholds are carried over. Its 15-50c grid is calibrated to
SELLING yes at ~7c, a different trade in a different direction; the grid
here is a preregistered sweep in contract-price space and 30c/60c have no
special status in it.

FOUR POLICIES FROM THE IDENTICAL ENTRY, replayed causally through
settlement:

  A HOLD        -- the control. Hold to settlement.
  B EXIT        -- close after an adverse move, swept over a preregistered
                   threshold grid. The exit EXECUTES against the recorded
                   opposite side: selling UP hits the YES bid, selling DOWN
                   hits 1 - the YES ask. No midpoint, no last trade, and a
                   level too thin for the size does not fill.
  C COMPLEMENT  -- buy the opposite side after an adverse move, ONLY where
                   the completed position's prospective economics beat
                   continuing. A matched book costing MORE than $1 is a
                   LOSS, and is never called protection because exposure
                   fell.
  D L5_INVENTORY-- bounded subsequent BUYs on either side, tracking UP and
                   DOWN inventory, matched quantity, directional residual,
                   effective matched-pair cost, residual principal and total
                   deployed capital after every action. No martingale: every
                   action is the same unit size.

WHAT THIS CORPUS CAN AND CANNOT SUPPORT is reported before any policy
number, because it decides how much the comparison is worth: the only
pre-existing BTC5m executable path is the tte ladder, whose grid starts at
tte=120s (T+180) and has 11 points per window. That is a real causal path,
and it is NOT the early window -- entries at T+15..T+120 cannot be studied
here at all.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
from decimal import ROUND_CEILING, Decimal as D
from typing import Any, Dict, List, Optional, Tuple

LABEL = "GEMINI_BTC5M_LOSS_MANAGEMENT_HARNESS_V1"
LADDER = "analysis/gemini/info_edge/ie_tte_ladder.json"

#: the venue's reconciled TAKER rate (predicted 0.0155/0.0168/0.0161/0.0135
#: against cohort 1's charged $0.02 each, at cent rounding). RECONCILED ON
#: THE BUY SIDE ONLY -- this programme has never sold on this venue, so the
#: sell-side fee is the same published formula applied to the exit price and
#: is labelled UNRECONCILED wherever it is used.
TAKER_RATE = D("0.07")
FEE_QUANTUM = D("0.01")

#: PREREGISTERED adverse-move grid, in CONTRACT PRICE (probability) points,
#: never account dollars. A sweep, not a selection: every level is reported
#: and none is privileged. The Kalshi 30c/60c thresholds are NOT ported.
ADVERSE_GRID_CENTS: Tuple[int, ...] = (3, 5, 8, 10, 15, 20, 25, 30, 40)
#: the unit of every action, in contracts. Constant by construction, which
#: is what makes martingale sizing INEXPRESSIBLE here.
UNIT = D("1")
#: bounded inventory for policy D
MAX_UNITS_PER_SIDE = 3
#: remaining-time buckets for the MFE/MAE path study, in seconds
TTE_BUCKETS: Tuple[Tuple[int, int], ...] = ((120, 91), (90, 61), (60, 31),
                                            (30, 16), (15, 6), (5, 1))
#: a stop rate above this would trip a live circuit breaker. Reported, never
#: used to filter the backtest -- a breaker is a deployment control, and
#: applying it retroactively would flatter the policy that trips it.
CIRCUIT_BREAKER_STOP_RATE = 0.50


class Population:
    """raw -> excluded(reason) -> included, and they must reconcile.

    Ported from _hourly_stop_backtest's own Population: a denominator that
    shrinks silently is how a backtest flatters itself, so every dropped
    window is counted under a named reason and the total is verified.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.n_raw = 0
        self.n_included = 0
        self.excluded: Dict[str, int] = collections.Counter()

    def raw(self) -> None:
        self.n_raw += 1

    def exclude(self, reason: str) -> None:
        self.excluded[reason] += 1

    def include(self) -> None:
        self.n_included += 1

    def verify(self) -> Dict[str, Any]:
        dropped = sum(self.excluded.values())
        return {"name": self.name, "raw": self.n_raw,
                "included": self.n_included,
                "excluded_by_reason": dict(self.excluded),
                "excluded_total": dropped,
                "reconciles": self.n_raw == self.n_included + dropped}


#: a level at or above this price with a size of one contract is a NO-OFFER
#: artifact, not a quote. The original drops 0.9999 asks with no book.
NO_OFFER_PRICE = D("0.98")
NO_OFFER_MAX_SIZE = D("1")


def _is_no_offer(px: Optional[D], sz: Optional[D]) -> bool:
    return bool(px is not None and sz is not None
                and px >= NO_OFFER_PRICE and sz <= NO_OFFER_MAX_SIZE)


def _d(v: Any) -> Optional[D]:
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def fee(price: D, qty: D = UNIT) -> D:
    """The venue's charged fee at the cent rounding it applied, rounded UP."""
    raw = TAKER_RATE * qty * price * (D("1") - price)
    if raw <= 0:
        return D("0")
    return raw.quantize(FEE_QUANTUM, rounding=ROUND_CEILING)


# ------------------------------------------------------------------ the tape
def load() -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    raw = [r for r in json.load(open(LADDER, encoding="utf-8"))
           if str(r.get("ticker", "")).startswith("BTC05M")]
    by: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in raw:
        if r.get("tte") is not None:
            by[r["ticker"]].append(r)
    for tk in by:
        by[tk].sort(key=lambda r: -float(r["tte"]))
    settled = {tk: rs for tk, rs in by.items()
               if any(r.get("up_won") is not None for r in rs)}
    grid = sorted({float(r["tte"]) for r in raw}, reverse=True)
    cov = {
        "file": LADDER,
        "btc5m_windows": len(by),
        "windows_with_settlement": len(settled),
        "tte_grid_s": grid,
        "path_points_per_window": len(grid),
        "earliest_observation_T_plus_s": (300 - int(grid[0]) if grid
                                          else None),
        "what_this_cannot_study": (
            "entries at T+15..T+120. The grid starts at tte=120s, i.e. "
            "T+180, so the early window this programme cares about is "
            "absent and no policy result here transfers to it."),
        "sell_side_fee_status": (
            "UNRECONCILED. The buy-side taker rate is reconciled against "
            "cohort 1's charged fees; nothing has ever been SOLD on this "
            "venue by this programme, so every exit fee below is the "
            "published formula applied to the exit price, not a charged "
            "one."),
    }
    return settled, cov


# ------------------------------------------------------- executable prices
def acquire(row: Dict[str, Any], side: str,
            qty: D = UNIT) -> Optional[Tuple[D, D]]:
    """(price, size_available) to BUY `side`, or None if unfillable."""
    if side == "UP":
        px, sz = _d(row.get("up_ask")), _d(row.get("up_ask_sz"))
    else:
        px, sz = _d(row.get("down_price")), _d(row.get("down_sz"))
    if px is None or sz is None or sz < qty or _is_no_offer(px, sz):
        return None
    return px, sz


def liquidate(row: Dict[str, Any], side: str,
              qty: D = UNIT) -> Optional[Tuple[D, D]]:
    """(price, size) to SELL `side` AGAINST RECORDED DEPTH. Conservative.

    Selling UP hits the YES BID. Selling DOWN hits 1 - the YES ASK, because
    the venue publishes one ladder and a NO sale is a YES purchase in
    disguise. A midpoint, a last trade or a touch too thin for the size is
    NOT a fill, and no mark-to-market value is ever returned here.
    """
    if side == "UP":
        px, sz = _d(row.get("bid")), _d(row.get("down_sz"))
    else:
        a, sz = _d(row.get("up_ask")), _d(row.get("up_ask_sz"))
        px = None if a is None else D("1") - a
    if px is None or sz is None or sz < qty or px <= 0:
        return None
    if _is_no_offer(px, sz):
        return None
    return px, sz


def mark(row: Dict[str, Any], side: str) -> Optional[D]:
    """The adverse-move YARDSTICK ONLY: what the side could be SOLD for.

    It is deliberately the liquidation price and not a midpoint: a stop that
    triggers off a price it cannot achieve is a backtest artifact.
    """
    got = liquidate(row, side)
    return None if got is None else got[0]


def settle_value(row: Dict[str, Any], side: str) -> Optional[D]:
    up_won = row.get("up_won")
    if up_won is None:
        return None
    if side == "UP":
        return D("1") if up_won else D("0")
    return D("0") if up_won else D("1")


# ------------------------------------------------------------- the policies
def entry_of(rs: List[Dict[str, Any]],
             entry_tte: float) -> Optional[Dict[str, Any]]:
    """The identical entry every policy starts from: the cheaper executable
    side at the first eligible checkpoint, both sides fillable at the unit.
    """
    for r in rs:
        if float(r["tte"]) != entry_tte:
            continue
        up, dn = acquire(r, "UP"), acquire(r, "DOWN")
        if up is None or dn is None:
            return None
        side = "UP" if up[0] < dn[0] else ("DOWN" if dn[0] < up[0] else None)
        if side is None:
            return None                      # a tie has no underdog
        px = up[0] if side == "UP" else dn[0]
        return {"row": r, "side": side, "price": px,
                "fee": fee(px), "other_price": (dn[0] if side == "UP"
                                                else up[0]),
                "tte": float(r["tte"])}
    return None


def policy_hold(e: Dict[str, Any], rs: List[Dict[str, Any]]) -> Dict:
    sv = settle_value(rs[-1], e["side"])
    if sv is None:
        return {"ok": False}
    pnl = sv * UNIT - e["price"] * UNIT - e["fee"]
    return {"ok": True, "policy": "A_HOLD", "pnl": pnl,
            "capital_deployed": e["price"] * UNIT + e["fee"],
            "residual_units": UNIT, "intervened": False,
            "fees": e["fee"], "settlement_pnl_without_intervention": pnl}


def _next_fill(rs: List[Dict[str, Any]], after_tte: float,
               side: str) -> Optional[Tuple[D, float]]:
    """The liquidation price at the NEXT observation after `after_tte`."""
    for r in rs:
        if float(r["tte"]) >= after_tte:
            continue
        got = liquidate(r, side)
        if got is None:
            continue
        return got[0], float(r["tte"])
    return None


def policy_exit(e: Dict[str, Any], rs: List[Dict[str, Any]],
                adverse_cents: int) -> Dict:
    """Close after an adverse move of `adverse_cents` in contract price.

    IDEMPOTENT CLOSE: the position is closed at most once, and a close that
    could not fill does NOT make the position disappear -- it stays open and
    is tried again at the next observation, which is the live failure mode
    this convention exists to model.
    """
    thr = D(adverse_cents) / D("100")
    entry_mark = mark(e["row"], e["side"])
    closed = False
    close_px = None
    close_tte = None
    failed_closes = 0
    triggered_tte = None
    trigger_px = None
    for r in rs:
        if float(r["tte"]) >= e["tte"]:
            continue
        m = mark(r, e["side"])
        if m is None:
            continue
        ref = entry_mark if entry_mark is not None else e["price"]
        if (ref - m) >= thr and not closed:
            if triggered_tte is None:
                triggered_tte = float(r["tte"])
                trigger_px = m
            # FILL WORSE AND LATER, as the original does: it triggers on the
            # minute's adverse HIGH and fills at that minute's CLOSE. This
            # tape has one observation per checkpoint, so the analogue is to
            # fill at the NEXT observation's liquidation price -- and never
            # better than the price that tripped the rule. Filling at the
            # triggering touch itself is the flattering version.
            nxt = _next_fill(rs, float(r["tte"]), e["side"])
            if nxt is None:
                failed_closes += 1       # NOT a close. The position remains.
                continue
            close_px = min(nxt[0], trigger_px)
            close_tte = nxt[1]
            closed = True
            break
    if closed:
        f_out = fee(close_px)
        pnl = (close_px * UNIT - e["price"] * UNIT - e["fee"] - f_out)
        residual = D("0")
        fees = e["fee"] + f_out
    else:
        sv = settle_value(rs[-1], e["side"])
        if sv is None:
            return {"ok": False}
        pnl = sv * UNIT - e["price"] * UNIT - e["fee"]
        residual = UNIT
        fees = e["fee"]
    hold = policy_hold(e, rs)
    if not hold.get("ok"):
        return {"ok": False}
    return {"ok": True, "policy": "B_EXIT_%02dc" % adverse_cents,
            "pnl": pnl, "capital_deployed": e["price"] * UNIT + e["fee"],
            "residual_units": residual, "intervened": closed,
            "triggered": triggered_tte is not None,
            "triggered_tte": triggered_tte,
            "failed_close_attempts": failed_closes,
            "close_price": (None if close_px is None else str(close_px)),
            "close_tte": close_tte, "fees": fees,
            "settlement_pnl_without_intervention": hold["pnl"],
            # a FALSE STOP: stopped out, and holding would have won
            "false_stop": bool(closed and hold["pnl"] > 0),
            "retained_winner": bool(not closed and hold["pnl"] > 0)}


def policy_complement(e: Dict[str, Any], rs: List[Dict[str, Any]],
                      adverse_cents: int) -> Dict:
    """Buy the OTHER side after an adverse move -- only if that is better.

    THE TEST IS EXPLICIT AND ARITHMETIC: completing costs
    original acquisition + complement acquisition + ALL fees, and the matched
    pair pays exactly $1. If that total exceeds $1 the completion LOCKS A
    LOSS; it is permitted only when the locked outcome beats the prospective
    value of continuing, which is bounded above by what the directional
    position could still be SOLD for. Reduced exposure is never itself
    called protection or profit.
    """
    thr = D(adverse_cents) / D("100")
    other = "DOWN" if e["side"] == "UP" else "UP"
    entry_mark = mark(e["row"], e["side"])
    for r in rs:
        if float(r["tte"]) >= e["tte"]:
            continue
        m = mark(r, e["side"])
        if m is None:
            continue
        ref = entry_mark if entry_mark is not None else e["price"]
        if (ref - m) < thr:
            continue
        got = acquire(r, other)
        if got is None:
            continue
        c_px = got[0]
        c_fee = fee(c_px)
        total = e["price"] * UNIT + e["fee"] + c_px * UNIT + c_fee
        locked = D("1") * UNIT - total          # matched pair pays $1
        # continuing is worth AT MOST the current liquidation value
        continue_value = m * UNIT - e["price"] * UNIT - e["fee"]
        if locked <= continue_value:
            continue                            # completing is not better
        hold = policy_hold(e, rs)
        return {"ok": True, "policy": "C_COMPLEMENT_%02dc" % adverse_cents,
                "pnl": locked, "capital_deployed": total,
                "residual_units": D("0"), "intervened": True,
                "matched_pair_cost": str(e["price"] + c_px),
                "matched_pair_cost_with_fees": str(total),
                "pair_is_above_one_dollar": bool(total > D("1")),
                "locked_outcome": str(locked),
                "fees": e["fee"] + c_fee,
                "complement_price": str(c_px), "complement_tte": float(
                    r["tte"]),
                "settlement_pnl_without_intervention": hold["pnl"],
                "label": ("a matched book costing more than $1 is a LOSS. "
                          "It is reported as one.")}
    hold = policy_hold(e, rs)
    if not hold.get("ok"):
        return {"ok": False}
    return {"ok": True, "policy": "C_COMPLEMENT_%02dc" % adverse_cents,
            "pnl": hold["pnl"], "capital_deployed": hold["capital_deployed"],
            "residual_units": UNIT, "intervened": False,
            "fees": e["fee"],
            "settlement_pnl_without_intervention": hold["pnl"]}


def policy_l5(e: Dict[str, Any], rs: List[Dict[str, Any]],
              adverse_cents: int) -> Dict:
    """Bounded subsequent BUYs on either side, with full inventory state.

    EVERY ACTION IS ONE UNIT. Martingale sizing is not disabled by a flag --
    it is inexpressible, because no code path multiplies the size.
    """
    thr = D(adverse_cents) / D("100")
    inv = {"UP": D("0"), "DOWN": D("0")}
    cost = {"UP": D("0"), "DOWN": D("0")}
    fees = D("0")
    actions: List[Dict[str, Any]] = []

    def act(side: str, px: D, tte: float, kind: str) -> None:
        nonlocal fees
        f = fee(px)
        inv[side] += UNIT
        cost[side] += px * UNIT
        fees += f
        matched = min(inv["UP"], inv["DOWN"])
        resid_side = ("UP" if inv["UP"] > inv["DOWN"]
                      else ("DOWN" if inv["DOWN"] > inv["UP"] else None))
        pair_cost = None
        if matched > 0:
            avg_up = cost["UP"] / inv["UP"] if inv["UP"] else D("0")
            avg_dn = cost["DOWN"] / inv["DOWN"] if inv["DOWN"] else D("0")
            pair_cost = avg_up + avg_dn
        actions.append({
            "kind": kind, "side": side, "price": str(px), "qty": str(UNIT),
            "tte": tte, "fee": str(f),
            "up_inventory": str(inv["UP"]), "down_inventory": str(inv["DOWN"]),
            "matched_quantity": str(matched),
            "directional_residual_side": resid_side,
            "directional_residual_qty": str(abs(inv["UP"] - inv["DOWN"])),
            "effective_matched_pair_cost": (None if pair_cost is None
                                            else str(pair_cost)),
            "residual_principal": str(
                (cost["UP"] + cost["DOWN"]) - (pair_cost or D("0")) * matched),
            "total_deployed_capital": str(cost["UP"] + cost["DOWN"] + fees),
            "unit_is_constant": "no code path multiplies the size",
        })

    act(e["side"], e["price"], e["tte"], "OPEN")
    entry_mark = mark(e["row"], e["side"])
    other = "DOWN" if e["side"] == "UP" else "UP"
    for r in rs:
        if float(r["tte"]) >= e["tte"]:
            continue
        m = mark(r, e["side"])
        if m is None:
            continue
        ref = entry_mark if entry_mark is not None else e["price"]
        adverse = (ref - m) >= thr
        if not adverse:
            continue
        # COMPLETE if the pair economics are good; otherwise EXTEND the
        # cheaper side, both bounded.
        c = acquire(r, other)
        if c is not None and inv[other] < MAX_UNITS_PER_SIDE:
            pair = (cost[e["side"]] / inv[e["side"]]) + c[0]
            if pair < D("1"):
                act(other, c[0], float(r["tte"]), "COMPLETE")
                continue
        s = acquire(r, e["side"])
        if s is not None and inv[e["side"]] < MAX_UNITS_PER_SIDE:
            act(e["side"], s[0], float(r["tte"]), "EXTEND")
    last = rs[-1]
    up_sv, dn_sv = settle_value(last, "UP"), settle_value(last, "DOWN")
    if up_sv is None or dn_sv is None:
        return {"ok": False}
    proceeds = up_sv * inv["UP"] + dn_sv * inv["DOWN"]
    deployed = cost["UP"] + cost["DOWN"] + fees
    hold = policy_hold(e, rs)
    if not hold.get("ok"):
        return {"ok": False}
    matched = min(inv["UP"], inv["DOWN"])
    return {"ok": True, "policy": "D_L5_INVENTORY_%02dc" % adverse_cents,
            "pnl": proceeds - deployed,
            "capital_deployed": deployed,
            "residual_units": abs(inv["UP"] - inv["DOWN"]),
            "intervened": len(actions) > 1,
            "n_actions": len(actions), "actions": actions,
            "final_up_inventory": str(inv["UP"]),
            "final_down_inventory": str(inv["DOWN"]),
            "final_matched_quantity": str(matched),
            "fees": fees,
            "settlement_pnl_without_intervention": hold["pnl"]}


# ---------------------------------------------------------- the MFE/MAE path
def path_study(settled: Dict[str, List[Dict[str, Any]]],
               entry_tte: float) -> Dict[str, Any]:
    """Do eventual WINNERS suffer the same adverse excursions as LOSERS?

    If they do, any stop that cuts losers cuts winners at a similar rate and
    the EXIT family cannot help. Measured in executable terms: the excursion
    is in the price the entered side could actually be SOLD for.
    """
    rows = {"winner": collections.defaultdict(list),
            "loser": collections.defaultdict(list)}
    per_window = []
    for tk, rs in settled.items():
        e = entry_of(rs, entry_tte)
        if e is None:
            continue
        sv = settle_value(rs[-1], e["side"])
        if sv is None:
            continue
        group = "winner" if sv > 0 else "loser"
        ref = mark(e["row"], e["side"]) or e["price"]
        mfe = mae = D("0")
        for r in rs:
            if float(r["tte"]) >= e["tte"]:
                continue
            m = mark(r, e["side"])
            if m is None:
                continue
            mfe = max(mfe, m - ref)
            mae = min(mae, m - ref)
            for hi, lo in TTE_BUCKETS:
                if lo <= float(r["tte"]) <= hi:
                    rows[group]["%ds-%ds" % (hi, lo)].append(
                        float(m - ref))
        per_window.append({"ticker": tk, "group": group,
                           "side": e["side"], "entry": str(e["price"]),
                           "mfe": float(mfe), "mae": float(mae)})
    def summ(vals):
        if not vals:
            return None
        return {"n": len(vals),
                "mean": round(statistics.fmean(vals), 4),
                "median": round(statistics.median(vals), 4),
                "p10": round(sorted(vals)[max(0, len(vals) // 10)], 4),
                "p90": round(sorted(vals)[min(len(vals) - 1,
                                              9 * len(vals) // 10)], 4),
                "worst_adverse": round(min(vals), 4),
                "best_favorable": round(max(vals), 4)}
    buckets = {}
    for hi, lo in TTE_BUCKETS:
        k = "%ds-%ds" % (hi, lo)
        buckets[k] = {"winners": summ(rows["winner"][k]),
                      "losers": summ(rows["loser"][k])}
    w = [p for p in per_window if p["group"] == "winner"]
    l = [p for p in per_window if p["group"] == "loser"]
    def exc(group, thr):
        if not group:
            return None
        return round(100.0 * sum(1 for p in group
                                 if p["mae"] <= -thr) / len(group), 2)
    return {
        "entry_tte_s": entry_tte,
        "windows": len(per_window),
        "winners": len(w), "losers": len(l),
        "by_remaining_time_bucket": buckets,
        "mae_distribution": {"winners": summ([p["mae"] for p in w]),
                             "losers": summ([p["mae"] for p in l])},
        "mfe_distribution": {"winners": summ([p["mfe"] for p in w]),
                             "losers": summ([p["mfe"] for p in l])},
        "pct_touching_each_adverse_threshold": {
            "%02dc" % c: {"winners": exc(w, D(c) / D("100")),
                          "losers": exc(l, D(c) / D("100"))}
            for c in ADVERSE_GRID_CENTS},
        "the_question": ("if winners touch a threshold at a similar rate to "
                         "losers, a stop at that threshold cannot separate "
                         "them and the EXIT family is dead on arrival"),
    }


# ------------------------------------------------------------------ reporting
def summarise(name: str, results: List[Dict[str, Any]],
              n_all_windows: int) -> Dict[str, Any]:
    ok = [r for r in results if r.get("ok")]
    if not ok:
        return {"policy": name, "entries": 0}
    pnls = [r["pnl"] for r in ok]
    losers = [p for p in pnls if p < 0]
    total = sum(pnls, D("0"))
    peak = run = dd = D("0")
    for p in pnls:
        run += p
        peak = max(peak, run)
        dd = min(dd, run - peak)
    no_int = [r["settlement_pnl_without_intervention"] for r in ok]
    stopped = [r for r in ok if r.get("intervened")]
    false_stops = [r for r in ok if r.get("false_stop")]
    triggered = [r for r in ok if r.get("triggered")]
    return {
        "policy": name,
        "entries": len(ok),
        "net_pnl_after_fees_usd": str(total.quantize(D("0.0001"))),
        "ev_per_entry_usd": str((total / len(ok)).quantize(D("0.0001"))),
        "ev_per_all_windows_usd": str(
            (total / n_all_windows).quantize(D("0.0001"))
            if n_all_windows else "0"),
        "opportunity_frequency_per_window": round(len(ok) / n_all_windows, 4)
        if n_all_windows else None,
        "losing_trades": len(losers),
        "avg_losing_trade_usd": (str((sum(losers, D("0")) / len(losers))
                                     .quantize(D("0.0001")))
                                 if losers else None),
        "median_losing_trade_usd": (str(statistics.median(losers))
                                    if losers else None),
        "worst_loss_usd": str(min(pnls)) if pnls else None,
        "max_drawdown_usd": str(dd),
        "winner_retention_pct": (
            round(100.0 * sum(1 for r in ok if r.get("retained_winner"))
                  / max(1, sum(1 for p in no_int if p > 0)), 2)),
        "false_stop_reversion_rate_pct": (
            round(100.0 * len(false_stops) / len(stopped), 2)
            if stopped else None),
        "intervention_rate_pct": round(100.0 * len(stopped) / len(ok), 2),
        "trigger_rate_pct": (round(100.0 * len(triggered) / len(ok), 2)
                             if triggered else None),
        "would_trip_circuit_breaker": (
            (len(stopped) / len(ok)) > CIRCUIT_BREAKER_STOP_RATE),
        "failed_close_attempts_total": sum(
            r.get("failed_close_attempts") or 0 for r in ok),
        # SAVED vs COST, as the original decomposes it: a net figure hides
        # whether a stop earned its keep on the disasters or merely churned.
        "saved_usd_on_positions_it_helped": str(sum(
            (r["pnl"] - r["settlement_pnl_without_intervention"]
             for r in ok if r.get("intervened")
             and r["pnl"] > r["settlement_pnl_without_intervention"]),
            D("0")).quantize(D("0.0001"))),
        "cost_usd_on_positions_it_hurt": str(sum(
            (r["settlement_pnl_without_intervention"] - r["pnl"]
             for r in ok if r.get("intervened")
             and r["pnl"] < r["settlement_pnl_without_intervention"]),
            D("0")).quantize(D("0.0001"))),
        "capital_deployed_usd": str(
            sum((r["capital_deployed"] for r in ok), D("0"))),
        "residual_exposure_units": str(
            sum((r["residual_units"] for r in ok), D("0"))),
        "fees_usd": str(sum((r["fees"] for r in ok), D("0"))),
        "settlement_pnl_without_intervention_usd": str(
            sum(no_int, D("0")).quantize(D("0.0001"))),
        "pairs_above_one_dollar": sum(
            1 for r in ok if r.get("pair_is_above_one_dollar")),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry-tte", type=float, default=120.0)
    ap.add_argument("--out", default=("analysis/gemini/updown/"
                                      "LOSS_MANAGEMENT_HARNESS.json"))
    a = ap.parse_args(argv)
    settled, cov = load()
    n_all = len(settled)

    pop = Population("btc5m loss-management entries")
    usable: Dict[str, Dict[str, Any]] = {}
    for tk, rs in settled.items():
        pop.raw()
        if not any(float(r["tte"]) == a.entry_tte for r in rs):
            pop.exclude("01_no_observation_at_the_entry_checkpoint")
            continue
        e = entry_of(rs, a.entry_tte)
        if e is None:
            pop.exclude("02_entry_not_executable_both_sides_at_the_unit"
                        "_or_a_tie")
            continue
        if settle_value(rs[-1], e["side"]) is None:
            pop.exclude("03_no_settlement_on_the_last_observation")
            continue
        pop.include()
        usable[tk] = e

    pol: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for tk, e in usable.items():
        rs = settled[tk]
        pol["A_HOLD"].append(policy_hold(e, rs))
        for c in ADVERSE_GRID_CENTS:
            pol["B_EXIT_%02dc" % c].append(policy_exit(e, rs, c))
            pol["C_COMPLEMENT_%02dc" % c].append(policy_complement(e, rs, c))
            pol["D_L5_INVENTORY_%02dc" % c].append(policy_l5(e, rs, c))

    summaries = {k: summarise(k, v, n_all) for k, v in sorted(pol.items())}
    hold_ev = D(summaries["A_HOLD"]["ev_per_entry_usd"])
    head = {}
    for fam in ("B_EXIT", "C_COMPLEMENT", "D_L5_INVENTORY"):
        rows = [(k, D(s["ev_per_entry_usd"]), D(s["max_drawdown_usd"]),
                 s.get("avg_losing_trade_usd"))
                for k, s in summaries.items() if k.startswith(fam)]
        head[fam] = [{"policy": k, "ev_per_entry_usd": str(ev),
                      "ev_minus_hold_usd": str(ev - hold_ev),
                      "max_drawdown_usd": str(dd),
                      "avg_losing_trade_usd": al,
                      "advances": bool(ev >= hold_ev)}
                     for k, ev, dd, al in rows]

    rep = {
        "label": LABEL,
        "status": "RESEARCH ONLY / EXPLORATORY / IN-SAMPLE",
        "authorizes_nothing": ("no live SELL, hedge, added inventory or "
                               "recovery sizing; nothing is armed and no "
                               "frozen artifact, boundary or sealed "
                               "partition is read or changed"),
        "methodology_note": (
            "there is no _hourly_stop_backtest.py in this repository. The "
            "reused methodology is that of l5_cap_vs_live_stop and "
            "l5_adverse_extend_sequences plus the stated engineering "
            "principles. NO Kalshi threshold is ported: the adverse grid is "
            "a preregistered sweep and 30c/60c have no special status."),
        "coverage": cov,
        "entry_rule": ("the cheaper executable side at the entry "
                       "checkpoint, both sides fillable at the unit; a tie "
                       "has no underdog and the window is skipped"),
        "entry_tte_s": a.entry_tte,
        "windows_with_settlement": n_all,
        "windows_with_a_usable_entry": len(usable),
        "population": pop.verify(),
        "unit": str(UNIT),
        "adverse_grid_cents": list(ADVERSE_GRID_CENTS),
        "threshold_space": ("contract price / probability points, never "
                            "account dollars"),
        "exit_convention": ("executes against RECORDED depth: selling UP "
                            "hits the YES bid, selling DOWN hits 1 - the "
                            "YES ask. No midpoint, no last trade, no "
                            "mark-to-market, and a level too thin for the "
                            "size does not fill"),
        "idempotent_close": ("the position closes at most once, and a close "
                             "that cannot fill does NOT make it disappear"),
        "circuit_breaker_stop_rate": CIRCUIT_BREAKER_STOP_RATE,
        "circuit_breaker_note": ("reported, never applied retroactively -- "
                                 "a breaker is a deployment control, and "
                                 "filtering the backtest with it would "
                                 "flatter the policy that trips it"),
        "fee_model": {"taker_rate": str(TAKER_RATE),
                      "rounding": "cent, rounded up",
                      "buy_side": "RECONCILED against cohort 1's fills",
                      "sell_side": "UNRECONCILED -- never sold here"},
        "summaries": summaries,
        "headline_vs_hold": head,
        "advancement_rule": ("a loss-management rule advances only if it "
                             "reduces loss severity and drawdown WITHOUT "
                             "reducing expectancy enough to make the "
                             "strategy worse. In-sample numbers advance "
                             "nothing by themselves."),
        "path_study": path_study(settled, a.entry_tte),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, sort_keys=True, default=str)
    print(json.dumps({
        "label": LABEL, "windows": n_all, "usable_entries": len(usable),
        "hold_ev_per_entry": summaries["A_HOLD"]["ev_per_entry_usd"],
        "hold_max_dd": summaries["A_HOLD"]["max_drawdown_usd"],
        "out": a.out}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
