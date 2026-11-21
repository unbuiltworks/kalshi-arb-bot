"""Evaluate one frozen intent against one venue. Shared by both adapters.

ONE implementation, parameterised by the existing `VenueAdapter` (PMUSAdapter /
GeminiAdapter), rather than a Polymarket evaluator and a Gemini evaluator that
drift apart. If the two venues were scored by two code paths, every difference
between them would be ambiguous: venue economics, or an implementation
accident? Sharing the path makes the comparison mean something.

MODELLED AND OBSERVED ARE NEVER MIXED. This module can reach `would_submit` and
`would_fill` from a book alone. It can reach `filled` ONLY when handed venue
evidence, and it never manufactures that evidence.
"""
from __future__ import annotations

import time
from decimal import Decimal as D
from typing import Any, Mapping, Optional

from src.l5_adapters.base import AdapterEvaluation, no_market, within_ceiling
from src.l5_venue_adapter import NormalizedBook

ONE = D("1")


def _ask_for_outcome(book: NormalizedBook, outcome: str):
    """Best price to BUY the wanted outcome, and the depth resting there.

    L5 accumulates a side, so acquiring `Up` means lifting the YES ask and
    acquiring `Down` means the NO ask, which is 1 - yes_bid. Getting this
    inversion wrong silently prices the wrong side.
    """
    if outcome == "Up":
        return book.yes_ask, book.yes_ask_qty
    return book.no_ask, book.yes_bid_qty


def evaluate(intent, venue_adapter, book: Optional[NormalizedBook], *,
             venue_market_id: Optional[str] = None,
             mapping_confidence: Optional[str] = None,
             mapping_reason: Optional[str] = None,
             now: Optional[float] = None,
             assume_taker_fee: bool = True) -> AdapterEvaluation:
    """Score a frozen intent on one venue's current book.

    `assume_taker_fee` is deliberately the DEFAULT and deliberately
    conservative: until the venue tells us we were a maker, the fee we should
    plan against is the one we would pay for crossing. Planning on a maker
    rebate we have not been credited is how unsupported economics enter.
    """
    t = time.time() if now is None else now
    venue = venue_adapter.venue
    if book is None:
        return no_market(venue, intent,
                         mapping_reason or "no book available on this venue")

    ev = AdapterEvaluation(
        venue=venue, intent_id=intent.id, market_key=intent.market_key,
        evaluated_at=t, venue_market_id=venue_market_id or book.market_id,
        mapping_confidence=mapping_confidence, mapping_reason=mapping_reason,
        book_age_s=round(t - book.t, 3),
        spread=(str(book.spread) if book.spread is not None else None),
        stage="not_executable")

    px, depth = _ask_for_outcome(book, intent.desired_outcome)
    if px is None:
        ev.would_submit = False
        ev.would_submit_reason = f"no priceable {intent.desired_outcome} side"
        return ev
    ev.available_price = str(px)
    ev.available_depth = None if depth is None else str(depth)

    qty = D(intent.incremental_qty)
    try:
        fee = venue_adapter.taker_fee(px, qty) if assume_taker_fee else \
            venue_adapter.maker_fee(px, qty)
    except Exception as exc:                                  # noqa: BLE001
        fee = None
        ev.extra["fee_error"] = f"{type(exc).__name__}: {exc}"
    if fee is None:
        # unavailable is NULL WITH A REASON, never a fabricated zero: a 0 fee
        # reads as the cheapest possible execution.
        ev.expected_fee = None
        ev.expected_fee_basis = "unavailable: venue fee could not be computed"
        ev.would_submit = False
        ev.would_submit_reason = "fee unknown; refusing to price against it"
        return ev

    ev.expected_fee = str(fee)
    ev.expected_fee_basis = ("taker (conservative default)" if assume_taker_fee
                             else "maker (venue-credited only)")
    per_share_fee = (fee / qty) if qty else D("0")
    eff = px + per_share_fee
    ev.submission_price = str(px)
    ev.effective_cost = str(eff)
    ev.within_ceiling = within_ceiling(eff, intent.max_acceptable_price)

    if not ev.within_ceiling:
        ev.would_submit = False
        ev.would_submit_reason = (
            f"effective cost {eff} exceeds the intent ceiling "
            f"{intent.max_acceptable_price}")
        return ev

    if not venue_adapter.entry_ok(px):
        ev.would_submit = False
        ev.would_submit_reason = f"venue entry band rejects {px}"
        return ev

    ev.would_submit = True
    ev.would_submit_reason = "within ceiling and inside the venue entry band"
    ev.stage = "would_not_submit" if not ev.would_submit else "not_executable"

    # would_fill is a BELIEF about resting depth. It is an upper bound: queue
    # position is unobservable from a public book, so "there is size here" is
    # not "we would have got it".
    if depth is not None and D(str(depth)) >= qty:
        ev.would_fill = True
        ev.would_fill_basis = (
            f"depth {depth} >= wanted {qty} at {px}; UPPER BOUND -- queue "
            f"position is unobservable from a public book")
    else:
        ev.would_fill = False
        ev.would_fill_basis = f"insufficient visible depth ({depth}) for {qty}"
    return ev


def attach_fill_evidence(ev: AdapterEvaluation, evidence: Mapping[str, Any], *,
                         filled_qty: Any, liquidity_role: Optional[str] = None,
                         latency_s: Optional[float] = None
                         ) -> AdapterEvaluation:
    """Advance an evaluation to `filled` -- ONLY from venue evidence.

    `liquidity_role` is passed through exactly as the venue reported it and is
    left None when the venue did not say. There is no inference step here on
    purpose.
    """
    if not evidence:
        raise ValueError("attach_fill_evidence requires venue evidence")
    ev.fill_evidence = dict(evidence)
    ev.filled = True
    ev.filled_qty = str(filled_qty)
    ev.liquidity_role = liquidity_role
    ev.latency_s = latency_s
    ev.stage = "filled"
    return ev
