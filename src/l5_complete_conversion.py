"""Why COMPLETE orders do not fill: a classifier over observable evidence.

OWNER P0: "COMPLETE FILL CONVERSION FORENSICS ... distinguish these failure
modes quantitatively ... If evidence cannot distinguish queue behavior, say
UNKNOWN rather than guessing."

READ-ONLY. Nothing here submits, prices, cancels or reprices. It reads order
rows the live engine already wrote and labels them. No production constant is
modified and none is read for configuration.

THE SEVEN MODES, each with an OBSERVABLE definition. A mode that cannot be
decided from the row is UNKNOWN -- never the residual bucket for everything
that did not match another rule.

  M1 NEVER_REACHED     the complement never came to our price, and we waited
                       long enough for that to mean something
  M2 REACHED_NO_FILL   the complement DID come to our price and we did not
                       fill. Queue position itself is NOT observable -- the
                       label is "reached and did not fill", not "we lost the
                       queue"
  M3 CANCELLED_EARLY   we cancelled before the observation could decide
                       anything: our own action truncated the experiment
  M4 SIZE_EXCEEDS_FLOW our size outran the flow willing to trade with us.
                       For a MAKER order this needs a partial fill to
                       evidence; far-side resting depth bounds a TAKER
                       execution and says nothing about being hit, so depth
                       alone never supports this label
  M5 UNCHANGED_BOOK    the attempt repeats the previous attempt in this
                       market at the same price against the same book
  M6 MOVED_THROUGH     our own side's touch rose above our resting price --
                       the market moved past where we sat
  M7 UNKNOWN           the row does not carry what the question needs

PRECEDENCE, and why. Evidence about the BOOK outranks evidence about OUR
OWN behaviour, and our own behaviour outranks a conclusion we could only
draw by assuming what we did not observe:

  M4 -> M2 -> M6 -> M3 -> M1 -> M5 -> M7

M3 sits ABOVE M1 deliberately. "The complement never reached our price"
cannot be concluded from a window we closed ourselves after two seconds;
claiming it would turn our own cancel into a fact about the market.

AND M6 REQUIRES THAT THE TOUCH ROSE **DURING** THE REST. An order posted
BELOW its own side's touch starts behind the market by construction -- the
completion price is bounded by pair economics, not by the touch -- so
`max_touch > px` on such a row is the state we deliberately created, not a
move the market made. Calling that "the market moved past us" would launder
a pricing decision into a market event. Those rows fall through to M3 when
we then cancelled them, which is what actually happened to them.

THE REST-BOOK IS AN AGGREGATE, NOT A SERIES. `min_opp_ask` and `max_touch`
are extrema over the resting window and `polls` says how many observations
they are built from. A one-poll row is a SNAPSHOT: it can establish what was
true at that instant and can never establish what was true over a window.
Every count derived from it is labelled with its poll support.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Iterable, Mapping, Optional, Sequence

M1_NEVER_REACHED = "M1_COMPLEMENT_NEVER_REACHED_OUR_PRICE"
M2_REACHED_NO_FILL = "M2_REACHED_OUR_PRICE_NO_FILL_QUEUE_UNKNOWN"
M3_CANCELLED_EARLY = "M3_WE_CANCELLED_BEFORE_IT_COULD_FILL"
M4_SIZE_EXCEEDS_FLOW = "M4_SIZE_EXCEEDED_AVAILABLE_FLOW"
M5_UNCHANGED_BOOK = "M5_REPEAT_ATTEMPT_UNCHANGED_PRICE_AND_BOOK"
M6_MOVED_THROUGH = "M6_MARKET_MOVED_PAST_US"
M7_UNKNOWN = "M7_UNKNOWN_INSUFFICIENT_EVIDENCE"
REJECTED = "R0_REJECTED_BEFORE_RESTING"
# not a failure mode: a STATE the row was already in when it was posted,
# carried alongside whatever mode it earns
PRICED_BEHIND_THE_TOUCH_AT_POST = "S_PRICED_BEHIND_OUR_OWN_TOUCH_AT_POST"

FAILURE_MODES = (M1_NEVER_REACHED, M2_REACHED_NO_FILL, M3_CANCELLED_EARLY,
                 M4_SIZE_EXCEEDS_FLOW, M5_UNCHANGED_BOOK, M6_MOVED_THROUGH,
                 M7_UNKNOWN)

# Venue price grids seen on PMUS sports. The tick is a property of the
# MARKET, not of the venue, so it is read from the row where the venue
# stated it and otherwise INFERRED from the market's own observed price
# grid -- never defaulted, because a wrong tick silently rescales every
# distance in this report.
TICK_COARSE = D("0.01")
TICK_FINE = D("0.005")


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                        # noqa: BLE001
        return None


def infer_tick(declared: Iterable[Any], observed_prices: Iterable[Any]
               ) -> tuple[Optional[D], str]:
    """(tick, provenance). A declared tick always wins. Otherwise a price
    that is an odd multiple of the fine tick proves the fine grid; seeing
    only coarse prices does NOT prove the coarse grid, so that case is
    reported as an inference and not as a fact."""
    ds = {_d(x) for x in declared if _d(x) is not None}
    if len(ds) == 1:
        return ds.pop(), "declared_by_venue"
    if len(ds) > 1:
        return None, "declared_inconsistently"
    for p in observed_prices:
        v = _d(p)
        if v is None:
            continue
        if (v / TICK_FINE) % 2 == 1:
            return TICK_FINE, "proved_by_a_half_cent_price"
    return TICK_COARSE, "assumed_coarse_no_half_cent_seen"


def ticks_between(a: Any, b: Any, tick: Optional[D]) -> Optional[D]:
    """Signed distance a - b, in ticks. None when either side or the tick
    is unknown -- never 0, which would read as 'at the touch'."""
    x, y = _d(a), _d(b)
    if x is None or y is None or not tick:
        return None
    return (x - y) / tick


def attempt_features(order: Mapping[str, Any], tick: Optional[D],
                     prev: Optional[Mapping[str, Any]] = None) -> dict:
    """Everything observable about one COMPLETE attempt.

    `prev` is the previous COMPLETE attempt in the SAME market, used only
    for the repeat-attempt test; None for the first attempt.
    """
    px = _d(order.get("px"))
    qty = _d(order.get("qty"))
    filled = _d(order.get("filled_qty")) or D("0")
    rest = _d(order.get("rest_s"))
    touch = _d(order.get("l5_touch_px"))
    rb = order.get("rest_book") if isinstance(order.get("rest_book"),
                                              Mapping) else {}
    book = order.get("recorder_book") if isinstance(
        order.get("recorder_book"), Mapping) else {}
    min_opp = _d(rb.get("min_opp_ask"))
    max_touch = _d(rb.get("max_touch"))
    polls = rb.get("polls")
    bid, ask = _d(book.get("yes_bid")), _d(book.get("yes_ask"))
    end = str(order.get("end_reason") or "")

    prev_px = _d(prev.get("px")) if prev else None
    prev_book = (prev.get("recorder_book") if prev and isinstance(
        prev.get("recorder_book"), Mapping) else {}) or {}
    same_px = prev_px is not None and px is not None and prev_px == px
    same_book = bool(prev_book) and bool(book) and (
        (_d(prev_book.get("yes_bid")), _d(prev_book.get("yes_ask")))
        == (bid, ask))

    return {
        "order_id": order.get("order_id"),
        "stage": order.get("stage"),
        "side": order.get("side"),
        "posted_maker_price": None if px is None else str(px),
        "requested_qty": None if qty is None else str(qty),
        "filled_qty": str(filled),
        "partially_filled": bool(qty and D("0") < filled < qty),
        "best_bid": None if bid is None else str(bid),
        "best_ask": None if ask is None else str(ask),
        "spread_ticks": (None if (bid is None or ask is None)
                         else _s(ticks_between(ask, bid, tick))),
        "executable_depth": _first(order, ("obs_executable_depth",)),
        "executable_depth_status": _first(
            order, ("obs_executable_depth_status",)) or "NOT_RECORDED",
        "our_side_touch_price": None if touch is None else str(touch),
        "ticks_below_our_touch": _s(ticks_between(touch, px, tick)),
        "priced_below_our_touch": bool(touch is not None and px is not None
                                       and px < touch),
        "rest_s": None if rest is None else float(rest),
        "book_polls_while_resting": polls,
        "closest_complement_ask": None if min_opp is None else str(min_opp),
        "complement_reached_our_price": (None if min_opp is None or px is None
                                         else bool(min_opp <= px)),
        "highest_our_side_touch": None if max_touch is None else str(max_touch),
        "our_touch_rose_above_us": (None if max_touch is None or px is None
                                    else bool(max_touch > px)),
        "end_reason": end,
        "we_cancelled_to_requote": end.startswith("repriced:"),
        "rejected": end.startswith("rejected"),
        "rejection_class": order.get("rejection_class"),
        "repeat_same_price_as_previous": same_px,
        "repeat_same_book_as_previous": same_book,
        "tick": None if tick is None else str(tick),
    }


def _s(v: Optional[D]) -> Optional[float]:
    return None if v is None else float(v)


def _first(m: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for k in keys:
        if m.get(k) is not None:
            return m[k]
    return None


def classify(feat: Mapping[str, Any], min_fill_latency_s: float) -> dict:
    """One primary mode plus every co-occurring signal.

    `min_fill_latency_s` is the bar for "long enough to mean something" and
    must be supplied by the caller from the SAME corpus, so the threshold is
    an observed quantity and not a constant invented here.
    """
    co = []
    if feat.get("rejected"):
        return {"mode": REJECTED, "co_occurring": [],
                "why": f"venue rejected before resting: "
                       f"{feat.get('rejection_class')}"}
    if D(str(feat.get("filled_qty") or 0)) > 0 and not feat.get(
            "partially_filled"):
        return {"mode": None, "co_occurring": [], "why": "filled in full"}

    reached = feat.get("complement_reached_our_price")
    rose = feat.get("our_touch_rose_above_us")
    rest = feat.get("rest_s")
    polls = feat.get("book_polls_while_resting")
    cancelled = feat.get("we_cancelled_to_requote")

    below_at_post = feat.get("priced_below_our_touch") is True
    if below_at_post:
        co.append(PRICED_BEHIND_THE_TOUCH_AT_POST)
    if feat.get("repeat_same_price_as_previous") and feat.get(
            "repeat_same_book_as_previous"):
        co.append(M5_UNCHANGED_BOOK)
    if rose and not below_at_post:
        co.append(M6_MOVED_THROUGH)
    if cancelled and rest is not None and rest < min_fill_latency_s:
        co.append(M3_CANCELLED_EARLY)

    if feat.get("partially_filled"):
        return {"mode": M4_SIZE_EXCEEDS_FLOW, "co_occurring": co,
                "why": "partial fill: flow met part of the size and stopped"}
    if reached is True:
        return {"mode": M2_REACHED_NO_FILL, "co_occurring": co,
                "why": ("the complement ask reached our price and we did not "
                        "fill; QUEUE POSITION IS UNKNOWN -- not observable "
                        "from anything we record")}
    if rose is True and reached is False and not below_at_post:
        return {"mode": M6_MOVED_THROUGH, "co_occurring":
                [c for c in co if c != M6_MOVED_THROUGH],
                "why": ("we posted at or above our own side's touch and the "
                        "touch then rose past our resting price")}
    if cancelled and rest is not None and rest < min_fill_latency_s:
        return {"mode": M3_CANCELLED_EARLY, "co_occurring":
                [c for c in co if c != M3_CANCELLED_EARLY],
                "why": (f"we cancelled after {rest:.2f}s, inside the "
                        f"{min_fill_latency_s:.2f}s median latency of our own "
                        f"observed fills; nothing after that is observed")}
    if (reached is False and rest is not None
            and rest >= min_fill_latency_s
            and isinstance(polls, int) and polls >= 2):
        return {"mode": M1_NEVER_REACHED, "co_occurring": co,
                "why": (f"rested {rest:.2f}s over {polls} book polls and the "
                        f"complement never came to our price")}
    if M5_UNCHANGED_BOOK in co:
        return {"mode": M5_UNCHANGED_BOOK,
                "co_occurring": [c for c in co if c != M5_UNCHANGED_BOOK],
                "why": "same price, same book, as the previous attempt"}
    return {"mode": M7_UNKNOWN, "co_occurring": co,
            "why": "no rest book, no reach verdict, or no rest time recorded"}


def sensitivity(unfilled_attempts: int, mean_qty: D, marginal_pair_cost: D,
                maker_rebate_per_contract: D, residual_pnl_usd: D,
                residual_contracts_available: D, observed_fill_rate: D,
                targets: Sequence[D]) -> list[dict]:
    """How much would a higher COMPLETE fill rate be worth?

    NOT a forecast and NOT realized P&L. It answers one arithmetic question:
    holding the MARGINAL pair cost at the value actually observed on the
    attempts that did NOT fill, what does each extra completed contract add?

    The marginal pair cost -- not the average -- is the honest input. Our
    fills land at a materially cheaper pair than our no-fills, so the
    contracts a higher fill rate would ADD are the expensive ones.

    THE HARD CEILING, and it is what makes this analysis mean anything: a
    COMPLETE can only match inventory we ALREADY HOLD. Extra matched
    contracts are capped at the residual that actually existed
    (`residual_contracts_available`). Without that cap the arithmetic
    "completes" hundreds of contracts we never owned and the answer is
    meaningless. Once a rate saturates the residual, a higher rate adds
    NOTHING -- which is the finding, not a limitation of the model.

    A matched contract pays exactly $1.00 at settlement and costs its pair,
    so its margin is (1 - pair). The maker rebate is carried SEPARATELY and
    is an accrual until the venue credits it.
    """
    out = []
    per_contract = D("1") - marginal_pair_cost
    for rate in targets:
        extra_fills = (D(str(unfilled_attempts))
                       * (rate - observed_fill_rate)
                       / (D("1") - observed_fill_rate))
        if extra_fills < 0:
            extra_fills = D("0")
        wanted = extra_fills * mean_qty
        contracts = min(wanted, residual_contracts_available)
        margin = contracts * per_contract
        rebate = contracts * maker_rebate_per_contract
        out.append({
            "complete_fill_rate": str(rate),
            "extra_fills_vs_observed": str(extra_fills.quantize(D("0.1"))),
            "contracts_those_fills_would_want":
                str(wanted.quantize(D("0.01"))),
            "residual_available_to_match":
                str(residual_contracts_available),
            "extra_matched_contracts_capped_by_residual":
                str(contracts.quantize(D("0.01"))),
            "saturates_the_residual": bool(wanted >= residual_contracts_available),
            "matched_margin_usd": str(margin.quantize(D("0.0001"))),
            "maker_rebate_usd_accrual_only":
                str(rebate.quantize(D("0.0001"))),
            "settled_residual_pnl_usd_held_constant": str(residual_pnl_usd),
            "total_if_residual_unchanged_usd":
                str((margin + residual_pnl_usd).quantize(D("0.0001"))),
            "turns_positive": bool(margin + residual_pnl_usd > 0)})
    return out


def pair_cost_needed(residual_contracts: D, loss_to_cover_usd: D
                     ) -> Optional[D]:
    """The OTHER axis. If every contract of residual were completed, what
    pair cost would it take to cover a given loss? Returns None when no pair
    cost can do it (the loss exceeds the contracts' whole face value)."""
    if residual_contracts <= 0:
        return None
    margin_needed = loss_to_cover_usd / residual_contracts
    if margin_needed > 1:
        return None
    return (D("1") - margin_needed).quantize(D("0.0001"))


def l5_switch_process(fills: Sequence[Mapping[str, Any]]) -> dict:
    """L5's observable process around a side switch, from PUBLIC FILLS ONLY.

    What public activity establishes: time, price, size, outcome, and the
    venue's own maker/taker label. What it does NOT establish, and what is
    therefore reported as UNKNOWN rather than modelled: resting quotes,
    queue position, cancels, reprices, orders that never filled, and the
    book he was quoting against.

    `fills` must be one market window, already in trade order.
    """
    gaps_opposite, gaps_same, runs, switch_px, switch_qty = [], [], [], [], []
    level_runs = []
    prev = None
    run = level = 1
    for r in fills:
        if prev is not None:
            dt = float(r["t"]) - float(prev["t"])
            if r["outcome"] == prev["outcome"]:
                gaps_same.append(dt)
                run += 1
            else:
                gaps_opposite.append(dt)
                runs.append(run)
                run = 1
                switch_px.append(float(r["price"]))
                switch_qty.append(float(r["quantity"]))
            if r["outcome"] == prev["outcome"] and r["price"] == prev["price"]:
                level += 1
            else:
                level_runs.append(level)
                level = 1
        prev = r
    runs.append(run)
    level_runs.append(level)
    return {
        "seconds_to_opposite_side_fill": gaps_opposite,
        "seconds_between_same_side_fills": gaps_same,
        "same_side_run_lengths": runs,
        "switch_prices": switch_px,
        "switch_clips": switch_qty,
        "consecutive_fills_at_one_price": level_runs,
        "queue_position": "UNKNOWN -- public fills cannot establish it",
        "cancel_and_reprice_behaviour":
            "UNKNOWN -- unfilled orders are not public",
        "resting_quotes": "UNKNOWN -- only executions are public",
    }
