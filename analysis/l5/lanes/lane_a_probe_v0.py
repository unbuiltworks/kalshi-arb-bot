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

THE TARGET SERVICE MOVED, AND THE PIN HAD TO MOVE WITH IT. This module was
written against `pmus-spring-l5-probe` / artifact 6adcc4c43e67. OWNER DECISION
2026-09-16 P0 then put that service permanently out of scope -- it stays HALTED
and its latch is not to be cleared -- so the probe runs on the isolated
`l5-r1-probe` service instead. That service can never serve 6adcc4c43e67: it is
a different Dockerfile producing a different image.

Re-anchoring an identity pin is exactly the move that turns a safety check into
a rubber stamp, so the guarantee is not dropped, it is RELOCATED. The pin said
"the runtime is the artifact that was reviewed". For the R1 service that claim
is carried by SOURCE_LINEAGE_ACCEPTED, which recomputes the image's whole COPY
set against 6adcc4c4 blob-by-blob and fails on any undisclosed delta or any
delta of class EXECUTION/DECISION/SIZING/SUBMISSION. The pin alone was weaker
than what replaces it.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Optional

LABEL = "LANE_A_EXECUTION_MEASUREMENT_PROBE_V0"
SCOPE = "EXECUTION_AND_ACCOUNTING_MEASUREMENT_ONLY"

# Pinned. A mismatch on any of these is a STOP, never a warning.
EVIDENCE_SHA256_PREFIX = "43da9ac5"
REQUIRED_RUNG = "R1"
REQUIRED_MARKETS_ALLOWED = 1

# SERVICE-SCOPED artifact identity. Both entries are kept: the legacy one so a
# reader can see what the pin used to name and confirm it was not quietly
# repointed at nothing, and the R1 one because that is what now serves.
LEGACY_SERVICE = "pmus-spring-l5-probe"
LEGACY_SERVING_ARTIFACT = "6adcc4c43e67"    # HALTED, latched, OUT OF SCOPE
R1_SERVICE = "l5-r1-probe"
R1_SERVING_ARTIFACT = "f8b8fae2712b"
TARGET_SERVICE = R1_SERVICE
SERVING_ARTIFACT = R1_SERVING_ARTIFACT

# REARM_RUNG as it stands in UNMUTATED repo source. The legacy service's
# Railway startCommand rewrote it to "R5FT" with `sed -i` at boot, which is the
# whole reason a re-arm-rung gate exists at all.
SOURCE_REARM_RUNG = "R5F"

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
            "action": ("NOTHING TO DO, AND NOTHING TO TOUCH. The probe no "
                       "longer runs on the legacy service, so there is no latch "
                       "to clear: PMUS_SPRING_SHUTDOWN was never set on "
                       "l5-r1-probe (6111a5d4-a2d9-4762-a057-31f02d07586f) and "
                       "the container proves it by running the campaign loop "
                       "instead of shutdown_idle."),
            "legacy_service_latch": (
                "pmus-spring-l5-probe (d84d9a68-2e5b-4f74-9694-5f422417c74f) "
                "KEEPS its PMUS_SPRING_SHUTDOWN latch. OWNER DECISION 2026-09-16 "
                "P0 supersedes the earlier authorization to clear it. DO NOT "
                "CLEAR THAT LATCH."),
            "read_as": 'os.getenv("PMUS_SPRING_SHUTDOWN","").strip().upper() '
                       'in ("1","YES","TRUE")',
            "why_the_move_removes_the_R5F_hazard": (
                "the hazard was _apply_rung() reading DURABLE STATE on "
                "/state/pmus-spring, where a persisted R5F/R5FT campaign carries "
                "a $7.50/$30.00 per-market cap. l5-r1-probe mounts its own empty "
                "volume at /state/l5-r1-probe and its first campaign_start reads "
                "previous_campaigns: [], so there is no campaign to inherit. "
                "ACTIVE_RUNG_IS_R1 is still read from the container's own log."),
        },
        "DUAL_KEY_ARMED": {
            "action": ["on service 6111a5d4-a2d9-4762-a057-31f02d07586f "
                       "(l5-r1-probe) ONLY:",
                       "PMUS_SPRING_LIVE=YES",
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
            "also_required_before_a_candidate_exists": (
                "PMUS_SPRING_EVIDENCE_STATUS_URL is UNSET on l5-r1-probe, so the "
                "strict-evidence gate reports status_unavailable and the funnel "
                "sees universe_rows: 0. The probe cannot select a market until "
                "the owner points it at a status source; that is an additional "
                "owner action, not a defect."),
            "restart_policy_consequence": (
                "restartPolicyType is NEVER, so setting these variables "
                "redeploys the service once and the probe gets exactly one "
                "process. It does not relaunch itself after a crash or after "
                "the first market completes."),
        },
        "both_required": False,
        "only_dual_key_remains": True,
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
         "trigger": f"serving artifact != {SERVING_ARTIFACT} on "
                    f"{TARGET_SERVICE}, active rung != "
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
                       start_command: Optional[str] = None,
                       rearm_rung: Optional[str] = None,
                       rearm_gate_enabled: Optional[bool] = None,
                       lineage: Optional[Dict[str, Any]] = None,
                       dual_key_vars_unset: Optional[bool] = None,
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

    # RUNTIME IDENTITY. The Railway startCommand OVERRIDES the image CMD, and
    # on this service it runs `sed -i` against src/pmus_l5_continuous_session.py
    # at boot to rewrite REARM_RUNG from "R5F" to "R5FT". A container whose
    # source is patched at boot is NOT the artifact that was reviewed, however
    # well the commit hash matches -- the hash describes the image, not what
    # runs. Verifying 6adcc4c43e67 and stopping there would have missed it.
    gates.append(g("RUNTIME_IS_UNMUTATED_ARTIFACT",
                   None if start_command is None else
                   not any(tok in start_command for tok in ("sed -i", "sed -e",
                                                            "patch ", "> src/")),
                   "start command not supplied" if start_command is None else
                   (None if not any(tok in start_command
                                    for tok in ("sed -i", "sed -e", "patch ",
                                                "> src/"))
                    else "the start command MUTATES SOURCE at boot; the running "
                         "code is not the reviewed artifact"),
                   {"start_command": start_command}))
    # THE PREVIOUS VERSION OF THIS GATE WAS UNSATISFIABLE, and is corrected
    # here. It compared the running REARM_RUNG against REQUIRED_RUNG ("R1"),
    # but REARM_RUNG is "R5F" in every unmutated build of this repo -- so the
    # gate blocked the correct artifact and the sed-mutated one identically and
    # had no discriminating power at all. The two real questions are:
    #
    #   (a) has the constant been MUTATED? "R5FT" is precisely what the legacy
    #       boot `sed -i` produced, so anything other than SOURCE_REARM_RUNG is
    #       a rewritten runtime. This gate KEEPS that failure and gains the
    #       ability to pass the clean artifact.
    #   (b) can it RAISE the rung? Only if boot_assertions() runs, and that
    #       needs PMUS_SPRING_REARM_GATE set. Unset, REARM_RUNG is inert. Set,
    #       with REARM_RUNG != the active rung, the boot logs rearm_refused and
    #       HOLDS HALTED -- fail-closed, but the probe would then never submit,
    #       so it still blocks here rather than being found at arming time.
    _rearm_mutated = (rearm_rung is not None and rearm_rung != SOURCE_REARM_RUNG)
    _rearm_would_refuse = bool(rearm_gate_enabled) and rearm_rung != active_rung
    gates.append(g("REARM_RUNG_UNMUTATED_AND_INERT",
                   None if rearm_rung is None else
                   (not _rearm_mutated and not _rearm_would_refuse),
                   "rearm rung not supplied" if rearm_rung is None else
                   (f"REARM_RUNG is {rearm_rung}, not the unmutated "
                    f"{SOURCE_REARM_RUNG}; the running source has been "
                    f"rewritten" if _rearm_mutated else
                    (f"PMUS_SPRING_REARM_GATE is enabled and REARM_RUNG "
                     f"{rearm_rung} != active rung {active_rung}; boot would "
                     f"log rearm_refused and hold halted"
                     if _rearm_would_refuse else None)),
                   {"rearm_rung": rearm_rung,
                    "unmutated_value": SOURCE_REARM_RUNG,
                    "rearm_gate_enabled": bool(rearm_gate_enabled),
                    "inert": not bool(rearm_gate_enabled),
                    "active_rung": active_rung}))
    # What the artifact pin used to assert, recomputed rather than asserted.
    gates.append(g("SOURCE_LINEAGE_ACCEPTED",
                   None if lineage is None else bool(lineage.get("ACCEPTANCE")),
                   "source lineage not supplied" if lineage is None else
                   (None if lineage.get("ACCEPTANCE") else
                    "the image COPY set differs from the reviewed artifact in "
                    "an undisclosed or execution-class file"),
                   None if lineage is None else
                   {"reviewed_artifact": lineage.get("reviewed_artifact"),
                    "copy_sources": lineage.get("copy_sources"),
                    "identical": lineage.get("identical"),
                    "differing": lineage.get("differing"),
                    "undisclosed": lineage.get("undisclosed"),
                    "forbidden_class": lineage.get("forbidden_class")}))
    # Any of these present as a SERVICE VARIABLE can arm the moment the latch
    # clears, because armed() reads exactly them and the latch is the only
    # thing currently holding it.
    gates.append(g("DUAL_KEY_VARS_PROVEN_UNSET",
                   None if dual_key_vars_unset is None else dual_key_vars_unset,
                   "cannot establish that PMUS_SPRING_LIVE/_ARM/_SESSION_ID are "
                   "unset; if they already hold arming values, clearing the "
                   "latch ALONE arms the session"
                   if dual_key_vars_unset is None else
                   (None if dual_key_vars_unset else
                    "dual-key variables hold arming values")))

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
