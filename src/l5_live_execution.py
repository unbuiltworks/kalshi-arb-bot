"""The execution path for the faithful L5 engine.

    L5_NATIVE -> PMUS_TRANSLATION -> SAFETY_OVERLAY -> ORDER -> FILL
              -> INVENTORY -> SETTLEMENT -> REALIZED

Every stage is recorded on ONE provenance record per L5 action, so any dollar
can be walked back to the L5 trade that caused it and to the layer that
allowed or refused it. A stage that did not happen is recorded as not having
happened; nothing is inferred backwards.

=== IT CANNOT TRANSMIT ===

There is no venue client, no credential and no HTTP call in this module. It
emits ORDER INTENTS and hands them to a transport that the caller injects.
The default transport is `NullTransport`, which refuses every submission and
says so. A real transport must be passed in explicitly by a caller that holds
an owner authorization; nothing here can construct one, so an accidental
import can place nothing.

=== WHAT PROTECTS THE ACCOUNT, AND WHERE IT LIVES ===

Hard safety is OUTSIDE L5_NATIVE and always was. `SafetyLimits` (max dollars
per market, max directional residual, max total exposure, max concurrent
markets, daily realized-loss halt) can refuse an ORDER. It may never rewrite
the behavioural decision, and a refusal is recorded against the
SAFETY_OVERLAY layer so it can never be misread later as "L5 would not have
done this" or "PMUS could not have done this".

What the old q1/maker-only approximation disliked is NOT suppressed here:
temporary matched cost above $1, repeated same-side accumulation, opposite
acquisition, continued recycling and zero-crossing all pass through. Only the
hard limits above can stop an order, and each states which limit it was.

=== IDEMPOTENCY AND RECOVERY ===

A client order id is DERIVED, not generated: it is a hash of the L5 action
key plus the attempt number, so replaying the same action after a crash
produces the same id and the venue rejects the duplicate rather than doubling
the position. Inventory is persisted on every transition, and on restart the
engine reconciles against VENUE state rather than trusting its own file --
the venue is authoritative, our record is a cache.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
from decimal import ROUND_FLOOR, Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_fees as fees
from src import l5_faithful_translation as FT

#: the pipeline, in order. A record names the furthest stage it reached.
STAGES = ("L5_NATIVE", "PMUS_TRANSLATION", "SAFETY_OVERLAY", "ORDER",
          "FILL", "INVENTORY", "SETTLEMENT", "REALIZED")

#: an action older than this at decision time is refused: acting on a stale
#: observation is how a replay after a restart turns into a wrong-price order
STALE_ACTION_MAX_S = 30.0


class IncoherentBook(RuntimeError):
    """The quantities and the costs disagree in a way no trade could produce.

    Raised rather than returned: every caller of the risk and settlement
    paths must stop, because a book like this cannot be priced at all and
    guessing at it is what produced the 1815z settlement.
    """


class NotArmed(RuntimeError):
    """Raised on any attempt to transmit without an explicit authorization."""


#: PMUS order states seen or documented as terminal. The AUTHORITY is
#: leavesQuantity == 0, which is numeric and was observed directly on
#: CQ5NPBADMYC3; these strings corroborate it and never override it, because
#: only ORDER_STATE_FILLED has actually been seen on this venue and inventing
#: the spelling of the others would be the same mistake as inventing a shape.
TERMINAL_HINTS = ("FILLED", "CANCEL", "REJECT", "EXPIR", "DONE")


def _money(v: Any) -> D:
    """A venue money field, which arrives as {"value","currency"} or scalar."""
    if isinstance(v, Mapping):
        v = v.get("value")
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return D(0)


def order_progress(row: Mapping[str, Any]) -> Dict[str, Any]:
    """What the venue says about one order, in the terms the store needs.

    CQ5NPBADMYC3 answered state ORDER_STATE_FILLED, quantity 2.25,
    cumQuantity 2.25, leavesQuantity 0, avgPx 0.2900, commission 0.0300 --
    eleven minutes after an ACK that had said filled_qty 0. `cum` is
    CUMULATIVE, so a caller must apply the DELTA against what it has already
    recorded or it will double-count its own partials.
    """
    state = str(row.get("state") or "")
    has_leaves = row.get("leavesQuantity") is not None
    leaves = _money(row.get("leavesQuantity"))
    cum = _money(row.get("cumQuantity"))
    # leavesQuantity DECIDES when the venue sends it. The state string only
    # decides when it does not -- and it must not be allowed to override the
    # number, because "ORDER_STATE_PARTIALLY_FILLED" contains "FILLED" and a
    # substring match would retire an order that is still working.
    if has_leaves:
        terminal = leaves <= 0
    else:
        terminal = any(h in state.upper() for h in TERMINAL_HINTS)
    return {"venue_order_id": str(row.get("id") or ""),
            "state": state or None,
            "cum_qty": cum,
            "leaves_qty": leaves,
            "avg_px": _money(row.get("avgPx")),
            "commission": _money(
                row.get("commissionNotionalTotalCollected")),
            "terminal": bool(terminal)}


def venue_tick_size(transport: Any) -> Optional[D]:
    """The venue's PRICE grid, from ITS metadata, or None.

    Same rule as the quantity grid: read off the transport, which took it
    from this market's own preflight. None means not established, and a
    maker order is refused rather than sent at a price the venue will
    normalise -- which is what the exact-echo gate caught on Probe 07.
    """
    tick = getattr(transport, "tick_size", None)
    if tick is None:
        return None
    try:
        tick = D(str(tick))
    except Exception:                                          # noqa: BLE001
        return None
    return tick if tick > 0 else None


def quantize_price_non_aggressive(price: D, tick: D, side: str) -> D:
    """L5's price on the venue's grid, never made more aggressive.

    PREREGISTERED ROUNDING RULE. Every L5 action in this corpus is a BUY of
    an outcome, and for a buy the aggressive direction is UP: paying more
    crosses sooner. So the price is FLOORED to the tick, always. A rounding
    rule that could round up would be price improvement by arithmetic, which
    the manifest forbids, and it could turn a resting order into a crossing
    one -- exactly the thing post-only exists to prevent.

    Probe 07 sent 0.7399999979 and the venue echoed 0.73. The echo gate
    refused, correctly. The gate is not the defect; sending a price the
    venue cannot represent is.
    """
    if tick <= 0:
        raise ValueError("tick must be positive")
    steps = (price / tick).to_integral_value(rounding=ROUND_FLOOR)
    return (steps * tick).normalize() + D(0)


def venue_quantity_increment(transport: Any) -> Optional[D]:
    """The venue's quantity grid, from ITS metadata, or None.

    Read off the transport, which took it from this market's own preflight.
    None means not established, and an armed order is refused rather than
    guessed at -- never defaulted to 0.01, which is only what this market
    happened to answer on 2026-09-25.
    """
    inc = getattr(transport, "quantity_increment", None)
    if inc is None:
        return None
    try:
        inc = D(str(inc))
    except Exception:                                          # noqa: BLE001
        return None
    return inc if inc > 0 else None


def floor_to_increment(qty: D, increment: D) -> D:
    """The largest multiple of `increment` that does not exceed `qty`.

    Floor, never round: rounding up would submit more than the safety
    overlay reserved, and the overlay is the thing standing between a
    translation and the account. The increment is not assumed to be a power
    of ten -- 0.05 and 0.25 grids exist -- so this divides rather than
    quantizing to a number of places.
    """
    if increment <= 0:
        raise ValueError("increment must be positive")
    steps = (qty / increment).to_integral_value(rounding=ROUND_FLOOR)
    return (steps * increment).normalize() + D(0)


def _redact(text: Any) -> str:
    """The transport's own scrubber, or a refusal to print at all.

    Imported lazily so this module keeps no import-time dependency on the
    transport, and defensively: if the scrubber cannot be reached, nothing
    is printed rather than something unscrubbed.
    """
    try:
        from src.l5_pmus_transport import redact
    except Exception:                                          # noqa: BLE001
        return "[REDACTION UNAVAILABLE]"
    return redact(text)


def submit_forensics(transport: Any) -> Dict[str, Any]:
    """WHICH call was in flight when submit() raised.

    str(HTTPError) is "HTTP Error 400: Bad Request" and str(URLError) is
    "<urlopen error timed out>". Neither carries the path, so the exception
    alone cannot distinguish a failed /v1/order/preview from a failed
    /v1/orders -- the question Probe 03 could not answer about itself. The
    transport records its own progress; this reads it, and never assumes:
    an attribute that is not there reads as None, not as False.
    """
    def got(name):
        return getattr(transport, name, None)

    return {"failure_stage": got("submit_stage"),
            "preview_attempted": got("preview_attempted"),
            "preview_status": got("preview_status"),
            "order_post_attempted": got("order_post_attempted")}


class NullTransport:
    """The default. Refuses everything and holds no credential."""

    armed = False
    name = "NULL"

    def submit(self, intent: Mapping[str, Any]) -> Dict[str, Any]:
        raise NotArmed(
            "no authorized transport is installed; this engine cannot "
            "transmit an order. Intent was: %s" % json.dumps(
                {k: str(v) for k, v in intent.items()}, sort_keys=True))

    def cancel(self, client_order_id: str) -> Dict[str, Any]:
        raise NotArmed("no authorized transport is installed")

    def open_orders(self) -> List[dict]:
        return []

    def positions(self) -> List[dict]:
        return []


def client_order_id(action_key: str, attempt: int = 0) -> str:
    """DERIVED, never random: the same action always yields the same id.

    This is the idempotency guarantee. A crash between submit and the
    response, followed by a replay, produces a byte-identical id, so the
    venue rejects the duplicate instead of us doubling the position.
    """
    raw = "%s|%d" % (action_key, attempt)
    return "l5f-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


class SafetyLimits:
    """Hard account protection. Outside L5_NATIVE, and never silent."""

    def __init__(self, *, max_usd_per_market: D = D("5"),
                 max_abs_residual: D = D("10"),
                 max_total_exposure_usd: D = D("10"),
                 max_concurrent_markets: int = 1,
                 max_live_orders: int = 1,
                 daily_realized_loss_halt_usd: D = D("5"),
                 band: Optional[tuple] = None) -> None:
        self.max_usd_per_market = max_usd_per_market
        self.max_abs_residual = max_abs_residual
        self.max_total_exposure_usd = max_total_exposure_usd
        self.max_concurrent_markets = max_concurrent_markets
        #: the first probe works ONE order at a time. A second order may not
        #: be submitted while the first is live; only a venue-authoritative
        #: terminal state (filled, cancelled, rejected) releases the slot.
        self.max_live_orders = max_live_orders
        self.daily_realized_loss_halt_usd = daily_realized_loss_halt_usd
        #: None by default: a price band is a BEHAVIOURAL preference, not
        #: account protection, and the faithful engine does not impose one.
        self.band = band

    def as_dict(self) -> Dict[str, Any]:
        return {"max_usd_per_market": str(self.max_usd_per_market),
                "max_abs_residual": str(self.max_abs_residual),
                "max_total_exposure_usd": str(self.max_total_exposure_usd),
                "max_concurrent_markets": self.max_concurrent_markets,
                "max_live_orders": self.max_live_orders,
                "daily_realized_loss_halt_usd":
                    str(self.daily_realized_loss_halt_usd),
                "band": [str(x) for x in self.band] if self.band else None}

    def worst_case_loss_usd(self) -> D:
        """The most this configuration can lose, before any trade happens.

        Every share bought can settle worthless, so committed dollars are the
        exposure. The halt bounds the day; the per-market and total caps bound
        a single probe. The binding number is the smaller of total exposure
        and the halt, because the halt stops new commitment but cannot unwind
        what is already at risk.
        """
        return max(self.max_total_exposure_usd,
                   self.daily_realized_loss_halt_usd)

    def refuse(self, *, price: D, qty: D, market: str,
               committed_by_market: Mapping[str, D],
               residual_after: D, realized_today: D,
               open_markets: Sequence[str],
               outstanding_by_market: Mapping[str, D],
               live_order_count: int,
               outstanding_residual: D = D(0),
               orders_uncertain: Optional[str] = None,
               risk_by_market: Optional[Mapping[str, D]] = None
               ) -> Optional[str]:
        """Refuse on POTENTIAL exposure: filled + still-fillable + this one.

        `outstanding_by_market` and `live_order_count` are REQUIRED rather
        than defaulted, so a caller that has not been taught about live
        orders fails loudly instead of silently losing the bound.
        """
        if orders_uncertain:
            return "ORDER_STATE_UNCERTAIN"
        if live_order_count >= self.max_live_orders:
            return "MAX_LIVE_ORDERS"
        if realized_today <= -self.daily_realized_loss_halt_usd:
            return "DAILY_REALIZED_LOSS_HALT"
        cost = price * qty
        if risk_by_market is None:
            # no risk model supplied: fall back to notional, which OVERSTATES
            # a short leg rather than understating it, so the fallback is
            # conservative and never lets more risk through than the bound.
            mk = (committed_by_market.get(market, D(0))
                  + outstanding_by_market.get(market, D(0)))
            if mk + cost > self.max_usd_per_market:
                return "MAX_USD_PER_MARKET"
            total = (sum(committed_by_market.values(), D(0))
                     + sum(outstanding_by_market.values(), D(0)))
            if total + cost > self.max_total_exposure_usd:
                return "MAX_TOTAL_EXPOSURE"
        else:
            if risk_by_market.get(market, D(0)) > self.max_usd_per_market:
                return "MAX_USD_PER_MARKET"
            if sum(risk_by_market.values(), D(0)) > self.max_total_exposure_usd:
                return "MAX_TOTAL_EXPOSURE"
        # residual is bounded on what would land if every live order filled
        if abs(residual_after + outstanding_residual) > self.max_abs_residual:
            return "MAX_ABS_RESIDUAL"
        live_markets = set(open_markets) | {
            m for m, v in outstanding_by_market.items() if v > 0}
        if (market not in live_markets
                and len(live_markets) >= self.max_concurrent_markets):
            return "MAX_CONCURRENT_MARKETS"
        if self.band is not None and not (self.band[0] <= price <= self.band[1]):
            return "BAND"
        return None


#: Probe 08's inventory classes. Every eligible action gets one, whether it
#: is permitted or refused, so the treatment can be read off the record
#: rather than inferred from what happened afterwards.
FLAT_OPEN = "FLAT_OPEN"
INCREASE_RESIDUAL = "INCREASE_RESIDUAL"
REDUCE_RESIDUAL = "REDUCE_RESIDUAL"


#: how a price arrived. There is no default anywhere: a caller that does not
#: say which convention its number is in does not get to book a fill.
WIRE = "WIRE"      # the venue's number: ALWAYS the long (YES) side's price
OWN = "OWN"        # already in the outcome's own terms


def own_outcome_price(side: str, price: D, *, basis: str) -> D:
    """The cash one share of `side` actually costs, in its own terms.

    THE VENUE QUOTES ONE NUMBER PER MARKET and it is always the LONG (YES)
    side's price (l5_order_body: "To buy NO at X the wire value is 1.00 - X").
    So a wire price p means an UP share costs p and a DOWN share costs 1 - p.

    Everything downstream of a fill is in OWN terms and always was:
    risk_exposure_usd computes `at_zero = -up_c + (dn_q - dn_c)`, which is
    only true if dn_c is what the DOWN shares cost; retire_settled_market
    pays a DOWN share $1 against dn_c for the same reason.

    LIVE 2026-09-26. apply_order_progress booked fills at the venue's avgPx,
    a WIRE number, straight into leg_cost, an OWN field. On 1600z that
    recorded 10.46 DOWN shares as costing 4.4399 when they cost
    10.46 - 4.4399 = 6.0201, reported a settlement of +5.0244 against a real
    +3.4442, and left realized_today reading +4.7662 while the account had
    actually made +1.9092. The same dn_c feeds the $5 exposure gate, so the
    gate was under-binding on exactly the cheap short legs this strategy
    accumulates. One conversion, in one place, is the whole fix.
    """
    p = D(str(price))
    if basis == OWN:
        return p
    if basis != WIRE:
        raise ValueError("price basis must be %r or %r, got %r"
                         % (WIRE, OWN, basis))
    return p if canonical_side(side, doing="pricing a fill") == UP else D(1) - p


def inventory_class(residual: D, side: str) -> str:
    """What this action would do to |residual|, from REALIZED inventory.

    Realized, not realized-plus-outstanding: the directive says "current
    realized residual", and an order that has not filled has not moved the
    imbalance. Outstanding orders are still priced as if they fill in full
    by `risk_exposure_usd`, so the dollar bound is unaffected by this
    choice; only the classification is.
    """
    up = canonical_side(side, doing="classifying an action") == UP
    if residual == 0:
        return FLAT_OPEN
    return INCREASE_RESIDUAL if ((residual > 0) == up) else REDUCE_RESIDUAL


class Provenance:
    """One record per L5 action, carrying the whole chain."""

    def __init__(self, action_key: str, l5: Mapping[str, Any],
                 market: str, window: str) -> None:
        self.rec: Dict[str, Any] = {
            "action_key": action_key, "market": market, "window": window,
            "L5_NATIVE": dict(l5), "stage_reached": "L5_NATIVE",
            "PMUS_TRANSLATION": None, "SAFETY_OVERLAY": None,
            "ORDER": None, "FILL": None, "INVENTORY": None,
            "SETTLEMENT": None, "REALIZED": None,
            "refused_by_layer": None, "refused_reason": None,
            "labels": ("provenance chain; a stage that did not happen is "
                       "recorded as not having happened"),
        }

    def refuse(self, layer: str, reason: str) -> Dict[str, Any]:
        self.rec["refused_by_layer"] = layer
        self.rec["refused_reason"] = reason
        return self.rec

    def stage(self, name: str, payload: Mapping[str, Any]) -> None:
        self.rec[name] = dict(payload)
        self.rec["stage_reached"] = name

    def note(self, name: str, payload: Mapping[str, Any]) -> None:
        """Record a fact about the action without claiming a stage was
        reached. Classification is not progress through the chain."""
        self.rec[name] = dict(payload)


#: What a venue POSITION ROW calls the market. `positions()` returns rows
#: built by `rows_of`, which folds the response's mapping KEY back in as
#: `marketSlug` -- so the row never carries `market`, which is what
#: `reconcile_from_venue` used to subscript. Probe 04's account was flat, the
#: loop never ran, and the bug stayed invisible; Probe 05 closed out holding
#: positions, the loop ran, and `p["market"]` raised KeyError.
VENUE_POSITION_MARKET_KEYS = ("marketSlug", "market", "slug")

#: What it calls the quantity. Taken from `pmus_settlement_auditor.
#: _net_position`, which has read these against real fills and settlements on
#: this venue, rather than invented here. `qtyDecimal`-style decimal fields
#: come FIRST: the auditor measured `qty` disagreeing with its own decimal
#: twin on 12 of 13 rows, and preferring the wrong one produced a 17x
#: phantom position.
VENUE_POSITION_QTY_KEYS = ("netPositionDecimal", "netPosition", "quantity",
                           "qty")

#: What the row calls the two halves of the position. These need NO sign
#: convention: shares bought and shares sold are unambiguous, and their
#: difference is long-positive by construction. Decimal twins first, because
#: the settlement auditor measured `qty` disagreeing with its own decimal
#: field on 12 of 13 rows.
VENUE_POSITION_BOUGHT_KEYS = ("qtyBoughtDecimal", "qtyBought")
VENUE_POSITION_SOLD_KEYS = ("qtySoldDecimal", "qtySold")

#: The signed net position. Probe 06 captured the real field list of a
#: POPULATED row and there is no side field in it at all:
#:
#:   avgPx baseCost bodPosition bodPositionDecimal cashValue comboLegDetails
#:   cost costPerShare expired fees marketMetadata marketSlug netPosition
#:   netPositionDecimal positionId qtyAvailable qtyAvailableDecimal qtyBought
#:   qtyBoughtDecimal qtySold qtySoldDecimal realized updateTime
#:
#: so the direction lives in the SIGN. Which sign means long is not asserted
#: here: no populated row's VALUES have ever been captured, the account was
#: flat by the time one could be, and nothing in this repository has ever
#: read the field for anything but a non-zero check. The convention is
#: therefore proven per row, from `qtyBought - qtySold`, and a net that
#: disagrees with that difference fails closed.
VENUE_POSITION_NET_KEYS = ("netPositionDecimal", "netPosition")

class UnrecognizedVenueShape(RuntimeError):
    """A venue row we cannot read. Carries the structure, never a guess.

    Raised instead of KeyError so the caller can fail CLOSED with evidence:
    which keys the row actually had. That is the thing we did not have after
    Probe 05 and had to reason about from the exception name alone.
    """

    #: fields of a position row that are numbers or identifiers -- never a
    #: credential, and the only things that can settle a shape question
    VALUE_FIELDS = ("marketSlug", "netPosition", "netPositionDecimal",
                    "qtyBought", "qtyBoughtDecimal", "qtySold",
                    "qtySoldDecimal", "qtyAvailable", "qtyAvailableDecimal",
                    "bodPosition", "bodPositionDecimal", "avgPx", "cost",
                    "costPerShare", "baseCost", "cashValue", "realized",
                    "fees", "expired", "positionId", "updateTime")

    def __init__(self, what: str, rows: Sequence[Mapping[str, Any]]) -> None:
        super().__init__(what)
        self.what = what
        self.rows = [sorted(r) for r in rows if isinstance(r, Mapping)]
        # Probe 06 reported the KEYS of the first populated position row we
        # had ever seen and not its values, so the one question the capture
        # existed to answer -- which sign means long -- still could not be
        # answered, and by then the account was flat again. The values come
        # out too now. They are quantities and identifiers; nothing here is
        # a credential.
        self.values = [{k: r.get(k) for k in self.VALUE_FIELDS if k in r}
                       for r in rows if isinstance(r, Mapping)]


def _first_key(row: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


#: What a venue OPEN-ORDER row calls its limit price. Probes 04-07 never
#: exercised the adoption path at all -- every one of them closed out with
#: nothing resting, so `open_orders` was empty and the price was never read
#: from a real row. Probe 08 is the first probe whose treatment left an order
#: resting at the close, and the line that read it had been written against
#: a shape nobody had seen: `str(o.get("price", ...))` produced the STRING
#: "None" (or a dict repr) and `Decimal` refused it, killing the report after
#: the close-out had already run.
VENUE_ORDER_PRICE_KEYS = ("price", "limitPrice", "limit_price", "orderPrice",
                          "px", "avgPx", "priceDecimal")

#: PMUS wraps some numbers as objects rather than scalars, so a price may
#: arrive as {"value": "0.66"}. Unwrapped by name, never by guessing.
VENUE_SCALAR_KEYS = ("value", "amount", "decimal")


def venue_decimal(raw: Any) -> Optional[D]:
    """A venue number as a Decimal, or None when it cannot be read.

    Accepts a number, a numeric string, or a single-valued wrapper object.
    Returns None rather than raising, because the callers are RISK metrics
    and a risk metric that raises stops the process at exactly the moment it
    is most needed. What the callers must not do is read None as zero.
    """
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        for k in VENUE_SCALAR_KEYS:
            if k in raw:
                return venue_decimal(raw[k])
        return None
    if isinstance(raw, bool):
        return None
    try:
        return D(str(raw))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def venue_order_price(row: Mapping[str, Any]) -> Optional[D]:
    """The limit price off a venue order row, by name, or None."""
    if not isinstance(row, Mapping):
        return None
    for k in VENUE_ORDER_PRICE_KEYS:
        if k in row:
            px = venue_decimal(row[k])
            if px is not None:
                return px
    return None


#: What an order whose price we cannot read is worth, per share, when
#: bounding risk. A share cannot settle above $1, so $1 is the worst it can
#: have cost us -- and the one thing that must never happen is reading an
#: unknown price as free.
UNKNOWN_PRICE_WORST_CASE = D(1)


class UnknownSide(RuntimeError):
    """A side word no layer of this system is allowed to guess at.

    LIVE 2026-09-26, 2115z. An order adopted from the venue kept the venue's
    own word for its side -- "ORDER_SIDE_SELL" -- and carried it all the way
    into apply_fill, which builds its ledger key as `market|side`. Three and
    a quarter DOWN shares and every dollar they cost went into
    `...2115z|ORDER_SIDE_SELL`, a key that leg_cost(), risk_exposure_usd and
    retire_settled_market never read, because those read only `|UP` and
    `|DOWN`. The venue's net then arrived under `...2115z|DOWN` with no cost
    beside it, the market settled reading all zeros, and $0.66 of real cost
    settled off the books.

    Nothing anywhere else in this file decided that "ORDER_SIDE_SELL" was
    DOWN. It never had to: `own_outcome_price` asked `startswith("U")`, which
    is False for ORDER_SIDE_SELL and therefore happened to price it as a
    DOWN leg, and False for ORDER_SIDE_BUY too -- so an adopted BUY would
    have been priced at `1 - p` AND filed under the wrong key at once. A
    convention that survives only because two wrongs agree is not a
    convention. This is the one that is written down.
    """


UP = "UP"
DOWN = "DOWN"
CANONICAL_SIDES = (UP, DOWN)

#: Every word this venue has been OBSERVED to use for a side, and what it
#: means in our terms. Sourced from authoritative /v1/order rows read by the
#: read-only recovery service on 2026-09-26, which pair them exactly:
#:
#:   side ORDER_SIDE_BUY  + outcomeSide OUTCOME_SIDE_YES + ORDER_INTENT_BUY_LONG
#:   side ORDER_SIDE_SELL + outcomeSide OUTCOME_SIDE_NO  + ORDER_INTENT_BUY_SHORT
#:
#: The venue's `side` is about the YES token: it expresses "buy NO" as "sell
#: YES". `outcomeSide` names the outcome you end up long. They are two views
#: of one fact, which is why venue_row_side below requires them to agree
#: rather than picking a favourite.
VENUE_SIDE_WORDS = {
    # our own words, so a round trip through this table is the identity
    "UP": UP, "DOWN": DOWN,
    # the outcome, named directly
    "YES": UP, "NO": DOWN,
    "OUTCOME_SIDE_YES": UP, "OUTCOME_SIDE_NO": DOWN,
    # the order's direction on the YES token
    "BUY": UP, "SELL": DOWN,
    "ORDER_SIDE_BUY": UP, "ORDER_SIDE_SELL": DOWN,
    # the order's stated intent
    "LONG": UP, "SHORT": DOWN,
    "ORDER_INTENT_BUY_LONG": UP, "ORDER_INTENT_BUY_SHORT": DOWN,
}

#: The keys of a venue order/fill row that state a side, most direct first.
VENUE_ROW_SIDE_KEYS = ("outcomeSide", "outcome_side", "intent", "side")


def venue_side(raw: Any) -> Optional[str]:
    """UP or DOWN, or None when the venue said something we do not know.

    Non-raising, for callers that want to test a word. Anything that is
    about to write to the ledger uses canonical_side instead.
    """
    if raw is None:
        return None
    return VENUE_SIDE_WORDS.get(str(raw).strip().upper())


def canonical_side(raw: Any, *, doing: str) -> str:
    """UP or DOWN, or refuse. THE ONLY WAY A SIDE ENTERS THE LEDGER.

    Fails closed on purpose. A side we cannot map is not a DOWN by default
    and not an UP by default -- it is a fact about our own position that we
    do not have, and booking inventory against it is how 2115z happened.
    """
    got = venue_side(raw)
    if got is None:
        raise UnknownSide(json.dumps({
            "raw": None if raw is None else str(raw),
            "doing": doing, "accepts": sorted(VENUE_SIDE_WORDS),
            "why": ("internal accounting accepts only canonical UP/DOWN; an "
                    "unmapped side word must not reach a ledger key, a "
                    "price conversion, an exposure bound or a settlement")}))
    return got


def venue_row_side(row: Mapping[str, Any], *, doing: str) -> str:
    """The canonical side of one venue order/fill row, cross-checked.

    A row usually states its side three times -- outcomeSide, intent and
    side. Where more than one is present they must agree once mapped: a row
    saying OUTCOME_SIDE_YES and ORDER_SIDE_SELL at the same time is not a
    row this system understands (it would be selling a YES position, which
    no path here creates), and resolving it in favour of whichever key we
    happened to read first is exactly the guess this refuses to make.
    """
    seen: Dict[str, str] = {}
    for key in VENUE_ROW_SIDE_KEYS:
        if key not in row or row.get(key) is None:
            continue
        got = venue_side(row.get(key))
        if got is not None:
            seen[key] = got
    if not seen:
        raise UnknownSide(json.dumps({
            "doing": doing, "row_keys": sorted(row),
            "why": "no recognisable side on this venue row"}))
    if len(set(seen.values())) > 1:
        raise UnknownSide(json.dumps({
            "doing": doing, "disagreement": seen,
            "why": ("this row states its side more than once and the "
                    "statements do not agree")}))
    return next(iter(seen.values()))


def position_row(p: Mapping[str, Any]) -> Dict[str, str]:
    """One venue position row as market / side / qty, or refuse it.

    The row has no side. It has a SIGNED net position, and two unsigned
    halves -- shares bought and shares sold -- whose difference is
    long-positive by construction and needs no convention at all. That
    difference is what decides the side here.

    `netPositionDecimal` is then used as a CHECK, not as the source: if it is
    present and disagrees with bought-minus-sold, the row is refused rather
    than resolved in favour of one of them. A row carrying only the signed
    net, with no halves to prove the convention against, is also refused --
    guessing which sign means long is exactly the assumption this function
    exists to avoid.
    """
    if not isinstance(p, Mapping):
        raise UnrecognizedVenueShape("position row is not an object", [])
    market = _first_key(p, VENUE_POSITION_MARKET_KEYS)
    if market is None:
        raise UnrecognizedVenueShape("position row lacks market", [p])
    bought = _first_key(p, VENUE_POSITION_BOUGHT_KEYS)
    sold = _first_key(p, VENUE_POSITION_SOLD_KEYS)
    net = _first_key(p, VENUE_POSITION_NET_KEYS)
    if bought is None or sold is None:
        raise UnrecognizedVenueShape(
            "position row carries no bought/sold halves, so the sign "
            "convention of its net position cannot be proven from it", [p])
    signed = _as_qty(bought) - _as_qty(sold)
    if net is not None:
        n = _as_qty(net)
        if n != signed:
            raise UnrecognizedVenueShape(
                "net position %s disagrees with qtyBought - qtySold %s"
                % (n, signed), [p])
    side = "UP" if signed >= 0 else "DOWN"
    qty = signed if signed >= 0 else -signed
    return {"market": str(market), "side": side, "qty": str(qty),
            "signed_net": str(signed)}


def _as_qty(v: Any) -> D:
    if isinstance(v, Mapping):                 # {"value": "...", "currency"}
        v = v.get("value")
    try:
        return D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        raise UnrecognizedVenueShape("quantity %r is not a number" % (v,), [])


class InventoryStore:
    """Persisted inventory. The VENUE is authoritative; this is a cache."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.state: Dict[str, Any] = {"positions": {}, "committed": {},
                                      "realized_today": "0", "day": None,
                                      "submitted": {}, "live_orders": {},
                                      "orders_uncertain": None}
        self._load()

    def _load(self) -> None:
        p = pathlib.Path(self.path)
        if p.exists():
            try:
                self.state.update(json.loads(p.read_text()))
            except ValueError:
                pass

    def save(self) -> None:
        p = pathlib.Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.state, default=str))
        tmp.replace(p)                       # atomic: never a torn file

    def committed(self) -> Dict[str, D]:
        return {k: D(str(v)) for k, v in self.state["committed"].items()}

    # ---- live orders -------------------------------------------------
    # An order is EXPOSURE from the moment it is submitted, not from the
    # moment it fills. `committed` counts filled notional and cannot see a
    # resting order, so on its own it would let any number of unfilled
    # orders sit at the venue whose combined fillable notional exceeds the
    # envelope. Everything below exists so the caps apply to POTENTIAL
    # exposure: filled + still-fillable.

    def live_orders(self) -> Dict[str, Dict[str, Any]]:
        return {k: dict(v) for k, v in
                (self.state.get("live_orders") or {}).items()}

    def live_order_count(self) -> int:
        return len(self.state.get("live_orders") or {})

    def outstanding_by_market(self) -> Dict[str, D]:
        """Still-fillable notional per market, at the price we offered."""
        out: Dict[str, D] = {}
        for o in (self.state.get("live_orders") or {}).values():
            m = str(o.get("market"))
            rem = venue_decimal(o.get("remaining")) or D(0)
            if rem <= 0:
                continue
            px = venue_decimal(o.get("price"))
            if px is None:
                px = UNKNOWN_PRICE_WORST_CASE
            out[m] = out.get(m, D(0)) + px * rem
        return out

    def outstanding_residual(self, market: str) -> D:
        """Signed shares that would land if every live order filled."""
        r = D(0)
        for o in (self.state.get("live_orders") or {}).values():
            if str(o.get("market")) != market:
                continue
            rem = venue_decimal(o.get("remaining")) or D(0)
            r += rem if str(o.get("side")) == "UP" else -rem
        return r

    def orders_uncertain(self) -> Optional[str]:
        """Set when we cannot say whether an order reached the venue.

        Cleared ONLY by an authoritative venue reconciliation. While set,
        every further submission is refused: an unknown order is assumed to
        be live and consuming the envelope, because assuming otherwise is
        the assumption that loses money.
        """
        return self.state.get("orders_uncertain") or None

    def mark_uncertain(self, why: str) -> None:
        self.state["orders_uncertain"] = str(why)
        self.save()

    def attach_venue_id(self, coid: str, venue_order_id: Any) -> None:
        """Record the id the VENUE gave this order.

        Without it a cancel has nothing to name: the venue cancels by its own
        order id, not by our deterministic client order id, and passing the
        latter would silently cancel nothing.
        """
        live = self.state.setdefault("live_orders", {})
        if coid in live and venue_order_id:
            live[coid]["venue_order_id"] = str(venue_order_id)
            self.save()

    def terminate_order(self, coid: str, *, reason: str) -> None:
        """A venue-authoritative terminal state releases the envelope."""
        live = self.state.setdefault("live_orders", {})
        if coid in live:
            live[coid]["state"] = str(reason)
            live[coid]["remaining"] = "0"
            live.pop(coid)
            self.save()

    def position(self, market: str, side: str) -> D:
        return D(str(self.state["positions"].get("%s|%s" % (market, side), 0)))

    def residual(self, market: str) -> D:
        return self.position(market, "UP") - self.position(market, "DOWN")

    def realized_today(self) -> D:
        """The loss halt's authority: attributed settled P&L PLUS the
        explicit difference we could not attribute.

        Two lines, never one. `realized_attributed` is the sum of settlement
        results computed from filled inventory in own-outcome terms.
        `realized_unattributed` is what an authoritative cash anchor says is
        left over -- windows whose settlement record we do not hold, fees,
        credits, anything not yet traced to a line. Adding them is not the
        same as setting realized := balance: the parts stay separate and
        auditable, and the halt sees the whole of what the account did.

        The legacy scalar is still read if the new fields are absent, so a
        store written before this change keeps working.
        """
        if ("realized_attributed" in self.state
                or "realized_unattributed" in self.state):
            return (D(str(self.state.get("realized_attributed") or 0))
                    + D(str(self.state.get("realized_unattributed") or 0)))
        return D(str(self.state.get("realized_today") or 0))

    def realized_lines(self) -> Dict[str, str]:
        """Every line apart, for reporting. Never collapsed by this method."""
        return {
            "realized_attributed": str(
                D(str(self.state.get("realized_attributed") or 0))),
            "realized_unattributed": str(
                D(str(self.state.get("realized_unattributed") or 0))),
            "realized_total": str(self.realized_today()),
            "cash_anchor": dict(self.state.get("cash_anchor") or {}),
            "legacy_realized_today": str(
                D(str(self.state.get("realized_today") or 0))),
            "labels": ("attributed is settlement P&L from filled inventory "
                       "in own-outcome terms; unattributed is the explicit "
                       "difference against an authoritative cash anchor and "
                       "is NOT a trading result. Fees, maker rebates and "
                       "rewards are separate confirmed lines and are not in "
                       "either. A zero commission is NOT a rebate."),
        }

    def rebase_session_accounting(self, *, attributed: D, cash_delta: D,
                                  anchor: Mapping[str, Any],
                                  reason: str) -> Dict[str, Any]:
        """Replace a contaminated realized total with a decomposed one.

        Used once, after the own-terms cost-basis defect was found: the
        stored realized_today of +4.7662 was computed from wire-priced DOWN
        legs and cannot be repaired in place, because the per-fill prices
        that produced it are gone.

        `attributed` is what we CAN derive from evidence we hold.
        `cash_delta` is what the venue's balance actually did between two
        authoritative flat reads. The remainder is recorded as unattributed
        rather than folded into the trading line, because calling an
        unexplained cash movement a trading result is the thing this whole
        accounting discipline exists to prevent.
        """
        att = D(str(attributed))
        cash = D(str(cash_delta))
        prior = dict(self.state.get("accounting_rebases") or {})
        self.state["realized_attributed"] = str(att)
        self.state["realized_unattributed"] = str(cash - att)
        self.state["cash_anchor"] = dict(anchor)
        hist = self.state.setdefault("accounting_rebase_log", [])
        hist.append({"t": dt.datetime.now(dt.timezone.utc).isoformat(),
                     "reason": reason,
                     "superseded_realized_today": str(
                         D(str(self.state.get("realized_today") or 0))),
                     "attributed": str(att),
                     "unattributed": str(cash - att),
                     "cash_delta": str(cash)})
        # the contaminated scalar is KEPT, not deleted, and stops being the
        # authority: realized_today() prefers the decomposed fields.
        self.save()
        return dict(self.realized_lines(), rebases=len(hist), prior=prior)

    def already_submitted(self, coid: str) -> bool:
        return coid in (self.state.get("submitted") or {})

    def record_submit(self, coid: str, intent: Mapping[str, Any],
                      *, session_id: Optional[str] = None,
                      mode: Optional[str] = None) -> None:
        rec = {
            "t": dt.datetime.now(dt.timezone.utc).isoformat(),
            "market": intent.get("market"), "side": intent.get("side"),
            "price": str(intent.get("price")), "qty": str(intent.get("qty")),
            # RUN PROVENANCE, stamped at submit. The inventory store is a file
            # on a volume that outlives the process, so without this a later
            # reconciliation cannot tell this run's orders from Probe 04's.
            "session_id": session_id,
            "mode": mode,
        }
        self.state.setdefault("submitted", {})[coid] = rec
        # live from this instant: the envelope must already count it.
        self.state.setdefault("live_orders", {})[coid] = dict(
            rec, remaining=str(intent.get("qty")), state="LIVE")
        self.save()

    def live_orders_for_session(self, session_id: str) -> Dict[str, Any]:
        """This run's resting orders, and only this run's."""
        return {k: dict(v) for k, v in (self.state.get("live_orders") or {}).items()
                if str(v.get("session_id") or "") == str(session_id)}

    def live_orders_other_sessions(self, session_id: str) -> Dict[str, Any]:
        """Anything resting that this run did not place. Never cancelled here.

        An order from an earlier session is still OUR exposure and still
        counts against the envelope, but cancelling it is not this run's
        business -- it did not place it and cannot know what it was for.
        """
        return {k: dict(v) for k, v in (self.state.get("live_orders") or {}).items()
                if str(v.get("session_id") or "") != str(session_id)}

    def apply_fill(self, market: str, side: str, qty: D, price: D,
                   *, coid: Optional[str] = None, basis: str) -> None:
        """Book a fill. `basis` says which convention `price` is in.

        It is keyword-only and has NO DEFAULT on purpose: a caller that does
        not state its convention raises TypeError here rather than quietly
        booking a wire price into an own-terms field, which is exactly how
        the 1600z cost basis went wrong. Normalisation happens ONCE, here,
        so the ACK, progress and reconcile paths cannot disagree.

        THE SIDE IS NORMALISED HERE TOO, and for the same reason. This is
        the only writer of `positions` and `leg_cost` on the fill path, so
        it is the only place the ledger key is built -- and on 2115z it
        built `market|ORDER_SIDE_SELL` from a word the venue chose. The raw
        word is kept beside the entry for audit; the KEY is always canonical.
        """
        own_side = canonical_side(side, doing="booking a fill")
        own = own_outcome_price(own_side, price, basis=basis)
        k = "%s|%s" % (market, own_side)
        if str(side).strip().upper() != own_side:
            raw = self.state.setdefault("venue_side_raw", {})
            raw.setdefault(k, sorted(set(raw.get(k, []))
                                     | {str(side)}))
        self.state["positions"][k] = str(
            D(str(self.state["positions"].get(k, 0))) + qty)
        # both of these are CASH OUT in the leg's own terms now. They used to
        # take the wire number, which credits a short instead of charging it.
        self.state["committed"][market] = str(
            D(str(self.state["committed"].get(market, 0))) + own * qty)
        lc = self.state.setdefault("leg_cost", {})
        lc[k] = str(D(str(lc.get(k, 0))) + own * qty)
        # HOW MANY FILLS BUILT THIS LEG. A leg with shares and no cost is
        # either a genuine fill at $0.00 -- rare but real, and attested by
        # this counter -- or quantity that arrived from the venue's net
        # with no cost basis at all, which is 2045z. book_violations reads
        # this to tell the two apart instead of guessing.
        fc = self.state.setdefault("fill_count", {})
        fc[k] = int(fc.get(k, 0)) + 1
        # A fill converts outstanding exposure into committed exposure. Not
        # decrementing here would double-count the same shares in both.
        live = self.state.setdefault("live_orders", {})
        o = live.get(coid) if coid else None
        if o is not None:
            rem = D(str(o.get("remaining") or 0)) - qty
            if rem <= 0:
                live.pop(coid, None)
            else:
                o["remaining"] = str(rem)
                o["state"] = "PARTIALLY_FILLED"
        self.save()


    def leg_cost(self, market: str) -> Dict[str, D]:
        lc = self.state.get("leg_cost") or {}
        return {side: D(str(lc.get("%s|%s" % (market, side), 0)))
                for side in ("UP", "DOWN")}

    def fill_count(self, market: str, side: str) -> int:
        """How many fills built this leg. 0 means nothing we saw did."""
        fc = self.state.get("fill_count") or {}
        return int(fc.get("%s|%s" % (market, side), 0) or 0)

    def cost_attested(self, market: str, side: str) -> bool:
        """Did a fill actually state this leg's price, whatever it was?

        A leg built by apply_fill has an attested basis even when that basis
        is $0.00 -- an UP fill at a wire price of 0.00 costs nothing and is
        a real thing that can happen. A leg whose quantity was written by
        reconcile from the venue's net has no attestation at all, and the
        difference between those two is the difference between free shares
        and shares whose cost we lost.
        """
        return self.fill_count(market, side) > 0

    #: a share of a $0-$1 contract cannot have cost more than $1 or less
    #: than $0. Fees are attributable separately and are never folded into a
    #: leg's cost, so this bound is exact rather than approximate.
    MAX_OWN_COST_PER_SHARE = D(1)

    def book_violations(self, market: str) -> List[Dict[str, Any]]:
        """Ways this market's book cannot be true. Empty means coherent.

        LIVE 2026-09-26, 1815z settled on up_qty 1.8100, dn_qty 0, up_cost
        0.0931 and dn_cost 4.2494 -- four dollars of cost against zero DOWN
        shares -- and priced the surviving 1.81 UP shares at 4.3425, which is
        $2.40 a share for an instrument that pays at most $1. It posted
        -4.3425 to realized and moved the loss halt with it.

        That book was not produced by any trade. reconcile_from_venue
        rewrote `positions` and never touched `leg_cost`, so when the venue's
        NET replaced our GROSS the matched pair's quantities vanished while
        both legs' costs stayed. The quantities and the costs are one ledger
        and must be written together; this is the check that says so.
        """
        out: List[Dict[str, Any]] = []
        lc = self.leg_cost(market)
        for side in ("UP", "DOWN"):
            q = self.position(market, side)
            c = lc[side]
            if q < 0:
                out.append({"violation": "NEGATIVE_QUANTITY", "side": side,
                            "qty": str(q)})
            if c < 0:
                out.append({"violation": "NEGATIVE_COST", "side": side,
                            "cost": str(c)})
            if q == 0 and c != 0:
                out.append({"violation": "COST_WITHOUT_QUANTITY",
                            "side": side, "qty": str(q), "cost": str(c),
                            "why": ("cost survived a quantity that did not; "
                                    "the two were written independently")})
            if q > 0 and c == 0 and not self.cost_attested(market, side):
                # THE MIRROR, and the one that was missing. 2045z: an order
                # filled, the venue dropped it from /v1/orders/open, its
                # /v1/order/{id} read raised, reconcile released it without
                # booking the fill, and the venue's NET then supplied 2.25
                # DOWN shares with no cost beside them. per_share was
                # 0/2.25 = 0, which is inside [0,1], so every check below
                # passed: the shares priced at zero risk and settled at
                # zero P&L. Real money, spent, reported as free inventory.
                #
                # Shares we hold were paid for. A leg with quantity and no
                # cost is therefore not free inventory, it is a cost basis
                # we have LOST -- unless a fill actually attested a $0.00
                # price, which fill_count records.
                out.append({"violation": "QUANTITY_WITHOUT_COST",
                            "side": side, "qty": str(q), "cost": str(c),
                            "fills_booked": self.fill_count(market, side),
                            "why": ("quantity with no cost basis and no "
                                    "fill that attested a zero price; the "
                                    "cost of these shares is unknown, not "
                                    "zero")})
            if q > 0:
                per = c / q
                if per > self.MAX_OWN_COST_PER_SHARE or per < 0:
                    out.append({"violation": "COST_PER_SHARE_IMPOSSIBLE",
                                "side": side, "qty": str(q), "cost": str(c),
                                "per_share": str(per),
                                "bound": str(self.MAX_OWN_COST_PER_SHARE),
                                "why": ("a share of a $0-$1 contract cannot "
                                        "have cost this much")})
        return out

    def require_coherent_book(self, market: str, *, doing: str) -> None:
        """Fail closed before risk or settlement reads an impossible book."""
        bad = self.book_violations(market)
        if not bad:
            return
        self.mark_uncertain(
            "incoherent book for %s while %s: %s"
            % (market, doing, ";".join(str(v.get("violation")) for v in bad)))
        raise IncoherentBook(json.dumps({"market": market, "doing": doing,
                                         "violations": bad}))

    def cash_deployed_usd(self, market: str) -> Dict[str, str]:
        """Gross cash actually paid out into this market. REPORTING ONLY.

        This is NOT the gate and must not become one without being asked
        for. The gate is risk_exposure_usd, which bounds worst-case LOSS
        over both settlements, and it stays exactly as it was.

        The two differ by more than people expect and 2100z is the example:
        $7.4871 of cash went out (5.67 UP at 5.1627 plus 18.89 DOWN at
        2.3244) while the worst case over either settlement was $1.8171. A
        bound on loss permits a cash outlay several times its own size,
        because the two legs pay for each other at settlement. That is
        sound, and it is also worth SEEING, so it is reported beside the
        gate rather than folded into it.
        """
        lc = self.leg_cost(market)
        return {
            "market": market,
            "cash_deployed_usd": str(D(str(
                (self.state.get("committed") or {}).get(market, 0)))),
            "leg_cost_up": str(lc["UP"]), "leg_cost_down": str(lc["DOWN"]),
            "leg_cost_total": str(lc["UP"] + lc["DOWN"]),
            "labels": ("gross cash out in own-outcome terms. NOT the "
                       "worst-case-loss gate and not bounded by it; "
                       "reported so a large two-sided outlay under a small "
                       "loss bound is visible rather than implicit."),
        }

    def risk_exposure_usd(self, market: str, *, extra_side: str = "",
                          extra_qty: D = D(0), extra_price: D = D(0)) -> D:
        """Worst-case cash loss in this market, over both settlements.

        `committed` is not this number and never was: it adds a DOWN fill's
        notional as cash out when the venue CREDITS it, so on Probe 05 it
        read $2.8479 against $0.4615 of movement. It stays for reporting
        cash; the gates read this.

        Prices are our YES-convention prices, so an UP share costs `price`
        and a DOWN share -- a short -- takes in `1 - price` and owes $1 if
        YES settles 1. Both legs are evaluated under the SAME settlement, so
        a genuine hedge reduces the bound while a short's credit never buys
        headroom on its own.

        An order that has not filled is priced as though it fills in full,
        which is what `extra_*` and the outstanding legs are for.
        """
        self.require_coherent_book(market, doing="pricing risk")
        pos = self.state.get("positions") or {}
        up_q = D(str(pos.get("%s|UP" % market, 0)))
        dn_q = D(str(pos.get("%s|DOWN" % market, 0)))
        cost = self.leg_cost(market)
        up_c, dn_c = cost["UP"], cost["DOWN"]
        for o in (self.state.get("live_orders") or {}).values():
            if str(o.get("market")) != market:
                continue
            q = venue_decimal(o.get("remaining")) or D(0)
            px = venue_decimal(o.get("price"))
            if px is None:
                # a price we cannot read is priced at the most it could have
                # cost. On BOTH legs the higher price is the worse case, so
                # $1 a share bounds it either way -- and reading it as free,
                # which is what `or 0` did, would have handed the gate a
                # position that looked costless.
                px = UNKNOWN_PRICE_WORST_CASE
            # canonical_side, not a prefix test: an order carrying the
            # venue's own word would be silently bucketed as DOWN here and
            # the gate would bound the wrong leg.
            #
            # A side we cannot read at all is priced like a price we cannot
            # read -- at the worst it could be -- rather than raising out of
            # the hot path. Charging its notional against BOTH settlements
            # with no offsetting quantity makes at_zero and at_one each
            # worse by exactly that amount and can never create redemption
            # that does not exist, so the bound is strictly conservative
            # whichever side it turns out to be.
            try:
                is_up = canonical_side(
                    o.get("side"), doing="pricing an outstanding order") == UP
            except UnknownSide:
                up_c += px * q
                dn_c += px * q
                continue
            if is_up:
                up_q += q
                up_c += px * q
            else:
                dn_q += q
                dn_c += px * q
        if extra_qty > 0:
            if canonical_side(extra_side,
                              doing="pricing a proposed action") == UP:
                up_q += extra_qty
                up_c += extra_price * extra_qty
            else:
                dn_q += extra_qty
                dn_c += extra_price * extra_qty
        # cash if YES settles 0, and if it settles 1
        at_zero = -up_c + (dn_q - dn_c)
        at_one = (up_q - up_c) - dn_c
        worst = max(-at_zero, -at_one)
        return worst if worst > 0 else D(0)

    def applied_fill_qty(self, coid: str) -> D:
        """How much of this order we have already booked. Idempotency key."""
        o = (self.state.get("live_orders") or {}).get(coid) or {}
        done = (self.state.get("filled_qty") or {}).get(coid)
        if done is not None:
            return D(str(done))
        return D(str(o.get("applied") or 0))

    def apply_order_progress(self, coid: str, prog: Mapping[str, Any],
                             *, market: str, side: str) -> Dict[str, Any]:
        """Book the venue's cumulative fill for one order, exactly once.

        The delta, never the total: polling is idempotent because a second
        call with the same cumQuantity books nothing. A terminal order stops
        consuming the envelope and frees the one-live-order slot; a partial
        keeps the remainder outstanding.
        """
        was_live = coid in (self.state.get("live_orders") or {})
        already = self.applied_fill_qty(coid)
        cum = D(str(prog.get("cum_qty") or 0))
        delta = cum - already
        out = {"coid": coid, "already": str(already), "cum": str(cum),
               "delta": str(delta), "terminal": bool(prog.get("terminal")),
               "state": prog.get("state")}
        if delta > 0:
            # avgPx is the VENUE's number, so it is WIRE and gets converted
            self.apply_fill(market, side, delta,
                            D(str(prog.get("avg_px") or 0)), coid=coid,
                            basis=WIRE)
            self.state.setdefault("filled_qty", {})[coid] = str(cum)
            fees = self.state.setdefault("fees", {})
            fees[coid] = str(D(str(prog.get("commission") or 0)))
        elif delta < 0:
            # the venue went BACKWARDS on a cumulative field. Do not guess.
            self.mark_uncertain(
                "venue cum_qty %s below booked %s for %s" % (cum, already,
                                                             coid))
            out["refused"] = "CUM_QTY_WENT_BACKWARDS"
            self.save()
            return out
        if prog.get("terminal"):
            # whatever remains is not live: the venue says the order is done.
            # `released` reports that it WAS holding the slot on entry -- a
            # full fill has already popped it inside apply_fill, and the slot
            # is no less freed for that.
            self.state.setdefault("filled_qty", {})[coid] = str(cum)
            self.state.setdefault("live_orders", {}).pop(coid, None)
            out["released"] = bool(was_live)
        self.save()
        return out

    def canonicalize_position_keys(self) -> Dict[str, Any]:
        """Merge ledger keys written under a raw venue side word.

        MIGRATION, for a state file written before canonical_side existed.
        The live volume holds `...2115z|ORDER_SIDE_SELL` with 3.25 shares
        and $0.66 of cost beside it. Left alone those keys are now counted
        as uncanonical by _gross_by_market and skipped by _net_by_market,
        which is a permanent gross/net disagreement and therefore a book
        that is uncertain forever and never submits again.

        So they are MERGED, not dropped: quantity and cost move together
        into the canonical key, which is exactly where apply_fill should
        have put them. A key whose word maps to nothing at all is left
        alone -- inventing a side for it would be the original sin again --
        and named in the result so it is visible.
        """
        moved: List[Dict[str, Any]] = []
        unmapped: List[str] = []
        for name in ("positions", "leg_cost", "fill_count"):
            d = self.state.get(name)
            if not isinstance(d, dict):
                continue
            for key in [k for k in d if "|" in str(k)]:
                market, raw = str(key).rsplit("|", 1)
                if raw in CANONICAL_SIDES:
                    continue
                got = venue_side(raw)
                if got is None:
                    unmapped.append(key)
                    continue
                dest = "%s|%s" % (market, got)
                if name == "fill_count":
                    d[dest] = int(d.get(dest, 0) or 0) + int(d.pop(key) or 0)
                else:
                    d[dest] = str(D(str(d.get(dest, 0)))
                                  + D(str(d.pop(key))))
                moved.append({"store": name, "from": key, "to": dest})
                raws = self.state.setdefault("venue_side_raw", {})
                raws.setdefault(dest, [])
                if raw not in raws[dest]:
                    raws[dest].append(raw)
        if moved:
            self.state.setdefault("key_migrations", []).append(
                {"t": dt.datetime.now(dt.timezone.utc).isoformat(),
                 "moved": moved})
            self.save()
        return {"moved": moved, "unmapped": sorted(unmapped)}

    def adopt_flat_account(self, *, anchor: Mapping[str, Any], reason: str
                           ) -> Dict[str, Any]:
        """Close every market in the book against an authoritative FLAT read.

        NO P&L IS POSTED. This is not a settlement -- it is the statement
        that the venue holds nothing, owes nothing and has nothing
        outstanding, so whatever these markets were is finished and their
        result is already wherever the accounting put it. Using it when the
        account is NOT flat would silently delete live exposure, so the
        anchor must say so and is recorded verbatim.

        It exists because the alternative for the 2026-09-26 volume is
        worse: canonicalising its keys would leave two already-settled
        markets looking open, and the next sweep would settle them a second
        time on top of a rebase that has already accounted for them.
        """
        closed = sorted(self.markets_with_exposure())
        for name in ("positions", "leg_cost", "fill_count",
                     "venue_side_raw"):
            if isinstance(self.state.get(name), dict):
                self.state[name] = {}
        self.state["committed"] = {}
        self.state["live_orders"] = {}
        self.state["pending_terminal"] = {}
        self.state["orders_uncertain"] = None
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for m in closed:
            self.state.setdefault("retired_markets", {})[m] = {
                "t": now, "settlement_pnl": "0",
                "closed_against": "AUTHORITATIVE_FLAT_ACCOUNT",
                "why": ("no P&L posted here: the result is already in the "
                        "accounting lines, and the venue holds nothing"),
            }
        self.state.setdefault("flat_adoptions", []).append(
            {"t": now, "reason": str(reason), "anchor": dict(anchor),
             "markets_closed": closed})
        self.save()
        return {"closed": closed, "posted_pnl": "0", "anchor": dict(anchor)}

    def pending_terminal(self) -> Dict[str, Dict[str, Any]]:
        """Orders that left the venue's open list still owing us a fill."""
        return {k: dict(v) for k, v in
                (self.state.get("pending_terminal") or {}).items()}

    def note_pending_attempt(self, coid: str, why: str) -> None:
        """Record that we asked the venue about this one and could not tell."""
        pend = self.state.setdefault("pending_terminal", {})
        row = pend.get(coid)
        if row is None:
            return
        row["attempts"] = int(row.get("attempts") or 0) + 1
        row["last_error"] = str(why)[:200]
        self.save()

    def resolve_pending_terminal(self, coid: str, prog: Mapping[str, Any]
                                 ) -> Dict[str, Any]:
        """Book a vanished order's FINAL fill from an authoritative read.

        This is the reconstruction that lets a QUANTITY_WITHOUT_COST book
        become a coherent one again, and it is the only thing that clears a
        pending order. `prog` must come from the venue's own /v1/order row
        (order_progress of it), never from a guess, a mark or our own memory
        of what we sent.

        Idempotent by the same key apply_order_progress uses: a second call
        with the same cumQuantity books nothing.
        """
        pend = self.state.setdefault("pending_terminal", {})
        row = dict(pend.get(coid) or {})
        if not row:
            return {"coid": coid, "resolved": False, "reason": "NOT_PENDING"}
        market = str(row.get("market") or "")
        side = canonical_side(row.get("side"),
                              doing="booking a pending terminal order")
        # put it back on the live map just long enough for the shared
        # booking path to see it, so the delta/idempotency logic is the same
        # code that books an ordinary progress read. Nothing else reads it
        # in between: this returns before the next tick.
        live = self.state.setdefault("live_orders", {})
        restored = coid not in live
        if restored:
            live[coid] = {"market": market, "side": side,
                          "venue_order_id": row.get("venue_order_id"),
                          "client_order_id": coid,
                          "session_id": row.get("session_id"),
                          "remaining": "0", "state": "PENDING_TERMINAL",
                          "price": None, "price_unreadable": True}
        out = self.apply_order_progress(coid, dict(prog, terminal=True),
                                        market=market, side=side)
        live.pop(coid, None)
        if out.get("refused"):
            # the venue went backwards on a cumulative field. Still pending.
            pend[coid] = dict(row, attempts=int(row.get("attempts") or 0) + 1,
                              last_error=str(out.get("refused")))
            self.save()
            return dict(out, coid=coid, resolved=False)
        pend.pop(coid, None)
        self.save()
        return dict(out, coid=coid, resolved=True, market=market, side=side,
                    labels=("final fill booked from the venue's own order "
                            "row; this is the reconstruction that resolves "
                            "a lost cost basis, not an assumption about it"))

    def reconstruct_cost_basis(self, market: str, side: str, *, qty: D,
                               cost: D, source: str) -> Dict[str, Any]:
        """Attest a leg's quantity and cost together, from named evidence.

        The operator-level escape from QUANTITY_WITHOUT_COST, for a leg whose
        order rows can be read by hand when the automatic path cannot reach
        them. `source` is required and is recorded: a cost basis with no
        stated evidence is the thing this whole check exists to refuse.

        It writes quantity and cost in ONE step, which is the invariant the
        1815z defect broke by writing them in two.
        """
        if not str(source).strip():
            raise ValueError("reconstruct_cost_basis requires a source")
        own_side = canonical_side(side, doing="reconstructing a cost basis")
        k = "%s|%s" % (market, own_side)
        q, c = D(str(qty)), D(str(cost))
        if q < 0 or c < 0:
            raise ValueError("reconstructed quantity and cost must be >= 0")
        if q > 0 and c / q > self.MAX_OWN_COST_PER_SHARE:
            raise ValueError("reconstructed cost exceeds $1 a share")
        self.state["positions"][k] = str(q)
        self.state.setdefault("leg_cost", {})[k] = str(c)
        fc = self.state.setdefault("fill_count", {})
        fc[k] = int(fc.get(k, 0)) + 1
        log = self.state.setdefault("cost_reconstructions", [])
        log.append({"t": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "market": market, "side": own_side, "qty": str(q),
                    "cost": str(c), "source": str(source)})
        self.save()
        return {"reconstructed": True, "market": market, "side": own_side,
                "qty": str(q), "cost": str(c), "source": str(source)}

    @staticmethod
    def _gross_by_market(positions: Mapping[str, Any]) -> Dict[str, Dict[str, D]]:
        """Our own book, kept as LONG and SHORT rather than collapsed.

        matched = min(up, dn) is the pair whose settlement value is locked at
        $1 whatever happens. residual = up - dn is the only directional part.
        A net number cannot distinguish 0/0 from 5/5, and those are entirely
        different positions.
        """
        out: Dict[str, Dict[str, D]] = {}
        for k, v in positions.items():
            if "|" not in k:
                continue
            market, side = k.rsplit("|", 1)
            d = out.setdefault(market, {"up": D(0), "dn": D(0),
                                        "uncanonical": D(0)})
            try:
                q = D(str(v))
            except Exception:                                  # noqa: BLE001
                continue
            # a key whose side is not canonical is NOT folded into either
            # leg: it is counted apart so the caller can see that the book
            # holds quantity under a key nothing else reads.
            got = venue_side(side)
            if got is None:
                d["uncanonical"] = d.get("uncanonical", D(0)) + q
                continue
            d["up" if got == UP else "dn"] += q
        for m, d in out.items():
            d["matched"] = min(d["up"], d["dn"])
            d["residual"] = d["up"] - d["dn"]
            d["net"] = d["up"] - d["dn"]
        return out

    @staticmethod
    def _net_by_market(positions: Mapping[str, Any]) -> Dict[str, D]:
        out: Dict[str, D] = {}
        for k, v in positions.items():
            if "|" not in k:
                continue
            market, side = k.rsplit("|", 1)
            try:
                q = D(str(v))
            except Exception:                                  # noqa: BLE001
                continue
            got = venue_side(side)
            if got is None:
                # unknown side: it cannot be added OR subtracted honestly,
                # so it is left out of the net and shows up instead as a
                # gross/net disagreement, which fails closed.
                continue
            out[market] = out.get(market, D(0)) + (q if got == UP else -q)
        return out

    def gross_inventory(self, market: str) -> Dict[str, str]:
        """Gross long/short, matched and residual, for reporting."""
        g = self._gross_by_market(self.state.get("positions") or {}).get(
            market, {"up": D(0), "dn": D(0), "matched": D(0),
                     "residual": D(0), "net": D(0), "uncanonical": D(0)})
        return {k: str(v) for k, v in g.items()}

    def markets_with_exposure(self) -> List[str]:
        """Every market this book still has anything in.

        Both sources, because they are written by different paths and can
        disagree: `committed` moves on our own fills, while
        reconcile_from_venue writes `positions` straight from the venue
        without touching committed. A market that came back from a restart
        through reconciliation therefore has positions and no committed, and
        sweeping only committed would leave it in the book forever.
        """
        out = {m for m, v in self.committed().items() if v != 0}
        for key in (self.state.get("positions") or {}):
            out.add(str(key).rsplit("|", 1)[0])
        for key in (self.state.get("leg_cost") or {}):
            out.add(str(key).rsplit("|", 1)[0])
        # AN OUTSTANDING ORDER IS SOMETHING THE BOOK HAS IN A MARKET, even
        # before it fills. A market whose only trace is a live or parked
        # order was invisible here, so the settlement sweep never looked at
        # it -- and a sweep that never looks cannot refuse, which is how a
        # market with an unbooked fill would have slipped past the
        # UNBOOKED_TERMINAL_ORDERS guard entirely.
        for o in (self.state.get("live_orders") or {}).values():
            if o.get("market"):
                out.add(str(o["market"]))
        for o in (self.state.get("pending_terminal") or {}).values():
            if o.get("market"):
                out.add(str(o["market"]))
        # a market already retired is finished with, whatever residue a
        # future defect leaves behind: it is never swept, never re-settled
        # and never re-read from the venue.
        return sorted(out - set(self.state.get("retired_markets") or {}))

    def retire_settled_market(self, market: str, *, yes_value: D
                              ) -> Dict[str, Any]:
        """Close a settled market's book and release what it was holding.

        WHY THIS EXISTS. `committed` is incremented on every fill and nothing
        ever retired it, because every probe before this ran ONE window and
        exited. Run continuously and that is fatal: `open_markets` keeps
        naming a market forever, and MAX_CONCURRENT_MARKETS then refuses
        every later window. The executor would trade once and spend the rest
        of the day silently declining.

        It is also what makes the daily loss halt real. `realized_today` is
        read by SafetyLimits.refuse and was written by nothing at all, so the
        halt could never fire. Settlement is the only event that turns a
        position into cash, so it is the only thing entitled to move it.

        REALIZED, strictly. `yes_value` must be the venue's own settlement
        price -- callers pass what /bbo reported, never a mark, a model or a
        mid. Redemption is computed under that one settlement for BOTH legs:
        an UP share pays `yes_value`, a DOWN share pays `1 - yes_value`.

        The lines are kept APART. Matched-pair economics are locked at fill
        time and cannot be luck; residual P&L is a directional bet that
        happened to land. Reporting one number would hide exactly the
        distinction this programme is built on, so both are returned and the
        caller is free to sum them -- this does not.

        Refuses while an order is still live in the market: an order that can
        still fill is exposure, and releasing the cap against it would let
        the next market's first leg sit on top of it.
        """
        # ONCE. A market retired once must never be retired again: the
        # second pass would post a second P&L into realized and move the
        # loss halt with it. On 2026-09-26 2115z re-emitted L5_LIVE_SETTLED
        # every ~3 seconds, 40+ times, because retirement popped only the
        # `|UP` and `|DOWN` keys while the market's real inventory sat under
        # `|ORDER_SIDE_SELL`, so markets_with_exposure kept naming it. The
        # side fix removes that particular residue; this makes a repeat
        # impossible whatever the residue is, and it persists in the state
        # file so a restart cannot resurrect it either.
        done = (self.state.get("retired_markets") or {}).get(market)
        if done:
            return {"retired": False, "market": market,
                    "reason": "ALREADY_RETIRED", "retired_at": done.get("t"),
                    "first_settlement_pnl": done.get("settlement_pnl")}
        live = [c for c, o in (self.state.get("live_orders") or {}).items()
                if str(o.get("market")) == market]
        if live:
            return {"retired": False, "market": market,
                    "reason": "LIVE_ORDERS_IN_MARKET", "live_orders": live}
        pending = [c for c, o in (self.state.get("pending_terminal") or {}
                                  ).items() if str(o.get("market")) == market]
        if pending:
            # an order that left the open list still owes this market a
            # fill. Settling before it is booked prices the redemption
            # against an incomplete book -- which is 2045z exactly.
            return {"retired": False, "market": market,
                    "reason": "UNBOOKED_TERMINAL_ORDERS",
                    "pending_terminal": sorted(pending)}
        self.require_coherent_book(market, doing="settling")
        v = D(str(yes_value))
        if not (D(0) <= v <= D(1)):
            return {"retired": False, "market": market,
                    "reason": "SETTLEMENT_OUT_OF_RANGE", "yes_value": str(v)}

        up = self.position(market, "UP")
        dn = self.position(market, "DOWN")
        lc = self.leg_cost(market)
        cost_up, cost_dn = lc["UP"], lc["DOWN"]

        redeem = up * v + dn * (D(1) - v)
        cost = cost_up + cost_dn
        pnl = redeem - cost

        # matched at average cost per leg; the residual carries the rest.
        matched = min(up, dn)
        avg_up = (cost_up / up) if up else D(0)
        avg_dn = (cost_dn / dn) if dn else D(0)
        matched_cost = matched * (avg_up + avg_dn)
        matched_redeem = matched * D(1)          # a pair redeems exactly $1
        residual_qty = up - dn                   # + is UP-heavy
        residual_cost = cost - matched_cost
        residual_redeem = redeem - matched_redeem

        # EVERY key of this market, not the two we expect. A key under any
        # other side word is exactly what kept 2115z alive in
        # markets_with_exposure after it had been retired.
        for store_name in ("positions", "leg_cost", "fill_count",
                           "venue_side_raw"):
            d = self.state.get(store_name)
            if not isinstance(d, dict):
                continue
            for key in [k for k in d
                        if str(k).rsplit("|", 1)[0] == market]:
                d.pop(key, None)
        released = D(str(self.state["committed"].pop(market, 0)))
        # a settlement is attributable by construction: it came from filled
        # inventory and the venue's own settlement price.
        self.state["realized_attributed"] = str(
            D(str(self.state.get("realized_attributed") or 0)) + pnl)
        self.state.setdefault("realized_unattributed",
                              str(D(str(self.state.get(
                                  "realized_unattributed") or 0))))
        # keep the legacy scalar moving too, for anything still reading it
        self.state["realized_today"] = str(
            D(str(self.state.get("realized_today") or 0)) + pnl)
        self.state.setdefault("retired_markets", {})[market] = {
            "t": dt.datetime.now(dt.timezone.utc).isoformat(),
            "yes_value": str(v), "settlement_pnl": str(pnl),
            "up_qty": str(up), "dn_qty": str(dn),
            "cost_total": str(cost), "redemption_total": str(redeem),
        }
        self.save()
        return {
            "retired": True, "market": market, "yes_value": str(v),
            "up_qty": str(up), "dn_qty": str(dn),
            "up_cost": str(cost_up), "dn_cost": str(cost_dn),
            "matched_qty": str(matched), "matched_cost": str(matched_cost),
            "matched_redeem": str(matched_redeem),
            "matched_pnl": str(matched_redeem - matched_cost),
            "residual_qty": str(abs(residual_qty)),
            "residual_side": ("UP" if residual_qty > 0
                              else "DOWN" if residual_qty < 0 else None),
            "residual_cost": str(residual_cost),
            "residual_redeem": str(residual_redeem),
            "residual_pnl": str(residual_redeem - residual_cost),
            "redemption_total": str(redeem), "cost_total": str(cost),
            "settlement_pnl": str(pnl),
            "committed_released": str(released),
            "realized_today_after": str(self.realized_today()),
            "labels": ("SETTLED. matched and residual are separate lines and "
                       "are NOT summed here. Commissions and maker rebates "
                       "are their own lines elsewhere and are not in "
                       "settlement_pnl."),
        }

    def reconcile_from_venue(self, positions: Sequence[Mapping[str, Any]],
                             open_orders: Sequence[Mapping[str, Any]]
                             ) -> Dict[str, Any]:
        """The venue overwrites us. Divergence is REPORTED, never averaged.

        On restart our file may be stale (a fill landed after our last save)
        or ahead (we saved an optimistic fill that never happened). Trusting
        the file in either case trades on a fiction, so the venue's answer
        replaces ours and the difference is surfaced.
        """
        before = dict(self.state["positions"])
        after: Dict[str, str] = {}
        for p in positions:
            row = position_row(p)          # raises UnrecognizedVenueShape
            after["%s|%s" % (row["market"], row["side"])] = row["qty"]
        drift = {k: {"ours": before.get(k, "0"), "venue": after.get(k, "0")}
                 for k in set(before) | set(after)
                 if str(before.get(k, "0")) != str(after.get(k, "0"))}

        # GROSS vs NET. The venue nets the two outcome tokens of a market:
        # Probe 07 held UP 2.47 and DOWN 5.28 and /v1/portfolio/positions
        # reported DOWN 2.8100, because 5.28 - 2.47 = 2.81. Overwriting our
        # book with that number does not merely lose detail -- it DELETES THE
        # MATCHED PAIR, which is the half of the position whose economics are
        # locked and the half this whole strategy is built on.
        #
        # So the venue's net is recorded as the venue's net, our gross is
        # kept as gross, and the two are reconciled by the identity
        # net = long - short rather than by replacement. The gross book is
        # only overwritten where it CONTRADICTS that identity.
        gross = self._gross_by_market(before)
        net = self._net_by_market(after)
        agrees = {m: (gross[m]["net"] == net.get(m, D(0))) for m in gross}
        # WE DO NOT INVENT GROSS FROM NET. This used to overwrite our two
        # legs with the venue's single net number and leave `leg_cost`
        # untouched -- the quantities collapsed, both legs' costs stayed, and
        # 1815z settled 1.81 UP shares against $4.3425 of cost, $2.40 a share
        # for a contract that pays at most $1.
        #
        # A net cannot be decomposed into a gross book. When ours disagrees
        # with the venue's, the honest state is NOT KNOWN: the divergence is
        # named and the book is marked uncertain, which fails every
        # submission closed until the difference is resolved. Keeping our
        # gross unchanged is not a claim that it is right -- it is a refusal
        # to fabricate a cost basis for shares whose composition we cannot
        # see.
        # ONLY a market the venue actually reports can contradict us. A
        # market it says nothing about is the ordinary post-settlement
        # state -- the venue drops a position the moment the market expires,
        # while our book legitimately still holds it until
        # retire_settled_market books the redemption. Treating that silence
        # as divergence would mark the book uncertain after every single
        # settled window.
        disputed = sorted(m for m, ok in agrees.items()
                          if not ok and m in net)
        for k, v in after.items():
            m = k.rsplit("|", 1)[0]
            if m in agrees:
                continue
            # a market the venue holds and we have never booked: record the
            # quantity so risk can see the shares, and mark it uncertain
            # because we have NO cost basis for them at all.
            self.state["positions"][k] = v
            if k not in (self.state.get("leg_cost") or {}):
                disputed.append(m)
        for k in list(self.state["positions"]):
            m = k.rsplit("|", 1)[0]
            if m not in agrees and k not in after:
                # the venue does not hold it and neither did our gross book
                self.state["positions"].pop(k, None)
                (self.state.get("leg_cost") or {}).pop(k, None)

        # The venue's open orders are the authority on what is still live.
        # An order we think is live but the venue does not list is terminal
        # there -- filled, cancelled or rejected -- and stops consuming the
        # envelope. An order the venue lists that we never recorded is still
        # OUR exposure, so it is adopted rather than ignored.
        ours = dict(self.state.get("live_orders") or {})
        rebuilt: Dict[str, Any] = {}
        for o in open_orders:
            coid = str(o.get("client_order_id") or o.get("clientOrderId")
                       or o.get("order_id") or o.get("id") or "")
            if not coid:
                continue
            mine = ours.get(coid, {})
            rem = o.get("remaining_qty", o.get("remainingQuantity"))
            if rem is None:
                rem = o.get("qty", o.get("quantity", mine.get("remaining", 0)))
            # The venue row states the side up to three ways and they must
            # agree; if the row says nothing we recognise, fall back to what
            # WE recorded when we sent it, and refuse if that is missing too.
            try:
                _adopt_side = venue_row_side(o, doing="adopting an order")
            except UnknownSide:
                _adopt_side = canonical_side(
                    mine.get("side"), doing="adopting an order")
            rebuilt[coid] = {
                "t": mine.get("t"),
                # THE ID poll_orders ASKS THE VENUE BY. Adoption used to omit
                # it, so an adopted order could never be polled: it made no
                # progress, tripped the 90s stall, was re-adopted, and looped
                # every 92s with submissions refused the whole time. 1830z
                # spent its entire window in that loop.
                "venue_order_id": str(o.get("id") or o.get("order_id")
                                      or coid),
                "client_order_id": coid,
                "session_id": mine.get("session_id"),
                "adopted": coid not in ours,
                "market": o.get("market") or mine.get("market"),
                # CANONICAL, always. Adoption used to copy the venue's own
                # word straight through -- "ORDER_SIDE_SELL" -- and
                # apply_fill then built its ledger key from it. The raw word
                # is kept beside it for audit and provenance; nothing
                # downstream is allowed to read it.
                "side": _adopt_side,
                "venue_side_raw": (None if o.get("side") is None
                                   else str(o.get("side"))),
                "venue_outcome_side_raw": (None if o.get("outcomeSide") is None
                                           else str(o.get("outcomeSide"))),
                # by name, never str() of whatever arrived: an unreadable
                # price is recorded AS unreadable so the risk readers can
                # price it at worst case instead of at zero.
                #
                # AND IN OWN-OUTCOME TERMS. venue_order_price returns the
                # venue's number, which is the long (YES) side's price, while
                # risk_exposure_usd reads live_orders["price"] as what the
                # leg costs us -- the same mismatch that wrecked leg_cost.
                # An order WE placed was already stored in own terms by
                # record_submit, so only the venue's number is converted.
                "price": (str(own_outcome_price(_adopt_side,
                                                _adopt_px, basis=WIRE))
                          if (_adopt_px := venue_order_price(o)) is not None
                          else (str(mine.get("price"))
                                if venue_decimal(mine.get("price")) is not None
                                else None)),
                "price_unreadable": (venue_order_price(o) is None
                                     and venue_decimal(mine.get("price"))
                                     is None),
                "price_row_keys": (sorted(o) if venue_order_price(o) is None
                                   else None),
                "qty": str(o.get("qty", mine.get("qty", rem))),
                "remaining": str(rem), "state": "LIVE_AT_VENUE",
            }
        # AN ORDER THAT VANISHED IS NOT AN ORDER THAT DID NOTHING.
        #
        # This used to release every live order the venue no longer lists,
        # on the reasoning that an order absent from /v1/orders/open is
        # terminal there. It is -- but terminal covers FILLED as well as
        # cancelled and expired, and releasing it books neither. 2045z:
        # CR157F2WJYC3 filled, dropped off the open list, its /v1/order read
        # raised, reconcile released it, and the 2.25 DOWN shares it had
        # bought arrived instead from the venue's net with no cost at all.
        #
        # So disappearance now moves an order to PENDING TERMINAL, not to
        # gone. It stops consuming the live-order envelope -- it cannot fill
        # any further -- but its final cumQuantity and avgPx are still owed
        # to the ledger, and the book stays uncertain until an authoritative
        # /v1/order/{id} read supplies them. Fail-closed on the accounting,
        # open on the envelope: the shape that loses no money and no facts.
        # AN ORDER WITH NO VENUE ID IS A DIFFERENT CASE and must not be
        # parked, because nothing can ever ask about it: the id is what
        # /v1/order/{id} takes. It has no id because it was never ACKed, so
        # we hold no proof it reached the venue at all -- and this same
        # reconcile has just read the venue's positions, which is the only
        # evidence that could show it had. If it did fill, the shares land
        # with no cost basis and QUANTITY_WITHOUT_COST fails the book
        # closed; that is the backstop. Parking it instead would wedge the
        # session on a question with no way to answer it, which is what the
        # eight 1700z phantoms would have done.
        vanished = sorted(c for c in (set(ours) - set(rebuilt))
                          if (ours.get(c) or {}).get("venue_order_id"))
        unacked = sorted(c for c in (set(ours) - set(rebuilt))
                         if not (ours.get(c) or {}).get("venue_order_id"))
        pend = self.state.setdefault("pending_terminal", {})
        for coid in vanished:
            was = dict(ours.get(coid) or {})
            prior = dict(pend.get(coid) or {})
            pend[coid] = {
                "coid": coid,
                "venue_order_id": was.get("venue_order_id"),
                "market": was.get("market"),
                "side": was.get("side"),
                "session_id": was.get("session_id"),
                "applied": str(self.applied_fill_qty(coid)),
                "first_seen_missing": prior.get("first_seen_missing")
                or dt.datetime.now(dt.timezone.utc).isoformat(),
                "attempts": int(prior.get("attempts") or 0),
                "reason": "ABSENT_FROM_VENUE_OPEN_ORDERS",
                "resolvable": bool(was.get("venue_order_id")),
            }
        # AN ORDER THAT CAME BACK is not pending any more. The venue can
        # drop an order from its open list and list it again on the next
        # read, and a parked entry left behind for one that is live again
        # would refuse its market's settlement for ever. Double-booking is
        # not the risk here -- apply_order_progress is idempotent on the
        # cumulative -- a permanently unsettleable market is.
        returned = sorted(set(rebuilt) & set(pend))
        for coid in returned:
            pend.pop(coid, None)
        adopted = sorted(set(rebuilt) - set(ours))
        self.state["live_orders"] = rebuilt
        # An authoritative answer is the ONLY thing that clears uncertainty
        # -- and only when what it leaves behind is a book that could exist.
        self.state["orders_uncertain"] = None
        incoherent: Dict[str, Any] = {}
        for m in sorted({k.rsplit("|", 1)[0]
                         for k in (self.state.get("positions") or {})}):
            bad = self.book_violations(m)
            if bad:
                incoherent[m] = bad
        pending = sorted(self.state.get("pending_terminal") or {})
        if disputed or incoherent or pending:
            self.state["orders_uncertain"] = (
                "gross/net divergence %s; incoherent book %s; "
                "unbooked terminal orders %s"
                % (sorted(set(disputed)), sorted(incoherent), pending))
        self.save()
        return {"reconciled": True, "drift": drift,
                # stated separately and never collapsed into one another
                "gross_before": {m: {k: str(v) for k, v in d.items()}
                                 for m, d in gross.items()},
                "venue_net": {m: str(v) for m, v in net.items()},
                "gross_net_agrees": agrees,
                "gross_detail_lost": sorted(m for m, ok in agrees.items()
                                            if not ok),
                "labels_inventory": (
                    "the venue reports a NET position; our book is GROSS. "
                    "Where they agree the matched pair is preserved. Where "
                    "they disagree the venue's net becomes the position of "
                    "record and the market is named in gross_detail_lost, "
                    "because a matched pair silently deleted is a locked "
                    "economic result silently deleted."),
                "open_orders_at_venue": len(open_orders),
                # named for what it is now: these orders are OFF the live
                # envelope but still owe the ledger their final fill.
                "live_orders_pending_terminal": vanished,
                "live_orders_released_unacked": unacked,
                "pending_terminal_returned": returned,
                "pending_terminal_total": len(pending),
                "live_orders_adopted": adopted,
                "labels_pending": (
                    "an order absent from the venue's open orders is "
                    "terminal there, which includes FILLED. It is not "
                    "released until an authoritative /v1/order read books "
                    "its final cumQuantity, because releasing it unbooked "
                    "is how 2.25 DOWN shares arrived at 2045z with no cost. "
                    "An order that was never ACKed has no id to ask by and "
                    "is released instead; if it did fill, the shares arrive "
                    "without a cost basis and the book fails closed."),
                "uncertainty_cleared": not (disputed or incoherent
                                            or pending),
                "authority": "VENUE"}


class FaithfulExecutor:
    """Drives one L5 action through the whole path. Cannot transmit alone."""

    def __init__(self, store: InventoryStore, limits: SafetyLimits, *,
                 transport: Optional[Any] = None,
                 lam: D = D("1"), now_fn=None,
                 maker_treatment: bool = False,
                 maker_only: bool = False,
                 suppress_imbalance_increase: bool = False) -> None:
        self.store = store
        self.limits = limits
        self.transport = transport or NullTransport()
        self.lam = lam
        #: MAKER_TREATMENT: an action the frozen rule called MAKER and then
        #: refused STRICT_NOT_EVALUABLE is RESTED at L5's own price instead of
        #: dropped. That refusal is about replay evidence -- whether a
        #: hypothetical resting order would have filled -- and live we do not
        #: need to prove it, we place the order and let the venue answer.
        #: L5_NATIVE is untouched: this reads its output and adds nothing.
        self.maker_treatment = maker_treatment
        #: MAKER_ONLY: no taker fallback at all. Probe 07 is a test of the
        #: maker mechanism, and a crossing order would contaminate it.
        self.maker_only = maker_only
        #: PROBE 08 TREATMENT. While the market's REALIZED residual is
        #: non-zero, an order that would deepen the imbalance is refused.
        #: It refuses and nothing else: it never invents a trade, never
        #: crosses, never chases, never re-prices, and never blocks the
        #: complementary side or an opening leg from flat. Because it can
        #: only ever decline, it cannot raise exposure above what the
        #: untreated path would have reached.
        self.suppress_imbalance_increase = suppress_imbalance_increase
        self._now = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def _age_s(self, row: Mapping[str, Any]) -> Optional[float]:
        try:
            t = dt.datetime.fromisoformat(str(row["receipt_utc"]))
        except Exception:                                      # noqa: BLE001
            return None
        return (self._now() - t).total_seconds()

    def handle(self, row: Mapping[str, Any],
               authenticated: Mapping[str, Any],
               *, market: str, window: str,
               open_markets: Sequence[str] = ()) -> Dict[str, Any]:
        key = str(row.get("action_key"))
        dec = FT.l5_native(row)
        prov = Provenance(key, dec or {"unreadable": True}, market, window)
        if dec is None:
            return prov.refuse("L5_NATIVE", "UNREADABLE_ROW")

        age = self._age_s(row)
        if age is not None and age > STALE_ACTION_MAX_S:
            return prov.refuse("PMUS_TRANSLATION",
                               "STALE_ACTION:%0.1fs" % age)

        want = dec["l5_size"] * self.lam
        tr = FT.pmus_translate(dec, row, authenticated, want)

        # The venue can only create orders on its own quantity grid. Probe 03
        # asked for 0.4066877541798463 against an increment of 0.01, the
        # preview came back as 0.40, and the echo gate refused -- correctly,
        # because an order resting at a size the engine did not choose is
        # worse than no order. The gate is not the defect; sending a quantity
        # the venue cannot represent is. Everything downstream -- reservation,
        # preview, echo, submission, fill and reconciliation -- uses the
        # EXECUTABLE quantity from here on, so nothing is ever reserved at one
        # size and sent at another.
        inc = venue_quantity_increment(self.transport)
        desired = tr.get("qty")
        executable = desired
        if tr.get("ok") and desired is not None and inc is not None:
            executable = floor_to_increment(D(str(desired)), inc)
        delta = (None if (desired is None or executable is None)
                 else D(str(desired)) - D(str(executable)))

        prov.stage("PMUS_TRANSLATION", {
            "ok": bool(tr.get("ok")), "mode": tr.get("mode"),
            "price": str(tr.get("price")),
            # `qty` is what we will execute. The translation's own number is
            # kept beside it: L5's behaviour is not rewritten to pretend he
            # asked for a round lot.
            "qty": str(executable),
            "qty_desired": str(desired),
            "quantity_increment": (None if inc is None else str(inc)),
            "quantization_delta": (None if delta is None else str(delta)),
            "quantized": bool(inc is not None and tr.get("ok")),
            "slippage_vs_l5": str(tr.get("slippage")),
            "depth": str(tr.get("depth")), "reason": tr.get("reason")})
        if not tr.get("ok"):
            # The one refusal the maker treatment answers empirically. Every
            # other refusal -- stale, no book, no depth, queue-ambiguous --
            # stands exactly as the frozen rule made it.
            if (self.maker_treatment
                    and str(tr.get("mode") or "").upper() == "MAKER"
                    and str(tr.get("reason") or "") == "STRICT_NOT_EVALUABLE"
                    and dec.get("price") is not None):
                want = dec["l5_size"] * self.lam
                raw_px = D(str(dec["price"]))      # P1: L5's price
                # ...on the venue's grid. L5 trades at full float precision
                # and the venue does not: 0.7399999979 came back 0.73 and
                # the echo gate refused. FLOORED, never rounded up, because
                # up is the aggressive direction for a buy.
                tick = venue_tick_size(self.transport)
                if tick is None and bool(getattr(self.transport, "armed",
                                                 False)):
                    return prov.refuse("SAFETY_OVERLAY", "TICK_SIZE_UNKNOWN")
                px = (quantize_price_non_aggressive(raw_px, tick, dec["side"])
                      if tick is not None else raw_px)
                if px <= 0:
                    return prov.refuse("PMUS_TRANSLATION",
                                       "PRICE_BELOW_MIN_TICK")
                executable = (floor_to_increment(want, inc)
                              if inc is not None else want)
                if executable <= 0:
                    return prov.refuse("PMUS_TRANSLATION",
                                       "QUANTITY_BELOW_MIN_INCREMENT")
                tr = {"ok": True, "mode": FT.MAKER, "price": px,
                      "qty": executable, "qty_desired": want,
                      "slippage": D(0), "depth": None,
                      "fee": D(0), "rebate": D(0),
                      "treatment": "MAKER_TREATMENT_V1"}
                desired = want
                prov.stage("MAKER_TREATMENT", {
                    "rested_at_l5_price": str(px),
                    "l5_price_raw": str(raw_px),
                    "tick_size": (None if tick is None else str(tick)),
                    "price_quantization_delta": str(raw_px - px),
                    "rounding": "FLOOR_NEVER_MORE_AGGRESSIVE",
                    "qty_desired": str(want), "qty": str(executable),
                    "post_only": True,
                    "why": ("the frozen rule called this restable and could "
                            "not prove the fill in replay. Live, the venue "
                            "answers that question instead."),
                })
            else:
                return prov.refuse("PMUS_TRANSLATION", tr["reason"])

        # Probe 07 is a test of the MAKER mechanism. A crossing order would
        # contaminate the endpoint it exists to measure, so there is no taker
        # fallback and the refusal says which rule declined it.
        if self.maker_only and str(tr.get("mode") or "").upper() != FT.MAKER:
            return prov.refuse("SAFETY_OVERLAY", "MAKER_ONLY_NO_TAKER")

        # An armed transport has passed preflight, and preflight does not pass
        # without quantity_constraint_known -- so an unknown grid here means
        # something is wrong with what we believe about this market, and the
        # answer is to send nothing.
        if inc is None and bool(getattr(self.transport, "armed", False)):
            return prov.refuse("SAFETY_OVERLAY", "QUANTITY_INCREMENT_UNKNOWN")
        if inc is not None and executable <= 0:
            return prov.refuse("SAFETY_OVERLAY",
                               "QUANTITY_BELOW_MIN_INCREMENT:%s<%s"
                               % (desired, inc))
        tr = dict(tr, qty=executable)

        resid_before = self.store.residual(market)
        klass = inventory_class(resid_before, dec["side"])
        prov.note("INVENTORY_CLASS", {
            "inventory_class": klass,
            "residual_before": str(resid_before),
            "side": dec["side"],
            "treatment_active": bool(self.suppress_imbalance_increase),
            "basis": "REALIZED residual; outstanding orders are not counted"})
        if self.suppress_imbalance_increase and klass == INCREASE_RESIDUAL:
            return prov.refuse("SAFETY_OVERLAY", "IMBALANCE_INCREASE_SUPPRESSED")

        resid_after = resid_before + (
            tr["qty"] if dec["side"] == "UP" else -tr["qty"])
        outstanding = self.store.outstanding_by_market()
        why = self.limits.refuse(
            price=tr["price"], qty=tr["qty"], market=market,
            committed_by_market=self.store.committed(),
            residual_after=resid_after,
            realized_today=self.store.realized_today(),
            open_markets=open_markets,
            outstanding_by_market=outstanding,
            live_order_count=self.store.live_order_count(),
            outstanding_residual=self.store.outstanding_residual(market),
            orders_uncertain=self.store.orders_uncertain(),
            # the corrected metric: worst-case loss over both settlements,
            # with this order counted as if it fills in full. committed_usd
            # is still passed, and still reported, but no longer gates.
            risk_by_market={market: self.store.risk_exposure_usd(
                market, extra_side=dec["side"], extra_qty=tr["qty"],
                extra_price=tr["price"])})
        prov.stage("SAFETY_OVERLAY", {
            "permitted": why is None, "reason": why,
            "limits": self.limits.as_dict(),
            "committed_usd": {k: str(v) for k, v
                              in self.store.committed().items()},
            "outstanding_usd": {k: str(v) for k, v in outstanding.items()},
            "live_orders": self.store.live_order_count(),
            "orders_uncertain": self.store.orders_uncertain(),
            "risk_exposure_usd": str(self.store.risk_exposure_usd(market)),
            "measures": ("POTENTIAL exposure: filled + still-fillable + "
                         "this order, not filled alone. The $5 caps read "
                         "risk_exposure_usd -- worst case over both "
                         "settlements -- not committed_usd notional."),
            "note": ("account protection only; never a statement about L5 "
                     "behaviour or PMUS capability")})
        if why:
            return prov.refuse("SAFETY_OVERLAY", why)

        coid = client_order_id(key)
        if self.store.already_submitted(coid):
            prov.stage("ORDER", {"client_order_id": coid,
                                 "submitted": False,
                                 "reason": "DUPLICATE_SUPPRESSED"})
            return prov.rec

        intent = {"client_order_id": coid, "market": market,
                  "side": dec["side"], "price": tr["price"], "qty": tr["qty"],
                  "mode": tr["mode"], "action_key": key}
        # Reserve the envelope BEFORE the wire. If the process dies between
        # the send and the ack, the order may still be live at the venue, and
        # a store that never recorded it would hand the whole envelope to the
        # next decision.
        self.store.record_submit(
            coid, intent,
            session_id=os.getenv("PMUS_Q1_SESSION_ID", "").strip() or None,
            mode=str(tr.get("mode") or ""))
        try:
            ack = self.transport.submit(intent)
        except NotArmed as exc:
            # DISARMED never reached the venue, so the reservation is undone.
            self.store.terminate_order(coid, reason="NOT_ARMED")
            prov.stage("ORDER", {"client_order_id": coid, "submitted": False,
                                 "reason": "NOT_ARMED", "detail": str(exc)[:200],
                                 "intent": {k: str(v) for k, v in intent.items()}})
            return prov.rec
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # TERMINAL KNOWN-NO-ORDER. A failure that happened BEFORE the
            # order POST was attempted did not create an order -- the
            # transport records that it never got there -- so the outcome is
            # not unknown at all. Probe 07 lost nine subsequent actions to
            # ORDER_STATE_UNCERTAIN because a preview echo refusal, with
            # order_post_attempted False, was treated as if an order might be
            # resting somewhere. The reservation is released and the session
            # continues; the echo gate itself is untouched and still refused
            # the order, which is exactly what it is for.
            forensics = submit_forensics(self.transport)
            if forensics.get("order_post_attempted") is False:
                self.store.terminate_order(coid, reason="NO_ORDER_CREATED")
                prov.stage("ORDER", {
                    "client_order_id": coid, "submitted": False,
                    "reason": "REFUSED_BEFORE_ORDER_POST",
                    "error": type(exc).__name__,
                    "detail": _redact(str(exc))[:200], **forensics,
                    "intent": {k: str(v) for k, v in intent.items()},
                    "note": ("the venue was never asked to create this "
                             "order, so nothing is outstanding and the "
                             "reservation is released")})
                return prov.refuse("SAFETY_OVERLAY",
                                   "REFUSED_BEFORE_ORDER_POST")
            # Otherwise we genuinely do NOT know whether it reached the
            # venue. The reservation stands and the session refuses to submit
            # anything further until the venue answers authoritatively.
            self.store.mark_uncertain(
                "submit raised %s for %s" % (type(exc).__name__, coid))
            prov.stage("ORDER", {"client_order_id": coid, "submitted": None,
                                 "reason": "SUBMIT_OUTCOME_UNKNOWN",
                                 "error": type(exc).__name__,
                                 "detail": _redact(str(exc))[:200],
                                 **forensics,
                                 "intent": {k: str(v)
                                            for k, v in intent.items()},
                                 "note": ("assumed live and still consuming "
                                          "the envelope until the venue says "
                                          "otherwise")})
            return prov.refuse("SAFETY_OVERLAY", "SUBMIT_OUTCOME_UNKNOWN")

        self.store.attach_venue_id(coid, ack.get("order_id")
                                   or ack.get("orderId") or ack.get("id"))
        prov.stage("ORDER", {"client_order_id": coid, "submitted": True,
                             "venue_order_id": ack.get("order_id"),
                             "transport": getattr(self.transport, "name", "?")})
        filled = D(str(ack.get("filled_qty") or 0))
        terminal = str(ack.get("state") or ack.get("status") or "").upper()
        if filled <= 0:
            # Rejected or cancelled on the ack releases the slot; anything
            # else stays live and keeps consuming the envelope.
            if any(w in terminal for w in ("REJECT", "CANCEL", "EXPIRED")):
                self.store.terminate_order(coid, reason=terminal)
            prov.stage("FILL", {"filled": False, "filled_qty": "0",
                                "venue_state": terminal or None,
                                "still_live": self.store.live_order_count()})
            return prov.rec
        # tr["price"] is L5's own-side price, already in OWN terms. The
        # venue's ACK carries no own-terms price -- anything it returns is
        # WIRE -- so a raw ACK field must never be read as though it were
        # own. The authoritative fill price arrives later from
        # order_progress, which converts it.
        fill_px = D(str(tr["price"]))
        fee = (fees.taker_fee(fill_px, filled) if tr["mode"] == FT.TAKER
               else D(0))
        rebate = (fees.maker_rebate(fill_px, filled) if tr["mode"] == FT.MAKER
                  else D(0))
        prov.stage("FILL", {"filled": True, "filled_qty": str(filled),
                            "fill_price": str(fill_px), "fee_usd": str(fee),
                            "maker_rebate_usd": str(rebate),
                            "note": ("fee and rebate are separate lines and "
                                     "are never netted into trading P&L")})
        self.store.apply_fill(market, dec["side"], filled, fill_px,
                              coid=coid, basis=OWN)
        # record what this path booked, or the next order_progress read will
        # see cum > already == 0 and book the same shares a second time.
        self.store.state.setdefault("filled_qty", {})[coid] = str(filled)
        self.store.save()
        prov.stage("INVENTORY", {
            "market": market,
            "up": str(self.store.position(market, "UP")),
            "dn": str(self.store.position(market, "DOWN")),
            "residual": str(self.store.residual(market)),
            "committed_usd": str(self.store.committed().get(market, D(0))),
            "outstanding_usd": str(
                self.store.outstanding_by_market().get(market, D(0))),
            "live_orders": self.store.live_order_count()})
        return prov.rec


def settle(records: Sequence[Mapping[str, Any]], *, market: str,
           yes_value: D) -> Dict[str, Any]:
    """SETTLEMENT and REALIZED, from filled inventory only.

    REALIZED requires a settlement value from the venue. Nothing modelled,
    marked or accrued is called realized here, and fees and rebates stay on
    their own lines.
    """
    up = dn = cost_up = cost_dn = fee = rebate = D(0)
    for r in records:
        f = r.get("FILL") or {}
        if not f.get("filled"):
            continue
        q = D(str(f["filled_qty"]))
        px = D(str(f["fill_price"]))
        fee += D(str(f.get("fee_usd") or 0))
        rebate += D(str(f.get("maker_rebate_usd") or 0))
        # canonical_side rather than `== "UP"`: this is a settlement, and
        # a settlement that buckets an unrecognised side into DOWN by
        # falling off the end of an if is the same silent default that
        # filed 2115z under the venue's own word. These records come from
        # the L5 engine and are canonical by construction, so this raises
        # only when something upstream has already gone wrong -- which is
        # when a settlement should refuse to produce a number.
        if canonical_side((r.get("L5_NATIVE") or {}).get("side"),
                          doing="settling a filled record") == UP:
            up += q
            cost_up += px * q
        else:
            dn += q
            cost_dn += px * q
    matched = min(up, dn)
    residual = up - dn
    au = (cost_up / up) if up > 0 else D(0)
    ad = (cost_dn / dn) if dn > 0 else D(0)
    matched_pnl = ((D(1) - (au + ad)) * matched) if (up > 0 and dn > 0) else D(0)
    if residual > 0:
        resid_pnl = yes_value * residual - au * residual
    elif residual < 0:
        q = -residual
        resid_pnl = (D(1) - yes_value) * q - ad * q
    else:
        resid_pnl = D(0)
    return {
        "market": market, "settlement_yes_value": str(yes_value),
        "matched": str(matched), "residual": str(residual),
        "matched_pair_pnl_usd": str(matched_pnl),
        "residual_pnl_usd": str(resid_pnl),
        "taker_fees_usd": str(fee), "maker_rebate_usd": str(rebate),
        "realized_net_usd": str(matched_pnl + resid_pnl + rebate - fee),
        "labels": ("REALIZED requires venue settlement; fees and rebates are "
                   "separate lines, never netted into trading P&L"),
    }


def deadman(store: InventoryStore, transport: Any, *,
            reason: str) -> Dict[str, Any]:
    """Cancel everything we believe is live. Safe when nothing is armed."""
    out = {"reason": reason, "cancelled": [], "errors": []}
    for coid in list((store.state.get("submitted") or {}).keys()):
        try:
            transport.cancel(coid)
            out["cancelled"].append(coid)
        except Exception as exc:                               # noqa: BLE001
            out["errors"].append({"client_order_id": coid,
                                  "error": type(exc).__name__})
    return out
