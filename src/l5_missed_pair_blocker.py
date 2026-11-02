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

#: exact reasons, matched before any substring rule. `decide`'s own
#: vocabulary, plus the two the runner adds after `decide` returns.
_EXACT: Dict[str, str] = {
    # the runner's own post-decision refusals
    "ORDER_ALREADY_RESTING": ORDER_CONCURRENCY,
    "MAX_LIVE_ORDERS": ORDER_CONCURRENCY,
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
    "BOOK_GAP_SUSPECTED": DATA_QUALITY,
    "CUTOFF_UNREADABLE": DATA_QUALITY,
    # correct refusals, and NOT missed profit
    "NO_INVENTORY": NOT_AN_OPPORTUNITY,
    "ALREADY_MATCHED": NOT_AN_OPPORTUNITY,
    "PAST_TERMINAL": NOT_AN_OPPORTUNITY,
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


def record(*, market: Any, reason: Any,
           decision: Optional[Mapping[str, Any]] = None,
           entry_ceiling: Any = None,
           held_side: Any = None, held_qty: Any = None,
           deficient_qty: Any = None,
           stage: str = "DECISION") -> Dict[str, Any]:
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
        cls = classify(reason)
        pv = pair_verdict(pair_own_cost=dec.get("pair_own_cost"),
                          entry_ceiling=entry_ceiling)
        if cls == NOT_AN_OPPORTUNITY:
            missed = "N/A"
        elif not pv["known"]:
            missed = "UNKNOWN"
        elif pv["locks_gain_vs_redemption"] == "YES":
            missed = "YES"
        else:
            missed = "NO"
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
