"""Every barrier on the canonical L5 -> PMUS path, classified KEEP or REMOVE.

OWNER 5594284979 / 5594313563: separate barriers into HARD/REAL (venue,
account, settlement, observability, capital, bounded safety) and
SELF-IMPOSED (readiness gates, category heuristics, spring admission,
evidence thresholds, cooldowns, arbitrary timers, legacy sports filters,
stale certification, duplicate safety shells). Remove class B from the
canonical path. Do NOT loosen dollar-loss, exposure, or deadman bounds.

--- THE ONE THING TO READ IF YOU READ NOTHING ELSE ---
The binding barrier is NOT a software gate. It is SESSION_NOTIONAL_CAP =
$0.05 per market on the first leg. At a $0.50 contract that is one tenth of
one contract, and the venue minimum is one contract. Every readiness gate
below could be deleted tonight and the lane would still not produce a fill,
because the money bound is an order of magnitude under the venue's minimum
tradeable unit.

That is exactly the distinction the owner asked for: an ECONOMIC/VENUE
mismatch, not a readiness gate we invented. It is classified KEEP because
it is a dollar bound and only the owner may move it -- but it is marked
BLOCKING, because keeping it means no live action is possible at all, and
saying that plainly is the point of this inventory.

--- HOW TO READ THE TABLE ---
blocks_now       does this stop an action TODAY on the canonical path
l5_supported     does L5's own observed behaviour require this
safety_required  is this one of the bounded protections the owner named
action           KEEP or REMOVE
"""

from decimal import Decimal as D
from typing import Any, Dict, List

# ---------------------------------------------------------------- the table
# Ordered so the blocking rows come first.
BARRIERS: List[Dict[str, Any]] = [
    # ---------------------------------------------------- HARD, and blocking
    {
        "barrier": "SESSION_NOTIONAL_CAP = $0.05 first leg per market",
        "where": "pmus_micro_executor.PER_MARKET_FIRST_LEG_CAP",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": True,
        "action": "KEEP",
        "note": ("THE BINDING CONSTRAINT. $0.05 buys a tenth of a contract "
                 "at $0.50; the venue minimum is one. No gate removal can "
                 "make this lane act. Only the owner can raise a money "
                 "bound, and this inventory does not."),
    },
    {
        "barrier": "daily realized loss stop (-$15.00)",
        "where": "pmus_q1_executor.DAILY_LOSS_STOP",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": True,
        "action": "KEEP",
        "note": ("currently TRIPPED -- the lane halted on "
                 "CAMPAIGN_STOP:DAILY_LOSS_STOP. Reconciled exactly at "
                 "-$18.5873. Not to be reset to manufacture activity."),
    },
    {
        "barrier": "MAX_RESIDUAL_UNITS = 3",
        "where": "pmus_spring_l5_session",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": True,
        "action": "KEEP",
        "note": ("residual exposure bound. L5 runs FAR more residual (99% "
                 "of windows end carrying some), so this is a real fidelity "
                 "cost -- but residual is the term that cost us 6.458%/$, "
                 "and the owner named exposure as preserved."),
    },
    {
        "barrier": "cancel / deadman (STOP_BEFORE_SETTLEMENT_S=120)",
        "where": "pmus_spring_l5_session",
        "blocks_now": False,
        "l5_supported": False,
        "safety_required": True,
        "action": "KEEP",
        "note": "explicitly named as preserved by the owner",
    },
    {
        "barrier": "maker-only / post-only order path",
        "where": "pmus_spring_l5_session + micro executor",
        "blocks_now": False,
        "l5_supported": True,
        "safety_required": True,
        "action": "KEEP",
        "note": ("L5 is 77.3% maker on classified fills, so this is BOTH "
                 "L5-supported and a cost control. Not a self-imposed gate."),
    },
    # ------------------------------------------------------- SELF-IMPOSED
    {
        "barrier": "SPRING_BAND 0.35-0.65 entry price band",
        "where": "pmus_spring_l5_session.SPRING_BAND_LO/HI",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": False,
        "action": "REMOVE",
        "note": ("a category heuristic we invented. L5's own fills span the "
                 "whole 0-1 range and its same-side purchase probability "
                 "RISES with attainable pair cost, so a mid-band-only rule "
                 "contradicts the evidence rather than reproducing it."),
    },
    {
        "barrier": "spring admission / strict repeated-completion replay",
        "where": "PMUS_SPRING_GATE_EVIDENCE, PMUS_SPRING_REPEATED_COMPLETION",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": False,
        "action": "REMOVE",
        "note": ("the owner names spring admission rules explicitly. It "
                 "requires an operator-pasted evidence string before the "
                 "lane may act -- pure readiness ceremony, no money bound."),
    },
    {
        "barrier": "complement-acceptance proof gate",
        "where": "COMPLEMENT_PROOF_FILE / COMPLEMENT_PROOF_RETRY_S=600",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": False,
        "action": "REMOVE",
        "note": ("a DUPLICATE SAFETY SHELL and a known deadlock: when flat "
                 "there is no re-proof path, which is what took the lane "
                 "down at 13:09 on 2026-09-08. The crossing guard already "
                 "prevents the thing this was protecting against."),
    },
    {
        "barrier": "SPORTS_MAKER_REST_MAX_S = 30s",
        "where": "pmus_spring_l5_session",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": False,
        "action": "REMOVE",
        "note": ("an arbitrary timer that CONTRADICTS L5: his median gap "
                 "between legs is 47s, so a 30s cancel guarantees we "
                 "abandon exactly the orders he would still be resting. "
                 "Replace with the market's own deadline, not a constant."),
    },
    {
        "barrier": "COMPLETE_REJECT_STREAK_MAX = 3 breaker",
        "where": "pmus_spring_l5_session",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": False,
        "action": "REMOVE",
        "note": ("a cooldown over a venue behaviour we do not yet "
                 "understand (task #93, non-crossing rejects). Halting on "
                 "it hides the diagnostic instead of collecting it."),
    },
    {
        "barrier": "MAX_ACTIONS = 6 per session",
        "where": "PMUS_SPRING_MAX_ACTIONS",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": False,
        "action": "REMOVE",
        "note": ("arbitrary. L5 takes hundreds of actions per window. The "
                 "money caps already bound exposure; counting actions "
                 "bounds nothing that dollars do not."),
    },
    {
        "barrier": "MIN_REEVAL_S = 2.0",
        "where": "pmus_spring_l5_session",
        "blocks_now": False,
        "l5_supported": False,
        "safety_required": False,
        "action": "REMOVE",
        "note": "arbitrary timer; rate limiting belongs at the transport",
    },
    {
        "barrier": "SPRING_PAIR_TARGET = 0.98 fee-safe ceiling",
        "where": "pmus_spring_l5_session",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": False,
        "action": "REMOVE",
        "note": ("stale certification. L5 pairs ABOVE par on 19.7% of "
                 "matched units -- a deliberate loss he accepts. A 0.98 "
                 "ceiling refuses a fifth of his book. The 1.00 hard "
                 "completion cap is the real bound and it stays."),
    },
    {
        "barrier": "SPORTS_TTE_DEFAULT_S = 1e9 (no settlement clock)",
        "where": "pmus_spring_l5_session",
        "blocks_now": False,
        "l5_supported": False,
        "safety_required": False,
        "action": "REMOVE",
        "note": ("a sports-specific clock hack. Crypto up/down markets "
                 "serve a real close_time, so the coarsest-TTE fallback is "
                 "not needed on the L5-native universe."),
    },
    {
        "barrier": "legacy sports discovery filters (props, slate rank)",
        "where": "pmus sports collector selection path",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": False,
        "action": "REMOVE",
        "note": ("the canonical L5 universe is crypto up/down, not sports. "
                 "These filters select a universe L5 never traded."),
    },
    # ------------------------------------------------- keep, but not safety
    {
        "barrier": "off_distribution hold",
        "where": "polymarket_l5.action_model",
        "blocks_now": True,
        "l5_supported": True,
        "safety_required": False,
        "action": "KEEP",
        "note": ("this is the one gate that IS L5 evidence: it fires when "
                 "the state was never visited in his tape, so acting would "
                 "be invention rather than reproduction. Keeping it is "
                 "fidelity, not ceremony."),
    },
    {
        "barrier": "concurrency = 1 market",
        "where": "_CONCURRENT_OWNED",
        "blocks_now": True,
        "l5_supported": False,
        "safety_required": True,
        "action": "KEEP",
        "note": ("L5 runs many markets at once, so this costs fidelity. It "
                 "is retained because it bounds simultaneous exposure, "
                 "which the owner named. Raising it is a money decision."),
    },
]


def _rows(**match) -> List[Dict[str, Any]]:
    return [b for b in BARRIERS
            if all(b.get(k) == v for k, v in match.items())]


def to_remove() -> List[Dict[str, Any]]:
    return _rows(action="REMOVE")


def to_keep() -> List[Dict[str, Any]]:
    return _rows(action="KEEP")


def blocking_after_removal() -> List[Dict[str, Any]]:
    """What still stops a live action once every REMOVE is gone. This is the
    honest answer to 'what prevents the next live action'."""
    return [b for b in BARRIERS
            if b["action"] == "KEEP" and b["blocks_now"]]


THE_ANSWER = (
    "Removing every self-imposed barrier does NOT unblock the lane. Four "
    "KEEP barriers still block, and three of them are money bounds only the "
    "owner can move: a $0.05 first-leg cap that is a tenth of the venue "
    "minimum, a daily loss stop that is already tripped, a 3-unit residual "
    "cap, and concurrency 1. The software gates were never the reason "
    "nothing happened.")

NOT_LOOSENED = ("no dollar-loss, exposure, deadman or concurrency bound is "
                "changed by this inventory. Every REMOVE is a readiness "
                "gate, a category heuristic, an arbitrary timer, a stale "
                "certification or a duplicate shell.")
