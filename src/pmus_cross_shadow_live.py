"""SHADOW ONLY: the prospective bounded complement-cross, measured live.

WHAT THIS IS FOR, STATED AGAINST ITSELF. The retrospective pre-registered
experiment (spec frozen at 135f5d79, run at 66548a5e) returned ZERO
supported crosses over 260 real COMPLETE decisions in 8 markets -- and not
because the prices failed. NO ROW EVER REACHED THE PRICE TEST. First stop
at T0 was:

    no authenticated quote age recorded   146   56.2%
    observed ask depth not recorded        48   18.5%
    authenticated quote STALE (>5s)        65   25.0%
    malformed evidence                      1    0.4%

So the standing result on executable crossing is UNKNOWN, and this module
exists to make it knowable -- NOT to make it favourable. The adverse-
selection evidence that IS available leans AGAINST crossing: among the
cohort that passed everything except depth, waiting produced a CHEAPER
complement more often than a dearer one inside a minute (+10s n=48, 12.5%
cheaper vs 0% dearer, mean -0.011; +30s n=20, 50% vs 10%, mean -0.1028;
+60s n=14, 50% vs 42.9%, mean +0.034). At +30s the mean move was about
-10 cents. That is a NEGATIVE prior and nothing here may quietly reverse
it: a SUPPORTED verdict means "this cross was executable and net-positive
at that instant", never "crossing is a good policy".

NO ORDER CAN BE PLACED FROM THIS MODULE, BY CONSTRUCTION -- not by a flag
that could be flipped. It imports nothing from `src`, opens no socket,
holds no transport, no credentials and no client, and has no function that
submits, cancels, replaces or reprices anything. Every result it returns
carries ``would_submit: False`` and SHADOW_ONLY provenance. It is stateless:
it caches nothing, so an older depth observation can never stand in for a
missing one.

WHAT IT MAY NOT TOUCH. The LIVE hard $1.00 pair ceiling does not move, and
this module does not implement it -- it only records that it is unchanged.
The 0.97 prospective ceiling below belongs to the shadow candidate ALONE.
Maker-only live execution, sizing, the residual cap, concurrency, the spend
caps, admission, ranking, the band and the daily stop are all elsewhere and
all unchanged.

WHY 0.97 IS THE ONLY PROSPECTIVE ARM. Source-confirmed PMUS fees (below) make
crossing cost roughly 1.6-2.3 cents per contract at our clips once BOTH the
taker fee AND the forgone maker rebate are counted, putting break-even pair
cost at about 0.977-0.988. At a representative clip of ~1.72 contracts and
p~=0.50, a 0.97 pair's gross edge is ~$0.0516 against ~$0.03 of taker fee and
~$0.01 of forgone rebate -- fees eat ~77% of it. 0.99 and 1.00 are therefore
economically dead for this experiment and 0.98 is too marginal to carry as a
promotion arm, so none of them exists here. The retrospective module keeps
its four frozen arms because its spec was frozen before any outcome was seen;
this prospective one carries 0.97 and nothing else.

THE DEPTH MAPPING IS A PROPERTY OF THE ORDER'S SIDE (a previous verbal
statement of it was BACKWARDS):

    buys UP   -> ask_qty            holding DOWN -> COMPLETE buys UP   -> ask_qty
    buys DOWN -> bid_qty            holding UP   -> COMPLETE buys DOWN -> bid_qty

Selling one NO at q pays YES:+q, NO:q-1; buying one YES at 1-q pays YES:q,
NO:q-1. They are the same instrument, so the best NO ask IS 1 - best YES bid
and the quantity available there is the quantity resting AT THE YES BID. The
recorder quotes YES, so a DOWN buy reads the BID side -- for its price and
for its depth alike, which is why this module derives both from one side and
never mixes them.

EVIDENCE FAILS CLOSED, ALWAYS. Missing depth is UNKNOWN, malformed depth is
UNKNOWN, a stale quote is STALE, and none of them is ever converted into a
hypothetical fill. Zero is not a substitute for unknown, infinity is not a
substitute for unknown, depth is never interpolated, never invented from a
price, and never carried over from an earlier observation.

FEES ARE SOURCE-CONFIRMED, and the rounding is the point at our size:

    docs.polymarket.us/fees, fetched 2026-09-13, effective 12 AM ET
    2026-07-01, exchange-wide:
      taker   Fee = 0.06 * C * p * (1-p),  max $1.50 at p = 0.50
      maker   same structure with -0.0125, max rebate -$0.31
      "All fees and rebates are rounded to the nearest $0.01 using
       banker's rounding (round half to even)."
      reduced taker tiers begin at $250,000 prior-calendar-month taker
      volume; we are orders of magnitude below the first tier, so the base
      0.06 applies and no tier is assumed.

These constants are duplicated from src/l5_cross_shadow.py rather than
imported: that module is a FROZEN pre-registered artifact carrying four
retrospective arms, and the live probe image must not gain a path to arms
this experiment excluded. A regression asserts the two agree numerically on
a grid, so they cannot drift.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Mapping, Optional

# --------------------------------------------------------------- identity
SHADOW_ONLY = True
WOULD_SUBMIT = False                       # not a setting; an invariant
PROVENANCE = "SHADOW_ONLY:pmus_cross_shadow_live"
OBSERVATION_SCHEMA = "pmus_cross_shadow_live/observation/v1"
EVALUATION_SCHEMA = "pmus_cross_shadow_live/evaluation/v1"
LABELS = ("SHADOW ONLY -- observational. No order is submitted, cancelled, "
          "repriced or replaced from this module; no inventory, ledger, risk "
          "state or live price is read back from it. Shadow P&L is "
          "THEORETICAL and is never realized.")

# ------------------------------------------------- the one prospective arm
PROSPECTIVE_THRESHOLD = D("0.97")
PROSPECTIVE_THRESHOLDS = (PROSPECTIVE_THRESHOLD,)
# Named so the omission is legible and cannot be read as an oversight.
# NOT arms: nothing in this module promotes, reports or evaluates them.
EXCLUDED_PROSPECTIVE_THRESHOLDS = {
    "0.98": "break-even is ~0.977-0.988 after taker fee AND forgone maker "
            "rebate; too marginal to carry as a promotion arm",
    "0.99": "economically dead: confirmed costs exceed the gross edge",
    "1.00": "economically dead: confirmed costs exceed the gross edge",
}
# The LIVE ceiling. This module does not enforce it and cannot move it; the
# value is carried so a shadow row states on its face that it did not move.
LIVE_PAIR_CEILING = D("1.00")
MAX_QUOTE_AGE_S = 5.0

# ------------------------------------------------ confirmed fee schedule
#: CORRECTED 2026-11-03 against the VENUE'S OWN RECORDS. Every retained
#: BTC15m market from 2026-09-30 to 2026-11-03 carries `feeCoefficient`
#: 0.0695 -- queried individually by slug, 11 dates spanning the claimed
#: 2026-10-19 change, with no step at it. The 0.06 here came from a
#: docs.polymarket.us/fees read on 2026-09-13, which predates the gateway's
#: market-retention floor (~2026-09-30) and therefore cannot be re-verified.
#:
#: DIRECTION OF THE ERROR: 0.06 UNDERSTATES the taker charge by ~16%, so
#: every crossing cost computed with it was optimistic. All of our own live
#: trade dates (2026-10-11 .. 2026-11-02) fall inside the verified span, so
#: one coefficient covers them and no per-date split is warranted.
TAKER_THETA = D("0.0695")
MAKER_THETA = D("0.0125")                  # credited to us; a rebate
TAKER_MAX = D("1.50")
MAKER_REBATE_MAX = D("0.31")
FEE_SOURCE = ("taker theta from the venue's own per-market feeCoefficient, "
              "gateway.polymarket.us/v1/market/slug/<slug>, verified constant at "
              "0.0695 across 2026-09-30..2026-11-03; maker theta from "
              "docs.polymarket.us/fees fetched 2026-09-13")
FEE_STATUS_CONFIRMED = "CONFIRMED"

# ----------------------------------------------------------- depth status
DEPTH_OBSERVED = "OBSERVED"
DEPTH_UNKNOWN_NO_BOOK = "UNKNOWN_NO_BOOK"
DEPTH_UNKNOWN_NOT_RECORDED = "UNKNOWN_NOT_RECORDED"
DEPTH_UNKNOWN_MALFORMED = "UNKNOWN_MALFORMED"
DEPTH_UNKNOWN_QUOTE_AGE = "UNKNOWN_QUOTE_AGE"
DEPTH_STALE = "STALE"
#: every status except OBSERVED yields depth None and can never be a fill
DEPTH_NOT_EXECUTABLE = (DEPTH_UNKNOWN_NO_BOOK, DEPTH_UNKNOWN_NOT_RECORDED,
                        DEPTH_UNKNOWN_MALFORMED, DEPTH_UNKNOWN_QUOTE_AGE,
                        DEPTH_STALE)

# --------------------------------------------------------- verdict labels
SUPPORTED = "SUPPORTED_COUNTERFACTUAL_CROSS"
REFUSED = "REFUSED_BY_A_GATE"
UNKNOWN_EXEC = "UNKNOWN_EXECUTABILITY"

# ------------------------------------------------------- capture allowlist
#: ALLOWLIST, never a denylist. Only these keys are read off a recorder row;
#: anything else the recorder happens to serve -- now or in future -- is not
#: copied, not logged and not persisted. Credentials cannot reach a shadow
#: row because no key that could hold one is on this list.
CAPTURE_ALLOWLIST = ("slug", "bid", "ask", "bid_qty", "ask_qty",
                     "both_sides_served")
ONE = D("1")


# =========================================================== depth mapping
def executable_depth_field(buys_up: bool) -> str:
    """Which recorder quantity an order of this side actually executes on.

    BUY UP -> ask_qty. BUY DOWN -> bid_qty. See the module docstring for the
    derivation; the previous verbal rule was backwards and this function is
    the only place the mapping is expressed.
    """
    return "ask_qty" if buys_up else "bid_qty"


def executable_price_field(buys_up: bool) -> str:
    """The side of the book the executable price comes from -- the SAME side
    the depth comes from. Paired here so the two cannot drift apart."""
    return "ask" if buys_up else "bid"


def complete_buys_up(held_residual: Any) -> Optional[bool]:
    """COMPLETE buys the DEFICIT side. Holding DOWN (residual < 0) completes
    by buying UP; holding UP completes by buying DOWN. A flat residual has no
    complement to buy and returns None rather than guessing a side."""
    try:
        r = D(str(held_residual))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if r == 0:
        return None
    return r < 0


def _decimal(value: Any) -> Optional[D]:
    """A finite Decimal, or None. Never a float path: the venue serves
    fixed-point strings and they stay exact. Sign is NOT checked here --
    each caller decides what a negative value means for its own field."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        d = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not d.is_finite():
        return None
    return d


def _age_seconds(value: Any) -> Optional[float]:
    """Seconds, or None. Unparseable, NaN and NEGATIVE ages are all None:
    a quote cannot be younger than zero, and an age we cannot read is never
    a small one."""
    if value is None or isinstance(value, bool):
        return None
    try:
        age = float(value)
    except (TypeError, ValueError):
        return None
    if age != age or age < 0:                      # NaN, or impossible
        return None
    return age


def executable_depth(book: Optional[Mapping[str, Any]], buys_up: bool,
                     quote_age_s: Any = None,
                     max_age_s: float = MAX_QUOTE_AGE_S) -> tuple:
    """The depth an order of this side could actually have executed against.

    Returns ``(depth, status)``. **The depth is None for every status except
    OBSERVED** -- missing, malformed, ageless and stale evidence all fail
    closed, and none of them may ever become a hypothetical fill.

    Precedence, most specific fact about the datum first:

        no book at all          -> UNKNOWN_NO_BOOK
        field absent / null     -> UNKNOWN_NOT_RECORDED
        field unparseable / < 0 -> UNKNOWN_MALFORMED
        age unknown / negative  -> UNKNOWN_QUOTE_AGE
        quote older than max    -> STALE
        otherwise               -> OBSERVED

    A depth of exactly 0 is a real observation ("the far side was empty")
    and is reported as OBSERVED 0 -- it fails the size test honestly rather
    than masquerading as missing evidence.
    """
    if not isinstance(book, Mapping):
        return None, DEPTH_UNKNOWN_NO_BOOK
    field = executable_depth_field(buys_up)
    if field not in book or book.get(field) is None:
        return None, DEPTH_UNKNOWN_NOT_RECORDED
    depth = _decimal(book.get(field))
    if depth is None or depth < 0:
        return None, DEPTH_UNKNOWN_MALFORMED
    age = _age_seconds(quote_age_s)
    if age is None:
        return None, DEPTH_UNKNOWN_QUOTE_AGE
    if age > float(max_age_s):
        return None, DEPTH_STALE
    return depth, DEPTH_OBSERVED


def executable_complement_price(book: Optional[Mapping[str, Any]],
                                buys_up: bool) -> tuple:
    """What buying the complement AT THE TOUCH would cost, from the same side
    of the book the depth comes from.

    Returns ``(price, why_unknown)``. Buying UP pays the YES ask. Buying DOWN
    pays ``1 - best YES bid``, because selling one NO at q and buying one YES
    at 1-q are the same instrument. A price outside (0, 1) is malformed
    evidence, not a bargain.
    """
    if not isinstance(book, Mapping):
        return None, "no_authenticated_book"
    field = executable_price_field(buys_up)
    if field not in book or book.get(field) is None:
        return None, f"{field}_not_recorded"
    raw = _decimal(book.get(field))
    if raw is None:
        return None, f"{field}_malformed"
    px = raw if buys_up else ONE - raw
    if px <= 0 or px >= ONE:
        return None, f"{field}_outside_venue_price_range"
    return px, None


# ================================================================== timing
def parse_utc(value: Any) -> Optional[datetime]:
    """An ISO-8601 UTC timestamp in the recorder's own convention
    (``datetime.now(timezone.utc).isoformat()``), tolerating the trailing
    ``Z`` form. Anything else is None -- never a guess."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def probe_transport_delta_s(status_utc: Any,
                            probe_receipt_utc: Any) -> Optional[float]:
    """How long the status document took to reach the probe, by the two
    timestamps. None when either is unreadable."""
    a, b = parse_utc(status_utc), parse_utc(probe_receipt_utc)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


def age_at_probe_receipt_s(authenticated_age_s: Any, status_utc: Any,
                           probe_receipt_utc: Any) -> Optional[float]:
    """The quote's TRUE age when the probe received it.

    ``authenticated_age_s`` is the recorder's own age AT ``status_utc``; by
    the time the probe reads the document the quote is older by the transport
    delay. This ADDS that delay and is purely observational -- it never
    replaces ``authenticated_age_s``, whose value and meaning the existing
    execution gates depend on and which this module does not touch.

    A NEGATIVE delay is clock skew between two machines, not a quote getting
    younger. Crediting it would make a stale quote look fresh, which is the
    exact failure this field exists to prevent, so it is floored at zero and
    the raw delta is reported separately. None in, None out: an age we cannot
    compute is never a small one.
    """
    base = authenticated_age_s
    if base is None or isinstance(base, bool):
        return None
    try:
        age = float(base)
    except (TypeError, ValueError):
        return None
    if age != age:
        return None
    delta = probe_transport_delta_s(status_utc, probe_receipt_utc)
    if delta is None:
        return None
    return round(age + max(delta, 0.0), 6)


# =============================================================== economics
def _cents(x: D) -> D:
    """Banker's rounding to the cent, as the venue states it."""
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_EVEN)


def taker_fee(contracts: Any, price: Any) -> D:
    """The CHARGE, not the formula value, rounded at the real clip size."""
    q, p = D(str(contracts)), D(str(price))
    return min(_cents(TAKER_THETA * q * p * (ONE - p)), TAKER_MAX)


def maker_rebate(contracts: Any, price: Any) -> D:
    """What a maker fill WOULD have been credited -- so a cross can be
    charged for giving it up, which is a real cost of crossing."""
    q, p = D(str(contracts)), D(str(price))
    return min(_cents(MAKER_THETA * q * p * (ONE - p)), MAKER_REBATE_MAX)


def cross_economics(*, contracts: Any, taker_price: Any,
                    held_avg_cost: Any, maker_price: Any = None) -> dict:
    """One prospective cross, costed after BOTH confirmed costs.

    A matched pair pays exactly $1, so the locked gross is ``(1 - pair_cost)``
    per contract. From it come the confirmed TAKER FEE and the MAKER REBATE
    THE CROSS FORGOES -- the maker leg we would have been paid for, and did
    not get, precisely because we crossed. Net is after both. Nothing here is
    realized P&L and nothing here is booked anywhere.
    """
    q = D(str(contracts))
    px = D(str(taker_price))
    avg = D(str(held_avg_cost))
    pair = avg + px
    gross = q * (ONE - pair)
    fee = taker_fee(q, px)
    forgone = (maker_rebate(q, D(str(maker_price)))
               if maker_price is not None else D("0"))
    net = gross - fee - forgone
    # The FLAG is read off the number the row actually reports, not off an
    # unrounded value a reader cannot see on the row. A net that vanishes at
    # the reported precision is not a net gain, and calling it one would
    # promote a cross on arithmetic nobody can check.
    net_shown = net.quantize(D("0.0001"))
    raw_fee = TAKER_THETA * q * px * (ONE - px)
    return {
        "contracts": str(q),
        "taker_price": str(px),
        "held_avg_cost": str(avg),
        "maker_price_forgone": (str(D(str(maker_price)))
                                if maker_price is not None else None),
        "pair_cost": str(pair.quantize(D("0.0001"))),
        "gross_locked_pair_pnl": str(gross.quantize(D("0.0001"))),
        "confirmed_taker_fee": str(fee),
        "maker_rebate_forgone": str(forgone),
        "net_locked_pair_pnl": str(net_shown),
        "net_positive_after_confirmed_costs": bool(net_shown > 0),
        "fee_status": FEE_STATUS_CONFIRMED,
        "fee_source": FEE_SOURCE,
        "fee_formula_value": str(raw_fee.quantize(D("0.000001"))),
        "fee_quantisation_premium": str(
            (fee - raw_fee).quantize(D("0.000001"))),
        "theoretical": True,
        "labels": "THEORETICAL shadow economics; never realized, never booked",
    }


# ============================================================ observation
def observational_capture(*, row: Optional[Mapping[str, Any]],
                          book_source: Any, status_utc: Any,
                          probe_receipt_utc: Any, authenticated_age_s: Any,
                          buys_up: bool) -> dict:
    """The durable observational row, built from an ALLOWLIST.

    Purely additive and purely observational: it reads fields the recorder
    status document ALREADY carries on a row the probe has ALREADY fetched,
    so it costs no extra recorder request, no extra load and no new polling.
    ``authenticated_age_s`` is COPIED, never recomputed -- the execution
    gates that consume it must see exactly what they saw before.

    Only ``CAPTURE_ALLOWLIST`` keys are taken from ``row``, verbatim and
    unmutated. Arbitrary recorder payload cannot enter a logged or persisted
    row, and no key that could carry a credential is on the list.
    """
    out: dict = {"schema": OBSERVATION_SCHEMA, "provenance": PROVENANCE,
                 "shadow_only": True, "would_submit": WOULD_SUBMIT}
    for key in CAPTURE_ALLOWLIST:
        out[key] = row.get(key) if isinstance(row, Mapping) else None
    out["book_source"] = book_source if isinstance(book_source, str) else None
    out["status_utc"] = status_utc if isinstance(status_utc, str) else None
    out["probe_receipt_utc"] = (probe_receipt_utc
                                if isinstance(probe_receipt_utc, str) else None)
    # the EXISTING field, copied through untouched, beside the NEW one
    out["authenticated_age_s"] = authenticated_age_s
    out["probe_transport_delta_s"] = probe_transport_delta_s(
        status_utc, probe_receipt_utc)
    out["age_at_probe_receipt_s"] = age_at_probe_receipt_s(
        authenticated_age_s, status_utc, probe_receipt_utc)
    out["order_buys"] = "UP" if buys_up else "DOWN"
    out["executable_depth_field"] = executable_depth_field(buys_up)
    out["executable_price_field"] = executable_price_field(buys_up)
    depth, status = executable_depth(row, buys_up,
                                     quote_age_s=out["age_at_probe_receipt_s"])
    out["executable_depth"] = str(depth) if depth is not None else None
    out["executable_depth_status"] = status
    price, why = executable_complement_price(row, buys_up)
    out["executable_complement_price"] = str(price) if price is not None else None
    out["executable_complement_price_unknown"] = why
    out["labels"] = LABELS
    return out


# ============================================================== evaluation
def evaluate(*, book: Optional[Mapping[str, Any]], buys_up: bool,
             intended_qty: Any, held_avg_cost: Any, held_residual: Any,
             model_relation: str, maker_price: Any = None,
             quote_age_s: Any = None,
             risk_gates_pass: bool = True,
             book_source: Any = None, status_utc: Any = None,
             probe_receipt_utc: Any = None,
             authenticated_age_s: Any = None) -> dict:
    """Would a bounded <=0.97 taker COMPLETE have been executable here, and
    net-positive after BOTH confirmed costs?

    SHADOW ONLY. It observes and reports. It submits nothing, cancels
    nothing, reprices nothing, touches no inventory, no ledger, no risk state
    and no live price, and it returns ``would_submit: False`` on every path
    including the SUPPORTED one.

    THE CEILING IS NOT A PARAMETER. There is exactly one prospective arm and
    this function takes no threshold argument, so no caller -- not a future
    one either -- can evaluate a 0.98, 0.99 or 1.00 cross through it. Those
    arms are economically dead at our confirmed costs (see the module
    docstring) and their absence is structural, not a default to override.

    Gates run in order and the FIRST thing that stopped the row is reported,
    so a corpus can be read the way the pre-registered one was. "We could not
    tell" (UNKNOWN_EXECUTABILITY) and "we would not have" (REFUSED_BY_A_GATE)
    stay separate facts and neither is ever a fill.
    """
    age = _age_seconds(quote_age_s)
    if age is None:
        age = _age_seconds(age_at_probe_receipt_s(
            authenticated_age_s, status_utc, probe_receipt_utc))
    depth, depth_status = executable_depth(book, buys_up, quote_age_s=age)
    price, price_why = executable_complement_price(book, buys_up)
    want = _decimal(intended_qty)
    avg = _decimal(held_avg_cost)
    out: dict = {
        "schema": EVALUATION_SCHEMA,
        "provenance": PROVENANCE,
        "shadow_only": True,
        "would_submit": WOULD_SUBMIT,
        "prospective_pair_cost_max": str(PROSPECTIVE_THRESHOLD),
        "prospective_arms": [str(a) for a in PROSPECTIVE_THRESHOLDS],
        "excluded_prospective_arms": sorted(EXCLUDED_PROSPECTIVE_THRESHOLDS),
        "live_pair_ceiling_unchanged": str(LIVE_PAIR_CEILING),
        "max_quote_age_s": MAX_QUOTE_AGE_S,
        "model_relation": model_relation,
        "order_buys": "UP" if buys_up else "DOWN",
        "executable_depth_field": executable_depth_field(buys_up),
        "executable_price_field": executable_price_field(buys_up),
        "executable_depth": str(depth) if depth is not None else None,
        "executable_depth_status": depth_status,
        "executable_complement_price": (str(price) if price is not None
                                        else None),
        "intended_qty": str(want) if want is not None else None,
        "quote_age_s": age,
        "book_source": book_source if isinstance(book_source, str) else None,
        "labels": LABELS,
    }

    def stop(verdict: str, reason: str, **extra: Any) -> dict:
        out.update(extra)
        out["verdict"] = verdict
        out["first_stop"] = reason
        out["would_submit"] = WOULD_SUBMIT          # on every path, again
        return out

    if not isinstance(book, Mapping):
        return stop(UNKNOWN_EXEC, "no_authenticated_book")
    if model_relation != "complete":
        return stop(REFUSED, "model_did_not_select_complete")
    if complete_buys_up(held_residual) is None:
        return stop(REFUSED, "no_held_residual")
    if avg is None:
        return stop(UNKNOWN_EXEC, "held_average_cost_unknown")
    if want is None or want <= 0:
        return stop(UNKNOWN_EXEC, "intended_quantity_unknown")
    if age is None:
        return stop(UNKNOWN_EXEC, "no_authenticated_quote_age")
    if age > MAX_QUOTE_AGE_S:
        return stop(REFUSED, f"quote_age_{age}s_over_{MAX_QUOTE_AGE_S}s")
    if price is None:
        return stop(UNKNOWN_EXEC,
                    f"executable_complement_price_unknown:{price_why}")
    if depth_status == DEPTH_STALE:
        return stop(REFUSED, "authenticated_quote_stale")
    if depth is None:
        return stop(UNKNOWN_EXEC, f"executable_depth:{depth_status}")
    if depth < want:
        return stop(REFUSED, f"depth_{depth}_below_intended_{want}")
    econ = cross_economics(contracts=want, taker_price=price,
                           held_avg_cost=avg, maker_price=maker_price)
    out["economics"] = econ
    pair = D(econ["pair_cost"])
    if pair > PROSPECTIVE_THRESHOLD:
        return stop(REFUSED,
                    f"pair_{pair}_over_prospective_{PROSPECTIVE_THRESHOLD}")
    if pair > LIVE_PAIR_CEILING:                    # unreachable; asserted
        return stop(REFUSED, "pair_over_the_live_1_00_ceiling")
    if not risk_gates_pass:
        return stop(REFUSED, "existing_risk_spend_or_daily_loss_gate")
    if not econ["net_positive_after_confirmed_costs"]:
        return stop(REFUSED, "net_not_positive_after_confirmed_costs")
    return stop(SUPPORTED,
                "executable_and_net_positive_after_confirmed_costs")
