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
from typing import (Any, Dict, Iterable, List, Mapping, Optional,
                    Sequence)

from src import pmus_fees as fees
from src import l5_faithful_translation as FT
from src import l5_queue_measurement as QM
from src import l5_defensive_completion as DEF
from src import l5_taker_completion_policy as TCP
from src import l5_two_sided_probe as TS
from src import l5_execution_translations as X
from src import l5_origin as ORIGIN

#: the pipeline, in order. A record names the furthest stage it reached.
STAGES = ("L5_NATIVE", "PMUS_TRANSLATION", "SAFETY_OVERLAY", "ORDER",
          "FILL", "INVENTORY", "SETTLEMENT", "REALIZED")

#: an action older than this at decision time is refused: acting on a stale
#: observation is how a replay after a restart turns into a wrong-price order
STALE_ACTION_MAX_S = 30.0


#: WHETHER A ROUND MAY RETIRE WHILE IT STILL HOLDS AN IMBALANCE.
#:
#: THE DEADLOCK THIS EXISTS TO BREAK, measured on the owner-authorized
#: four-window campaign of 2026-11-02 (2215z/2230z/2245z/2300z). Every
#: window produced EXACTLY ONE venue-confirmed fill and ZERO completed
#: pairs, and the cause is a four-link freeze:
#:
#:   1. plans are stored only for the CURRENT round
#:      (`probe_store_plan(market, plan, rnd)`)
#:   2. `rnd` advances only via `probe_retire_round` + re-read
#:   3. which runs only `if not _blocked`
#:   4. and `probe_round_is_safe_to_retire` returns LEG_HOLDS_UNMATCHED
#:      whenever `up != dn`
#:
#: So one leg filling alone makes `up != dn` permanently -- the complement
#: is refused because the market has moved -- the round never retires,
#: `rnd` never advances, no new plan is ever stored, and the market is dead
#: at round 0 of 8 for the rest of the window. The campaign's own records
#: show `accumulation_round 0`, `accumulation_max_rounds 8`,
#: `retirement_blocked_by LEG_HOLDS_UNMATCHED:DOWN`.
#:
#: WHY LIFTING IT IS SAFE NOW, AND WAS NOT BEFORE. The guard's stated
#: premise is that "the complement is tracked per round, so walking past
#: the round walks past the only thing that would have hedged it." That
#: premise no longer holds: `two_sided_complement` derives the complement
#: from CURRENT inventory and the CURRENT book
#: (`provenance: DERIVED_FROM_CURRENT_BOOK`), and `maybe_book_completion`
#: completes held inventory independently of any round. The residual is
#: therefore still pursued after retirement, and still bounded by the
#: residual cap, the cash envelope, the per-market worst case, the
#: worst-case deficit and both loss halts.
#:
#: WHAT IT DOES NOT TOUCH. `ORDER_STILL_LIVE` and `ORDER_STILL_FILLABLE`
#: both remain: a round that owns a live or still-fillable order can never
#: be walked past, so no order can be orphaned and duplicate-order
#: protection is unchanged. Retirement stays additive and deletes no claim.
#:
#: DEFAULT OFF, so the deployed engine is byte-for-byte unchanged and this
#: can be A/B'd as one testable condition.
#: WHETHER A CONTINUOUS-INVENTORY DECISION MAY CLAIM A SUBMISSION IDENTITY.
#:
#: `l5_continuous_inventory` decides from the current book and the current
#: realized inventory, with no round and no stored plan. Its decisions could
#: not be submitted at all while the claimable identity existed only in the
#: round-scoped form, so the candidate was untestable against real fills.
#: This switch admits its content-addressed key at that one seam.
#:
#: It grants NO allowance of any other kind. Every capital limit, the
#: residual cap, both loss halts, the cash envelope, the order-concurrency
#: ceiling, reconciliation, exact-order cancellation and the watchdog are
#: upstream of the seam and are untouched by it.
#:
#: DEFAULT OFF, so the deployed engine is unchanged, and explicit YES only.
CONTINUOUS_INVENTORY_ENV = "L5_TS_CONTINUOUS_INVENTORY"


def continuous_inventory_enabled() -> bool:
    """Explicit YES only; anything else keeps the round-scoped identity."""
    return (str(os.getenv(CONTINUOUS_INVENTORY_ENV, "")).strip().upper()
            == "YES")


ROUND_RETIRE_RESIDUAL_ENV = "L5_TS_ROUND_RETIRE_WITH_RESIDUAL"


def round_retire_with_residual() -> bool:
    """Explicit YES only; anything else is the historical behaviour."""
    return (str(os.getenv(ROUND_RETIRE_RESIDUAL_ENV, "")).strip().upper()
            == "YES")


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


def _money_or_none(v: Any) -> Optional[D]:
    """A venue money field, preserving the difference between ABSENT and ZERO.

    WHY THIS IS NOT `_money`. `_money` returns D(0) for a field the venue did
    not send, because for a PRICE or a QUANTITY a missing value and a zero
    are both "nothing to book". For a COMMISSION they are opposite facts, and
    conflating them silently destroyed the only positive term in this
    account's economics.

    THE INCIDENT. On the owner-authorized campaign of 2026-11-02 all four
    venue-confirmed maker fills recorded `fee_usd 0.0000` from
    `venue_order_progress.commission`. Three of the four cleared the
    cent-rounding floor (1.92, 2.04 and 2.00 shares against
    REBATE_FLOOR_SHARES 1.62) and the venue's own published formula
    -0.0125*C*p*(1-p) puts a $0.01 maker rebate on each of them. The
    recorded zeros were read as "the venue credited nothing", and that
    reading is what made the strategy look unconditionally unprofitable.

    It was not a venue statement at all. `/v1/order/{id}` carries
    `commissionNotionalTotalCollected`, and when that key is simply absent
    `_money` turns None into D(0) -- so `order_progress` handed the caller a
    hard zero, the caller's own `None`-means-unknown branch became
    unreachable, and an UNMEASURED quantity was durably booked as a measured
    one. The authoritative per-fill credit lives on a different object
    entirely: `/v1/portfolio/activities` -> trade -> the execution that is
    ours by `isAggressor` -> `commissionNotionalCollected` (note: no
    `Total`), which is what `pmus_incentive_audit.credited_on_fills` reads
    and where all 25 of the September credits were found as NEGATIVE
    commissions.

    So: a stated value, including a stated ZERO, is a fact and is returned.
    An absent or unparseable field returns None, which the ledger records as
    unknown rather than as zero. This function never invents a number.
    """
    if isinstance(v, Mapping):
        if "value" not in v:
            return None
        v = v.get("value")
    if v is None:
        return None
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


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
            # ABSENT IS NOT ZERO. `_money` would turn a missing key into
            # D(0) and the caller would durably book an unmeasured rebate as
            # a measured zero -- see `_money_or_none`.
            "commission": _money_or_none(
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


#: ACCUMULATION BOUND. How many UP+DOWN pairs may be worked per market.
#: The superseded design allowed exactly one for the whole authorization.
ACC_MAX_ROUNDS_ENV = "L5_ACC_MAX_ROUNDS"
ACC_MAX_ROUNDS_DEFAULT = 8
#: no configuration may exceed this, whatever the environment says
ACC_MAX_ROUNDS_HARD_CAP = 64

#: A ROUND IS SPENT BY AN ORDER, NOT BY A PROPOSAL (owner decision 2026-11-10).
#:
#: MEASURED, 2026-11-10 2145z, eight rounds permitted. Round 0 was retired at
#: 21:45:10 before any order existed (`LEG_WOULD_CROSS:DOWN`). Rounds 1 and 2
#: traded and matched. Rounds 3 to 7 were then spent in about 100 seconds
#: without one order: each reopening was refused on its economics, its plan
#: stayed frozen, the book moved, the frozen plan stopped being placeable and
#: the round was retired for it. From 21:47:53 the window answered
#: ACCUMULATION_EXHAUSTED 223 times.
#:
#: The freeze exists so leg two matches leg one. A round in which no leg was
#: ever submitted has no leg one. With this set, such a round's stored plan
#: is dropped and the pair is priced again from the current book, in the
#: SAME round, every time it is considered -- so it can neither go stale nor
#: be retired, and the round count advances only past rounds that submitted.
#:
#: Exactly "ON". Anything else is the behaviour every earlier deployment had.
ROUND_ON_SUBMISSION_ENV = "L5_TS_ROUND_CHARGED_ON_SUBMISSION"


def round_charged_on_submission() -> bool:
    return str(os.getenv(ROUND_ON_SUBMISSION_ENV, "")).strip() == "ON"


def client_order_id(action_key: str, attempt: int = 0) -> str:
    """DERIVED, never random: the same action always yields the same id.

    This is the idempotency guarantee. A crash between submit and the
    response, followed by a replay, produces a byte-identical id, so the
    venue rejects the duplicate instead of us doubling the position.
    """
    raw = "%s|%d" % (action_key, attempt)
    return "l5f-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


#: The prefix that marks a key as OURS. An L5 `action_key` is
#: "txhash|asset|side|price|size|timestamp" -- six fields opening with a
#: transaction hash -- so nothing L5 produces can begin with this word and
#: the two namespaces cannot collide. That matters because the key becomes
#: the `client_order_id`, which is the idempotency guarantee: a controller
#: order must never be able to claim an L5 action's id, or suppress one.
CONTROLLER_KEY_PREFIX = "CONTROLLER"


def controller_action_key(market: str, rnd: int, leg: str) -> str:
    """The idempotency key for ONE autonomous leg. Deterministic.

    Keyed on (market, round, leg) and NOT on the clock, so the timer firing
    ten times while one order rests produces the same key, hence the same
    `client_order_id`, hence DUPLICATE_SUPPRESSED on every tick after the
    first. That is what stops a repeating trigger from stacking orders --
    and it is also what stops the timer path and the L5 path from both
    placing an order for the same opportunity, because an L5 action's key
    cannot take this form.

    The round and the leg are IN the key because a pair needs two orders
    and a window may work several rounds: a key fixed per market would
    suppress the complement as a duplicate of the opening leg.
    """
    return "%s|OPEN|%s|r%d|%s" % (CONTROLLER_KEY_PREFIX, str(market),
                                  int(rnd), str(leg).upper())


def controller_provisional_price(side: str, book: Mapping[str, Any],
                                 tick: Any) -> Optional[D]:
    """A restable maker price one tick inside our own touch, or None.

    PROVISIONAL, AND THAT IS NOT A HEDGE. The order that actually reaches
    the venue is built by `TS.build_orders` from this same authenticated
    book inside the two-sided branch, which supersedes the incoming
    decision exactly as it already supersedes L5's. What this price is for
    is the TRANSLATION GATE: the gate needs a side and a price to decide
    staleness, book availability and -- on the restable branch -- maker
    class, and `our_touch - tick` is restable by construction, so the gate
    judges a maker order rather than a crossing one. Returning None when
    the book or the tick is unreadable makes the caller refuse instead of
    inventing a price.
    """
    bid, ask = X._d(book.get("bid")), X._d(book.get("ask"))
    if bid is None or ask is None:
        return None
    try:
        t = D(str(tick))
    except BaseException:                                      # noqa: BLE001
        return None
    if t <= 0:
        return None
    touch = ask if str(side).upper() == UP else (D(1) - bid)
    px = touch - t
    return px if px > 0 else None


#: THE APPROVED ABSOLUTE RESIDUAL CEILING, in contracts.
#:
#: WHY THIS EXISTS. The owner approved "residual cap 3". The implemented
#: control is RESIDUAL_CAP_k2: `cap = k * max(largest filled clip, this
#: order's qty)` on the prospective book, with k=2. Those are NOT equivalent.
#: They coincide at a clip of 1.5 and the RELATIVE bound exceeds 3 above it,
#: without limit -- on 2026-10-23 the 2015z clip was 1.3, giving 2.6, but a
#: larger envelope would raise the clip and with it the cap, silently past
#: the figure that was approved.
#:
#: So the relative bound is now intersected with this absolute one. It can
#: only ever TIGHTEN: `min(k * clip, 5)`. max_abs_residual (10) remains the
#: outer SafetyLimits bound and is unchanged; this is the approved figure,
#: enforced where the k-rule is applied.
#:
#: RAISED 3 -> 5 ON OWNER AUTHORIZATION 2026-11-02, twice stated, after the
#: evidence that exposure ($20) and cash ($12) were not the binding limits.
#:
#: AND IT RE-ADMITS THE 1415Z INCIDENT ORDER. THIS IS NOT A SIDE EFFECT; IT
#: IS THE DIRECT CONSEQUENCE AND IT IS RECORDED HERE SO IT CANNOT BE
#: REDISCOVERED BY A LOSS. On 2026-10-31 the 1415z window reached 4.25
#: contracts of unmatched residual: UP 2.17 filled, then order six added UP
#: 2.08 on top of it. The cap of 3 is what should have refused that order,
#: and `test_l5_residual_cap_1415z_regression` exists because it did not.
#: 4.25 is BELOW 5, so at this cap order six is admitted again -- verified,
#: not inferred: at cap 3 the gate refuses it, at cap 5 it does not.
#:
#: WHAT STILL BOUNDS THAT EXPOSURE, since the residual cap no longer does.
#: A 4.25-contract unmatched book is bounded instead by the $10 campaign
#: additional-loss gate (`l5_campaign_baseline`, deficit = cost - min(up,
#: dn), so a fully unmatched 4.25 at ~0.46 is ~$1.96 of deficit and passes),
#: by the $12 cash cap, and by the $1.50 worst-case-deficit ceiling. The
#: residual cap was the tightest of those on CONTRACT COUNT and is now the
#: loosest. The incident's own arithmetic is preserved as a pinned record
#: against its historical cap of 3 rather than being rewritten away, and
#: `test_cap_five_readmits_the_1415z_order_six` pins this consequence
#: directly so that raising it further is a deliberate act with visible
#: cost.
ABS_RESIDUAL_CAP_CONTRACTS = D("5")


def residual_cap_decision(*, resid: D, same_side: D, other_side: D,
                          qty: D, side: str, k: D, clip: D,
                          same_side_includes_this_order: bool,
                          certain: bool = True) -> Dict[str, Any]:
    """The residual cap, on a FILL-CONTINGENT WORST CASE. One definition.

    BOTH call sites use this: the gate in `handle` and the final guard
    immediately before the wire. They are deliberately redundant and must
    never disagree, so the arithmetic lives once.

    THE RULE. For an order on side S, assume the worst thing that can
    happen: every still-fillable order on S FILLS, and nothing resting on
    the OTHER side fills at all. A resting opposite leg is an intention, not
    a hedge -- it can be cancelled, and on 1415z it was.

    WHAT THIS REPLACES, AND WHY. The old basis was PROSPECTIVE_NET:
    `resid + signed_outstanding`, netting both sides. On 2026-10-31 at
    14:21:01 that read `residual_realized 2.17` and
    `outstanding_residual -2.17` -- UP 2.17 HELD against a DOWN 2.17
    completion RESTING -- summed them to `prospective_before 0.00`, and
    `exempt_open = (net == 0)` fired. An exposure-increasing UP 2.08 was
    therefore treated as a first leg into an empty market, the 3-contract
    ceiling never applied, the DOWN leg was cancelled unfilled, and the
    position became UP 4.25. The net also understated the post-fill figure
    as 2.08 when the truth was 4.25.

    THE EXEMPTIONS SURVIVE, both of them, and are now stated separately so a
    record shows which one applied:

      * A GENUINE OPENING LEG -- nothing held AND nothing resting on either
        side -- is never capped, so a first order cannot be refused by a
        bound derived from its own size. `net == 0` was never this test and
        is what went wrong; three explicit conditions are.
      * A LEG THAT SHRINKS THE WORST CASE is never refused at any depth. A
        completion against held inventory reduces |worst| and passes, which
        is the asymmetry that keeps risk-reducing orders flowing.

    `same_side_includes_this_order` exists because the two call sites differ in
    one respect only: `record_submit` has run by the time the final guard
    is reached, so this order is already in the same-side sum there and is
    not in the gate's. Computing it either way round would double-count the
    order at one site and miss it at the other.
    """
    up = str(side).upper() == UP
    signed = qty if up else -qty
    signed_same = same_side if up else -same_side
    worst_after = resid + signed_same
    if not same_side_includes_this_order:
        worst_after = worst_after + signed
    worst_before = worst_after - signed
    b_abs = -worst_before if worst_before < 0 else worst_before
    a_abs = -worst_after if worst_after < 0 else worst_after
    use_clip = qty if qty > clip else clip
    cap = min(k * use_clip, ABS_RESIDUAL_CAP_CONTRACTS)
    nothing_held = resid == D(0)
    resting_same_other_than_this = (
        same_side - qty if same_side_includes_this_order else same_side)
    nothing_resting = (resting_same_other_than_this == D(0)
                       and other_side == D(0))
    # AN APPARENT FLAT POSITION MAY NOT EXEMPT AN OPENING WHILE THE BOOK IS
    # UNCERTAIN. `nothing_held` reads OUR ledger, and on 1445z our ledger
    # was wrong: a phantom DOWN leg had netted a real UP contract to zero,
    # so this read flat and waived the cap for a second opening that took
    # venue exposure to 2 contracts. The exemption exists so a FIRST order
    # cannot be refused by a bound derived from its own size; it was never
    # meant to cover a flat reading we cannot trust.
    exempt_open = bool(nothing_held and nothing_resting and certain)
    increases = a_abs > b_abs
    binds = bool((not exempt_open) and increases and a_abs > cap)
    return {
        "basis": "FILL_CONTINGENT_WORST_CASE",
        "k": str(k), "clip_used": str(use_clip), "cap_contracts": str(cap),
        "relative_cap_contracts": str(k * use_clip),
        "absolute_cap_contracts": str(ABS_RESIDUAL_CAP_CONTRACTS),
        "which_binds": ("ABSOLUTE"
                        if ABS_RESIDUAL_CAP_CONTRACTS < k * use_clip
                        else "RELATIVE"),
        "residual_realized": str(resid),
        "outstanding_same_side": str(same_side),
        "outstanding_other_side": str(other_side),
        "same_side_includes_this_order": bool(same_side_includes_this_order),
        "worst_case_before": str(worst_before),
        "worst_case_after": str(worst_after),
        "abs_worst_before": str(b_abs), "abs_worst_after": str(a_abs),
        "nothing_held": bool(nothing_held),
        "nothing_resting": bool(nothing_resting),
        "prospective_book_flat": exempt_open,
        "certain": bool(certain),
        "increases_abs_prospective": bool(increases),
        "binds": binds,
        # THE OLD NETTED BASIS, reported beside the worst case so the two
        # readings stay comparable in the record. It nets BOTH sides, which
        # is what made a held 2.17 against a resting 2.17 read as 0.00.
        "netted_view_would_have_said": str(
            resid + signed_same + (-other_side if up else other_side)),
        # LEGACY NAMES, KEPT AS ALIASES. Existing readers -- the dedicated
        # residual-cap suite among them -- address the before/after
        # magnitudes by these names, and the magnitudes are what changed
        # basis, not what they mean. Renaming them outright broke eleven
        # tests that were asserting correct behaviour, so the old names are
        # preserved pointing at the operative worst-case figures. `basis`
        # says which basis produced them, and that string DID change.
        "prospective_before": str(worst_before),
        "prospective_after_if_filled": str(worst_after),
        "abs_prospective_before": str(b_abs),
        "abs_prospective_after": str(a_abs),
        "outstanding_residual": str(
            signed_same + (-other_side if up else other_side)),
        "comparison": ("refuse iff NOT (nothing held and nothing resting) "
                       "AND this increases the worst case AND the worst "
                       "case after exceeds the cap; equality passes"),
        "note": ("a resting OPPOSITE-side order is never credited as a "
                 "hedge; same-side resting orders count toward maximum "
                 "potential exposure"),
        "units": "contracts",
    }



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
        if self.max_total_exposure_usd is None:
            # A CONFIGURED-BUT-UNREADABLE CEILING. The worst case is not
            # knowable, so the halt is reported as the only bound that IS
            # readable. `refuse` blocks every submission in this state, so
            # this figure is a diagnostic rather than a permission.
            return self.daily_realized_loss_halt_usd
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
               risk_by_market: Optional[Mapping[str, D]] = None,
               pair_second_leg_ok: bool = False
               ) -> Optional[str]:
        """Refuse on POTENTIAL exposure: filled + still-fillable + this one.

        `outstanding_by_market` and `live_order_count` are REQUIRED rather
        than defaulted, so a caller that has not been taught about live
        orders fails loudly instead of silently losing the bound.
        """
        if orders_uncertain:
            return "ORDER_STATE_UNCERTAIN"
        # A CEILING THAT CANNOT BE READ IS NOT AN ABSENT CEILING, and this
        # check comes before any arithmetic that would use it. The owner
        # configured L5_MAX_TOTAL_EXPOSURE_USD and it did not parse, so the
        # approved envelope is unknown -- refusing is the only answer that
        # cannot exceed it. Defaulting here would let a typo restore a
        # ceiling the owner had deliberately lowered.
        if self.max_total_exposure_usd is None:
            return "MAX_TOTAL_EXPOSURE_CEILING_UNREADABLE"
        # THE SECOND MAKER SLOT, FOR ONE BOUNDED PAIR ONLY.
        #
        # `pair_second_leg_ok` is EVIDENCE the caller had to establish, not a
        # preference: `l5_two_sided_probe.second_leg_slot_refusal` grants it
        # only when the flag is on, exactly one order is live, it is in this
        # market, on the opposite side, claimed in this round, and this side
        # is not. Default False, so with no caller change and no flag this
        # line is the ceiling it always was.
        #
        # IT RAISES THE CEILING BY EXACTLY ONE AND NOTHING ELSE. Every other
        # bound below -- per-market worst case, account-wide exposure, the
        # cash envelope, the residual ceiling, the daily and cumulative
        # halts -- already prices an outstanding order as though it fills in
        # full, so two resting legs are charged as two filled legs without
        # any change here. That is what makes this safe to grant: it buys a
        # slot, never headroom.
        ceiling = self.max_live_orders + (1 if pair_second_leg_ok else 0)
        if live_order_count >= ceiling:
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

#: The two things that can originate a decision, named the same way
#: `l5_intent_gate` names them so one vocabulary covers the shadow
#: controller, the boundary and the tape.
L5_FOLLOWED = "L5_FOLLOWED"
CONTROLLER_GENERATED = "CONTROLLER_GENERATED"


class Provenance:
    """One record per decision, carrying the whole chain.

    `provenance` SAYS WHOSE DECISION THIS WAS, and it is recorded rather
    than inferred. Until autonomous openings existed, every record here
    came from an observed L5 action and the `L5_NATIVE` slot could be read
    as "what L5 did". It can no longer: a CONTROLLER_GENERATED record puts
    OUR OWN decision in that slot, because the slot is the chain's
    first stage and the downstream readers need its `side`. So the field
    below is the only thing that distinguishes them, and anything
    attributing a record to L5 must read it rather than assume.
    """

    def __init__(self, action_key: str, l5: Mapping[str, Any],
                 market: str, window: str,
                 provenance: str = L5_FOLLOWED) -> None:
        if provenance not in (L5_FOLLOWED, CONTROLLER_GENERATED):
            raise ValueError("unknown provenance: %r" % (provenance,))
        self.rec: Dict[str, Any] = {
            "action_key": action_key, "market": market, "window": window,
            "provenance": provenance,
            "decision_source": (
                "an observed L5 action, translated for PMUS"
                if provenance == L5_FOLLOWED else
                "OUR OWN CONTROLLER, from the authenticated PMUS book. No "
                "L5 action underlies this record and none was fabricated; "
                "the L5_NATIVE slot holds the CONTROLLER's decision because "
                "that slot is the chain's first stage."),
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

#: Where a venue row may state when the order was created. Read in this
#: order and parsed STRICTLY: an authoritative timestamp is usable evidence,
#: a guess is not. Nothing here invents a time -- an unreadable row returns
#: None and the deadline check keeps failing closed.
VENUE_ORDER_CREATED_KEYS = ("createTime", "createdAt", "created_at",
                            "submitTime", "submittedAt", "orderTime",
                            "transactTime", "creationTime")


def venue_order_created_utc(row: Mapping[str, Any]) -> Optional[str]:
    """An ISO-8601 UTC submit time from the venue's own row, or None.

    STRICT ON PURPOSE. `maybe_acc_deadline` requires a tz-aware ISO string
    and treats anything else as unusable, so a value this cannot parse into
    one must come back as None rather than as a plausible-looking string. A
    naive timestamp is refused too: assuming UTC for a venue that did not
    say so is exactly the fabrication the deadline rule exists to prevent.
    """
    for k in VENUE_ORDER_CREATED_KEYS:
        raw = row.get(k)
        if raw is None:
            continue
        try:
            t = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except BaseException:                                  # noqa: BLE001
            continue
        if t.tzinfo is None:
            continue
        return t.astimezone(dt.timezone.utc).isoformat()
    return None


def recover_submit_time(venue_row: Mapping[str, Any], *,
                        mine: Mapping[str, Any],
                        live_orders: Mapping[str, Any],
                        submitted: Mapping[str, Any],
                        venue_order_id: str) -> Optional[str]:
    """When did WE send this order? None when it cannot be established.

    WHAT THIS FIXES, measured live on 2026-10-30 in window 0200z. We
    submitted under our own `l5f-...` id; the venue's ack carried NO
    `venue_order_id`, so when the reconciliation read the open order back it
    could not match our id and adopted the row keyed by the VENUE id
    instead. That adopted row had no `t`, because nothing was ever submitted
    under that key. `maybe_acc_deadline` then read the missing `t` as
    unusable, marked the store UNCERTAIN and disarmed -- five seconds after
    the order reached the book. The order was cancelled at cum 0 and the
    window was spent.

    THE TIME IS RECOVERED, NEVER INVENTED, in descending order of
    authority:

      1. our own record under this key, if we have one;
      2. a DURABLE CLIENT-ORDER RECORD whose `venue_order_id` matches this
         row -- `link_venue_order_id` writes that link as soon as the venue
         discloses the id, so the order we sent is identifiable even when
         the ack did not name it;
      3. the venue's own creation timestamp, parsed strictly.

    None means not established, and the caller must leave `t` absent so the
    deadline rule keeps failing closed. That is the whole point: this makes
    a recoverable case recoverable and changes nothing about the
    unrecoverable one.
    """
    t = mine.get("t")
    if t:
        return str(t)
    vid = str(venue_order_id or "").strip()
    if vid:
        for book in (live_orders, submitted):
            if not isinstance(book, Mapping):
                continue
            for other_coid, row in book.items():
                if not isinstance(row, Mapping):
                    continue
                if str(row.get("venue_order_id") or "").strip() != vid:
                    continue
                got = row.get("t")
                if got:
                    return str(got)
    return venue_order_created_utc(venue_row)


#: Books a venue order id of ours may be recorded in, for the purpose of
#: RE-KEYING an open-orders row onto the coid we sent it with. `quarantine`
#: is deliberately absent: a quarantined order is ours, but it was parked
#: because it could not be identified, and silently lifting it back onto the
#: live map is not this function's business. It is adopted instead, and
#: `duplicate_venue_order_coid` is what then protects the money.
COID_BOOKS = ("live_orders", "pending_terminal", "submitted")

#: Every book an id of ours can sit in -- the same four `owns_venue_order_id`
#: consults, and for the same reason. Used when ANSWERING "which venue order
#: is this coid", where missing a bucket would weaken a fail-closed guard.
OWNED_COID_BOOKS = ("live_orders", "pending_terminal", "quarantine",
                    "submitted")


#: A DURABLE coid -> venue order id link, written at booking time.
#:
#: WHY IT IS NEEDED. `apply_order_progress` pops a terminal order out of
#: `live_orders`, so by the time a SECOND coid tries to book the same venue
#: order the first one's record is gone from every row-book and the
#: duplicate guard has nothing to compare. This map outlives the order.
VENUE_ORDER_LINK = "venue_order_ids"
#: which ORDER an `orders_uncertain` mark is about, when it is about one.
#: Evidence for `resolve_cancel_uncertainty`; never read as permission.
UNCERTAIN_CAUSE = "orders_uncertain_cause"
#: the only causes the venue's terminal read of that order can end: both
#: say "we cancelled it and could not see it finish".
CANCEL_UNCERTAINTY_KINDS = ("CANCEL_STILL_OPEN", "CANCEL_UNVERIFIED")
#: a reconciliation mark whose WHOLE content is "these orders left the
#: venue's open list and their final fill is not booked yet". Resolving each
#: of them from the venue's own order row answers it in full -- provided
#: nothing was booked, so the positions that reconciliation just verified
#: are still the positions. See `resolve_pending_uncertainty`.
RECONCILE_PENDING_ONLY = "RECONCILE_PENDING_ONLY"
#: audit rows kept for resolved marks. Bounded so the ledger cannot grow.
UNCERTAIN_RESOLVED_KEEP = 50


def venue_order_id_for_coid(state: Mapping[str, Any], coid: str,
                            books: Sequence[str] = OWNED_COID_BOOKS
                            ) -> Optional[str]:
    """The venue order id WE recorded for one of our client order ids."""
    link = state.get(VENUE_ORDER_LINK) if isinstance(state, Mapping) else None
    if isinstance(link, Mapping):
        vid = str(link.get(coid) or "").strip()
        if vid:
            return vid
    for book in books:
        rows = state.get(book) if isinstance(state, Mapping) else None
        if not isinstance(rows, Mapping):
            continue
        row = rows.get(coid)
        if not isinstance(row, Mapping):
            continue
        vid = str(row.get("venue_order_id") or "").strip()
        if vid:
            return vid
    return None


#: ANOTHER STRATEGY'S ORDERS ON THE SAME VENUE ACCOUNT ARE NOT OURS.
#:
#: Reconciliation rebuilds `live_orders` from the venue's open list, and it
#: adopts every row it finds -- that is right for an order of ours the file
#: lost, and wrong for an order a second strategy placed. Run beside the
#: BTC 5m adapter, a reconciliation adopted its resting `b5m-` order:
#: `owns_venue_order_id` then answered True for it, it counted against the
#: live-order ceiling, and the terminal sweep CANCELLED it
#: (tests/test_l5_btc5m_btc15m_isolation.py records this).
#:
#: MANDATORY, BY OWNER DECISION (2026-11-11): "Make the BTC15m ownership
#: filter mandatory before simultaneous execution, not an optional feature.
#: Neither strategy should adopt, cancel, or settle another strategy's
#: orders." So the prefixes of every OTHER registered strategy
#: (`l5_origin.STRATEGY_COID_PREFIX`) are foreign ALWAYS -- a code registry,
#: with no variable that has to be set for it to hold and none that can turn
#: it off. The variable below can only ADD further prefixes.
#:
#: THE VENUE DOES NOT ECHO OUR CLIENT IDS, so the prefix alone is not
#: enough. `l5_order_body.build` -- the body actually posted -- carries no
#: client order id at all: the id lives in each strategy's own ledger, and a
#: real `/v1/orders/open` row names only the venue's id and the market. A
#: rule that needs the prefix on the row therefore holds against a test
#: double that echoes it and NOT against the venue. What every real row
#: does carry is `marketSlug`, and the registered strategies trade disjoint
#: series. So an order this ledger does not already track, resting in a
#: market of ANOTHER registered strategy's series, is foreign too -- decided
#: on the venue's own field. (An untracked order in OUR OWN series is still
#: adopted, as before: this ledger cannot tell a hand-placed order there
#: from one of its own that its file lost, and losing track of its own
#: order is the worse error.)
#:
#: Our own prefix can never be named foreign, and a token that is not a
#: short lowercase prefix ending in "-" is ignored. Foreign orders are
#: listed in `foreign_orders` with their origin and are never adopted, never
#: counted against OUR order ceiling and never cancelled by us. They ARE
#: counted in account-wide exposure, at $1 a contract -- see
#: `account_wide_risk_by_market`. POSITIONS are a separate matter: a market
#: this ledger did not trade is still adopted at its upper bound, so the
#: account-wide exposure and loss limits still see it.
FOREIGN_COID_PREFIXES_ENV = "L5_FOREIGN_COID_PREFIXES"
OWN_STRATEGY = ORIGIN.BTC15M
OWN_COID_PREFIX = ORIGIN.STRATEGY_COID_PREFIX[OWN_STRATEGY]
_FOREIGN_PREFIX_RE = re.compile(r"[a-z0-9]{2,8}-")
#: how many markets `foreign_orders_seen` remembers. It is evidence for a
#: label on a later settlement, so it only has to outlive a window or two.
FOREIGN_SEEN_KEEP = 64


def foreign_coid_prefixes() -> Tuple[str, ...]:
    out: List[str] = [p for p in ORIGIN.other_strategy_prefixes(OWN_STRATEGY)
                      if p != OWN_COID_PREFIX]
    for tok in (os.getenv(FOREIGN_COID_PREFIXES_ENV) or "").split(","):
        tok = tok.strip()
        if _FOREIGN_PREFIX_RE.fullmatch(tok) and tok != OWN_COID_PREFIX \
                and tok not in out:
            out.append(tok)
    return tuple(out)


def is_foreign_coid(coid: Any) -> bool:
    c = str(coid or "")
    return bool(c) and not c.startswith(OWN_COID_PREFIX) and any(
        c.startswith(p) for p in foreign_coid_prefixes())


def tracked_coid_for_venue_order(venue_order_id: Optional[str],
                                 state: Mapping[str, Any]) -> Optional[str]:
    """Which of OUR client order ids already owns this venue order, or None.

    WHY THIS EXISTS. 0230z, 2026-10-30. The venue's open-orders row for an
    order WE sent carried no client order id, so `reconcile_from_venue` keyed
    it by the venue's own id -- `DDDXP54G12TT` -- and marked it `adopted`,
    while our own `l5f-b420c940...` was absent from that rebuilt map and
    parked as vanished. ONE venue order, two tracking records, two
    idempotency keys: `ORDER_PROGRESS` booked 1.91 under the venue id at
    02:30:27.385 and `PENDING_TERMINAL_RESOLVED` booked the same 1.91 under
    our coid 0.45 seconds later, each reading `already: 0`. The book went to
    3.82 against the venue's 1.91, `adopt_venue_position` then replaced a
    cost basis that was individually correct ($0.38/share) with the
    $1-a-share bound it must use for a net it cannot decompose, and
    settlement priced that placeholder: -$1.9100 realized against $0.7258
    actually spent.

    The evidence that identifies the order was already in the record -- both
    entries carried `venue_order_id: DDDXP54G12TT`. This reads it.

    DELIBERATELY NARROW. It answers only "do we already track this venue
    order", so an order we genuinely never sent still has no match and is
    still adopted. On the impossible case of two of our coids claiming one
    venue order -- itself the defect state -- the lowest id wins, which is
    deterministic and creates no THIRD record; the duplicate-booking guard
    is what stops the money moving twice.
    """
    vid = str(venue_order_id or "").strip()
    if not vid:
        return None
    hits = set()
    for book in COID_BOOKS:        # NOT quarantine; see the note on COID_BOOKS
        rows = state.get(book) if isinstance(state, Mapping) else None
        if not isinstance(rows, Mapping):
            continue
        for coid, row in rows.items():
            if not isinstance(row, Mapping):
                continue
            if str(row.get("venue_order_id") or "").strip() == vid:
                hits.add(str(coid))
    return min(hits) if hits else None


def submitted_identity_mismatch(state: Mapping[str, Any], coid: str,
                                market: str, side: str) -> Optional[str]:
    """Why this (market, side) may not be booked under `coid`, or None.

    THE 1445z PHANTOM, 2026-11-04T14:47:13Z. The runner books order progress
    with `side=o.get("side")` read from its OWN `live_orders` record, and
    `apply_order_progress` trusted it. The venue's order row carries no side
    for us to check it against, so a wrong side was unfalsifiable: a real UP
    execution at WIRE 0.26 was booked as DOWN, whose own price is
    1 - 0.26 = 0.74. That phantom DOWN leg zeroed the engine's residual, the
    residual cap then read the market as flat, exempted a new opening, and
    real venue exposure reached 2.0000 UP contracts against a 1-contract
    cap. The venue-authoritative audit recorded the disagreement as
    QUANTITY_MISMATCH venue_qty 2.0000 / ledger_qty 1.0000 and booked the
    leg at the $1-a-share placeholder, which is why the window reported
    -$1.00 against a true -$0.51.

    THE ONLY SIDE WE MAY BOOK IS THE ONE WE SUBMITTED. `submitted[coid]` is
    written when the order is sent and is the sole durable record of what
    was authorized under that client id, so it -- not a later in-memory
    read -- decides. A disagreement is REFUSED and the store is marked
    uncertain rather than reconciled here, because nothing inside this
    process can tell which of the two readings is the real order.

    ABSENT IS NOT A MISMATCH. An order submitted before this record existed
    has no `submitted` entry, and refusing those would strand real
    inventory, so they keep the previous behaviour. A record that EXISTS and
    DISAGREES is the only case this refuses -- the same compatibility line
    the opening-bound guard draws.
    """
    if not coid:
        return None
    sub = (state.get("submitted") or {}).get(str(coid))
    if not isinstance(sub, Mapping):
        return None
    want_m = str(sub.get("market") or "").strip()
    want_s = str(sub.get("side") or "").strip().upper()
    got_m = str(market or "").strip()
    got_s = str(side or "").strip().upper()
    if want_m and want_m != got_m:
        return "SUBMITTED_MARKET_DISAGREES:%s!=%s" % (got_m or None, want_m)
    if want_s and want_s != got_s:
        return "SUBMITTED_SIDE_DISAGREES:%s!=%s" % (got_s or None, want_s)
    return None


def duplicate_venue_order_coid(state: Mapping[str, Any],
                               coid: str) -> Optional[str]:
    """Another client order id that has ALREADY booked this venue order.

    THE FAIL-CLOSED HALF of the 0230z repair. `tracked_coid_for_venue_order`
    removes the known way one venue order acquires two coids; this makes the
    consequence unreachable however identity goes wrong next time, because
    `applied_fill_qty` is keyed on the CLIENT id and so cannot see it.

    Only a DIFFERENT coid counts. Successive partial fills arrive under the
    same coid and are the ordinary case this must not disturb.
    """
    vid = venue_order_id_for_coid(state, coid)
    if not vid:
        return None
    booked = state.get("filled_qty") if isinstance(state, Mapping) else None
    if not isinstance(booked, Mapping):
        return None
    others = set()
    for other, qty in booked.items():
        if str(other) == str(coid):
            continue
        try:
            if D(str(qty)) <= 0:
                continue
        except (TypeError, ValueError, ArithmeticError):
            pass            # unreadable is not evidence of nothing booked
        if venue_order_id_for_coid(state, str(other)) == vid:
            others.add(str(other))
    return min(others) if others else None


# ====================== WHO OPENED THE INVENTORY WE HOLD ======================
#
# 1445z/1415z/1430z, 2026-10-30. `Runner.maybe_book_completion` chose its
# completion route on `self._last_action_row.get(slug) is None` -- "is there an
# L5 action row for this market" -- which answers a question about the INGEST
# FEED, not about the position. L5 rows arrive continuously, so on every tick
# the route ran there was a row, the L5 branch always won, and inventory the
# autonomous controller had opened was completed and recorded as
# `L5_FOLLOWED` with a reused L5 action key. Across the three-window campaign
# `book_completion:REENTERED:CONTROLLER` never incremented once while 12 of
# the 15 orders were controller-keyed.
#
# The route must follow the ORIGIN OF THE HELD INVENTORY instead, and that
# origin is recoverable exactly, because `client_order_id` is a pure function
# of the action key and an L5 action key cannot take the controller's form
# (see `controller_action_key`). Two sources, in order:
#
#   1. THE STAMP, written by `record_submit` at submission time. Authoritative
#      and needs no bounds.
#   2. THE DETERMINISTIC NAMESPACE, by enumerating the controller's own coids.
#      This is what makes the attribution correct for inventory ALREADY in the
#      ledger, with no migration and no rewrite of a historical record.
#
# ATTRIBUTION ONLY. Nothing here is a limit, a gate or a price. It changes
# which provenance the tape records and which vehicle re-enters `handle`; the
# economics on both routes are the same `TS.complement_order` call against the
# same ceilings, and every submission still passes every existing gate.
ORIGIN_CONTROLLER = "CONTROLLER"
ORIGIN_L5 = "L5"
ORIGIN_MIXED = "MIXED"
ORIGIN_UNKNOWN = "UNKNOWN"

#: How far to enumerate the controller's deterministic coid namespace. Rounds
#: observed in a live 15-minute window reached r7; attempts reached 0. These
#: are generous bounds on both, and overrunning them cannot do harm: an
#: unrecognised coid reads UNKNOWN, which leaves the pre-existing route in
#: force -- the same behaviour as before this attribution existed.
CONTROLLER_ROUND_SCAN = 64
CONTROLLER_ATTEMPT_SCAN = 8


def controller_coid_namespace(market: str, *,
                              rounds: int = CONTROLLER_ROUND_SCAN,
                              attempts: int = CONTROLLER_ATTEMPT_SCAN
                              ) -> Dict[str, str]:
    """Every client order id the controller could have produced for `market`.

    Pure and deterministic: the same market always yields the same set, so an
    attribution taken twice cannot disagree with itself.
    """
    out: Dict[str, str] = {}
    keys = [controller_action_key(market, rnd, leg)
            for rnd in range(int(rounds)) for leg in ("UP", "DOWN")]
    # the unresolved-decision form the controller also emits
    keys.append("%s|OPEN|%s|UNRESOLVED" % (CONTROLLER_KEY_PREFIX, str(market)))
    for key in keys:
        for att in range(int(attempts)):
            out[client_order_id(key, att)] = key
    return out


def coid_origin(state: Mapping[str, Any], coid: str, market: str,
                namespace: Optional[Mapping[str, str]] = None) -> str:
    """Who placed one order: CONTROLLER, L5, or UNKNOWN.

    UNKNOWN is a real answer and the safe one. An L5 coid is a hash that
    cannot be inverted, so NOT being in the controller's namespace is evidence
    of an L5 origin rather than proof of one, and this declines to upgrade
    evidence into proof.

    `namespace` lets a caller with many coids build the controller namespace
    ONCE instead of per coid -- it is ~1k hashes, and a market accumulates
    fills all window. Omitted, it is built here.
    """
    sub = (state.get("submitted") or {}).get(coid) if isinstance(
        state, Mapping) else None
    if isinstance(sub, Mapping):
        prov = str(sub.get("provenance") or "").strip()
        if prov == CONTROLLER_GENERATED:
            return ORIGIN_CONTROLLER
        if prov == L5_FOLLOWED:
            return ORIGIN_L5
        key = str(sub.get("action_key") or "").strip()
        if key.startswith(CONTROLLER_KEY_PREFIX + "|"):
            return ORIGIN_CONTROLLER
        if key:
            return ORIGIN_L5
        # `mode` ALREADY CARRIES IT on the controller route: `l5_intent_gate`
        # has always recorded `mode=CONTROLLER_GENERATED`, so this attributes
        # every controller order ALREADY ON DISK without a new field and
        # without a migration. The executor's own route puts MAKER/TAKER
        # here, which says nothing about who decided -- hence only this one
        # value is read as evidence.
        if str(sub.get("mode") or "").strip() == CONTROLLER_GENERATED:
            return ORIGIN_CONTROLLER
    try:
        ns = (namespace if namespace is not None
              else controller_coid_namespace(str(market)))
        if str(coid) in ns:
            return ORIGIN_CONTROLLER
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return ORIGIN_UNKNOWN
    return ORIGIN_UNKNOWN


def inventory_origin(state: Mapping[str, Any], market: str,
                     side: Optional[str] = None) -> str:
    """Who opened the inventory this ledger holds for `market`.

    Reads `fill_provenance`, which `apply_fill` writes per booked fill, so
    this is attribution from CONFIRMED FILLS and never from an intention.

    MIXED when both origins definitely contributed -- the honest answer, and
    the one that keeps the pre-existing route in force rather than crediting
    either side with the other's inventory. Fails to UNKNOWN on anything it
    cannot read, which does the same.
    """
    try:
        proof = ((state.get("fill_provenance") or {}).get(str(market))
                 or {}) if isinstance(state, Mapping) else {}
        entries = proof.get("entries") or ()
        want = str(side).upper() if side is not None else None
        # ONCE per call, not once per fill: the namespace is deterministic,
        # so building it per entry only repeats the same ~1k hashes.
        ns = controller_coid_namespace(str(market))
        seen = set()
        for e in entries:
            if not isinstance(e, Mapping):
                continue
            if want is not None and str(e.get("side") or "").upper() != want:
                continue
            try:
                if D(str(e.get("qty") or 0)) <= 0:
                    continue
            except (TypeError, ValueError, ArithmeticError):
                pass        # an unreadable quantity is not evidence of none
            c = e.get("coid")
            seen.add(coid_origin(state, str(c), str(market), ns) if c
                     else ORIGIN_UNKNOWN)
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return ORIGIN_UNKNOWN
    if not seen:
        return ORIGIN_UNKNOWN
    if ORIGIN_CONTROLLER in seen and ORIGIN_L5 in seen:
        return ORIGIN_MIXED
    if seen == {ORIGIN_L5}:
        return ORIGIN_L5
    if ORIGIN_CONTROLLER in seen:
        return ORIGIN_CONTROLLER
    return ORIGIN_UNKNOWN


def _floor_to_inc(qty: D, inc: D) -> D:
    """`qty` floored to a whole multiple of `inc`. Never rounds up: a size
    the venue's grid cannot express must become smaller, never larger."""
    if inc is None or inc <= 0:
        return qty
    return (qty / inc).to_integral_value(rounding="ROUND_FLOOR") * inc


#: How many bounded-taker-completion claims the durable ledger keeps. A claim
#: only has to outlive its own 15-minute window, so 256 is days of history and
#: far more than the duplicate-prevention it exists for needs; the bound is
#: there because the whole state dict is rewritten on every save and nothing
#: else ever removed these rows.
CLAIM_RETENTION = 256


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


def finite_decimal(raw: Any) -> Optional[D]:
    """`venue_decimal` plus finiteness. A NaN or an Infinity is UNKNOWN.

    The cost-basis correction compares and sums these, and a non-finite value
    would propagate silently through both -- so it is refused at the edge.
    """
    out = venue_decimal(raw)
    return out if (out is not None and out.is_finite()) else None


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
def venue_order_avg_price(row: Mapping[str, Any]) -> Optional[D]:
    """The venue's average fill price on an order row (WIRE terms), or None
    when the row states none. Never a limit price standing in for it."""
    if not isinstance(row, Mapping):
        return None
    px = venue_decimal(row.get("avgPx"))
    return px if px is not None and D(0) < px < D(1) else None


UNKNOWN_PRICE_WORST_CASE = D(1)

#: A RE-QUOTE KEEPS ITS ROUND. Exactly "ON" or it is off. See `probe_round`.
REQUOTE_KEEPS_ROUND_ENV = "L5_TS_REQUOTE_KEEPS_ROUND"
#: at most this many unfilled re-quotes are walked past per permitted round,
#: and never more than the hard cap in all: the walk is bounded whatever
#: the market does, and the submission budgets bound the orders themselves.
ACC_REQUOTES_PER_ROUND = 16
ACC_ROUND_INDEX_HARD_CAP = 512


def requote_keeps_round() -> bool:
    return (os.getenv(REQUOTE_KEEPS_ROUND_ENV) or "") == "ON"


#: THE FAVORITE-ONLY TEST, as the portfolio controller names it. Read here
#: as a variable so the submission gate needs no import of that module.
FAVORITE_ONLY_PROFILE_ENV = "L5_PORTFOLIO_PROFILE"
FAVORITE_ONLY_PROFILE = "FAVORITE_ONLY"

#: how many consecutive reconciliations a difference that our own unbooked
#: fills can explain is left for the order read to book before the venue's
#: position is adopted at the bound anyway. Reconciliations are about 13 s
#: apart in production; the order read that resolved 0415z took 1.7 s.
FILL_IN_FLIGHT_MAX_DEFERRALS = 2


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

    def __init__(self, path: str,
                 now_fn: Optional[Callable[[], dt.datetime]] = None) -> None:
        self.path = path
        #: THE CALIBRATION RECORDER, attached by the executor and optional.
        #:
        #: It lives here rather than only on the executor because
        #: `apply_order_progress` is the ONE idempotent choke point for a
        #: venue fill: four call sites reach it (the runner's reconcile, the
        #: executor's terminal self-call, and the deadman cancel verifier),
        #: and the recorder has to see all of them or a fill booked by the
        #: deadman path would be invisible to calibration. Wiring it at the
        #: choke point also inherits the idempotency for free -- the hooks
        #: fire only on the DELTA, so polling the same cumQuantity twice
        #: records one fill, exactly as it books one.
        #:
        #: None is the default and the normal state for every non-money
        #: caller, so nothing changes for a store that nobody attaches one to.
        self.fill_calibration: Optional[Any] = None
        #: THE CLOCK THAT STAMPS A SUBMISSION. `record_submit` writes the
        #: durable submit time that the per-order deadline later measures
        #: against the runner's `now`. In production both are the wall clock
        #: and agree, which is why this defaulted to reading the wall clock
        #: directly -- and why the TTL could not be exercised end to end at
        #: all: a harness that stands the clock at T+600 compares it against
        #: a stamp written at real-time-now, so every order reads overdue the
        #: instant it is sent. Injecting it changes nothing in production
        #: (the default IS the previous expression) and makes the deadline
        #: testable against the same clock the decision used.
        self._now = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))
        self.state: Dict[str, Any] = {"positions": {}, "committed": {},
                                      "realized_today": "0", "day": None,
                                      "submitted": {}, "live_orders": {},
                                      "orders_uncertain": None,
                                      # market -> epoch seconds of the FIRST
                                      # fill that took this market off flat.
                                      # Persisted, so a restart inside a
                                      # window does not reset the dwell.
                                      "stranded_since": {}}
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
            # A PARTIAL IS NOT TERMINAL, exactly as `order_progress` already
            # says: "ORDER_STATE_PARTIALLY_FILLED" CONTAINS "FILLED", and the
            # bare substring test that used to stand here therefore retired
            # every partially filled order from this count -- the one count
            # `max_live_orders` binds on. A partial fill thus voided the
            # one-live-order bound and let a second order go out on top of an
            # order still resting at the venue with `remaining` to work.
            #
            # The terminal-state check is KEPT, narrowed by one clause, so a
            # row that genuinely names a terminal state still counts zero.
            # That case is defensive rather than reachable -- `terminate_order`
            # zeroes `remaining` and POPS the row, and a completing fill pops
            # it too -- but a belt that costs one clause is worth keeping.
            state = str(o.get("state") or "").upper()
            if (any(h in state for h in TERMINAL_HINTS)
                    and "PARTIAL" not in state):
                continue
            rem = venue_decimal(o.get("remaining"))
            if rem is None or rem > 0:
                n += 1
        n += len(self.state.get("pending_terminal") or {})
        n += len(self.state.get("quarantine") or {})
        return n

    @staticmethod
    def worst_case_remaining(o: Mapping[str, Any]) -> D:
        """Still-fillable shares on a live row; UNREADABLE IS NOT ZERO.

        `outstanding_by_market` has always substituted
        `UNKNOWN_PRICE_WORST_CASE` for an unreadable PRICE, on the stated
        principle that an unreadable row is not an absent one. The same rows'
        unreadable QUANTITY was mapped to zero, which is the opposite
        convention and the one direction that loses money: a row we cannot
        size contributed nothing to the residual contract bound, so the bound
        could be understated by a whole clip while `live_order_count` -- which
        does count an unreadable row -- happily allowed the pair slot.

        Order of preference: the readable `remaining`; else the row's own
        ordered quantity; else the approved absolute residual ceiling, which
        guarantees the bound is at least at the ceiling that refuses any
        further opening. Fail closed, never open.
        """
        rem = venue_decimal(o.get("remaining"))
        if rem is not None:
            return rem if rem > 0 else D(0)
        for k in ("qty", "quantity", "original_qty", "ordered_qty"):
            v = venue_decimal(o.get(k))
            if v is not None and v > 0:
                return v
        return ABS_RESIDUAL_CAP_CONTRACTS

    def outstanding_by_market(self) -> Dict[str, D]:
        """Still-fillable notional per market, at the price we offered."""
        out: Dict[str, D] = {}
        for o in (self.state.get("live_orders") or {}).values():
            m = str(o.get("market"))
            rem = self.worst_case_remaining(o)
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
            rem = self.worst_case_remaining(o)
            r += rem if str(o.get("side")) == "UP" else -rem
        return r

    def outstanding_on_side(self, market: str, side: str) -> D:
        """Still-fillable shares resting on ONE side. Never netted.

        WHY THE SIGNED NET IS THE WRONG QUANTITY FOR A BOUND. The 1415z
        incident of 2026-10-31: UP 2.17 was HELD while a DOWN 2.17
        completion was RESTING, so `outstanding_residual` returned -2.17,
        the signed net came to exactly 0.00, and the gate read the book as
        FLAT. It then exempted an UP 2.08 as a genuine opening leg. The
        DOWN completion was cancelled unfilled and the position became
        UP 4.25 against a 3-contract ceiling.
        """
        want = canonical_side(side, doing="summing outstanding on one side")
        r = D(0)
        for o in (self.state.get("live_orders") or {}).values():
            if str(o.get("market")) != market:
                continue
            try:
                got = canonical_side(o.get("side"),
                                     doing="reading an outstanding order")
            except UnknownSide:
                # AN UNREADABLE SIDE COUNTS ON BOTH SIDES. A row we cannot
                # attribute must not reduce a worst case.
                r += self.worst_case_remaining(o)
                continue
            if got == want:
                r += self.worst_case_remaining(o)
        return r

    def orders_uncertain(self) -> Optional[str]:
        """Set when we cannot say whether an order reached the venue.

        Cleared ONLY by an authoritative venue reconciliation. While set,
        every further submission is refused: an unknown order is assumed to
        be live and consuming the envelope, because assuming otherwise is
        the assumption that loses money.
        """
        return self.state.get("orders_uncertain") or None

    def mark_uncertain(self, why: str, *,
                       cause: Optional[Mapping[str, Any]] = None) -> None:
        """Record that the book is uncertain, and optionally WHICH ORDER.

        `cause` is evidence, not permission. It names the one order whose
        unknown state IS the uncertainty, so that the venue's own later
        answer about that same order can end it (see
        `resolve_cancel_uncertainty`). A mark without a cause -- every caller
        but the deadman's two cancel branches -- is cleared only by a full
        reconciliation, exactly as before, and any later mark replaces the
        cause along with the reason.
        """
        self.state["orders_uncertain"] = str(why)
        self.state[UNCERTAIN_CAUSE] = (
            dict(cause, why=str(why)) if isinstance(cause, Mapping) else None)
        self.save()

    def resolve_cancel_uncertainty(self, coid: str, prog: Mapping[str, Any]
                                   ) -> Optional[Dict[str, Any]]:
        """End an uncertainty that ONE cancelled order caused, on the venue's
        own word that the order is finished. Returns the record, or None.

        WHAT WAS WRONG, 2026-11-10 0200z. The requote cancelled UP order
        DMG2WQZG344P at 02:00:35.9. The deadman's three re-reads (0.6 s in
        all) still saw it working, so it marked the store uncertain:
        "deadman cancelled DMG2WQZG344P but the venue still reports it live".
        The order poll read it CANCELED 0.3 s later and booked that -- and
        the mark stayed, because only a full account reconciliation cleared
        it. Every submission was refused BOOK_UNCERTAIN until 02:00:48 while
        0.6 DOWN sat unhedged, and the replacement landed at 0.55 where pairs
        at 0.94-0.95 had been on the book.

        The question the mark asks -- "is that order still resting?" -- had
        been answered by the venue. Waiting for a reconciliation to repeat
        the answer bought nothing and cost the complement.

        EVERY CONDITION MUST HOLD, and failing any one leaves the mark alone:

          * the venue reports THIS order terminal;
          * the mark carries a cause, of a cancel kind, naming this client
            order id and the venue id this ledger linked to it;
          * the reason in force is BYTE-FOR-BYTE the one that cause was
            recorded with. Any later uncertainty, from any writer, changes
            the reason and is not this function's to clear;
          * the order is no longer live here, and nothing is parked in
            `pending_terminal` or quarantined -- those are uncertainties of
            their own that a reconciliation would re-raise.

        IT CLEARS ONLY WHAT IT CAN PROVE. The periodic reconciliation still
        runs, still compares the whole account, and still re-marks on any
        divergence. This never clears a mark it did not see the cause of.
        """
        try:
            if not bool(prog.get("terminal")):
                return None
            cause = self.state.get(UNCERTAIN_CAUSE)
            if not isinstance(cause, Mapping):
                return None
            if str(cause.get("kind")) not in CANCEL_UNCERTAINTY_KINDS:
                return None
            if str(cause.get("coid") or "") != str(coid):
                return None
            why = self.state.get("orders_uncertain")
            if not why or str(why) != str(cause.get("why")):
                return None
            vid = str(cause.get("venue_order_id") or "")
            linked = str((self.state.get(VENUE_ORDER_LINK) or {}).get(coid)
                         or "")
            if not vid or vid != linked:
                return None
            if coid in (self.state.get("live_orders") or {}):
                return None
            if (self.state.get("pending_terminal") or {}) or \
                    (self.state.get("quarantine") or {}):
                return None
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None                 # unreadable is not resolved
        rec = {"coid": str(coid), "venue_order_id": vid,
               "kind": str(cause.get("kind")),
               "venue_state": prog.get("state"),
               "cum_qty": str(prog.get("cum_qty")),
               "was": str(why),
               "t": dt.datetime.now(dt.timezone.utc).isoformat(),
               "labels": ("cleared by the venue's terminal read of the SAME "
                          "order whose unknown state was the uncertainty; "
                          "the periodic reconciliation is unchanged")}
        self.state["orders_uncertain"] = None
        self.state[UNCERTAIN_CAUSE] = None
        log = self.state.setdefault("uncertain_resolved", [])
        if isinstance(log, list):
            log.append(rec)
            del log[:-UNCERTAIN_RESOLVED_KEEP]
        return rec

    # ------------------------------------------- the defensive loss budget

    def defensive_spent(self) -> D:
        """Worst-case defensive loss COMMITTED so far, across the campaign.

        CHARGED AT SUBMISSION, NOT AT FILL, and at the worst case rather than
        the outcome. A defensive order that fills costs what it costs; one
        that does not fill costs nothing -- but a budget that only counts
        fills would let an unbounded number of unfilled attempts through,
        and each attempt is a real offer to lose that money. Counting the
        commitment is the conservative direction and it is the one a bound
        has to take. Persisted, so a restart cannot reset the budget.
        """
        try:
            return D(str(self.state.get("defensive_spent_usd") or 0))
        except BaseException:                                  # noqa: BLE001
            # unreadable means spent: a budget we cannot read is exhausted
            return DEF.DEFENSIVE_LOSS_BUDGET_USD

    def defensive_charge(self, usd: Any, detail: Mapping[str, Any]) -> D:
        """Charge the budget before the wire, and record why. Returns the new
        total. fsynced through `save()` so a process that dies between this
        and the submission has already paid."""
        amt = D(str(usd))
        if amt < 0:
            amt = D(0)
        total = self.defensive_spent() + amt
        self.state["defensive_spent_usd"] = str(total)
        log = self.state.setdefault("defensive_log", [])
        if isinstance(log, list):
            log.append(dict(detail, charged_usd=str(amt),
                            total_usd=str(total)))
        self.save()
        return total

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
        #: THE DOCSTRING ABOVE WAS TRUE OF THE INTENT AND FALSE OF THE
        #: CODE. `live_orders` is where `reconcile_from_venue` WRITES the
        #: venue's open orders, adopted ones included, so consulting it
        #: without reading provenance answered "did this ledger put that
        #: id on the book" with "is that id on the book" -- which is yes
        #: for every order the venue is holding, including the owner's.
        #:
        #: `submitted` is the permanent log of what WE sent. A row there
        #: is positive evidence of ownership and settles it on its own,
        #: which is what keeps the "we lost track of our own order" case
        #: cancellable: an order of ours that reconciliation re-adopted
        #: is still in `submitted` and is still ours.
        #:
        #: An ADOPTED row with no submit record is NOT ours, however many
        #: reconciliations have copied it forward.
        #: THE DURABLE LINK IS POSITIVE PROOF AND OUTLIVES THE ROW.
        #: `tracked_coid_for_venue_order` answers "do we already track
        #: this venue order" from the coid books plus the booking-time
        #: link map, so an order of ours whose live_orders record was
        #: lost is still recognised here even before reconciliation
        #: rewrites its key.
        #: ...BUT ONLY UNDER ONE OF *OUR* COIDS. `tracked_coid_for_venue_order`
        #: searches `live_orders` too, which is where adopted rows live, so
        #: using it unqualified returned the owner's own adopted order and
        #: re-opened the hole this method exists to close. Our coids carry
        #: OWN_COID_PREFIX; an adopted foreign order is keyed by the venue's
        #: own id and carries no such prefix.
        try:
            _tracked = tracked_coid_for_venue_order(vid, self.state)
        except BaseException:                                  # noqa: BLE001
            _tracked = None
        if _tracked and str(_tracked).startswith(OWN_COID_PREFIX):
            return True
        buckets = ("live_orders", "pending_terminal", "quarantine", "submitted")
        adopted_only = False
        for name in buckets:
            rows = self.state.get(name) or {}
            if isinstance(rows, dict):
                rows = rows.values()
            for row in (rows or ()):
                if not isinstance(row, Mapping):
                    continue
                if str(row.get("venue_order_id") or "").strip() != vid:
                    continue
                if name == "submitted" or not row.get("adopted"):
                    return True
                adopted_only = True
        if adopted_only:
            return False
        return False

    def adopted_not_ours(self, venue_order_id: Any) -> bool:
        """Is this id present ONLY as an adopted, never-submitted order?

        The positive form of the boundary, so a caller can REPORT a
        foreign order it declined to touch instead of silently skipping
        it. Silence is how an ownership breach goes unnoticed.
        """
        vid = str(venue_order_id or "").strip()
        if not vid or self.owns_venue_order_id(vid):
            return False
        for name in ("live_orders", "pending_terminal", "quarantine"):
            rows = self.state.get(name) or {}
            if isinstance(rows, dict):
                rows = rows.values()
            for row in (rows or ()):
                if isinstance(row, Mapping) and str(
                        row.get("venue_order_id") or "").strip() == vid:
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

    # ---------------------------------------------------------------- dwell
    #
    # THE FIRST UNMATCHED-FILL TIMESTAMP, per market, persisted.
    #
    # `l5_defensive_completion.decide` takes `stranded_since_t` and uses it to
    # run its timing gate as a DWELL measured from the moment the leg became
    # unmatched, instead of as a 45-second slot before the terminal. The slot
    # is narrower than the executor's own evaluation jitter -- 0300z stepped
    # T+524 -> T+587 and straddled it -- so the gate has to be anchored to the
    # position, and the anchor has to survive a restart.
    #
    # FAIL CLOSED EVERYWHERE. A market with inventory but NO stamp returns
    # None, which `decide` reads as "use the arm window": the pre-existing
    # behaviour, byte for byte. Nothing here invents a stranding time it
    # cannot source from a fill.

    def _note_stranding(self, market: str, before: D, after: D) -> None:
        """Maintain the stamp across one authoritative fill."""
        try:
            ss = self.state.setdefault("stranded_since", {})
            if after == 0:
                # flat: there is nothing stranded, so there is no clock
                ss.pop(str(market), None)
                return
            if before == 0:
                # this fill is the one that took the market off flat
                ss[str(market)] = float(self._now().timestamp())
            # already unmatched: keep the ORIGINAL stamp. The dwell measures
            # from when the imbalance opened, not from the latest fill, so an
            # EXTEND must not restart it.
        except BaseException:                                  # noqa: BLE001
            # a stamp we could not write is a stamp we do not have, and the
            # reader falls back to the arm window.
            pass

    def stranded_since(self, market: str) -> Optional[float]:
        """Epoch seconds of the first unmatched fill, or None.

        None whenever the answer is not authoritative: no stamp, an
        unreadable stamp, or a market the ledger says is flat. A flat market
        with a leftover stamp is a reconciliation failure, and returning None
        makes the caller behave exactly as it did before this field existed.
        """
        try:
            if self.residual(market) == 0:
                return None
            v = (self.state.get("stranded_since") or {}).get(str(market))
            if v is None:
                return None
            f = float(v)
            return f if f > 0 else None
        except BaseException:                                  # noqa: BLE001
            return None

    def reconcile_stranded(self) -> Dict[str, Any]:
        """Drop stamps for markets the ledger says are flat, and REPORT the
        markets that hold inventory with no stamp rather than inventing one.

        Called on the reconcile path so a restart, an adoption from the venue
        or a store written before this field cannot leave the dwell keyed to
        a position that no longer exists.
        """
        out: Dict[str, Any] = {"cleared": [], "stranded_without_stamp": [],
                               "kept": []}
        try:
            ss = self.state.setdefault("stranded_since", {})
            markets = set(ss)
            for key in list(self.state.get("positions") or {}):
                markets.add(str(key).split("|", 1)[0])
            for m in sorted(markets):
                try:
                    res = self.residual(m)
                except BaseException:                          # noqa: BLE001
                    continue
                if res == 0:
                    if ss.pop(m, None) is not None:
                        out["cleared"].append(m)
                elif m not in ss:
                    out["stranded_without_stamp"].append(m)
                else:
                    out["kept"].append(m)
        except BaseException as exc:                           # noqa: BLE001
            out["error"] = "%s" % type(exc).__name__
        return out

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

    def realized_current_utc_day(self, *, now: Optional[float] = None
                                 ) -> Optional[D]:
        """Account-wide settled P&L for the CURRENT UTC DAY, or None.

        THE POLICY CONFLICT THIS RESOLVES. `daily_realized_loss_halt_usd` is
        a DAILY halt, and `realized_today()` is not a daily figure: it sums
        attributed plus unattributed realized P&L over ALL of history. So the
        `$5` "daily" halt was really a `$5` LIFETIME cap that could never
        reset, while the separate `$20` cumulative limit read the very same
        number and was therefore dead code. Two limits with different names
        and different values cannot both be the policy while both measure one
        all-time total. This measures the window the limit names.

        STILL ACCOUNT-WIDE, DELIBERATELY. Every market that settled today
        counts, manual and L5 alike. This is NOT the L5-scoped rescoping in
        L5_LOSS_GATE_RESCOPING_REVIEW.md: narrowing the POPULATION would
        leave manual trading with no realized-loss ceiling at all. Narrowing
        the WINDOW leaves every trade on the account restrained, and the
        all-time account-wide total goes on restraining through the two `$20`
        cumulative gates, which this change does not touch. Historical losses
        are preserved and keep counting there.

        A CORRECTION KEEPS ITS ORIGINAL DAY. A later cost-basis correction
        rewrites an old row's `settlement_pnl` but not its `t`, so the loss
        stays on the day the position actually settled rather than moving to
        the day we measured it better. `cost_corrected_at` records the latter.

        FAIL CLOSED, NEVER ZERO. An unreadable row, a row with no timestamp,
        or a nonzero undated `realized_unattributed` each return None: a
        day's loss cannot be computed from a residual that belongs to no day,
        and an undated settlement cannot be ruled out as today's. A ceiling
        fed an unknown figure is not a ceiling, and the caller must refuse.
        """
        ts = (dt.datetime.now(dt.timezone.utc) if now is None
              else dt.datetime.fromtimestamp(float(now), dt.timezone.utc))
        day = ts.strftime("%Y-%m-%d")
        try:
            resid = D(str(self.state.get("realized_unattributed") or 0))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if resid != 0:
            return None          # belongs to no day: the day is unknown
        total = D(0)
        for _mkt, row in (self.state.get("retired_markets") or {}).items():
            if not isinstance(row, Mapping):
                return None
            stamp = str(row.get("t") or "")
            if not stamp:
                return None      # undated: cannot be excluded from today
            if not stamp.startswith(day):
                continue
            try:
                total += D(str(row.get("settlement_pnl") or 0))
            except (InvalidOperation, TypeError, ValueError):
                return None
        return total

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

    def account_wide_delta_realized(self) -> Optional[D]:
        """ACCOUNT-WIDE realized change since the baseline epoch T, or None.

        NOT L5-attributed. Every post-T settlement counts, whoever placed it,
        because the thing being bounded is what the account can still lose --
        and an unattributable loss is still a loss.

        WHY IT IS NOT JUST `realized_today() - anchor`. The anchor is frozen
        at T and must stay frozen, but a cost-basis correction can restate a
        leg that settled BEFORE T. That correction raises realized_today()
        without anything having happened after T, so the naive subtraction
        would credit pre-T restatements to the post-T window. Measured on this
        account: the naive delta reads +54.68840000 where the true post-T
        figure is +6.41460000 -- a $48.27380000 overstatement of headroom, and
        exactly the pre-T correction total. Pre-T correction deltas are
        therefore subtracted back out.

        FAILS CLOSED. No baseline, an unreadable anchor, or any correction
        record whose delta or period cannot be determined returns None. None
        is UNKNOWN and the gate must refuse on it, never read it as zero.
        """
        base = self.state.get("l5_baseline")
        if not isinstance(base, Mapping) or not base.get("t"):
            return None
        t0 = str(base["t"])
        anchor = finite_decimal(base.get("pre_baseline_realized_total"))
        now = finite_decimal(self.realized_today())
        if anchor is None or now is None:
            return None
        pre_t_corrections = D(0)
        for rec in (self.state.get("cost_basis_corrections") or []):
            if not isinstance(rec, Mapping):
                return None
            delta = finite_decimal(rec.get("delta"))
            market = rec.get("market")
            if delta is None or not market:
                return None
            row = (self.state.get("retired_markets") or {}).get(market)
            if not isinstance(row, Mapping):
                return None
            settled_at = str(row.get("t") or "")
            if not settled_at:
                return None
            if settled_at <= t0:
                pre_t_corrections += delta
        return now - anchor - pre_t_corrections

    def account_wide_delta_lines(self) -> Dict[str, Any]:
        """The delta and its parts, for the heartbeat. Reporting only.

        LIFETIME REPORTING IS PRESERVED: `account_wide_realized_lifetime` is
        carried here verbatim alongside the delta, so a delta ceiling can
        never be mistaken for the lifetime figure going away.
        """
        base = self.state.get("l5_baseline")
        anchored = isinstance(base, Mapping) and bool(base.get("t"))
        delta = self.account_wide_delta_realized()
        return {
            "baseline_t": (str(base.get("t")) if anchored else None),
            "baseline_anchor_realized": (
                str(base.get("pre_baseline_realized_total"))
                if anchored else None),
            "account_wide_realized_lifetime": str(self.realized_today()),
            "account_wide_delta_realized": (None if delta is None
                                            else str(delta)),
            "cost_basis_corrections": len(
                self.state.get("cost_basis_corrections") or []),
            "labels": (
                "delta is ACCOUNT-WIDE change since T, not L5-attributed, with "
                "pre-T cost corrections subtracted back out so a restatement "
                "of pre-T history cannot create post-T headroom. None is "
                "UNKNOWN and the gate refuses on it. The lifetime figure is "
                "reported here unchanged and is never replaced by the delta."),
        }

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

    def l5_realized_today(self, day_utc: Optional[str] = None) -> Optional[D]:
        """Attested L5 settlement P&L for ONE UTC DAY, or None without a baseline.

        Identical population and attestation rule to
        l5_realized_since_baseline -- post-baseline and
        l5_owner_attribution_verified only -- narrowed to settlements stamped
        with `day_utc` (default: today, UTC).

        It exists because the ceiling named L5_DAILY_LOSS_LIMIT used to read the
        cumulative post-baseline figure, so a "daily" budget never reset. This
        is the day-scoped measure that ceiling now reads. It does NOT replace
        or relax l5_realized_since_baseline, which the independent cumulative
        ceiling still reads in full.
        """
        base = self.state.get("l5_baseline")
        if not isinstance(base, Mapping) or not base.get("t"):
            return None
        t0 = str(base["t"])
        day = (day_utc or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"))
        total = D(0)
        for _mkt, row in (self.state.get("retired_markets") or {}).items():
            if not isinstance(row, Mapping):
                continue
            stamp = str(row.get("t") or "")
            if stamp <= t0:
                continue
            if not stamp.startswith(day):
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

    #: the four populations realized P&L is reported in. Named so a caller
    #: cannot silently conflate two of them.
    ACCOUNTING_BUCKETS = ("account_wide", "verified_l5", "manual",
                          "unknown_unattributed")

    def four_bucket_accounting(self) -> Dict[str, Any]:
        """Account-wide, verified L5, manual and unknown -- kept APART.

        THE IDENTITY THIS MAINTAINS:

            account_wide = verified_l5 + manual + unknown_unattributed

        and `unknown_unattributed` is what absorbs everything we cannot
        attribute, so the identity holds without any bucket being guessed.

        WHY `manual` IS USUALLY None, AND MUST BE. Manual P&L is not
        separately recorded anywhere -- this account is SHARED and the venue
        does not tell us who placed a historical fill. The only way to get it
        is as a residual, account_wide minus verified_l5 minus unknown, and
        that residual is meaningful ONLY when nothing is unknown. While
        anything is unattributable the manual share of it is unknown too, so
        this returns None with a reason rather than a number that looks
        measured. A residual reported as a measurement is how a $1/share
        upper bound becomes "the owner's trading losses".

        FAIL CLOSED. `verified_l5` is None without a baseline or on any
        unreadable settlement row, and None NEVER reads as zero. A caller
        that needs a figure must refuse on None; `gate_input()` below is the
        only sanctioned way to feed one to a limit.
        """
        account = None
        try:
            account = self.realized_today()
        except BaseException:                                  # noqa: BLE001
            account = None
        l5 = self.l5_realized_since_baseline()
        lines = self.l5_baseline_lines()

        # UNKNOWN: every post-T settlement we could not attest, plus the
        # whole pre-T total, whose L5 share is unknown by construction, plus
        # any leg still priced at the $1/share upper bound.
        unattested, unknown_total, unreadable = [], D(0), False
        for row in (lines.get("post_baseline_settlements_NOT_attested") or []):
            unattested.append(dict(row))
            try:
                unknown_total += D(str(row.get("pnl") or 0))
            except (InvalidOperation, TypeError, ValueError):
                unreadable = True
        pre = lines.get("pre_baseline_realized_total")
        pre_dec = None
        if pre is not None:
            try:
                pre_dec = D(str(pre))
                unknown_total += pre_dec
            except (InvalidOperation, TypeError, ValueError):
                unreadable = True

        placeholder_keys = sorted(self.state.get("leg_cost_basis_unknown")
                                  or [])
        manual, manual_reason = None, None
        if unreadable:
            manual_reason = "UNKNOWN_ROW_UNREADABLE"
        elif account is None:
            manual_reason = "ACCOUNT_WIDE_UNREADABLE"
        elif l5 is None:
            manual_reason = "NO_VERIFIED_L5_FIGURE"
        elif unknown_total != 0 or placeholder_keys:
            manual_reason = ("UNATTRIBUTED_RESIDUAL_NONZERO: manual is a "
                             "residual and is only meaningful once nothing "
                             "is unattributable")
        else:
            manual = account - l5
        return {
            "account_wide_realized": None if account is None else str(account),
            "verified_l5_realized": None if l5 is None else str(l5),
            "manual_realized": None if manual is None else str(manual),
            "manual_unavailable_reason": manual_reason,
            "unknown_unattributed_realized": (
                None if unreadable else str(unknown_total)),
            # THE ACTIONABLE PART, BESIDE THE CONSERVATIVE ONE.
            #
            # `unknown_unattributed_realized` deliberately folds in the
            # WHOLE pre-baseline total, because its L5 share is unknown by
            # construction. That makes it the right number for "how much
            # of this account can we not attribute" and the WRONG number
            # for "how much is still fixable": read beside an
            # L5-ledger-scoped `account_wide_realized`, the two look like
            # an unexplained gap, and that reading cost a review cycle.
            #
            # So the post-baseline component is reported on its own. It is
            # the set an attestation can still close, and it is the only
            # one a live probe can land in.
            "unknown_post_baseline_only": (
                None if unreadable else str(unknown_total - (pre_dec or D(0)))),
            "unknown_detail": {
                "post_baseline_settlements_not_attested": unattested,
                "pre_baseline_total_l5_share_unknown": (
                    None if pre_dec is None else str(pre_dec)),
                "legs_priced_at_one_dollar_upper_bound": placeholder_keys,
                "count": len(placeholder_keys),
            },
            # THE IDENTITY HOLDS ONLY WHERE THE POPULATIONS MATCH, and
            # it is stated with its precondition because it was stated
            # without one and read as a balance that should tie out.
            # `account_wide_realized` is L5-ledger-scoped;
            # `unknown_unattributed_realized` includes pre-baseline
            # history that is outside that scope. So the identity ties
            # only when the pre-baseline total is zero, and
            # `identity_holds` false below is ordinarily that, not a fault.
            "identity": ("account_wide = verified_l5 + manual + "
                         "unknown_unattributed, and ONLY where the "
                         "pre-baseline total is zero: that total is "
                         "inside unknown_unattributed and outside "
                         "account_wide, so a non-zero one breaks the "
                         "identity by construction rather than by error"),
            "identity_precondition_met": (
                None if unreadable else bool(pre_dec in (None, D(0)))),
            "identity_holds": bool(
                account is not None and l5 is not None and manual is not None
                and not unreadable and account == l5 + manual + unknown_total),
            "labels": ("four populations, never collapsed. A None is UNKNOWN "
                       "and never zero. manual is a RESIDUAL, reported only "
                       "when nothing is unattributable, because this account "
                       "is SHARED and the venue does not say who placed a "
                       "historical fill. A $1/share upper bound is an "
                       "estimate, not a verified realized loss."),
        }

    def gate_input(self, bucket: str) -> Optional[D]:
        """The ONE sanctioned way to feed a realized figure to a limit.

        Returns None -- never zero -- when the named bucket is unknown, and
        the caller must refuse on None. It exists so that which population a
        loss ceiling measures is an explicit, named, testable choice at the
        call site instead of whatever `realized_today()` happens to hold.

        It CHANGES NO POLICY on its own: the account-wide gates still read
        "account_wide" until the protected rescoping in
        L5_LOSS_GATE_RESCOPING_REVIEW.md is applied by a human with write
        access. This is the mechanism that change needs, landed separately
        from the change itself.
        """
        if bucket not in self.ACCOUNTING_BUCKETS:
            return None
        acc = self.four_bucket_accounting()
        raw = acc.get({"account_wide": "account_wide_realized",
                       "verified_l5": "verified_l5_realized",
                       "manual": "manual_realized",
                       "unknown_unattributed":
                           "unknown_unattributed_realized"}[bucket])
        if raw is None:
            return None
        try:
            return D(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            return None

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

    def realized_by_origin(self) -> Dict[str, Any]:
        """Settled results split BTC15M / BTC5M / MANUAL / UNKNOWN, beside
        the account-wide lines exactly as `realized_lines` reports them.

        READ-ONLY. The split is over the `retired_markets` rows this ledger
        still holds; the account-wide figures are the ledger's own and are
        what every loss limit reads. Nothing here feeds a gate.
        """
        out = ORIGIN.realized_by_origin(
            self.state.get("retired_markets") or {}, own=OWN_STRATEGY,
            seen=self.state.get("foreign_orders_seen") or {})
        out["account_wide"] = {
            "realized_attributed": str(
                D(str(self.state.get("realized_attributed") or 0))),
            "realized_unattributed": str(
                D(str(self.state.get("realized_unattributed") or 0))),
            "labels": ("the account-wide lines, unchanged by the split "
                       "above. The loss limits read these.")}
        return out

    def position_origins(self) -> Dict[str, Any]:
        """Every held position leg with its origin and cost-basis kind.

        READ-ONLY. A leg this ledger booked from its own order fills is
        this strategy's at an authenticated cost. A leg in
        `adopted_unattributed` came from the venue's position list with no
        price, is held at $1 a contract, and is labelled from evidence only:
        the owner's attestation, or a registered strategy's order the venue
        listed in that market. Otherwise UNKNOWN.
        """
        unattributed = self.state.get("adopted_unattributed") or {}
        unknown = set(str(k) for k in
                      (self.state.get("leg_cost_basis_unknown") or []))
        seen = self.state.get("foreign_orders_seen") or {}
        lc = self.state.get("leg_cost") or {}
        legs: Dict[str, Any] = {}
        for key, qty in sorted((self.state.get("positions") or {}).items()):
            market = str(key).rsplit("|", 1)[0]
            own = str(key) not in unattributed and str(key) not in unknown
            got = ORIGIN.origin_of_position(
                market, own=OWN_STRATEGY, booked_by_own_fills=own,
                other_strategy_order_seen=seen.get(market))
            legs[str(key)] = dict(got, qty=str(qty),
                                  booked_cost_usd=(None if key not in lc
                                                   else str(lc.get(key))))
        return {"legs": legs,
                "labels": ("booked_cost_usd of an UPPER_BOUND leg is $1 a "
                           "contract, a bound and not a cost. Every leg "
                           "here is in account-wide exposure whatever its "
                           "label.")}

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

    def correct_cost_basis(self, market: str, side: str, *,
                           correction_id: str,
                           expect: Mapping[str, Any],
                           measured_cost: D,
                           measured_fees: D,
                           evidence: Mapping[str, Any],
                           restate: bool = False
                           ) -> Dict[str, Any]:
        """Replace ONE leg's $1/share PLACEHOLDER cost with a measured one.

        WHY A SEPARATE VERB. `correct_settled_market` restates a settlement
        OUTCOME: it recomputes pnl from `row["cost_total"]` and never WRITES
        it. A cost correction also necessarily moves realized TOWARD zero,
        which that method refuses as CORRECTION_WOULD_RELAX_LOSS_HALT. That
        guard is correct and is NOT relaxed, inherited or shared here --
        `correct_settled_market` is untouched. This is a different operation
        with its own, narrower guards.

        WHAT IT IS NOT. It cannot clear HALT, cannot change a limit, and
        cannot attribute a leg to anyone. It writes no owner field, so
        `verified_l5` stays whatever it was and `manual` stays UNKNOWN. A
        corrected cost is not evidence of who traded.

        THE CALLER CANNOT ASSERT A COST. `measured_cost` must reconcile to
        the sum of the named `evidence` fills, or the correction is refused.
        The caller supplies venue trade ids and arithmetic is re-derived from
        them; a figure that merely looks plausible is rejected.
        """
        market = canonical_market(market, doing="correcting a cost basis")
        side = str(side or "").strip().upper()
        if side not in ("UP", "DOWN"):
            return {"corrected": False, "reason": "BAD_SIDE", "side": side}
        key = "%s|%s" % (market, side)

        # 1. idempotent by correction id
        done = {r.get("id") for r in
                (self.state.get("cost_basis_corrections") or [])}
        if correction_id in done:
            return {"corrected": False, "key": key,
                    "reason": "COST_CORRECTION_ALREADY_APPLIED",
                    "correction_id": correction_id}
        # 2. the leg must exist as a retired market
        row = (self.state.get("retired_markets") or {}).get(market)
        if not row:
            return {"corrected": False, "key": key,
                    "reason": "MARKET_NOT_RETIRED"}
        # 3. never correct twice -- UNLESS this is an explicit RESTATEMENT.
        #    A first correction replaces an upper bound with a measurement
        #    and may only ever come DOWN. A restatement revisits a figure
        #    this verb itself got wrong, so it must be able to move EITHER
        #    way: 13 of the 44 legs corrected on 2026-10-16 were costed at
        #    the YES price on a DOWN leg and need their cost RAISED. The
        #    measurement is still re-derived from the named venue fills
        #    below (guard 6), so a restatement cannot assert a number
        #    either.
        restate = bool(restate)
        if row.get("cost_superseded") and not restate:
            return {"corrected": False, "key": key,
                    "reason": "COST_ALREADY_CORRECTED"}
        if restate and not row.get("cost_superseded"):
            # nothing has been corrected here, so there is nothing to
            # restate. Taking the first correction's one-directional path
            # by mistake is how an upper bound gets raised.
            return {"corrected": False, "key": key,
                    "reason": "NOTHING_TO_RESTATE"}
        # 4. the ledger must hold what the evidence describes
        mismatch = {k: {"expected": str(v), "found": str(row.get(k))}
                    for k, v in dict(expect).items()
                    if str(row.get(k)) != str(v)}
        if mismatch:
            return {"corrected": False, "key": key,
                    "reason": "LEDGER_DOES_NOT_MATCH_EVIDENCE",
                    "mismatch": mismatch}
        # 5. a partial walk cannot establish a cost. Missing is not complete.
        walk = dict((evidence or {}).get("activity_walk") or {})
        if walk.get("complete") is not True:
            return {"corrected": False, "key": key,
                    "reason": "ACTIVITY_WALK_INCOMPLETE",
                    "activity_walk": walk}
        # 6. RE-DERIVE the cost from the named fills. The caller cannot
        #    assert it, and a fill whose qty or price will not parse makes
        #    the whole correction unreconciled rather than smaller.
        fills = list((evidence or {}).get("per_fill") or ())
        if not fills:
            return {"corrected": False, "key": key,
                    "reason": "COST_NOT_RECONCILED", "detail": "NO_FILLS"}
        qty_sum = D(0)
        cost_sum = D(0)
        fee_sum = D(0)
        ids = []
        for f in fills:
            q = finite_decimal(f.get("qty"))
            px = finite_decimal(f.get("price"))
            fee = finite_decimal(f.get("fee"))
            tid = f.get("trade_id")
            if q is None or px is None or fee is None or not tid:
                return {"corrected": False, "key": key,
                        "reason": "COST_NOT_RECONCILED",
                        "detail": "FILL_UNREADABLE"}
            qty_sum += q
            # OUR COST PER SHARE, from the market's single quoted price.
            # PMUS quotes ONE number per market and it is ALWAYS the UP/YES
            # price, so a DOWN fill's cost is the COMPLEMENT. This
            # re-derivation had the same defect the audit's establish_basis
            # did: it charged the YES price on both sides, which on the 44
            # legs corrected on 2026-10-16 understated the correct total by
            # 30.75140000 and over-corrected 13 DOWN legs by 15.79500000.
            # The rule lives in exactly two places -- here and
            # l5_cost_basis_audit.own_price -- and a test pins them equal.
            cost_sum += q * (px if side == "UP" else (D(1) - px))
            fee_sum += fee
            ids.append(str(tid))
        want_cost = finite_decimal(measured_cost)
        want_fees = finite_decimal(measured_fees)
        if want_cost is None or want_fees is None:
            return {"corrected": False, "key": key,
                    "reason": "COST_UNREADABLE"}
        if want_cost != cost_sum or want_fees != fee_sum:
            return {"corrected": False, "key": key,
                    "reason": "COST_NOT_RECONCILED",
                    "supplied_cost": str(want_cost),
                    "derived_cost": str(cost_sum),
                    "supplied_fees": str(want_fees),
                    "derived_fees": str(fee_sum)}
        # 7. the venue quantity must equal the ledger quantity. This is what
        #    PRESERVES a gross-vs-net leg as UNKNOWN by construction, rather
        #    than by anyone remembering to exclude it.
        ledger_qty = finite_decimal(row.get("%s_qty" % side.lower()[:2]))
        if ledger_qty is None:
            ledger_qty = finite_decimal(
                row.get("up_qty") if side == "UP" else row.get("dn_qty"))
        if ledger_qty is None:
            return {"corrected": False, "key": key,
                    "reason": "LEDGER_QTY_UNREADABLE"}
        if qty_sum != ledger_qty:
            return {"corrected": False, "key": key,
                    "reason": "VENUE_QTY_NE_LEDGER_QTY",
                    "venue_qty": str(qty_sum),
                    "ledger_qty": str(ledger_qty)}
        # 8. ONE-DIRECTIONAL. It may only ever replace an upper bound with a
        #    smaller measurement, never inflate a cost.
        # THIS LEG'S OWN COST, and the market total it belongs to. A row
        # carries BOTH when the market was two-sided:
        # cpc-btc-updown-15m-2026-10-15-1945z retired with up_cost 0.4600 and
        # dn_cost 1.0000, so cost_total 1.4600 is the PAIR. Writing a
        # measured DOWN cost over cost_total would have erased the UP leg's
        # real 0.4600 and flattered realized. The side's own field is
        # corrected and the total is RECOMPUTED from the two sides.
        own_field = "up_cost" if side == "UP" else "dn_cost"
        other_field = "dn_cost" if side == "UP" else "up_cost"
        other_qty = finite_decimal(
            row.get("dn_qty" if side == "UP" else "up_qty")) or D(0)
        was_total = finite_decimal(row.get("cost_total"))
        own_now = finite_decimal(row.get(own_field))
        other_now = finite_decimal(row.get(other_field))
        if other_qty != 0 and other_now is None:
            # the other side HOLDS inventory and its cost cannot be read, so
            # this leg's cost cannot be separated from the pair's. Writing
            # cost_total here would erase a cost nobody can restate.
            return {"corrected": False, "key": key,
                    "reason": "OTHER_SIDE_COST_UNREADABLE",
                    "other_side_qty": str(other_qty)}
        if (own_now is not None and other_now is not None
                and was_total is not None
                and own_now + other_now != was_total):
            return {"corrected": False, "key": key,
                    "reason": "ROW_COSTS_DO_NOT_RECONCILE",
                    "up_cost": str(row.get("up_cost")),
                    "dn_cost": str(row.get("dn_cost")),
                    "cost_total": str(was_total)}
        # THIS LEG'S OWN pre-state, and the market total it belongs to.
        was_cost = own_now if own_now is not None else was_total
        new_total = want_cost + (other_now if other_now is not None else D(0))
        if was_cost is None:
            return {"corrected": False, "key": key,
                    "reason": "COST_UNREADABLE"}
        if want_cost == was_cost:
            return {"corrected": False, "key": key,
                    "reason": "COST_ALREADY_CORRECT",
                    "was": str(was_cost), "supplied": str(want_cost)}
        if want_cost > was_cost and not restate:
            # a FIRST correction may only replace an upper bound with a
            # smaller measurement. A restatement is exempt, and only a
            # restatement.
            return {"corrected": False, "key": key,
                    "reason": "COST_CORRECTION_MUST_REDUCE",
                    "was": str(was_cost), "supplied": str(want_cost)}
        # 9. a negative or non-finite cost is not a measurement
        if want_cost < 0:
            return {"corrected": False, "key": key,
                    "reason": "COST_UNREADABLE", "supplied": str(want_cost)}
        # 10. the same fills may not be spent twice across legs
        spent = set()
        for rec in (self.state.get("cost_basis_corrections") or []):
            # A RESTATEMENT RE-READS THE SAME FILLS. Ids this very leg
            # already consumed are its own, so they are not a clash; ids
            # consumed by ANY OTHER leg still are, which is the double-spend
            # this guard exists to stop.
            if rec.get("key") == key:
                continue
            spent.update(rec.get("trade_ids") or ())
        clash = sorted(set(ids) & spent)
        if clash:
            return {"corrected": False, "key": key,
                    "reason": "TRADE_ID_ALREADY_CONSUMED",
                    "trade_ids": clash}

        before = self.realized_lines()
        up = D(str(row.get("up_qty") or 0))
        dn = D(str(row.get("dn_qty") or 0))
        v = finite_decimal(row.get("yes_value"))
        redeem = (up * v + dn * (D(1) - v)) if v is not None else None
        pnl = (redeem - new_total) if redeem is not None else None
        delta = (pnl - D(str(row.get("settlement_pnl") or 0))
                 if pnl is not None else was_cost - want_cost)

        if not row.get("cost_superseded"):
            row["cost_superseded"] = dict(row)    # verbatim, never edited
        else:
            # A RESTATEMENT NEVER OVERWRITES THE ORIGINAL. cost_superseded
            # keeps the row as it was BEFORE the first correction, for good,
            # and each later state is appended beside it.
            row.setdefault("cost_restatements", []).append(
                {k: v for k, v in row.items()
                 if k not in ("cost_superseded", "cost_restatements")})
        if own_now is not None:
            # keep the side field and the total in step; a stale side field
            # beside a corrected total is a row that disagrees with itself.
            row[own_field] = str(want_cost)
        row["cost_total"] = str(new_total)
        row["cost_provenance"] = "MEASURED_FROM_VENUE_FILLS"
        row["fees_total"] = str(want_fees)
        if pnl is not None:
            row["settlement_pnl"] = str(pnl)
            row["redemption_total"] = str(redeem)
        row["cost_corrected_at"] = dt.datetime.now(
            dt.timezone.utc).isoformat()
        row["cost_correction_id"] = correction_id
        self.state["realized_attributed"] = str(
            D(str(self.state.get("realized_attributed") or 0)) + delta)
        self.state["realized_today"] = str(
            D(str(self.state.get("realized_today") or 0)) + delta)
        # `realized_unattributed` IS NOT TOUCHED, for the same reason
        # correct_settled_market leaves it alone: inventing an unattributed
        # line here would be a phantom credit in another column.
        self.state.setdefault("cost_basis_corrections", []).append({
            "id": correction_id, "key": key, "market": market, "side": side,
            "t": dt.datetime.now(dt.timezone.utc).isoformat(),
            "was_cost_total": str(was_cost),
            "now_cost_total": str(want_cost),
            "restatement": restate,
            "market_cost_total_after": str(new_total),
            "fees_total": str(want_fees),
            "delta": str(delta),
            "qty": str(qty_sum),
            "trade_ids": ids,
            "cost_provenance": "MEASURED_FROM_VENUE_FILLS",
            "activity_walk": walk,
            "ownership_unchanged": True,
        })
        self.save()
        return {"corrected": True, "key": key,
                "correction_id": correction_id,
                "was_cost_total": str(was_cost),
                "now_cost_total": str(want_cost),
                "delta": str(delta),
                "trade_ids": ids,
                "realized_before": before,
                "realized_after": self.realized_lines(),
                "halt_unchanged": True,
                "labels": ("a COST restatement. It attributes nothing: no "
                           "owner field is written, so verified_l5 and manual "
                           "are exactly as they were. HALT is not read and "
                           "not cleared.")}

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
                      mode: Optional[str] = None,
                      provenance: Optional[str] = None,
                      action_key: Optional[str] = None) -> None:
        rec = {
            "t": self._now().isoformat(),
            "market": intent.get("market"), "side": intent.get("side"),
            "price": str(intent.get("price")), "qty": str(intent.get("qty")),
            # RUN PROVENANCE, stamped at submit. The inventory store is a file
            # on a volume that outlives the process, so without this a later
            # reconciliation cannot tell this run's orders from Probe 04's.
            "session_id": session_id,
            "mode": mode,
            # WHO DECIDED THIS ORDER, stamped at the only moment it is known
            # for certain. `inventory_origin` prefers this over enumerating
            # the controller's coid namespace; absent, it falls back to that
            # enumeration, so an older ledger still attributes correctly.
            "provenance": provenance,
            "action_key": action_key,
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
        # WHEN DID THIS MARKET BECOME UNMATCHED? Read the residual either
        # side of the only write that can change it, so the stamp is a
        # consequence of an authoritative fill and never of a clock read
        # somewhere else. `apply_fill` is the single writer of `positions`
        # on the fill path, which is why this belongs here and nowhere else.
        _res_before = self.residual(own_market)
        # THE POSITION UPDATE CAN ARRIVE BEFORE THE FILL. 0415z, 2026-11-11:
        # the venue's position showed our 0.44 UP at 04:15:20.8, the order
        # row that prices it at 0.32 was read at 04:15:22.5. In between the
        # 0.44 was adopted at the $1/share bound; the fill was then ADDED to
        # it (0.88 against the venue's 0.44), the next reconciliation
        # adopted again, and settlement priced the bound: -$0.4400 booked
        # against -$0.1408 paid. `absorbed` is the part of this fill the
        # adopted quantity already contains: it replaces that much of the
        # bound with the measured cost and adds no quantity.
        absorbed = self.absorb_into_adopted(k, qty, own, coid)
        self.state["positions"][k] = str(
            D(str(self.state["positions"].get(k, 0))) + qty - absorbed)
        self._note_stranding(own_market, _res_before,
                             self.residual(own_market))
        # both of these are CASH OUT in the leg's own terms now. They used to
        # take the wire number, which credits a short instead of charging it.
        self.state["committed"][own_market] = str(
            D(str(self.state["committed"].get(own_market, 0))) + own * qty)
        lc = self.state.setdefault("leg_cost", {})
        lc[k] = str(D(str(lc.get(k, 0))) + own * qty
                    - absorbed * UNKNOWN_PRICE_WORST_CASE)
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


    def absorb_into_adopted(self, key: str, qty: D, own_px: D,
                            coid: Optional[str]) -> D:
        """How much of this fill the ADOPTED position already contains.

        Zero unless the leg was adopted from the venue's position with no
        cost basis and this is a fill of an order WE track -- an anonymous
        fill proves nothing about what the venue's number was made of. The
        absorbed quantity comes off `adopted_unattributed`; when none is
        left the leg is no longer listed in `leg_cost_basis_unknown`,
        because every share of it now has a measured cost. Each absorption
        is appended to `adoption_attributions`, so the bound that was
        replaced, and by which order, stays on the record.

        IF IT IS WRONG IT IS WRONG IN THE SAFE DIRECTION. A fill that
        happened AFTER the adopted read is not in the adopted quantity;
        absorbing it leaves our book short of the venue's, the next
        reconciliation sees that and adopts at the bound again.
        """
        if not coid:
            return D(0)
        book = self.state.get("adopted_unattributed") or {}
        if key not in book:
            return D(0)
        try:
            have = D(str(book.get(key) or 0))
        except Exception:                                      # noqa: BLE001
            return D(0)
        take = min(D(str(qty)), have)
        if take <= 0:
            return D(0)
        left = have - take
        if left > 0:
            book[key] = str(left)
        else:
            book.pop(key, None)
            unknown = self.state.get("leg_cost_basis_unknown") or []
            if key in unknown:
                unknown.remove(key)
        self.state.setdefault("adoption_attributions", []).append({
            "key": key, "coid": str(coid), "qty": str(take),
            "own_price": str(own_px),
            "bound_replaced_usd": str(take * UNKNOWN_PRICE_WORST_CASE),
            "measured_usd": str(take * D(str(own_px))),
            "still_at_the_bound": str(max(left, D(0))),
            "at": dt.datetime.now(dt.timezone.utc).isoformat()})
        return take

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

    def lots(self, market: str, side: str) -> List[Dict[str, str]]:
        """This leg's fills as INDIVIDUAL LOTS, dearest first.

        WHY LOTS AND NOT THE AVERAGE. `leg_cost` is one scalar per side, so a
        leg built from a cheap fill and an expensive one reports only their
        blend -- and a blend can be a price that neither lot ever traded at.
        The live 2026-11-02 2300z book is the case: it held 3.3600 UP at an
        average own cost of 0.605, which is exactly 2 x 1.68 and exactly
        (0.21 + 1.00) / 2 -- the venue-confirmed lot of 1.68 at own 0.2100
        blended with a second lot carrying the 1.00 unattested-basis
        placeholder. Priced on that average every completion in the window
        reads 1.77-1.94 and the window disappears; priced on the real lot it
        contributes six admissible completions.

        NO NEW STATE. `apply_fill` already appends every fill to
        `fill_provenance[market]["entries"]` with its side, quantity and
        actual cost, and that structure is already persisted, so this is a
        READER over a ledger that exists rather than a second copy of it.

        ATTESTATION IS THE POINT. Only a real fill writes an entry here, so
        quantity that arrived from `reconcile_from_venue`'s net has NO lot --
        which is exactly how an unattested placeholder is told apart from
        shares somebody actually bought. Compare `attested_qty` against
        `position` to find it.

        Dearest first, because a gate that must choose an attribution should
        choose the conservative one: matching the dearest lots first can
        never make a completion look cheaper than it is.
        """
        market = canonical_market(market, doing="reading lots")
        side = canonical_side(side, doing="reading lots")
        prov = (self.state.get("fill_provenance") or {}).get(market) or {}
        out: List[Dict[str, str]] = []
        for e in (prov.get("entries") or ()):
            if not isinstance(e, Mapping):
                continue
            if str(e.get("side") or "") != side:
                continue
            try:
                q = D(str(e.get("qty")))
                c = D(str(e.get("actual_cost")))
            except BaseException:                              # noqa: BLE001
                continue
            if q <= 0:
                continue
            out.append({"qty": str(q), "cost_usd": str(c),
                        "own_px": str(c / q),
                        "coid": (None if e.get("coid") is None
                                 else str(e.get("coid"))),
                        "l5_owner_verified": bool(
                            e.get("l5_owner_verified"))})
        out.sort(key=lambda r: D(str(r["own_px"])), reverse=True)
        return out

    def attested_qty(self, market: str, side: str) -> D:
        """Quantity on this leg that a real fill accounted for.

        `position` minus this is quantity with no cost basis of its own --
        the 2300z class. A completion priced against it would be priced
        against a placeholder, so callers that must not do that compare the
        two and refuse rather than averaging.
        """
        return sum((D(str(r["qty"])) for r in self.lots(market, side)), D(0))

    def unattested_qty(self, market: str, side: str) -> D:
        """`position` that no fill explains. Zero on a healthy leg."""
        return self.position(market, side) - self.attested_qty(market, side)

    def basis_attestation(self, market: str, side: str) -> Dict[str, Any]:
        """Is this leg's cost basis a price a fill actually stated?

        TWO WAYS IT IS NOT, and both are real and both have happened.

        THE $1/SHARE PLACEHOLDER. `adopt_venue_position` books an adopted
        net at `UNKNOWN_PRICE_WORST_CASE` -- $1 a share, the most a share can
        cost -- and lists the key in `leg_cost_basis_unknown`. That is the
        right conservative bound for a LOSS estimate and the wrong number for
        pricing a pair: it is not an observed cost, and the module's own
        comment says no later reader should mistake it for one.

        QUANTITY NO FILL EXPLAINS. Shares can also arrive without any cost
        entry at all, in which case the average silently falls.

        WHY THIS IS A GATE AND NOT A REPORT. `leg_cost_basis_unknown` was
        read in five reporting paths and consulted by NO gate, so the
        completion path would price a hedge against the placeholder. The
        live 2026-11-02 2300z book is the case: 3.3600 UP at an average own
        cost of 0.605, which is exactly (0.21 + 1.00) / 2 -- the
        venue-confirmed lot of 1.68 at own 0.2100 blended with a placeholder
        lot. On that blend every completion in the window prices above
        redemption and is refused; on the real lot one prices at 0.9500.
        Same inventory, opposite decisions, and neither the engine nor the
        operator was told which number was being used.

        REPORTING ONLY HERE. This answers the question; the refusal is the
        caller's.
        """
        market = canonical_market(market, doing="attesting a basis")
        side = canonical_side(side, doing="attesting a basis")
        k = "%s|%s" % (market, side)
        placeholder = k in (self.state.get("leg_cost_basis_unknown") or [])
        pos = self.position(market, side)
        att = self.attested_qty(market, side)
        unatt = pos - att
        out: Dict[str, Any] = {
            "market": market, "side": side,
            "position": str(pos), "attested_qty": str(att),
            "unattested_qty": str(unatt),
            "placeholder_basis": bool(placeholder),
            "lots": len(self.lots(market, side)),
        }
        if pos <= 0:
            out["ok"] = True
            out["reason"] = None
            return out
        if placeholder:
            out["ok"] = False
            out["reason"] = "PLACEHOLDER_BASIS"
            return out
        if unatt > 0:
            out["ok"] = False
            out["reason"] = "QUANTITY_WITHOUT_ATTESTED_COST"
            return out
        out["ok"] = True
        out["reason"] = None
        return out

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
        # IDENTITY BEFORE ARITHMETIC, and before any state is written.
        # The caller supplies `side` from its own bookkeeping; booking the
        # wrong one is how the 1445z phantom DOWN leg was created. See
        # `submitted_identity_mismatch`.
        _bad = submitted_identity_mismatch(self.state, coid, market, side)
        if _bad:
            self.mark_uncertain(
                "order progress for %s disagrees with what was submitted "
                "under it (%s); refusing to book it" % (coid, _bad))
            return {"coid": coid, "refused": _bad, "delta": "0",
                    "booked": "0", "terminal": bool(prog.get("terminal")),
                    "state": prog.get("state"),
                    "labels": ("nothing was booked and the store is now "
                               "uncertain, so exposure-increasing "
                               "submissions fail closed until a "
                               "reconciliation clears it")}
        # PERSIST THE LINK FIRST. A terminal order is popped out of
        # `live_orders` below, so this is the last moment its venue id is
        # readable -- and the duplicate guard needs it to outlive the order.
        _vid_now = venue_order_id_for_coid(self.state, coid)
        if _vid_now:
            self.state.setdefault(VENUE_ORDER_LINK, {})[coid] = _vid_now
        was_live = coid in (self.state.get("live_orders") or {})
        already = self.applied_fill_qty(coid)
        cum = D(str(prog.get("cum_qty") or 0))
        delta = cum - already
        out = {"coid": coid, "already": str(already), "cum": str(cum),
               "delta": str(delta), "terminal": bool(prog.get("terminal")),
               "state": prog.get("state")}
        if delta > 0:
            # THE SAME VENUE ORDER, TWICE. `already` above is keyed on the
            # CLIENT order id, so it reads 0 for a second coid pointing at an
            # order whose fills are already booked -- which is exactly how
            # 0230z booked 1.91 contracts twice. Refuse, book nothing, and
            # mark the store uncertain so submissions fail closed until the
            # duplicate identity is resolved.
            _dup = duplicate_venue_order_coid(self.state, coid)
            if _dup:
                self.mark_uncertain(
                    "venue order %s is already booked under %s; refusing to "
                    "book it again under %s"
                    % (venue_order_id_for_coid(self.state, coid), _dup, coid))
                out["refused"] = "DUPLICATE_VENUE_ORDER"
                out["already_booked_under"] = _dup
                out["venue_order_id"] = venue_order_id_for_coid(
                    self.state, coid)
                self.save()
                return out
            # avgPx is the VENUE's number, so it is WIRE and gets converted
            self.apply_fill(market, side, delta,
                            D(str(prog.get("avg_px") or 0)), coid=coid,
                            basis=WIRE)
            self.state.setdefault("filled_qty", {})[coid] = str(cum)
            fees = self.state.setdefault("fees", {})
            # ABSENT IS NOT ZERO, and this map's consumer already relies on
            # that: `l5_only_attested_net` refuses FEE_EVIDENCE_MISSING for an
            # order with NO recorded commission and accepts a RECORDED zero as
            # a fact. Writing "0" for a commission the venue never stated
            # turned the unknown into a fact and let an unknown net reach a
            # risk gate -- the thing that docstring exists to prevent. So the
            # key is written only when the venue actually stated a figure.
            _c = prog.get("commission")
            if _c is not None:
                fees[coid] = str(D(str(_c)))
            # THE CALIBRATION RECORD'S FILL AND ITS ACTUAL FEE, from the one
            # place the venue's own numbers are booked.
            #
            # IDEMPOTENT BY INHERITANCE: this sits inside `delta > 0`, so a
            # second poll of the same cumQuantity books nothing and records
            # nothing. Partial fills arrive as successive deltas and are
            # recorded as successive fills, which is what a fill model needs.
            #
            # `commission` is the VENUE's figure, so it fills the ACTUAL fee
            # column -- the one that has read PENDING on all 10,741 rows
            # because nothing ever called this. A commission the venue did
            # not state is passed as None and recorded as unknown; a zero is
            # a FACT and is recorded as zero, because this venue credits
            # rebates separately and a zero commission is not a rebate.
            self._calib_safe(
                "on_fill", coid, qty=delta,
                price=D(str(prog.get("avg_px") or 0)),
                cumulative_qty=cum, remaining=D(str(prog.get("leaves_qty")
                                                    or 0)))
            _comm = prog.get("commission")
            self._calib_safe("on_fees_actual", coid,
                             fee_usd=(None if _comm is None else D(str(_comm))),
                             source="venue_order_progress.commission")
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
            # VENUE-CONFIRMED TERMINAL. This is the only place in the money
            # path that learns an order's end FROM THE VENUE, which is why
            # `terminal_state` could never be PRESENT before: the shadow path
            # passes venue_confirmed=False by construction, correctly, and
            # nothing else called it. A modelled observation is still never
            # promoted to a venue-confirmed fill -- this fires only on
            # `prog["terminal"]`, which is the venue saying the order is done.
            self._calib_safe("on_terminal", coid,
                             state=str(prog.get("state") or "TERMINAL"),
                             reason="venue_order_progress",
                             venue_confirmed=True)
            # AFTER the fill is booked and the row released, never before:
            # the venue has now answered for this order in full.
            _cleared = self.resolve_cancel_uncertainty(coid, prog)
            if _cleared is not None:
                out["uncertainty_cleared"] = _cleared
        self.save()
        return out

    def _calib_safe(self, hook: str, coid: str, **kw: Any) -> None:
        """Call one recorder hook, and never let it matter.

        A recorder fault must not fail a submission the venue has already
        accepted, nor corrupt a reconciliation that has already booked a
        fill: losing an order's identity over telemetry is a far worse
        outcome than losing the telemetry. So every fault is swallowed, the
        absence of a recorder is normal, and this returns None always. It
        has no veto and cannot acquire one.
        """
        cal = getattr(self, "fill_calibration", None)
        if cal is None:
            return
        try:
            fn = getattr(cal, hook, None)
            if callable(fn):
                fn(coid, **kw)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise

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
        _cleared = self.resolve_pending_uncertainty(
            coid, out, venue_terminal=bool(prog.get("terminal")))
        if _cleared is not None:
            out = dict(out, uncertainty_cleared=_cleared)
        self.save()
        return dict(out, coid=coid, resolved=True, market=market, side=side,
                    labels=("final fill booked from the venue's own order "
                            "row; this is the reconstruction that resolves "
                            "a lost cost basis, not an assumption about it"))

    def resolve_pending_uncertainty(self, coid: str,
                                    booked: Mapping[str, Any], *,
                                    venue_terminal: bool = False
                                    ) -> Optional[Dict[str, Any]]:
        """End a reconciliation mark that was ONLY about parked orders, once
        the venue has answered for every one of them. The record, or None.

        WHAT WAS WRONG, 2026-11-10 1300z, live. The order-age deadline
        cancelled DOWN order DMSGYZH013ZD at 13:02:14.3 and the deadman's
        re-reads still saw it working. A reconciliation ran in the SAME tick,
        found it gone from the venue's open list, parked it, and replaced the
        deadman's mark with its own: "unbooked terminal orders [...]". The
        venue's order row said CANCELED, nothing filled, at 13:02:18.3 -- and
        the mark stood until the next reconciliation at 13:02:29.7.

        That is `resolve_cancel_uncertainty`'s defect one writer over: the
        mark asked whether those orders were finished, the venue said yes,
        and nothing listened. It was not covered there because a
        reconciliation's mark carried no cause.

        EVERY CONDITION MUST HOLD:

          * the mark in force is byte-for-byte the one this cause was
            recorded with, so anything marked since is left alone;
          * it was about parked orders ONLY -- reconciliation records a cause
            only when there was no divergence, no incoherent book and no
            quarantine;
          * the venue's OWN order row said terminal. A parked order is
            booked as final on any readable row, because leaving the open
            list is terminal there -- but a row still reading NEW is the
            venue's lag, not its answer, and this shortcut waits for the
            answer;
          * this order is one of them, and it resolved with NOTHING BOOKED.
            A fill that arrives here changes the positions the
            reconciliation had just verified, so it is left for the next
            reconciliation to confirm: that case forfeits the shortcut for
            the whole mark;
          * every order the mark named has now resolved that way, and
            nothing is parked or quarantined.

        The periodic reconciliation is untouched and still re-marks.
        """
        try:
            cause = self.state.get(UNCERTAIN_CAUSE)
            if not isinstance(cause, dict):
                return None
            if str(cause.get("kind")) != RECONCILE_PENDING_ONLY:
                return None
            why = self.state.get("orders_uncertain")
            if not why or str(why) != str(cause.get("why")):
                return None
            named = [str(c) for c in (cause.get("pending") or [])]
            if str(coid) not in named:
                return None
            delta = D(str(booked.get("filled_delta")
                          or booked.get("delta") or 0))
            if delta != 0 or not venue_terminal:
                cause["fill_booked"] = True      # wait for a reconciliation
                return None
            if cause.get("fill_booked"):
                return None
            done = [str(c) for c in (cause.get("resolved_unfilled") or [])]
            if str(coid) not in done:
                done.append(str(coid))
            cause["resolved_unfilled"] = done
            if set(named) - set(done):
                return None                      # others still unanswered
            if (self.state.get("pending_terminal") or {}) or \
                    (self.state.get("quarantine") or {}):
                return None
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None                 # unreadable is not resolved
        rec = {"coid": str(coid), "kind": RECONCILE_PENDING_ONLY,
               "orders": list(named), "was": str(why),
               "t": dt.datetime.now(dt.timezone.utc).isoformat(),
               "labels": ("cleared because every order the reconciliation "
                          "parked has been answered by the venue's own "
                          "order row with nothing booked, so the positions "
                          "it verified are unchanged")}
        self.state["orders_uncertain"] = None
        self.state[UNCERTAIN_CAUSE] = None
        log = self.state.setdefault("uncertain_resolved", [])
        if isinstance(log, list):
            log.append(rec)
            del log[:-UNCERTAIN_RESOLVED_KEEP]
        return rec

    def own_settled_round_evidence(self, *,
                                   series: str = "cpc-btc-updown-15m-",
                                   last_n: int = 50) -> Dict[str, Any]:
        """What OUR OWN venue-settled windows actually paid, per contract.

        Read-only, and the only inputs are rows this ledger wrote when the
        venue settled a market: quantities, cost and redemption. One window
        is one observation. The figure is

            sum(settlement_pnl) / sum(max(up_qty, dn_qty))

        over the last `last_n` settled windows of this series -- the realized
        result per contract that took a FIRST fill, pairs and stranded legs
        together, which is what one more round buys.

        ONLY ROWS WHOSE MONEY IS KNOWN. A row settled at the venue's own
        price with every cost basis verified is counted; one carrying
        `unverified_cost_basis_keys`, another resolution source or an
        unreadable number is skipped and reported, never estimated.

        It is evidence, not a forecast: it mixes whatever configurations
        produced those windows, and a small sample is a wide interval. The
        caller decides how many windows it needs before listening.
        """
        used: List[Tuple[str, D, D, D, D]] = []
        skipped = 0
        for mkt, row in (self.state.get("retired_markets") or {}).items():
            try:
                if not str(mkt).startswith(series):
                    continue
                if str(row.get("resolution_source")) != "VENUE_SETTLEMENT_PX" \
                        or row.get("unverified_cost_basis_keys"):
                    skipped += 1
                    continue
                up, dn = D(str(row["up_qty"])), D(str(row["dn_qty"]))
                pnl = D(str(row["settlement_pnl"]))
                if up < 0 or dn < 0 or max(up, dn) <= 0:
                    skipped += 1
                    continue
                # THE SAME WINDOW, SPLIT: what its pairs paid and what its
                # unmatched leg paid. Absent or unreadable on a row is None,
                # and one None makes the whole split unknown below -- never
                # a smaller sample that happens to read better.
                try:
                    split = (D(str(row["matched_qty"])),
                             D(str(row["matched_pnl"])),
                             D(str(row["residual_qty"])),
                             D(str(row["residual_pnl"])))
                    if not all(x.is_finite() for x in split) \
                            or split[0] < 0 or split[2] < 0:
                        split = None
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    split = None
                used.append((str(row.get("t") or ""), up, dn, pnl,
                             max(up, dn), split))
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                skipped += 1
        used.sort(key=lambda r: r[0])
        used = used[-max(1, int(last_n)):]
        units = sum((r[4] for r in used), D(0))
        net = sum((r[3] for r in used), D(0))
        # WHAT A FIRST FILL BECAME ON THIS VENUE: how often it paired, what
        # a pair paid and what an unmatched contract paid. Reported only
        # when EVERY window in the sample states its split and both kinds
        # occurred; otherwise the reason, and no figure.
        cond: Dict[str, Any] = {"status": "UNKNOWN"}
        if not used:
            cond["reason"] = "NO_SETTLED_WINDOWS"
        elif any(r[5] is None for r in used):
            cond["reason"] = "A_WINDOW_DOES_NOT_STATE_ITS_SPLIT"
        else:
            m = sum((r[5][0] for r in used), D(0))
            mp = sum((r[5][1] for r in used), D(0))
            u = sum((r[5][2] for r in used), D(0))
            rp = sum((r[5][3] for r in used), D(0))
            if m <= 0 or u <= 0:
                cond["reason"] = ("NO_PAIR_IN_THE_SAMPLE" if m <= 0
                                  else "NO_UNMATCHED_LEG_IN_THE_SAMPLE")
            else:
                cond = {
                    "status": "MEASURED",
                    "first_leg_contracts": str(m + u),
                    "p_pair_given_first_fill": str(
                        (m / (m + u)).quantize(D("0.0001"))),
                    "pair_net_per_contract": str(
                        (mp / m).quantize(D("0.0001"))),
                    "unmatched_net_per_contract": str(
                        (rp / u).quantize(D("0.0001"))),
                    "labels": ("this venue's settled windows only. A "
                               "pairing rate GIVEN a first fill; it says "
                               "nothing about how often an order fills.")}
        return {
            "conditional": cond,
            "windows": len(used), "skipped": skipped,
            "contracts": str(units), "net_usd": str(net),
            "net_per_contract": (None if units <= 0
                                 else str((net / units).quantize(
                                     D("0.0001")))),
            "windows_with_a_pair": sum(1 for r in used
                                       if min(r[1], r[2]) > 0),
            "windows_with_a_stranded_leg": sum(1 for r in used
                                               if r[1] != r[2]),
            "from": (used[0][0] if used else None),
            "to": (used[-1][0] if used else None),
            "series": series, "last_n": int(last_n),
            "labels": ("venue-settled, cost basis verified, realized. "
                       "Net per contract that took a first fill; pairs and "
                       "stranded legs together."),
        }

    def attest_other_strategy_cost(self, market: str,
                                   fills: Sequence[Mapping[str, Any]],
                                   venue_rows: Mapping[str, Any],
                                   *, source: str) -> Dict[str, Any]:
        """Replace the $1 bound on ANOTHER strategy's position with its real
        cost -- only when the evidence closes exactly. Otherwise nothing.

        THE DEFECT. This ledger is the account-wide one, and it never sees a
        5-minute fill's price: a 5-minute position it adopts from the venue
        is held at $1 a contract, so at settlement a matched 5-minute pair
        is booked as a loss of its whole quantity. Conservative, and wrong
        enough to trip the daily halt on a strategy that made money.

        THE EVIDENCE, two independent sources that must agree:
          `fills`       the other strategy's own ledger rows for this market:
                        venue_order_id, side, filled_qty, price (its limit:
                        a post-only maker buy fills at its limit);
          `venue_rows`  the VENUE's own `/v1/order/{id}` row for each of
                        those ids, read by this service with its own
                        credentials.
        Every id must be read, name this market, and report a cumulative
        quantity equal to the other ledger's; where the venue states an
        average price it must equal the stated price (in own-outcome terms);
        and per side the sum must equal EXACTLY what this ledger holds. Any
        miss leaves the position at the bound and says which check failed.

        Only for a market in ANOTHER registered strategy's series, and only
        for a market this ledger holds at the bound. It writes quantity and
        cost together, records its source, and never touches realized P&L:
        settlement posts the result through the one ordinary path.
        """
        def no(reason: str, **kw: Any) -> Dict[str, Any]:
            return dict(kw, attested=False, market=market, reason=reason)

        try:
            market = canonical_market(market, doing="attesting a cost")
        except UnknownMarket:
            return no("MARKET_UNREADABLE")
        owner = ORIGIN.strategy_of_market(market)
        if owner is None or owner == OWN_STRATEGY:
            return no("NOT_ANOTHER_STRATEGYS_MARKET")
        if not str(source).strip():
            return no("NO_SOURCE")
        unknown = [str(k) for k in
                   (self.state.get("leg_cost_basis_unknown") or [])
                   if str(k).rsplit("|", 1)[0] == market]
        if not unknown:
            return no("NOT_HELD_AT_THE_BOUND")
        pos = self.state.get("positions") or {}
        held = {side: D(str(pos.get("%s|%s" % (market, side), 0)))
                for side in (UP, DOWN)}
        got = {UP: [D(0), D(0)], DOWN: [D(0), D(0)]}
        seen = set()
        for f in fills or ():
            if not isinstance(f, Mapping):
                return no("FILL_ROW_UNREADABLE")
            vid = str(f.get("venue_order_id") or "").strip()
            if not vid or vid in seen:
                return no("FILL_ID_MISSING_OR_REPEATED", venue_order_id=vid)
            seen.add(vid)
            try:
                side = canonical_side(f.get("side"), doing="attesting a cost")
            except UnknownSide:
                return no("FILL_SIDE_UNREADABLE", venue_order_id=vid)
            qty, px = venue_decimal(f.get("filled_qty")), venue_decimal(
                f.get("price"))
            if qty is None or px is None or qty < 0 or not (D(0) < px < D(1)):
                return no("FILL_NUMBERS_UNREADABLE", venue_order_id=vid)
            if qty == 0:
                continue
            row = venue_rows.get(vid) if isinstance(venue_rows, Mapping) \
                else None
            if not isinstance(row, Mapping):
                return no("VENUE_ORDER_NOT_READ", venue_order_id=vid)
            vm = row.get("marketSlug") or row.get("market_slug") \
                or row.get("market")
            if vm is not None and str(vm) != market:
                return no("VENUE_ORDER_IN_ANOTHER_MARKET", venue_order_id=vid,
                          venue_market=str(vm))
            cum = venue_decimal(row.get("cumQuantity"))
            if cum is None or cum != qty:
                return no("VENUE_QUANTITY_DISAGREES", venue_order_id=vid,
                          venue_cum=None if cum is None else str(cum),
                          ledger_qty=str(qty))
            avg = venue_order_avg_price(row)
            if avg is not None:
                own = own_outcome_price(side, avg, basis=WIRE)
                if abs(own - px) > D("0.0001"):
                    return no("VENUE_PRICE_DISAGREES", venue_order_id=vid,
                              venue_own_price=str(own), ledger_price=str(px))
            got[side][0] += qty
            got[side][1] += qty * px
        for side in (UP, DOWN):
            if got[side][0] != held[side]:
                return no("FILLS_DO_NOT_ACCOUNT_FOR_THE_POSITION", side=side,
                          held=str(held[side]), fills=str(got[side][0]))
        lc = self.state.setdefault("leg_cost", {})
        for side in (UP, DOWN):
            k = "%s|%s" % (market, side)
            if held[side] > 0:
                lc[k] = str(got[side][1])
        self.state["leg_cost_basis_unknown"] = [
            k for k in (self.state.get("leg_cost_basis_unknown") or [])
            if str(k).rsplit("|", 1)[0] != market]
        for k in list((self.state.get("adopted_unattributed") or {})):
            if str(k).rsplit("|", 1)[0] == market:
                self.state["adopted_unattributed"].pop(k, None)
        (self.state.get("risk_floor_usd") or {}).pop(market, None)
        rec = {"t": dt.datetime.now(dt.timezone.utc).isoformat(),
               "market": market, "origin": owner, "source": str(source),
               "orders": sorted(seen),
               "legs": {side: {"qty": str(got[side][0]),
                               "cost": str(got[side][1])}
                        for side in (UP, DOWN)}}
        self.state.setdefault("other_strategy_cost_attested", {})[market] = rec
        self.state.setdefault("cost_reconstructions", []).append(
            dict(rec, side="BOTH"))
        self.save()
        return dict(rec, attested=True)

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

    def account_wide_risk_by_market(
            self, *, extra_market: str = "", extra_side: str = "",
            extra_qty: D = D(0), extra_price: D = D(0)
            ) -> Dict[str, Any]:
        """Worst-case cash loss per market, across EVERY market with anything
        in it -- manual, L5, filled or merely resting.

        THE DEFECT THIS FIXES, which is load-bearing. The gate was handed
        `risk_by_market={market: ...}` -- a ONE-ENTRY dict for the market
        being traded. So `sum(risk_by_market.values())` summed a single
        market and max_total_exposure_usd was arithmetically IDENTICAL to
        max_usd_per_market: there was no account-wide ceiling at all, and
        $2.04 of manual inventory in another market contributed nothing to a
        $5 "total exposure" bound.

        NO DOUBLE COUNTING. risk_exposure_usd(m) already prices m's filled
        positions AND its still-fillable orders under both settlements, so
        each market is counted exactly once and nothing is added twice. The
        pending order is applied to its own market only, via extra_*.

        FAILS CLOSED. Enumeration and every per-market read go through the
        store's own coherence check; any market that cannot be priced makes
        the whole figure unavailable and the caller refuses. A partial sum
        would understate account-wide exposure, which is the one direction
        that lets more risk through than the bound.
        """
        try:
            markets = set(self.markets_with_exposure())
        except BaseException as exc:                           # noqa: BLE001
            return {"ok": False, "reason": "EXPOSURE_ENUMERATION_FAILED:%s"
                    % type(exc).__name__}
        # a market with no position but a RESTING order is still exposure
        for o in (self.state.get("live_orders") or {}).values():
            m = str((o or {}).get("market") or "").strip()
            if m:
                markets.add(m)
        if extra_market:
            markets.add(extra_market)
        out: Dict[str, D] = {}
        for m in sorted(markets):
            try:
                if m == extra_market:
                    out[m] = self.risk_exposure_usd(
                        m, extra_side=extra_side, extra_qty=extra_qty,
                        extra_price=extra_price)
                else:
                    out[m] = self.risk_exposure_usd(m)
            except BaseException as exc:                       # noqa: BLE001
                return {"ok": False,
                        "reason": "EXPOSURE_UNREADABLE:%s:%s"
                        % (m, type(exc).__name__)}
        # ANOTHER STRATEGY'S RESTING ORDERS ARE ACCOUNT EXPOSURE TOO.
        #
        # They are deliberately not in `live_orders` -- this strategy must
        # never adopt, poll or cancel them -- so the per-market figure above
        # does not see them. But they can fill, and the account-wide ceiling
        # is about the account. Their price is the other strategy's
        # business and is not read here: each remaining contract is charged
        # at $1, the most a contract can cost, which is the same bound an
        # unreadable price gets everywhere else in this module.
        #
        # FAILS CLOSED like everything above: a foreign order whose
        # remaining quantity cannot be read makes the whole figure
        # unavailable. Reading it as zero would understate the account.
        foreign_usd = D(0)
        foreign_by_origin: Dict[str, D] = {}
        for _fc, _fo in (self.state.get("foreign_orders") or {}).items():
            _rem = venue_decimal((_fo or {}).get("remaining")) \
                if isinstance(_fo, Mapping) else None
            if _rem is None or not _rem.is_finite() or _rem < 0:
                return {"ok": False,
                        "reason": "FOREIGN_ORDER_UNREADABLE:%s"
                        % str(_fc)[:40]}
            _charge = _rem * UNKNOWN_PRICE_WORST_CASE
            _fm = str((_fo or {}).get("market") or "").strip() \
                or "FOREIGN_ORDERS_MARKET_UNSTATED"
            out[_fm] = out.get(_fm, D(0)) + _charge
            markets.add(_fm)
            foreign_usd += _charge
            _og = str((_fo or {}).get("origin") or ORIGIN.UNKNOWN)
            foreign_by_origin[_og] = foreign_by_origin.get(_og, D(0)) + _charge
        return {"ok": True, "risk_by_market": out,
                "markets": sorted(markets),
                "foreign_orders_usd": str(foreign_usd),
                "foreign_orders_usd_by_origin": {
                    k: str(v) for k, v in sorted(foreign_by_origin.items())},
                "total_usd": str(sum(out.values(), D(0)))}

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
        # WHOSE RESULT THIS IS. A LABEL ON THE ROW, and nothing else: the
        # account-wide lines above have just been moved by the full amount
        # and the label does not touch them. Written here, inside the one
        # writer of `retired_markets`, so there is still exactly one. See
        # `l5_origin` and `realized_by_origin`.
        _att = (self.state.get("other_strategy_cost_attested") or {}).get(
            market)
        if isinstance(_att, Mapping) and not unknown_basis_at_settlement:
            _og = {"origin": str(_att.get("origin")),
                   "cost_basis": ORIGIN.BASIS_AUTHENTICATED,
                   "evidence": "OTHER_STRATEGY_FILLS_CONFIRMED_BY_VENUE_"
                               "ORDER_READS"}
        else:
            _og = ORIGIN.origin_of_settlement(
                market, own=OWN_STRATEGY, verified_own=bool(verified_l5),
                unknown_basis=bool(unknown_basis_at_settlement),
                other_strategy_order_seen=(
                    self.state.get("foreign_orders_seen") or {}).get(market))
        self.state.setdefault("retired_markets", {})[market] = {
            "origin": _og["origin"],
            "origin_cost_basis": _og["cost_basis"],
            "origin_evidence": _og["evidence"],
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
        # HOW MUCH OF EACH LEG IS STILL PRICED AT THE BOUND. `apply_fill`
        # reads this: a fill of OUR OWN order that is read after the venue's
        # position already contained it replaces that much of the bound with
        # the measured cost instead of being added on top. See
        # `absorb_into_adopted`.
        unattributed = self.state.setdefault("adopted_unattributed", {})
        for k in keys:
            unattributed.pop(k, None)
        for k, v in rows.items():
            if k.rsplit("|", 1)[0] != market:
                continue
            pos[k] = str(v)
            lc[k] = str(D(str(v)) * UNKNOWN_PRICE_WORST_CASE)
            unattributed[k] = str(v)
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

    def fills_in_flight(self, gross: Mapping[str, Mapping[str, D]],
                        net: Mapping[str, D],
                        open_orders: Sequence[Mapping[str, Any]]
                        ) -> Dict[str, Dict[str, Any]]:
        """Markets whose whole difference our own unbooked fills can explain.

        For every order we track with a venue id -- live, or absent from the
        venue's open orders and awaiting its final read -- the most it can
        have filled without our having booked it is:

            still open at the venue   qty - venue remaining - booked
            no longer open            qty - booked

        A market qualifies when the venue's net differs from ours and the
        difference lies inside what those orders could add on that side.
        An order with no venue id, an unreadable quantity or an unknown side
        contributes NOTHING, so anything we cannot account for exactly is
        still adopted at the bound.
        """
        open_rem: Dict[str, D] = {}
        for o in open_orders or ():
            vid = str(o.get("id") or o.get("order_id") or "").strip()
            rem = o.get("remaining_qty", o.get("remainingQuantity"))
            try:
                if vid and rem is not None:
                    open_rem[vid] = D(str(rem))
            except Exception:                                  # noqa: BLE001
                continue
        cap: Dict[str, Dict[str, Any]] = {}
        submitted = self.state.get("submitted") or {}
        seen = set()
        for book in ("live_orders", "pending_terminal"):
            for coid, o in (self.state.get(book) or {}).items():
                if coid in seen or not isinstance(o, Mapping):
                    continue
                seen.add(coid)
                vid = str(o.get("venue_order_id") or "").strip()
                side = venue_side(o.get("side"))
                market = o.get("market")
                sub = submitted.get(coid) or {}
                try:
                    qty = D(str(o.get("qty") if o.get("qty") is not None
                                else sub.get("qty")))
                    done = self.applied_fill_qty(coid)
                except Exception:                              # noqa: BLE001
                    continue
                if not vid or side is None or not market or qty <= 0:
                    continue
                if book == "live_orders" and vid in open_rem:
                    could = qty - open_rem[vid] - done
                else:
                    could = qty - done
                if could <= 0:
                    continue
                c = cap.setdefault(str(market), {UP: D(0), DOWN: D(0),
                                                 "orders": []})
                c[side] += could
                c["orders"].append({"coid": str(coid), "venue_order_id": vid,
                                    "side": side, "unbooked_at_most":
                                    str(could)})
        out: Dict[str, Dict[str, Any]] = {}
        for m, c in cap.items():
            if m not in net:
                continue
            ours = (gross.get(m) or {}).get("net", D(0))
            diff = net[m] - ours
            if diff == 0 or not (-c[DOWN] <= diff <= c[UP]):
                continue
            out[m] = {"venue_net": str(net[m]), "our_net": str(ours),
                      "difference": str(diff), "orders": c["orders"]}
        return out

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
        # A FILL OF OUR OWN ORDER THAT WE HAVE NOT READ YET IS NOT A
        # CONTRADICTION. The venue updates the position the instant an
        # order trades; we learn the price only when we read the order.
        # Adopting in between throws away the book -- matched pairs
        # included -- for a difference the next order read explains in
        # full. Where the whole difference fits inside what our own tracked
        # orders could have filled and not yet booked, the book is LEFT
        # ALONE for a bounded number of reconciliations and the order read
        # books the fill at its real price. Past the bound it is adopted
        # exactly as before.
        flight = self.fills_in_flight(gross, net, open_orders)
        fstate = self.state.setdefault("fill_in_flight", {})
        deferred: List[str] = []
        for m in sorted(flight):
            n = int((fstate.get(m) or {}).get("deferrals") or 0)
            if n >= FILL_IN_FLIGHT_MAX_DEFERRALS:
                continue
            fstate[m] = dict(
                flight[m], deferrals=n + 1,
                first_seen=((fstate.get(m) or {}).get("first_seen")
                            or dt.datetime.now(dt.timezone.utc).isoformat()))
            deferred.append(m)
        for m in list(fstate):
            if m not in deferred and m not in flight:
                fstate.pop(m, None)
        disputed = [m for m in disputed if m not in deferred]
        for m in list(disputed):
            self.adopt_venue_position(
                m, after, why="venue net contradicts our gross")
        for k, v in after.items():
            m = k.rsplit("|", 1)[0]
            if m in agrees or m in deferred:
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
        foreign_now: Dict[str, Any] = {}
        self.state["foreign_orders"] = foreign_now
        for o in open_orders:
            coid = str(o.get("client_order_id") or o.get("clientOrderId")
                       or o.get("order_id") or o.get("id") or "")
            if not coid:
                continue
            # NOT OURS, BY NAME. Decided on the venue's own client id, before
            # any mapping back to a tracked order, and never for an id our
            # file already holds. See `FOREIGN_COID_PREFIXES_ENV`.
            _f_market = (o.get("marketSlug") or o.get("market_slug")
                         or o.get("market"))
            _f_series = ORIGIN.strategy_of_market(_f_market)
            _f_by_series = (_f_series is not None
                            and _f_series != OWN_STRATEGY)
            _f_tracked = coid in ours or bool(tracked_coid_for_venue_order(
                o.get("id") or o.get("order_id"), self.state))
            if not _f_tracked and (is_foreign_coid(coid) or _f_by_series):
                _f_origin = ORIGIN.origin_of_coid(coid)
                foreign_now[coid] = {
                    "venue_order_id": str(o.get("id") or o.get("order_id")
                                          or ""),
                    # by the id's prefix when the row carries one; a real
                    # venue row does not, and then WHOSE it is stays
                    # UNKNOWN -- only that it is not ours is established
                    "origin": _f_origin,
                    "series_strategy": _f_series,
                    "foreign_because": ("CLIENT_ID_PREFIX"
                                        if is_foreign_coid(coid)
                                        else "MARKET_SERIES"),
                    "market": _f_market,
                    "remaining": str(o.get("remaining_qty", o.get(
                        "remainingQuantity", o.get("qty", "")))),
                    "labels": "another strategy's order: listed, never "
                              "adopted, never cancelled and never counted "
                              "against this strategy's own order ceiling. "
                              "Its remaining quantity IS counted in "
                              "account-wide exposure at $1 a contract."}
                # EVIDENCE FOR A LATER LABEL, not a claim about a position:
                # the venue listed a registered strategy's order in this
                # market. Bounded, oldest dropped first.
                if _f_market and _f_origin in ORIGIN.STRATEGY_COID_PREFIX:
                    _seen = self.state.setdefault("foreign_orders_seen", {})
                    _seen.pop(str(_f_market), None)
                    _seen[str(_f_market)] = _f_origin
                    while len(_seen) > FOREIGN_SEEN_KEEP:
                        _seen.pop(next(iter(_seen)))
                continue
            # OUR ORDER, UNDER OUR OWN KEY. A venue row that states no client
            # order id falls through to the venue's id above, and keying it
            # there is what made one order into two records at 0230z. If we
            # already track this venue order, it is not an adoption: it is
            # ours, and it keeps the coid we sent it with. See
            # `tracked_coid_for_venue_order`.
            _known = tracked_coid_for_venue_order(
                o.get("id") or o.get("order_id"), self.state)
            if _known and coid not in ours:
                coid = _known
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
                # PROVENANCE IS STICKY, AND IT IS NOT DECORATION.
                #
                # IT USED TO BE BOTH. Written here as `coid not in ours`
                # and read NOWHERE, it also laundered itself: on the next
                # reconciliation the adopted coid IS in `ours`, so the
                # flag flipped to False and the order became
                # indistinguishable from one we placed. That is how the
                # owner's hand-placed 5 DOWN order on
                # cpc-btc-updown-15m-2026-11-18-2215z became "ours" and
                # was then cancelled by the unarmed orphan sweep, whose
                # own label promises "a manual order is never one of
                # them".
                #
                # Once foreign, always foreign: the only thing that makes
                # an order ours is OUR OWN submit record, which
                # `submitted` keeps permanently. Adoption keeps the order
                # VISIBLE -- polled, counted against account-wide
                # exposure at the $1 upper bound, published to the
                # watchdog -- without making it OWNED. Visibility and
                # ownership were the same field; they are now separate.
                #: `_known` IS WHAT DISTINGUISHES THE TWO CASES, and my
                #: first version of this ignored it. An order of OURS
                #: whose file record was lost comes back with its venue
                #: id mapping to one of our own coids, so `_known` is set
                #: and `coid` has already been rewritten to it -- it is
                #: NOT adopted, and marking it so stranded it behind the
                #: ownership guard below, which is the very failure
                #: adoption exists to prevent. The owner's hand-placed
                #: order has no tracked coid, so `_known` is None and it
                #: is adopted, visible, and never ours.
                "adopted": (bool(mine.get("adopted"))
                            or (coid not in ours and not _known)),
                "adopted_first_seen_utc": (
                    mine.get("adopted_first_seen_utc")
                    or (dt.datetime.now(dt.timezone.utc).isoformat()
                        if coid not in ours else None)),
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
            # WHEN WE SENT IT, recovered rather than invented. An adopted
            # row had no `t` at all, and `maybe_acc_deadline` reads a
            # missing `t` as unusable -- which marks the store UNCERTAIN and
            # disarms. See `recover_submit_time`: 0200z lost its window and
            # its order to exactly that. Absent stays absent, so the
            # unrecoverable case still fails closed.
            _t = recover_submit_time(
                o, mine=mine,
                live_orders=(self.state.get("live_orders") or {}),
                submitted=(self.state.get("submitted") or {}),
                venue_order_id=rebuilt[coid]["venue_order_id"])
            if _t:
                rebuilt[coid]["t"] = _t
                rebuilt[coid]["submit_time_source"] = (
                    "OURS" if mine.get("t") else "RECOVERED")
            else:
                rebuilt[coid]["submit_time_unrecoverable"] = True
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
                # what it was FOR, kept so `fills_in_flight` can bound what
                # this order may have filled once it is off the live map
                "qty": (was.get("qty") if was.get("qty") is not None
                        else prior.get("qty")),
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
        #: A FLOOR OUTLIVES ITS UNCERTAINTY, AND UNKNOWN COST IS
        #: UNCERTAINTY.
        #:
        #: Keying the floor's life to `disputed` alone deleted it at the
        #: worst moment. Adoption RESOLVES the gross/net divergence by
        #: construction -- it copies the venue's number into our book --
        #: so the market stops being disputed in the same pass that
        #: adopts it, and the floor the adoption raised is dropped
        #: immediately. Observed on cpc-btc-updown-15m-2026-11-18-2215z:
        #: at 22:22:56Z quarantined with a $5 floor, and by 22:28:12Z
        #: `risk_floor_usd` was {} while `leg_cost_basis_unknown` still
        #: named that market's DOWN leg.
        #:
        #: "Its own book is authoritative again" was the premise, and it
        #: is false: the book agrees with the venue because the venue's
        #: number was written into it, not because we know what the
        #: shares cost. AGREEMENT REACHED BY ADOPTION IS NOT AUTHORITY.
        #: The floor therefore stays while the cost basis is unknown, and
        #: is released by the attestation path, which clears both
        #: together after verifying fills against the venue.
        _basis_unknown = {
            str(k).rsplit("|", 1)[0]
            for k in (self.state.get("leg_cost_basis_unknown") or [])}
        for m in list(floors):
            if m not in disputed and m not in _basis_unknown:
                floors.pop(m, None)
        self.state[UNCERTAIN_CAUSE] = None
        if disputed or incoherent or pending or quarantine:
            _why = (
                "gross/net divergence %s; incoherent book %s; "
                "unbooked terminal orders %s; quarantined orders %s"
                % (sorted(set(disputed)), sorted(incoherent), pending,
                   quarantine))
            self.state["orders_uncertain"] = _why
            # WHICH ORDERS, when they are the whole of it. Evidence for
            # `resolve_pending_uncertainty`; a divergence, an incoherent
            # book or a quarantine records NO cause and is cleared only by
            # the next reconciliation, exactly as before.
            if pending and not (disputed or incoherent or quarantine):
                self.state[UNCERTAIN_CAUSE] = {
                    "kind": RECONCILE_PENDING_ONLY, "why": _why,
                    "pending": list(pending), "resolved_unfilled": []}
        # THE DWELL ANCHOR IS RECONCILED HERE TOO, after the venue has
        # overwritten our positions. A stamp for a market the venue says is
        # flat is dropped; a market that holds inventory with NO stamp is
        # REPORTED, never given an invented one -- it reads as None and the
        # defensive gate falls back to the arm window for that market.
        _dwell = self.reconcile_stranded()
        self.save()
        return {"reconciled": True, "drift": drift,
                "stranded_since": _dwell,
                # stated separately and never collapsed into one another
                "gross_before": {m: {k: str(v) for k, v in d.items()}
                                 for m, d in gross.items()},
                "venue_net": {m: str(v) for m, v in net.items()},
                "gross_net_agrees": agrees,
                "gross_detail_lost": sorted(m for m, ok in agrees.items()
                                            if not ok and m not in deferred),
                # our own unbooked fills explain these; nothing was adopted
                "fills_in_flight_deferred": {
                    m: dict(fstate.get(m) or {}) for m in deferred},
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


class _SelfCheckStore:
    """A store stub that can answer two questions and do nothing else.

    It holds no transport, no credentials, no file handle and no venue client,
    so the self-check below cannot submit, cancel, amend or persist anything
    even if a future edit tried to.
    """

    __slots__ = ("_res", "_out", "_raise")

    def __init__(self, res: str, out: str = "0", raise_it: bool = False) -> None:
        self._res, self._out, self._raise = res, out, raise_it

    def residual(self, market: str) -> D:                      # noqa: ARG002
        if self._raise:
            raise RuntimeError("unreadable residual (self-check case)")
        return D(self._res)

    def outstanding_residual(self, market: str) -> D:           # noqa: ARG002
        if self._raise:
            raise RuntimeError("unreadable residual (self-check case)")
        return D(self._out)


class _SelfCheckShim:
    """Carries only `store`, which is all the side-selection logic reads."""

    __slots__ = ("store",)

    def __init__(self, store: "_SelfCheckStore") -> None:
        self.store = store


#: the states that cost 1245z and 0130z, plus the counterexample that must not
#: change and the flat case that must stay byte-for-byte cheapest-first.
SELFCHECK_CASES = (
    # label, residual, outstanding, qty offered, want_leg, want_qty
    ("1245z up 1.4 / dn 2.8",        "-1.4",  "0", "1.4",  UP,   "1.4"),
    ("0130z dn 1.87",                "-1.87", "0", "1.87", UP,   "1.87"),
    ("0145z dn 1.56 (must not move)", "-1.56", "0", "1.56", UP,   "1.56"),
    ("flat -> cheapest-first",        "0",     "0", "1.4",  DOWN, "1.4"),
    ("resting complement counted",    "0",    "-2", "2",    UP,   "2"),
    ("overshoot capped to residual",  "-0.5",  "0", "1.4",  UP,   "0.5"),
)


def residual_side_selfcheck() -> Dict[str, Any]:
    """Run the DEPLOYED side-selection logic against the recorded states.

    WHY THIS EXISTS. The wrong-side fix is deliberately inert while flat, and a
    shadow-locked executor can never acquire a residual, because shadow blocks
    submission and therefore blocks fills. So no shadow window can distinguish
    the fixed code from the old code, and "observe one correct shadow residual
    decision" is unsatisfiable by construction. This closes that gap the way
    `l5_runtime_config_verify` closes it for configuration: by asking the
    RUNNING PROCESS what its own code does.

    It calls `FaithfulExecutor.probe_leg_order` and `.probe_leg_qty_cap`
    themselves through an unbound call on a stub, so a PASS is evidence about
    the deployed methods and not about a reimplementation. It builds no
    executor, opens no socket, reads no real store, and lifts no lock.
    """
    rows: List[Dict[str, Any]] = []
    legs = {UP: {"price": D("0.45")}, DOWN: {"price": D("0.40")}}
    cheapest = [DOWN, UP]                       # DOWN is cheaper in `legs`
    for label, res, out, qty, want_leg, want_qty in SELFCHECK_CASES:
        row: Dict[str, Any] = {"case": label, "residual": res,
                               "outstanding": out, "want_leg": want_leg}
        try:
            shim = _SelfCheckShim(_SelfCheckStore(res, out))
            order = FaithfulExecutor.probe_leg_order(
                shim, "selfcheck-market", legs, list(cheapest))
            got_leg = order[0] if order else None
            got_qty = FaithfulExecutor.probe_leg_qty_cap(
                shim, "selfcheck-market", got_leg, D(qty))
            row.update(got_leg=got_leg, want_qty=want_qty, got_qty=str(got_qty),
                       both_legs_once=sorted(order) == sorted([UP, DOWN]),
                       ok=(got_leg == want_leg and got_qty == D(want_qty)
                           and sorted(order) == sorted([UP, DOWN])))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            row.update(ok=False, error=type(exc).__name__)
        rows.append(row)
    # an unreadable residual must keep today's rule rather than halt
    try:
        shim = _SelfCheckShim(_SelfCheckStore("0", "0", raise_it=True))
        order = FaithfulExecutor.probe_leg_order(
            shim, "selfcheck-market", legs, list(cheapest))
        rows.append({"case": "unreadable residual -> cheapest-first",
                     "want_leg": DOWN, "got_leg": order[0] if order else None,
                     "ok": list(order) == list(cheapest)})
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        rows.append({"case": "unreadable residual -> cheapest-first",
                     "ok": False, "error": type(exc).__name__})
    failed = [r for r in rows if not r.get("ok")]
    return {"label": "L5_RESIDUAL_SIDE_SELFCHECK", "PASS": not failed,
            "checked": len(rows), "failed": failed, "cases": rows,
            "labels": ("exercises the DEPLOYED probe_leg_order / "
                       "probe_leg_qty_cap through an unbound call on a stub "
                       "holding only a store. Builds no executor, opens no "
                       "socket, reads no real store, submits nothing and lifts "
                       "no lock. An unreadable case FAILS rather than passing "
                       "silently.")}


#: A matched UP+DOWN pair redeems exactly this. Not a policy parameter and
#: not a ceiling: above it the pair is a CERTAIN loss, whatever happens.
REDEMPTION_PAIR = D("1.00")


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
        #: PER-CALL SCRATCH FOR THE TWO-SIDED LEG, declared rather than
        #: created on first use inside `handle`. They were implicit runtime
        #: attributes, so a fresh executor genuinely lacked them and the
        #: runner's `finally` referenced attributes that need not exist.
        #: None is exactly what "no leg pending" already meant.
        self._probe_leg_pending: Optional[str] = None
        self._probe_round_pending: Optional[int] = None
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
        #: ORDER-ID-LINKED CALIBRATION CAPTURE, attached by the runner like
        #: the gates above. Declared here so the executor's surface states
        #: what may be attached to it: the graft guard checks every
        #: `self.executor.X` the runner touches against a freshly built
        #: instance, and an attribute the runner invents is exactly the
        #: class of bug that guard exists to catch.
        #:
        #: Absent (None) means no capture and changes nothing else: the
        #: recorder has no veto and the submission path does not consult it.
        self.fill_calibration: Optional[Any] = None
        self.calib_book: Optional[Any] = None
        self.calib_ladder: Optional[Any] = None
        #: THE AUTHENTICATED BOOK MIRROR, for the two-leg planner only.
        #:
        #: WHY IT EXISTS. The runner calls `handle(row, {}, ...)` -- the empty
        #: dict IS the `authenticated` argument, and it is deliberate: the
        #: L5-following path rests at L5's own price through MAKER_TREATMENT
        #: and needs no book. But `probe_plan` prices BOTH legs from that
        #: argument, so with `{}` it read stream_age_s as None and
        #: `build_orders` refused BOOK_STALE unconditionally. The two-leg
        #: engine could never place an order, which shadow validation found
        #: within seven minutes of being switched on.
        #:
        #: THE MIRROR, NEVER A SNAPSHOT. A stored snapshot would go stale and
        #: could be reused across a restart or a reconnect; a mirror is
        #: snapshotted at the moment of the decision, and `AuthBook.snapshot`
        #: already returns None unless it is connected AND two-sided. So
        #: disconnected, one-sided and unreadable all arrive as "no book",
        #: which is the same BOOK_STALE refusal as before.
        #:
        #: It changes nothing else: `pmus_translate` still receives whatever
        #: the caller passed, so L5-following translation, pricing and sizing
        #: are untouched.
        self.ts_book: Optional[Any] = None
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
        # THE DEFENSIVE MANDATE. None except in the moments between a
        # confirmed cancellation of the resting complement and the single
        # order that replaces it. It is the ONLY thing that relaxes
        # `maker_only` or the 0.96 pair ceiling, it names the exact order it
        # covers (market, side, price, quantity), it expires on the window
        # clock, and it is consumed by the first matching order. Nothing
        # sets it unless `l5_defensive_completion.enabled()` is true, which
        # is false on every deployment by default.
        self._defensive_mandate: Optional[Dict[str, Any]] = None
        #: THE RUNNER'S BOUNDED TAKER COMPLETION PROPOSAL, or None. Attached
        #: after construction for the same reason the validation gates are:
        #: the executor's own signature stays unchanged and an executor
        #: nobody attached one to simply has no proposal rather than a
        #: different constructor. It is SINGLE USE -- `handle` takes it off
        #: before acting on it -- so a refusal cannot be re-ridden next tick.
        self.taker_completion_proposal: Optional[Dict[str, Any]] = None
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

    def ts_authenticated_book(self, market: str,
                              supplied: Any) -> Mapping[str, Any]:
        """The book the TWO-LEG planner prices from. Fail-closed.

        A supplied book carrying both sides AND its own age is used as given.
        Anything else -- the runner's deliberate `{}`, a half-filled dict, a
        dict with no age -- falls through to a FRESH snapshot of the mirror,
        taken here and never stored, so no restart or reconnect can reuse an
        old one.

        Every failure returns an empty mapping, whose `stream_age_s` is None,
        which `build_orders` refuses as BOOK_STALE. That is the same refusal
        as before the mirror existed, so nothing becomes permissive.
        """
        if isinstance(supplied, Mapping) \
                and supplied.get("bid") is not None \
                and supplied.get("ask") is not None \
                and supplied.get("stream_age_s") is not None:
            return supplied
        snap = getattr(getattr(self, "ts_book", None), "snapshot", None)
        if not callable(snap):
            return supplied if isinstance(supplied, Mapping) else {}
        try:
            fresh = snap(str(market))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {}                      # unreadable is not a fresh book
        return fresh if isinstance(fresh, Mapping) else {}

    def disarmed_refusal(self) -> Optional[Dict[str, Any]]:
        """None unless the transport is DEFINITELY disarmed.

        WHY THIS EXISTS. The submission claim is consumed before the wire and
        is never returned -- "not by a cancellation, a partial or zero fill,
        a reconnect, a restart, or a rejected or ambiguous acknowledgement".
        That is the right rule: a claim released on a crash could buy a
        second order into a venue that already holds the first.

        But a DISARMED transport is not an ambiguous outcome. It raises
        `NotArmed` from `submit()` without attempting a POST, so nothing
        reached the venue and nothing can have. The four-window campaign
        `camp1.87265e...` closed having spent 1 of 21 submissions and 1 of 6
        OPENs on exactly this: a well-formed MAKER intent (1715z DOWN 0.4500
        x 1.54) that was refused `NOT_ARMED` after its claim was already
        durable. The budget recorded a submission the venue never saw.

        AN EFFICIENCY GUARD, NOT A SAFETY GATE -- the same class of check as
        `presend_cross_refusal`, and placed beside it. It cannot authorize
        anything: the transport's own arm check at `submit()` is untouched
        and remains the authority. All this can do is decline to spend a
        claim on an order that provably cannot be sent.

        IT ACTS ONLY ON A POSITIVE `armed is False`. An unreadable or absent
        attribute FAILS OPEN into the existing path, because an unusual
        transport that is in fact armed must not be blocked by a telemetry
        read -- and failing open merely restores today's behaviour. Refusing
        early when `armed` is definitively False is free by construction:
        `submit()` would refuse the same order on the next line.
        """
        try:
            tp = getattr(self, "transport", None)
            if tp is None:
                return None
            armed = getattr(tp, "armed", None)
        except BaseException:                                  # noqa: BLE001
            return None
        if armed is not False:
            return None
        return {
            "reason": "NOT_ARMED_PRESEND",
            "transport": str(getattr(tp, "name", type(tp).__name__))[:40],
            "claim_consumed": False,
            "labels": ("the transport is disarmed, so submit() cannot reach "
                       "the venue. The submission claim is NOT consumed, "
                       "because an order that was never sent has not spent "
                       "the authorization. This is an accounting guard: the "
                       "transport's own arm check still decides whether any "
                       "order may be sent."),
        }

    def presend_cross_refusal(self, market: str,
                              intent: Mapping[str, Any],
                              tr: Mapping[str, Any]
                              ) -> Optional[Dict[str, Any]]:
        """None to send; a record when this MAKER leg would cross RIGHT NOW.

        `intent["price"]` is the leg's OWN price -- the two-sided override
        sets `tr["price"] = D(leg["price"])` from the plan, and the wire
        conversion happens later in `l5_order_body.from_intent` -- so it is
        passed to `leg_crosses` unconverted, which is what that helper
        expects.

        THE BOOK IS RE-READ, NOT REUSED. `ts_authenticated_book(market, {})`
        takes a fresh mirror snapshot, because a book carried down from
        planning is exactly the stale input this check exists to replace.

        FAILS OPEN ON EVERY UNKNOWN. This is an efficiency guard and not a
        safety gate: every risk control has already run by the call site, so
        the only thing it can do is decline an order the venue would have
        rejected anyway. A missing book, an unreadable price or side, or a
        raising helper therefore returns None and the order proceeds to the
        venue's own post-only check. Failing closed here would turn a
        budget-saving guard into a new way to stop trading, which is a larger
        behavioural change than the waste it removes.

        COUNTS WHAT IT AVOIDS, so the saving is measurable rather than
        asserted. The counter is per side plus a total, lazily initialised so
        this adds nothing to `__init__`.
        """
        if str(tr.get("mode") or "").upper() != FT.MAKER:
            return None                      # a taker order cannot post-only
        side = str(intent.get("side") or "")
        if side not in (UP, DOWN):
            return None
        own = intent.get("price")
        if own is None:
            return None
        try:
            book = self.ts_authenticated_book(market, {}) or {}
            bid, ask = book.get("bid"), book.get("ask")
            if bid is None or ask is None:
                return None                  # no book to judge against
            if not TS.leg_crosses(side, own, bid=bid, ask=ask):
                return None
            wire = TS.wire_price(side, own)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None                      # unreadable is not a refusal
        counts = getattr(self, "presend_cross_skips", None)
        if not isinstance(counts, dict):
            counts = {"UP": 0, "DOWN": 0, "total": 0}
            self.presend_cross_skips = counts
        return self._presend_record("PRESEND_WOULD_CROSS", side, own, wire,
                                    bid, ask, book, counts)

    def _presend_record(self, reason, side, own, wire, bid, ask, book,
                        counts):
        counts[side] = int(counts.get(side) or 0) + 1
        counts["total"] = int(counts.get("total") or 0) + 1
        return {
            "reason": "%s:%s" % (reason, side),
            "side": side,
            "own_px": str(own),
            "wire_px": None if wire is None else str(wire),
            "bid": str(bid), "ask": str(ask),
            "stream_age_s": (None if book.get("stream_age_s") is None
                             else str(book.get("stream_age_s"))),
            "skips": dict(counts),
            "labels": ("a post-only leg the book has left, declined BEFORE "
                       "the submission claim is consumed. No price, size, "
                       "depth, TTL or cutoff is changed; the order is "
                       "simply not sent, and the next tick prices a fresh "
                       "one."),
        }

    def presend_behind_refusal(self, market: str,
                               intent: Mapping[str, Any],
                               tr: Mapping[str, Any],
                               rnd: Any = None
                               ) -> Optional[Dict[str, Any]]:
        """None to send; a record when this MAKER leg is now FAR BEHIND.

        THE OTHER HALF OF `presend_cross_refusal`, and the asymmetry it
        closes is the same one `TS.replan_drift_ticks` documents at the
        plan. That guard declines a leg the book has made too AGGRESSIVE --
        the venue would reject it post-only. Nothing declined a leg the
        book has left too PASSIVE, because such a leg is perfectly
        placeable: it rests far behind the touch, never fills, and holds
        one of the two live-order slots for its whole TTL.

        THE PLAN-LEVEL RE-PRICE DOES NOT COVER THIS. It recomputes from the
        mirror as the plan is read; this reads the mirror again immediately
        before the wire, which is the only snapshot that describes the book
        the order will actually land in.

        ONLY WHEN THE ROUND HOLDS NOTHING, read through
        `probe_round_is_safe_to_retire` -- the same durable-state predicate
        the plan-level re-price and retirement both use. A complement
        matching a HELD leg must never be declined: declining it would
        strand the inventory it was going to match, which is a far worse
        outcome than a quote that fills slowly. Any answer but None sends.

        FAILS OPEN ON EVERY UNKNOWN, exactly like the crossing guard, and
        for the same reason: every risk control has already run by the call
        site, so this can only decline an order that was not going to fill.
        A missing book, an unreadable price, an unreadable round state or a
        raising helper returns None and the order proceeds.

        IT CHANGES NO PRICE, SIZE, DEPTH, TTL OR CUTOFF. It either lets the
        order through untouched or does not send it.
        """
        if str(tr.get("mode") or "").upper() != FT.MAKER:
            return None
        side = str(intent.get("side") or "")
        if side not in (UP, DOWN):
            return None
        own = intent.get("price")
        if own is None:
            return None
        try:
            # a round that holds anything is never declined here
            if self.probe_round_is_safe_to_retire(
                    market, int(rnd or 0)) is not None:
                return None
            book = self.ts_authenticated_book(market, {}) or {}
            bid, ask = book.get("bid"), book.get("ask")
            if bid is None or ask is None:
                return None
            drift = TS.plan_drift_ticks(
                {"legs": [{"side": side, "price": own},
                          {"side": (DOWN if side == UP else UP),
                           "price": own}]},
                bid=bid, ask=ask,
                tick=getattr(self.transport, "tick_size", None),
                depth_ticks=(int(os.getenv("L5_TS_DEPTH_TICKS", "2"))
                             + self.probe_level_offset(market)),
                spent_sides=[(DOWN if side == UP else UP)])
            if drift is None or drift < D(TS.replan_drift_ticks()):
                return None
            wire = TS.wire_price(side, own)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None                      # unreadable is not a refusal
        counts = getattr(self, "presend_behind_skips", None)
        if not isinstance(counts, dict):
            counts = {"UP": 0, "DOWN": 0, "total": 0}
            self.presend_behind_skips = counts
        rec = self._presend_record("PRESEND_LEFT_BEHIND", side, own, wire,
                                   bid, ask, book, counts)
        rec["drift_ticks"] = str(drift)
        rec["allow_ticks"] = TS.replan_drift_ticks()
        return rec

    def probe_plan(self, market: str,
                   authenticated: Mapping[str, Any],
                   loss_ceiling: Optional[D] = None) -> Dict[str, Any]:
        """Both probe legs, priced once from the authenticated book."""
        authenticated = self.ts_authenticated_book(market, authenticated)
        return TS.build_orders(
            market=market,
            bid=authenticated.get("bid"), ask=authenticated.get("ask"),
            bid_depth=authenticated.get("bid_shares"),
            ask_depth=authenticated.get("ask_shares"),
            tick=getattr(self.transport, "tick_size", None),
            min_qty=getattr(self.transport, "quantity_increment", None),
            depth_ticks=(int(os.getenv("L5_TS_DEPTH_TICKS", "2"))
                         + self.probe_level_offset(market)),
            cash_envelope=self.probe_cash_envelope(),
            max_worst_case_loss=(self.probe_loss_ceiling()
                                 if loss_ceiling is None else loss_ceiling),
            budget_headroom=self.probe_budget_headroom(),
            book_age_s=authenticated.get("stream_age_s"),
            gap_suspected=bool(authenticated.get("gap_suspected")))

    def probe_resting_in_market(self, market: str) -> int:
        """Orders of THIS market still on the ledger's live map."""
        return sum(1 for o in (self.store.live_orders() or {}).values()
                   if str((o or {}).get("market") or "") == str(market))

    def probe_level_offset(self, market: str) -> int:
        """Extra opening depth for a pair laddered behind resting ones.

        Zero unless `L5_TS_CONCURRENT_ROUNDS` is above one, so every existing
        configuration prices exactly as it did. An unreadable ledger is zero
        too: the level is a price choice, and the gates that bound the order
        -- pair ceiling, cash, exposure, order count -- do not depend on it.
        """
        extra = self.portfolio_steer_extra_depth()
        try:
            return int(TS.level_offset_ticks(
                self.probe_resting_in_market(market))) + extra
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return extra

    #: THE PORTFOLIO CONTROLLER'S STEER, for ONE timer-path placement. The
    #: runner sets it immediately before that `handle` call and clears it
    #: in a `finally` immediately after, so it exists for no other call --
    #: not an L5 action, not a completion, not a requote. None, which is
    #: what it is in every configuration that does not set
    #: L5_PORTFOLIO_CONTROLLER=STEER, leaves every path byte-for-byte as it
    #: was.
    portfolio_steer: Optional[Mapping[str, Any]] = None
    PORTFOLIO_STEER_MAX_EXTRA_TICKS = 10

    def portfolio_steer_extra_depth(self) -> int:
        """Extra opening depth the steer asks for: zero or more, bounded.

        IT CAN ONLY DEEPEN. A negative or unreadable value is zero, so the
        steer can make an opening more patient than the owner's approved
        depth and never more aggressive."""
        st = self.portfolio_steer
        if not isinstance(st, Mapping):
            return 0
        try:
            n = int(st.get("extra_depth_ticks") or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, min(n, self.PORTFOLIO_STEER_MAX_EXTRA_TICKS))

    def portfolio_held_reducing_side(self, market: str) -> Optional[str]:
        """The side that reduces FILLED inventory, or None when there is
        none -- or when the ledger cannot say. Resting orders do not count:
        an order that has not filled is not a position to hedge."""
        try:
            held = D(str(self.store.residual(market)))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None
        if held == 0:
            return None
        return DOWN if held > 0 else UP

    def portfolio_steer_refusal(self, side: str,
                                reducing: Optional[str]) -> Optional[str]:
        """Why the steer does not want this leg OPENED, or None.

        A LEG THAT REDUCES A HELD RESIDUAL IS NEVER REFUSED HERE. The steer
        chooses which exposure to take on; it has no say over taking
        exposure off. And an unreadable steer refuses, because a steer that
        cannot be read is not permission to open."""
        st = self.portfolio_steer
        if st is None:
            return None
        if reducing is not None and str(side) == str(reducing):
            return None
        try:
            wanted = st["sides"][str(side)].get("wanted") is True
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return "PORTFOLIO_STEER_UNREADABLE"
        return None if wanted else "PORTFOLIO_SIDE_NOT_WANTED"

    def probe_cash_envelope(self) -> Optional[D]:
        vg = self.validation_gates
        return None if vg is None else vg.max_cash_deployed_usd

    def probe_budget_headroom(self) -> Optional[D]:
        """What the CAMPAIGN budget can still fund, on gate 3b's own basis.

        WHY THE PLANNER NEEDS THIS. `build_orders` used to size against the
        cash envelope and the loss ceiling only, so it emitted a 3.36-contract
        plan whose $4.6368 obligation fit the $4.65 cash gate and was then
        refused by gate 3b at 13.5356 > 11.10 -- the $8.8988 of account-wide
        realized loss already standing against the $11.10 campaign budget.
        A plan the planner's own gates must reject is a wasted window
        reservation, and `WindowLedger.reserve` is deliberately not
        refundable.

        IT REUSES THE GATE'S HELPERS RATHER THAN RE-DERIVING THE FIGURE.
        `cash_committed` and `pending_obligation` are the audited ones gate 3b
        calls, and the realized term is the same `max(0, -realized)`. A second
        opinion computed here could drift from the one actually enforced, and
        then the planner would be sizing to a budget nobody holds.

        None when no campaign budget is configured, which `build_orders`
        treats as unconstrained -- the pre-campaign behaviour, unchanged.
        A figure that cannot be established is NOT reported as room: it
        returns zero, which `build_orders` refuses as exhausted, because
        "I could not tell" and "the budget is empty" must fail the same way.
        """
        vg = self.validation_gates
        if vg is None or getattr(vg, "campaign_budget", None) is None:
            return None
        try:
            # An UNREADABLE realized figure is not a zero loss. `realized_
            # today` returns None when it cannot be established, and reading
            # that as "nothing lost" would hand the planner the whole budget
            # at precisely the moment the accounting is broken.
            realized = finite_decimal(self.store.realized_today())
            budget = finite_decimal(vg.campaign_budget.budget_usd)
            if realized is None or budget is None:
                return D(0)
            open_risk = (vg.cash_committed(self.cash_legs_for_gates())
                         + vg.pending_obligation(self.outstanding_for_gates()))
            # THE CAMPAIGN-SCOPED BASIS, which is the one gate 3b enforces.
            #
            # THE DRIFT THIS REPAIRS, measured live at 2026-11-04T03:34:35Z.
            # Until the campaign budget was scoped to the campaign, the term
            # here was `max(0, -realized)` and gate 3b's was identical, so
            # the two agreed. Scoping the GATE and not the PLANNER made them
            # disagree in the one direction this method exists to prevent:
            # with $10.6211 of standing account-wide realized loss against a
            # $5 budget, the planner read `max(0, 5 - 10.6211)` = 0 and
            # refused every window with CAMPAIGN_BUDGET_EXHAUSTED, while the
            # gate it is supposed to anticipate held $5 of room. Four
            # authorized windows could not place a first leg.
            #
            # So this reuses the baseline the gate reads, exactly as the
            # docstring above requires: the planner must size against the
            # budget somebody actually holds. It is not a relaxation -- gate
            # 3b still evaluates every order independently and still refuses
            # on its own basis; this only stops the planner refusing room
            # the gate would have allowed.
            base = getattr(vg, "campaign_baseline", None)
            if base is None:
                return D(0)
            cs = base.additional_loss_usd(
                getattr(vg, "campaign_id", "") or "",
                account_wide_realized=realized,
                open_worst_case_usd=open_risk)
            if not cs.get("ok"):
                return D(0)          # an unestablished baseline is not room
            used = finite_decimal(cs.get("additional_loss_usd"))
            if used is None:
                return D(0)
            return max(D(0), budget - used)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return D(0)                    # unreadable is never headroom

    def probe_loss_ceiling(self) -> Optional[D]:
        vg = self.validation_gates
        if vg is None:
            return None
        # the DEFICIT bound is the per-window worst case; the cumulative
        # ceiling is run-scoped. The smaller one binds the sizing.
        d = vg.max_worst_case_deficit_usd
        c = vg.max_cumulative_loss_usd
        return c if d is None else min(d, c)

    def acc_max_rounds(self) -> int:
        """How many PAIRS may be worked per market. The accumulation bound.

        One round is one UP+DOWN pair. The old design had exactly one round
        for the whole authorization; this makes the count explicit and
        configurable, and it is a HARD bound: when the rounds for a market
        are spent, the engine refuses rather than continuing.

        It does not replace any risk gate. Cash, the worst-case deficit,
        account-wide exposure, unmatched contracts and the realized-loss
        halts are all still evaluated per round, and each round is sized
        against the state the PREVIOUS rounds left behind -- so accumulation
        stops on whichever of those binds first, usually well before this.
        """
        raw = str(os.getenv(ACC_MAX_ROUNDS_ENV, "")).strip()
        if not raw:
            return ACC_MAX_ROUNDS_DEFAULT
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return 1                      # unparseable = the safe reading
        return max(0, min(n, ACC_MAX_ROUNDS_HARD_CAP))

    def probe_plan_key(self, market: str, rnd: int = 0) -> str:
        from src import l5_live_authorization as AUTH
        return "%s|%s|%d" % (AUTH.experiment_id(), market, int(rnd))

    def probe_stored_plan(self, market: str, rnd: int = 0
                          ) -> Optional[Dict[str, Any]]:
        """The frozen plan for this experiment, market and round, or None."""
        p = (self.store.state.get("probe_plans") or {}).get(
            self.probe_plan_key(market, rnd))
        return dict(p) if isinstance(p, Mapping) else None

    def probe_store_plan(self, market: str, plan: Mapping[str, Any],
                         rnd: int = 0) -> None:
        """Freeze it durably, so leg two matches leg one across a restart.

        PER ROUND, which is what makes re-planning safe. The plan was
        previously frozen per market forever, so the economics of the FIRST
        pair were reused for every later one however far the book had moved.
        Each round now plans against the book and the inventory as they are,
        while the two legs WITHIN a round stay byte-identical across a
        restart -- which is the property the freeze existed for.
        """
        plans = self.store.state.setdefault("probe_plans", {})
        plans[self.probe_plan_key(market, rnd)] = dict(plan)
        self.store.save()

    def probe_round_never_placed(self, market: str, rnd: int) -> bool:
        """True only when NO order was ever submitted in this round.

        Read from the claim files on disk -- the same durable record
        `probe_legs_claimed` reads. A claim is taken BEFORE the order is
        sent, so a claim that exists means an order may exist, whatever
        became of it; and a later attempt is only ever claimed after
        attempt zero. No claim on either leg therefore means no order, no
        fill and nothing to match. Unreadable is False: a round we cannot
        prove empty keeps its frozen plan.
        """
        from src import l5_live_authorization as AUTH
        try:
            if not str(market):
                return False
            for leg in AUTH.PROBE_LEGS:
                if AUTH.claim_held(AUTH.accumulation_sid(
                        AUTH.experiment_id(), str(market), int(rnd), leg)):
                    return False
            return True
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return False

    def round_lifecycle(self, market: str) -> Dict[str, Any]:
        """Rounds SPENT against orders SUBMITTED, for one market. Read-only.

        Everything here is read from the durable claim files and the
        retirement register, so it survives a restart and cannot disagree
        with what `probe_round` will do next. An unreadable claim counts as
        a submission, which is the reading that under-states what is left.
        """
        from src import l5_live_authorization as AUTH
        out: Dict[str, Any] = {
            "market": str(market),
            "charged_on_submission": round_charged_on_submission(),
            "rounds_permitted": None}
        try:
            n = int(self.acc_max_rounds())
            legs = 0
            with_order, retired_no_order, retired_with_order = [], [], []
            _keep = requote_keeps_round()
            requoted: List[int] = []
            for r in range(self.probe_round_index_cap() if _keep else n):
                held = 0
                for leg in AUTH.PROBE_LEGS:
                    try:
                        held += bool(AUTH.claim_held(AUTH.accumulation_sid(
                            AUTH.experiment_id(), str(market), r, leg)))
                    except BaseException as exc:               # noqa: BLE001
                        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            raise
                        held += 1
                legs += held
                ret = self.probe_round_retired(market, r)
                if _keep and (ret or held >= len(AUTH.PROBE_LEGS)) and \
                        self.probe_round_never_filled(market, r):
                    requoted.append(r)
                if held:
                    with_order.append(r)
                    if ret:
                        retired_with_order.append(r)
                elif ret:
                    retired_no_order.append(r)
            spent = len((set(with_order) | set(retired_no_order))
                        - set(requoted))
            if _keep:
                out["requote_keeps_round"] = True
                out["rounds_requoted_without_a_fill"] = len(requoted)
            out.update({
                "rounds_permitted": n,
                "rounds_with_a_submitted_order": len(with_order),
                "legs_submitted": legs,
                "rounds_retired_without_an_order": len(retired_no_order),
                "rounds_retired_after_an_order": len(retired_with_order),
                "rounds_spent": spent,
                "rounds_left": max(0, n - spent),
                "next_round": self.probe_round(market),
                "proposals_repriced_since_start": int(
                    (self.__dict__.get("round_repriced") or {}
                     ).get(str(market), 0)),
                "labels": ("rounds_spent counts a round an order was "
                           "submitted in, plus any round retired without "
                           "one. With charged_on_submission true the second "
                           "kind cannot occur for a round that never "
                           "submitted; a proposal priced again is counted "
                           "in proposals_repriced_since_start and spends "
                           "nothing.")})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["error"] = type(exc).__name__
        return out

    def probe_discard_plan(self, market: str, rnd: int) -> bool:
        """Drop a stored plan that was never acted on. Deletes nothing else.

        Not saved here: the plan that replaces it is saved by
        `probe_store_plan`, and if none does, a restart finds the same
        never-placed round and drops the same plan again.
        """
        plans = self.store.state.get("probe_plans")
        if not isinstance(plans, dict):
            return False
        return plans.pop(self.probe_plan_key(market, rnd), None) is not None

    def probe_legs_claimed(self, market: str = "", rnd: int = 0
                           ) -> Dict[str, bool]:
        """Which legs of THIS round this market has already spent, from DISK.

        The claim file is the restart record: its presence means the leg was
        submitted, so a restarted process cannot re-place a leg whatever its
        in-memory state says.

        SPENT MEANS FINISHED, NOT MERELY SENT ONCE. This asked only about
        attempt ZERO, so the first submission of a leg marked it spent for
        the rest of the round -- whatever became of it. The consequences were
        both wrong and opposite in direction:

          * a leg cancelled UNFILLED at its TTL was never re-placed, and the
            chooser moved on to the OPPOSITE leg, so a round could end
            holding ONE unhedged leg on whichever side happened to fill --
            the exact state a both-or-neither pair exists to avoid;
          * the bounded replacement budget (`L5_ACC_MAX_ATTEMPTS`) was
            therefore unreachable: `acc_leg_attempt` could only ever be
            consulted for attempt 0, because a leg with a later attempt
            available was never selected to be placed again.

        So a claimed leg is spent only when NO further placement of it is
        permitted, which is precisely what `acc_leg_attempt` decides -- and
        it refuses whenever the leg holds filled quantity or an order is
        live, so a FILLED leg can still never be re-placed.
        """
        from src import l5_live_authorization as AUTH
        out = {}
        for leg in AUTH.PROBE_LEGS:
            try:
                held = AUTH.claim_held(AUTH.accumulation_sid(
                    AUTH.experiment_id(), str(market), int(rnd), leg))
                if held and market:
                    held = self.acc_leg_attempt(
                        str(market), int(rnd), leg) is None
                out[leg] = bool(held)
            except BaseException:                              # noqa: BLE001
                out[leg] = True        # unreadable = treat as spent
        return out

    def acc_leg_attempt(self, market: str, rnd: int, leg: str
                        ) -> Optional[int]:
        """Which PLACEMENT of this leg may be made next, or None to refuse.

        THE SAFETY RULE, and it is the whole point of the attempt axis: a leg
        cancelled UNFILLED may be re-placed; a leg that FILLED may never be.
        So a later attempt is granted only when BOTH hold:

          * this leg holds NO filled quantity in this market, and
          * no order is live in this market.

        Both come from the authoritative durable state the rest of the engine
        already uses, rather than from an attempt-to-order map this would
        otherwise have to invent and keep correct across restarts. A filled
        leg therefore cannot be re-placed by any path, and a leg whose
        cancellation was never venue-confirmed cannot either, because an
        unverified cancel leaves the order live here and marks the store
        UNCERTAIN -- which the arming and submission gates already refuse on.

        FAIL CLOSED. An unreadable claim is treated as spent by
        `probe_legs_claimed`, and any exception here returns None.
        """
        side = str(leg or "").upper()
        try:
            from src import l5_live_authorization as AUTH
            cap = AUTH.acc_max_attempts()
            exp = AUTH.experiment_id()
            for a in range(cap):
                try:
                    held = AUTH.claim_held(AUTH.accumulation_sid(
                        exp, str(market), int(rnd), side, a))
                except BaseException:                          # noqa: BLE001
                    return None            # unreadable = refuse, never guess
                if held:
                    continue
                if a == 0:
                    return 0               # the first placement of this leg
                # a RE-placement: only over a leg that holds nothing and has
                # nothing live. Either condition failing means refuse.
                if self.store.position(str(market), side) != D(0):
                    return None
                if self.store.live_order_count():
                    return None
                return a
            return None                    # the placement budget is spent
        except BaseException:                                  # noqa: BLE001
            return None

    def probe_round_retired(self, market: str, rnd: int) -> bool:
        """Has this round been abandoned because the book left its plan?"""
        key = "%s|%d" % (str(market), int(rnd))
        return bool((self.store.state.get("probe_rounds_retired") or {}
                     ).get(key))

    def probe_retire_round(self, market: str, rnd: int, why: str) -> None:
        """Abandon a round durably. ADDITIVE, and it deletes nothing.

        The claims of a retired round STAY on disk -- they are the record
        that those legs were sent, and resetting a claim is never automatic.
        Retirement is a separate durable fact that makes `probe_round` walk
        PAST this round, so the engine re-plans at the current touch in the
        next one instead of re-proposing a pair the market has left.
        """
        reg = self.store.state.setdefault("probe_rounds_retired", {})
        key = "%s|%d" % (str(market), int(rnd))
        if key not in reg:
            reg[key] = {"t": self._now().isoformat(), "why": str(why)[:160]}
            self.store.save()

    def probe_round_is_safe_to_retire(self, market: str,
                                      rnd: int) -> Optional[str]:
        """None if this round may be abandoned, else what forbids it.

        THE TWO THINGS THAT FORBID IT, both read from authoritative durable
        state rather than from memory:

          * ANY order live in this market. An order we may still own cannot
            be orphaned by moving on from the round that owns it -- it has to
            be cancelled by its own deadline first, by exact id, and proved
            terminal by a re-read.
          * UNMATCHED filled quantity, including a PARTIAL. Retiring a
            round that holds an imbalance would strand it: the complement is
            tracked per round, so walking past the round walks past the only
            thing that would have hedged it.

        KEYED ON THE IMBALANCE, NOT ON HOLDING ANYTHING -- the same
        correction the opening floor already carries, and for the same
        reason. This read `position(market, leg) != 0` on either leg, so a
        round became unretirable the instant ANY quantity filled. After a
        pair COMPLETES both sides are non-zero AND EQUAL: there is nothing
        left to strand, because the complement this round was tracking has
        already been bought.

        MEASURED, and it cost a window. 2026-10-28 0015z round 1 matched its
        pair at +29s and +74s, leaving UP 1.87 and DOWN 1.87. Its frozen
        plan had been priced against a 0.62 touch and the book then walked to
        0.46/0.47, so `plan_executable` returned LEG_WOULD_CROSS:UP on every
        later tick -- and retirement was refused with
        `LEG_HOLDS_INVENTORY:UP` against a perfectly balanced book. The round
        could neither execute nor retire, the UP leg at own 0.6000 was
        re-proposed 23 times from 00:17:34 to 00:22:01, and the cross guard
        refused each one: 446 of 600 seconds of entry capacity lost, and 3
        of 4 authorized round allocations never usable. Across the preserved
        tapes 139 ticks in eight windows were blocked this way with a ZERO
        residual.

        An unreadable position or order count forbids it too, because an
        unknown is not a zero.
        """
        try:
            if self.store.live_order_count():
                return "ORDER_STILL_LIVE"
            if self.store.still_fillable_order_count():
                return "ORDER_STILL_FILLABLE"
            up = self.store.position(market, UP)
            dn = self.store.position(market, DOWN)
            if up != dn and not round_retire_with_residual():
                return "LEG_HOLDS_UNMATCHED:%s" % (UP if up > dn else DOWN)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return "STATE_UNREADABLE:%s" % type(exc).__name__
        return None

    def probe_leg_order(self, market: str, legs: Mapping[str, Any],
                        cheapest_first: Sequence[str]) -> List[str]:
        """Which leg to offer next: the one that REDUCES a held residual.

        THE DEFECT THIS FIXES, and it cost two windows. The side came only
        from the frozen plan, cheapest leg first. That rule is right while
        flat and wrong the instant one leg has filled alone: on 1245z the
        book was `up 1.4 / dn 2.8` and the engine spent the last nine
        minutes of the window quoting DOWN -- the side it ALREADY HELD --
        while the UP that would have completed the pair was never offered
        once. 0130z shows the same shape against a DOWN 1.87 residual.

        THE SIGNED RESIDUAL IS THE ONLY INPUT, and it is the PROSPECTIVE
        one: realized inventory plus every outstanding order as if it
        filled, which is the same basis `prospective_residual` and the
        residual cap use. So a partial fill counts its filled part, a
        resting order counts its remainder, and a complement already on the
        book does not get ordered twice.

        CHEAPEST-FIRST IS PRESERVED EXACTLY WHEN THERE IS NOTHING TO
        REDUCE, so a flat market's behaviour is byte-for-byte what it was.
        An unreadable residual also keeps today's rule: this is a
        preference between two already-gated legs, not a safety gate, and
        failing it closed would stop the engine quoting at all.
        """
        try:
            signed = (D(str(self.store.residual(market)))
                      + D(str(self.store.outstanding_residual(market))))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return list(cheapest_first)
        if signed == 0:
            return list(cheapest_first)
        need = DOWN if signed > 0 else UP
        if need not in legs:
            return list(cheapest_first)
        return [need] + [k for k in cheapest_first if k != need]

    def probe_reducing_side(self, market: str) -> Optional[str]:
        """The side whose purchase REDUCES this market's residual, or None.

        THE SAME BASIS `probe_leg_order` USES -- realized inventory plus
        every outstanding order as if it filled -- read through the same two
        store calls, so the eligibility gate and the leg ordering can never
        disagree about whether a completion exists. A flat or matched market
        returns None, and so does an unreadable one: the caller then treats
        the action as an ENTRY and the earlier `may_enter` cutoff applies,
        which fails closed.
        """
        try:
            signed = (D(str(self.store.residual(market)))
                      + D(str(self.store.outstanding_residual(market))))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None
        if signed == 0:
            return None
        return DOWN if signed > 0 else UP

    def probe_leg_qty_cap(self, market: str, side: str, qty: D) -> D:
        """Never overshoot a residual into a reversal on the other side.

        A reducing leg sized by the PAIR plan can be larger than the
        residual it is reducing -- 1.4 offered against 0.5 held flips the
        book to 0.9 the other way and is an opening dressed as a
        completion. The quantity is therefore capped at the magnitude of
        the prospective residual whenever this leg is the reducing one.

        Only ever shrinks, never grows, and leaves an opening leg alone.
        """
        try:
            signed = (D(str(self.store.residual(market)))
                      + D(str(self.store.outstanding_residual(market))))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return qty
        if signed == 0:
            return qty
        reducing = (str(side).upper() == DOWN and signed > 0) or \
                   (str(side).upper() == UP and signed < 0)
        if not reducing:
            return qty
        return min(qty, abs(signed))

    def probe_round_never_filled(self, market: str, rnd: int) -> bool:
        """Did every order this round ever sent end with nothing booked?

        True only when the round's orders are ON RECORD, every one of them
        is finished at the venue, and each booked zero. A round with no
        record -- one from before the record existed, or one whose record
        cannot be read -- is NOT known to be unfilled and answers False,
        which is the reading that charges it.
        """
        try:
            coids = (self.store.state.get("probe_round_orders") or {}).get(
                "%s|%d" % (str(market), int(rnd)))
            if not coids:
                return False
            working = set(self.store.state.get("live_orders") or {}) | set(
                self.store.state.get("pending_terminal") or {})
            for c in coids:
                # AN ORDER STILL AT THE VENUE, or one that has left it and
                # still owes its final read, may yet turn out to have
                # filled. Only an order that is finished AND booked nothing
                # counts as a quote that came to nothing.
                if str(c) in working:
                    return False
                if self.store.applied_fill_qty(str(c)) != D(0):
                    return False
            return True
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return False

    def probe_round_index_cap(self) -> int:
        """How far the round walk may go when a re-quote keeps its round."""
        n = int(self.acc_max_rounds())
        return max(n, min(ACC_ROUND_INDEX_HARD_CAP,
                          n * (1 + ACC_REQUOTES_PER_ROUND)))

    def probe_round(self, market: str) -> Optional[int]:
        """The first round of this market with a leg still unspent, or None.

        Rounds are walked in order from zero and the first incomplete one is
        the current one, so a one-sided round is FINISHED before a new pair
        is started -- the opposite leg of an existing position always takes
        priority over opening another. None means every permitted round for
        this market is spent, and the caller refuses.

        A RE-QUOTE IS NOT AN OPENING. A round abandoned because the book
        left its plan, whose orders were all cancelled with nothing filled,
        took no exposure: it was one opening, quoted again. Counting each
        re-quote against `L5_ACC_MAX_ROUNDS` spent 16 rounds in about four
        minutes at a 15-second quote and then stopped the engine. With the
        owner's switch such a round is walked past WITHOUT being charged;
        a round that filled anything, or that is still working, is charged
        exactly as before. The walk itself is bounded, and every order
        still passes the submission budgets and every other gate.
        """
        budget = int(self.acc_max_rounds())
        if not requote_keeps_round():
            for r in range(budget):
                if self.probe_round_retired(market, r):
                    continue       # abandoned: its plan is not coming back
                claimed = self.probe_legs_claimed(market, r)
                if not all(claimed.values()):
                    return r
            return None
        charged = 0
        for r in range(self.probe_round_index_cap()):
            if self.probe_round_retired(market, r):
                if not self.probe_round_never_filled(market, r):
                    charged += 1
                continue
            if charged >= budget:
                return None
            claimed = self.probe_legs_claimed(market, r)
            if not all(claimed.values()):
                return r
            # BOTH LEGS SPENT. If every order they sent was cancelled with
            # nothing filled, this round was a quote that came to nothing
            # -- with rounds resting side by side the attempt axis cannot
            # re-place a leg, so each re-quote arrives here as a whole
            # round -- and it is not charged. Anything else is.
            if not self.probe_round_never_filled(market, r):
                charged += 1
        return None

    def probe_reopen_decision(self, market: str, rnd: int, plan: Any, *,
                              bid: Any, ask: Any, tick: Any,
                              depth_ticks: Any,
                              now_s: Any, window_end_s: Any
                              ) -> Dict[str, Any]:
        """Price a round beyond the first. Delegates; adds no economics here.

        `probe_round` walks rounds and returns the first incomplete one, with
        no economics at all -- which is exactly how 1230z opened round 1 into
        a flat book two minutes after round 0 had locked its pair, filled one
        leg of it, and settled the window at -$0.4100. The round COUNT was
        never the defect; the MISSING DECISION was. The decision itself lives
        in `l5_unmatched_disposition.reopen_decision` beside `decide`, because
        one module owns inventory economics and a second opinion computed
        here would drift from it.

        THIS ONLY EVER GATES OPENING, and that distinction is the whole
        safety argument. A round with ANY leg already claimed is being
        COMPLETED -- its exposure is already standing, and refusing the
        complement would strand precisely the unmatched leg this gate exists
        to prevent. So a partially-claimed round returns `reopen: True`
        without consulting the economics at all.

        FAILS CLOSED. Anything unreadable refuses the reopening, because the
        safe direction for ADDING exposure is to stop. A refusal never
        touches inventory already held; `decide` still works that.
        """
        def no(reason: str, **kw: Any) -> Dict[str, Any]:
            return dict(kw, reopen=False, reason=reason,
                        market=str(market), round=rnd)
        try:
            rnd_i = int(rnd)
        except (TypeError, ValueError):
            return no("ROUND_INDEX_UNREADABLE")

        # A RETIRED ROUND IS NOT A ROUND USED, and the round INDEX is
        # therefore the wrong thing to count. Retirement abandons a plan the
        # book has walked away from -- `probe_round_is_safe_to_retire`
        # refuses while anything is live or filled -- so the round after one
        # is still the FIRST attempt to take exposure in this market, just
        # at a repriced plan. Gating it would stop the engine re-planning
        # after a 0330z-style frozen plan, which is a different defect and
        # one this branch already fixed.
        #
        # What counts is rounds that actually SPENT legs. Any residual a
        # retirement did leave behind is not lost either: it is reserved
        # against the headroom below, as held inventory.
        try:
            used = sum(1 for r in range(max(rnd_i, 0))
                       if not self.probe_round_retired(market, r))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            used = rnd_i          # unreadable: judge it as a reopening
        # THE FIRST ROUND WAS NEVER PRICED. With the owner's switch it is
        # priced below like any other; without it, nothing changes.
        try:
            from src import l5_unmatched_disposition as _UDP0
            _first_priced = bool(_UDP0.first_opening_priced())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("FIRST_OPENING_SWITCH_UNREADABLE")
        if used <= 0 and not _first_priced:
            return {"reopen": True,
                    "reason": "FIRST_EXPOSURE_TAKING_ROUND_NOT_A_REOPENING",
                    "market": str(market), "round": rnd,
                    "rounds_used": used,
                    "retired_rounds_do_not_count": True}

        # ---- THE PLAN'S OWN LEGS, read before anything that depends on
        #      them. THE DEARER LEG IS THE WORST CASE: which leg fills
        #      alone is not ours to choose, so capacity prices the dear one.
        try:
            _legs = list((plan or {}).get("legs") or [])
            pxs = [D(str(l["price"])) for l in _legs]
            qtys = [D(str(l.get("qty") or 0)) for l in _legs]
            if not pxs:
                return no("PLAN_HAS_NO_LEGS")
            worst_px = max(pxs)
            qty = max(qtys) if qtys else D(0)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("PLAN_UNREADABLE")

        # ---- STANDING EXPOSURE FIRST, FROM DURABLE INVENTORY, and this
        #      ordering is the whole safety argument for the claim read
        #      below. A leg that actually FILLED is in the store and on the
        #      venue; a claim FILE only says a leg was once submitted. So
        #      the thing that protects real inventory from being stranded is
        #      this check, not the claim read -- which means the claim read
        #      is then free to fail CLOSED without stranding anything.
        #
        #      NEVER ON THE SIGNED NET. On 1415z UP 2.17 was HELD while a
        #      DOWN 2.17 completion was RESTING; the signed net came to
        #      exactly 0.00, a gate read the book as FLAT and exempted an
        #      UP 2.08 as a genuine opening leg. The completion was
        #      cancelled unfilled and the position reached UP 4.25 against a
        #      3-contract ceiling. So the question is per side: is there
        #      residual NOT ALREADY BEING WORKED.
        try:
            held = D(str(self.store.residual(market)))
            up_live = D(str(self.store.outstanding_on_side(market, UP)))
            dn_live = D(str(self.store.outstanding_on_side(market, DOWN)))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("PROSPECTIVE_RESIDUAL_UNREADABLE")

        # PROSPECTIVE PER-SIDE TOTALS are the quantity that answers this,
        # and one rule covers every case. For each side: what we HOLD plus
        # every live order on it as if it filled. The side with the SMALLER
        # total is the one a new leg REDUCES; the side with the larger total
        # is the one it would deepen.
        #
        # This is deliberately not the signed net. On 1415z UP 2.17 was HELD
        # while a DOWN 2.17 completion was RESTING; the net came to exactly
        # 0.00, a gate read the book as FLAT and exempted an UP 2.08 as a
        # genuine opening leg. The completion was cancelled unfilled and the
        # position reached UP 4.25 against a 3-contract ceiling. Per side
        # those totals are 2.17 and 2.17 -- EQUAL, no side is smaller, so
        # there is nothing to reduce and the UP is gated as the opening it
        # was. The same arithmetic also admits the cases that must not be
        # stranded: a filled leg with nothing working against it, and a leg
        # SUBMITTED but not yet filled -- which reads as UNCLAIMED by
        # design, since an unfilled leg is deliberately replaceable, and so
        # would otherwise fall through to the economics and have its
        # complement refused.
        try:
            prosp = {}
            for _s in (UP, DOWN):
                prosp[_s] = (abs(D(str(self.store.position(market, _s) or 0)))
                             + (up_live if _s == UP else dn_live))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("PROSPECTIVE_RESIDUAL_UNREADABLE")

        smaller = UP if prosp[UP] < prosp[DOWN] else DOWN
        gap = abs(prosp[UP] - prosp[DOWN])
        if gap > 0:
            # A GAP EXISTING IS NOT ENOUGH. The exemption has to establish
            # that THIS plan's leg actually closes it: the smaller side must
            # be IN the plan, and the quantity that would really be
            # submitted -- after `probe_leg_qty_cap`, the authority on it --
            # must be positive and no larger than the gap. Anything else is
            # an opening wearing a completion's clothes, and it falls
            # through to the full economics below.
            red_leg = None
            for _l in _legs:
                try:
                    if canonical_side(_l.get("side"),
                                      doing="reading a plan leg") == smaller:
                        red_leg = _l
                        break
                except UnknownSide:
                    continue
            if red_leg is not None:
                try:
                    want = D(str(red_leg.get("qty") or 0))
                    capped = self.probe_leg_qty_cap(market, smaller, want)
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    return no("REDUCING_LEG_QTY_UNREADABLE")
                if capped > 0 and capped <= gap:
                    return {"reopen": True, "round": rnd,
                            "market": str(market),
                            "reason":
                                "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS",
                            "held_residual": str(held),
                            "prospective_up": str(prosp[UP]),
                            "prospective_down": str(prosp[DOWN]),
                            "reducing_side": smaller,
                            "imbalance_gap": str(gap),
                            "reducing_leg_qty_requested": str(want),
                            "reducing_leg_qty_after_cap": str(capped),
                            "verified_reduces_not_opens": True}

        # ---- THE CLAIM READ, AND IT FAILS CLOSED.
        #
        # A round with a leg already standing is a COMPLETION, not a
        # reopening, and must not be gated. But an UNREADABLE claim is a
        # missing state, and a missing state may not AUTHORIZE NEW
        # EXPOSURE. My first version returned reopen=True here, reasoning
        # from `acc_stale_by_distance` that one must never veto on a failed
        # read. That lesson is about CANCELLING -- a veto there strands
        # inventory. This is the opposite direction: permitting here ADDS
        # risk. The two cases pull apart and I had them conflated.
        #
        # Failing closed is safe precisely because of the ordering above:
        # real filled inventory was already established from the durable
        # store and the venue's own resting orders, neither of which
        # depends on a claim file. So nothing that actually exists can be
        # stranded by refusing here -- only an unverified opening is.
        try:
            claimed = self.probe_legs_claimed(market, rnd) or {}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("CLAIMS_UNREADABLE_NO_UNVERIFIED_OPENING",
                      detail=str(exc)[:120],
                      durable_inventory_already_checked=True)
        if any(claimed.values()):
            return {"reopen": True, "round": rnd, "market": str(market),
                    "reason": ("ROUND_ALREADY_HAS_A_LEG_"
                               "COMPLETING_NOT_OPENING"),
                    "claimed": {str(k): bool(v)
                                for k, v in claimed.items()}}

        # the budget actually enforced, not a second opinion of it
        try:
            room = self.probe_budget_headroom()
            ceil = self.probe_loss_ceiling()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("BUDGET_UNREADABLE")
        # None from either means "not configured" upstream, which the
        # planner treats as unconstrained. For ADDING exposure that is not
        # good enough: an unbounded reopening is the one this gate refuses.
        if room is None and ceil is None:
            return no("NO_CONFIGURED_EXPOSURE_BOUND_TO_REOPEN_AGAINST")
        bound = ceil if room is None else (
            room if ceil is None else min(room, ceil))

        # THE WORST CASE RESERVED HERE covers held inventory AND live orders
        # on EITHER side filling alone -- `max`, not the sum, because if
        # both sides fill they match each other, and not the net, for the
        # 1415z reason above. On 1130z four legs each calling itself the
        # opening one filled inside seven seconds, so what we have ALREADY
        # COMMITTED TO is the bound, not what we hold.
        outstanding = abs(held) + max(up_live, dn_live)

        # locked pair economics are REPORTED and never financed against
        try:
            locked = self.probe_locked_pair_pnl(market)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            locked = None

        # ---- A PAIR OPENED BESIDE RESTING ONES IS A LADDER LEVEL, and it
        #      needs a free level and room for BOTH legs. This is the same
        #      test the runner's timer path asks, repeated here because an
        #      L5 action reaches this method without passing through it.
        #
        #      AT THE ORDER CAP IT SAYS NOTHING. A fresh round with no order
        #      slot left is stopped by MAX_LIVE_ORDERS further down, by that
        #      name, and four existing suites and the dashboards read that
        #      name. My first version ran this unconditionally and renamed
        #      the refusal -- same outcome, different string -- which the
        #      full suite caught.
        #
        #      WITH A SLOT FREE AND THE LADDER OFF IT REFUSES, and that is a
        #      tightening the runner harness found: once the owner's order
        #      limit is above two, the pass loop's second call opened the
        #      first leg of a NEW round beside a resting pair, and nothing
        #      could then place its complement. A pair that can only have
        #      one leg is not opened.
        concurrent = False
        ladder_on = TS.concurrent_rounds() > 1
        try:
            resting_here = self.probe_resting_in_market(market)
            slot_free = (int(self.store.still_fillable_order_count())
                         < int(self.limits.max_live_orders))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if ladder_on:
                return no("LIVE_ORDERS_UNREADABLE",
                          detail=type(exc).__name__)
            resting_here, slot_free = 0, False
        if resting_here > 0 and (ladder_on or slot_free):
            concurrent = ladder_on
            _lane = TS.concurrent_round_slot_refusal(
                market=market, live_orders=self.store.live_orders(),
                max_live_orders=self.limits.max_live_orders)
            if _lane is not None:
                return no("CONCURRENT_ROUND_REFUSED:%s" % _lane,
                          resting_in_market=resting_here,
                          concurrent_rounds=TS.concurrent_rounds(),
                          max_live_orders=self.limits.max_live_orders)

        from src import l5_unmatched_disposition as UDP
        # WHAT OUR OWN SETTLED WINDOWS PAID. A ledger with no such reader
        # (a double, an older store) contributes nothing and the decision
        # stands on its priors as before; a reader that RAISES is an
        # unreadable record, and an unreadable record does not open a round.
        own_evidence = None
        _own = getattr(self.store, "own_settled_round_evidence", None)
        if callable(_own):
            try:
                own_evidence = _own()
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                return no("OWN_SETTLED_EVIDENCE_UNREADABLE",
                          detail=type(exc).__name__)
        try:
            return dict(UDP.reopen_decision(
                market=market, rounds_used=used,
                locked_pair_pnl_usd=locked,
                worst_leg_own_px=worst_px, qty=qty,
                bid=bid, ask=ask, tick=tick, depth_ticks=depth_ticks,
                now_s=now_s, window_end_s=window_end_s,
                exposure_headroom_usd=bound,
                outstanding_worst_case_usd=outstanding * worst_px,
                daily_loss_room_usd=ceil,
                own_evidence=own_evidence, concurrent=concurrent),
                round=rnd, concurrent=concurrent)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("REOPEN_DECISION_RAISED", detail=str(exc)[:120])

    def probe_locked_pair_pnl(self, market: str) -> Optional[D]:
        """Matched-pair P&L already locked in this market, for the RECORD.

        Reported by the reopening decision and deliberately excluded from
        both of its tests. "We are +$0.045 up, so a -$0.455 risk is
        affordable" is the reasoning that lost 1230z, and it is wrong twice:
        the locked profit was a tenth of the risk, and netting them spends a
        realized gain as a loss reserve. None when it cannot be established,
        which changes no decision because no decision reads it.
        """
        try:
            up = D(str(self.store.position(market, UP) or 0))
            dn = D(str(self.store.position(market, DOWN) or 0))
            matched = min(abs(up), abs(dn))
            if matched <= 0:
                return D(0)
            if up == 0 or dn == 0:
                return None
            costs = self.store.leg_cost(market) or {}
            cu = D(str(costs.get(UP) or 0))
            cd = D(str(costs.get(DOWN) or 0))
            basis = (cu / abs(up)) + (cd / abs(dn))
            return (matched * (D(1) - basis)).quantize(D("0.0001"))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None

    # ============================= WHAT KIND OF ORDER IS THIS, AND MAY IT GO
    #
    # A flat "N submissions per authorization" bound cannot tell an order
    # that TAKES ON risk from one that UNWINDS it, so it is spent by the
    # first kind and then refuses the second -- stranding exactly the
    # unmatched exposure it was meant to contain. These two methods are what
    # let the budget be split by what the order does.
    #
    # BOTH READ THE PROSPECTIVE BOOK, never the realized one. "Prospective"
    # is realized inventory PLUS every outstanding order in this market as
    # if it filled, which is the same basis the residual cap uses and for
    # the same reason: on 1130z four legs each calling itself the opening
    # one filled inside seven seconds. What we HAVE is not the bound; what
    # we have ALREADY COMMITTED TO is.

    def prospective_residual(self, market: str, dec: Mapping[str, Any],
                             tr: Mapping[str, Any]) -> Dict[str, Any]:
        """Signed unmatched inventory before and after, if everything fills."""
        before = D(str(self.store.residual(market)))
        outstanding = D(str(self.store.outstanding_residual(market)))
        prosp_before = before + outstanding
        qty = D(str(tr.get("qty") or 0))
        signed = qty if str(dec.get("side")).upper() == UP else -qty
        prosp_after = prosp_before + signed
        return {"realized": before, "outstanding": outstanding,
                "before": prosp_before, "after": prosp_after,
                "signed_qty": signed,
                "abs_before": abs(prosp_before), "abs_after": abs(prosp_after),
                "basis": "PROSPECTIVE_NET: realized + every live order in "
                         "this market as if it filled"}

    def submission_class(self, market: str, dec: Mapping[str, Any],
                         tr: Mapping[str, Any], *,
                         attempt: Optional[int] = None) -> str:
        """One of AUTH.SUBMISSION_CLASSES, from the order's EFFECT.

        REPLACE first, and whatever the direction. A re-price of a leg
        already placed once is a replacement -- that is the act the owner
        asked to bound separately, and letting it be charged to OPEN or
        COMPLETE instead would make repricing consume the budget for new
        decisions.

        Then COMPLETE for anything that strictly reduces |prospective
        unmatched|, and OPEN for everything else. A flat order -- one that
        changes nothing -- is OPEN, deliberately: it is not reducing, and
        the scarce budget is the right place for an order whose effect we
        cannot call risk-reducing.
        """
        if attempt is not None:
            try:
                if int(attempt) > 0:
                    return "REPLACE"
            except (TypeError, ValueError):
                return "REPLACE"      # unreadable attempt: the bounded class
        pr = self.prospective_residual(market, dec, tr)
        if pr["abs_after"] < pr["abs_before"]:
            return "COMPLETE"
        return "OPEN"

    def balancing_refusal(self, market: str, dec: Mapping[str, Any],
                          tr: Mapping[str, Any], klass: str
                          ) -> Optional[Dict[str, Any]]:
        """None if a risk-reducing order may go, else why not.

        A looser COMPLETE budget is only safe if an order in that class
        genuinely cannot add inventory. Two conditions, both on the
        prospective book:

          REDUCES    |after| must be strictly less than |before|. An order
                     charged to the risk-reducing budget that does not
                     reduce risk is the whole failure mode this prevents.
          NO OVERSHOOT  it may not cross zero. A complement larger than the
                     remaining unmatched need would retire the imbalance and
                     open an equal one on the other side -- "balancing" that
                     leaves the account exactly as exposed, with another
                     pair of fees paid for it.

        Cash and outstanding-order commitments are NOT re-checked here. They
        are enforced account-wide at the cash gate and at
        `max_live_orders`, both of which this order still passes; a second
        copy of that arithmetic here is what produced the phantom
        `CASH_COMMITMENT:5.9760>4.65` by counting a filled leg twice.
        """
        if str(klass).upper() != "COMPLETE":
            return None
        pr = self.prospective_residual(market, dec, tr)
        # OVERSHOOT FIRST, so the refusal names the real fault and carries
        # the size that WOULD have been allowed. A large enough overshoot
        # also fails the reduction test, and reporting it as "does not
        # reduce" would hide both the cause and the remedy.
        crosses = ((pr["before"] > 0 and pr["after"] < 0)
                   or (pr["before"] < 0 and pr["after"] > 0))
        if crosses:
            need = pr["before"] if pr["before"] > 0 else -pr["before"]
            return {"reason": "BALANCING_WOULD_OVERSHOOT",
                    "prospective_before": str(pr["before"]),
                    "prospective_after": str(pr["after"]),
                    "max_qty": str(need),
                    "note": "larger than the remaining unmatched need: it "
                            "would open an equal imbalance on the other side"}
        if pr["abs_after"] >= pr["abs_before"]:
            return {"reason": "BALANCING_DOES_NOT_REDUCE",
                    "prospective_before": str(pr["before"]),
                    "prospective_after": str(pr["after"]),
                    "note": "charged to the risk-reducing budget but does "
                            "not reduce |unmatched| on the prospective book"}
        return None

    def defensive_execute(self, *, market: str, buy_side: str,
                          held_side: str, resting, decide_kwargs,
                          prov) -> Dict[str, Any]:
        """Retire the resting complement, PROVE it is gone, then authorize
        exactly one crossing order -- or refuse and change nothing.

        THE ORDER OF OPERATIONS IS THE SAFETY PROPERTY. Cancel, prove by the
        venue's own re-read, re-read OUR OWN inventory, decide again against
        what is actually there, charge the budget, and only then issue a
        mandate naming that one order. Every failure returns `ok: False` and
        leaves the position exactly as it was: the complement either still
        rests (cancel refused) or is gone and nothing replaces it (which is
        the state the window was heading for anyway).

        OVERHEDGING IS REFUSED, NOT RESIZED IN FLIGHT. If the complement
        filled while we were cancelling it, the residual it was there to
        match has already shrunk -- possibly to nothing -- so this tick
        stops. The next tick re-reads the position and decides again from
        venue-confirmed state rather than from a quantity computed before
        the fill existed.
        """
        def no(reason: str, **extra: Any) -> Dict[str, Any]:
            return dict({"ok": False, "reason": reason}, **extra)

        # THE SIDES, VALIDATED FIRST, BEFORE ANY STATE IS READ OR ANY ORDER
        # IS CANCELLED. Two sides that are not opposed do not describe a
        # reduction of anything, and a side this venue does not have cannot
        # be priced -- `position()` would answer 0 for it and the quantity
        # arithmetic below would compute a plausible-looking order for a
        # market leg that does not exist. Checked here rather than later so
        # the refusal names the real fault instead of surfacing as
        # NOTHING_UNMATCHED, which is what a same-side pair produces.
        if buy_side not in (UP, DOWN) or held_side not in (UP, DOWN) \
                or buy_side == held_side:
            return no("DEFENSIVE_SIDES_NOT_OPPOSED",
                      buy_side=str(buy_side), held_side=str(held_side))

        if "__unreadable__" in (resting or []):
            return no("DEFENSIVE_LIVE_ORDERS_UNREADABLE")

        # ONE DEFENSIVE ATTEMPT PER WINDOW, AND ONE CHARGE FOR IT. A mandate
        # outlives the order it was issued for -- it expires on the clock,
        # not on consumption -- so without this a defensive order refused
        # further downstream (cash, the submission claim, the validation
        # gates) could be rebuilt identically on the next tick and ride the
        # mandate that was already paid for. Refusing here means the budget
        # is charged exactly once per window and the refusal reason stays in
        # the record where it can be read. Fail closed: a window whose
        # defensive attempt did not reach the venue stays as it was.
        _live = self._defensive_mandate
        if isinstance(_live, Mapping):
            try:
                _unexpired = (float(self._now().timestamp())
                              < float(_live["expires_at"]))
            except BaseException:                              # noqa: BLE001
                _unexpired = True          # unreadable expiry blocks
            if _unexpired and str(_live.get("market")) == str(market):
                return no("DEFENSIVE_ALREADY_MANDATED",
                          mandate=dict(_live))

        confirmations: List[Dict[str, Any]] = []
        filled_during = D(0)
        if resting:
            try:
                res = deadman(self.store, self.transport,
                              reason="DEFENSIVE_COMPLETION", only=resting)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                return no("DEFENSIVE_CANCEL_FAILED",
                          error="%s: %s" % (type(exc).__name__,
                                            str(exc)[:120]))
            if prov is not None:
                try:
                    prov.note("DEFENSIVE_CANCEL", dict(
                        res, labels=("recorded BEFORE the verdict that could "
                                     "submit, so a cancellation stands in "
                                     "the record even if everything after "
                                     "it refuses or raises.")))
                except BaseException:                          # noqa: BLE001
                    pass
            for coid in resting:
                c = DEF.cancel_confirmed(res, coid)
                confirmations.append(c)
                if not c.get("confirmed"):
                    return no("DEFENSIVE_CANCEL_NOT_CONFIRMED",
                              confirmations=confirmations)
                filled_during += D(str(c.get("filled_during_cancel") or 0))
            if filled_during > 0:
                return no("DEFENSIVE_FILLED_DURING_CANCEL",
                          filled_during_cancel=str(filled_during),
                          confirmations=confirmations)

        # THE INVENTORY AFTER THE CANCEL is the only inventory that may be
        # defended, read from the store rather than carried in from before.
        try:
            held = self.store.position(market, held_side)
            mine = self.store.position(market, buy_side)
            inc = getattr(self.transport, "quantity_increment", None)
            inc = None if inc is None else D(str(inc))
        except BaseException:                                  # noqa: BLE001
            return no("DEFENSIVE_POSITION_UNREADABLE")
        need = held - mine
        if need <= 0:
            return no("DEFENSIVE_NOTHING_UNMATCHED", unmatched=str(need))
        if inc is None or inc <= 0:
            return no("DEFENSIVE_QUANTITY_INCREMENT_UNKNOWN")
        qty = (need / inc).to_integral_value(rounding="ROUND_FLOOR") * inc
        if qty <= 0:
            return no("DEFENSIVE_BELOW_MIN_INCREMENT",
                      unmatched=str(need), increment=str(inc))

        # EXPOSURE REDUCTION IS A PROPERTY OF THE DERIVATION, AND THE TWO
        # CHECKS BELOW ARE ASSERTIONS OF IT RATHER THAN THE PROTECTION.
        #
        # Being precise about this, because a guard that reads like a
        # safeguard and cannot fire is worse than no guard: it implies a
        # protection that is not there. With the sides validated as opposed
        # at the top of this method, `need = position(held) -
        # position(buy)` being positive ALREADY means the held side is the
        # long one. residual is defined as position(UP) - position(DOWN), so
        # need > 0 with held UP forces residual > 0 and buy DOWN, and held
        # DOWN forces residual < 0 and buy UP. The direction cannot come out
        # wrong, and `qty = floor(need/inc)*inc <= need = |residual|` cannot
        # overshoot flat.
        #
        # They are kept because they pin that invariant to the store's own
        # venue-confirmed positions rather than to this function's local
        # arithmetic. If `need` is ever sourced differently -- from a plan, a
        # cached snapshot, a venue read -- these fire instead of a
        # directional position being doubled while the record calls it a
        # defensive exit. Tested by construction in both directions, not by
        # pretending an unreachable branch is reachable.
        try:
            resid = self.store.residual(market)
        except BaseException:                                  # noqa: BLE001
            return no("DEFENSIVE_RESIDUAL_UNREADABLE")

        # AND THE EXPOSURE IS THE VENUE'S, NOT OURS. Everything above reads
        # the local ledger. The ledger is written from venue-confirmed fills
        # and the cancel's own re-read refreshed it moments ago, but "our
        # books say we are long 3.39" is not the same claim as "the venue
        # says we are long 3.39" -- and only the second one is the exposure
        # a defensive order has to reduce.
        #
        # `position_row` is the parser that refuses rather than guesses: a
        # row carries a SIGNED net plus unsigned bought/sold halves, the
        # halves decide the sign (long-positive by construction), and a
        # stated net that disagrees with them refuses the row. Summed over
        # this market the signed net IS the residual in our terms, including
        # for a matched pair -- we buy UP and wire "buy DOWN" as a SELL, so
        # a matched book nets to zero at the venue exactly as it does here.
        #
        # FAIL CLOSED ON EVERY UNCERTAINTY: an endpoint that raises, a shape
        # the parser will not accept, a market with no row while we believe
        # we hold one, or any disagreement at all. The consequence of
        # refusing is that the leg holds to settlement, which is the
        # behaviour this window had before the feature existed -- never a
        # defensive order placed against a position we cannot confirm.
        try:
            rows = [position_row(r)
                    for r in (self.transport.positions() or [])]
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("DEFENSIVE_VENUE_POSITIONS_UNREADABLE",
                      error="%s: %s" % (type(exc).__name__, str(exc)[:160]))
        mine_rows = [r for r in rows
                     if canonical_market(r["market"], doing="defensive "
                                         "venue reconciliation") == market]
        if not mine_rows:
            return no("DEFENSIVE_VENUE_POSITION_ABSENT",
                      residual_local=str(resid),
                      note=("we believe we hold an unmatched leg and the "
                            "venue reports no position in this market"))
        venue_net = sum((D(r["signed_net"]) for r in mine_rows), D(0))
        if venue_net != resid:
            return no("DEFENSIVE_VENUE_POSITION_DISAGREES",
                      residual_local=str(resid),
                      residual_venue=str(venue_net),
                      rows=[dict(r) for r in mine_rows],
                      note=("the ledger and the venue do not agree on this "
                            "market's exposure, so neither may be used to "
                            "size an order that spends money"))

        reduces = ((buy_side == DOWN and resid > 0)
                   or (buy_side == UP and resid < 0))
        if not reduces:
            return no("DEFENSIVE_WOULD_INCREASE_EXPOSURE",
                      buy_side=buy_side, held_side=held_side,
                      residual=str(resid), unmatched=str(need),
                      note=("residual is UP - DOWN: an unmatched UP is "
                            "reduced only by buying DOWN, and an unmatched "
                            "DOWN only by buying UP"))
        if qty > abs(resid):
            return no("DEFENSIVE_WOULD_REVERSE_EXPOSURE",
                      qty=str(qty), residual=str(resid),
                      note=("a defensive order may bring the position to "
                            "flat, never through it"))

        # PHASE TWO. The only verdict in this system that can submit a
        # defensive order, and it runs against the post-cancel quantity with
        # the cancellation declared and CONFIRMED.
        rc = (None if not resting
              else {"venue_order_id": resting[0], "cancel_confirmed": True})
        verdict = DEF.decide(**dict(decide_kwargs, unmatched_qty=qty,
                                    resting_complement=rc))
        verdict = dict(verdict, phase="AUTHORIZE")
        if not verdict.get("act"):
            return no("DEFENSIVE_REFUSED_AFTER_CANCEL", verdict=verdict,
                      confirmations=confirmations)

        px = D(str(verdict["exit_price"]))
        # CHARGE THE BUDGET BEFORE THE MANDATE AND BEFORE THE WIRE, at the
        # worst case. See `InventoryStore.defensive_charge`.
        try:
            total = self.store.defensive_charge(
                verdict["exit_downside_usd"],
                {"market": market, "buy_side": buy_side, "qty": str(qty),
                 "price": str(px), "reason": verdict.get("reason")})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("DEFENSIVE_BUDGET_NOT_CHARGEABLE",
                      error=type(exc).__name__)

        expires = None
        try:
            expires = (float(decide_kwargs["window_start"])
                       + float(verdict["binding_terminal_s"]))
        except BaseException:                                  # noqa: BLE001
            expires = None
        if expires is None:
            return no("DEFENSIVE_MANDATE_HAS_NO_EXPIRY")
        self._defensive_mandate = {
            "market": market, "side": buy_side, "price": str(px),
            "qty": str(qty), "expires_at": expires,
            # NAMED, not implied. `defensive_mandate_covers` gates a
            # defensive mandate on DEF.enabled() and a taker-completion
            # mandate on its own two switches, so the origin has to be on
            # the record rather than inferred from which method wrote it.
            "origin": "DEFENSIVE",
            "reason": verdict.get("reason"),
            "budget_total_usd": str(total),
            "labels": ("permits ONE crossing order and the defensive pair "
                       "ceiling for that order alone. It is not a change to "
                       "the maker-only overlay or to the 0.96 ceiling, "
                       "which stand for everything else."),
        }
        return {"ok": True, "verdict": verdict,
                "confirmations": confirmations,
                "exposure": {"residual_before": str(resid),
                             "residual_venue_confirmed": str(venue_net),
                             "residual_after": str(
                                 resid - qty if buy_side == DOWN
                                 else resid + qty),
                             "held_side": held_side, "buy_side": buy_side,
                             "basis": ("venue-confirmed fills only. This is "
                                       "the exposure the order REDUCES; it "
                                       "is not hedged until the venue "
                                       "confirms a fill.")},
                "mandate": dict(self._defensive_mandate),
                "leg": {"side": buy_side, "action": "BUY", "price": str(px),
                        "qty": str(qty), "mode": FT.TAKER,
                        "post_only": False,
                        "defensive": True,
                        "wire": verdict.get("wire"),
                        "labels": ("a crossing order against displayed "
                                   "depth. Displayed size is not a fill and "
                                   "this position is NOT hedged until the "
                                   "venue confirms one.")}}

    def completion_taker_execute(self, *, market: str, buy_side: str,
                                 held_side: str, resting,
                                 proposal: Mapping[str, Any],
                                 window_start: Any, now_t: Any,
                                 prov=None) -> Dict[str, Any]:
        """Retire the resting complement, PROVE it is gone, then authorize
        exactly one bounded crossing completion -- or refuse and change
        nothing.

        THIS IS THE ROUTE `maybe_ts_completion` DID NOT HAVE. That method
        computed the economics, asked the bounded policy, got a yes, and
        wrote `crossed: "WOULD_SUBMIT"` with `submission_route: "deliberately
        not wired"`. On 2026-11-06 it did that 43 times in the 0300z window
        and 22 times in 0315z, against worst-case improvements of $0.44-$0.79
        and $0.04-$0.35, while both residuals rode to settlement for -$1.00.
        This is the wiring, and it is the SAME order of operations the
        defensive unwind uses, because that order IS the safety property:

          cancel -> prove by the venue's own re-read -> re-read OUR OWN
          inventory -> re-check the authorized size against what is actually
          there -> cross-check the venue's exposure -> charge the budget ->
          issue a mandate naming that one order.

        Every failure returns `ok: False` and leaves the position exactly as
        it was. The complement either still rests (cancel refused) or is gone
        and nothing replaces it, which is the state the window was heading for
        anyway.

        WHAT IT SHARES WITH THE DEFENSIVE PATH, DELIBERATELY. One mandate
        slot, so "one crossing order per market per window" stays one
        invariant; one `defensive_charge` budget, because both are crossing
        orders bought to reduce a residual and a second budget would let the
        pair of them spend twice what either was allowed; and one exemption
        point in the maker-only overlay.

        WHAT IS ITS OWN. The authorization. `proposal` is the runner's
        already-computed `l5_taker_completion_policy.authorize` result, whose
        conservative quantity and VWAP are what this may submit -- never the
        quantity that was hoped for. The two default-off switches are checked
        here as well as at the exemption point, because a route that is
        unreachable when off is better than one that is merely unlikely to
        fire.

        OVERHEDGING IS REFUSED, NOT RESIZED IN FLIGHT. If the complement
        filled while we were cancelling it the residual has already shrunk,
        so this tick stops and the next one decides again from
        venue-confirmed state.
        """
        def no(reason: str, **extra: Any) -> Dict[str, Any]:
            return dict({"ok": False, "reason": reason}, **extra)

        # ---- 0. THE SWITCHES, BEFORE ANY STATE IS READ OR ANY ORDER IS
        # CANCELLED. With either off this method must be indistinguishable
        # from not existing.
        if not TCP.taker_completion_enabled():
            return no("COMPLETION_TAKER_NOT_ENABLED",
                      policy=TCP.policy_enabled(),
                      owner=TCP.owner_cross_authorized())
        if not isinstance(proposal, Mapping) or not proposal.get("authorized"):
            return no("COMPLETION_TAKER_NOT_AUTHORIZED",
                      proposal_reason=str((proposal or {}).get("reason"))[:60])
        if buy_side not in (UP, DOWN) or held_side not in (UP, DOWN) \
                or buy_side == held_side:
            return no("COMPLETION_SIDES_NOT_OPPOSED",
                      buy_side=str(buy_side), held_side=str(held_side))
        if str(proposal.get("side") or "").strip().upper() != buy_side:
            return no("COMPLETION_PROPOSAL_SIDE_MISMATCH",
                      proposal_side=str(proposal.get("side")),
                      buy_side=buy_side)
        # ---- THE PROPOSAL MUST NAME THIS MARKET, and it is checked here
        # rather than trusted. Today the runner sets the proposal and calls
        # `handle` synchronously for the same slug, so a mismatch is
        # unreachable -- which is exactly why it is worth refusing now: the
        # side check alone would not catch a proposal priced on one market's
        # book reaching another's, and the mandate would then name an order
        # in the wrong market. An absent market reads as a mismatch, not as a
        # wildcard.
        if str(proposal.get("market") or "") != str(market):
            return no("COMPLETION_PROPOSAL_MARKET_MISMATCH",
                      proposal_market=str(proposal.get("market"))[:60],
                      market=str(market)[:60])
        if "__unreadable__" in (resting or []):
            return no("COMPLETION_LIVE_ORDERS_UNREADABLE")

        # ---- 1. AN UNCERTAIN LEDGER IS NOT PERMISSION TO SPEND. While we
        # cannot say whether an earlier order reached the venue, a crossing
        # order could be the second copy of one already resting.
        try:
            unc = self.store.orders_uncertain()
        except BaseException:                                  # noqa: BLE001
            return no("COMPLETION_UNCERTAINTY_UNREADABLE")
        if unc:
            return no("COMPLETION_ORDERS_UNCERTAIN", why=str(unc)[:120])

        # ---- 2. ONE CROSSING ORDER PER MARKET PER WINDOW. The mandate
        # outlives the order it was issued for -- it expires on the clock,
        # not on consumption -- so without this a completion refused further
        # downstream could be rebuilt identically next tick and ride a
        # mandate that was already paid for.
        _live = self._defensive_mandate
        if isinstance(_live, Mapping):
            try:
                _unexpired = (float(self._now().timestamp())
                              < float(_live["expires_at"]))
            except BaseException:                              # noqa: BLE001
                _unexpired = True          # unreadable expiry blocks
            if _unexpired and str(_live.get("market")) == str(market):
                return no("COMPLETION_ALREADY_MANDATED",
                          mandate=dict(_live))

        # ---- 3. THE DURABLE CLAIM, written BEFORE the wire and never
        # released by a retry. This is what makes an uncertain
        # acknowledgement and a restart safe: the claim is on the volume, so
        # a process that dies between here and the venue comes back already
        # holding it and does not try again.
        claim_key = "%s|%s" % (market, buy_side)
        try:
            claims = self.store.state.setdefault("ts_completion_claims", {})
            held_claim = claims.get(claim_key)
        except BaseException:                                  # noqa: BLE001
            return no("COMPLETION_CLAIM_LEDGER_UNREADABLE")
        if isinstance(held_claim, Mapping):
            try:
                same_window = (str(held_claim.get("window_start"))
                               == str(window_start))
            except BaseException:                              # noqa: BLE001
                same_window = True
            if same_window:
                return no("COMPLETION_ALREADY_CLAIMED",
                          claim=dict(held_claim),
                          note=("a crossing completion was already claimed "
                                "for this market, side and window. It is "
                                "never retried: an unknown order is assumed "
                                "live, because assuming otherwise is the "
                                "assumption that loses money"))

        # ---- 4. CANCEL AND PROVE. Exactly the named orders, by venue id,
        # through the existing deadman with proof by re-read.
        confirmations: List[Dict[str, Any]] = []
        filled_during = D(0)
        if resting:
            try:
                res = deadman(self.store, self.transport,
                              reason="TAKER_COMPLETION", only=resting)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                return no("COMPLETION_CANCEL_FAILED",
                          error="%s: %s" % (type(exc).__name__,
                                            str(exc)[:120]))
            if prov is not None:
                try:
                    prov.note("TAKER_COMPLETION_CANCEL", dict(
                        res, labels=("recorded BEFORE the verdict that could "
                                     "submit, so a cancellation stands in "
                                     "the record even if everything after "
                                     "it refuses or raises.")))
                except BaseException:                          # noqa: BLE001
                    pass
            for coid in resting:
                c = DEF.cancel_confirmed(res, coid)
                confirmations.append(c)
                if not c.get("confirmed"):
                    return no("COMPLETION_CANCEL_NOT_CONFIRMED",
                              confirmations=confirmations)
                filled_during += D(str(c.get("filled_during_cancel") or 0))
            if filled_during > 0:
                return no("COMPLETION_FILLED_DURING_CANCEL",
                          filled_during_cancel=str(filled_during),
                          confirmations=confirmations)

        # ---- 5. THE INVENTORY AFTER THE CANCEL is the only inventory that
        # may be completed, read from the store rather than carried in.
        try:
            held = self.store.position(market, held_side)
            mine = self.store.position(market, buy_side)
            inc = getattr(self.transport, "quantity_increment", None)
            inc = None if inc is None else D(str(inc))
        except BaseException:                                  # noqa: BLE001
            return no("COMPLETION_POSITION_UNREADABLE")
        need = held - mine
        if need <= 0:
            return no("COMPLETION_NOTHING_UNMATCHED", unmatched=str(need))
        if inc is None or inc <= 0:
            return no("COMPLETION_QUANTITY_INCREMENT_UNKNOWN")

        # ---- 6. NEVER MORE THAN THE CONFIRMED DEFICIT, AND NEVER MORE THAN
        # THE POLICY AUTHORIZED. The policy's figure is its CONSERVATIVE
        # quantity, computed against haircut depth; the deficit is what the
        # ledger says is actually unmatched now. The order is the smaller.
        want = _money_or_none(proposal.get("qty"))
        px = _money_or_none(proposal.get("price"))
        if want is None or want <= 0:
            return no("COMPLETION_PROPOSAL_QTY_UNREADABLE")
        if px is None or px <= 0 or px >= D(1):
            return no("COMPLETION_PROPOSAL_PRICE_UNREADABLE")
        qty = floor_to_increment(min(want, need), inc)
        if qty <= 0:
            return no("COMPLETION_BELOW_MIN_INCREMENT",
                      unmatched=str(need), authorized=str(want),
                      increment=str(inc))

        # ---- 7. IT MUST REDUCE, AND IT MUST NOT REVERSE. Asserted against
        # the store's own venue-confirmed positions rather than against this
        # function's arithmetic, for the same reason the defensive path
        # asserts it: if `need` is ever sourced differently these fire
        # instead of a directional position being doubled while the record
        # calls it a completion.
        try:
            resid = self.store.residual(market)
        except BaseException:                                  # noqa: BLE001
            return no("COMPLETION_RESIDUAL_UNREADABLE")
        reduces = ((buy_side == DOWN and resid > 0)
                   or (buy_side == UP and resid < 0))
        if not reduces:
            return no("COMPLETION_WOULD_INCREASE_EXPOSURE",
                      buy_side=buy_side, held_side=held_side,
                      residual=str(resid), unmatched=str(need))
        if qty > abs(resid):
            return no("COMPLETION_WOULD_REVERSE_EXPOSURE",
                      qty=str(qty), residual=str(resid))

        # ---- 8. AND THE EXPOSURE IS THE VENUE'S, NOT OURS. "Our books say
        # we are long 1" is not the claim "the venue says we are long 1", and
        # only the second is the exposure a completion has to reduce. Fail
        # closed on an endpoint that raises, a shape the parser will not
        # accept, a market with no row while we believe we hold one, or any
        # disagreement at all.
        try:
            rows = [position_row(r)
                    for r in (self.transport.positions() or [])]
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("COMPLETION_VENUE_POSITIONS_UNREADABLE",
                      error="%s: %s" % (type(exc).__name__, str(exc)[:160]))
        mine_rows = [r for r in rows
                     if canonical_market(r["market"], doing="taker "
                                         "completion venue reconciliation")
                     == market]
        if not mine_rows:
            return no("COMPLETION_VENUE_POSITION_ABSENT",
                      residual_local=str(resid))
        venue_net = sum((D(r["signed_net"]) for r in mine_rows), D(0))
        if venue_net != resid:
            return no("COMPLETION_VENUE_POSITION_DISAGREES",
                      residual_local=str(resid),
                      residual_venue=str(venue_net),
                      rows=[dict(r) for r in mine_rows])

        # ---- 8b. THE ECONOMICS, RE-VERIFIED AFTER THE CANCEL, AGAINST THE
        # BOOK AS IT IS NOW.
        #
        # THE GAP THIS CLOSES. `proposal` is `TCP.authorize`'s verdict, and
        # it was computed BEFORE the cancel. Between then and here there are
        # venue round trips -- the cancel, its verification re-read, and the
        # positions read above -- and the book moves. So the mandate could be
        # issued, and the 1.20 ceiling unlocked, on economics that had
        # already evaporated. Nothing downstream would catch it:
        # `pair_cost_refusal` SKIPS `completion_pair_rescue` for a mandated
        # order precisely because the mandate holder is supposed to have
        # established this.
        #
        # So it is established here, three ways, and all three must hold:
        #
        #   (a) WE NEVER PAY MORE THAN WAS AUTHORIZED. If the executable own
        #       price has moved against us the completion is refused rather
        #       than re-priced upward: chasing a rescue is how a rescue
        #       becomes the loss it was meant to avoid.
        #   (b) THE IMPROVEMENT IS RECOMPUTED at the clamped quantity and
        #       the post-cancel inventory, through the SAME `TS.top_up`
        #       arithmetic the policy used, which charges the taker fee on
        #       both legs. So "positive expected risk reduction AFTER FEES"
        #       is a measurement here and not an inherited claim.
        #   (c) IT MUST STILL CLEAR THE OWNER'S FLOOR. A rescue that no
        #       longer removes the exposure the owner priced is not a rescue.
        #
        # FAIL CLOSED ON AN UNREADABLE OR STALE BOOK. The 1.20 ceiling is
        # worth $0.20 a share of deliberate loss; it is not a licence to
        # trade on a book we cannot see.
        _floor = _money_or_none(proposal.get("min_worst_improvement_usd"))
        _snap = None
        try:
            _getter = getattr(getattr(self, "ts_book", None), "snapshot", None)
            if callable(_getter):
                _snap = _getter(market) or None
        except BaseException:                                  # noqa: BLE001
            _snap = None
        if _snap is None:
            return no("COMPLETION_BOOK_UNREADABLE_AT_EXECUTION")
        if bool(_snap.get("gap_suspected")):
            return no("COMPLETION_BOOK_GAP_AT_EXECUTION")
        _bid = _money_or_none(_snap.get("bid"))
        _ask = _money_or_none(_snap.get("ask"))
        if _bid is None or _ask is None or _bid <= 0 or _ask >= D(1) \
                or _bid >= _ask:
            return no("COMPLETION_BOOK_NOT_TWO_SIDED_AT_EXECUTION",
                      bid=str(_bid), ask=str(_ask))
        # OWN-OUTCOME EXECUTABLE PRICE. Buying DOWN consumes the YES bid, so
        # our own cost is 1 - bid; buying UP lifts the ask.
        _now_px = (D(1) - _bid) if buy_side == DOWN else _ask
        if _now_px > px:
            return no("COMPLETION_PRICE_MOVED_AGAINST_US",
                      authorized_px=str(px), executable_px=str(_now_px),
                      note=("refused rather than re-priced upward: chasing "
                            "a rescue is how a rescue becomes the loss it "
                            "was meant to avoid"))
        # AN UNATTESTED BASIS IS NOT A ZERO BASIS, and this check is what
        # makes step 8b self-sufficient rather than dependent on the caller.
        # `TS.top_up` reads a missing price as `D(0)` -- a FREE leg -- and a
        # free leg makes EVERY completion look like an improvement, however
        # dear. `ts_completion_inputs` already refuses UNKNOWN_COST_BASIS
        # upstream and `pair_cost_refusal` has its own BASIS_UNATTESTED gate,
        # but this is the check the mandate's authority now rests on, so it
        # asks for itself. `cost_attested` is the right predicate rather than
        # "basis > 0": a fill at a wire price of 0.00 is a real thing and is
        # attested, while a quantity reconcile adopted from the venue's net
        # has no attestation at all.
        for _s in (held_side, buy_side):
            try:
                if self.store.position(market, _s) and not \
                        self.store.cost_attested(market, _s):
                    return no("COMPLETION_BASIS_UNATTESTED_AT_EXECUTION",
                              side=_s,
                              note=("this leg's quantity came from the "
                                    "venue's net, not from a fill of ours, "
                                    "so its cost basis is unknown. It is "
                                    "NOT priced at zero, and a zero basis "
                                    "would make any completion look like an "
                                    "improvement."))
            except BaseException:                              # noqa: BLE001
                return no("COMPLETION_BASIS_ATTESTATION_UNREADABLE",
                          side=_s)
        _tu = TS.top_up(up_qty=self.store.position(market, UP),
                        dn_qty=self.store.position(market, DOWN),
                        up_price=(self.store.leg_cost(market) or {}).get(UP),
                        dn_price=(self.store.leg_cost(market) or {}).get(DOWN),
                        extra_side=buy_side, extra_qty=qty, extra_price=px)
        _wb = _money_or_none((_tu or {}).get("worst_before"))
        _wa = _money_or_none((_tu or {}).get("worst_after"))
        if _wb is None or _wa is None:
            return no("COMPLETION_IMPROVEMENT_UNREADABLE_AT_EXECUTION",
                      top_up=dict(_tu or {}))
        _impr = _wa - _wb
        if _impr <= 0:
            return no("COMPLETION_NO_RISK_REDUCTION_AT_EXECUTION",
                      worst_before=str(_wb), worst_after=str(_wa),
                      improvement_usd=str(_impr),
                      note=("recomputed after the cancel at the clamped "
                            "quantity, with the taker fee charged on both "
                            "legs. The 1.20 ceiling is never permission to "
                            "buy a pair that reduces no exposure."))
        if _floor is not None and _impr < _floor:
            return no("COMPLETION_IMPROVEMENT_BELOW_FLOOR_AT_EXECUTION",
                      improvement_usd=str(_impr),
                      min_worst_improvement_usd=str(_floor),
                      worst_before=str(_wb), worst_after=str(_wa))

        # ---- 9. THE SHARED LOSS BUDGET, CHECKED AND ONLY THEN CHARGED.
        #
        # THE GAP THIS CLOSES, FOUND BY REVIEWING THIS METHOD AGAINST THE ONE
        # IT MIRRORS. `InventoryStore.defensive_charge` ONLY ACCUMULATES --
        # it adds to `defensive_spent_usd` and saves, and it refuses nothing.
        # The defensive path is bounded because `DEF.decide` tests
        # `spent + exit_downside > loss_budget_usd` and returns
        # DEFENSIVE_LOSS_BUDGET before `defensive_execute` ever charges. This
        # method does not call `DEF.decide`, so charging without testing left
        # the campaign-wide $2.00 budget unenforced on the completion route --
        # and worse, a completion could quietly exhaust the budget the
        # defensive unwind depends on, since the two deliberately share it.
        #
        # The per-cross ($1.50) and per-window ($3.00) outlay caps in
        # `l5_taker_completion_policy` bound a single attempt either way, so
        # this was never unbounded; it was unbounded ACROSS the campaign,
        # which is exactly the dimension this budget exists to bound.
        #
        # FAIL CLOSED ON AN UNREADABLE BUDGET, which `defensive_spent` already
        # delivers by returning the whole budget when the read raises -- a
        # budget we cannot read is exhausted.
        worst_outlay = qty * px + TS.worst_fee(qty, px)
        try:
            spent = self.store.defensive_spent()
        except BaseException:                                  # noqa: BLE001
            spent = DEF.DEFENSIVE_LOSS_BUDGET_USD
        _remaining = DEF.DEFENSIVE_LOSS_BUDGET_USD - spent
        if spent + worst_outlay > DEF.DEFENSIVE_LOSS_BUDGET_USD:
            # ---- SIZE TO WHAT IS LEFT, THEN RE-EARN THE FLOOR AT THAT SIZE.
            #
            # A flat refusal here respected the budget but threw away the
            # part of the rescue the budget could still afford. So the
            # quantity is cut to the largest increment whose WORST outlay
            # fits what remains -- and then the improvement floor is tested
            # AGAIN at that smaller size, because a smaller cross removes
            # less exposure and may no longer be worth its own fee. Only a
            # completion that still clears the owner's floor at the affordable
            # size proceeds; anything else refuses, as before.
            _fit = D(0)
            if _remaining > 0 and px > 0:
                _fit = _floor_to_inc(_remaining / px, inc)
                # the fee is charged on top, so step down until it fits
                for _ in range(64):
                    if _fit <= 0:
                        break
                    if _fit * px + TS.worst_fee(_fit, px) <= _remaining:
                        break
                    _fit -= inc
            _fit = min(_fit, qty)
            if _fit <= 0:
                return no("COMPLETION_LOSS_BUDGET_EXHAUSTED",
                          defensive_spent_usd=str(spent),
                          would_charge_usd=str(worst_outlay),
                          defensive_budget_usd=str(
                              DEF.DEFENSIVE_LOSS_BUDGET_USD),
                          budget_remaining_usd=str(_remaining),
                          note=("the same campaign-wide budget the defensive "
                                "unwind spends, tested the same way "
                                "DEF.decide tests it. Charged at the WORST "
                                "case, because an attempt that does not fill "
                                "is still a real offer to spend that money."))
            _tu2 = TS.top_up(
                up_qty=self.store.position(market, UP),
                dn_qty=self.store.position(market, DOWN),
                up_price=(self.store.leg_cost(market) or {}).get(UP),
                dn_price=(self.store.leg_cost(market) or {}).get(DOWN),
                extra_side=buy_side, extra_qty=_fit, extra_price=px)
            _wb2 = _money_or_none((_tu2 or {}).get("worst_before"))
            _wa2 = _money_or_none((_tu2 or {}).get("worst_after"))
            if _wb2 is None or _wa2 is None:
                return no("COMPLETION_BUDGET_RESIZE_UNREADABLE")
            _impr2 = _wa2 - _wb2
            if _impr2 <= 0 or (_floor is not None and _impr2 < _floor):
                return no("COMPLETION_BUDGET_RESIZE_BELOW_FLOOR",
                          budget_remaining_usd=str(_remaining),
                          affordable_qty=str(_fit),
                          improvement_usd=str(_impr2),
                          min_worst_improvement_usd=(
                              None if _floor is None else str(_floor)),
                          note=("the budget could afford only a smaller "
                                "cross, and the smaller cross no longer "
                                "earns its own fee. Refused rather than "
                                "spending what was left on a completion "
                                "that does not pay."))
            qty = _fit
            worst_outlay = qty * px + TS.worst_fee(qty, px)
        # CHARGED BEFORE THE MANDATE AND BEFORE THE WIRE. `defensive_charge`
        # fsyncs, so a process that dies between here and the venue has
        # already paid.
        try:
            total = self.store.defensive_charge(worst_outlay, {
                "market": market, "buy_side": buy_side, "qty": str(qty),
                "price": str(px), "reason": "TAKER_COMPLETION",
                "origin": TCP.MANDATE_ORIGIN})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("COMPLETION_BUDGET_NOT_CHARGEABLE",
                      error=type(exc).__name__)

        # ---- 10. THE CLAIM AND THE MANDATE, in that order. The claim is
        # durable and blocks a retry; the mandate is in-process and permits
        # exactly this order.
        expires = _money_or_none(proposal.get("expires_at"))
        if expires is None:
            try:
                expires = D(str(now_t)) + D(str(
                    DEF.DEFENSIVE_MIN_EXECUTION_S))
            except BaseException:                              # noqa: BLE001
                expires = None
        if expires is None:
            return no("COMPLETION_MANDATE_HAS_NO_EXPIRY")
        try:
            claims[claim_key] = {
                "market": market, "side": buy_side, "qty": str(qty),
                "price": str(px), "window_start": str(window_start),
                "claimed_at": str(now_t), "origin": TCP.MANDATE_ORIGIN,
                "labels": ("written BEFORE the wire and never released by a "
                           "retry. A restart or an uncertain ACK comes back "
                           "to this row and does not try again.")}
            # BOUNDED, because this is on the durable volume and the WHOLE
            # state dict is rewritten on every `save()`. One entry per
            # (market, side) with a 15-minute window is 192 a day and nothing
            # ever removed them -- a slow leak that would eventually make
            # every ledger write bigger, in the one file the loss halt is
            # measured from. Oldest out first, by claim time, the way
            # `_shadow_completion_parked` is bounded in the runner.
            #
            # IT CANNOT EVICT A CLAIM THAT STILL MATTERS. A claim only has to
            # outlive its own window, and CLAIM_RETENTION windows of history
            # is far more than that; the eviction is by `claimed_at`, so the
            # row being written now is never the one dropped.
            if len(claims) > CLAIM_RETENTION:
                def _age(kv):
                    try:
                        return float(kv[1].get("claimed_at") or 0)
                    except (TypeError, ValueError, AttributeError):
                        return 0.0
                for k, _v in sorted(claims.items(), key=_age)[
                        :len(claims) - CLAIM_RETENTION]:
                    if k != claim_key:
                        claims.pop(k, None)
            self.store.save()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return no("COMPLETION_CLAIM_NOT_WRITABLE",
                      error=type(exc).__name__)
        self._defensive_mandate = {
            "market": market, "side": buy_side, "price": str(px),
            "qty": str(qty), "expires_at": float(expires),
            "origin": TCP.MANDATE_ORIGIN,
            "reason": "TAKER_COMPLETION",
            "budget_total_usd": str(total),
            # WHAT THIS MANDATE ACTUALLY WIDENS, for THIS ONE ORDER, stated
            # rather than left to be read off `pair_cost_refusal`. The first
            # version of this label said it was "not a change to the 0.96
            # pair ceiling", which was misleading: the ceiling stands for
            # every other order, and for this one it does not.
            "labels": ("permits ONE crossing order and only this one, and "
                       "for that order it widens exactly three things: the "
                       "maker-only overlay is exempted, the pair ceiling "
                       "becomes the DEFENSIVE 1.20 rather than the 0.96 "
                       "entry ceiling, and `completion_pair_rescue`'s three "
                       "conditions are skipped because the holder enforces "
                       "the two directional ones itself. 1.20 is ABOVE the "
                       "$1.00 certain-loss line on purpose: 0.96 is an "
                       "ENTRY ceiling for spread capture, and a leg that "
                       "would otherwise settle worthless is the one case "
                       "where paying above a dollar is the SMALLER loss. "
                       "What bounds it is not a pair-cost line but the "
                       "owner's improvement floor and the per-cross and "
                       "per-window outlay caps. Every other order in the "
                       "system is unaffected, and this is gated by the "
                       "taker-completion policy's own default-off switches "
                       "rather than by the defensive flag."),
        }
        return {"ok": True, "confirmations": confirmations,
                "authorization": dict(proposal),
                "exposure": {"residual_before": str(resid),
                             "residual_venue_confirmed": str(venue_net),
                             "residual_after": str(
                                 resid - qty if buy_side == DOWN
                                 else resid + qty),
                             "held_side": held_side, "buy_side": buy_side,
                             "basis": ("venue-confirmed fills only. This is "
                                       "the exposure the order REDUCES; it "
                                       "is not matched until the venue "
                                       "confirms a fill.")},
                "mandate": dict(self._defensive_mandate),
                "claim": dict(claims[claim_key]),
                "leg": {"side": buy_side, "action": "BUY", "price": str(px),
                        "qty": str(qty), "mode": FT.TAKER,
                        "post_only": False,
                        "taker_completion": True,
                        "labels": ("a crossing order against displayed "
                                   "depth, sized to the CONSERVATIVE "
                                   "quantity the policy authorized and "
                                   "never above the confirmed unmatched "
                                   "deficit. Displayed size is not a fill "
                                   "and this position is NOT matched until "
                                   "the venue confirms one.")}}

    def defensive_mandate_covers(self, market: str, dec: Mapping[str, Any],
                                 tr: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """The live defensive mandate, but only if it names THIS order.

        TIGHT ON PURPOSE. A mandate that matched loosely would be a standing
        taker permission and a standing 1.20 ceiling for any order that
        happened to follow it. It must agree on the market, the side, the
        price and the quantity, it must still be inside the window interval
        it was issued for, and the flag must still be on. Anything
        unreadable means NOT covered, so the normal maker-only and 0.96
        rules apply -- which is the direction that refuses.
        """
        m = self._defensive_mandate
        if not isinstance(m, Mapping):
            return None
        # ---- WHICH POLICY AUTHORIZED IT DECIDES WHICH FLAG GATES IT.
        #
        # One mandate slot, two issuers, and the origin says which. A
        # defensive unwind is gated by `DEF.enabled()` exactly as before; a
        # bounded taker COMPLETION is gated by its own pair of default-off
        # switches -- the policy flag and the owner's separate authorization
        # for live taker execution -- which is the authorization issue #195
        # requires and which `DEF.enabled()` does not express.
        #
        # SHARING THE SLOT IS THE POINT. "One crossing order per market per
        # window" stays ONE invariant with one latch, rather than two
        # invariants that could each be satisfied while two crossing orders
        # went out. An unknown origin is treated as defensive, which is the
        # stricter reading of a mandate we did not recognise.
        _origin = str(m.get("origin") or "DEFENSIVE").upper()
        if _origin == TCP.MANDATE_ORIGIN:
            if not TCP.taker_completion_enabled():
                return None
        elif not DEF.enabled():
            return None
        try:
            if str(m.get("market")) != str(market):
                return None
            if str(m.get("side")) != str(dec.get("side")):
                return None
            if D(str(m.get("price"))) != D(str(tr.get("price"))):
                return None
            if D(str(m.get("qty"))) != D(str(tr.get("qty"))):
                return None
            if float(self._now().timestamp()) >= float(m["expires_at"]):
                return None
        except BaseException:                                  # noqa: BLE001
            return None
        return dict(m)

    def completion_pair_rescue(self, market: str, *, side: str, other: str,
                               px: D, qty: D, held: Mapping[str, D],
                               after: Mapping[str, D],
                               lc: Mapping[str, Any], eff: D
                               ) -> Dict[str, Any]:
        """May this order clear the pair gate as a COMPLETION rather than an
        opening, and if not, why not.

        EVERY CONDITION IS NECESSARY and each one closes a specific way a
        rescue could do harm. `reduces` keeps it from being an opening wearing
        a completion's name. `no_reversal` keeps it from buying through parity
        into fresh exposure on the other side. `eff <= 1.00` keeps the matched
        book off a certain loss, because a matched pair pays exactly $1.00 a
        share. And the price must clear `completion_ceiling`, which is the
        minimum of "completing beats carrying the residual to settlement" and
        "the completed worst case stays inside the approved residual
        deficit", so the whole thing is bounded by an owner number rather
        than by this method's opinion.

        FAILS CLOSED EVERYWHERE. An unreadable budget, an unreadable basis or
        an unreadable ceiling all refuse, which leaves the 0.96 opening
        ceiling in force -- the stricter rule.
        """
        out: Dict[str, Any] = {"ok": False, "reason": None,
                               "effective_pair_cost": str(eff)}
        try:
            h_other, h_side = D(str(held[other])), D(str(held[side]))
            a_side = D(str(after[side]))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["reason"] = "INVENTORY_UNREADABLE:%s" % type(exc).__name__
            return out
        out["reduces"] = bool(h_other > h_side)
        if not out["reduces"]:
            out["reason"] = "NOT_A_COMPLETION_NOTHING_UNMATCHED"
            return out
        out["no_reversal"] = bool(a_side <= h_other)
        if not out["no_reversal"]:
            out["reason"] = "WOULD_CROSS_ZERO_INTO_NEW_EXPOSURE"
            return out
        # A MATCHED PAIR PAYS EXACTLY $1.00 A SHARE. Above that the matched
        # book is a certain loss, which no residual argument can justify.
        #
        # THE LIMIT IS NOW NAMED AND CONFIGURABLE, which is the owner's
        # 2026-11-03 completion-ceiling direction. It defaults to 1.00 --
        # exactly the bound this branch has always enforced -- so an unset
        # environment is byte-identical to the deployed behaviour, and the
        # knob can only tighten toward the 0.96 opening cap. 0.96 was never
        # the completion bound: a completion that fails the opening cap
        # arrives HERE, and here the test was redemption. Setting
        # L5_COMPLETION_PAIR_CEILING=0.99 makes the bound 0.99.
        #
        # UNREADABLE FAILS CLOSED to the default rather than to anything
        # looser, and the accessor clamps above 1.00, so a fat-fingered
        # value cannot buy a pair dearer than it redeems.
        try:
            _cap = TS.completion_pair_ceiling()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["reason"] = ("COMPLETION_CEILING_UNREADABLE:%s"
                             % type(exc).__name__)
            return out
        out["completion_pair_ceiling"] = str(_cap)
        if eff > _cap:
            out["reason"] = "EFFECTIVE_PAIR_ABOVE_REDEMPTION"
            out["redemption"] = "1.00"
            return out
        try:
            _vg = getattr(self, "validation_gates", None)
            budget = getattr(_vg, "max_worst_case_deficit_usd", None)
            if budget is None:
                out["reason"] = "RESIDUAL_BUDGET_UNKNOWN"
                return out
            ceil = TS.completion_ceiling(
                held_side=other, held_qty=h_other,
                held_cost_usd=lc.get(other), deficient_qty=h_side,
                deficient_cost_usd=(lc.get(side) or D(0)),
                complete_qty=qty, residual_budget_usd=budget)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["reason"] = "CEILING_UNREADABLE:%s" % type(exc).__name__
            return out
        out["completion_ceiling"] = (None if ceil is None else str(ceil))
        out["residual_budget_usd"] = str(budget)
        if ceil is None:
            out["reason"] = "NO_COMPLETION_CEILING"
            return out
        if px > ceil:
            out["reason"] = "ABOVE_COMPLETION_CEILING"
            return out
        out["ok"] = True
        out["labels"] = ("judged as a COMPLETION of confirmed residual "
                         "inventory, not as an opening. It reduces an "
                         "unmatched position without crossing zero, the "
                         "matched book stays at or under the $1.00 a share a "
                         "pair redeems, and the price clears the completion "
                         "ceiling the owner's residual budget bounds.")
        return out

    def worst_pair_combination_refusal(self, market: str,
                                       dec: Mapping[str, Any],
                                       tr: Mapping[str, Any]
                                       ) -> Optional[Dict[str, Any]]:
        """None unless some reachable fill combination pays less than it costs.

        WHY THIS EXISTS SEPARATELY FROM `pair_cost_refusal`. That gate is
        judged on VENUE-CONFIRMED FILLS and engages only on the leg that
        CREATES matched quantity -- correct for sequential completion, and
        exactly why SIMULTANEOUS quoting escapes it. With both legs merely
        submitted the book is flat, each sees matched_before == matched_after
        == 0, each is a legitimate first leg, and nothing judges the pair
        they would form. tests/test_l5_simultaneous_pair_defect.py shows UP
        2.46 at own 0.94 and DOWN 2.46 at own 0.90 BOTH ADMITTED -- a pair
        costing 1.84 that pays 1.00, a certain -$2.0664. That is the shape of
        the recorded 1215z incident.

        THIS IS ADDITIVE AND ONLY EVER REFUSES. `pair_cost_refusal` keeps its
        0.96 sequential-completion ceiling untouched; nothing here admits an
        order that gate refused.

        THE THRESHOLD IS REDEMPTION, NOT 0.96. A matched pair redeems exactly
        $1.00, so a pair costing more is a CERTAIN loss whatever happens --
        that needs no ceiling policy and no probability. Using 0.96 here
        would delete the profitable 0.97-0.99 simultaneous pairs the live
        campaign actually completed (0.9900 and 0.9700, +$0.030625 and
        +$0.183732), which is why the bound is 1.00.

        THE WORST REACHABLE COMBINATION, NOT THE ALL-FILL AVERAGE. Averaging
        hides the case that matters: UP orders at 0.40 and 0.95 with a DOWN
        at 0.30 average to 0.975 and look fine, but if only the 0.95 and the
        0.30 fill the pair costs 1.25. Any matched pair can be formed from
        the DEAREST unit still reachable on each side, so the bound is
        max(own price reachable on UP) + max(own price reachable on DOWN).
        That is scenario-independent: it covers every subset and every
        partial fill without enumerating them.

        RESTING ORDERS ARE POTENTIAL EXPOSURE, NEVER CONFIRMED PROTECTION.
        They can only RAISE a side's dearest reachable price here; they are
        never credited with matching anything.

        FAILS CLOSED. Unreadable holdings, unreadable live orders or an
        unreadable proposed order all refuse -- a combination we cannot
        price is not one we may assume is safe.
        """
        try:
            side = str(dec.get("side") or "")
            if side not in (UP, DOWN):
                return {"why": "SIDE_UNREADABLE", "side": side}
            px = D(str(tr.get("price")))
            qty = D(str(tr.get("qty")))
            if px <= 0 or qty <= 0:
                return {"why": "PRICE_OR_QTY_UNREADABLE",
                        "price": str(tr.get("price")),
                        "qty": str(tr.get("qty"))}
            lc = self.store.leg_cost(market)
            held = {s: self.store.position(market, s) for s in (UP, DOWN)}
            live = self.store.live_orders()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"why": "STATE_UNREADABLE:%s" % type(exc).__name__}

        # DEAREST REACHABLE OWN PRICE PER SIDE.
        dearest: Dict[str, Optional[D]] = {UP: None, DOWN: None}

        def _bump(s: str, v: Optional[D]) -> None:
            if v is None or v <= 0:
                return
            cur = dearest.get(s)
            if cur is None or v > cur:
                dearest[s] = v

        # held units: only the average is retained, which is the best
        # authoritative figure available for stock already bought.
        for s in (UP, DOWN):
            try:
                q = D(str(held.get(s) or 0))
                c = lc.get(s)
                if q > 0 and c is not None:
                    _bump(s, D(str(c)) / q)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                return {"why": "HELD_BASIS_UNREADABLE:%s"
                               % type(exc).__name__, "side": s}

        # every still-live order OF OURS in this market, on either side.
        try:
            rows = (live or {}).values() if isinstance(live, Mapping) \
                else (live or [])
            for r in rows:
                if not isinstance(r, Mapping):
                    return {"why": "LIVE_ORDER_ROW_UNREADABLE"}
                if str(r.get("market") or "") != str(market):
                    continue
                rs = str(r.get("side") or "")
                if rs not in (UP, DOWN):
                    return {"why": "LIVE_ORDER_SIDE_UNREADABLE"}
                _bump(rs, D(str(r.get("price"))))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"why": "LIVE_ORDERS_UNREADABLE:%s" % type(exc).__name__}

        # and the order being proposed
        _bump(side, px)

        up_d, dn_d = dearest[UP], dearest[DOWN]
        if up_d is None or dn_d is None:
            # ONE-SIDED. No pair is reachable at all, so there is no pair
            # cost to bound. Intentional imbalance is governed by the
            # residual, exposure, cash and worst-case limits, not here.
            return None
        worst_pair = up_d + dn_d
        if worst_pair <= REDEMPTION_PAIR:
            return None
        return {"why": "WORST_REACHABLE_PAIR_ABOVE_REDEMPTION",
                "worst_pair_cost": str(worst_pair),
                "redemption": str(REDEMPTION_PAIR),
                "dearest_up_own": str(up_d), "dearest_down_own": str(dn_d),
                "certain_loss_per_pair": str(worst_pair - REDEMPTION_PAIR),
                "labels": ("a matched pair redeems exactly $1.00, so some "
                           "reachable fill combination here pays less than "
                           "it costs. Resting orders are counted as "
                           "potential exposure and never as protection.")}

    def favorite_only_refusal(self, dec: Mapping[str, Any]
                              ) -> Optional[Dict[str, Any]]:
        """None unless the favorite-only test is on and this order is not
        the favorite the steer wants RIGHT NOW.

        FAILS CLOSED. In the test, an order with no steer behind it -- every
        completion, requote and defensive route, which run with none -- is
        refused, and so is one whose steer cannot be read or does not name
        this side as the FAVORITE.
        """
        # THE VARIABLE, NOT THE CONTROLLER MODULE. This file does not import
        # the portfolio controller, by design; the profile's name is one
        # exact token and a test pins that both files read the same one.
        if os.getenv(FAVORITE_ONLY_PROFILE_ENV, "") != FAVORITE_ONLY_PROFILE:
            return None
        side = str(dec.get("side") or "")
        st = self.portfolio_steer
        if not isinstance(st, Mapping):
            return {"why": "FAVORITE_ONLY_NO_STEER", "side": side}
        try:
            v = (st.get("sides") or {}).get(side) or {}
            ok = v.get("wanted") is True and v.get("role") == "FAVORITE"
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"why": "FAVORITE_ONLY_STEER_UNREADABLE", "side": side}
        if not ok:
            return {"why": "FAVORITE_ONLY_NOT_THE_FAVORITE", "side": side,
                    "steer": {k: (x or {}).get("reason") or (x or {}).get(
                        "role") for k, x in (st.get("sides") or {}).items()}}
        return None

    def held_side_add_refusal(self, market: str, dec: Mapping[str, Any]
                              ) -> Optional[Dict[str, Any]]:
        """None unless completions rest at their target (inventory recovery,
        or L5_TS_COMPLETION_RESTS_AT_CEILING alone) and this order would BUY
        MORE of the side already held unmatched, with no steer asking for it.

        WHAT IT CLOSES, found by replaying recorded venue books through the
        runner. With recovery on, a completion rests for as long as inventory
        is unmatched. The controller's opening path read that resting
        completion as the first leg of a new pair and granted the "complement"
        its slot -- on the side ALREADY HELD. 2026-11-03 00:30Z book: holding
        0.62 DOWN bought at 0.50 with UP resting at 0.48 and the market at
        0.79/0.80, it posted DOWN at 0.18, which filled. The two orders were
        never a pair (UP was 31 ticks from the touch); the DOWN order was a
        directional add to a losing position that nothing had decided to make.

        Without recovery the same tick is refused upstream, because the
        opening pair is priced at the touch and fails the opening ceiling;
        resting the completion at its target is what let it through.

        A deliberate add still has a route: the portfolio controller's steer
        names the side it wants, and an order it wants is not refused here.
        FAILS CLOSED: unreadable inventory refuses.
        """
        # keyed on RESTING, which is what creates the opening: the master
        # switch turns it on, and so does its own switch alone
        if not TS.completion_rests_at_ceiling():
            return None
        side = str(dec.get("side") or "")
        try:
            realised = D(str(self.store.residual(str(market))))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"why": "HELD_SIDE_ADD_INVENTORY_UNREADABLE", "side": side}
        if not realised.is_finite():
            return {"why": "HELD_SIDE_ADD_INVENTORY_UNREADABLE", "side": side}
        if realised == 0:
            return None
        held = "UP" if realised > 0 else "DOWN"
        if side != held:
            return None
        st = self.portfolio_steer
        try:
            if isinstance(st, Mapping) and (
                    (st.get("sides") or {}).get(side) or {}
                    ).get("wanted") is True:
                return None
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        return {"why": "HELD_SIDE_ADD_WHILE_UNMATCHED", "side": side,
                "unmatched_contracts": str(abs(realised))}

    def pair_cost_refusal(self, market: str, dec: Mapping[str, Any],
                          tr: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """None if this order keeps the market's pair cost under the ceiling.

        THE BASIS IS CONFIRMED FILLS. `leg_cost` is written from
        venue-confirmed fills, so a resting or submitted order contributes
        nothing here -- a submission is an intention, not a hedge.

        The constraint only exists once a pair would EXIST: if this order
        does not increase `min(up, dn)`, there is no pair cost to bound and
        this returns None. That keeps a legitimate first leg unconstrained
        while making the leg that completes a pair justify it.

        Unreadable state REFUSES. A pair cost we cannot compute is not a
        pair cost we may assume is fine.
        """
        try:
            side = str(dec.get("side") or "")
            if side not in (UP, DOWN):
                return {"why": "SIDE_UNREADABLE", "side": side}
            px = D(str(tr.get("price")))
            qty = D(str(tr.get("qty")))
            if px <= 0 or qty <= 0:
                return {"why": "PRICE_OR_QTY_UNREADABLE",
                        "price": str(px), "qty": str(qty)}
            lc = self.store.leg_cost(market)
            held = {s: self.store.position(market, s) for s in (UP, DOWN)}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return {"why": "STATE_UNREADABLE:%s" % type(exc).__name__}
        other = DOWN if side == UP else UP
        after = dict(held)
        after[side] = held[side] + qty
        matched_before = min(held[UP], held[DOWN])
        matched_after = min(after[UP], after[DOWN])
        if matched_after <= matched_before:
            return None          # creates no new matched quantity
        # ============ A HEDGE MAY NOT BE PRICED FROM A PLACEHOLDER
        #
        # Everything below divides `cost_after` by `matched_after`, so every
        # ceiling test here is only as good as the basis. `leg_cost_basis_
        # unknown` was read in five REPORTING paths and consulted by no gate,
        # which is how 2300z could offer a 0.605 blended basis -- the
        # venue-confirmed 1.68 at own 0.2100 averaged with a $1/share
        # placeholder -- to a decision that then refused every completion in
        # the window. On the real lot one of them priced at 0.9500.
        #
        # THIS RUNS AFTER THE EARLY RETURN ABOVE, so it binds ONLY orders
        # that create new matched quantity -- completions and hedges. A first
        # leg creates none and never reaches here, so openings are untouched
        # and an unattested book cannot block risk-reducing cancellation
        # either, which does not come through this gate.
        #
        # FAIL CLOSED, and say which leg and why. An unreadable attestation
        # refuses too: a basis we cannot vouch for is not a basis we may
        # price a pair from.
        #
        # AND IT MUST NOT BLOCK A MANDATED DEFENSIVE UNWIND. Found by
        # auditing my own change rather than by a test: the first version ran
        # this gate BEFORE `_mand` below, so an owner-mandated unwind on an
        # unattested book was refused -- failing closed in the
        # RISK-INCREASING direction, by trapping us in the exact position the
        # mandate exists to escape. An unwind reduces directional exposure
        # whatever we paid, so the sunk basis cannot be a reason to forbid
        # it. A PROFIT-SEEKING completion priced off fiction is a different
        # thing and stays refused.
        #
        # The mandated case is allowed and RECORDED, so the resulting P&L is
        # labelled as resting on a basis no fill ever stated rather than
        # passing as measured.
        _mand = self.defensive_mandate_covers(market, dec, tr)
        try:
            _att = {_s: self.store.basis_attestation(market, _s)
                    for _s in (UP, DOWN)}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if not _mand:
                return {"why": "BASIS_ATTESTATION_UNREADABLE:%s"
                               % type(exc).__name__}
            _att = {}
        _bad = [a for a in _att.values() if not a.get("ok")]
        # THE EXCEPTION NEEDS BOTH HALVES, and the mandate alone is not the
        # second one. `defensive_mandate_covers` proves AUTHORIZATION -- the
        # flag is on, the mandate names this market, side, price and quantity
        # exactly, and it has not expired -- so an expired or oversized
        # mandate already yields None there. It proves nothing about
        # DIRECTION. `reduces` and `no_reversal` are the same two conditions
        # `completion_pair_rescue` enforces below, and they run on the
        # non-mandated branch only, so without them here a mandate would be a
        # standing permission to trade on a basis nothing attested.
        _reduces = False
        _no_reversal = False
        if _bad:
            try:
                _reduces = bool(D(str(held[other])) > D(str(held[side])))
                _no_reversal = bool(D(str(after[side]))
                                    <= D(str(held[other])))
            except BaseException:                              # noqa: BLE001
                _reduces = _no_reversal = False     # unreadable -> refuse
        _exempt = bool(_bad and _mand and _reduces and _no_reversal)
        if _exempt:
            # allowed, because it is authorized AND demonstrably reducing --
            # but never silently
            try:
                self.store.state.setdefault(
                    "defensive_on_unattested_basis", []).append({
                        "market": market, "side": side, "price": str(px),
                        "qty": str(qty),
                        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "attestation": _att,
                        "mandate": {"expires_at": (None if not _mand else
                                                   _mand.get("expires_at"))},
                        "reduces": _reduces, "no_reversal": _no_reversal,
                        "labels": ("a DEFENSIVE unwind was permitted against "
                                   "inventory whose cost basis no fill "
                                   "stated. The exposure reduction is real; "
                                   "the P&L attributed to it is NOT measured "
                                   "and must not be reported as realized "
                                   "against a known basis.")})
                self.store.save()
            except BaseException:                              # noqa: BLE001
                pass
        elif _bad:
            return {"why": "BASIS_UNATTESTED",
                    "legs": _bad,
                    "attestation": _att,
                    "mandate_present": bool(_mand),
                    "reduces": _reduces,
                    "no_reversal": _no_reversal,
                    "exempt": False,
                    "this_order": {"side": side, "price": str(px),
                                   "qty": str(qty)},
                    "labels": ("a completion is priced by dividing confirmed "
                               "cost by matched quantity, so a leg whose "
                               "basis no fill ever stated cannot be paired "
                               "here. The quantity is real; the PRICE is "
                               "not, and a hedge sized off an invented price "
                               "is not a hedge. Reconcile the basis against "
                               "the venue first. This refusal binds "
                               "PROFIT-SEEKING completions only: a mandated "
                               "defensive unwind is permitted and recorded "
                               "in `defensive_on_unattested_basis`, and "
                               "cancellation never comes through this gate "
                               "at all.")}
        cost_after = (lc.get(UP) or D(0)) + (lc.get(DOWN) or D(0)) + px * qty
        eff = cost_after / matched_after
        # A PARTIAL COMPLETION, PER CONTRACT. `cost_after / matched_after`
        # charges the pairs this order creates with the cost of the part of
        # the held leg that stays unmatched, so one clip of a several-clip
        # leg can never clear any ceiling. With the owner's switch, and only
        # for an order that reduces an unmatched leg without reaching past
        # it, each side is taken at its own average: what the matched
        # contracts cost, which is what they redeem $1.00 against. A full
        # completion is the same number either way.
        try:
            if (TS.partial_completion_per_contract()
                    and held[other] > held[side]
                    and after[side] < held[other]
                    and (lc.get(other) is not None)):
                eff = (((lc.get(side) or D(0)) + px * qty) / after[side]
                       + D(str(lc.get(other))) / held[other])
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # unreadable: keep the stricter whole-cost figure
        # THE DEFENSIVE CEILING IS SEPARATE AND LOOSER, AND ONLY HERE.
        # Normal completion stays capped at 0.96: a pair above $1.00 buys a
        # certain loss and is never an edge. Unwinding a leg that would
        # otherwise settle worthless is the one case where paying above
        # $1.00 can still be the smaller loss, so it gets its own ceiling
        # (1.20 == at most $0.20 a share of realized loss) and only for the
        # exact order a live mandate names.
        # `_mand` was resolved above, before the basis gate, because that
        # gate must not refuse a mandated unwind.
        cap = (DEF.DEFENSIVE_PAIR_CEILING if _mand else TS.max_pair_cost())
        if eff <= cap:
            return None
        # ============ OPENING EXPOSURE VERSUS COMPLETING WHAT WE HOLD
        #
        # This gate is the one that actually decides, and it sits at the
        # SUBMISSION boundary -- after `complement_order` has priced the leg.
        # So widening the ceiling inside the complement achieved nothing on
        # the books that matter: 1245z's completion at own 0.65 against a
        # 0.7264 basis makes `eff` 0.9699, and 0.96 refused it HERE.
        #
        # The two cases are different questions and the codebase already
        # says so for the defensive mandate above. An order that OPENS
        # exposure answers to 0.96: a pair above it is a worse bargain than
        # the strategy accepts. An order that COMPLETES confirmed residual
        # inventory is not buying a bargain at all -- it is choosing between
        # a dear pair and a stranded leg -- so it answers to the completion
        # economics and the owner's approved residual budget.
        #
        # THREE CONDITIONS, ALL REQUIRED, so a rescue can neither increase
        # directional exposure nor create a guaranteed loss:
        #
        #   it must REDUCE an unmatched position   held[other] > held[side]
        #   it must not CROSS ZERO                 after[side] <= held[other]
        #   the matched book must not settle at a
        #   certain loss                           eff <= 1.00
        #
        # and the price must clear `completion_ceiling`, which is itself the
        # minimum of "beats carrying the residual to settlement" and "stays
        # inside the approved residual deficit". A matched pair pays exactly
        # $1.00 a share, so `eff <= 1.00` is the line between a dear rescue
        # and a certain loss, and it is never crossed.
        if not _mand:
            _resc = self.completion_pair_rescue(
                market, side=side, other=other, px=px, qty=qty,
                held=held, after=after, lc=lc, eff=eff)
            if _resc.get("ok"):
                return None
        else:
            _resc = None
        return {"why": "PAIR_COST_ABOVE_CEILING",
                "completion_rescue": _resc,
                "effective_pair_cost": str(eff.quantize(D("0.0001"))),
                "ceiling": str(cap),
                "ceiling_is_defensive": bool(_mand),
                "confirmed_cost_usd": str(cost_after),
                "matched_after": str(matched_after),
                "held_confirmed": {k: str(v) for k, v in held.items()},
                "this_order": {"side": side, "price": str(px),
                               "qty": str(qty)},
                "settlement_pays_usd": str(matched_after),
                "note": ("a matched pair pays exactly $1.00 a share, so a "
                         "pair cost above the ceiling buys a certain loss")}

    def probe_precheck(self, market: str, plan: Mapping[str, Any],
                       placed_sides: Optional[Sequence[str]] = None
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
                 "market": str(market),
                 "intent": ("ORDER_INTENT_BUY_SHORT" if l["side"] == DOWN
                            else "ORDER_INTENT_BUY_LONG")}
                for l in legs]
        inv = self.inventory_for_gates(market)
        unreal = self.unrealized_loss_for_gates()
        # EVERY OBLIGATION COUNTED EXACTLY ONCE.
        #
        # AT PLAN TIME -- `placed_sides` empty, which is the default and every
        # pre-existing caller -- neither leg exists yet, so each is gated with
        # the other counted as if it were already resting. That is what makes
        # this a PAIR gate rather than two independent orders, and it is
        # byte-for-byte the original behaviour.
        #
        # RE-CHECKED LATER, a leg that has ALREADY BEEN PLACED must not be
        # charged again. It is already counted: resting, through
        # `outstanding_for_gates()`; filled, through `cash_legs_for_gates()`
        # and the inventory. Charging it a second time as a planned leg
        # produced phantom refusals -- measured on a stored plan of
        # UP 0.94@0.58 + DOWN 0.94@0.37 whose true pair commitment is $1.4852,
        # a re-check reported CASH_COMMITMENT:2.4252, and at an $8 ceiling
        # with both legs resting, WORST_CASE_DEFICIT:3.66>3.
        #
        # THE DIRECTION OF THAT ERROR IS WHAT MATTERS: a phantom charge
        # REFUSES THE COMPLEMENT OF A LEG ALREADY WORKING, which strands it --
        # the exact failure this program has been repairing. An earlier
        # attempt here matched placed legs by their obligation, which caught
        # only RESTING ones and so still double-charged a FILLED leg; it broke
        # sixteen end-to-end tests. The caller states what it placed instead,
        # because the claim state is the authoritative record of that.
        _placed = {str(x).strip().upper() for x in (placed_sides or ())}
        _outstanding = self.outstanding_for_gates()

        for i, leg in enumerate(legs):
            # a leg already placed is not a new order and is already counted
            if str(leg["side"]).strip().upper() in _placed:
                continue
            # the other leg counts only while IT is still unplaced
            _sib_side = str(legs[1 - i]["side"]).strip().upper()
            other = [] if _sib_side in _placed else [both[1 - i]]
            reason = vg.refuse(
                order_qty=D(leg["qty"]), order_price=D(leg["price"]),
                order_intent=both[i]["intent"],
                filled_legs=self.cash_legs_for_gates(),
                outstanding=_outstanding + other,
                book_uncertain=self.store.orders_uncertain(),
                unmatched_after=self.store.residual(market),
                cumulative_realized=self.store.realized_today(),
                verified_strategy_net=self.store.verified_l5_net_for_gates(),
                l5_scoped_realized=self.store.l5_realized_since_baseline(),
                l5_scoped_realized_today=self.store.l5_realized_today(),
                # ADDITIVE and INERT until the ceiling is configured.
                # `cumulative_realized` above still carries the LIFETIME
                # account-wide figure and still gates on it; this supplies the
                # post-baseline delta alongside it, never instead of it.
                account_wide_delta_usd=(
                    self.store.account_wide_delta_realized()),
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

        `settled` is False UNLESS the market is in `retired_markets`, and
        that exception corrects a real double count rather than softening
        the conservative reading.

        WHAT WAS WRONG. This used to hardcode `settled: False` for every
        entry, on the stated grounds that "cash is released by a confirmed
        settlement, and this executor's ledger does not mark one here". The
        ledger DOES mark one. `retire_settled_market` is the only writer of
        `retired_markets`; it runs only on a venue-terminal state WITH a
        final settlement price, and it books the settlement P&L into
        `realized_attributed` and pops `committed` in the same step. A market
        in `retired_markets` is a CONFIRMED settlement by construction --
        exactly the evidence this docstring asked for and did not look up.

        HOW THE RESIDUE SURVIVES, which is why popping once is not enough.
        `committed` is re-incremented by any fill booked for a market after
        it was retired. `markets_with_exposure` subtracts `retired_markets`
        ("never swept, never re-settled"), so the settlement sweep cannot see
        that residue and nothing will ever release it -- while this method
        iterated `committed()` UNFILTERED, so the gates charged it forever.
        Two readings of one dict disagreed, and that asymmetry is the defect.

        WHY IT IS A DOUBLE COUNT AND NOT CONSERVATISM. Retirement already
        added the market's realized P&L to the figure the campaign budget
        charges as `max(0, -cumulative_realized)`. Charging the residue as
        unsettled cost on top bills ONE economic event twice: once at what
        the venue actually paid, and again at its full worst case. Measured
        in production on 2026-10-22 that residue was ~$4.17 against an
        $11.10 campaign budget, while the authenticated venue read returned
        `positions: {}`, `openOrders: 0`, `assetNotional: 0` and
        `unsettledFunds: 0` -- no open risk existed behind any of it.

        THIS RELEASES REAL HEADROOM on both the cash gate and the campaign
        budget, so it is deliberately narrow: `retired_markets` only, never a
        quote, never a `matched` flag, never an unsettled leg. A market that
        has CLOSED but not SETTLED keeps holding its cash in full, which is
        the case `retire_settled_market` already refuses to retire.
        """
        retired = self.store.state.get("retired_markets") or {}
        legs: List[Dict[str, Any]] = []
        recycle = self.cash_recycles_completed_pairs()
        for mkt, usd in sorted(self.store.committed().items()):
            legs.append({"market": mkt, "cash_out": usd,
                         "settled": mkt in retired,
                         "closing": False})
            if not recycle or mkt in retired:
                continue
            # A COMPLETED PAIR'S CASH, RETURNED WHEN THE VENUE RETURNS IT.
            # See `matched_pair_cost`. A credit leg, so the cash gate's own
            # policy switch decides whether it counts; never more than the
            # market was charged, and nothing at all when the matched cost
            # cannot be vouched for.
            credit = self.matched_pair_cost(mkt)
            if credit is None or credit <= 0:
                continue
            # ONLY WHAT THE VENUE'S BUYING POWER HAS CONFIRMED. The runner
            # records, per market, the matched quantity for which a fresh
            # balance read showed the cash back (`confirm_recycled_cash`).
            # Quantity matched since then earns nothing until the next read
            # agrees; nothing recorded -- a restart, an uncertain ledger, a
            # reading that disagreed -- earns nothing at all.
            share = self.recycle_confirmed_share(mkt)
            if share <= 0:
                continue
            legs.append({"market": mkt,
                         "cash_out": -min(credit * share, usd),
                         "settled": False, "closing": True,
                         "basis": "VENUE_BUYING_POWER_CONFIRMED_PAIR"})
        return legs

    def recycle_confirmed_share(self, market: str) -> D:
        """The fraction of this market's matched quantity the venue's
        buying power has confirmed returned: 0 to 1, and 0 on any doubt."""
        try:
            rec = (self.__dict__.get("recycle_confirmed") or {}).get(market)
            if not isinstance(rec, Mapping):
                return D(0)
            conf = D(str(rec.get("matched")))
            m = min(D(str(self.store.position(market, UP))),
                    D(str(self.store.position(market, DOWN))))
            if not (conf.is_finite() and m.is_finite()) or conf <= 0 \
                    or m <= 0:
                return D(0)
            return min(conf, m) / m
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return D(0)

    def cash_recycles_completed_pairs(self) -> bool:
        """Is the cash gate's policy RECYCLE_ON_COMPLETE? Off otherwise."""
        try:
            from src import l5_live_validation_gates as _VG
            return (getattr(self.validation_gates, "cash_policy", None)
                    == _VG.RECYCLE_ON_COMPLETE)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return False

    def matched_pair_cost(self, market: str) -> Optional[D]:
        """What the MATCHED part of this market's inventory cost, or None.

        WHY THIS MAY LEAVE THE CASH FIGURE. On this venue DOWN is a sale of
        the same contract UP buys, so equal UP and DOWN quantity is a FLAT
        position and the venue holds no cash against it. Observed on the
        venue's own balance, four times out of four:

          2026-11-11 0030z  340.42775 -> 340.33975 on the UP fill (-0.0880)
                            -> 340.43375 on the DOWN fill, 14 minutes before
                            settlement: all of it back, plus the pair's 0.6c.
          2026-11-11 0045z  340.43375 -> 340.34375 -> 340.44375, the same.
          2026-11-10 2145z  both pairs' cash and profit were in the balance
                            at 21:59:20, before the 22:00 settlement.

        The ledger's `committed` keeps the full cost until settlement, which
        is right for accounting and wrong as a measure of cash deployed.

        ONLY THE MATCHED QUANTITY, at each side's average cost, and only
        when both sides' cost basis is attested. The unmatched remainder
        keeps its full charge. Unreadable, unattested or incoherent is None,
        which releases nothing.
        """
        try:
            up = self.store.position(market, UP)
            dn = self.store.position(market, DOWN)
            if up is None or dn is None:
                return None
            up, dn = D(str(up)), D(str(dn))
            if not (up.is_finite() and dn.is_finite()) or up < 0 or dn < 0:
                return None
            m = min(up, dn)
            if m <= 0:
                return D(0)
            for side in (UP, DOWN):
                if not (self.store.basis_attestation(market, side)
                        or {}).get("ok"):
                    return None
            lc = self.store.leg_cost(market)
            uc, dc = D(str(lc.get("UP"))), D(str(lc.get("DOWN")))
            if not (uc.is_finite() and dc.is_finite()) or uc < 0 or dc < 0:
                return None
            return (uc * m / up) + (dc * m / dn)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None

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
            # `market` lets the cash gate tell a hedge from an order in
            # some other market. A row without one is never read as a hedge.
            out.append({"coid": coid, "qty": rem, "price": px,
                        "market": str(o.get("market") or ""),
                        "intent": ("ORDER_INTENT_BUY_LONG" if is_up
                                   else "ORDER_INTENT_BUY_SHORT")})
        return out

    # ========================= THE LAST RESIDUAL CHECK BEFORE THE WIRE
    #
    # WHY A SECOND CHECK EXISTS. On 1415z (2026-10-31) the residual cap in
    # `handle` passed an UP order that took realized UP inventory from 2.17
    # to 4.25 against an advertised 3-contract ceiling. Replay against the
    # real store proved the predicate correct, the store functions correct
    # and the configuration correct (k=2), so the order was evaluated
    # against state that did not describe the position it went on to create.
    #
    # A bound whose inputs can be wrong is not a bound. This one re-reads
    # them at the last possible instant, from the same authoritative store,
    # and REFUSES rather than assumes. It is deliberately redundant: if the
    # first gate is right, this one never fires.

    def final_residual_guard(self, market: str, side: str, qty: Any,
                             *, coid: Optional[str] = None,
                             ) -> Dict[str, Any]:
        """Refuse an exposure-increasing order that breaches the cap. CLOSED.

        `record_submit` has ALREADY run when this is called, so
        `outstanding_residual` includes this order: realized + outstanding IS
        the prospective-after figure, and prospective-before is recovered by
        removing this order's own signed quantity. Computing it the other way
        round would double-count the order and refuse legitimate ones.

        RISK-REDUCING ORDERS ARE PRESERVED. A leg that leaves |residual| no
        larger than it found it passes at any depth, exactly as the first
        gate allows. A genuinely flat prospective book is exempt, so a first
        leg is never refused by a bound derived from its own size.

        AN UNREADABLE INVENTORY IS A REFUSAL. "I could not read the position"
        must never be spent as "the position is flat" -- that is the one
        reading that loses money, and it is what a missing check amounts to.
        """
        out: Dict[str, Any] = {
            "ok": True, "reason": None, "coid": coid,
            "market": str(market), "side": str(side),
            "basis": "AUTHORITATIVE_RESTATED_IMMEDIATELY_BEFORE_SUBMIT",
            "labels": ("the last check before the wire; redundant by design. "
                       "If the gate in handle() is correct this never fires, "
                       "and a firing is itself a defect report."),
        }
        if self.residual_cap_k is None:
            out["reason"] = "POLICY_OFF"
            return out
        try:
            q = D(str(qty))
            resid = D(str(self.store.residual(market)))
            outstanding = D(str(self.store.outstanding_residual(market)))
            clip = D(str(self.store.max_clip(market)))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["ok"] = False
            out["reason"] = ("RESIDUAL_STATE_UNREADABLE:%s"
                             % type(exc).__name__)
            return out
        # THE SAME DEFINITION THE PRIMARY GATE USES. One function, two call
        # sites, deliberately redundant and unable to disagree.
        #
        # `same_side_includes_this_order=True`: `record_submit` has already
        # run here, so this order is in the same-side sum. Computing it the
        # other way round would double-count it and refuse orders that are
        # fine.
        try:
            same_side = D(str(self.store.outstanding_on_side(market, side)))
            other = D(str(self.store.outstanding_on_side(
                market, DOWN if str(side).upper() == UP else UP)))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["ok"] = False
            out["reason"] = ("OUTSTANDING_BY_SIDE_UNREADABLE:%s"
                             % type(exc).__name__)
            return out
        rc = residual_cap_decision(
            resid=resid, same_side=same_side, other_side=other, qty=q,
            side=side, k=self.residual_cap_k, clip=clip,
            same_side_includes_this_order=True,
            certain=(self.store.orders_uncertain() is None))
        out.update(rc)
        out["outstanding_residual_net_incl_this_order"] = str(outstanding)
        if rc["binds"]:
            out["ok"] = False
            out["reason"] = "FINAL_RESIDUAL_ABOVE_CAP"
        return out

    def handle(self, row: Optional[Mapping[str, Any]],
               authenticated: Mapping[str, Any],
               *, market: str, window: str,
               open_markets: Sequence[str] = (),
               controller: Optional[Mapping[str, Any]] = None
               ) -> Dict[str, Any]:
        """One decision, from the top of the chain to the wire or a refusal.

        TWO WAYS IN, ONE PATH THROUGH. Called with a `row`, this follows an
        observed L5 action, exactly as it always has. Called with
        `controller=` and `row=None`, it follows OUR OWN decision -- and
        then runs the identical remainder: the same halt gate, the same
        two-sided branch, the same validation gates, the same maker-only
        overlay, the same residual cap, the same cash check, the same
        claim, the same reconciliation. The autonomous path is NOT a second
        executor; it is this one, entered without a row.

        WHY A ROW COULD NOT SIMPLY BE SYNTHESISED. Four things here read
        the row: the key, the native decision, the action age and the
        translation. Handing it a fabricated row would make our own order
        indistinguishable from L5's in the tape, the ledger and the
        provenance chain, which is the one confusion this programme has
        guarded against throughout. So each of the four is sourced from the
        controller instead, and `provenance` records which happened.
        """
        # BOUND ON BOTH ROUTES, because the refusal that reads it sits
        # after the branch rejoins -- the L5 route never sets it, and an
        # unbound local there would raise where the old code did not.
        _bad_num: Optional[str] = None
        if controller is not None:
            if row is not None:
                raise ValueError(
                    "handle() takes a row OR a controller intent, never "
                    "both: two decisions cannot share one provenance chain")
            if str(controller.get("provenance")) != CONTROLLER_GENERATED:
                raise ValueError(
                    "a controller intent must declare provenance "
                    "CONTROLLER_GENERATED; got %r"
                    % (controller.get("provenance"),))
            dec = controller.get("decision")
            dec = dict(dec) if isinstance(dec, Mapping) else None
            # ONE NUMERIC CONTRACT FOR BOTH ROUTES, AND IT IS THE L5 ONE.
            # `FT.l5_native` coerces `price` and `l5_size` to Decimal and
            # returns None when either is unreadable. This route took the
            # caller's mapping VERBATIM, so a caller that passed strings
            # reached `dec["l5_size"] * self.lam` -- three sites below --
            # and raised TypeError before any gate, order, refusal or
            # record.
            #
            # WHAT THAT COST, MEASURED. 1815z on 2026-11-04:
            # `maybe_book_completion` seeds its controller re-entry from
            # `TS.complement_order`, whose `leg` carries `str(px)` and
            # `str(q)`. All 14 RISK_REDUCING_COMPLETION decisions in 93
            # seconds -- pair 0.96 to 0.99, locked_pnl_usd 0.01 to 0.04 --
            # raised `TypeError: can't multiply sequence by non-int of type
            # 'decimal.Decimal'` and not one became an order.
            # `order_resting` was false for all 125 residual samples of
            # that window, and `L5_BOOK_COMPLETION_RESULT` is emitted AFTER
            # the placement call, so the tape recorded the decision, the
            # trigger and a bare `L5_LIVE_ERROR` -- and could not say that
            # the two were the same event. The 2026-11-01 note above
            # `_bc_capture` records the same signature: COMPLETE 145 times,
            # 109 inside the 0.96 ceiling, no order.
            #
            # FAIL CLOSED, NOT COERCE-ANYTHING. `X._d` is the same helper
            # `l5_native` uses; an unreadable number becomes a NAMED
            # REFUSAL, never a TypeError and never a silent zero. A
            # non-positive size is refused for the same reason `l5_native`
            # refuses one. Only these two fields are touched: every other
            # key reaches the gates exactly as the caller wrote it.
            # THE REFUSAL IS TAKEN BELOW, AFTER THE MARKER RESET, never
            # here: `test_l5_probe_leg_marker_leak` requires the
            # unconditional reset of `_probe_leg_pending` to precede EVERY
            # refusal exit in this method, because a refusal taken ahead of
            # it leaks the marker into the next call. So this only RECORDS
            # which field was unreadable.
            #
            # That test substring-searches this method's SOURCE TEXT, so the
            # wording here deliberately avoids spelling the refusal
            # statement out -- the same reason `maybe_book_completion`'s
            # note in the runner avoids one verb.
            if dec is not None:
                for _f in ("price", "l5_size"):
                    # ABSENT COUNTS AS UNREADABLE, for the same reason
                    # `l5_native` returns None on a missing price: the L5
                    # route refuses by name, while this route handed the
                    # mapping to `FT.controller_translate`, which does
                    # `decision["price"]` unconditionally and raised
                    # KeyError -- a second crash of the same family as the
                    # TypeError above, one layer further on. `X._d(None)`
                    # is already None, so no extra branch is needed.
                    _v = X._d(dec.get(_f))
                    # NaN AND INFINITY ARE READABLE DECIMALS AND NOT
                    # NUMBERS. `X._d("nan")` returns Decimal("NaN"), which
                    # is not None and for which `<= 0` is False, so both
                    # slipped past a null-and-sign check and raised
                    # InvalidOperation inside `controller_translate`
                    # instead. `opening_leg_max_contracts` already guards
                    # its own input with `is_finite()`; this is the same
                    # standard applied at the boundary.
                    if (_v is None or not _v.is_finite()
                            or (_f == "l5_size" and _v <= 0)):
                        _bad_num = _f
                        break
                    dec[_f] = _v
            # PROVISIONAL, and refined to the real (round, leg) once the
            # two-sided branch has chosen them -- see THE CONTROLLER'S KEY
            # below. It is never the key an order is submitted under.
            key = "%s|OPEN|%s|UNRESOLVED" % (CONTROLLER_KEY_PREFIX,
                                             str(market))
            prov = Provenance(key, dec or {"unreadable": True},
                              market, window,
                              provenance=CONTROLLER_GENERATED)
        else:
            key = str(row.get("action_key"))
            dec = FT.l5_native(row)
            prov = Provenance(key, dec or {"unreadable": True},
                              market, window)

        # THE TWO-SIDED LEG MARKERS ARE PER-CALL SCRATCH, AND THEY LEAKED.
        #
        # `_probe_leg_pending` / `_probe_round_pending` are set by the
        # two-sided path later in THIS method and cleared immediately before
        # the wire, after every gate. Nineteen refusal exits sit between
        # those two points, and none of them reset the markers -- so any
        # two-sided plan that was built and then refused left them set for
        # the life of the process.
        #
        # WHAT THAT COST, MEASURED LIVE IN 1445z. The strategy-ownership
        # gate below reads exactly this marker to decide whether the order in
        # hand belongs to the two-sided engine. The first two-sided plan of
        # the window was refused (LEG_WOULD_CROSS:DOWN), which latched the
        # marker, and from then on every L5-following order in the window was
        # refused by the gate whose entire purpose is to exclude the OTHER
        # engine. 15 of 27 decisions died there, none reached the maker-only
        # overlay (`safety` was null in all of them), and nothing reached the
        # venue. The latch is self-sustaining: only a successful submission
        # clears it, and it is what prevents a successful submission.
        #
        # It is also a risk defect, not merely a throughput one. The
        # second-maker-slot evidence below reads `_probe_round_pending` to
        # decide whether this order is a pair leg, so a stale marker could
        # grant an L5-following order a slot reserved for a two-sided
        # complement. Clearing the markers on entry is therefore
        # risk-REDUCING; it relaxes no limit, moves no price or size, and
        # introduces no crossing.
        #
        # Nothing legitimately depends on them surviving a call: both are
        # set and consumed within one `handle()`.
        self._probe_leg_pending = None
        self._probe_round_pending = None

        # A CONTROLLER DECISION WHOSE NUMBERS CANNOT BE READ, refused here
        # rather than where it was detected: the reset above must precede
        # every refusal exit in this method. See the note beside `_bad_num`.
        if _bad_num is not None:
            return prov.refuse("PMUS_TRANSLATION",
                               "CONTROLLER_DECISION_UNREADABLE:%s" % _bad_num)

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

        if controller is None:
            age = self._age_s(row)
            if age is not None and age > STALE_ACTION_MAX_S:
                return prov.refuse("PMUS_TRANSLATION",
                                   "STALE_ACTION:%0.1fs" % age)

            want = dec["l5_size"] * self.lam
            tr = FT.pmus_translate(dec, row, authenticated, want)
        else:
            # NO ACTION AGE, BECAUSE THERE IS NO ACTION. STALE_ACTION_MAX_S
            # bounds how old an OBSERVED L5 ACTION may be when we act on it;
            # a decision we just made has no such age and giving it a
            # fabricated one would be meaningless. Nothing is weakened by
            # its absence: the gate that matters for an autonomous order is
            # the BOOK's age, and `controller_translate` applies
            # STALE_MAX_S = 2.0s to it -- fifteen times tighter than the
            # action bound skipped here.
            age = None
            want = dec["l5_size"] * self.lam
            # THE SAME BOOK THE TWO-SIDED BRANCH PRICES FROM, through the
            # same fail-closed resolver, so the gate and the order cannot
            # be judging different books. An empty mapping has
            # `stream_age_s` None and `bid`/`ask` None, which the core
            # refuses as T_NO_BOOK.
            cbook = self.ts_authenticated_book(market, authenticated)
            cbook = cbook if isinstance(cbook, Mapping) else {}
            tr = FT.controller_translate(
                dec, bbo=cbook,
                snapshot_age_s=cbook.get("stream_age_s"),
                maker_class=controller.get("maker_class"), want=want)
            prov.note("CONTROLLER_BOOK", {
                "bid": str(cbook.get("bid")), "ask": str(cbook.get("ask")),
                "stream_age_s": str(cbook.get("stream_age_s")),
                "source": str(cbook.get("source")),
                "labels": ("the authenticated book this decision was gated "
                           "against, recorded so the refusal or the order "
                           "can be checked against what we could see"),
            })

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
                # WHOSE PRICE THIS IS, AND THE KEY SAYS SO.
                #
                # `rested_at_l5_price` is read by
                # `Runner.observable_despite_refusal` and by the unresolved
                # accounting, and `l5_shadow_controller` states as an
                # invariant that a controller record is "never labelled
                # `rested_at_l5_price`". An autonomous decision has NO L5
                # price -- there is no L5 action behind it -- so writing
                # that key for one would be a false attribution in the very
                # record that exists to prevent false attribution. The L5
                # path therefore keeps its keys byte-identical, and the
                # controller path carries its own.
                _mt = ({"rested_at_l5_price": str(px),
                        "l5_price_raw": str(raw_px)}
                       if controller is None else
                       {"rested_at_own_price": str(px),
                        "own_price_raw": str(raw_px),
                        "provenance": CONTROLLER_GENERATED,
                        "no_l5_price": ("this decision is ours; no L5 "
                                        "action underlies it, so there is "
                                        "no L5 price to have rested at")})
                prov.stage("MAKER_TREATMENT", dict(_mt, **{
                    "tick_size": (None if tick is None else str(tick)),
                    "price_quantization_delta": str(raw_px - px),
                    "rounding": "FLOOR_NEVER_MORE_AGGRESSIVE",
                    "qty_desired": str(want), "qty": str(executable),
                    "post_only": True,
                    "why": ("the frozen rule called this restable and could "
                            "not prove the fill in replay. Live, the venue "
                            "answers that question instead."),
                }))
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
            # THE GATE IS CHOSEN BY WHAT THIS ACTION IS, and it is applied
            # BELOW, once the round is known -- see THE ENTRY/COMPLETION
            # GATE. A single cutoff for both stopped the engine REDUCING
            # risk at the same instant it stopped taking it.

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
            # WHICH ACCUMULATION ROUND IS THIS. Walked from disk, so a
            # restart resumes the round it was working rather than opening a
            # fresh pair on top of inventory it has forgotten.
            # THE BOOK, RESOLVED ONCE FOR THIS BRANCH AND NOWHERE ELSE.
            #
            # `handle` is called with `{}` as `authenticated` on purpose: the
            # L5-following path rests at L5's own price through
            # MAKER_TREATMENT and needs no book, and giving `pmus_translate` a
            # real one would change the pricing and sizing of a path that has
            # traded real money. `probe_plan` resolves the mirror itself, but
            # it does so in a LOCAL -- so the retirement test and the
            # complement, which are decided out here, were reading the empty
            # dict and refusing BOOK_STALE unconditionally. Exactly the shape
            # of the blocker that cost us the 0200z window.
            #
            # So this is a separate local, used ONLY by the two-sided
            # lifecycle below. `authenticated` is deliberately NOT reassigned.
            ts_book = self.ts_authenticated_book(market, authenticated)
            _tick = getattr(self.transport, "tick_size", None)
            _dticks = (int(os.getenv("L5_TS_DEPTH_TICKS", "2"))
                       + self.probe_level_offset(market))
            # ============ THE COMPLEMENT'S OWN DEPTH, NOT THE OPENING'S
            #
            # THE DEFECT, from the venue record of 2026-11-02 2245z. At
            # 22:45:36.749 this path submitted the complement at own 0.5100
            # while the UP bid was 0.5300 -- two ticks BELOW the touch,
            # behind the entire visible queue, unfillable unless the market
            # moves two more ticks AGAINST the leg we are trying to hedge.
            # Its pair was 0.9600 and it cleared every economic gate. It
            # never filled, and nothing in the campaign's four windows ever
            # completed.
            #
            # `l5_book_completion` already diagnosed and fixed exactly this
            # for ITS path -- COMPLETION_DEPTH_TICKS defaults to 0, and its
            # comment records the same failure at 1445z -- but the two-sided
            # path kept reusing the OPENING depth, and the two-sided path is
            # the one that submits here.
            #
            # An opening rests behind the touch to earn the spread. A
            # completion is removing risk from inventory already held, and
            # queue position is the whole of its fill probability. Those are
            # different jobs and they do not share a number.
            #
            # DEFAULT OFF, so an unset environment prices the complement
            # exactly as the deployed code does and parity is unchanged.
            # Resting AT the touch is a FILLABLE price and nothing more: it
            # is not an assumption of a fill, post-only still proves it does
            # not cross, and no resting complement is ever reported as a
            # hedge.
            _cdticks = _dticks
            try:
                if (os.getenv("L5_TS_COMPLETION_AT_TOUCH") or ""
                        ).strip().upper() in ("1", "YES", "TRUE", "ON"):
                    from src import l5_book_completion as _BCd
                    _cdticks = int(_BCd.completion_depth_ticks())
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                # a depth we cannot resolve falls back to the OPENING depth,
                # which is the behaviour that is already deployed
                _cdticks = _dticks

            rnd = self.probe_round(market)
            if rnd is None:
                return prov.refuse("TWO_SIDED_PROBE",
                                   "ACCUMULATION_EXHAUSTED")

            # A ROUND NOBODY HAS PLACED AN ORDER IN IS PRICED FROM THE BOOK
            # AS IT IS NOW. See ROUND_ON_SUBMISSION_ENV. Its stored plan is
            # dropped here, so nothing below can find it stale, retire the
            # round for it, or re-propose prices the market has left; the
            # planning loop prices the pair afresh and every gate that ran
            # on the old plan runs again on the new one. A round with a
            # claim on either leg is untouched, and so is everything when
            # the switch is not exactly ON.
            if round_charged_on_submission():
                try:
                    if (self.probe_round_never_placed(market, rnd)
                            and self.probe_discard_plan(market, rnd)):
                        # telemetry only: proposals priced again, per
                        # market, since this process started
                        _rr = self.__dict__.setdefault("round_repriced", {})
                        _rr[str(market)] = _rr.get(str(market), 0) + 1
                        prov.stage("TWO_SIDED_ROUND_REPRICED", {
                            "round": rnd,
                            "labels": ("no order was ever submitted in this "
                                       "round, so its plan is priced again "
                                       "from the current book and the round "
                                       "is not spent")})
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    # unreadable: the frozen plan stands, as before

            # RETIRE A ROUND THE MARKET HAS LEFT BEHIND, and only then.
            #
            # THE 0330z WINDOW. A plan frozen against a 0.42/0.56 touch was
            # re-proposed 32 times after the book had gone to 0.91/0.92, by
            # which point its DOWN leg's wire price was 45 cents BELOW the
            # bid -- post-only, the venue rejects that for crossing, so it
            # could never rest -- and its UP leg was 51 cents under the
            # touch, which no fill scenario can reach. Freezing the plan per
            # round is right (re-planning each tick gave legs of 1.89 and
            # 0.98, which is not a matched pair); never ABANDONING it was
            # not.
            #
            # SAFETY FIRST, AND IT IS NOT NEGOTIABLE. Nothing is retired
            # while an order is live or either leg holds filled quantity --
            # not even a partial. Walking past a round that owns a live
            # order would orphan it, and walking past one that holds
            # inventory would strand it, because the complement is tracked
            # per round. `probe_round_is_safe_to_retire` reads both from
            # durable state and treats an unreadable answer as a refusal.
            # The claims themselves are never deleted: retirement is an
            # additive durable fact.
            _stale_plan = self.probe_stored_plan(market, rnd)
            if _stale_plan is not None:
                # THE SPENT LEGS ARE EXCLUDED FROM THE JUDGEMENT, read from
                # the DURABLE claim record rather than from memory.
                #
                # 2215z, 2026-11-02: the DOWN leg filled at 22:17:09, the
                # book ran to 0.8800/0.9000, and that leg's old offer at
                # wire 0.61 then "crossed". `plan_executable` judged both
                # legs and returned on the first crossing one, so it blamed
                # a leg whose price was history and would never be sent
                # again -- while the UP complement at own 0.5600, which did
                # NOT cross, was never judged at all. The record read
                # LEG_WOULD_CROSS:DOWN and no complement was submitted.
                try:
                    _spent = [k for k, v in
                              (self.probe_legs_claimed(market, rnd) or
                               {}).items() if v]
                except BaseException:                          # noqa: BLE001
                    _spent = None      # unreadable claims: judge both legs
                _why = TS.plan_executable(
                    _stale_plan, bid=ts_book.get("bid"),
                    ask=ts_book.get("ask"), tick=_tick,
                    depth_ticks=_dticks, spent_sides=_spent)
                if _why:
                    _blocked = self.probe_round_is_safe_to_retire(market, rnd)
                    prov.stage("TWO_SIDED_ROUND", {
                        "round": rnd, "plan_unexecutable": _why,
                        "spent_legs_excluded": _spent,
                        "retired": not _blocked,
                        "retirement_blocked_by": _blocked,
                        "labels": ("a frozen plan the book has left cannot "
                                   "fill and cannot be completed. It is "
                                   "abandoned only with nothing live and "
                                   "nothing filled; otherwise the deadline "
                                   "owns the order and the residual is "
                                   "carried under the existing limits.")})
                    if not _blocked:
                        self.probe_retire_round(market, rnd, _why)
                        rnd = self.probe_round(market)
                        if rnd is None:
                            return prov.refuse("TWO_SIDED_PROBE",
                                               "ACCUMULATION_EXHAUSTED")

            # THE ENTRY/COMPLETION GATE. Opening a first leg late is the
            # dangerous action: if the window cannot still produce a
            # complement that clears the fee floor, that leg is a directional
            # bet this engine never intended. Completing is the SAFE action --
            # it removes exposure -- so it stays available later. Which one
            # this is comes from the claims on disk, not from memory, so a
            # restart mid-round still completes rather than re-entering.
            _claimed = self.probe_legs_claimed(market, rnd)
            _tnow = float(self._now().timestamp())
            # WHETHER THIS IS A COMPLETION IS A MARKET FACT, NOT A ROUND
            # FACT. `_entering` used to read `not any(_claimed.values())`
            # alone, which is a question about THIS round and says nothing
            # about what the market holds.
            #
            # LIVE 1215z, 2026-10-28. A third UP leg filled at 12:22:46
            # leaving 4.21 UP against 1.92 DOWN. The DOWN completion rested
            # 56s unfilled and was cancelled at 12:24:49; the probe then
            # advanced to round 4, a FRESH round with nothing claimed, so
            # `_entering` read True and this gate asked `may_enter` (T+600)
            # instead of `may_complete` (T+720). Eleven seconds later entry
            # closed and the engine logged PAST_ENTRY_CUTOFF with
            # `decision_role` COMPLEMENT and `two_sided_complement` null at
            # 12:25:03 and 12:26:16. 120s of authorized completion runway
            # went unused while 2.29 UP sat unmatched; at 12:25:03 DOWN was
            # available at own 0.43 for a pair cost of 0.799. The contracts
            # expired worthless and the window settled -$0.3835 instead of
            # roughly +$0.91.
            #
            # `may_complete` already documents the intent this restores:
            # "completing removes risk, so the engine keeps doing it after
            # it has stopped opening."
            _reducing = self.probe_reducing_side(market)
            _entering = not any(_claimed.values()) and _reducing is None
            late = (TS.may_enter(_tnow, float(ws), float(ws) + TS.WINDOW_S)
                    if _entering
                    else TS.may_complete(_tnow, float(ws),
                                         float(ws) + TS.WINDOW_S))
            if late:
                return prov.refuse("TWO_SIDED_PROBE", late)
            # THE LATER DEADLINE IS NOT A LATE OPENING. Past the entry
            # cutoff the ONLY leg that may reach the wire is the one that
            # reduces the residual, so the extra 120s can never be spent
            # taking on fresh directional exposure. Asserted again below,
            # once `nxt` is known, because that is the first point at which
            # the side actually being placed exists.
            _entry_shut = TS.may_enter(_tnow, float(ws),
                                       float(ws) + TS.WINDOW_S) is not None

            # A SPENT DEFENSIVE BUDGET CLOSES THE DOOR, NOT THE BUILDING.
            #
            # The $2 budget is what pays to get OUT of an unmatched leg. Once
            # it is spent, the next stranded leg has no funded exit and would
            # hold to settlement for its full cost -- so opening a new first
            # leg after that point is taking on exactly the risk the budget
            # existed to bound, with the bound gone. New ENTRIES therefore
            # stop.
            #
            # COMPLETIONS DO NOT. `_entering` is false once a leg of this
            # round is claimed, and that is the branch that matches an
            # existing position: refusing it would strand the very inventory
            # this rule is protecting. Managing what we already hold
            # continues under the unchanged hard limits -- the $5 exposure
            # cap, the $5 daily realized-loss halt, the residual bound, one
            # outstanding order, the watchdog and the HALT latch.
            #
            # CAMPAIGN-WIDE, not per-market: one budget, one campaign, and a
            # leg stranded in another market has already spent it. Unreadable
            # reads as spent (`defensive_spent` returns the whole budget on a
            # read that raises), so an unreadable budget stops new entries
            # rather than permitting them.
            if _entering:
                try:
                    _dspent = self.store.defensive_spent()
                except BaseException:                          # noqa: BLE001
                    _dspent = DEF.DEFENSIVE_LOSS_BUDGET_USD
                if _dspent >= DEF.DEFENSIVE_LOSS_BUDGET_USD:
                    prov.stage("TWO_SIDED_PROBE", {
                        "refused": "DEFENSIVE_BUDGET_EXHAUSTED_NO_NEW_ENTRY",
                        "defensive_spent_usd": str(_dspent),
                        "defensive_budget_usd": str(
                            DEF.DEFENSIVE_LOSS_BUDGET_USD),
                        "labels": ("no funded exit remains for a new "
                                   "stranded leg, so no new first leg is "
                                   "opened. Completion of inventory already "
                                   "held continues under the unchanged hard "
                                   "limits.")})
                    return prov.refuse(
                        "TWO_SIDED_PROBE",
                        "DEFENSIVE_BUDGET_EXHAUSTED_NO_NEW_ENTRY")

            # ============== A FROZEN PLAN THE BOOK HAS LEFT IS RE-PRICED
            #
            # See `TS.replan_drift_ticks` for the measurement and for why
            # this is safe. In short: `plan_executable` only notices a leg
            # left behind past 6 ticks, and the only remedy for it is
            # retiring the round -- which at the deployed
            # `ts_round_allocations` of 1 ENDS THE WINDOW. So nothing
            # re-prices a drifted plan, and the engine rests at a price the
            # book has walked away from, where it cannot fill.
            #
            # THE THREE CONDITIONS, all required, all fail-closed:
            #
            #   * the round holds NOTHING -- no live order, nothing
            #     fillable, no unmatched fill -- read through
            #     `probe_round_is_safe_to_retire`, the same durable-state
            #     predicate retirement itself uses. Any answer but None
            #     keeps the frozen plan, so a resting or partly filled
            #     round behaves exactly as before and leg two still
            #     matches leg one. Nothing is cancelled here, so this
            #     cannot churn orders: it runs only when none exist.
            #   * the drift is at least `replan_drift_ticks()` (2), so a
            #     plan already where the planner would price it is left
            #     alone. An unreadable drift is not a drift.
            #   * the fresh plan is `ok`. A plan that will not price is NOT
            #     substituted for one that already did, so this can never
            #     take the engine from a placeable plan to none.
            #
            # The replacement goes through `probe_precheck` and every gate
            # below exactly as a first plan does. It adds no capability and
            # moves no limit.
            _rp_plan = self.probe_stored_plan(market, rnd)
            if _rp_plan is not None:
                try:
                    _rp_blocked = self.probe_round_is_safe_to_retire(
                        market, rnd)
                    _rp_spent = [k for k, v in
                                 (self.probe_legs_claimed(market, rnd)
                                  or {}).items() if v]
                    _rp_drift = TS.plan_drift_ticks(
                        _rp_plan, bid=ts_book.get("bid"), ask=ts_book.get("ask"),
                        tick=_tick, depth_ticks=_dticks,
                        spent_sides=_rp_spent)
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    _rp_blocked, _rp_drift = "REPLAN_STATE_UNREADABLE", None
                _rp_allow = TS.replan_drift_ticks()
                if (_rp_blocked is None and _rp_drift is not None
                        and _rp_drift >= D(_rp_allow)):
                    _rp_fresh = self.probe_plan(market, authenticated)
                    _rp_ok = bool(_rp_fresh.get("ok"))
                    prov.stage("TWO_SIDED_REPLAN", {
                        "round": rnd,
                        "drift_ticks": str(_rp_drift),
                        "allow_ticks": _rp_allow,
                        "repriced": _rp_ok,
                        "refused": (None if _rp_ok
                                    else str(_rp_fresh.get("reason"))[:80]),
                        "was": [{"side": str(l.get("side")),
                                 "price": str(l.get("price"))}
                                for l in (_rp_plan.get("legs") or ())],
                        "now": [{"side": str(l.get("side")),
                                 "price": str(l.get("price"))}
                                for l in (_rp_fresh.get("legs") or ())],
                        "labels": ("the round holds nothing, so there is no "
                                   "sibling leg to match and no order to "
                                   "cancel. A plan that will not price is "
                                   "never substituted for one that did.")})
                    if _rp_ok:
                        # STORED, not assigned. The canonical read below is
                        # left exactly where it was so that the atomic
                        # re-reservation it anchors is unmoved; it simply
                        # reads the plan this repaired.
                        self.probe_store_plan(market, _rp_fresh, rnd)
            plan = self.probe_stored_plan(market, rnd)
            bad = None
            if plan is not None:
                # ATOMIC AT THE RESERVATION, NOT ONLY AT THE PLAN.
                #
                # The pair was gated as a whole when it was sized, and the
                # plan was then stored -- but the SECOND leg used to go out on
                # the strength of that stored decision without the pair being
                # re-checked. Between the two legs the world can move: another
                # market's order, a settlement, a cash change. So the pair is
                # re-reserved here, and a leg is placed only while the WHOLE
                # pair still fits.
                #
                # WHAT THIS CAN AND CANNOT DO. Checked before EITHER leg is
                # submitted it refuses both, which is the guarantee asked for.
                # Re-checked when leg one is already resting it cannot un-rest
                # it -- nothing can -- so it refuses leg two and the
                # completion route takes the single leg from there under the
                # 1.00 ceiling. It is sound only because the sibling is now
                # charged once; with the double charge above it would have
                # refused the complement of every resting leg.
                bad = self.probe_precheck(
                    market, plan,
                    placed_sides=[sd for sd, done
                                  in self.probe_legs_claimed(market,
                                                             rnd).items()
                                  if done])
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
                    self.probe_store_plan(market, plan, rnd)
            if bad:
                prov.stage("TWO_SIDED_PROBE", {
                    "refused": bad, "pair_cost": plan.get("pair_cost"),
                    "labels": ("the PAIR was gated as if BOTH legs were "
                               "already resting, so neither is placed. Leg "
                               "one can never spend leg two's headroom.")})
                return prov.refuse("TWO_SIDED_PROBE", bad[:120])

            # ============== IS ANOTHER OPENING ROUND WORTH ITS WORST CASE
            #
            # 1230z. Round 0 locked a 0.9550 pair at 12:30:49 for +$0.0450.
            # Round 1 then opened UP 0.4600 and DOWN 0.4900 at 12:32:54
            # because `probe_round` returns the first INCOMPLETE round and
            # asked nothing else. The UP filled at 12:33:57, the DOWN never
            # did, and the window settled -$0.4100. The round count was not
            # the defect; nobody priced the round.
            #
            # NOT A CAP. This gates round >= 1 ONLY when that round has no
            # leg claimed yet, so completing a round already underway is
            # never blocked -- and the gate is arithmetic, not a counter, so
            # repeated rounds stay available wherever the economics support
            # them. The record carries `breakeven_single_outcome_usd` so a
            # refusal always states what would have to be true instead.
            _reopen = self.probe_reopen_decision(
                market, rnd, plan, bid=ts_book.get("bid"),
                ask=ts_book.get("ask"), tick=_tick, depth_ticks=_dticks,
                now_s=_tnow, window_end_s=float(ws) + TS.WINDOW_S)
            prov.stage("TWO_SIDED_REOPEN", _reopen)
            if not _reopen.get("reopen"):
                return prov.refuse(
                    "TWO_SIDED_PROBE",
                    ("REOPENING_NOT_JUSTIFIED:%s"
                     % (_reopen.get("reason") or "UNSTATED"))[:120])

            claimed = self.probe_legs_claimed(market, rnd)
            legs = {l["side"]: l for l in plan["legs"]}
            # deterministic: the CHEAPER leg first, because a cheap leg
            # filling alone is the smaller worst case.
            order = sorted(legs, key=lambda k: D(legs[k]["price"]))
            # A HELD RESIDUAL OUTRANKS CHEAPNESS. See probe_leg_order.
            order = self.probe_leg_order(market, legs, order)
            nxt = next((k for k in order if not claimed.get(k)), None)
            if nxt is None:
                # probe_round only returns an INCOMPLETE round, so this is
                # unreachable unless the claim state changed underneath us.
                return prov.refuse("TWO_SIDED_PROBE", "ACCUMULATION_EXHAUSTED")
            # ============ THE PORTFOLIO CONTROLLER'S STEER, IF THERE IS ONE
            #
            # Absent -- every configuration that does not set
            # L5_PORTFOLIO_CONTROLLER=STEER -- this block does not run. When
            # present it can only NARROW the choice already made: among the
            # legs this round has not claimed it prefers one the controller
            # wants opened, and if it wants none of them the placement is
            # refused by name. It never offers a leg the order above did not
            # contain, and a reducing leg is never the steer's to refuse.
            if self.portfolio_steer is not None:
                # A HEDGE MEANS FILLED INVENTORY. `_reducing` counts a
                # RESTING order as though it had filled, which is right for
                # choosing the next leg and wrong here: under it the second
                # leg of a pair is always "reducing" the first, and the
                # steer could never decline it.
                _held_red = self.portfolio_held_reducing_side(market)
                # ...AND A HEDGE THAT IS STILL NEEDED. If resting orders
                # already cover the filled inventory, `_reducing` -- which
                # counts them -- is None, and another order on that side is
                # not a hedge any more: it is the first leg of a new round,
                # and the steer decides it like any other opening. At
                # 21:31:42Z on 2026-11-10 that second order was placed
                # beside a resting hedge while its pair's other leg was
                # refused, leaving a round with one leg.
                if _held_red is not None and _held_red != _reducing:
                    _held_red = None
                _wanted = [k for k in order if not claimed.get(k)
                           and self.portfolio_steer_refusal(
                               k, _held_red) is None]
                if not _wanted:
                    _why = (self.portfolio_steer_refusal(nxt, _held_red)
                            or "PORTFOLIO_SIDE_NOT_WANTED")
                    prov.stage("TWO_SIDED_PROBE", {
                        "refused": _why, "leg_to_place": nxt,
                        "reducing_side": _held_red,
                        "labels": ("the portfolio controller does not want "
                                   "this side opened on this book and "
                                   "inventory. Not a limit and not a "
                                   "fault: an opening it judged not worth "
                                   "its risk.")})
                    return prov.refuse("TWO_SIDED_PROBE", _why)
                nxt = _wanted[0]
            # PAST THE ENTRY CUTOFF, ONLY A COMPLETION. `_entry_shut` means
            # `may_enter` has closed and we are here on the completion
            # deadline instead; the one leg that may be placed is the
            # reducing one. `probe_leg_order` already puts it first and
            # `probe_leg_qty_cap` already stops it overshooting into a
            # reversal, so this normally agrees with `nxt` -- it is the
            # explicit refusal that makes "no new directional opening after
            # T+600" a property of this gate rather than of their
            # interaction.
            if _entry_shut and nxt != _reducing:
                prov.stage("TWO_SIDED_PROBE", {
                    "refused": "PAST_ENTRY_CUTOFF_NOT_A_COMPLETION",
                    "leg_to_place": nxt, "reducing_side": _reducing,
                    "labels": ("the completion deadline extends the window "
                               "for REDUCING a residual only. Opening a new "
                               "leg here would be fresh directional "
                               "exposure taken after entry closed.")})
                return prov.refuse("TWO_SIDED_PROBE",
                                   "PAST_ENTRY_CUTOFF_NOT_A_COMPLETION")
            leg = legs[nxt]

            # ============== THE OPENING LEG'S OWN-SIDE PRICE FLOOR
            #
            # `order` above is sorted by price, so with nothing claimed `nxt`
            # IS the first leg and it is the cheaper side. On 2026-10-23 the
            # four windows that opened at own 0.29-0.36 all completed and
            # settled positive, and the two that opened at own 0.12 and 0.06
            # never completed and settled -$0.1764 and -$0.0930. See
            # l5_two_sided_probe.MIN_FIRST_LEG_OWN_PRICE for the mechanism,
            # and for why six windows is preliminary and 0.20 is not claimed
            # to be optimal.
            #
            # IT IS CONSULTED HERE AND NOWHERE ELSE, so it can only ever
            # refuse an OPENING. The predicate is additionally handed the
            # claim state and the held quantities and permits the leg
            # unconditionally if either says this is a completion -- the
            # floor is its LAST test, not its first.
            _floor_no = TS.first_leg_entry_refusal(
                side=nxt, own_px=leg.get("price"), legs_claimed=claimed,
                held_up=self.store.position(market, UP),
                held_down=self.store.position(market, DOWN))
            if _floor_no is not None:
                prov.stage("TWO_SIDED_PROBE", dict(
                    _floor_no, leg_to_place=nxt, legs_claimed=claimed,
                    pair_cost=plan.get("pair_cost"),
                    labels=("an OPENING refusal. No order is placed and no "
                            "round is claimed, so the window stays "
                            "available to a later, eligible book.")))
                return prov.refuse("TWO_SIDED_PROBE",
                                   str(_floor_no["reason"])[:120])

            # THE COMPLEMENT IS PRICED NOW, NOT AT PLANNING TIME.
            #
            # With one live-order slot the complement can never rest beside
            # the first leg -- it is refused MAX_LIVE_ORDERS until that leg
            # leaves the book -- so it is ALWAYS placed later, against a
            # book that has moved. Carrying the frozen price is what left
            # the 0330z complement 45 cents through the touch.
            #
            # Only when the OTHER side actually holds filled quantity, so
            # this is a genuine completion and not a first leg: the quantity
            # is the unmatched remainder (never more than was filled), and
            # the economics are judged on the cost we REALLY paid plus this
            # quote. A complement that no longer clears the fee floor is
            # REFUSED, not repriced into a loss -- the residual is then
            # carried under the existing controls, which is an economic
            # decision and not a deadline.
            _other = "DOWN" if nxt == "UP" else "UP"
            try:
                _held = self.store.position(market, _other)
                _mine = self.store.position(market, nxt)
            except BaseException:                              # noqa: BLE001
                _held = _mine = None
            _need = (None if (_held is None or _mine is None)
                     else _held - _mine)
            # UNMATCHED REMAINDER, not merely "the other side holds
            # something". After a completed round BOTH sides hold stock and
            # the book is matched, so `_held > 0` was true while there was
            # nothing to complete -- the next round's FIRST leg was then
            # routed down the complement path and refused NOTHING_TO_MATCH,
            # which stopped accumulation dead at one pair. A completion
            # exists only while one side is ahead of the other.
            if _need is not None and _need > D(0):
                _legs = self.store.leg_cost(market)
                _basis = _legs.get(_other)
                _avg = (_basis / _held) if (_basis is not None
                                            and _held > D(0)) else None
                # THE COMPLETION CONTEXT. With the inventory actually held
                # and the owner's residual budget, `complement_order` judges
                # this on `completion_ceiling` -- does completing beat
                # carrying the residual to settlement -- instead of on the
                # 0.96 OPENING cap, and it quotes at the touch rather than
                # `depth_ticks` behind it. Withholding any of these reverts
                # it to the opening cap, which is the stricter path, so an
                # unreadable budget cannot buy a dearer completion.
                #
                # The budget is the owner's approved worst-case directional
                # deficit, read from the gate that enforces it rather than
                # from a second copy of the number.
                _vg = getattr(self, "validation_gates", None)
                _resid_budget = getattr(_vg, "max_worst_case_deficit_usd",
                                        None)
                comp = TS.complement_order(
                    side=nxt, qty=_need, cost_px=_avg,
                    held_qty=_held, held_cost_usd=_basis,
                    deficient_qty=_mine,
                    deficient_cost_usd=_legs.get(nxt),
                    residual_budget_usd=_resid_budget,
                    bid=ts_book.get("bid"), ask=ts_book.get("ask"),
                    tick=_tick,
                    min_qty=getattr(self.transport, "quantity_increment",
                                    None),
                    depth_ticks=_cdticks,
                    book_age_s=ts_book.get("stream_age_s"),
                    gap_suspected=bool(ts_book.get("gap_suspected")),
                    # WHAT THE COMPLEMENT IS FOR, stated rather than
                    # implied. The market is the one this inventory is in
                    # and the one `ts_book` was resolved for, so a
                    # complement priced from any other book is refused at
                    # the adoption site below instead of being trusted.
                    # `_other` is the side we HOLD, so `complement_order`
                    # can assert that `nxt` is its opposite rather than
                    # take it on faith -- both are derived from the same
                    # variable here, which is exactly why the assertion
                    # belongs on the far side of the call.
                    market=market, held_side=_other,
                    # THE RESTING SIDE'S OWN DEPTH. A maker BUY of UP rests
                    # a YES bid and a BUY of DOWN rests a YES sell on the
                    # ask, so `complement_order` selects by side -- it is
                    # handed both and picks, rather than being handed the
                    # taker-side depth the defensive unwind needs below.
                    bid_shares=ts_book.get("bid_shares"),
                    ask_shares=ts_book.get("ask_shares"))
                # ============ DEFENSIVE COMPLETION, IN TWO PHASES
                #
                # PRIORITY ONE IS STILL THE PROFITABLE COMPLETION above, and
                # `DEF.decide` refuses while that completion is reachable at
                # the live book. Only when it is not, and only in the last
                # minute before the BINDING window terminal, is unwinding
                # considered at all.
                #
                # THE EXECUTABLE PRICE, NOT THE MAKER PRICE. `comp`'s price
                # is a post-only quote `_cdticks` behind the touch -- what we
                # HOPE to fill at. An unwind has to cross, so its price is
                # the touch on the side we would consume: buying DOWN sells
                # the YES token into the bid, buying UP lifts the ask.
                _exit_px = None
                try:
                    _b = D(str(ts_book.get("bid")))
                    _a = D(str(ts_book.get("ask")))
                    if _b > 0 and _a > _b:
                        _exit_px = (D(1) - _b) if nxt == DOWN else _a
                except BaseException:                          # noqa: BLE001
                    _exit_px = None
                # ONE crossing leg's fee, on its own price. `comp`'s
                # `fee_floor` charges BOTH legs as takers, and the first leg
                # has already filled as a maker -- using the pair figure
                # would roughly double the fee and misprice the comparison.
                _dfee = (None if _exit_px is None
                         else TS.TAKER_THETA * _exit_px * (D(1) - _exit_px))
                _cc = None
                try:
                    _cc = (TS.cancel_cutoff(float(ws) + TS.WINDOW_S)
                           - float(ws))
                except BaseException:                          # noqa: BLE001
                    _cc = None
                _dkw = dict(
                    now_t=_tnow, window_start=float(ws),
                    # BOTH clocks. `complete_cutoff_s()` alone is 720, and
                    # `may_complete` refuses PAST_TERMINAL from 600 at the
                    # deployed cancel margin -- a guard built on 720 would
                    # arm entirely inside dead time.
                    complete_cutoff_s=TS.complete_cutoff_s(),
                    cancel_cutoff_s=_cc,
                    unmatched_qty=_need, first_leg_cost=_avg,
                    exit_price=_exit_px,
                    maker_complement_price=(comp.get("leg") or {}).get(
                        "price") or comp.get("complement_price"),
                    buy_side=nxt,
                    # SYMMETRIC, AND FROM ONE VARIABLE. Buying DOWN sells
                    # the YES token INTO the bid and consumes bid depth;
                    # buying UP lifts the ask and consumes ask depth. Both
                    # the price above and the depth here are derived from
                    # `nxt`, so they cannot come to disagree about which
                    # side of the book is being taken.
                    displayed_depth=(ts_book.get("bid_shares")
                                     if nxt == DOWN
                                     else ts_book.get("ask_shares")),
                    taker_fee_per_contract=_dfee,
                    defensive_spent_usd=self.store.defensive_spent(),
                    # THE DWELL ANCHOR. `stranded_since` returns None unless
                    # the ledger holds inventory in this market AND an
                    # authoritative fill stamped the moment it went off flat,
                    # and the flag gates it besides -- so with the flag off,
                    # or on a store that predates the field, or on a market
                    # whose stamp we cannot source, this is None and `decide`
                    # falls back to the arm window with the deployed timing
                    # unchanged. Both phases share `_dkw`, so the precheck
                    # and the authorizing verdict cannot disagree about it.
                    stranded_since_t=(self.store.stranded_since(market)
                                      if DEF.dwell_enabled() else None),
                    window_settled=False)
                _resting = []
                try:
                    _resting = sorted(
                        str(c) for c, r in
                        (self.store.live_orders() or {}).items()
                        if str((r or {}).get("market") or "") == str(market))
                except BaseException:                          # noqa: BLE001
                    _resting = ["__unreadable__"]
                _defensive = None
                try:
                    # PHASE ONE asks only whether this is the moment and
                    # whether the economics sit inside every bound. It is
                    # deliberately asked with NO resting order declared,
                    # because its answer is what decides whether to cancel
                    # at all -- and it authorizes nothing. Phase two, inside
                    # `defensive_execute`, is the only verdict that can
                    # submit, and it runs after the venue has confirmed the
                    # book is clear.
                    _defensive = DEF.decide(**_dkw)
                except BaseException as exc:               # noqa: BLE001
                    # a failure here is recorded, never raised into the
                    # money path, and refuses by omission.
                    _defensive = {"act": False,
                                  "reason": "DEFENSIVE_EVAL_FAILED",
                                  "error": "%s: %s" % (type(exc).__name__,
                                                       str(exc)[:120])}
                _defensive = dict(_defensive, phase="PRECHECK",
                                  resting_orders=_resting,
                                  flag_enabled=bool(DEF.enabled()))
                _dact = None
                if _defensive.get("act"):
                    try:
                        _dact = self.defensive_execute(
                            market=market, buy_side=nxt, held_side=_other,
                            resting=_resting, decide_kwargs=_dkw, prov=prov)
                    except BaseException as exc:           # noqa: BLE001
                        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            raise
                        _dact = {"ok": False,
                                 "reason": "DEFENSIVE_EXECUTE_FAILED",
                                 "error": "%s: %s" % (type(exc).__name__,
                                                      str(exc)[:120])}
                # ============ THE BOUNDED TAKER COMPLETION
                #
                # ONLY WHEN THE DEFENSIVE UNWIND DID NOT ACT, and only when
                # the runner has attached a FRESH proposal its own economics
                # and the bounded policy both authorized. The two routes
                # share one mandate slot and one budget, so they can never
                # both put a crossing order on the book for this market in
                # this window; the defensive one is asked first because it
                # is the one that exists for the last minute before the
                # terminal, where there is nothing else left to try.
                #
                # INERT WITHOUT BOTH SWITCHES. `completion_taker_execute`
                # checks them before it reads any state or cancels anything,
                # and `taker_completion_proposal` is None unless the runner
                # put one there, so with the policy off this block is
                # indistinguishable from not existing.
                #
                # SINGLE USE. The proposal is taken off the executor here,
                # before it is acted on, so a refusal downstream cannot be
                # re-ridden on the next tick by a proposal nobody refreshed.
                _tprop = getattr(self, "taker_completion_proposal", None)
                self.taker_completion_proposal = None
                _tact = None
                if (_dact is None or not _dact.get("ok")) and _tprop:
                    try:
                        _tact = self.completion_taker_execute(
                            market=market, buy_side=nxt, held_side=_other,
                            resting=_resting, proposal=_tprop,
                            window_start=ws, now_t=_tnow, prov=prov)
                    except BaseException as exc:           # noqa: BLE001
                        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            raise
                        _tact = {"ok": False,
                                 "reason": "COMPLETION_TAKER_EXECUTE_FAILED",
                                 "error": "%s: %s" % (type(exc).__name__,
                                                      str(exc)[:120])}
                prov.stage("TWO_SIDED_COMPLEMENT", dict(
                    comp, round=rnd, matching_side=_other,
                    filled_qty=str(_held), already_matched=str(_mine),
                    unmatched=str(_need),
                    frozen_price_superseded=str(leg.get("price")),
                    defensive=_defensive,
                    defensive_action=_dact,
                    taker_completion_proposed=bool(_tprop),
                    taker_completion_action=_tact))
                if _dact is not None and _dact.get("ok"):
                    # THE DEFENSIVE ORDER REPLACES THE COMPLEMENT, and it is
                    # reached only with the complement venue-confirmed gone.
                    leg = dict(_dact["leg"])
                elif _tact is not None and _tact.get("ok"):
                    # SAME RULE, SAME REASON: reached only with the resting
                    # complement venue-confirmed gone, sized to the confirmed
                    # deficit, and covered by a mandate naming this order.
                    leg = dict(_tact["leg"])
                elif not comp.get("ok"):
                    return prov.refuse("TWO_SIDED_PROBE",
                                       str(comp.get("reason"))[:120])
                else:
                    # ADOPTION, AND WHAT IT IS CHECKED AGAINST FIRST.
                    #
                    # `complement_order` priced this against a book, a cost
                    # basis and a quantity. Everything it was given is
                    # checked inside it; what it CANNOT check is whether the
                    # caller handed it the right market and the right side,
                    # because those are the caller's own variables. So they
                    # are verified here, against the inventory this leg is
                    # meant to match, before the leg replaces the plan.
                    #
                    # Neither can currently fail -- both are passed from
                    # `market` and `_other` a few lines above. That is the
                    # point: an adoption that silently matched the wrong
                    # market's book, or doubled the side we already hold,
                    # would look exactly like a correct one in the record.
                    _cm = comp.get("market")
                    if _cm is not None and str(_cm) != str(market):
                        return prov.refuse(
                            "TWO_SIDED_PROBE",
                            "COMPLEMENT_MARKET_MISMATCH:%s" % str(_cm)[:60])
                    if str((comp.get("leg") or {}).get("side")) != str(nxt):
                        return prov.refuse("TWO_SIDED_PROBE",
                                           "COMPLEMENT_SIDE_MISMATCH")
                    leg = dict(comp["leg"])
                # NO PAIR RE-GATE HERE, DELIBERATELY. `probe_precheck` prices
                # both legs as if BOTH were still outstanding, which is right
                # when neither has been placed -- it stops leg one spending
                # leg two's headroom. Applied to a completion it DOUBLE-COUNTS
                # the filled leg, which is already carried as inventory by
                # `inventory_for_gates`: re-gating this complement that way
                # produced a phantom $5.976 commitment against a $4.65 cap and
                # refused a pair that in fact fits. The complement is ONE
                # order and the gates it must pass are the single-order ones
                # below -- the validation gates and `limits.check`, both of
                # which see the filled leg as inventory and this order as the
                # only thing outstanding. Its ECONOMICS were already judged
                # by `complement_order` against the cost we really paid.

            # rewrite BOTH the decision and the translation: downstream code
            # reads dec["side"] for the order intent and the residual
            # direction, and tr for price and quantity.
            dec["side"] = leg["side"]
            # `executable` IS RE-STAMPED ONTO tr FURTHER DOWN
            # (tr = dict(tr, qty=executable)), so overriding tr alone is not
            # enough: the lambda quantity would come back and the override
            # would silently become a lambda-sized order. Found by the
            # money-field integrity check. Both are set here.
            executable = self.probe_leg_qty_cap(market, nxt, D(leg["qty"]))
            # THE LEG'S OWN MODE. Every leg this path builds is MAKER except
            # the defensive unwind, which must cross or it is not an unwind;
            # a missing mode falls back to MAKER, so nothing else changes.
            # `l5_order_body.from_intent` derives participateDontInitiate
            # from this field, and the maker-only overlay reads it too --
            # which is why the overlay needs the mandate to let it past.
            tr = dict(tr, ok=True,
                      mode=(str(leg.get("mode") or FT.MAKER).upper()),
                      price=D(leg["price"]), qty=executable)
            prov.stage("TWO_SIDED_PROBE", dict(
                plan, leg_to_place=nxt, legs_claimed=claimed,
                accumulation_round=rnd,
                accumulation_max_rounds=self.acc_max_rounds(),
                replaced_l5_intent=dict(superseded_l5)))
            self._probe_leg_pending = nxt
            self._probe_round_pending = rnd

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
            # DEFENSIVE-ONLY TAKER, NEVER GLOBALLY. The overlay stands for
            # every order in the system; the single exception is an order a
            # live defensive mandate names exactly, which exists only
            # between a venue-confirmed cancellation of the resting
            # complement and the one order that replaces it. An unwind that
            # could not cross would not be an unwind -- resting a limit is
            # what we are abandoning -- so the exemption is the policy, not
            # a convenience.
            _mand = self.defensive_mandate_covers(market, dec, tr)
            if not _mand:
                return prov.refuse("SAFETY_OVERLAY", "MAKER_ONLY_NO_TAKER")
            prov.note("DEFENSIVE_TAKER_PERMITTED", dict(
                _mand, labels=("taker permitted for THIS order only. The "
                               "maker-only overlay is unchanged for every "
                               "other order and for every later tick.")))

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
                l5_scoped_realized_today=self.store.l5_realized_today(),
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
        # ACCOUNT-WIDE exposure, every market, manual included. A figure we
        # cannot compute refuses: a partial sum understates the account, and
        # understating is the only direction that admits more risk than the
        # bound allows.
        acct_risk = self.store.account_wide_risk_by_market(
            extra_market=market, extra_side=dec["side"],
            extra_qty=tr["qty"], extra_price=tr["price"])
        if not acct_risk.get("ok"):
            prov.stage("SAFETY_OVERLAY", {"refused": acct_risk["reason"]})
            return prov.refuse("SAFETY_OVERLAY", acct_risk["reason"])
        # THE DAILY HALT READS THE DAY, account-wide. `realized_today()` is
        # an ALL-TIME total, so feeding it to a limit named "daily" made that
        # limit a lifetime cap and made the separate $20 cumulative limit
        # dead. The population is unchanged -- every market, manual included
        # -- and the all-time total still binds at the two cumulative gates
        # below. None is UNKNOWN and refuses; it is never read as zero.
        day_realized = self.store.realized_current_utc_day()
        if day_realized is None:
            prov.stage("SAFETY_OVERLAY",
                       {"refused": "DAY_REALIZED_UNAVAILABLE"})
            return prov.refuse("SAFETY_OVERLAY", "DAY_REALIZED_UNAVAILABLE")
        # THE SECOND MAKER SLOT, established as EVIDENCE before the gate
        # runs. `_probe_leg_pending` and `_probe_round_pending` were set by
        # the two-sided path above when it chose this leg, so the claim state
        # is read for the SAME round that produced this order. Anything
        # missing -- another strategy's order, no pending round, an
        # unreadable store -- leaves the ceiling where it was.
        _pair_slot = False
        _pair_slot_why = "NOT_A_TWO_SIDED_LEG"
        try:
            _rnd = getattr(self, "_probe_round_pending", None)
            if _rnd is not None and TS.two_order_pair_enabled():
                _pair_slot_why = TS.second_leg_slot_refusal(
                    market=market, side=str(dec["side"]),
                    live_orders=self.store.live_orders(),
                    legs_claimed=self.probe_legs_claimed(market, _rnd))
                _pair_slot = _pair_slot_why is None
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            _pair_slot, _pair_slot_why = False, (
                "PAIR_SLOT_LAYER_FAILED:%s" % type(exc).__name__)
        prov.stage("PAIR_SECOND_SLOT", {
            "granted": bool(_pair_slot),
            "why_not": None if _pair_slot else str(_pair_slot_why),
            "enabled": TS.two_order_pair_enabled(),
            "labels": ("the slot raises MAX_LIVE_ORDERS by exactly one for "
                       "this order. Every cash, exposure, residual, "
                       "per-market and loss bound still prices a resting "
                       "order as though it fills in full, so it buys a slot "
                       "and never headroom.")})
        why = self.limits.refuse(
            price=tr["price"], qty=tr["qty"], market=market,
            committed_by_market=self.store.committed(),
            residual_after=resid_after,
            realized_today=day_realized,
            open_markets=open_markets,
            outstanding_by_market=outstanding,
            pair_second_leg_ok=_pair_slot,
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
            risk_by_market=acct_risk["risk_by_market"])
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
            # FILL-CONTINGENT WORST CASE, via the ONE shared definition in
            # `residual_cap_decision`. The final guard before the wire calls
            # the same function, so the two cannot drift -- and the primary
            # gate does NOT depend on that guard to correct it.
            #
            # WHAT THIS REPLACED. The basis was PROSPECTIVE_NET, which netted
            # both sides. On 1415z (2026-10-31 14:21:01) it read
            # `residual_realized 2.17` against `outstanding_residual -2.17`
            # -- UP 2.17 HELD, a DOWN 2.17 completion RESTING -- summed them
            # to 0.00, fired `exempt_open = (net == 0)`, and let an
            # exposure-increasing UP 2.08 through as a first leg. The DOWN
            # leg was cancelled unfilled and the position became UP 4.25
            # against a 3-contract ceiling. A resting opposite leg is an
            # intention, never a completed hedge.
            #
            # `same_side_includes_this_order=False`: this runs BEFORE
            # `record_submit`, so the order is not yet in the sums.
            outstanding = self.store.outstanding_residual(market)
            _side = str(dec["side"])
            _same = self.store.outstanding_on_side(market, _side)
            _other = self.store.outstanding_on_side(
                market, DOWN if _side.upper() == UP else UP)
            _rc = residual_cap_decision(
                resid=D(str(resid_before)), same_side=D(str(_same)),
                other_side=D(str(_other)), qty=D(str(tr["qty"])),
                side=_side, k=self.residual_cap_k,
                clip=D(str(self.store.max_clip(market))),
                same_side_includes_this_order=False,
                certain=(self.store.orders_uncertain() is None))
            prov.note("RESIDUAL_CAP", dict(
                _rc,
                max_clip_filled=str(self.store.max_clip(market)),
                clip_basis="max(largest filled clip, this order's qty)",
                outstanding_residual_net=str(outstanding),
                inventory_class_realized=klass))
            if _rc["binds"]:
                return prov.refuse("SAFETY_OVERLAY", "RESIDUAL_ABOVE_CAP")

        # THE CONTROLLER'S KEY, RESOLVED HERE AND NOWHERE EARLIER.
        #
        # The key becomes the `client_order_id` immediately below, and for
        # an autonomous order it must name the (round, leg) actually being
        # placed -- a key fixed per market would make the complement look
        # like a duplicate of the opening leg and suppress it. Those two
        # facts are chosen by the two-sided branch above, which publishes
        # them on `self`, so this is the first point at which the key CAN
        # be correct. Every gate between there and here has already run on
        # the order itself, so moving the key does not move a gate.
        #
        # FAIL CLOSED IF THE BRANCH DID NOT RUN. The autonomous path exists
        # only to place two-sided legs, each under its own
        # `claim_accumulation_leg`. If no leg is pending, there is nothing
        # legitimate to submit, and submitting under the UNRESOLVED
        # placeholder would be an order with no claimable identity.
        if controller is not None:
            _clg = getattr(self, "_probe_leg_pending", None)
            _crd = getattr(self, "_probe_round_pending", None)
            # THE ONE COUPLING THAT BLOCKED THE CANDIDATE ENTIRELY.
            #
            # The claimable identity was available ONLY in the round-scoped
            # form, so a decision taken without a round -- which is every
            # decision `l5_continuous_inventory` makes -- hit
            # CONTROLLER_NO_PROBE_LEG here and was refused BEFORE submission.
            # The continuous candidate could therefore never place an order
            # at all, which is what stopped it from being testable against
            # real venue fills under any authorization.
            #
            # `_continuous_key_pending` carries that decision's own
            # content-addressed key (`l5_continuous_inventory.action_key`,
            # on market/side/price/qty). It is accepted only when the flag
            # is on AND the candidate actually set one, so with no caller
            # change and no flag this branch is byte-for-byte what it was.
            #
            # FAIL CLOSED IS PRESERVED, and that is the property that
            # matters: if NEITHER a round-scoped leg NOR a continuous key is
            # pending there is still nothing legitimate to submit and this
            # still refuses. Nothing may submit under the UNRESOLVED
            # placeholder.
            #
            # IDEMPOTENCE IS PRESERVED because the continuous key is also
            # DERIVED, not minted: the same wanted order yields the same key
            # and hence the same client_order_id, so a repeated trigger is
            # still DUPLICATE_SUPPRESSED. It is not round-scoped, which is
            # the intended behavioural change, and every gate between the
            # decision and this line has already run on the order itself --
            # so moving the key does not move a gate.
            if continuous_inventory_enabled():
                # DERIVED HERE, from the order that is actually about to be
                # submitted, so no new durable state is introduced and
                # nothing has to survive a call. `tr` and `dec` are the same
                # translated order and decision the payload is built from
                # thirty lines below, which is what makes the key and the
                # order one object rather than two that could disagree.
                from src import l5_continuous_inventory as _CI
                key = _CI.action_key(market, str(dec["side"]),
                                     D(str(tr["price"])), D(str(tr["qty"])))
            elif not _clg or _crd is None:
                return prov.refuse("CONTROLLER_INTENT",
                                   "CONTROLLER_NO_PROBE_LEG")
            else:
                key = controller_action_key(market, _crd, _clg)
            prov.rec["action_key"] = key

        # WHICH PLACEMENT OF THIS LEG THIS IS, and it must be the SAME index
        # the claim is taken under below.
        #
        # 1445z, 2026-10-30. `acc_leg_attempt` already grants a later attempt
        # over a leg that holds nothing with nothing live, and
        # `probe_legs_claimed` already reports such a leg unspent so the
        # chooser re-offers it -- but the coid was built here at attempt ZERO
        # regardless. A permitted re-placement therefore landed on the FIRST
        # placement's id and was suppressed as a duplicate two lines below,
        # before the claim was ever reached. That made `L5_ACC_MAX_ATTEMPTS`
        # unreachable in practice and left a cancelled-unfilled complement
        # unattended for the rest of the window: 1.91 DOWN sat with nothing
        # resting for 12 minutes and settled for -$0.7067.
        #
        # IDEMPOTENCE IS UNCHANGED, because the id is still DERIVED: the same
        # (leg, attempt) always yields the same coid, so a replay of THIS
        # placement is still suppressed. Only a placement the attempt axis
        # has actually granted gets a new id. Attempt zero keeps its old id
        # byte-for-byte, exactly as `accumulation_sid` does, so first
        # placements and every claim already on the volume are unaffected.
        #
        # THE SAFETY RULE STILL LIVES IN ONE PLACE. This reads
        # `acc_leg_attempt` rather than counting anything itself, so a FILLED
        # leg still can never be re-placed, an unverified cancellation still
        # cannot buy a retry, and the budget still bounds the total.
        _att = 0
        _lp = getattr(self, "_probe_leg_pending", None)
        _rp = getattr(self, "_probe_round_pending", None)
        if TS.active(market) and _lp and _rp is not None:
            _granted = self.acc_leg_attempt(market, _rp, _lp)
            if _granted is not None:
                _att = int(_granted)
        if _att:
            prov.rec["placement_attempt"] = _att
        coid = client_order_id(key, _att)
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
            # ============== THE 1215z LOSS, AND THE GATE THAT WAS MISSING
            #
            # WHAT HAPPENED. Two real orders filled 38 seconds apart in
            # cpc-btc-updown-15m-2026-10-20-1215z: UP 2.46 at 0.9400
            # (D78BWYNG10JH) and DOWN 2.46 at 0.9000 (D78C4Q7G10JR). A
            # matched pair costing 1.84 pays exactly 1.00 at settlement, so
            # that book is a CERTAIN loss of -$2.0664 before fees. Nothing
            # refused it, because the pair-cost ceiling lived only inside
            # `build_orders` -- the two-sided planner -- and these orders did
            # not come from it. The L5-following path copies L5's own side
            # and price and had no pair discipline at all.
            #
            # SO THE CEILING MOVES HERE, to the one boundary every order
            # passes, and it is judged on CONFIRMED FILLS rather than on a
            # plan: cost already paid for this market, plus this order, over
            # the matched quantity it would create. The first leg of a pair
            # is unconstrained (there is nothing to match yet, so no pair
            # cost exists); it is the leg that CREATES a matched pair that
            # has to justify one.
            #
            # A submitted order is never a hedge. Only `leg_cost`, which is
            # written from venue-confirmed fills, counts here.
            # THE FAVORITE-ONLY TEST ADMITS ONE KIND OF ORDER. Checked here,
            # at the one boundary every order passes, so no completion,
            # requote or defensive route can hedge a favorite that the
            # owner's test holds to settlement. Off unless the profile is
            # named exactly; then it only ever refuses.
            _fav_bad = self.favorite_only_refusal(dec)
            if _fav_bad:
                prov.stage("ORDER", {
                    "client_order_id": coid, "submitted": False,
                    "reason": "FAVORITE_ONLY", "detail": _fav_bad,
                    "labels": ("the favorite-only test submits the market's "
                               "favorite and nothing else: no second leg, "
                               "no hedge, no pair.")})
                return prov.refuse("FAVORITE_ONLY",
                                   str(_fav_bad.get("why"))[:120])
            # NO ACCIDENTAL ADD TO THE SIDE ALREADY HELD. Inert unless
            # completions rest at their target; then it only ever refuses. See
            # `held_side_add_refusal`.
            _add_bad = self.held_side_add_refusal(market, dec)
            if _add_bad:
                prov.stage("ORDER", {
                    "client_order_id": coid, "submitted": False,
                    "reason": "HELD_SIDE_ADD", "detail": _add_bad,
                    "labels": ("with unmatched inventory held, recovery "
                               "works the completing side only; buying more "
                               "of the held side needs the portfolio "
                               "controller to ask for it.")})
                return prov.refuse("HELD_SIDE_ADD",
                                   str(_add_bad.get("why"))[:120])
            _pair_bad = self.pair_cost_refusal(market, dec, tr)
            if _pair_bad:
                prov.stage("ORDER", {
                    "client_order_id": coid, "submitted": False,
                    "reason": "PAIR_COST_CEILING", "detail": _pair_bad,
                    "labels": ("judged on venue-confirmed fills plus this "
                               "order. A pair above the ceiling pays less "
                               "at settlement than it costs to buy.")})
                return prov.refuse("PAIR_COST_CEILING",
                                   str(_pair_bad.get("why"))[:120])

            # THE SIMULTANEOUS-PAIR GUARD, additive to the ceiling above.
            #
            # `pair_cost_refusal` is judged on CONFIRMED FILLS and engages
            # only on the leg that creates matched quantity, so two legs
            # submitted while the book is flat are each a first leg and
            # neither is judged -- the defect c370c13d demonstrates, and the
            # shape of the recorded 1215z incident. This asks the question
            # that survives any fill order: is some REACHABLE combination of
            # our own orders a pair costing more than the $1.00 it redeems?
            #
            # It only ever REFUSES, its bound is redemption rather than the
            # 0.96 ceiling (so the profitable 0.97-0.99 simultaneous pairs
            # stay allowed), and it treats resting orders as potential
            # exposure, never as protection.
            _combo_bad = self.worst_pair_combination_refusal(market, dec, tr)
            if _combo_bad:
                prov.stage("ORDER", {
                    "client_order_id": coid, "submitted": False,
                    "reason": "WORST_PAIR_COMBINATION", "detail": _combo_bad,
                    "labels": ("judged on the dearest reachable own price "
                               "per side across confirmed holdings, our "
                               "live orders and this order. A pair above "
                               "$1.00 is a certain loss.")})
                return prov.refuse("WORST_PAIR_COMBINATION",
                                   str(_combo_bad.get("why"))[:120])

            # ONE STRATEGY PER AUTHORIZED WINDOW, never both.
            #
            # The two-sided engine and the L5-following path share this
            # executor, this envelope and this market. In 1215z they ran
            # together: the L5-following path spent $4.5264 of the $4.65
            # cash envelope on the two fills above, and the two-sided engine
            # then refused `CASH_COMMITMENT:6.99>4.65` -- which was the cash
            # gate working exactly as designed, on an envelope another
            # strategy had already emptied. $4.5264 + a 2.46 short leg at a
            # dollar a share is $6.9864, which is the 6.99 in that refusal.
            # The limit was never the problem and raising it would only have
            # funded the loss.
            #
            # WHICH STRATEGY OWNS THE WINDOW IS NAMED, NOT INFERRED.
            #
            # The first version of this gate said "if the market is bound for
            # the two-sided experiment, two-sided wins". That would have
            # BLOCKED the 1215z fills -- and those fills were the first
            # economically valid completed pair this programme has produced:
            # UP 2.46 at own 0.90 and DOWN 2.46 at own 0.06, a pair cost of
            # exactly 0.96, redeemed 2.46 for +$0.0984 settled. They came
            # from the L5-following path. Letting an unset preference decide
            # which strategy dies is how you lose the one that works.
            #
            # So exclusivity is explicit: `L5_WINDOW_STRATEGY` names the
            # owner of the window. Unset enforces nothing and preserves
            # today's behaviour; TWO_SIDED admits only two-sided orders;
            # L5_FOLLOWING admits only L5-following ones.
            _owner_strategy = str(
                os.getenv("L5_WINDOW_STRATEGY", "")).strip().upper()
            if _owner_strategy == "TWO_SIDED" and not getattr(
                    self, "_probe_leg_pending", None):
                prov.stage("ORDER", {
                    "client_order_id": coid, "submitted": False,
                    "reason": "STRATEGY_EXCLUSIVE_TWO_SIDED",
                    "detail": {"market": market},
                    "strategy": _owner_strategy,
                    "labels": ("L5_WINDOW_STRATEGY names TWO_SIDED as the "
                               "owner of this window, so the L5-following "
                               "path may not submit in it. Two strategies "
                               "sharing one cash envelope is how the "
                               "two-sided engine was starved in 1215z.")})
                return prov.refuse("STRATEGY_EXCLUSIVE_TWO_SIDED", market)
            if _owner_strategy == "L5_FOLLOWING" and getattr(
                    self, "_probe_leg_pending", None):
                prov.stage("ORDER", {
                    "client_order_id": coid, "submitted": False,
                    "reason": "STRATEGY_EXCLUSIVE_L5_FOLLOWING",
                    "detail": {"market": market},
                    "strategy": _owner_strategy,
                    "labels": ("L5_WINDOW_STRATEGY names L5_FOLLOWING as the "
                               "owner of this window, so the two-sided "
                               "engine may not submit in it.")})
                return prov.refuse("STRATEGY_EXCLUSIVE_L5_FOLLOWING", market)

            # THE RISK-REDUCING SAFEGUARD, before any claim is spent. The
            # COMPLETE budget is deliberately looser than OPEN; that is only
            # safe because an order charged to it has to actually reduce
            # |prospective unmatched| and may not cross zero. Evaluated here,
            # at the common boundary, so it binds whichever engine chose the
            # order. A refusal costs nothing: no claim, no ticket, no wire.
            # THE EFFECT CLASS, with no attempt axis: this asks only "does
            # this order reduce unmatched exposure?". The attempt index is
            # not known until the accumulation branch below, which upgrades
            # the charge to REPLACE; the SAFEGUARD must not depend on that,
            # because a re-price that overshoots is exactly as unwanted as a
            # first placement that does.
            _bal_klass = self.submission_class(market, dec, tr)
            _bal = self.balancing_refusal(market, dec, tr, _bal_klass)
            if _bal:
                prov.stage("ORDER", {
                    "submitted": False,
                    "reason": str(_bal.get("reason")),
                    "detail": _bal,
                    "klass": _bal_klass,
                    "labels": ("an order charged to the risk-reducing budget "
                               "must reduce unmatched exposure on the "
                               "PROSPECTIVE book and must not overshoot into "
                               "an equal imbalance on the other side")})
                # TWO NAMED SITES, not one dynamic refusal. The refusal
                # inventory in `l5_intent_gate` is asserted against the
                # number of `prov.refuse` sites in this method, so a site
                # that emits two different reasons would make that guard
                # under-count and the declared review surface stale.
                if str(_bal.get("reason")) == "BALANCING_WOULD_OVERSHOOT":
                    return prov.refuse("BALANCING_WOULD_OVERSHOOT",
                                       str(_bal.get("note"))[:160])
                return prov.refuse("BALANCING_DOES_NOT_REDUCE",
                                   str(_bal.get("note"))[:160])

            # PRE-SEND CROSSING RE-CHECK, against the freshest book, and
            # placed HERE for one reason: it is the last point before the
            # submission claim is consumed.
            #
            # WHAT IT IS FOR. A post-only leg the book has already left is
            # rejected by the venue outright, and the claim it spent is never
            # returned -- "not by a cancellation, a partial or zero fill, a
            # reconnect, a restart, or a rejected or ambiguous
            # acknowledgement". Windows 0030z and 0045z on 2026-10-25 spent 3
            # of 12 submissions on exactly that: ORDER_STATE_REJECTED with
            # cum_qty 0 at 00:39:03, 00:49:41 and 00:50:46. `plan_executable`
            # already computes LEG_WOULD_CROSS, but it judges the plan at
            # PLANNING time; the book moves between then and the wire.
            #
            # IT IS AN EFFICIENCY GUARD, NOT A SAFETY GATE, and the
            # distinction decides every fail direction below. Every risk
            # control has already run and already returned by this line, so
            # this can only ever skip an order that the venue was certain to
            # reject. It therefore FAILS OPEN on anything it cannot establish
            # -- no book, no prices, an unreadable side, a raising helper --
            # because a budget-saving check that could halt trading on a
            # mirror hiccup would be a worse change than the waste it
            # removes. The venue's own post-only check remains the backstop.
            #
            # IT CHANGES NO PRICE, SIZE, DEPTH, TTL OR CUTOFF. It either
            # lets the order through untouched or does not send it.
            _xr = self.presend_cross_refusal(market, intent, tr)
            if _xr is not None:
                prov.stage("PRESEND_CROSS", _xr)
                return prov.refuse("PRESEND_CROSS",
                                   str(_xr.get("reason"))[:120])

            # AND THE OTHER DIRECTION. The guard above declines a leg the
            # book has made too AGGRESSIVE; this declines one it has left
            # too PASSIVE, which is placeable, never fills, and holds a
            # live-order slot for its whole TTL. Same fail-open discipline,
            # and it only ever runs while the round holds nothing -- see
            # `presend_behind_refusal`.
            _br = self.presend_behind_refusal(
                market, intent, tr,
                rnd=getattr(self, "_probe_round_pending", None))
            if _br is not None:
                prov.stage("PRESEND_BEHIND", _br)
                return prov.refuse("PRESEND_BEHIND",
                                   str(_br.get("reason"))[:120])

            # LAST, and for the same reason PRESEND_CROSS is here: the next
            # statement consumes a claim that is never returned. A disarmed
            # transport cannot reach the venue, so spending one on it records
            # a submission that never happened. Positive `armed is False`
            # only; anything else falls through untouched.
            _dr = self.disarmed_refusal()
            if _dr is not None:
                prov.stage("ORDER", {"client_order_id": coid,
                                     "submitted": False,
                                     "reason": "NOT_ARMED",
                                     "claim_consumed": False,
                                     "detail": _dr["labels"],
                                     "intent": {k: str(v)
                                                for k, v in intent.items()}})
                return prov.refuse("NOT_ARMED_PRESEND", "transport disarmed")

            try:
                # ACCUMULATION NEEDS MANY SUBMISSIONS, so it claims a
                # NAMED (market, round, leg) slot rather than loosening the
                # single-submission rule for everything else. Each slot is
                # still one-shot and durable, the coid is still derived and
                # still duplicate-suppressed, and max_live_orders still
                # bounds concurrency.
                leg_pending = getattr(self, "_probe_leg_pending", None)
                rnd_pending = getattr(self, "_probe_round_pending", None)
                if TS.active(market) and leg_pending and rnd_pending is not None:
                    # KEYED ON (market, round, leg), so an intent that is
                    # genuinely new gets its own durable slot while a replay
                    # of THIS intent lands on the same slot and is refused.
                    # WHICH PLACEMENT OF THIS LEG. `acc_leg_attempt` grants a
                    # later attempt only over a leg holding NOTHING with
                    # nothing live, so a filled leg can never be re-placed
                    # and an unverified cancellation cannot buy a retry. None
                    # means refuse: the budget is spent, the leg holds
                    # inventory, an order is live, or a claim was unreadable.
                    att = self.acc_leg_attempt(market, rnd_pending,
                                               leg_pending)
                    if att is None:
                        raise AUTH.Refused(
                            "ACC_NO_ATTEMPT_AVAILABLE",
                            {"market": market, "round": rnd_pending,
                             "leg": leg_pending,
                             "note": "no placement of this leg is permitted: "
                                     "the attempt budget is spent, the leg "
                                     "holds inventory, an order is live, or a "
                                     "claim could not be read"})
                    # TWO AXES, DELIBERATELY SEPARATE. `klass` is which
                    # budget pays -- a re-price is charged to REPLACE
                    # whatever its direction, which is the split the owner
                    # approved. `reducing` is the order's EFFECT, and it is
                    # what exempts a risk-reducing order from the flat
                    # backstop. Collapsing them would mean a re-priced
                    # complement could be refused by a total that OPEN had
                    # already spent, stranding the inventory it created.
                    _klass = self.submission_class(market, dec, tr,
                                                   attempt=att)
                    claim = AUTH.claim_accumulation_leg(
                        market, rnd_pending, leg_pending,
                        dict(payload, market=market), attempt=att,
                        klass=_klass, reducing=(_bal_klass == "COMPLETE"))
                else:
                    _klass = self.submission_class(market, dec, tr)
                    claim = AUTH.claim_submission(
                        market, payload, klass=_klass,
                        reducing=(_bal_klass == "COMPLETE"))
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
        # WHICH ROUND THIS ORDER BELONGS TO, on the durable record, so a
        # round whose orders all end unfilled can later be told apart from
        # one that took exposure. Written with the submission record and
        # read by `probe_round_never_filled`; an order with no round (every
        # non-accumulation path) records nothing.
        try:
            _rp = getattr(self, "_probe_round_pending", None)
            if TS.active(market) and _rp is not None:
                self.store.state.setdefault("probe_round_orders", {}
                    ).setdefault("%s|%d" % (str(market), int(_rp)), []
                    ).append(str(coid))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # unrecorded: the round then reads as charged, never as free
        self.store.record_submit(
            coid, intent,
            session_id=os.getenv("PMUS_Q1_SESSION_ID", "").strip() or None,
            mode=str(tr.get("mode") or ""),
            # WHOSE DECISION THIS WAS, from the chain record that already
            # carries it rather than from a second judgement here. This is
            # what lets `inventory_origin` attribute a completion to the
            # engine that actually opened the position.
            provenance=str(prov.rec.get("provenance") or "") or None,
            action_key=str(prov.rec.get("action_key") or "") or None)
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
            self._probe_round_pending = None
            # PRE-SUBMISSION CALIBRATION CAPTURE, immediately before the
            # wire and after every gate. Observational only: it holds no
            # transport, cannot refuse, cannot alter `intent`, and swallows
            # its own faults. It exists because record_submit persists
            # price and quantity but not the queue our order met, which is
            # the one field a fill model has to be checked against.
            #
            # NOT A GATE. A capture failure does NOT stop this submission --
            # letting a recorder veto the money path is the more dangerous
            # coupling. It instead marks the record unusable, and
            # `precapture_ok` lets a probe decline an uncalibratable order
            # before it ever gets here.
            cal = getattr(self, "fill_calibration", None)
            if cal is not None:
                try:
                    cal.pre_submit(
                        coid, slug=str(intent.get("market")),
                        side=str(intent.get("_side") or intent.get("side")),
                        own_px=intent.get("price"), qty=intent.get("qty"),
                        book=getattr(self, "calib_book", None),
                        ladder=getattr(self, "calib_ladder", None),
                        now=dt.datetime.now(dt.timezone.utc).timestamp())
                except BaseException as exc:                   # noqa: BLE001
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
            # DEFENCE IN DEPTH, AND THE LAST THING BEFORE THE WIRE.
            #
            # Every gate above has already run. This re-reads the position
            # one more time from the authoritative store and refuses an
            # exposure-increasing order that would breach the residual cap,
            # because on 1415z the earlier gate passed exactly such an order
            # and the replay proved its predicate sound -- so the failure was
            # in the state it was handed, which only a restated read can
            # catch. Risk-reducing legs and a genuinely flat book pass
            # untouched; an unreadable position refuses.
            #
            # The order has already been claimed and recorded by this point,
            # so a refusal here terminates it rather than leaving it pending:
            # the claim is spent either way, which is the conservative
            # direction and is what the claim is for.
            _frg = self.final_residual_guard(
                market, dec["side"], tr["qty"], coid=coid)
            prov.note("FINAL_RESIDUAL_GUARD", _frg)
            if not _frg.get("ok"):
                self.store.terminate_order(
                    coid, reason=str(_frg.get("reason")))
                return prov.refuse("SAFETY_OVERLAY",
                                   str(_frg.get("reason")))
            ack = self.transport.submit(intent)
            # THE CALIBRATION RECORD'S VENUE BINDING, and the first of the
            # money path's lifecycle hooks to be wired at all.
            #
            # WHAT WAS WRONG. `pre_submit` above is the ONLY recorder call
            # the money path has ever made. `on_ack`, `on_fill`,
            # `on_settlement` and `on_fees_actual` are implemented and were
            # called from nowhere, so a genuine resting order could never
            # produce a usable record: `terminal_state` is PRESENT only when
            # venue_confirmed, and nothing was ever going to confirm it.
            #
            # WITHOUT THIS BINDING the downstream hooks have nothing to
            # correlate a venue report against, so it is the first one that
            # has to exist. It records the id the venue returned and nothing
            # else; it derives no fill, asserts no terminal state and claims
            # no fee.
            #
            # OBSERVATIONAL, AND STRUCTURALLY UNABLE TO BE OTHERWISE. It runs
            # AFTER the wire, so it cannot affect what was sent; it swallows
            # its own faults, so a recorder bug cannot fail a submission that
            # the venue has already accepted -- which would be the worst
            # possible coupling, losing the order's identity over telemetry.
            try:
                _cal = getattr(self, "fill_calibration", None)
                if _cal is not None:
                    # the ack is a Mapping on this transport and an object on
                    # others, so read both shapes rather than assume one. An
                    # id we cannot find is recorded as None, which the record
                    # then says -- never invented.
                    if isinstance(ack, Mapping):
                        _vid = ack.get("venue_order_id")
                    else:
                        _vid = getattr(ack, "venue_order_id", None)
                    _cal.on_ack(coid, _vid)
            except BaseException as _exc:                      # noqa: BLE001
                if isinstance(_exc, (KeyboardInterrupt, SystemExit)):
                    raise
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


#: HOW MANY TIMES THE CANCEL VERIFICATION MAY BE RE-READ, and how long it
#: waits between attempts. Bounded and tiny by construction: this runs on the
#: deadman path, so a long stall here is itself a hazard.
#:
#: THE 0145z/0200z INCIDENT IS WHY. Ten requotes produced not one clean
#: cancel: SEVEN came back `verify_error: HTTPError` and THREE came back
#: `still_open` with `ORDER_STATE_NEW` and the full `leaves_qty`. The verify
#: was a SINGLE attempt, so one transient error on an idempotent read left
#: the order's fate permanently unknown and marked the whole book uncertain
#: -- which then blocks every subsequent submission. Seven of ten were lost
#: to that, not to anything the venue decided.
#:
#: ONLY THE READ IS RETRIED, NEVER THE CANCEL. `GET /v1/order/{id}` is
#: idempotent, so asking again is free and cannot move anything. Re-issuing
#: `transport.cancel` would not be: by then a replacement may exist on the
#: same slot, and a second cancel could retire the wrong order. The cancel is
#: sent exactly once, as before.
#:
#: A NON-TERMINAL READ IS ALSO RETRIED, for the three `still_open` rows. A
#: venue's order query can lag its own cancel, so an immediate re-read can
#: report ORDER_STATE_NEW on an order that is already gone -- the same
#: read-after-write lag the reference quoter allows 30s of indexing grace
#: for. Retrying can only ever turn a FALSE still_open into a proven
#: cancellation; it can never invent a terminal state, because the verdict is
#: still the venue's own last word. Exhaust the attempts and the order is
#: `still_open` exactly as it is today.
VERIFY_ATTEMPTS = 3
VERIFY_BACKOFF_S = (0.20, 0.40)


def _verify_cancelled(transport: Any, vid: str, *,
                      attempts: int = VERIFY_ATTEMPTS,
                      sleeper: Any = None) -> Dict[str, Any]:
    """Re-read one order id until the venue reports it terminal, or give up.

    Returns {"prog": ..., "attempts": n, "errors": [...], "terminal": bool}.
    `prog` is None only when EVERY attempt raised. Fail-closed is preserved:
    the caller still treats a None `prog` as unverified and a non-terminal
    `prog` as still_open.
    """
    if sleeper is None:
        import time as _t
        sleeper = _t.sleep
    n = max(1, int(attempts))
    errors: List[str] = []
    prog = None
    for i in range(n):
        if i:
            idx = min(i - 1, len(VERIFY_BACKOFF_S) - 1)
            try:
                sleeper(VERIFY_BACKOFF_S[idx])
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
        try:
            prog = order_progress(transport.order(vid))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            errors.append(type(exc).__name__)
            continue
        if prog.get("terminal"):
            return {"prog": prog, "attempts": i + 1, "errors": errors,
                    "terminal": True}
    return {"prog": prog, "attempts": n, "errors": errors,
            "terminal": bool(prog is not None and prog.get("terminal"))}


def _mark_cancel_uncertain(store: Any, why: str, *, kind: str, coid: Any,
                           vid: Any) -> None:
    """Mark the book uncertain, and name the order IF the store can hold it.

    THE MARK IS THE SAFETY CONTROL; THE CAUSE IS A CONVENIENCE. A store that
    does not accept a cause -- an older ledger, a test double -- must still
    be marked, so a refused keyword falls back to the bare mark instead of
    propagating. Losing the cause only means this uncertainty waits for a
    full reconciliation, as every uncertainty did before; losing the MARK
    would let a replacement race an order that may still be resting.
    """
    try:
        store.mark_uncertain(why, cause={"kind": str(kind), "coid": str(coid),
                                         "venue_order_id": str(vid)})
    except TypeError:
        store.mark_uncertain(why)


def deadman(store: InventoryStore, transport: Any, *,
            reason: str, only: Optional[Iterable[str]] = None,
            verify_attempts: int = VERIFY_ATTEMPTS,
            verify_sleeper: Any = None) -> Dict[str, Any]:
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
        #: ALWAYS PRESENT, so an empty list means "we checked and every id
        #: was ours" rather than "this build does not check".
        "refused_not_ours": [],
        "labels": ("cancelled means the VENUE reported a terminal state on "
                   "re-read, never that a cancel call returned. still_open "
                   "and unverified are failures and mark the book uncertain."),
    }
    # `only` SCOPES IT TO NAMED ORDERS, for the per-order age deadline. None
    # keeps the sweep over every resting order, so every existing caller is
    # unchanged. Scoping matters because a per-order deadline must withdraw
    # the OVERDUE leg without also killing a complement placed seconds ago --
    # and it must do so through this function, not a second cancellation
    # path, so it inherits the cancel-by-venue-id, proof-by-re-read,
    # book-the-fill and fail-closed behaviour rather than reimplementing it.
    want = None if only is None else {str(c) for c in only}
    out["scoped_to"] = None if want is None else sorted(want)
    # a snapshot: apply_order_progress mutates live_orders underneath us
    for coid, row in sorted(store.live_orders().items()):
        if want is not None and str(coid) not in want:
            continue
        vid = str(row.get("venue_order_id") or "").strip()
        market = str(row.get("market") or getattr(transport, "market_slug", "")
                     or "")
        side = str(row.get("side") or "")
        #: OWNERSHIP IS CHECKED HERE, BEFORE THE CANCEL, NOT ASSUMED BY
        #: BEING IN THIS LIST. Iterating `live_orders` was itself the
        #: ownership test, and `reconcile_from_venue` writes the venue's
        #: open orders into that same list -- so the sweep cancelled the
        #: owner's hand-placed order and reported it as its own.
        #: Cancelling only reduces OUR exposure; cancelling someone
        #: else's order is not a reduction, it is an unauthorized change
        #: to their position.
        #: A store that cannot answer ownership is read through the row's
        #: own sticky provenance instead of crashing the sweep: an
        #: AttributeError here would abort a cancel we DO own, which is
        #: the opposite of fail-closed. Either route refuses an adopted
        #: row.
        _owns = getattr(store, "owns_venue_order_id", None)
        _not_ours = ((not _owns(vid)) if callable(_owns)
                     else bool(row.get("adopted")))
        if vid and _not_ours:
            out.setdefault("refused_not_ours", []).append(
                {"client_order_id": coid, "venue_order_id": vid,
                 "market": market, "side": side,
                 "why": "NO_SUBMIT_RECORD_FOR_THIS_ID",
                 "adopted": bool(row.get("adopted")),
                 "adopted_first_seen_utc": row.get(
                     "adopted_first_seen_utc")})
            continue
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
            # the ORDER's market, not the transport's current one: an
            # orphan sweep runs after roll has moved on.
            transport.cancel(vid, market_slug=market)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # NOT a failure yet: a cancel can raise on an order the venue has
            # already retired. The re-read decides.
            ack_error = type(exc).__name__
        ver = _verify_cancelled(transport, vid, attempts=verify_attempts,
                                sleeper=verify_sleeper)
        prog = ver["prog"]
        if prog is None:
            # EVERY attempt raised. Unchanged outcome, but the record now
            # says how hard we tried rather than implying one try was all.
            out["unverified"].append({"client_order_id": coid,
                                      "venue_order_id": vid,
                                      "market": market,
                                      "cancel_error": ack_error,
                                      "verify_error": (ver["errors"] or [None])[0],
                                      "verify_attempts": ver["attempts"],
                                      "verify_errors": list(ver["errors"])})
            _mark_cancel_uncertain(
                store,
                "deadman could not verify the cancel of %s (%s, %d attempts)"
                % (vid, (ver["errors"] or ["?"])[0], ver["attempts"]),
                kind="CANCEL_UNVERIFIED", coid=coid, vid=vid)
            continue
        res = store.apply_order_progress(coid, prog, market=market, side=side)
        booked = D(str(res.get("filled_delta") or res.get("delta") or 0))
        if booked > 0:
            out["filled"].append({"client_order_id": coid,
                                  "venue_order_id": vid, "market": market,
                                  "side": side, "qty": str(booked),
                                  "avg_px": str(prog.get("avg_px")),
                                  "commission": (
                                      None if prog.get("commission") is None
                                      else str(prog.get("commission"))),
                                  "labels": ("filled before the cancel took; "
                                             "booked with its cost basis")})
        rec = {"client_order_id": coid, "venue_order_id": vid,
               "market": market, "side": side,
               "venue_state": prog.get("state"),
               "cum_qty": str(prog.get("cum_qty")),
               "leaves_qty": str(prog.get("leaves_qty")),
               "cancel_error": ack_error,
               "verify_attempts": ver["attempts"],
               "verify_errors": list(ver["errors"])}
        if prog.get("terminal"):
            out["cancelled"].append(rec)
        else:
            # The order is STILL WORKING at the venue. This is the one state
            # that must never read as success: an unmanaged resting order is
            # exactly what a deadman exists to prevent.
            out["still_open"].append(rec)
            _mark_cancel_uncertain(
                store,
                "deadman cancelled %s but the venue still reports it live"
                % vid,
                kind="CANCEL_STILL_OPEN", coid=coid, vid=vid)
        if ack_error:
            out["errors"].append({"client_order_id": coid,
                                  "venue_order_id": vid, "error": ack_error,
                                  "resolved_by_reread": bool(
                                      prog.get("terminal"))})
    return out
