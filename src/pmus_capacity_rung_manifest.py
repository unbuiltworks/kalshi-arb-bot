"""EXP-010 capacity-ladder risk manifest and preflight — READ-ONLY, DISARMED.

Holds no credentials, opens no connection, and cannot reach the order path. It
computes the worst-case realized exposure of a proposed rung and REFUSES any
configuration that could let a single market consume the PROBE-CAP-02 envelope.

The two quantities are never conflated:
  deployed capital      cash out, recovered at settlement on a matched pair
  worst-case realized   the UNMATCHED basis, q * p, lost if it settles against
                        us and the complement never fills

PROBE-CAP-02 is a $5.00 cumulative REALIZED-loss envelope. It is NOT permission
for $5.00 of naked exposure per market: no single market may risk more than
PER_MARKET_FRACTION of it.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Mapping

#: separate from RESEARCH-LOSS-01 ($0.49320), which this never touches
PROBE_CAP_02_USD = D("5.00")
#: permanently unallocated buffer inside the envelope
RESERVE_USD = D("1.00")
#: no single market may risk more than this share of the envelope
PER_MARKET_FRACTION = D("0.40")
#: largest clip ever FILLED on an authenticated live order (r4_orders.jsonl)
DEMONSTRATED_LIVE_CLIP = D("5.31")


def allocable_usd() -> D:
    return PROBE_CAP_02_USD - RESERVE_USD


def per_market_worst_case_cap() -> D:
    return (PROBE_CAP_02_USD * PER_MARKET_FRACTION).quantize(D("0.01"))


def worst_case_realized(q: D, p: D) -> D:
    """Loss if the leg is unmatched at settlement: the whole basis."""
    return (D(str(q)) * D(str(p))).quantize(D("0.0001"))


def deployed_capital_for_pair(q: D, pair_cost: D = D("0.96")) -> D:
    """Cash out to hold a COMPLETED pair. Capital, not loss."""
    return (D(str(q)) * D(str(pair_cost))).quantize(D("0.0001"))


def max_clip_for_band(p: D, cap: D = None) -> D:
    """Largest clip whose unmatched basis stays inside the per-market cap."""
    cap = per_market_worst_case_cap() if cap is None else cap
    return (cap / D(str(p))).quantize(D("0.01"))


def evaluate_rung(*, name: str, q: D, entry_price_max: D,
                  max_unmatched_usd: D, rung_loss_budget_usd: D) -> dict:
    """Manifest for one rung plus a hard verdict. ADMIT means nothing else."""
    q = D(str(q)); p = D(str(entry_price_max))
    worst = worst_case_realized(q, p)
    cap = per_market_worst_case_cap()
    reasons = []
    if worst > cap:
        reasons.append("WORST_CASE_EXCEEDS_PER_MARKET_CAP:%s>%s" % (worst, cap))
    if worst > D(str(max_unmatched_usd)):
        reasons.append("WORST_CASE_EXCEEDS_MAX_UNMATCHED:%s>%s"
                       % (worst, max_unmatched_usd))
    if D(str(max_unmatched_usd)) > cap:
        reasons.append("MAX_UNMATCHED_EXCEEDS_PER_MARKET_CAP")
    if D(str(rung_loss_budget_usd)) > allocable_usd():
        reasons.append("RUNG_BUDGET_EXCEEDS_ALLOCABLE")
    return {
        "rung": name, "clip_contracts": str(q),
        "entry_price_max": str(p),
        "worst_case_realized_usd": str(worst),
        "per_market_worst_case_cap_usd": str(cap),
        "max_unmatched_usd": str(max_unmatched_usd),
        "deployed_capital_for_pair_usd": str(deployed_capital_for_pair(q)),
        "rung_loss_budget_usd": str(rung_loss_budget_usd),
        "cumulative_stop_usd": str(-PROBE_CAP_02_USD),
        "clip_vs_demonstrated_live": str(
            (q / DEMONSTRATED_LIVE_CLIP).quantize(D("0.01"))) + "x",
        "is_modeled_extrapolation": q > DEMONSTRATED_LIVE_CLIP,
        "verdict": "ADMIT" if not reasons else "REFUSE",
        "refusals": reasons,
    }


def preflight(state: Mapping[str, object]) -> dict:
    """Fail-closed gate. Every required fact must be AUTHENTICATED and true.

    `state` carries authenticated reads. A missing or unverified key is a
    REFUSAL, never a pass -- uncertain state fails closed.
    """
    required = ("balances_verified", "positions_flat_in_target_market",
                "no_open_orders", "credentials_present", "arm_trio_valid",
                "gate_evidence_matches_slug", "artifact_commit_is_current_head")
    missing = [k for k in required if not state.get(k)]
    return {
        "armed": False,
        "verdict": "DISARMED_REFUSE" if missing else "PREFLIGHT_CLEAR",
        "unsatisfied": missing,
        "note": ("PREFLIGHT_CLEAR is necessary and NOT sufficient: arming stays "
                 "operator-only and this module cannot arm anything."),
    }
