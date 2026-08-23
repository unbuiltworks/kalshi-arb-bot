"""Explicitly-approved phase-2 policy for sequential q=1 basis-risk research.

This module is intentionally NOT wired into the production entrypoint.  It exists so
a reviewed promotion can be activated later without rebuilding policy under pressure.
Phase 2 keeps the existing $5 maximum-loss research budget, q=1 / <=$1 tranche,
one unresolved trade maximum, hard stop on the first realized loss, ETH/SOL/XRP-only
scope, dynamic threshold-dominance/economics/freshness/depth/FOK gates, BTC quarantine,
shared guaranteed-arbitrage settlement-equivalence lock, and q5 block.

The semantic change versus v5 is explicit: settled winning probes do not permanently
consume the *maximum-loss* budget.  A phase-2 lease may only start after five fully
realized profitable/no-loss probes and requires CRYPTO_BASIS_RISK_PHASE2_APPROVED=YES.
No production code imports this module today.
"""
from __future__ import annotations

from decimal import Decimal
import os
from typing import Any

from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v5 as v5

D = Decimal
APPROVAL_ENV = "CRYPTO_BASIS_RISK_PHASE2_APPROVED"
PHASE2_PROBE_LIMIT = 10
MIN_BASELINE_REALIZED = 5


def approval_enabled() -> bool:
    return os.getenv(APPROVAL_ENV, "NO").strip().upper() == "YES"


def phase2_rollover_allowed(state: dict[str, Any]) -> tuple[bool, str]:
    """Fail-closed promotion gate while preserving the existing maximum-loss budget."""
    if state["unresolved_reservations"]:
        return False, "UNRESOLVED_RESERVATION"
    if state["unresolved_submissions"]:
        return False, "UNRESOLVED_SUBMISSION"
    if int(state["realized_losses"]) > 0:
        return False, "REALIZED_LOSS_HARD_STOP"
    realized_loss = D(state["realized_loss_usd"])
    if realized_loss >= child.APPROVED_BUDGET:
        return False, "APPROVED_LOSS_BUDGET_EXHAUSTED"

    reservations = int(state["reservations"])
    realized = int(state["realized"])
    if reservations < MIN_BASELINE_REALIZED or realized < MIN_BASELINE_REALIZED:
        return False, "FIVE_PROBE_BASELINE_NOT_PROVEN"
    if realized != reservations:
        return False, "PRIOR_PROBES_NOT_FULLY_REALIZED"
    if reservations >= PHASE2_PROBE_LIMIT:
        return False, "PHASE2_PROBE_LIMIT_REACHED"

    # Phase 2 treats the unchanged $5 approval as a maximum-loss ceiling rather than
    # a lifetime turnover ceiling.  Because only one <=$1 probe can be unresolved and
    # any realized loss hard-stops future probes, this check keeps worst-case approved
    # loss below the same $5 ceiling without silently increasing it.
    if realized_loss + child.INITIAL_TRANCHE_USD > child.APPROVED_BUDGET:
        return False, "APPROVED_LOSS_BUDGET_EXCEEDED"
    return True, "OK"


def install() -> None:
    """Install phase-2 policy only after an explicit external approval flag."""
    if not approval_enabled():
        raise PermissionError(f"phase 2 requires explicit {APPROVAL_ENV}=YES")
    v5.PROMOTED_PROBE_LIMIT = PHASE2_PROBE_LIMIT
    v5.automatic_rollover_allowed = phase2_rollover_allowed
    v5.install()
