"""WHY A PROFITABLE PAIR DID NOT GET COMPLETED, classified and named.

OWNER 2026-11-02: "instrument every missed profitable pairing opportunity
and classify the actual blocker: order concurrency, requote churn, pair
ceiling, residual cap, budget, liquidity, or another named refusal. We need
to determine how to increase completed profitable pairs, not simply increase
capital."

THE GAP THIS CLOSES, which is the DECISION layer and not the placement one.
`L5_BOOK_COMPLETION_RESULT` already pairs a COMPLETE decision with what the
placement path did, so a decision that never became an order is visible. But
`maybe_book_completion` returns on a REFUSED decision long before that:

    self.stats["book_completion:%s" % rec.get("reason")] += 1
    if not rec.get("ok"):
        return rec

-- so a refusal lands in an AGGREGATE COUNTER and nowhere else. That is the
same blindness the placement fix was written to remove, one layer up: across
the 2026-11-01 campaign the candidate causes were "only ever visible as
aggregate ages in the heartbeat", and a histogram of reasons cannot say
whether the thing refused was worth having.

THE DISTINCTION THAT MAKES THIS EVIDENCE RATHER THAN A HISTOGRAM. Most
refusals are CORRECT and are not missed opportunities at all: `NO_INVENTORY`
means there was nothing to complete, `ALREADY_MATCHED` means it already was.
Counting those as missed profit would manufacture a problem to solve. So
every record carries a separate verdict on whether a PROFITABLE pair was
actually given up.

AND IT IS A TRI-STATE, NEVER A BOOLEAN. A refusal that fires before the
complement is priced carries no pair cost, and "we do not know whether that
pair was profitable" is not "it was not profitable" -- they differ by the
whole question being asked. `UNKNOWN` is reported as itself, in the same way
`holding_ev_status` is, and is never collapsed into `NO`.

PRICE SEMANTICS. Costs here are OWN prices: `own(UP) = wire`, `own(DOWN) =
1 - wire`. A matched UP/DOWN pair redeems exactly $1.00, so a pair whose own
costs sum below $1.00 is locked economics. The $1.00 line answers "was this
worth completing at all"; the separate entry ceiling (0.96 where the venue
implementation uses it) answers "would our own discipline have taken it",
and both are reported because they are different questions.

OBSERVABILITY ONLY. Pure, no I/O, no clock, holds no limit and decides
nothing. It cannot place, reprice or withdraw an order, and it cannot turn a
refusal into a submission or a submission into a refusal.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, Mapping, Optional

#: a matched UP/DOWN pair redeems exactly this at settlement
REDEMPTION = D("1.00")

#: THE BLOCKER CLASSES, which are the owner's own taxonomy plus the two
#: honest additions the refusal vocabulary forces.
#:
#: NOT_AN_OPPORTUNITY exists because most refusals are correct and lumping
#: them with real blockers would overstate the problem. DATA_QUALITY exists
#: because a stale or gapped book is neither a liquidity shortage nor a
#: limit: nothing was offered and nothing was refused on its merits.
ORDER_CONCURRENCY = "ORDER_CONCURRENCY"
REQUOTE_CHURN = "REQUOTE_CHURN"
PAIR_CEILING = "PAIR_CEILING"
RESIDUAL_CAP = "RESIDUAL_CAP"
BUDGET = "BUDGET"
LIQUIDITY = "LIQUIDITY"
DATA_QUALITY = "DATA_QUALITY"
NOT_AN_OPPORTUNITY = "NOT_AN_OPPORTUNITY"
OTHER = "OTHER"

#: THE TWO DEFINITIONS, KEPT SIDE BY SIDE ON PURPOSE.
#:
#: v1 (`missed_profitable_pair`) is the ORIGINAL field and its meaning is
#: FROZEN, because records carrying it are already on the tape and in
#: committed artifacts. It asks: was the pair priced in this record below
#: $1.00? For a DECISION-stage record that is the right question -- the pair
#: priced there is the actual completion of inventory actually held.
#:
#: v2 (`missed_profitable_pair_in_state`) asks the question the OPENING
#: stage needs: was a pair available TO US, in the inventory state we were
#: actually in? Two things make v1 wrong there and neither is a rounding
#: detail.
#:
#:   THE FIGURE IS A CONSTANT. Both opening legs quote `d` ticks behind
#:   their own touch, so pair = 1 - spread - 2*d*t, which is 0.95 on every
#:   one-tick book at any price level. v1 reads that as a 5c opportunity on
#:   every tick of every window.
#:
#:   AND IT IS THE WRONG QUANTITY. `PAIR_COST_ABOVE_CEILING` comes from
#:   `pair_cost_refusal`, which returns None unless the order would create
#:   NEW MATCHED QUANTITY -- so it cannot fire on a genuine flat first leg
#:   at all. When it fires, the refused quantity is
#:   `eff = cost_after / matched_after`: the effective pair over the basis
#:   ALREADY PAID, not the fresh two-leg pair the book is showing. At the
#:   2026-11-02 2230z window those differed by ten cents -- a recorded
#:   0.9400 "missed 6c pair" against an effective 1.04 that was correctly
#:   refused.
#:
#: So v2 downgrades to N/A when unmatched inventory was held, and names why.
#: v1 is left exactly as it was. A reader comparing old and new records can
#: tell which definition produced which verdict from `definitions`.
DEF_V1 = "pair_below_redemption_regardless_of_inventory"
DEF_V2 = "pair_available_in_the_inventory_state_we_were_actually_in"

UNMATCHED_HELD = "UNMATCHED_INVENTORY_HELD"
FRESH_PAIR_IS_LEVEL_INVARIANT = "FRESH_OPENING_PAIR_IS_LEVEL_INVARIANT"

#: WHERE THE NUMBER CAME FROM, which item 4 of the owner's instruction turns
#: into a first-class field: an authenticated websocket book and a
#: venue-confirmed basis are evidence a fill could have happened at that
#: price; a sparse or lagged REST observation is not. The 2230z REST tape
#: read twelve cents away from the authenticated book in the same second, so
#: this is not a pedantic distinction.
EV_AUTHENTICATED = "AUTHENTICATED_WS"
EV_REST_SPARSE = "REST_SPARSE_OR_LAGGED"
EV_UNKNOWN = "EVIDENCE_BASIS_UNKNOWN"

#: an authenticated book older than this is reported as lagged rather than
#: authenticated, because the source only helps while it is current.
EV_FRESH_MAX_AGE_S = 2.0

#: exact reasons, matched before any substring rule. `decide`'s own
#: vocabulary, plus the two the runner adds after `decide` returns.
_EXACT: Dict[str, str] = {
    # the runner's own post-decision refusals
    "ORDER_ALREADY_RESTING": ORDER_CONCURRENCY,
    "MAX_LIVE_ORDERS": ORDER_CONCURRENCY,
    # the duplicate hedge, refused per-market rather than account-wide, and
    # the reservation that makes a second hedge unnecessary. Both are
    # concurrency and neither is a missed opportunity: the hedge they would
    # have duplicated is already working at the venue.
    "HEDGE_ALREADY_RESTING": ORDER_CONCURRENCY,
    "ALREADY_RESERVED": ORDER_CONCURRENCY,
    # idempotence and cadence: the quote is already good, or too soon
    "ALREADY_RESTING_AT_OR_BETTER": REQUOTE_CHURN,
    "DECIDE_COOLDOWN": REQUOTE_CHURN,
    # pair economics refused on their merits
    "PAIR_ABOVE_REDEMPTION": PAIR_CEILING,
    # the per-campaign submission backstop
    "NO_SUBMISSIONS_LEFT": BUDGET,
    # nothing was offered at a usable price
    "LEG_UNUSABLE": LIQUIDITY,
    "NO_FIRST_LEG_PRICE": LIQUIDITY,
    "WOULD_CROSS": LIQUIDITY,
    "BOOK_UNUSABLE": LIQUIDITY,
    # the book itself could not be trusted
    "STALE_BOOK": DATA_QUALITY,
    "BOOK_AGE_UNKNOWN": DATA_QUALITY,
    # the reservation could not be READ, which is a ledger fault and not a
    # limit. Named apart from the concurrency refusals so a reader can tell
    # "the hedge is already working" from "we cannot tell what is working".
    "OUTSTANDING_UNREADABLE": DATA_QUALITY,
    "BOOK_GAP_SUSPECTED": DATA_QUALITY,
    "CUTOFF_UNREADABLE": DATA_QUALITY,
    # correct refusals, and NOT missed profit
    "NO_INVENTORY": NOT_AN_OPPORTUNITY,
    "ALREADY_MATCHED": NOT_AN_OPPORTUNITY,
    "PAST_TERMINAL": NOT_AN_OPPORTUNITY,
    # THE WINDOW'S OWN DEADLINES, which are not blockers either. These
    # arrive from the OPENING layer, where the clock refuses far more
    # often than any limit does. Unmapped they fell through to OTHER, and
    # `record` only scores NOT_AN_OPPORTUNITY as "N/A" -- so a correct
    # past-cutoff refusal was published as `missed_profitable_pair: YES`
    # against a perfectly good 0.95 pair, which is how the metric
    # overstates itself. Observed live on 1915z 2026-11-02.
    "PAST_ENTRY_CUTOFF": NOT_AN_OPPORTUNITY,
    "PAST_COMPLETE_CUTOFF": NOT_AN_OPPORTUNITY,
    "PAST_PLACE_CUTOFF": NOT_AN_OPPORTUNITY,
    "WINDOW_NOT_OPEN": NOT_AN_OPPORTUNITY,
    "BEFORE_PLACE_FLOOR": NOT_AN_OPPORTUNITY,
}

#: substring rules, in order, for the composed reasons -- `decide` emits
#: `ECONOMICS:<inner>` and the executor's refusals arrive with suffixes.
#: Ordered most specific first; RESIDUAL before CAP so that a residual
#: refusal is not read as a pair-ceiling one.
_CONTAINS = (
    ("RESIDUAL", RESIDUAL_CAP),
    ("DEPTH", LIQUIDITY),
    ("PAIR_ABOVE_CAP", PAIR_CEILING),
    ("ABOVE_CAP", PAIR_CEILING),
    ("PAIR_COST", PAIR_CEILING),
    ("WORST_PAIR_COMBINATION", PAIR_CEILING),
    ("MAX_LIVE_ORDERS", ORDER_CONCURRENCY),
    ("ALREADY_RESTING", ORDER_CONCURRENCY),
    ("CASH", BUDGET),
    ("EXPOSURE", BUDGET),
    ("ADDITIONAL_LOSS", BUDGET),
    ("BUDGET", BUDGET),
    ("UNMATCHED_CONTRACTS", RESIDUAL_CAP),
    ("LIQUIDITY", LIQUIDITY),
    ("STALE", DATA_QUALITY),
)


def _d(v: Any) -> Optional[D]:
    """Decimal, or None. Never raises, and never substitutes a zero."""
    if v is None:
        return None
    try:
        out = D(str(v))
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return None
    return out if out.is_finite() else None


def classify(reason: Any) -> str:
    """The blocker class for a named refusal. Never raises.

    An unrecognised reason is `OTHER` and keeps its own name in the record,
    which is the point of "or another named refusal": a blocker we did not
    anticipate must still be countable and must not be silently dropped into
    a neighbouring bucket.
    """
    r = str(reason or "").strip().upper()
    if not r:
        return OTHER
    if r in _EXACT:
        return _EXACT[r]
    inner = r.split(":", 1)[1] if ":" in r else ""
    if inner and inner in _EXACT:
        return _EXACT[inner]
    for needle, cls in _CONTAINS:
        if needle in r:
            return cls
    return OTHER


def pair_verdict(*, pair_own_cost: Any = None,
                 entry_ceiling: Any = None) -> Dict[str, Any]:
    """Was the pair that was given up actually worth completing?

    Returns `known` False and leaves every judgement UNKNOWN when the pair
    cost is absent, because a refusal that fires before the complement is
    priced has no pair to assess and guessing one would invent the evidence
    this record exists to supply.
    """
    pc = _d(pair_own_cost)
    if pc is None or pc <= 0:
        return {"known": False,
                "pair_own_cost": None,
                "locks_gain_vs_redemption": "UNKNOWN",
                "inside_entry_ceiling": "UNKNOWN",
                "gain_per_contract_usd": None}
    ceil = _d(entry_ceiling)
    return {"known": True,
            "pair_own_cost": str(pc),
            "locks_gain_vs_redemption": ("YES" if pc < REDEMPTION else "NO"),
            "inside_entry_ceiling": ("UNKNOWN" if ceil is None
                                     else ("YES" if pc <= ceil else "NO")),
            "gain_per_contract_usd": str(REDEMPTION - pc)}


def evidence_basis(*, book_source: Any = None, stream_age_s: Any = None,
                   max_age_s: float = EV_FRESH_MAX_AGE_S) -> Dict[str, Any]:
    """Where this record's prices came from, and whether to trust them.

    AUTHENTICATED_WS only when the source says so AND the stream was
    current. A stale authenticated frame is reported as lagged, because at
    the 2230z window a book twelve cents out of date would otherwise have
    been labelled authenticated and read as executable.
    """
    src = str(book_source or "").strip().lower()
    age = None
    try:
        if stream_age_s is not None:
            age = float(str(stream_age_s))
    except (TypeError, ValueError):
        age = None
    if "ws" in src or "websocket" in src or "authenticated" in src:
        if age is None:
            basis, why = EV_UNKNOWN, "authenticated source but stream age unreadable"
        elif age <= max_age_s:
            basis, why = EV_AUTHENTICATED, "authenticated stream, current"
        else:
            basis, why = EV_REST_SPARSE, "authenticated source but stale frame"
    elif "rest" in src or "poll" in src or "tape" in src:
        basis, why = EV_REST_SPARSE, "polled REST observation"
    else:
        basis, why = EV_UNKNOWN, "book source not stated"
    return {"basis": basis, "book_source": (str(book_source)
                                            if book_source is not None
                                            else None),
            "stream_age_s": (None if age is None else age),
            "max_fresh_age_s": max_age_s, "why": why,
            "executable_evidence": basis == EV_AUTHENTICATED}


def record(*, market: Any, reason: Any,
           decision: Optional[Mapping[str, Any]] = None,
           entry_ceiling: Any = None,
           held_side: Any = None, held_qty: Any = None,
           deficient_qty: Any = None,
           stage: str = "DECISION",
           book_source: Any = None,
           effective_pair_cost: Any = None) -> Dict[str, Any]:
    """One missed-completion record, safe to emit. Never raises.

    `missed_profitable_pair` is the field the campaign is read on:

      YES      a pair below $1.00 was available and a real blocker stopped it
      NO       the pair was priced and was NOT worth completing
      UNKNOWN  the refusal fired before the pair could be priced
      N/A      there was no opportunity (no inventory, already matched, past
               the terminal) -- a correct refusal, not a missed pair

    Only YES is a problem to fix, and separating it from the other three is
    what makes the refusal histogram actionable instead of merely large.
    """
    try:
        dec = dict(decision or {})
        # ============ READ THE KEYS THE WRITER ACTUALLY WRITES.
        #
        # THE DEFECT THIS CLOSES, and it made this whole record useless at
        # the DECISION stage. Two callers hand us two different shapes:
        #
        #   OPENING  hand-builds flat keys and passes book_source, so it
        #            reported AUTHENTICATED_WS, stream_age_s 0.076 and a
        #            priced pair.
        #   DECISION passes `decide`'s own dict and no book_source. There
        #            the pair lives at `pair_if.at_touch`, the age at
        #            `book.stream_age_s` and the source at `book.source` --
        #            none of which this function read.
        #
        # So every DECISION record came out `pair.known false`,
        # `missed_profitable_pair UNKNOWN`, `basis EVIDENCE_BASIS_UNKNOWN`,
        # "book source not stated". `cooldown_observation` was added
        # specifically because "every cooldown record carried pair: None and
        # executable_evidence: false ... it made the one question worth
        # asking unanswerable". It priced all of it correctly and handed it
        # to a reader that looked elsewhere. Verified on the 2026-11-05
        # campaign: all six DECIDE_COOLDOWN records in window 2230z reported
        # UNKNOWN while holding UP 3.
        #
        # REPORTING ONLY. Nothing gates on this record; the fallbacks can
        # only make an existing record say more than "unknown".
        _bk = dec.get("book") if isinstance(dec.get("book"), Mapping) else {}
        _age = dec.get("stream_age_s")
        if _age is None:
            _age = _bk.get("stream_age_s")
        _src = book_source if book_source is not None else _bk.get("source")
        # THE PAIR, AND ITS PROVENANCE. `pair_if.at_touch` is explicitly
        # "what the pair WOULD have cost had that leg filled in full --
        # conditional, never realized". That is exactly the right basis for
        # "was a pair available", which is what this field answers, and the
        # source is recorded so a conditional pair can never be read as an
        # executed one.
        _pair = dec.get("pair_own_cost")
        _pair_src = "decision.pair_own_cost" if _pair is not None else None
        if _pair is None:
            _pif = (dec.get("pair_if")
                    if isinstance(dec.get("pair_if"), Mapping) else {})
            _pair = _pif.get("at_touch")
            if _pair is not None:
                _pair_src = "decision.pair_if.at_touch (CONDITIONAL)"
        cls = classify(reason)
        pv = pair_verdict(pair_own_cost=_pair, entry_ceiling=entry_ceiling)
        if cls == NOT_AN_OPPORTUNITY:
            missed = "N/A"
        elif not pv["known"]:
            missed = "UNKNOWN"
        elif pv["locks_gain_vs_redemption"] == "YES":
            missed = "YES"
        else:
            missed = "NO"

        # ============ THE INVENTORY-AWARE VERDICT
        #
        # Three corrections to v1, applied in order of how badly v1 is
        # wrong. None of them can turn a v1 NO or N/A into a YES: v2 is
        # never more optimistic than v1.
        _state: Dict[str, Any] = {}
        _in_state = missed
        _hq = _d(held_qty)
        _eff = _d(effective_pair_cost)
        _is_open = str(stage or "").upper() == "OPENING"
        if _eff is not None:
            # THE QUANTITY THAT WAS ACTUALLY REFUSED, when the caller knows
            # it. `eff` is what the gate compared against the ceiling, so a
            # verdict that ignores it is answering about a different trade.
            _state["effective_pair_cost"] = str(_eff)
            if _in_state == "YES" and _eff >= REDEMPTION:
                _in_state = "NO"
                _state["downgraded_because"] = (
                    "EFFECTIVE_PAIR_ABOVE_REDEMPTION: the fresh pair shown "
                    "here was never the refused quantity; completing at the "
                    "effective pair would have locked a loss")
        if (_in_state == "YES" and _is_open and _hq is not None
                and _hq != 0):
            # A FRESH PAIR IS NOT AVAILABLE WHILE WE ARE ALREADY LONG ONE
            # SIDE. The engine's job in that state is to complete what it
            # holds, and the leg it would place is priced as a completion
            # against the basis already paid -- which is why
            # `pair_cost_refusal` fires on it at all.
            _in_state = "N/A"
            _state["downgraded_because"] = UNMATCHED_HELD
            _state["not_an_opportunity_in_state"] = (
                "%s: %s unmatched contracts were held, so the pair priced "
                "from the book was not a pair available to us"
                % (UNMATCHED_HELD, _hq))
        if _is_open:
            _state["fresh_pair_caveat"] = FRESH_PAIR_IS_LEVEL_INVARIANT
        _state["verdict"] = _in_state

        out: Dict[str, Any] = {
            "label": "L5_MISSED_PAIR",
            "market": str(market or ""),
            "stage": str(stage or "DECISION"),
            "reason": str(reason or ""),
            "blocker_class": cls,
            "missed_profitable_pair": missed,
            "pair": pv,
            "inventory": {"held_side": (None if held_side is None
                                        else str(held_side)),
                          "held_qty": (None if held_qty is None
                                       else str(held_qty)),
                          "deficient_qty": (None if deficient_qty is None
                                            else str(deficient_qty))},
            "locked_pnl_usd": (None if dec.get("locked_pnl_usd") is None
                               else str(dec.get("locked_pnl_usd"))),
            "need": dec.get("need"),
            "missed_profitable_pair_in_state": _in_state,
            "in_state": _state,
            "evidence": evidence_basis(
                book_source=_src, stream_age_s=_age),
            "pair_source": _pair_src,
            "definitions": {
                "missed_profitable_pair": DEF_V1,
                "missed_profitable_pair_in_state": DEF_V2,
                "note": ("v1 is FROZEN for compatibility with records "
                         "already emitted; v2 is the inventory-aware "
                         "verdict and is the one to read. Where they "
                         "disagree, v1 is the optimistic one."),
            },
            # THE LABEL NAMES WHAT WAS ACTUALLY REFUSED. It said
            # "a completion" on every record, including the OPENING ones,
            # which describes the wrong decision to anyone reading the
            # tape: an opening refusal gives up a NEW pair, not the
            # completion of inventory already held.
            "labels": ("%s that was DECIDED AGAINST, with the blocker "
                       "named and the pair priced where it could be. "
                       "`missed_profitable_pair` YES is the only case that "
                       "is a problem; UNKNOWN is not NO. Reporting only -- "
                       "nothing here places, reprices or withdraws anything, "
                       "and no figure here is a fill or realized."
                       % ("an OPENING pair"
                          if str(stage or "").upper() == "OPENING"
                          else "a completion")),
        }
        return out
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        # OBSERVABILITY MUST NEVER BREAK THE MONEY PATH. A record that
        # cannot be built reports that it could not, and the caller emits it
        # anyway rather than losing the fact that something was refused.
        return {"label": "L5_MISSED_PAIR", "captured": False,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
