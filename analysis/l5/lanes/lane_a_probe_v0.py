"""LANE_A_EXECUTION_MEASUREMENT_PROBE_V0 — prepared for explicit owner arming.

SCOPE IS EXECUTION AND ACCOUNTING MEASUREMENT. Not economics. `SPRING_PAIR_TARGET`
is NOT modified and 0.98 is NOT promoted to an optimal or profitable threshold:
it is the deployed constant, applied unchanged, as a first-probe eligibility
envelope that expires with this probe. At the executable size the locked edge is
$0.0004, which is noise -- any P&L here is a measurement artefact.

WHAT CLEARING THE TWO OWNER GATES CAN AND CANNOT ACTIVATE. The user asked for
confirmation; the honest answer has a real caveat in it.

  CANNOT: the spring dual key is ISOLATED. Only pmus_spring_l5_session,
  pmus_spring_probe_readiness and pmus_spring_evidence_transfer read
  PMUS_SPRING_LIVE / _ARM / _SESSION_ID, and every other arming key on this
  account is a DISJOINT NAME -- PMUS_Q1_*, PMUS_MICRO_*, PMUS_HANDOFF_*,
  PMUS_SUPERVISOR_*, PMUS_SHADOW_TARGET_ARM. Arming spring cannot arm any of
  them. Clearing the latch alone cannot trade either: shutdown is tested BEFORE
  armed() is consulted, so an un-latched process with no dual key runs the
  campaign loop in mode SHADOW and places nothing.

  CAN, AND THIS IS THE ONE THAT MATTERS: the ACTIVE RUNG follows the CAMPAIGN,
  not the process. At boot the loop calls _apply_rung() from DURABLE STATE on
  /state/pmus-spring. The image pins PMUS_SPRING_RUNG_MARKETS=1, which bounds
  the market COUNT -- but the PER-MARKET CAP follows the rung, and a persisted
  R5F campaign carries per_market_cap_usd $7.50 (150x R1) with
  markets_per_campaign CONTINUOUS, while R5FT carries $30.00 (600x). The
  Dockerfile records that this already happened once: "a campaign booting
  without an allowance ... RESUMES R5F TRADING at the 0.98 ceiling R5FT
  replaces."

  THEREFORE ACTIVE_RUNG_IS_R1 IS A HARD PRE-ORDER GATE, and it must be read
  from the CONTAINER'S OWN first non-shutdown log line. Not from this
  container, whose ladder resolves R1 only because the env is unset here; not
  from the image, which does not pin PMUS_SPRING_RUNG; and not from Railway
  config, which the Dockerfile records as unreadable on this service.

NO DEPLOYMENT IS REQUIRED. Serving artifact 6adcc4c43e67 already carries
PMUS_SPRING_RUNG_MARKETS=1 in its image ENV, byte-identical to HEAD.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional

LABEL = "LANE_A_EXECUTION_MEASUREMENT_PROBE_V0"
SCOPE = "EXECUTION_AND_ACCOUNTING_MEASUREMENT_ONLY"

# Pinned. A mismatch on either is a STOP, never a warning.
EVIDENCE_SHA256_PREFIX = "43da9ac5"
SERVING_ARTIFACT = "6adcc4c43e67"
REQUIRED_RUNG = "R1"
REQUIRED_MARKETS_ALLOWED = 1

# Read, never written. Named so a reader can confirm it is untouched.
DEPLOYED_PAIR_TARGET = D("0.98")
PAIR_TARGET_MODIFIED = False

ENVELOPE = {
    "max_cumulative_notional_per_market_usd": "0.05",
    "max_per_day_usd": "0.15",
    "outstanding_orders": 1,
    "concurrent_markets": 1,
    "order_type": "maker/post-only",
    "residual_units_bound": 3,
    "max_actions_per_session": 6,
    "session_deadline_s": 900,
    "stop_before_settlement_s": 120,
    "restart": "NEVER",
    "residual_policy": "HOLD_TO_SETTLEMENT",
    "unchanged_from_R1": True,
}

# THE PROBE STOPS AFTER ONE MARKET even though the outer envelope permits
# three. Enforced by the image's PMUS_SPRING_RUNG_MARKETS=1, which halts the
# campaign at CAMPAIGN_WAITING:RUNG_MARKETS_REACHED with a reconciliation
# report rather than selecting a second market.
STOP_AFTER_FIRST_MARKET = True
STOP_MECHANISM = "PMUS_SPRING_RUNG_MARKETS=1 -> CAMPAIGN_WAITING:RUNG_MARKETS_REACHED"


def owner_gate_actions() -> Dict[str, Any]:
    """Exactly what the owner does, and exactly what it can reach."""
    return {
        "SHUTDOWN_LATCH_CLEARED": {
            "action": ("unset PMUS_SPRING_SHUTDOWN on service "
                       "d84d9a68-2e5b-4f74-9694-5f422417c74f, or set it to any "
                       "value outside {1, YES, TRUE}"),
            "read_as": 'os.getenv("PMUS_SPRING_SHUTDOWN","").strip().upper() '
                       'in ("1","YES","TRUE")',
            "effect_if_cleared_alone": (
                "the process leaves shutdown_idle and runs the campaign loop in "
                "mode SHADOW. armed() is still False, so NO order can be placed."),
            "cannot_activate": [
                "any PMUS_Q1_*, PMUS_MICRO_*, PMUS_HANDOFF_*, PMUS_SUPERVISOR_* "
                "or PMUS_SHADOW_TARGET_* path -- all disjoint key names",
                "any order path, because shutdown is tested before armed()",
            ],
            "CAN_activate_WARNING": (
                "leaving shutdown runs _apply_rung() from DURABLE STATE. If a "
                "persisted campaign carries R5F, the PER-MARKET CAP becomes "
                "$7.50 (150x) and markets_per_campaign CONTINUOUS; R5FT is "
                "$30.00. The image's RUNG_MARKETS=1 bounds the COUNT, not the "
                "CAP. ACTIVE_RUNG_IS_R1 must be confirmed from the container's "
                "own log BEFORE the dual key is set."),
            "ordering": "clear the latch FIRST, confirm the rung, THEN arm",
        },
        "DUAL_KEY_ARMED": {
            "action": ["PMUS_SPRING_LIVE=YES",
                       "PMUS_SPRING_SESSION_ID=<session id>",
                       "PMUS_SPRING_ARM=<the same session id, byte-identical>",
                       "PMUS_SPRING_GATE_EVIDENCE=<the exact market slug>"],
            "read_as": ("armed() is LIVE==YES and SESSION_ID non-empty and "
                        "ARM == SESSION_ID; gate_asserted() is "
                        "GATE_EVIDENCE == slug"),
            "isolation_confirmed": (
                "only pmus_spring_l5_session, pmus_spring_probe_readiness and "
                "pmus_spring_evidence_transfer read these names; the spring "
                "keys arm nothing else and no other arming key shares a name"),
            "effect_if_armed_without_clearing_latch": (
                "NONE -- shutdown is tested first and the process never reaches "
                "armed()"),
        },
        "both_required": True,
        "neither_cleared_here": True,
    }


def success_definition() -> List[Dict[str, str]]:
    """Defined BEFORE arming. Each stage is observed, never inferred."""
    return [
        {"stage": "submitted",
         "evidence": "our own submission record with an order id"},
        {"stage": "venue_acknowledged",
         "evidence": "authoritative venue order state from GET /v1/order/{id}, "
                     "never assumed from the submit response"},
        {"stage": "filled_or_cleanly_terminal",
         "evidence": "a fill with qty and price, OR an expiry/cancel confirmed "
                     "by a FRESH /v1/orders/open read showing none of ours "
                     "resting. An unknown state is NOT success."},
        {"stage": "exact_fee_rebate_attribution",
         "evidence": "the venue's own commission on OUR side chosen by "
                     "isAggressor, joined to the exact trade id and market "
                     "slug; every row retained, never truncated"},
        {"stage": "venue_confirmed_settlement_if_filled",
         "evidence": "venue-confirmed resolution with realizedPnl; a trade "
                     "lacking it is SETTLED_REALIZED_UNCONFIRMED and stays OUT "
                     "of the net"},
        {"stage": "ledger_reconciliation",
         "evidence": "order -> submitted -> fill -> matched -> residual -> fees "
                     "-> rebate -> reward -> settlement -> realized net closes, "
                     "and our inventory agrees with the venue's"},
    ]


def stop_conditions() -> List[Dict[str, str]]:
    """Defined BEFORE arming. Every one is STOP, latch, cancel -- never retry."""
    return [
        {"id": "UNEXPECTED_QUANTITY_OR_NOTIONAL",
         "trigger": "any filled qty or notional outside the computed executable "
                    "size, or cumulative spend exceeding $0.05 for the market",
         "action": "STOP, cancel, latch, report"},
        {"id": "UNKNOWN_ORDER_STATE",
         "trigger": "the venue cannot tell us an order's authoritative state",
         "action": "STOP, cancel, latch, report -- unknown is never assumed "
                   "terminal"},
        {"id": "RECONCILIATION_MISMATCH",
         "trigger": "our inventory or order set disagrees with a fresh venue "
                    "read; the VENUE is authoritative",
         "action": "STOP, cancel, latch, report"},
        {"id": "ARTIFACT_OR_CONFIG_MISMATCH",
         "trigger": f"serving artifact != {SERVING_ARTIFACT}, active rung != "
                    f"{REQUIRED_RUNG}, markets_allowed != "
                    f"{REQUIRED_MARKETS_ALLOWED}, or evidence sha != "
                    f"{EVIDENCE_SHA256_PREFIX}...",
         "action": "STOP before any order; this is the R5F guard"},
        {"id": "RISK_ENVELOPE_BREACH",
         "trigger": "daily realized-loss stop, residual bound, concurrency, "
                    "action count, or per-market cap breached",
         "action": "STOP, cancel, latch, hold residual to settlement"},
        {"id": "UNEXPECTED_RESTART",
         "trigger": "the container restarts during the probe",
         "action": "STOP. restart is NEVER; the probe does not resume itself"},
        {"id": "CANNOT_CANCEL",
         "trigger": "a cancel is not confirmed by a fresh /v1/orders/open read",
         "action": "STOP and report SHUTDOWN_NOT_VERIFIED; never report a "
                   "cancel the venue did not confirm"},
        {"id": "FIRST_MARKET_COMPLETE",
         "trigger": "one market has been worked to a terminal state",
         "action": "STOP AND REPORT. Do NOT select a second market, even "
                   "though the outer envelope permits $0.15/day"},
    ]


def preorder_preflight(*, rows, serving_artifact: Optional[str] = None,
                       active_rung: Optional[str] = None,
                       markets_allowed: Optional[int] = None,
                       evidence_sha256: Optional[str] = None,
                       budget_permits: Optional[bool] = None,
                       latch_set: Optional[bool] = None,
                       dual_key_set: Optional[bool] = None,
                       settlement_confirmed_markets: Optional[int] = None,
                       entry_price: Any = "0.27") -> Dict[str, Any]:
    """Rerun against CURRENT venue state immediately before any order.

    ANY NON-OWNER GATE NOT PASSING MEANS NO ORDER. Owner gates are reported
    separately so a failing evidence gate can never be mistaken for "just
    waiting on the owner".
    """
    from analysis.l5.lanes import lane_a_readiness as R
    v = R.arming_preflight(
        rows=rows, entry_price=entry_price, serving_artifact=serving_artifact,
        expected_artifact=SERVING_ARTIFACT, budget_permits=budget_permits,
        latch_set=latch_set, dual_key_set=dual_key_set,
        settlement_confirmed_markets=settlement_confirmed_markets)
    gates = list(v["gates"])

    def g(name, ok, blocker, detail=None):
        return {"gate": name,
                "state": ("PASS" if ok is True else
                          "FAIL" if ok is False else "UNDETERMINED"),
                "blocker": blocker, "detail": detail}

    # the R5F guard, and the pin on the frozen evidence
    gates.append(g("ACTIVE_RUNG_IS_R1",
                   None if active_rung is None else active_rung == REQUIRED_RUNG,
                   "active rung not supplied -- must be read from the "
                   "container's OWN log, not assumed"
                   if active_rung is None else
                   (None if active_rung == REQUIRED_RUNG else
                    f"active rung {active_rung} != {REQUIRED_RUNG}; a persisted "
                    f"R5F/R5FT campaign raises the per-market cap 150-600x"),
                   {"active_rung": active_rung, "required": REQUIRED_RUNG}))
    gates.append(g("MARKETS_ALLOWED_IS_ONE",
                   None if markets_allowed is None
                   else markets_allowed == REQUIRED_MARKETS_ALLOWED,
                   "markets_allowed not supplied" if markets_allowed is None
                   else (None if markets_allowed == REQUIRED_MARKETS_ALLOWED
                         else f"markets_allowed {markets_allowed} != 1"),
                   {"markets_allowed": markets_allowed}))
    gates.append(g("EVIDENCE_SHA_PINNED",
                   None if evidence_sha256 is None
                   else evidence_sha256.startswith(EVIDENCE_SHA256_PREFIX),
                   "evidence sha not supplied" if evidence_sha256 is None
                   else (None if evidence_sha256.startswith(EVIDENCE_SHA256_PREFIX)
                         else "frozen evidence has changed"),
                   {"expected_prefix": EVIDENCE_SHA256_PREFIX}))

    owner = {"SHUTDOWN_LATCH_CLEARED", "DUAL_KEY_ARMED"}
    non_owner_blockers = [x for x in gates
                          if x["state"] != "PASS" and x["gate"] not in owner]
    owner_blockers = [x for x in gates
                      if x["state"] != "PASS" and x["gate"] in owner]
    return {
        "label": LABEL, "scope": SCOPE,
        "pair_target": str(DEPLOYED_PAIR_TARGET),
        "pair_target_modified": PAIR_TARGET_MODIFIED,
        "envelope": ENVELOPE,
        "gates": gates,
        "non_owner_blockers": [{"gate": b["gate"], "state": b["state"],
                                "blocker": b["blocker"]}
                               for b in non_owner_blockers],
        "owner_blockers": [b["gate"] for b in owner_blockers],
        "ORDER_PERMITTED": not non_owner_blockers and not owner_blockers,
        "NO_ORDER_REASON": ("a non-owner gate is not PASS"
                            if non_owner_blockers else
                            ("awaiting owner gates" if owner_blockers else None)),
        "stop_after_first_market": STOP_AFTER_FIRST_MARKET,
        "stop_mechanism": STOP_MECHANISM,
        "labels": ("PREPARED FOR ARMING, NOT ARMED. Any non-owner gate not "
                   "PASS means NO ORDER. Nothing here clears a latch, arms a "
                   "key, deploys or orders."),
    }
