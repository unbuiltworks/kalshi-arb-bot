"""PRE-REGISTERED GATE for recommending concurrency 2 or larger per-market
capital (OWNER #167 comment 5586921649, scaling program).

FROZEN BEFORE ANY COUNTERFACTUAL RESULT IS READ. That ordering is the whole
point and it is the same discipline the sports-translation gate used: a
threshold chosen after seeing the numbers is not a threshold, it is a
description of the numbers. This module therefore contains the criteria and
nothing that computes them; the counterfactual lives in
src/l5_concurrency_counterfactual.py and imports this.

WHAT THIS GATE IS NOT. It never enacts anything. Concurrency, per-market
capital, the $7.50 cap and the -$15.00 daily stop are owner decisions and
stay exactly where they are until the owner says otherwise, whatever this
gate reports. A PASS is a recommendation to the owner, not a change.

WHY THESE CRITERIA. The owner's standing test for any optimisation is
"does this help us execute L5 more faithfully, or allocate capital among
equally faithful opportunities more efficiently?" Concurrency 2 is the
second kind: L5 itself held residuals in other windows at 72.4% of its
window opens, so one-market-at-a-time is a known fidelity gap, not a
neutral choice. But more slots also means more simultaneous residual
exposure, and residual is where this book actually loses money
(pair-locked +$155,076 against directional residual -$141,390 over L5's
own history). So the gate has to buy fidelity and throughput WITHOUT
buying worse worst-case exposure, and it has to be paid in SETTLED money
rather than in marks.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

# --------------------------------------------------------------------------

CONCURRENCY_GATE: dict[str, Any] = {
    "id": "L5_CONCURRENCY_SCALING_GATE_v1",
    "owner_ref": "#167 comment 5586921649",
    "frozen_before": ("any concurrency 1 vs 2 vs 3 counterfactual output is "
                      "read. Results computed before this gate was frozen "
                      "cannot satisfy it."),
    "enacts": ("NOTHING. A PASS is a recommendation to the owner. "
               "Concurrency, per-market capital, the $7.50 cap and the "
               "-$15.00 daily stop change only on explicit owner approval."),

    # ---- what must be true of the evidence before the gate may be read ----
    "sample_minimums": {
        "forward_hours_of_tape": 72,
        "completed_sequences_at_c1": 40,
        "distinct_markets": 40,
        "distinct_leagues": 4,
        "settled_markets_with_venue_confirmed_realized": 20,
        "note": ("all measured on the SAME forward tape replayed at each "
                 "concurrency; a counterfactual arm may not be scored on "
                 "decisions its own arm never reached"),
    },

    # ---- what concurrency 2 must beat, and by how much ----
    "must_improve": {
        "completed_sequences_per_bankroll_dollar_hour": (
            "c2 strictly greater than c1 by at least 15% relative. This is "
            "the throughput claim; below that margin the extra slot is not "
            "worth the extra state"),
        "l5_relation_fidelity": (
            "c2's L1 distance of ACTED relation mix to L5 no worse than c1's "
            "by more than 0.02 absolute. Concurrency is justified as a "
            "fidelity gain; it may not cost fidelity"),
        "flat_time_share_while_eligible_candidate_existed": (
            "c2 strictly lower than c1. If the second slot does not reduce "
            "measured idle time it is not solving the problem it is for"),
    },

    # ---- what concurrency 2 may not do, at any margin ----
    "must_not_worsen": {
        "worst_simultaneous_unmatched_usd": (
            "c2's maximum simultaneous unmatched dollars across all open "
            "markets must not exceed 2.0x the c1 maximum. Two slots may "
            "carry two markets' residual; they may not carry more than that "
            "through position-chasing"),
        "residual_loss_per_completed_sequence": (
            "c2 no worse than c1 by more than 10% relative, measured in "
            "SETTLED venue-confirmed dollars only"),
        "unresolved_residual_at_settlement_share": (
            "c2 no worse than c1 in absolute share of sequences that reach "
            "settlement still carrying residual"),
        "venue_ledger_reconciliation": (
            "every arm must reconcile; a counterfactual arm whose quantities "
            "cannot be tied to venue evidence is UNVERIFIABLE, not a pass"),
    },

    # ---- money rules that survive any result ----
    "accounting_rules": {
        "realized_only": ("settled, venue-confirmed dollars. No mark, no "
                          "modelled P&L, no projected rebate"),
        "incentives": ("credited/confirmed rebates only, reported SEPARATELY "
                       "from trading edge and never netted into it"),
        "counterfactual_label": ("counterfactual arms are DERIVED and are "
                                 "labelled derived wherever they appear"),
    },

    "rollback": {
        "trigger": ("if concurrency 2 is ever enacted and, over the first 20 "
                    "settled markets under it, either the residual loss per "
                    "completed sequence or the worst simultaneous unmatched "
                    "dollars breaches the must_not_worsen bounds above"),
        "action": ("return to concurrency 1 immediately and report; the "
                   "return does not require a new owner decision, only the "
                   "increase does"),
    },

    "verdict_values": ["NOT_YET_ENOUGH_FORWARD_EVIDENCE",
                       "RECOMMEND_CONCURRENCY_2_TO_OWNER",
                       "DO_NOT_RECOMMEND",
                       "UNVERIFIABLE"],
}


def gate_sha16(gate: Mapping[str, Any] = CONCURRENCY_GATE) -> str:
    """Stable identity for the frozen criteria, so a later reader can prove
    the gate they are looking at is the one that was frozen."""
    blob = json.dumps(gate, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


GATE_FROZEN_UTC = "2026-09-08T14:47:00+00:00"
GATE_SHA16_AT_FREEZE = "b86fe09178ea78a2"


def assert_unmodified() -> None:
    """A gate that can be edited after the fact is not pre-registered. Any
    change to the criteria changes the sha and this raises, which is the
    intended way to find out."""
    now = gate_sha16()
    if now != GATE_SHA16_AT_FREEZE:
        raise AssertionError(
            f"CONCURRENCY GATE MODIFIED AFTER FREEZE: {now} != "
            f"{GATE_SHA16_AT_FREEZE}. Frozen {GATE_FROZEN_UTC}. A criterion "
            f"chosen after seeing results is not a criterion; publish a v2 "
            f"gate with its own freeze time instead of editing v1.")
