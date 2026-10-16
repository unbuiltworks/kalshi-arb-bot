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
import re
from decimal import ROUND_FLOOR, Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_fees as fees
from src import l5_faithful_translation as FT
from src import l5_queue_measurement as QM
from src import l5_two_sided_probe as TS

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


class IncoherentOrderRow(ValueError):
    """The venue's own numbers for one order do not add up.

    LIVE 1300z, 2026-09-27 13:11:16Z. `/v1/order/CREX7ZDP2YC6` answered
    state ORDER_STATE_NEW, quantity 0.9, cumQuantity 0, leavesQuantity 0 --
    for an order that had FILLED 0.9 at 0.31 seven minutes earlier, as the
    same endpoint later confirmed. Nothing can be unstarted, fully resolved
    and 0.9 in size at once. `terminal = leaves <= 0` believed it, booked a
    zero fill as final, released the order, and logged that the number came
    from the venue's own row rather than being assumed. It had been assumed.
    The book then held quantity with no cost and refused to settle, which is
    the fail-closed end of the same defect 2045z was written to stop.

    A row that does not add up is not truth. It parks and stays uncertain.
    """


#: how far cumQuantity + leavesQuantity may sit from quantity before the row
#: is called incoherent. The venue's own grid is 0.01, so this is two orders
#: of magnitude tighter than anything it can legitimately send, and far wider
#: than float-to-Decimal noise.
QUANTITY_COHERENCE_TOLERANCE = D("0.0001")


#: What a binary prediction-market contract may settle at. NOTHING ELSE.
#: Not 0.01, not 0.99, not a mid, not a mark. A value between the two is a
#: PRICE, and a price is what a market was willing to pay a moment before it
#: resolved -- never what it paid out.
BINARY_SETTLEMENT_VALUES = (D(0), D(1))

#: Sources entitled to declare a settlement. Each names a venue record that
#: resolves the market itself. A book/quote route is deliberately absent:
#: `settlementPx` read off a quote route is admissible only because that
#: route reports the venue's own resolution field, and it arrives tagged
#: VENUE_SETTLEMENT_PX -- a last price, a mid or a mark has no tag here and
#: therefore no way in.
AUTHORITATIVE_SETTLEMENT_SOURCES = ("POSITION_RESOLUTION",
                                    "VENUE_SETTLEMENT_PX",
                                    "VENUE_MARKET_RESOLUTION")


def binary_settlement(yes_value: Any) -> Dict[str, Any]:
    """Read a settlement value, admitting ONLY exactly 0 or exactly 1.

    Returns {"value": D} when admissible, else {"reason": ..., "yes_value":
    ...}. It refuses rather than raises because its one caller must leave an
    unsettleable market OPEN and uncertain, not crash the sweep.
    """
    if yes_value is None:
        return {"reason": "SETTLEMENT_ABSENT", "yes_value": None}
    raw = yes_value
    if isinstance(raw, Mapping):                 # {"value": ..., "currency"}
        raw = raw.get("value")
        if raw is None:
            return {"reason": "SETTLEMENT_ABSENT", "yes_value": None}
    try:
        v = D(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return {"reason": "SETTLEMENT_UNREADABLE", "yes_value": str(raw)[:40]}
    if v not in BINARY_SETTLEMENT_VALUES:
        # covers BOTH the Stage A defect (0.01, in range) and an out-of-range
        # value (-1, 2). One refusal, because the fault is identical: that is
        # not a settlement.
        return {"reason": "SETTLEMENT_NOT_BINARY", "yes_value": str(v)}
    return {"value": v}


def resolution_refusal(market: str, resolution: Optional[Mapping[str, Any]],
                       value: D) -> Optional[Dict[str, Any]]:
    """Why this resolution record may not settle `market`, or None.

    A record is OPTIONAL for callers that already hold the venue's own
    resolution by construction (the ledger repairs, which replay recorded
    venue documents). When one IS supplied it is checked, and a supplied
    record that is wrong refuses -- a mismatch is worse evidence than none.
    """
    if resolution is None:
        return None
    if not isinstance(resolution, Mapping):
        return {"reason": "RESOLUTION_UNREADABLE"}
    src = str(resolution.get("source") or "").strip()
    if src not in AUTHORITATIVE_SETTLEMENT_SOURCES:
        return {"reason": "RESOLUTION_NOT_AUTHORITATIVE",
                "resolution_source": src or None}
    named = resolution.get("market")
    if named is None or str(named).strip() == "":
        return {"reason": "RESOLUTION_MARKET_UNNAMED"}
    try:
        same = canonical_market(named, doing="matching a resolution") == market
    except UnknownMarket:
        same = False
    if not same:
        return {"reason": "RESOLUTION_MARKET_MISMATCH",
                "resolution_market": str(named)[:80]}
    stated = resolution.get("yes_value")
    if stated is not None:
        got = binary_settlement(stated)
        if got.get("reason") or got["value"] != value:
            return {"reason": "RESOLUTION_VALUE_DISAGREES",
                    "resolution_yes_value": str(stated)[:40]}
    return None


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
    # THE 1300z CONTRADICTION, AND ONLY IT. leavesQuantity 0 claims there is
    # nothing left to work, which is how this function decides an order is
    # finished. That claim is believable when the state agrees the order
    # ended -- a CANCELLED order legitimately shows quantity 2.25, cum 0,
    # leaves 0, because the rest was cancelled rather than left working. It
    # is NOT believable when the state says the order has not started: an
    # ORDER_STATE_NEW row with nothing filled and nothing left describes no
    # possible order, and retiring one on it is how 1300z booked a zero fill
    # over a real 0.9 at 0.31.
    #
    # Deliberately narrow. A still-working row (leaves > 0) is not retired by
    # anything here, so it is not second-guessed; and a row whose state names
    # a terminal state is left to the checks that already exist, including
    # the store's backwards-cumulative guard.
    if (has_leaves and leaves <= 0 and row.get("quantity") is not None
            and not any(h in state.upper() for h in TERMINAL_HINTS)):
        quantity = _money(row.get("quantity"))
        gap = cum + leaves - quantity
        if (gap if gap >= 0 else -gap) > QUANTITY_COHERENCE_TOLERANCE:
            raise IncoherentOrderRow(
                "venue order row does not add up: id=%s state=%s quantity=%s "
                "cumQuantity=%s leavesQuantity=%s (cum+leaves-quantity=%s). "
                "Nothing left to work, but the state does not say the order "
                "ended. Refusing to treat it as terminal truth; the order "
                "stays parked and the book stays uncertain."
                % (row.get("id"), state or None, quantity, cum, leaves, gap))
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


#: what an uncountable book reports as its live-order count. Deliberately far
#: above any sane `max_live_orders`, so "I could not count" is always at the
#: limit rather than below it.
AT_LIMIT_WHEN_UNCOUNTABLE = 1_000_000


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



def _ack_epoch(ack: Mapping[str, Any]) -> float:
    """The venue's acknowledgement time, or our receipt as the fallback.

    A missing venue timestamp is NOT fatal -- our receipt bounds the ack from
    above, so a deadline computed from it can only be EARLIER than the truth,
    which is the safe direction. A deadline we could not compute at all is
    fatal, and `DeadlineStore.record_ack` refuses that case.
    """
    for k in ("ack_time", "transact_time", "transactTime", "createdAt",
              "created_at"):
        v = ack.get(k)
        if v is None:
            continue
        try:
            return float(v) if not isinstance(v, str) else (
                dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
                .timestamp())
        except Exception:                                      # noqa: BLE001
            continue
    return dt.datetime.now(dt.timezone.utc).timestamp()

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


class UnknownMarket(RuntimeError):
    """A market identity no financial state is allowed to accept.

    LIVE 2026-09-27. Two orders adopted from the venue -- CR52P8GZ4YAQ and
    CR52QM4S6YBY, 0.70 UP shares for $0.09700 -- were booked under the
    market key "". Adoption read the venue row's market as `row["market"]`,
    but this venue sends `marketSlug`, so a genuinely adopted order (one we
    never submitted, where there is no local record to fall back to) came
    through with market None. resolve_pending_terminal then did
    `str(row.get("market") or "")`, and `None or ""` is a perfectly good
    dict key.

    What followed was not a rounding error. `markets_with_exposure()`
    returned [""], `open_markets()` returned [""], and
    MAX_CONCURRENT_MARKETS refused every subsequent window -- the executor
    stopped trading entirely and spent 125 settlement reads asking the venue
    about a slug it has never heard of. The $0.09700 belonged to 0115z and
    was excluded from its settlement, which is why that market booked
    +0.599500 against a true +0.502500.

    An identity we do not have is not an identity we may invent. It is
    QUARANTINE.
    """


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

#: The keys a venue row may state its market under. `marketSlug` FIRST and
#: by name, because that is what this venue actually sends and reading
#: `market` alone is what produced the "" incident.
VENUE_ROW_MARKET_KEYS = ("marketSlug", "market_slug", "market", "slug")

#: A market id we will accept into financial state.
#:
#: THE RULE IS ONLY WHAT THE DEFECT WAS. None, "", whitespace -- an identity
#: that is absent. It is tempting to also demand this venue's slug shape,
#: and the first version of this did; it rejected every short market id the
#: rest of the suite uses and broke 53 tests, because a minimum length is a
#: rule I invented rather than an invariant the money requires. A market id
#: is valid when it is a non-empty string with no whitespace in it. What
#: the venue's slugs look like is the venue's business.
MARKET_ID_RE = re.compile(r"^\S+$")


def canonical_market(raw: Any, *, doing: str) -> str:
    """A market id fit to key financial state, or refuse.

    None, "", whitespace and anything not shaped like a slug all raise.
    There is no default and no fallback: every caller below this line
    writes money against the value it returns.
    """
    s = "" if raw is None else str(raw).strip()
    if not s or not MARKET_ID_RE.match(s):
        raise UnknownMarket(json.dumps({
            "raw": None if raw is None else str(raw), "doing": doing,
            "why": ("financial state accepts only a validated market id; "
                    "a blank or unrecognised identity is quarantined, never "
                    "used as a key")}))
    return s


def venue_row_market(row: Mapping[str, Any], *, doing: str,
                     fallback: Any = None) -> str:
    """The canonical market of one venue row, by name, then a local record.

    The venue's own key is tried first and in order. `fallback` is what WE
    recorded when we sent the order, which exists for an order we submitted
    and does not for one we merely adopted -- which is exactly the case that
    broke.
    """
    for key in VENUE_ROW_MARKET_KEYS:
        v = row.get(key) if isinstance(row, Mapping) else None
        if v is None:
            continue
        try:
            return canonical_market(v, doing=doing)
        except UnknownMarket:
            continue
    return canonical_market(fallback, doing=doing)


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


def _nonzero(value: Any) -> bool:
    """True when a quantity is unambiguously non-zero.

    AN UNREADABLE QUANTITY COUNTS AS NON-ZERO. It is used to decide whether
    an L5-owned position still blocks a baseline, so "I could not parse it"
    must behave like "something is still held", never like a clean zero.
    """
    try:
        return D(str(value)) != 0
    except (InvalidOperation, TypeError, ValueError):
        return True


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

    def still_fillable_order_count(self) -> int:
        """Orders that could still take money at the venue, right now.

        `live_order_count` is `len(live_orders)`, which counts a fully-filled
        row the same as a resting one -- so it is neither a count of exposure
        nor a count of slots. A one-order limit enforced on it would both
        refuse on a dead row and, worse, MISS an order the venue may still
        hold. This is the count the limit must bind on, and it is union of
        everything that might still be live:

          - a live order with `remaining > 0` and no terminal state hint;
          - a live order whose `remaining` cannot be READ, charged as live,
            because an unreadable row is not an absent one. This is the same
            worst-case convention `outstanding_by_market` and
            `risk_exposure_usd` use for an unreadable price;
          - every parked `pending_terminal` entry. Absent from the venue's
            open orders is terminal THERE, which includes FILLED -- but until
            an authoritative `/v1/order` read books its final cumQuantity we
            cannot say the order is gone, and a cancel we could not verify
            lands here;
          - every `quarantine` entry, for the same reason.

        An order whose cancel came back `still_open` or `unverified` stays in
        `live_orders` with its remaining intact, so it is counted by the first
        rule -- which is the case that matters most, because that is the
        unmanaged resting order a one-order pilot must never sit on top of.
        """
        n = 0
        for o in (self.state.get("live_orders") or {}).values():
            state = str(o.get("state") or "")
            if any(h in state.upper() for h in TERMINAL_HINTS):
                continue
            rem = venue_decimal(o.get("remaining"))
            if rem is None or rem > 0:
                n += 1
        n += len(self.state.get("pending_terminal") or {})
        n += len(self.state.get("quarantine") or {})
        return n

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

    def owns_venue_order_id(self, venue_order_id: Any) -> bool:
        """Did THIS ledger put that venue order id on the book?

        WHY THE LEDGER AND NOT THE TRANSPORT ANSWERS THIS. Ownership has to
        survive a restart: the orphan case is precisely a fresh transport that
        never submitted the order it now has to withdraw, so a transport-local
        set of "ids I submitted" is empty exactly when the question matters.
        The ledger is on the durable volume and remembers.

        Every place an id of ours can be recorded is consulted -- live orders,
        orders pending a terminal read, quarantined orders, and the permanent
        submit log -- because an id that has moved between those is still
        ours. An id we never recorded is NOT ours: a manual order the owner
        placed by hand must never be cancellable by this process.
        """
        vid = str(venue_order_id or "").strip()
        if not vid:
            return False
        buckets = ("live_orders", "pending_terminal", "quarantine", "submitted")
        for name in buckets:
            rows = self.state.get(name) or {}
            if isinstance(rows, dict):
                rows = rows.values()
            for row in (rows or ()):
                if not isinstance(row, Mapping):
                    continue
                if str(row.get("venue_order_id") or "").strip() == vid:
                    return True
        return False

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

    def verified_l5_net_for_gates(self) -> Optional[D]:
        """Return verified after-fee L5 net, never legacy estimated gross.

        No trustworthy net ledger exists for the earlier mixed/manual
        positions. Future activation requires the fee/rebate reconciliation
        to persist an explicit verified result with source evidence. An
        absent or malformed record is UNKNOWN, never a free $0 baseline.
        """
        record = self.state.get("verified_l5_net_attestation")
        if not isinstance(record, Mapping):
            return None
        if record.get("source") != "AUDITED_L5_FILLS_FEES_SETTLEMENTS":
            return None
        if not record.get("order_ids") or not record.get("fee_evidence"):
            return None
        try:
            amount = D(str(record.get("net_usd")))
            return amount if amount.is_finite() else None
        except (InvalidOperation, TypeError, ValueError):
            return None

    #: the one source string verified_l5_net_for_gates() will accept. Named
    #: here so the writer and the reader cannot drift apart.
    VERIFIED_L5_NET_SOURCE = "AUDITED_L5_FILLS_FEES_SETTLEMENTS"

    def reconcile_verified_l5_net(self) -> Dict[str, Any]:
        """Turn verified GROSS L5 settlement into a verified NET attestation.

        THE GAP THIS CLOSES. `verified_l5_net_for_gates()` has always read a
        `verified_l5_net_attestation` record, and NOTHING in src/ or scripts/
        ever wrote one -- the only writer in the repository was a test
        fixture. So the independent strategy-loss gate could never activate:
        opting in refused STRATEGY_NET_UNVERIFIED forever. The settlement
        path already accumulates the hard part (`verified_l5_settlement_gross`
        and `verified_l5_settlements`, gated on known cost basis AND
        l5_owner_verified provenance AND exact quantity/cost agreement); the
        only missing step was fees, which is why every row carries
        fee_rebate_status NOT_VERIFIED. This is that step and nothing more.

        WHAT IT WILL NOT DO. It never touches realized_attributed,
        realized_unattributed, or any retired settlement row: the
        account-wide history stays exactly as recorded, including the
        conservative $1/share upper bounds. It produces a SEPARATE L5-only
        figure. It is therefore not a way to make an account-wide loss
        smaller, and must never be described as one.

        MISSING IS NOT ZERO. A contributing order with no recorded commission
        refuses FEE_EVIDENCE_MISSING rather than contributing a free $0 fee,
        because an unknown fee makes the net unknown, and an unknown net must
        not reach a risk gate. A zero RECORDED commission is a fact and is
        accepted; an ABSENT one is not. Rebates are deliberately excluded:
        this venue credits them separately and a zero commission is not a
        rebate, so counting an uncredited rebate here would flatter the net.
        """
        rows = list(self.state.get("verified_l5_settlements") or [])
        if not rows:
            return {"ok": False, "reason": "NO_VERIFIED_L5_SETTLEMENTS",
                    "labels": ("no settled market has passed the verified-L5 "
                               "test, so there is no L5-only net to attest. "
                               "This is the correct answer while every leg "
                               "carries an unknown cost basis.")}
        prov = self.state.get("fill_provenance") or {}
        fee_map = self.state.get("fees") or {}   # NOT the pmus_fees module
        gross = D(str(self.state.get("verified_l5_settlement_gross") or 0))
        order_ids: List[str] = []
        fee_evidence: Dict[str, str] = {}
        missing: List[str] = []
        markets: List[str] = []
        for row in rows:
            mkt = str(row.get("market") or "")
            if not mkt:
                return {"ok": False, "reason": "VERIFIED_ROW_WITHOUT_MARKET"}
            markets.append(mkt)
            entries = ((prov.get(mkt) or {}).get("entries") or [])
            if not entries:
                return {"ok": False, "reason": "PROVENANCE_MISSING:%s" % mkt}
            for e in entries:
                if not e.get("l5_owner_verified"):
                    # a row that reached verified_l5_settlements cannot hold
                    # one of these, so this is a corrupted book, not a skip
                    return {"ok": False,
                            "reason": "UNVERIFIED_OWNER_IN_VERIFIED_ROW:%s"
                            % mkt}
                coid = e.get("coid")
                if not coid:
                    return {"ok": False, "reason": "FILL_WITHOUT_COID:%s" % mkt}
                coid = str(coid)
                if coid in fee_evidence:
                    continue
                if coid not in fee_map:
                    missing.append(coid)
                    continue
                try:
                    fee_evidence[coid] = str(D(str(fee_map[coid])))
                except (InvalidOperation, TypeError, ValueError):
                    return {"ok": False,
                            "reason": "FEE_UNPARSEABLE:%s" % coid}
                order_ids.append(coid)
        if missing:
            return {"ok": False, "reason": "FEE_EVIDENCE_MISSING",
                    "orders": sorted(set(missing))[:16],
                    "labels": ("an unknown commission makes the net unknown. "
                               "A missing fee is never counted as zero.")}
        if not order_ids:
            return {"ok": False, "reason": "NO_CONTRIBUTING_ORDERS"}
        fees_total = sum((D(v) for v in fee_evidence.values()), D(0))
        net = gross - fees_total
        rec = {
            "source": self.VERIFIED_L5_NET_SOURCE,
            "net_usd": str(net),
            "gross_usd": str(gross),
            "fees_usd": str(fees_total),
            "order_ids": sorted(set(order_ids)),
            "fee_evidence": dict(fee_evidence),
            "markets": sorted(set(markets)),
            "rebates_usd_excluded": "0",
            "t": dt.datetime.now(dt.timezone.utc).isoformat(),
            "labels": ("verified L5-ONLY after-fee net. NOT an account-wide "
                       "figure and NOT an offset against account-wide "
                       "realized loss. Rebates are excluded until credited "
                       "and source-accounted."),
        }
        self.state["verified_l5_net_attestation"] = rec
        for row in self.state.get("verified_l5_settlements") or []:
            if str(row.get("market") or "") in set(markets):
                row["fee_rebate_status"] = "FEES_VERIFIED_REBATES_EXCLUDED"
        self.save()
        return {"ok": True, "attestation": rec}

    #: the only source string establish_l5_baseline() will accept.
    L5_BASELINE_SOURCE = "AUTHORITATIVE_ACCOUNT_READS"

    def classify_inventory_ownership(self, positions: Mapping[str, Any]
                                     ) -> Dict[str, Dict[str, str]]:
        """Split held inventory into L5-OWNED and MANUAL_OR_UNKNOWN.

        A leg is L5-owned only when its market carries fill_provenance with
        at least one l5_owner_verified entry -- our own durable ACK record.
        Everything else is MANUAL_OR_UNKNOWN and is never promoted to L5 by
        the absence of a record: unknown ownership stays unknown.

        Both halves are returned, and the caller snapshots BOTH, because the
        manual/unknown half must keep contributing to account-wide capital and
        risk even though it is not L5's.
        """
        prov = self.state.get("fill_provenance") or {}
        l5_owned: Dict[str, str] = {}
        other: Dict[str, str] = {}
        for key, qty in dict(positions or {}).items():
            market = str(key).rsplit("|", 1)[0]
            entries = ((prov.get(market) or {}).get("entries") or [])
            mine = any(isinstance(e, Mapping) and e.get("l5_owner_verified")
                       for e in entries)
            (l5_owned if mine else other)[str(key)] = str(qty)
        return {"l5_owned": l5_owned, "manual_or_unknown": other}

    def establish_l5_baseline(self, *, cash_usd: Any,
                              l5_open_orders: Any,
                              positions: Any,
                              outstanding_obligations_usd: Any,
                              source: str) -> Dict[str, Any]:
        """Record the evidence-backed L5 baseline epoch T. Idempotent.

        WHAT A BASELINE IS NOT. It is NOT a claim that historical L5 losses
        were zero. The pre-T account-wide realized figure is snapshotted here
        and kept verbatim, the unknown-cost provenance keys are kept with it,
        and realized_lines() goes on reporting them. Nothing pre-T is
        rewritten, retired or netted away.

        WHAT IT IS. A stated instant, backed by authoritative account reads,
        from which L5-ATTRIBUTED settlements are counted. After T the measure
        reads 0 because zero attested L5 settlements have occurred since T --
        a COUNTED fact with a counted denominator, not an inference from
        absent history. That distinction is the whole point: it is why
        l5_realized_since_baseline() returns None without a baseline and a
        number only with one.

        EVERY READ IS REQUIRED. Cash, L5-owned open orders, positions and
        outstanding obligations must all be supplied from authoritative reads;
        any of them missing refuses. A baseline struck without knowing what is
        already resting or already held would be a baseline over an unknown
        book.
        """
        if str(source) != self.L5_BASELINE_SOURCE:
            return {"established": False, "reason": "BASELINE_SOURCE_INVALID",
                    "required": self.L5_BASELINE_SOURCE}
        prior = self.state.get("l5_baseline")
        if isinstance(prior, Mapping) and prior.get("t"):
            return {"established": False, "reason": "BASELINE_ALREADY_SET",
                    "baseline": dict(prior)}
        try:
            cash = D(str(cash_usd))
            oblig = D(str(outstanding_obligations_usd))
            if not (cash.is_finite() and oblig.is_finite()):
                raise InvalidOperation("non-finite")
        except (InvalidOperation, TypeError, ValueError):
            return {"established": False,
                    "reason": "BASELINE_READS_UNAVAILABLE",
                    "labels": ("cash and outstanding obligations must come "
                               "from authoritative reads; missing is never "
                               "zero")}
        if l5_open_orders is None or positions is None:
            return {"established": False,
                    "reason": "BASELINE_READS_UNAVAILABLE",
                    "labels": ("L5-owned open orders and positions must be "
                               "established before an epoch can be struck")}
        open_ids = sorted(str(x) for x in l5_open_orders)
        if open_ids:
            return {"established": False,
                    "reason": "L5_ORDERS_STILL_OPEN_AT_BASELINE",
                    "orders": open_ids[:16],
                    "labels": ("an epoch must start from a book with no L5 "
                               "order already working")}
        # L5-ONLY FLATNESS, NOT ACCOUNT-WIDE (OWNER 2026-10-16). The owner
        # trades manually alongside L5 and must not be asked to stop, so a
        # manual or unknown-ownership position does NOT block the epoch. What
        # must be clear is L5's OWN side: no working order (above), no
        # outstanding obligation, and no unresolved L5-owned position.
        if oblig != 0:
            return {"established": False,
                    "reason": "L5_OBLIGATIONS_OUTSTANDING_AT_BASELINE",
                    "outstanding_obligations_usd": str(oblig),
                    "labels": ("L5 still has committed cash outstanding; an "
                               "epoch cannot start over its own unsettled "
                               "obligation")}
        split = self.classify_inventory_ownership(positions)
        unresolved_l5 = {k: v for k, v in split["l5_owned"].items()
                         if _nonzero(v)}
        if unresolved_l5:
            return {"established": False,
                    "reason": "L5_POSITIONS_UNRESOLVED_AT_BASELINE",
                    "l5_owned_positions": dict(sorted(unresolved_l5.items())),
                    "labels": ("an L5-owned position is still held; its "
                               "settlement would land post-T and be counted "
                               "as L5 without its pre-T cost being in the "
                               "L5 measure")}
        rec = {
            "t": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source": self.L5_BASELINE_SOURCE,
            "cash_usd": str(cash),
            "outstanding_obligations_usd": str(oblig),
            "l5_open_orders": [],
            # BOTH halves are snapshotted. The manual/unknown half is
            # recorded so its exposure stays visible to account-wide capital
            # and risk, and is explicitly NOT assigned to L5.
            "positions_at_baseline": {str(k): str(v)
                                      for k, v in dict(positions).items()},
            "l5_owned_positions_at_baseline": dict(
                sorted(split["l5_owned"].items())),
            "manual_or_unknown_positions_at_baseline": dict(
                sorted(split["manual_or_unknown"].items())),
            "manual_inventory_labels": (
                "MANUAL_OR_UNKNOWN ownership. NOT L5 inventory and NOT L5 "
                "P&L. It remains in account-wide capital, exposure and risk "
                "calculations, and its ownership stays UNKNOWN rather than "
                "being read as L5 or as zero."),
            # PRE-T HISTORY, PRESERVED VERBATIM AND NEVER NETTED AWAY
            "pre_baseline_realized_attributed": str(
                D(str(self.state.get("realized_attributed") or 0))),
            "pre_baseline_realized_unattributed": str(
                D(str(self.state.get("realized_unattributed") or 0))),
            "pre_baseline_realized_total": str(self.realized_today()),
            "pre_baseline_unknown_basis_keys": sorted(
                str(k) for k in
                (self.state.get("leg_cost_basis_unknown") or [])),
            "labels": ("L5 strategy accounting starts here. Pre-T figures are "
                       "RETAINED and are NOT a claim that historical L5 "
                       "losses were zero -- they are account-wide and their "
                       "L5 share is UNKNOWN."),
        }
        self.state["l5_baseline"] = rec
        self.save()
        return {"established": True, "baseline": rec}

    def l5_realized_since_baseline(self) -> Optional[D]:
        """Attested L5 settlement P&L since T, or None without a baseline.

        Only markets retired AFTER T whose ownership was ATTESTED at
        settlement (l5_owner_attribution_verified) are counted. A post-T
        settlement that was NOT attested is deliberately excluded from the
        figure and surfaced separately by l5_baseline_lines(), so an
        unattributable settlement can never silently read as an L5 zero.
        """
        base = self.state.get("l5_baseline")
        if not isinstance(base, Mapping) or not base.get("t"):
            return None
        t0 = str(base["t"])
        total = D(0)
        for mkt, row in (self.state.get("retired_markets") or {}).items():
            if not isinstance(row, Mapping):
                continue
            if str(row.get("t") or "") <= t0:
                continue
            if not row.get("l5_owner_attribution_verified"):
                continue
            try:
                total += D(str(row.get("settlement_pnl") or 0))
            except (InvalidOperation, TypeError, ValueError):
                return None          # an unreadable row makes the sum unknown
        return total

    def l5_baseline_lines(self) -> Dict[str, Any]:
        """Everything an operator needs to judge the baseline, apart."""
        base = self.state.get("l5_baseline")
        if not isinstance(base, Mapping) or not base.get("t"):
            return {"baseline": None,
                    "l5_realized_since_baseline": None,
                    "labels": "no baseline: the L5-scoped gates refuse"}
        t0 = str(base["t"])
        counted, excluded = [], []
        for mkt, row in sorted((self.state.get("retired_markets") or {}).items()):
            if not isinstance(row, Mapping) or str(row.get("t") or "") <= t0:
                continue
            (counted if row.get("l5_owner_attribution_verified")
             else excluded).append({
                 "market": str(mkt), "pnl": str(row.get("settlement_pnl")),
                 "provenance": str(row.get("pnl_provenance"))})
        net = self.l5_realized_since_baseline()
        return {
            "baseline_t": t0,
            "baseline_cash_usd": str(base.get("cash_usd")),
            "l5_realized_since_baseline": None if net is None else str(net),
            "attested_l5_settlements_since_baseline": counted,
            "post_baseline_settlements_NOT_attested": excluded,
            "pre_baseline_realized_total": str(
                base.get("pre_baseline_realized_total")),
            "pre_baseline_unknown_basis_legs": len(
                base.get("pre_baseline_unknown_basis_keys") or []),
            "labels": ("l5_realized_since_baseline counts ONLY attested L5 "
                       "settlements after T. post_baseline_settlements_NOT_"
                       "attested are excluded from that figure and listed "
                       "here so an unattributable settlement is never read "
                       "as an L5 zero. Pre-T totals are account-wide and "
                       "their L5 share is UNKNOWN."),
        }

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
            # Existing retired-market rows do not persist complete order-owner,
            # cost-basis provenance or fee/rebate evidence. In particular,
            # venue-adopted positions use a $1/share loss upper bound, and
            # manual positions can share this account. NEVER call the legacy
            # attributed total verified L5-only net profit or loss.
            "verified_l5_realized_net": None,
            "verified_l5_status": "UNPROVEN_LEGACY_ATTRIBUTION",
            "unknown_basis_keys": sorted(set(str(k) for k in
                (self.state.get("leg_cost_basis_unknown") or []))),
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

    def correct_settled_market(self, market: str, *, correction_id: str,
                               expect: Mapping[str, Any],
                               corrected_yes_value: D,
                               evidence: Mapping[str, Any]
                               ) -> Dict[str, Any]:
        """Restate ONE already-retired market's settlement, auditably.

        WHY NOT `rebase_session_accounting`. That method sets
        `realized_unattributed = cash_delta - attributed`, so it is only
        correct when the cash anchor spans the WHOLE attributed line. Stage
        A's anchor spans Stage A alone while the attributed line also carries
        the 2026-10-12 windows, so rebasing with it would post a +0.100000
        unattributed credit that is a span artifact and would make realized
        LESS negative -- loosening the very halt this is meant to tighten.
        This restates the one market instead, and leaves `unattributed`
        alone.

        NOTHING IS OVERWRITTEN. The original retirement record is copied
        verbatim into `superseded` on the same row and never edited, so the
        contaminated number stays readable beside the corrected one.

        IDEMPOTENT THREE WAYS. By `correction_id` in
        `settlement_corrections`; by refusing unless the stored row still
        matches `expect` field for field (a row already corrected no longer
        does); and by refusing a correction that would move realized toward
        zero, so this can never be used to relax the daily loss halt.
        """
        market = canonical_market(market, doing="correcting a settlement")
        done = {r.get("id") for r in
                (self.state.get("settlement_corrections") or [])}
        if correction_id in done:
            return {"corrected": False, "market": market,
                    "reason": "CORRECTION_ALREADY_APPLIED",
                    "correction_id": correction_id}
        row = (self.state.get("retired_markets") or {}).get(market)
        if not row:
            return {"corrected": False, "market": market,
                    "reason": "MARKET_NOT_RETIRED"}
        if row.get("superseded"):
            return {"corrected": False, "market": market,
                    "reason": "ALREADY_CORRECTED"}
        mismatch = {k: {"expected": str(v), "found": str(row.get(k))}
                    for k, v in dict(expect).items()
                    if str(row.get(k)) != str(v)}
        if mismatch:
            # the ledger does not hold what the evidence describes. Repairing
            # it blind is how a correct number gets overwritten by a stale one.
            return {"corrected": False, "market": market,
                    "reason": "LEDGER_DOES_NOT_MATCH_EVIDENCE",
                    "mismatch": mismatch}
        verdict = binary_settlement(corrected_yes_value)
        if verdict.get("reason"):
            return {"corrected": False, "market": market,
                    "reason": verdict["reason"]}
        v = verdict["value"]
        up = D(str(row.get("up_qty") or 0))
        dn = D(str(row.get("dn_qty") or 0))
        cost = D(str(row.get("cost_total") or 0))
        redeem = up * v + dn * (D(1) - v)
        pnl = redeem - cost
        was_pnl = D(str(row.get("settlement_pnl") or 0))
        delta = pnl - was_pnl
        if delta > 0:
            # A correction that IMPROVES realized would move the account away
            # from DAILY_REALIZED_LOSS_HALT. That direction needs its own
            # evidence and its own decision; it is not available here.
            return {"corrected": False, "market": market,
                    "reason": "CORRECTION_WOULD_RELAX_LOSS_HALT",
                    "delta": str(delta)}
        before = self.realized_lines()
        self.state["realized_attributed"] = str(
            D(str(self.state.get("realized_attributed") or 0)) + delta)
        self.state["realized_today"] = str(
            D(str(self.state.get("realized_today") or 0)) + delta)
        # `realized_unattributed` IS NOT TOUCHED. Stage A's cash is fully
        # attributed; inventing an unattributed line here would be the
        # phantom credit again in another column.
        row["superseded"] = dict(row)            # verbatim, never edited
        row.update({
            "yes_value": str(v), "settlement_pnl": str(pnl),
            "redemption_total": str(redeem),
            "corrected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "correction_id": correction_id,
        })
        self.state.setdefault("settlement_corrections", []).append({
            "id": correction_id, "market": market,
            "t": dt.datetime.now(dt.timezone.utc).isoformat(),
            "was_yes_value": str(expect.get("yes_value")),
            "now_yes_value": str(v),
            "was_settlement_pnl": str(was_pnl), "now_settlement_pnl": str(pnl),
            "delta": str(delta), "evidence": dict(evidence),
        })
        self.save()
        return {"corrected": True, "market": market,
                "correction_id": correction_id,
                "delta": str(delta),
                "realized_before": before,
                "realized_after": self.realized_lines()}

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
        # IDENTITY FIRST, before a single field moves. This is the only
        # writer of positions and leg_cost on the fill path, so validating
        # here is what makes "" unreachable in inventory, committed,
        # cash_deployed, risk, settlement and retirement at once.
        own_market = canonical_market(market, doing="booking a fill")
        own_side = canonical_side(side, doing="booking a fill")
        own = own_outcome_price(own_side, price, basis=basis)
        k = "%s|%s" % (own_market, own_side)
        # Persist fill ownership at booking time; settlement cannot infer it.
        submitted = ((self.state.get("submitted") or {}).get(coid)
                     if coid else None)
        owner_verified = bool(
            isinstance(submitted, Mapping)
            and str(submitted.get("session_id") or "").strip()
            and str(submitted.get("market") or "") == own_market
            and str(submitted.get("side") or "") == own_side)
        proof = self.state.setdefault("fill_provenance", {}).setdefault(
            own_market, {"legs": {}, "entries": []})
        leg = proof["legs"].setdefault(
            own_side, {"total_qty": "0", "verified_qty": "0",
                       "verified_cost": "0"})
        leg["total_qty"] = str(D(str(leg["total_qty"])) + qty)
        if owner_verified:
            leg["verified_qty"] = str(D(str(leg["verified_qty"])) + qty)
            leg["verified_cost"] = str(
                D(str(leg["verified_cost"])) + own * qty)
        proof["entries"].append({
            "coid": str(coid) if coid else None,
            "session_id": submitted.get("session_id") if owner_verified else None,
            "side": own_side, "qty": str(qty), "actual_cost": str(own * qty),
            "l5_owner_verified": owner_verified})

        if str(side).strip().upper() != own_side:
            raw = self.state.setdefault("venue_side_raw", {})
            raw.setdefault(k, sorted(set(raw.get(k, []))
                                     | {str(side)}))
        self.state["positions"][k] = str(
            D(str(self.state["positions"].get(k, 0))) + qty)
        # both of these are CASH OUT in the leg's own terms now. They used to
        # take the wire number, which credits a short instead of charging it.
        self.state["committed"][own_market] = str(
            D(str(self.state["committed"].get(own_market, 0))) + own * qty)
        lc = self.state.setdefault("leg_cost", {})
        lc[k] = str(D(str(lc.get(k, 0))) + own * qty)
        # HOW MANY FILLS BUILT THIS LEG. A leg with shares and no cost is
        # either a genuine fill at $0.00 -- rare but real, and attested by
        # this counter -- or quantity that arrived from the venue's net
        # with no cost basis at all, which is 2045z. book_violations reads
        # this to tell the two apart instead of guessing.
        fc = self.state.setdefault("fill_count", {})
        fc[k] = int(fc.get(k, 0)) + 1
        # THE LARGEST CLIP THIS MARKET HAS ACTUALLY FILLED. RESIDUAL_CAP
        # states its bound in units of the session's own clip rather than as
        # a constant, so a market that trades in ones and a market that
        # trades in fifties get a bound of the same SHAPE. It is recorded on
        # every fill whether or not any policy reads it, so turning the cap
        # on later cannot find an empty history.
        mc = self.state.setdefault("max_clip", {})
        if D(str(qty)) > D(str(mc.get(own_market, 0))):
            mc[own_market] = str(D(str(qty)))
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
        market = canonical_market(market, doing="reading leg cost")
        lc = self.state.get("leg_cost") or {}
        return {side: D(str(lc.get("%s|%s" % (market, side), 0)))
                for side in ("UP", "DOWN")}

    def max_clip(self, market: str) -> D:
        """Largest single filled quantity in this market; 0 before any fill.

        Reads only what apply_fill wrote, so a market whose inventory was
        ADOPTED from the venue rather than filled through us reports 0 and a
        cap derived from it falls back to the candidate's own size. That is
        the conservative direction: a smaller clip is a tighter bound.
        """
        market = canonical_market(market, doing="reading max clip")
        mc = self.state.get("max_clip") or {}
        return D(str(mc.get(market, 0)))

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
        market = canonical_market(market, doing="reporting cash deployed")
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
        market = canonical_market(market, doing="pricing risk")
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
        worst = worst if worst > 0 else D(0)
        # NEVER BELOW THE FLOOR. Where the venue's position was adopted over
        # ours, adopt_venue_position pinned the worse of the two worst cases.
        # The computed number is still used when it is LARGER -- the floor is
        # a lower bound on the bound, never a replacement for it, so a
        # proposed `extra_*` leg still makes this grow as it should.
        floor = (self.state.get("risk_floor_usd") or {}).get(market)
        if floor is not None:
            try:
                worst = max(worst, D(str(floor)))
            except (TypeError, ValueError, ArithmeticError):
                pass
        return worst

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
        # refuse before anything is recorded, not after
        market = canonical_market(market, doing="booking order progress")
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

    def repair_orphaned_market_key(self, *, key: str, to_market: str,
                                   qty: D, cost: D, side: str,
                                   settlement_pnl: D, evidence: Mapping[str, Any]
                                   ) -> Dict[str, Any]:
        """Retire one corrupt ledger key against authoritative venue evidence.

        NOT a generic cleanup and not a plug. The caller states which key,
        which market it authoritatively belonged to, what the venue says it
        cost, and what it settled for -- and every one of those must come
        from a /v1/order row or an authoritative balance, never from the
        corrupt ledger itself.

        The P&L is booked ATTRIBUTED because it is attributable: two venue
        fill rows at venue prices. It is removed from the book entirely,
        because the market it belonged to has already settled and cannot
        settle again.
        """
        if not str(evidence.get("source") or "").strip():
            raise ValueError("repair requires stated authoritative evidence")
        canonical_market(to_market, doing="repairing an orphaned key")
        removed: Dict[str, Any] = {}
        for name in ("positions", "leg_cost", "fill_count", "committed",
                     "venue_side_raw"):
            d = self.state.get(name)
            if not isinstance(d, dict):
                continue
            for k in [x for x in d
                      if str(x) == key or str(x).rsplit("|", 1)[0] == key]:
                removed["%s:%s" % (name, k)] = d.pop(k)
        self.state["realized_attributed"] = str(
            D(str(self.state.get("realized_attributed") or 0))
            + D(str(settlement_pnl)))
        rec = {"t": dt.datetime.now(dt.timezone.utc).isoformat(),
               "bad_key": key, "authoritative_market": to_market,
               "side": side, "qty": str(qty), "cost": str(cost),
               "settlement_pnl": str(settlement_pnl),
               "removed": {k: str(v) for k, v in removed.items()},
               "evidence": dict(evidence),
               "labels": ("the original ledger did not know this market. "
                          "The bad key is preserved here, not rewritten.")}
        self.state.setdefault("orphan_repairs", []).append(rec)
        self.save()
        return dict(rec, realized_total=str(self.realized_today()))

    def quarantine_order(self, coid: str, *, reason: str, detail: str,
                         row: Mapping[str, Any]) -> Dict[str, Any]:
        """Hold an order whose identity we cannot establish. NO accounting.

        Quarantine is not a soft failure. The order leaves live_orders and
        pending_terminal so nothing keeps asking about it, its raw row is
        kept verbatim for a human to resolve, and the book is marked
        uncertain -- which refuses every submission -- because an order we
        cannot identify may hold real inventory we cannot see.

        Nothing here touches positions, leg_cost, committed or realized. An
        unidentified order contributes exactly zero to financial state,
        which is the whole point: the alternative is a market key of "".
        """
        q = self.state.setdefault("quarantine", {})
        q[str(coid)] = {
            "coid": str(coid), "reason": str(reason),
            "detail": str(detail)[:500],
            "t": dt.datetime.now(dt.timezone.utc).isoformat(),
            # verbatim, because resolving this by hand needs what the venue
            # actually said, not our interpretation of it
            "row": {k: (None if v is None else str(v))
                    for k, v in dict(row).items()},
        }
        (self.state.get("live_orders") or {}).pop(str(coid), None)
        (self.state.get("pending_terminal") or {}).pop(str(coid), None)
        self.mark_uncertain(
            "order %s quarantined: %s" % (coid, reason))
        self.save()
        return dict(q[str(coid)])

    def quarantined(self) -> Dict[str, Dict[str, Any]]:
        return {k: dict(v) for k, v in
                (self.state.get("quarantine") or {}).items()}

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
        # THE LINE THAT MADE "". `str(row.get("market") or "")` turned a
        # missing identity into a usable dict key; it now refuses, and the
        # caller quarantines rather than booking against a market we cannot
        # name.
        try:
            market = canonical_market(
                row.get("market"), doing="booking a pending terminal order")
            side = canonical_side(
                row.get("side"), doing="booking a pending terminal order")
        except (UnknownMarket, UnknownSide) as exc:
            self.quarantine_order(coid, reason=type(exc).__name__,
                                  detail=str(exc)[:300], row=row)
            return {"coid": coid, "resolved": False,
                    "reason": "QUARANTINED_UNIDENTIFIED",
                    "detail": type(exc).__name__}
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

    def corrupt_market_keys(self) -> List[str]:
        """Stored keys whose market part is not a valid identity.

        Enumeration must never raise. A guard that refuses to BOOK against
        a bad key is correct; a guard that refuses to LIST one leaves the
        process unable to say what is wrong with it. Deployed 2026-09-27,
        the "" key from the adopted-order incident was still on the volume
        and every reconcile died reading it -- fail-closed, and wedged.
        """
        bad: set = set()
        for name in ("positions", "leg_cost", "fill_count", "committed",
                     "venue_side_raw"):
            d = self.state.get(name)
            if not isinstance(d, dict):
                continue
            for key in d:
                head = (str(key).rsplit("|", 1)[0] if "|" in str(key)
                        else str(key))
                try:
                    canonical_market(head, doing="enumerating markets")
                except UnknownMarket:
                    bad.add(str(key))
        return sorted(bad)

    def markets_with_exposure(self) -> List[str]:
        """Every market this book still has anything in.

        Both sources, because they are written by different paths and can
        disagree: `committed` moves on our own fills, while
        reconcile_from_venue writes `positions` straight from the venue
        without touching committed. A market that came back from a restart
        through reconciliation therefore has positions and no committed, and
        sweeping only committed would leave it in the book forever.
        """
        def _ok(m: str) -> bool:
            try:
                canonical_market(m, doing="enumerating markets")
                return True
            except UnknownMarket:
                return False       # named by corrupt_market_keys(), not here

        out = {m for m, v in self.committed().items() if v != 0 and _ok(m)}
        for key in (self.state.get("positions") or {}):
            m = str(key).rsplit("|", 1)[0]
            if _ok(m):
                out.add(m)
        for key in (self.state.get("leg_cost") or {}):
            m = str(key).rsplit("|", 1)[0]
            if _ok(m):
                out.add(m)
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

    def retire_settled_market(self, market: str, *, yes_value: D,
                              resolution: Optional[Mapping[str, Any]] = None
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
        market = canonical_market(market, doing="settling a market")
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
        # STAGE A, 2026-10-13. `yes_value` arrived as 0.01 -- the last BOOK
        # PRICE the expired market happened to carry -- and the old guard
        # here accepted it because 0.01 is inside [0, 1]. 2.90 UP shares were
        # then redeemed at 0.01 for a PHANTOM $0.029 credit and realized was
        # booked -0.561000 when the true realized result was -0.590000. A
        # binary contract settles at exactly 0 or exactly 1; every other
        # value, in range or not, is a mark and a mark is not cash.
        verdict = binary_settlement(yes_value)
        if verdict.get("reason"):
            return {"retired": False, "market": market,
                    "reason": verdict["reason"],
                    "yes_value": verdict["yes_value"]}
        v = verdict["value"]
        # AUTHORITATIVE AND MARKET-MATCHED. A settlement price is only
        # realized cash if it came from the venue's own final resolution of
        # THIS market. A record from another market, from a route that does
        # not carry a resolution, or one that disagrees with the value being
        # booked, settles nothing.
        bad = resolution_refusal(market, resolution, v)
        if bad:
            return dict(bad, retired=False, market=market, yes_value=str(v))

        # Snapshot unknown cost provenance BEFORE retiring the book. A venue
        # position adopted at the $1/share upper bound is NOT an observed fill
        # cost and must not disappear into an apparently verified P&L line.
        unknown_basis_at_settlement = sorted(str(k) for k in
            (self.state.get("leg_cost_basis_unknown") or [])
            if str(k).rsplit("|", 1)[0] == market)
        up = self.position(market, "UP")
        dn = self.position(market, "DOWN")
        lc = self.leg_cost(market)
        cost_up, cost_dn = lc["UP"], lc["DOWN"]

        redeem = up * v + dn * (D(1) - v)
        cost = cost_up + cost_dn
        pnl = redeem - cost
        proof = (self.state.get("fill_provenance") or {}).get(market) or {}
        legs = proof.get("legs") or {}
        # Separate verified GROSS L5 settlement, never an account-loss offset.
        # Verified NET remains unavailable until fees/rebates are reconciled.
        verified_l5 = (
            not unknown_basis_at_settlement
            and bool(proof.get("entries"))
            and all(bool(x.get("l5_owner_verified"))
                    for x in proof["entries"])
            and D(str((legs.get("UP") or {}).get("total_qty", 0))) == up
            and D(str((legs.get("DOWN") or {}).get("total_qty", 0))) == dn
            and D(str((legs.get("UP") or {}).get("verified_cost", 0))) == cost_up
            and D(str((legs.get("DOWN") or {}).get("verified_cost", 0))) == cost_dn
        )
        if verified_l5:
            self.state["verified_l5_settlement_gross"] = str(
                D(str(self.state.get("verified_l5_settlement_gross") or 0))
                + pnl)
            self.state.setdefault("verified_l5_settlements", []).append({
                "market": market, "pnl_before_fees": str(pnl),
                "fee_rebate_status": "NOT_VERIFIED",
                "source": "VENUE_SETTLEMENT_AND_MATCHED_L5_FILL_ORDERS"})


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
            "resolution_source": (dict(resolution).get("source")
                                  if isinstance(resolution, Mapping)
                                  else None),
            "up_qty": str(up), "dn_qty": str(dn),
            "cost_total": str(cost), "redemption_total": str(redeem),
            "unverified_cost_basis_keys": unknown_basis_at_settlement,
            "l5_owner_attribution_verified": verified_l5,
            "pnl_provenance": ("VERIFIED_L5_GROSS_SETTLEMENT" if verified_l5
                               else "UNKNOWN_COST_UPPER_BOUND" if
                               unknown_basis_at_settlement else
                               "OWNERSHIP_NOT_ATTESTED"),
        }
        self.save()
        return {
            "retired": True, "market": market, "yes_value": str(v),
            "resolution_source": (dict(resolution).get("source")
                                  if isinstance(resolution, Mapping)
                                  else None),
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

    def adopt_venue_position(self, market: str,
                             rows: Mapping[str, Any], *, why: str) -> None:
        """Make the VENUE's rows the position of record for one market.

        WHY THIS EXISTS. `reconcile_from_venue`'s own contract said "where
        they disagree the venue's net becomes the position of record", and it
        did not do that: it named the market in `gross_detail_lost` and left
        our book alone. Measured 2026-10-14: our book 7.00 UP against a venue
        row of 10.34 UP left `positions` reading 7.00, and `positions` is what
        `risk_exposure_usd` -- and therefore the $5 gate -- reads. We were
        understating venue-confirmed exposure by 3.34 shares, in the one
        direction that lets the gate under-bind.

        THREE THINGS HAPPEN HERE, and the order matters.

        1. THE PRIOR DETAIL IS QUARANTINED, NOT DELETED. A matched pair is a
           locked economic result, so the gross book and its cost basis are
           moved to `gross_quarantine` verbatim before anything is replaced.
           Nothing is lost; it stops being the position of record.

        2. THE UNKNOWN COST BASIS IS BOOKED AT ITS UPPER BOUND. We cannot
           decompose a net into a gross book and we do not try. But leaving
           the basis absent makes the book INCOHERENT -- `QUANTITY_WITHOUT_COST`
           -- and `risk_exposure_usd` then RAISES instead of returning a
           number, which is fail-closed for new orders and useless for the
           risk-reducing actions that must stay available. So the basis is
           recorded at UNKNOWN_PRICE_WORST_CASE, $1 a share: the most a share
           can cost and the most it can lose. That is the conservative bound
           the module already uses for an unreadable order price, not an
           invented favourable detail, and the keys are listed in
           `leg_cost_basis_unknown` so no later reader mistakes it for an
           observed cost.

        3. A RISK FLOOR IS RECORDED. The swap must never be able to REDUCE
           measured exposure, including when the venue's net is smaller than
           our gross -- a net of 2.81 is consistent with 2.47 UP + 5.28 DOWN,
           and the pair's own legs can lose more than the net suggests. So
           the worse of the two worst cases is pinned as a floor and
           `risk_exposure_usd` never returns less than it while the market
           is uncertain.

        The market stays named to the caller so it is marked uncertain, which
        is what refuses new exposure. Cancellation and risk-reducing paths are
        untouched: they do not go through the submission gate.
        """
        market = canonical_market(market, doing="adopting a venue position")
        prior_risk: Optional[D] = None
        try:
            prior_risk = self.risk_exposure_usd(market)
        except BaseException:                                  # noqa: BLE001
            prior_risk = None          # an incoherent prior book bounds nothing
        pos = self.state.setdefault("positions", {})
        lc = self.state.setdefault("leg_cost", {})
        quarantine = self.state.setdefault("gross_quarantine", {})
        keys = [k for k in pos if k.rsplit("|", 1)[0] == market]
        quarantine[market] = {
            "why": str(why),
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "positions": {k: str(pos.get(k)) for k in keys},
            "leg_cost": {k: str(lc.get(k)) for k in keys if k in lc},
            "fill_count": {k: self.fill_count(market, k.rsplit("|", 1)[1])
                           for k in keys},
            "note": ("the gross book that was the position of record before "
                     "the venue contradicted it. Preserved verbatim: a "
                     "matched pair is a locked economic result and deleting "
                     "it silently deletes that result."),
        }
        for k in keys:
            pos.pop(k, None)
            lc.pop(k, None)
        unknown = self.state.setdefault("leg_cost_basis_unknown", [])
        for k, v in rows.items():
            if k.rsplit("|", 1)[0] != market:
                continue
            pos[k] = str(v)
            lc[k] = str(D(str(v)) * UNKNOWN_PRICE_WORST_CASE)
            if k not in unknown:
                unknown.append(k)
        new_risk: Optional[D] = None
        try:
            new_risk = self.risk_exposure_usd(market)
        except BaseException:                                  # noqa: BLE001
            new_risk = None
        floor = max([r for r in (prior_risk, new_risk) if r is not None]
                    or [D(0)])
        self.state.setdefault("risk_floor_usd", {})[market] = str(floor)

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
        # THE VENUE'S ROWS BECOME THE POSITION OF RECORD. This is the line the
        # docstring always promised and did not do; see adopt_venue_position
        # for what is preserved and what is bounded.
        for m in list(disputed):
            self.adopt_venue_position(
                m, after, why="venue net contradicts our gross")
        for k, v in after.items():
            m = k.rsplit("|", 1)[0]
            if m in agrees:
                continue
            # a market the venue holds and we have never booked: adopt it the
            # same way, so risk sees the shares AND can price them, rather
            # than holding a quantity whose missing basis makes the book
            # incoherent and risk pricing raise.
            if m not in disputed:
                self.adopt_venue_position(
                    m, after, why="venue holds a market we never booked")
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
        quarantined_now: Dict[str, Any] = {}
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
            # MARKETSLUG BY NAME. This venue sends `marketSlug`; adoption
            # used to read `market`, which is absent on a venue row, and for
            # a genuinely adopted order there is no local record to fall
            # back to -- so the market arrived None and became "".
            try:
                _adopt_market = venue_row_market(
                    o, doing="adopting an order",
                    fallback=mine.get("market"))
            except UnknownMarket as exc:
                quarantined_now[coid] = {"reason": "UnknownMarket",
                                         "detail": str(exc)[:300], "row": o}
                continue
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
                "market": _adopt_market,
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
        for _qc, _qv in quarantined_now.items():
            self.quarantine_order(_qc, reason=_qv["reason"],
                                  detail=_qv["detail"], row=_qv["row"])
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
        quarantine = sorted(self.state.get("quarantine") or {})
        # A FLOOR OUTLIVES ONLY ITS UNCERTAINTY. Once a market is no longer
        # disputed its own book is authoritative again, so a stale floor
        # would over-bound it forever.
        floors = self.state.setdefault("risk_floor_usd", {})
        for m in list(floors):
            if m not in disputed:
                floors.pop(m, None)
        if disputed or incoherent or pending or quarantine:
            self.state["orders_uncertain"] = (
                "gross/net divergence %s; incoherent book %s; "
                "unbooked terminal orders %s; quarantined orders %s"
                % (sorted(set(disputed)), sorted(incoherent), pending,
                   quarantine))
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
                "quarantined": quarantine,
                "gross_quarantine": {
                    m: d for m, d in
                    (self.state.get("gross_quarantine") or {}).items()
                    if m in disputed},
                "risk_floor_usd": {
                    m: v for m, v in
                    (self.state.get("risk_floor_usd") or {}).items()},
                "leg_cost_basis_unknown": list(
                    self.state.get("leg_cost_basis_unknown") or []),
                "ALERT": (
                    "VENUE POSITION ADOPTED OVER OUR BOOK for %s. The gross "
                    "detail and its cost basis are preserved in "
                    "gross_quarantine; the adopted shares are priced at the "
                    "$1 upper bound because their basis is unknown; a risk "
                    "floor pins the worse of the two worst cases. NEW "
                    "exposure is refused while this market is uncertain. "
                    "Resolve by reading /v1/order for the fills that built "
                    "the venue's number." % sorted(set(disputed))
                ) if disputed else None,
                "uncertainty_cleared": not (disputed or incoherent
                                            or pending or quarantine),
                "authority": "VENUE"}


class FaithfulExecutor:
    """Drives one L5 action through the whole path. Cannot transmit alone."""

    def __init__(self, store: InventoryStore, limits: SafetyLimits, *,
                 transport: Optional[Any] = None,
                 lam: D = D("1"), now_fn=None,
                 maker_treatment: bool = False,
                 maker_only: bool = False,
                 suppress_imbalance_increase: bool = False,
                 residual_cap_k: Optional[D] = None) -> None:
        self.store = store
        self.limits = limits
        self.transport = transport or NullTransport()
        self.lam = lam
        #: DISARMED-SHADOW HOOKS, declared here rather than attached from
        #: outside. The graft test is right to insist: an attribute the
        #: runner sets but the class never declares is duck-typing that
        #: breaks silently when a refactor renames it.
        #:
        #: None means ABSENT, and absent is never permission. The halt gate
        #: is consulted only when one is installed; available_cash_usd of
        #: None means "no authoritative reading", so the cash gate binds on
        #: its configured ceiling and require_cash_evidence() refuses to arm.
        self.halt_gate: Optional[Any] = None
        self.validation_gates: Optional[Any] = None
        self.available_cash_usd: Optional[D] = None
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
        #: RESIDUAL_CAP. None is OFF and is the default, so an executor that
        #: does not ask for this is byte-for-byte the frozen one. When set to
        #: k, an acquisition that would carry |residual| ABOVE
        #: k * max(largest clip this market has filled, this order's own qty)
        #: is refused. Like the Probe 08 treatment above it can only ever
        #: DECLINE: it never invents a trade, never crosses, never re-prices,
        #: and it is unreachable for an opening leg from flat or for any leg
        #: that reduces the imbalance, because both of those are classified
        #: FLAT_OPEN / REDUCE_RESIDUAL and the check sits behind
        #: klass == INCREASE_RESIDUAL. Registered in
        #: analysis/pmus/NEXT_PROFIT_POLICY_PREREG.md; L5_NATIVE is untouched.
        self.residual_cap_k = (None if residual_cap_k is None
                               else D(str(residual_cap_k)))
        self._now = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def _age_s(self, row: Mapping[str, Any]) -> Optional[float]:
        try:
            t = dt.datetime.fromisoformat(str(row["receipt_utc"]))
        except Exception:                                      # noqa: BLE001
            return None
        return (self._now() - t).total_seconds()

    # ---- the book, in the shape ValidationGates reads ----------------
    # Two adapters, not a change to either side: the store speaks in
    # per-market committed notional and live order rows, the gates speak in
    # legs and outstanding obligations. Both are ACCOUNT-WIDE on purpose --
    # `max_cash_deployed_usd` is a run-scoped total, not a per-market one,
    # and this account is SHARED, so exposure adopted from the venue counts
    # too. Counting it makes the gate refuse EARLIER; excluding it would let
    # an automated dollar sit on top of exposure we did not create.

    def _still_fillable_or_at_limit(self) -> int:
        """The still-fillable count, or a value that is certainly at the
        limit. Never raises into the order path and never reads as zero."""
        try:
            return int(self.store.still_fillable_order_count())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # A number we could not establish must refuse. `max_live_orders`
            # is a small positive integer, so any large value is at or past
            # it whatever it is configured to.
            return AT_LIMIT_WHEN_UNCOUNTABLE

    def unrealized_loss_for_gates(self) -> Optional[D]:
        """Conservative open-book loss, account-wide, or None if unknowable.

        `risk_exposure_usd` is already the figure the run-scoped ceiling
        needs: the worst cash loss in a market over BOTH settlements, with
        every unfilled order priced as though it fills in full and an
        unreadable price charged at a dollar a share. Summing it over every
        market still carrying exposure gives the most the open book can lose
        before anything settles.

        None, NEVER ZERO, when it cannot be computed. An incoherent book
        makes `risk_exposure_usd` raise by design, and a run-scoped loss
        ceiling fed a zero in that state would be reporting safety it has
        not established. The gate turns None into a refusal.
        """
        total = D(0)
        try:
            markets = self.store.markets_with_exposure()
        except BaseException:                                  # noqa: BLE001
            return None
        for m in markets:
            try:
                total += self.store.risk_exposure_usd(m)
            except BaseException:                              # noqa: BLE001
                return None            # incoherent or unreadable: fail closed
        return total

    def probe_plan(self, market: str,
                   authenticated: Mapping[str, Any],
                   loss_ceiling: Optional[D] = None) -> Dict[str, Any]:
        """Both probe legs, priced once from the authenticated book."""
        return TS.build_orders(
            market=market,
            bid=authenticated.get("bid"), ask=authenticated.get("ask"),
            bid_depth=authenticated.get("bid_shares"),
            ask_depth=authenticated.get("ask_shares"),
            tick=getattr(self.transport, "tick_size", None),
            min_qty=getattr(self.transport, "quantity_increment", None),
            depth_ticks=int(os.getenv("L5_TS_DEPTH_TICKS", "2")),
            cash_envelope=self.probe_cash_envelope(),
            max_worst_case_loss=(self.probe_loss_ceiling()
                                 if loss_ceiling is None else loss_ceiling),
            book_age_s=authenticated.get("stream_age_s"),
            gap_suspected=bool(authenticated.get("gap_suspected")))

    def probe_cash_envelope(self) -> Optional[D]:
        vg = self.validation_gates
        return None if vg is None else vg.max_cash_deployed_usd

    def probe_loss_ceiling(self) -> Optional[D]:
        vg = self.validation_gates
        if vg is None:
            return None
        # the DEFICIT bound is the per-window worst case; the cumulative
        # ceiling is run-scoped. The smaller one binds the sizing.
        d = vg.max_worst_case_deficit_usd
        c = vg.max_cumulative_loss_usd
        return c if d is None else min(d, c)

    def probe_plan_key(self, market: str) -> str:
        from src import l5_live_authorization as AUTH
        return "%s|%s" % (AUTH.experiment_id(), market)

    def probe_stored_plan(self, market: str) -> Optional[Dict[str, Any]]:
        """The frozen plan for this experiment and market, or None."""
        p = (self.store.state.get("probe_plans") or {}).get(
            self.probe_plan_key(market))
        return dict(p) if isinstance(p, Mapping) else None

    def probe_store_plan(self, market: str, plan: Mapping[str, Any]) -> None:
        """Freeze it durably, so leg two matches leg one across a restart."""
        plans = self.store.state.setdefault("probe_plans", {})
        plans[self.probe_plan_key(market)] = dict(plan)
        self.store.save()

    def probe_legs_claimed(self) -> Dict[str, bool]:
        """Which legs this authorization has already spent, from DISK.

        The claim file is the restart record: its presence means the leg was
        submitted, so a restarted process cannot re-place a leg whatever its
        in-memory state says.
        """
        from src import l5_live_authorization as AUTH
        out = {}
        for leg in AUTH.PROBE_LEGS:
            try:
                out[leg] = AUTH.claim_held(
                    AUTH.probe_leg_sid(AUTH.experiment_id(), leg))
            except BaseException:                              # noqa: BLE001
                out[leg] = True        # unreadable = treat as spent
        return out

    def probe_precheck(self, market: str, plan: Mapping[str, Any]
                       ) -> Optional[str]:
        """Gate the WHOLE PAIR before either leg is claimed.

        ITEM 5, AND IT IS THE REASON THIS IS NOT TWO INDEPENDENT ORDERS. If
        leg one were gated alone it could spend the cash and the deficit
        headroom the second leg needs, leaving a single unpaired order that
        the gates would then refuse to complete -- the exact state the probe
        exists to avoid. So both legs are priced as if BOTH were already
        resting, and if that combined book fails any gate NEITHER is placed.
        """
        vg = self.validation_gates
        if vg is None:
            return None
        legs = list(plan.get("legs") or ())
        if len(legs) != 2:
            return "PROBE_PLAN_NOT_TWO_LEGS"
        both = [{"qty": D(l["qty"]), "price": D(l["price"]),
                 "intent": ("ORDER_INTENT_BUY_SHORT" if l["side"] == DOWN
                            else "ORDER_INTENT_BUY_LONG")}
                for l in legs]
        inv = self.inventory_for_gates(market)
        unreal = self.unrealized_loss_for_gates()
        for i, leg in enumerate(legs):
            # each leg evaluated with the OTHER already outstanding
            other = [both[1 - i]]
            reason = vg.refuse(
                order_qty=D(leg["qty"]), order_price=D(leg["price"]),
                order_intent=both[i]["intent"],
                filled_legs=self.cash_legs_for_gates(),
                outstanding=self.outstanding_for_gates() + other,
                book_uncertain=self.store.orders_uncertain(),
                unmatched_after=self.store.residual(market),
                cumulative_realized=self.store.realized_today(),
                verified_strategy_net=self.store.verified_l5_net_for_gates(),
                l5_scoped_realized=self.store.l5_realized_since_baseline(),
                cumulative_unrealized_loss=unreal,
                available_cash_usd=self.available_cash_usd,
                inventory=inv)
            if reason:
                return "PAIR_%s:%s" % (leg["side"], reason)
        return None

    def inventory_for_gates(self, market: str) -> Dict[str, Any]:
        """Confirmed per-side quantity and ACTUAL acquisition cost.

        Both come from the ledger the venue has reconciled, never from a
        quote: `leg_cost` is what fills actually charged us, and a leg whose
        basis was adopted rather than attested already carries the $1-a-share
        worst case, which is the conservative direction. A side present with
        no cost is passed through as-is so the gate refuses it as an
        incoherent book instead of reading it as free inventory.
        """
        lc = self.store.leg_cost(market)
        return {"market": market,
                "up_qty": self.store.position(market, UP),
                "dn_qty": self.store.position(market, DOWN),
                "up_cost": lc.get("UP", D(0)),
                "dn_cost": lc.get("DOWN", D(0))}

    def cash_legs_for_gates(self) -> List[Dict[str, Any]]:
        """Filled notional as unreleased cash, one leg per market.

        `settled` is False for every leg, which is the conservative reading
        and the one NO_RECYCLING already encodes: cash is released by a
        confirmed settlement, and this executor's ledger does not mark one
        here. Reading it as settled would hand the gate headroom it has no
        evidence for.
        """
        legs: List[Dict[str, Any]] = []
        for mkt, usd in sorted(self.store.committed().items()):
            legs.append({"market": mkt, "cash_out": usd, "settled": False,
                         "closing": False})
        return legs

    def outstanding_for_gates(self) -> List[Dict[str, Any]]:
        """Every live order's still-fillable obligation, account-wide.

        A price we cannot read is priced at the worst it could be, exactly as
        `risk_exposure_usd` does: a resting order read as free is how an
        envelope gets spent without the gate seeing it. An unreadable side is
        charged as a short, whose worst case is $1 a share.
        """
        out: List[Dict[str, Any]] = []
        for coid, o in sorted(self.store.live_orders().items()):
            rem = venue_decimal(o.get("remaining")) or D(0)
            if rem <= 0:
                continue
            px = venue_decimal(o.get("price"))
            if px is None:
                px = UNKNOWN_PRICE_WORST_CASE
            try:
                is_up = canonical_side(
                    o.get("side"), doing="pricing an outstanding order") == UP
            except UnknownSide:
                is_up = False
            out.append({"coid": coid, "qty": rem, "price": px,
                        "intent": ("ORDER_INTENT_BUY_LONG" if is_up
                                   else "ORDER_INTENT_BUY_SHORT")})
        return out

    def handle(self, row: Mapping[str, Any],
               authenticated: Mapping[str, Any],
               *, market: str, window: str,
               open_markets: Sequence[str] = ()) -> Dict[str, Any]:
        key = str(row.get("action_key"))
        dec = FT.l5_native(row)
        prov = Provenance(key, dec or {"unreadable": True}, market, window)

        # INDEPENDENT HALT, read FIRST. Before translation, before the book,
        # before any other gate: a halt must not depend on the rest of this
        # method working. Absent, stale or unreachable all refuse, because a
        # gate that fails open is not a gate.
        hg = self.halt_gate
        if hg is not None:
            detail = None
            try:
                permitted = hg.submissions_permitted()
                hreason = hg.refuse_reason()
                # refuse_reason() is the coarse classification
                # (halt_asserted / halt_record_stale / ...). The guard's own
                # reason lives in the record, and an operator needs it: a
                # log saying only "halt_asserted" does not say what the
                # watchdog saw.
                try:
                    rec_ = (hg.state() or {}).get("record") or {}
                    detail = rec_.get("reason") or None
                except Exception:                          # noqa: BLE001
                    detail = None
            except Exception as exc:                       # noqa: BLE001
                permitted = False
                hreason = "HALT_GATE_UNREADABLE:%s" % type(exc).__name__
            prov.stage("HALT_GATE", {"submissions_permitted": permitted,
                                     "reason": hreason,
                                     "guard_reason": detail})
            if not permitted:
                return prov.refuse(
                    "HALT_GATE",
                    "HALT:%s" % (("%s:%s" % (hreason, detail)) if detail
                                 else hreason))

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
        # THE TWO-SIDED PROBE REPLACES THE ORDER BEFORE *EVERY* GATE.
        #
        # THE BUGS THIS POSITION FIXES. Two, found by audit.
        #
        # (a) Placed after the validation gates, the probe's order would never
        #     be gated at all: the gates would evaluate the ordinary lambda
        #     intent while the probe's own side, price and quantity reached
        #     the venue ungated.
        # (b) Placed after `maker_only` and `suppress_imbalance_increase`,
        #     those two would judge the SUPERSEDED L5 action. maker_only reads
        #     tr["mode"], so an L5 action that translated to TAKER refused the
        #     probe outright even though both probe legs are always MAKER; and
        #     suppress_imbalance_increase classifies on dec["side"], so it
        #     could reject a probe leg for an imbalance the probe does not
        #     create. Both now run on the probe's own order.
        #
        # So this sits upstream of EVERY gate -- maker_only, the imbalance
        # suppressor, the validation gates, the residual cap -- and the intent
        # construction, and each of them sees exactly what will be sent.
        #
        # Both legs are priced and gated TOGETHER, then placed on successive
        # decision ticks through this same path: the existing claim,
        # record_submit, transport, durable ledger, reconciliation and
        # per-order cancellation. Nothing new carries money.
        #
        # NO FALLBACK. A refusal anywhere here is terminal: falling back to
        # `tr` would submit an ordinary L5 trade under an authorization
        # granted for a two-sided probe.
        # THE SUPERSEDED L5 ACTION, captured BEFORE any override mutates it.
        # Reading dec["side"] afterwards records the override as the thing it
        # replaced, which destroys the provenance the record exists for.
        superseded_l5 = {"side": str(dec.get("side")),
                         "price": str(tr.get("price")),
                         "qty": str(tr.get("qty")),
                         "mode": str(tr.get("mode") or "")}

        # MUTUALLY EXCLUSIVE EXPERIMENTS, FAILING CLOSED. Both overrides
        # rewrite the order that reaches the venue. If both were authorized at
        # once the later one would silently win, and the gates would have
        # validated the other one. There is no safe precedence here, so both
        # being named is a configuration error and nothing is submitted.
        if TS.active(market) and QM.active(market):
            return prov.refuse("SAFETY_OVERLAY",
                               "CONFLICTING_EXPERIMENTS_TS_AND_QM")

        if TS.active(market):
            # THE PLACEMENT CUTOFF, ACTUALLY ENFORCED. may_place() existed
            # and had no call site; an audit is right that a constant nobody
            # calls is not a deadline. A leg placed too late cannot be
            # completed OR withdrawn in time, so past the cutoff nothing new
            # is placed -- and because the cutoff is derived from the WINDOW,
            # a late start or a restart cannot extend it.
            ws = None
            try:
                from src import l5_pmus_auth_book as _AB
                ws = _AB.slug_start(market)
            except BaseException:                              # noqa: BLE001
                ws = None
            if ws is None:
                return prov.refuse("TWO_SIDED_PROBE", "WINDOW_START_UNKNOWN")
            late = TS.may_place(float(self._now().timestamp()),
                                float(ws), float(ws) + TS.WINDOW_S)
            if late:
                return prov.refuse("TWO_SIDED_PROBE", late)

            # SIZE DOWN UNTIL THE GATES ACCEPT THE PAIR. The module sizes
            # from the cash and loss inputs it is given, but the gates own
            # the final word -- the deficit gate adds the other leg's cost
            # and its rounding slack, which the module does not model. So
            # the ceiling is walked down until the gated pair fits, and the
            # gate stays authoritative instead of being second-guessed.
            # THE PLAN IS FROZEN ONCE AND PERSISTED. Re-planning on each
            # tick produced legs of DIFFERENT sizes as the book moved -- 1.89
            # against 0.98 in test -- which is not a matched pair at all. The
            # first tick fixes both prices and the common quantity, the
            # second reuses them, and the store makes that survive a restart.
            plan = self.probe_stored_plan(market)
            bad = None
            if plan is None:
                bad = "PROBE_NOT_SIZED"
                ceiling = self.probe_loss_ceiling()
                last = None
                for _ in range(12):
                    plan = self.probe_plan(market, authenticated,
                                           loss_ceiling=ceiling)
                    if not plan.get("ok"):
                        # a gate reason already seen outranks the module's
                        # downstream complaint about the shrunken size
                        return prov.refuse(
                            "TWO_SIDED_PROBE",
                            str(last or plan.get("reason"))[:120])
                    bad = self.probe_precheck(market, plan)
                    if not bad:
                        break
                    last = bad
                    if ceiling is None:
                        break
                    ceiling = ceiling * D("0.85")
                if not bad:
                    self.probe_store_plan(market, plan)
            if bad:
                prov.stage("TWO_SIDED_PROBE", {
                    "refused": bad, "pair_cost": plan.get("pair_cost"),
                    "labels": ("the PAIR was gated as if BOTH legs were "
                               "already resting, so neither is placed. Leg "
                               "one can never spend leg two's headroom.")})
                return prov.refuse("TWO_SIDED_PROBE", bad[:120])
            claimed = self.probe_legs_claimed()
            legs = {l["side"]: l for l in plan["legs"]}
            # deterministic: the CHEAPER leg first, because a cheap leg
            # filling alone is the smaller worst case.
            order = sorted(legs, key=lambda k: D(legs[k]["price"]))
            nxt = next((k for k in order if not claimed.get(k)), None)
            if nxt is None:
                return prov.refuse("TWO_SIDED_PROBE", "BOTH_LEGS_SPENT")
            leg = legs[nxt]
            # rewrite BOTH the decision and the translation: downstream code
            # reads dec["side"] for the order intent and the residual
            # direction, and tr for price and quantity.
            dec["side"] = leg["side"]
            # `executable` IS RE-STAMPED ONTO tr FURTHER DOWN
            # (tr = dict(tr, qty=executable)), so overriding tr alone is not
            # enough: the lambda quantity would come back and the override
            # would silently become a lambda-sized order. Found by the
            # money-field integrity check. Both are set here.
            executable = D(leg["qty"])
            tr = dict(tr, ok=True, mode=FT.MAKER,
                      price=D(leg["price"]), qty=executable)
            prov.stage("TWO_SIDED_PROBE", dict(
                plan, leg_to_place=nxt, legs_claimed=claimed,
                replaced_l5_intent=dict(superseded_l5)))
            self._probe_leg_pending = nxt

        if QM.active(market):
            built = QM.build_order(
                market=market,
                bid=authenticated.get("bid"), ask=authenticated.get("ask"),
                bid_depth=authenticated.get("bid_shares"),
                tick=getattr(self.transport, "tick_size", None),
                min_qty=getattr(self.transport, "quantity_increment", None),
                qty_increment=getattr(self.transport, "quantity_increment",
                                      None),
                book_age_s=authenticated.get("stream_age_s"),
                gap_suspected=bool(authenticated.get("gap_suspected")))
            if not built.get("ok"):
                prov.stage("ORDER", {
                    "submitted": False,
                    "reason": "QUEUE_MEASUREMENT",
                    "detail": str(built.get("reason"))[:120],
                    "labels": ("the authorized queue measurement could not "
                               "be constructed from this book, so NOTHING is "
                               "submitted. There is deliberately no fallback "
                               "to lambda-based L5 sizing.")})
                return prov.refuse("QUEUE_MEASUREMENT",
                                   str(built.get("reason"))[:120])
            mi = built["intent"]
            # MODE IS WHAT REACHES THE WIRE. l5_order_body.from_intent
            # derives participateDontInitiate from `mode == "MAKER"` and
            # ignores `post_only` entirely, and the maker_only overlay
            # compares against FT.MAKER as well -- so the descriptive string
            # that used to sit here would have been sent NOT post-only, and
            # refused MAKER_ONLY_NO_TAKER on the way out. The measurement's
            # own label is preserved as `qm_mode` for the record.
            # REWRITE THE DECISION, NOT A LATE INTENT. Built after the
            # gates, this block mutated a money-moving field behind them --
            # the gates validated the lambda order and the venue received
            # the measurement. Found by the intent-integrity check below.
            # Now it rewrites dec and tr upstream, so maker_only, the
            # imbalance suppressor, the validation gates and the residual
            # cap all evaluate the measurement itself.
            dec["side"] = mi["side"]
            # see the two-sided note above: `executable` is re-stamped onto
            # tr further down, so it must be overridden too.
            executable = D(str(mi["qty"]))
            tr = dict(tr, ok=True, mode=FT.MAKER,
                      price=D(str(mi["price"])), qty=executable)
            self._qm_mode = mi["mode"]
            prov.stage("QUEUE_MEASUREMENT",
                       dict(mi, replaced_l5_intent=dict(superseded_l5)))

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
            "residual_cap_k": (None if self.residual_cap_k is None
                               else str(self.residual_cap_k)),
            "basis": "REALIZED residual; outstanding orders are not counted"})
        if self.suppress_imbalance_increase and klass == INCREASE_RESIDUAL:
            return prov.refuse("SAFETY_OVERLAY", "IMBALANCE_INCREASE_SUPPRESSED")

        # RUN-SCOPED VALIDATION GATES, additive and refuse-only. They are
        # consulted AFTER every pre-existing limit, so a deployed refusal is
        # never masked by one of these, and they can only ever turn an
        # allowed action into a refused one -- never the reverse.
        vg = self.validation_gates
        if vg is not None:
            try:
                # THE BOOK IS PASSED IN, and that is the whole point of the
                # cash gate. This call used to omit both `filled_legs` and
                # `outstanding`, which default to `()`, so the committed
                # figure was `cash_committed(()) + pending_obligation(()) +
                # this_order` -- just the proposed order's notional.
                # `max_cash_deployed_usd` therefore bounded a SINGLE CLIP and
                # not the account: a $1 ceiling permitted an unbounded series
                # of $0.95 orders, each passing on its own. The $5
                # SafetyLimits envelope still bound the total, so this was
                # never unbounded exposure -- but it was not the $1 the
                # variable claims, and L5_MAX_CASH_DEPLOYED_USD is set on
                # this service.
                #
                # AND IT FAILS CLOSED ON AN UNSETTLED BOOK. The cash figure
                # is computed FROM the ledger, so a ledger the venue has not
                # confirmed produces a number with no authority behind it.
                # `orders_uncertain` already refuses one layer down, in
                # SafetyLimits -- but that is AFTER this gate has already
                # computed and passed a cash total from the same untrusted
                # book, and a gate that approves on bad data and is saved by
                # the next one is not the gate it looks like.
                vreason = vg.refuse(
                    order_qty=tr["qty"], order_price=tr["price"],
                    order_intent=("ORDER_INTENT_BUY_SHORT"
                                  if str(dec.get("side")).upper() == "DOWN"
                                  else "ORDER_INTENT_BUY_LONG"),
                    filled_legs=self.cash_legs_for_gates(),
                    outstanding=self.outstanding_for_gates(),
                    book_uncertain=self.store.orders_uncertain(),
                    unmatched_after=self.store.residual(market),
                    cumulative_realized=self.store.realized_today(),
                    verified_strategy_net=self.store.verified_l5_net_for_gates(),
                l5_scoped_realized=self.store.l5_realized_since_baseline(),
                    cumulative_unrealized_loss=(
                        self.unrealized_loss_for_gates()),
                    available_cash_usd=self.available_cash_usd,
                    inventory=self.inventory_for_gates(market))
            except Exception as exc:                       # noqa: BLE001
                # a gate that cannot evaluate refuses. Failing open here
                # would silently remove the protection it exists to be.
                vreason = "VALIDATION_GATE_ERROR:%s" % type(exc).__name__
            if vreason:
                prov.stage("VALIDATION_GATES", {"refused": vreason})
                return prov.refuse("VALIDATION_GATES", vreason)

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
            # THE COUNT THAT BINDS THE ONE-ORDER LIMIT, and it fails closed.
            # `live_order_count` counts rows; this counts orders that could
            # still take money, including a cancel we could not verify and
            # every parked or quarantined order. A count we cannot compute is
            # reported as at the limit rather than as zero: the alternative
            # is a limit that disappears exactly when the book is confusing.
            live_order_count=self._still_fillable_or_at_limit(),
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

        # RESIDUAL_CAP, LAST. Behind every frozen check, so a
        # RESIDUAL_ABOVE_CAP refusal is only ever an action the frozen
        # executor WOULD have sent: the bind count is exactly what this
        # policy cost us, and every pre-existing refusal reason still reads
        # as it did. It is EVALUATED on every action that gets this far --
        # the guard that used to sit here (klass == INCREASE_RESIDUAL, from
        # realized inventory) is what let the 1130z burst through, so the
        # exemptions are now decided below from the prospective book instead
        # of assumed from the realized one. The note is emitted either way,
        # so the treatment is read off the record rather than inferred from
        # what happened next.
        if self.residual_cap_k is not None:
            # PROSPECTIVE NET. The gate reads realized inventory PLUS every
            # live order in this market as if it filled, because on 1130z it
            # did not and inventory reached 3.24 against a cap of 1.80. Four
            # DOWN orders accepted while realized residual sat inside the cap
            # filled inside seven seconds, two of them after resting three
            # minutes. Realized residual is what we HAVE; prospective is what
            # we have ALREADY COMMITTED TO, and the second is the one a bound
            # has to be written against.
            outstanding = self.store.outstanding_residual(market)
            prosp_before = resid_before + outstanding
            signed = tr["qty"] if dec["side"] == "UP" else -tr["qty"]
            prosp_after = prosp_before + signed
            b_abs = -prosp_before if prosp_before < 0 else prosp_before
            a_abs = -prosp_after if prosp_after < 0 else prosp_after
            # TWO EXEMPTIONS, BOTH DECIDED ON THE PROSPECTIVE BOOK.
            # A genuine opening leg -- nothing held AND nothing resting -- is
            # never capped, so the first order into a market cannot be refused
            # by a bound derived from its own size. What cannot happen any
            # more is four of them in a row each calling itself the first.
            exempt_open = b_abs == 0
            # A leg that shrinks |prospective| is never refused, at any depth,
            # whatever is outstanding. That is the repair asymmetry, and it is
            # unchanged except that it now reads the prospective book too.
            increases = a_abs > b_abs
            clip = self.store.max_clip(market)
            if tr["qty"] > clip:
                clip = tr["qty"]
            cap = self.residual_cap_k * clip
            prov.note("RESIDUAL_CAP", {
                "k": str(self.residual_cap_k),
                "basis": "PROSPECTIVE_NET",
                "max_clip_filled": str(self.store.max_clip(market)),
                "clip_used": str(clip),
                "clip_basis": "max(largest filled clip, this order's qty)",
                "cap_contracts": str(cap),
                "residual_realized": str(resid_before),
                "outstanding_residual": str(outstanding),
                "prospective_before": str(prosp_before),
                "prospective_after_if_filled": str(prosp_after),
                "abs_prospective_before": str(b_abs),
                "abs_prospective_after": str(a_abs),
                "increases_abs_prospective": bool(increases),
                "prospective_book_flat": bool(exempt_open),
                "inventory_class_realized": klass,
                "binds": bool(not exempt_open and increases and a_abs > cap),
                "comparison": ("refuse iff the prospective book is NOT flat "
                               "AND this increases |prospective| AND "
                               "|prospective after| > cap; equality passes"),
                "units": "contracts",
            })
            if (not exempt_open) and increases and a_abs > cap:
                return prov.refuse("SAFETY_OVERLAY", "RESIDUAL_ABOVE_CAP")

        coid = client_order_id(key)
        if self.store.already_submitted(coid):
            prov.stage("ORDER", {"client_order_id": coid,
                                 "submitted": False,
                                 "reason": "DUPLICATE_SUPPRESSED"})
            return prov.rec

        intent = {"client_order_id": coid, "market": market,
                  "side": dec["side"], "price": tr["price"], "qty": tr["qty"],
                  "mode": tr["mode"], "action_key": key}

        # THE QUEUE MEASUREMENT REPLACES THE INTENT ENTIRELY.
        #
        # THE GAP THIS CLOSES. `build_order` existed and was tested, but
        # nothing called it: when an experiment was active this path still
        # submitted the ordinary lambda-sized L5 decision, which is a
        # behavioural order and not the measurement. Implementing the
        # constructor is not the same as wiring it, and only the wiring makes
        # the authorized experiment the thing that actually reaches the venue.
        #
        # IT REPLACES, NEVER ADJUSTS. Side, price, quantity and mode all come
        # from the measurement; a refusal is terminal for this decision. There
        # is no fallback to `tr`, because falling back would submit a trade
        # under an authorization granted for a measurement.
        # WHAT THE GATES VALIDATED, in the only fields that move money. Any
        # later override must not change these behind the gates' back; the
        # equality check immediately before the wire enforces that.
        gated_money = {"market": str(market),
                       "side": str(dec.get("side")),
                       "price": str(tr.get("price")),
                       "qty": str(tr.get("qty")),
                       "mode": str(tr.get("mode") or "")}

        # THE ONE-SUBMISSION CLAIM, and it is the LAST gate before the wire.
        #
        # ONLY WHEN ARMED. A disarmed or shadow-locked transport cannot reach
        # the venue -- `submit` raises NotArmed -- so a claim taken on that
        # path would burn the owner's authorization on an order that never
        # existed, and in shadow mode would burn it on the first decision of
        # the day. The claim is therefore taken exactly where a submission
        # CAN reach the venue and nowhere else.
        #
        # BEFORE THE WIRE, DELIBERATELY. The claim is fsynced before
        # `transport.submit` is called, so a process that dies between the
        # two has already spent it. That is the conservative direction: the
        # order may be live at the venue, and a second attempt is exactly
        # what must not happen.
        #
        # WHAT THIS CATCHES THAT NOTHING ELSE DID. `max_live_orders` bounds
        # CONCURRENCY, so a cancellation returns the count to zero and lets
        # another order through; the cash gate hands back the unfilled
        # headroom after a partial fill; and the ARMING claim resets per
        # window, so a restart can re-arm the same window. None of those
        # bounds submissions per AUTHORIZATION. This does.
        if getattr(self.transport, "armed", False):
            from src import l5_live_authorization as AUTH
            payload = {
                "client_order_id": coid, "action_key": key,
                "side": str(dec["side"]), "price": str(tr["price"]),
                "qty": str(tr["qty"]), "mode": str(tr.get("mode") or "")}
            try:
                # THE PROBE NEEDS EXACTLY TWO SUBMISSIONS, so it claims a
                # NAMED LEG rather than loosening the single-submission rule
                # for everything else. Each leg is still one-shot and
                # durable, and max_live_orders still bounds concurrency.
                leg_pending = getattr(self, "_probe_leg_pending", None)
                if TS.active(market) and leg_pending:
                    claim = AUTH.claim_probe_leg(
                        leg_pending, dict(payload, market=market))
                else:
                    claim = AUTH.claim_submission(market, payload)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                args = getattr(exc, "args", None) or ("SUBMISSION_CLAIM",)
                why = str(args[0])[:120]
                prov.stage("ORDER", {
                    "client_order_id": coid, "submitted": False,
                    "reason": "SUBMISSION_CLAIM", "detail": why,
                    "labels": ("the one-submission claim for this experiment "
                               "and market was refused. A spent claim is "
                               "NEVER reset automatically -- not by a "
                               "cancellation, a partial or zero fill, a "
                               "reconnect, a restart, or a rejected or "
                               "ambiguous acknowledgement. Clearing it is an "
                               "owner action with the credentials.")})
                return prov.refuse("SUBMISSION_CLAIM", why)
            prov.stage("SUBMISSION_CLAIM", dict(claim, consumed=True))

        # Reserve the envelope BEFORE the wire. If the process dies between
        # the send and the ack, the order may still be live at the venue, and
        # a store that never recorded it would hand the whole envelope to the
        # next decision.
        self.store.record_submit(
            coid, intent,
            session_id=os.getenv("PMUS_Q1_SESSION_ID", "").strip() or None,
            mode=str(tr.get("mode") or ""))
        try:
            # BYTE-FOR-BYTE ON THE MONEY FIELDS. The queue-measurement
            # override runs after the gates, so without this an unvalidated
            # order could reach the venue. Rather than trust the ordering,
            # the intent about to be sent is compared with what the gates
            # saw, and a mismatch refuses.
            sent_money = {"market": str(intent.get("market")),
                          "side": str(intent.get("side")),
                          "price": str(intent.get("price")),
                          "qty": str(intent.get("qty")),
                          "mode": str(intent.get("mode") or "")}
            if sent_money != gated_money:
                prov.stage("INTENT_INTEGRITY", {
                    "gated": gated_money, "to_send": sent_money,
                    "why": ("an override changed a money-moving field after "
                            "the safety gates ran, so the gates validated an "
                            "order that is not this one")})
                self.store.mark_uncertain("intent mutated after the gates")
                return prov.refuse("SAFETY_OVERLAY",
                                   "INTENT_MUTATED_AFTER_GATES")
            self._probe_leg_pending = None
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

        vid = (ack.get("order_id") or ack.get("orderId") or ack.get("id"))
        self.store.attach_venue_id(coid, vid)
        # AN ACK THAT DOES NOT NAME THE ORDER IS NOT AN ACK WE CAN ACT ON.
        #
        # `attach_venue_id` silently does nothing when the id is falsy, so an
        # order could be recorded LIVE with no venue id -- and a cancel has
        # nothing to name. `deadman` would catch it as `no_venue_id` on a
        # later sweep, but for the measurement that is too late and too
        # indirect: the deadline is evaluated against a named order. So the
        # book is marked UNCERTAIN here, at the moment the ambiguity appears,
        # which refuses every further submission until the venue answers
        # authoritatively. Never assume the order does not exist.
        if not str(vid or "").strip():
            self.store.mark_uncertain(
                "ack without a venue order id for %s" % coid)
            prov.stage("ORDER", {
                "client_order_id": coid, "submitted": True,
                "venue_order_id": None, "reason": "ACK_WITHOUT_VENUE_ID",
                "labels": ("the venue acknowledged but did not name the "
                           "order, so it may be resting and we cannot cancel "
                           "it by id. The book is UNCERTAIN and reconciles "
                           "against the venue; nothing further is submitted "
                           "and the order is NOT assumed cancelled.")})
            return prov.refuse("SAFETY_OVERLAY", "ACK_WITHOUT_VENUE_ID")
        if QM.active(market):
            # DURABLE BEFORE THE DEADLINE IS USED. The ack time and the exact
            # venue id are fsynced to the volume now, so a restart recovers
            # the deadline instead of guessing it.
            try:
                QM.record_ack_for(
                    self.store, market=market, venue_order_id=vid,
                    t_ack=_ack_epoch(ack), price=str(intent["price"]),
                    qty=str(intent["qty"]))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.store.mark_uncertain(
                    "measurement ack not persisted: %s" % type(exc).__name__)
                prov.stage("ORDER", {
                    "client_order_id": coid, "submitted": True,
                    "venue_order_id": str(vid),
                    "reason": "ACK_NOT_PERSISTED",
                    "detail": str(exc)[:120],
                    "labels": ("the order is live but its deadline record "
                               "could not be persisted, so a restart could "
                               "not time it. UNCERTAIN and reconciling.")})
                return prov.refuse("SAFETY_OVERLAY", "ACK_NOT_PERSISTED")
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
    """Cancel our RESTING orders by VENUE id, then prove it by reading them.

    THREE DEFECTS THIS REPLACES, all in the four lines it used to be, none of
    which would have shown up until a real order was resting:

      1. it iterated `state["submitted"]` -- the permanent, never-pruned
         submit log -- so every order this ledger has ever sent would be
         cancelled again at every window end, forever;
      2. it passed the CLIENT order id to `transport.cancel`, whose own
         docstring says the venue cancels by its own id and "passing the
         latter would silently cancel nothing";
      3. it appended to `cancelled` on any non-raising return, so the record
         claimed a cancellation the venue had never been asked for.

    A CANCEL ACK IS NOT PROOF, so the ack decides nothing here. Every id is
    re-read with `GET /v1/order/{id}` afterwards and classified by the
    VENUE's own state. A cancel that raised but whose re-read comes back
    terminal is cancelled; a cancel that returned 200 but whose re-read is
    still working is `still_open`, which is the dangerous case and is never
    reported as success.

    FILLS FOUND WHILE CANCELLING ARE BOOKED, not discarded. The re-read goes
    through `order_progress` into `apply_order_progress`, which books the
    delta exactly once, so a partial filled between the decision and the
    cancel arrives with its cost basis instead of becoming another
    QUANTITY_WITHOUT_COST. That is the failure mode that cost this programme
    the 1900z share.

    FAIL CLOSED, ALWAYS. An order with no venue id cannot be cancelled by
    this route at all; a re-read that raises leaves the order's fate unknown;
    a re-read that says the order is still working means the cancel did not
    take. All three mark the store UNCERTAIN, which is what stops the next
    submission -- `SafetyLimits.refuse()` returns ORDER_STATE_UNCERTAIN
    before every other check. Clearing it needs an authoritative read that
    agrees, which is the point.

    Safe when nothing is armed: with no live orders it reads nothing, sends
    nothing and returns empty lists.
    """
    out: Dict[str, Any] = {
        "reason": reason, "cancelled": [], "errors": [], "unverified": [],
        "no_venue_id": [], "still_open": [], "filled": [],
        "labels": ("cancelled means the VENUE reported a terminal state on "
                   "re-read, never that a cancel call returned. still_open "
                   "and unverified are failures and mark the book uncertain."),
    }
    # a snapshot: apply_order_progress mutates live_orders underneath us
    for coid, row in sorted(store.live_orders().items()):
        vid = str(row.get("venue_order_id") or "").strip()
        market = str(row.get("market") or getattr(transport, "market_slug", "")
                     or "")
        side = str(row.get("side") or "")
        if not vid:
            # Never submitted-and-ACKed, or the ACK carried no id. There is
            # nothing to name in a cancel, and if it did rest and fill the
            # shares arrive without a cost basis -- so this is uncertainty,
            # not a no-op.
            out["no_venue_id"].append({"client_order_id": coid,
                                       "market": market, "side": side})
            store.mark_uncertain(
                "deadman could not cancel %s: no venue order id" % coid)
            continue
        ack_error = None
        try:
            transport.cancel(vid)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # NOT a failure yet: a cancel can raise on an order the venue has
            # already retired. The re-read decides.
            ack_error = type(exc).__name__
        try:
            prog = order_progress(transport.order(vid))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["unverified"].append({"client_order_id": coid,
                                      "venue_order_id": vid,
                                      "market": market,
                                      "cancel_error": ack_error,
                                      "verify_error": type(exc).__name__})
            store.mark_uncertain(
                "deadman could not verify the cancel of %s (%s)"
                % (vid, type(exc).__name__))
            continue
        res = store.apply_order_progress(coid, prog, market=market, side=side)
        booked = D(str(res.get("filled_delta") or res.get("delta") or 0))
        if booked > 0:
            out["filled"].append({"client_order_id": coid,
                                  "venue_order_id": vid, "market": market,
                                  "side": side, "qty": str(booked),
                                  "avg_px": str(prog.get("avg_px")),
                                  "commission": str(prog.get("commission")),
                                  "labels": ("filled before the cancel took; "
                                             "booked with its cost basis")})
        rec = {"client_order_id": coid, "venue_order_id": vid,
               "market": market, "side": side,
               "venue_state": prog.get("state"),
               "cum_qty": str(prog.get("cum_qty")),
               "leaves_qty": str(prog.get("leaves_qty")),
               "cancel_error": ack_error}
        if prog.get("terminal"):
            out["cancelled"].append(rec)
        else:
            # The order is STILL WORKING at the venue. This is the one state
            # that must never read as success: an unmanaged resting order is
            # exactly what a deadman exists to prevent.
            out["still_open"].append(rec)
            store.mark_uncertain(
                "deadman cancelled %s but the venue still reports it live"
                % vid)
        if ack_error:
            out["errors"].append({"client_order_id": coid,
                                  "venue_order_id": vid, "error": ack_error,
                                  "resolved_by_reread": bool(
                                      prog.get("terminal"))})
    return out
