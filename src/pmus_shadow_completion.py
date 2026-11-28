"""Why shadow breadth reports zero completions, and the second arm that fixes it.

OWNER: "The current `possible` fill heuristic produced ~12.5% virtual fills and
may be the reason COMPLETE observations are extremely sparse. Do not loosen it
merely to manufacture completions. Compare it against our actual PMUS fills."

I compared it, and the fill heuristic is NOT the problem.

MEASURED 2026-09-10 22:2x on aec-nfl-sf-lar-2026-09-10 (the live Thursday
game, the highest-flow market in the universe), 47 polls at 5s over 239s:

    cadence   observations   inferred tape   quotes   possible fills   rate
    5s                  47               8        3                3   100%
    45s                  6               5        3                3   100%

Every quote the policy posted was classed filled, at BOTH cadences. Trade
DIRECTION also resolved cleanly -- 8 of 8 inferred trades were buy or sell,
none "unknown" -- so the tight 0.5c spread does not defeat the inference, a
hypothesis I held before measuring and which the data refused. The ~12.5%
figure was an average dominated by markets with NO PRINTS AT ALL in the
window; on a market that actually trades, the heuristic fills everything.

THE REAL CAUSE IS AN ABSORBING STATE IN THE REPLAY, and it has nothing to do
with fills. Traced directly against the frozen v2 model:

    step  residual  proposed_rel  plan
    0            0  open          quote
    1            1  extend        quote
    2            2  extend        quote
    3            3  extend        no_action residual_cap:3>=3
    4..n         3  extend        no_action residual_cap:3>=3   (forever)

At the cap the model keeps proposing EXTEND. plan_action refuses it. The model
never proposes COMPLETE, so `rel == "complete"` -- the one branch that prices a
completion -- is never reached. decide_market's completion count is therefore
not merely sparse, it is STRUCTURALLY ZERO once a market reaches residual 3.
No amount of additional observation time can produce a completion from it.

WHY LIVE R5F STILL COMPLETES 75% OF THE TIME (12 of 16 settled markets paired
to zero residual, analysis/l5/L5_R5F_COHORT.md): live completion is NOT driven
by decide_market at all. The session layer runs its own complement subsystem --
spring_complement_price, q1.complement_bound, the complement-acceptance proof --
which posts completions independently of whatever relation the model proposed.
decide_market is the ACQUISITION half of the live engine. Shadow breadth was
replaying that half and reporting its completion column as though it were the
whole engine.

So the owner's conditional is answered plainly: the shadow's completion
statistics do NOT tell us what live R5F would do, and the reason is not
calibration of the fill model but a missing component.

THE FIX IS A SECOND ARM, NOT A LOOSER FIRST ONE. Nothing here relaxes
MAX_RESIDUAL_UNITS, the pair ceiling, or the fill rule -- the owner forbade
manufacturing completions and this does not. Arm A stays exactly as it is.
Arm B additionally asks the FROZEN POLICY'S OWN completion branch, via
plan_action(rel="complete"), what it would post against the same book and the
same replayed inventory. Both arms are reported side by side and never
summed: A is "what decide_market did", B is "what the live engine's
completion half would have added". Collapsing them into one number would hide
exactly the distinction that made the zero misleading in the first place.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from src import pmus_sports_l5_decision as DEC

ARM_A = "decide_market_only"
ARM_B = "with_live_completion_half"


def _inventory_from_actions(actions, snapshot=None):
    """Rebuild the replayed inventory from the ACTIONS, not the snapshot.

    Inventory.snapshot() carries only counts -- {yes, no, matched, residual}
    -- and NO prices. Completion pricing needs inv.avg(surplus_side), so a
    reconstruction from the snapshot would silently produce an empty
    inventory, report "flat_nothing_to_complete", and look exactly like a
    market with nothing to complete. That is the failure mode this whole
    module exists to expose, so it is refused rather than approximated.

    The actions carry everything needed and nothing inferred: decide_market
    records a fill as inv.record(side_yes, px) precisely when an action is a
    quote classed `possible`, and each such action reports `side` and
    `target_price`. Rebuilding from those reproduces the same inventory by
    construction.

    The result is CROSS-CHECKED against the snapshot's residual. A mismatch
    means the reconstruction and the replay disagree about the position, and
    an inventory we cannot confirm is never completed against.
    """
    try:
        inv = DEC.Inventory()
        for a in actions or []:
            if a.get("decision") != "quote" or a.get("fill_class") != "possible":
                continue
            side = a.get("side")
            px = a.get("target_price")
            if side is None or px is None:
                return None, "action_missing_side_or_price"
            inv.record(side == "YES", DEC.D(str(px)))
        if snapshot is not None and "residual" in snapshot:
            if inv.residual != snapshot.get("residual"):
                return None, (f"reconstruction_disagrees:"
                              f"{inv.residual}!={snapshot.get('residual')}")
        return inv, None
    except Exception as exc:                                # noqa: BLE001
        return None, f"reconstruction_failed:{exc!r}"


def completion_attempt(inv, book: Mapping[str, Any]) -> dict:
    """What the frozen policy's COMPLETE branch would post right now.

    This calls plan_action with rel="complete" directly. It does not invent a
    price, relax a bound, or bypass complement_target: an above-ceiling or
    unpriceable completion comes back as the refusal plan_action already
    returns, exactly as it would live.
    """
    try:
        if inv is None or inv.residual == 0:
            return {"decision": "no_action", "reason": "flat_nothing_to_complete"}
        return DEC.plan_action("complete", inv, book)
    except Exception as exc:                                # noqa: BLE001
        return {"decision": "no_action", "reason": f"complete_failed:{exc!r}"}


def two_arm(decision: Mapping[str, Any],
            book: Optional[Mapping[str, Any]]) -> dict:
    """Arm A exactly as decide_market reported it; arm B adds the completion
    half. The two are reported separately and MUST NOT be summed."""
    acts = decision.get("actions_replayed") or []
    a_completes = sum(1 for a in acts
                      if a.get("relation") == "complete"
                      and a.get("decision") == "quote")
    snap = decision.get("inventory_final") or {}
    residual = snap.get("residual")
    stalled = any(str(a.get("reason", "")).startswith("residual_cap")
                  for a in acts)

    arm_b: dict = {"decision": "no_action", "reason": "no_book"}
    if book is not None:
        inv, why = _inventory_from_actions(acts, snap)
        arm_b = ({"decision": "no_action", "reason": why} if inv is None
                 else completion_attempt(inv, book))

    return {
        "kind": "shadow_two_arm",
        ARM_A: {"completions": a_completes,
                "residual_final": residual,
                "stalled_at_residual_cap": stalled},
        ARM_B: {"completion_would_post": arm_b.get("decision") == "quote",
                "plan": arm_b},
        "labels": (
            "arm A is decide_market alone -- the ACQUISITION half of the live "
            "engine, whose completion count is structurally zero once a market "
            "reaches residual_cap. arm B asks the same frozen policy's own "
            "COMPLETE branch what it would post. Neither arm is a fill, and "
            "the two are never summed."),
    }
