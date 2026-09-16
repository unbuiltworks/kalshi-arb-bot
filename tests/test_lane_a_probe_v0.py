"""LANE_A_EXECUTION_MEASUREMENT_PROBE_V0: prepared, pinned, and not armed."""
import pytest

from analysis.l5.lanes import first_probe_decision as F
from analysis.l5.lanes import lane_a_probe_v0 as P


def _rows():
    return F.snapshot()["spring_watch"]


# ------------------------------------------------- scope and pins

def test_the_deployed_pair_target_is_not_modified():
    from src import pmus_spring_l5_session as sess
    assert sess.SPRING_PAIR_TARGET == P.DEPLOYED_PAIR_TARGET
    assert P.PAIR_TARGET_MODIFIED is False
    assert P.SCOPE == "EXECUTION_AND_ACCOUNTING_MEASUREMENT_ONLY"


def test_evidence_and_artifact_are_pinned():
    assert F.snapshot()["sha256"].startswith(P.EVIDENCE_SHA256_PREFIX)
    assert P.SERVING_ARTIFACT == "6adcc4c43e67"


def test_the_envelope_is_unchanged_from_R1():
    from src import pmus_l5_ramp_ladder as ladder
    r = ladder.rung("R1")
    assert P.ENVELOPE["max_cumulative_notional_per_market_usd"] == r["per_market_cap_usd"]
    assert P.ENVELOPE["residual_units_bound"] == r["residual_units"]
    assert P.ENVELOPE["concurrent_markets"] == r["concurrency"]
    assert P.ENVELOPE["max_actions_per_session"] == r["max_actions_per_session"]
    assert P.ENVELOPE["restart"] == "NEVER"


# ------------------------------------------------- the R5F guard

def test_a_persisted_R5F_campaign_would_raise_the_cap_150x():
    """The reason ACTIVE_RUNG_IS_R1 is a hard gate and not a formality."""
    from src import pmus_l5_ramp_ladder as ladder
    from decimal import Decimal as D
    r1 = D(ladder.rung("R1")["per_market_cap_usd"])
    r5f = D(ladder.rung("R5F")["per_market_cap_usd"])
    r5ft = D(ladder.rung("R5FT")["per_market_cap_usd"])
    assert r5f / r1 == 150
    assert r5ft / r1 == 600
    assert ladder.rung("R5F")["markets_per_campaign"] is None   # CONTINUOUS


def test_a_wrong_rung_blocks_the_order():
    r = P.preorder_preflight(rows=_rows(), serving_artifact=P.SERVING_ARTIFACT,
                             active_rung="R5F", markets_allowed=1,
                             evidence_sha256=F.snapshot()["sha256"],
                             budget_permits=True, latch_set=False,
                             dual_key_set=True, settlement_confirmed_markets=34)
    assert r["ORDER_PERMITTED"] is False
    assert "ACTIVE_RUNG_IS_R1" in [b["gate"] for b in r["non_owner_blockers"]]


def test_markets_allowed_must_be_exactly_one():
    r = P.preorder_preflight(rows=_rows(), serving_artifact=P.SERVING_ARTIFACT,
                             active_rung="R1", markets_allowed=3,
                             evidence_sha256=F.snapshot()["sha256"],
                             budget_permits=True, latch_set=False,
                             dual_key_set=True, settlement_confirmed_markets=34)
    assert r["ORDER_PERMITTED"] is False
    assert "MARKETS_ALLOWED_IS_ONE" in [b["gate"] for b in r["non_owner_blockers"]]


def test_changed_frozen_evidence_blocks_the_order():
    r = P.preorder_preflight(rows=_rows(), serving_artifact=P.SERVING_ARTIFACT,
                             active_rung="R1", markets_allowed=1,
                             evidence_sha256="deadbeef" * 8,
                             budget_permits=True, latch_set=False,
                             dual_key_set=True, settlement_confirmed_markets=34)
    assert r["ORDER_PERMITTED"] is False


# ------------------------------------------------- no order, ever, from here

def test_any_non_owner_gate_failing_means_no_order():
    """And it must be distinguishable from merely waiting on the owner."""
    r = P.preorder_preflight(rows=_rows(), serving_artifact="WRONGSHA",
                             active_rung="R1", markets_allowed=1,
                             evidence_sha256=F.snapshot()["sha256"],
                             budget_permits=True, latch_set=False,
                             dual_key_set=True, settlement_confirmed_markets=34)
    assert r["ORDER_PERMITTED"] is False
    assert r["NO_ORDER_REASON"] == "a non-owner gate is not PASS"


def test_todays_state_blocks_on_owner_gates_and_unconfirmed_rung():
    r = P.preorder_preflight(rows=_rows(), serving_artifact=P.SERVING_ARTIFACT,
                             evidence_sha256=F.snapshot()["sha256"],
                             budget_permits=True, latch_set=True,
                             dual_key_set=False,
                             settlement_confirmed_markets=34)
    assert r["ORDER_PERMITTED"] is False
    assert set(r["owner_blockers"]) == {"SHUTDOWN_LATCH_CLEARED",
                                        "DUAL_KEY_ARMED"}
    # the rung cannot be read while the container is latched
    assert {"ACTIVE_RUNG_IS_R1", "MARKETS_ALLOWED_IS_ONE"} <= {
        b["gate"] for b in r["non_owner_blockers"]}


def test_all_gates_green_and_owner_gates_open_permits_an_order():
    """The only combination that returns True -- so the gate is real."""
    r = P.preorder_preflight(rows=_rows(), serving_artifact=P.SERVING_ARTIFACT,
                             active_rung="R1", markets_allowed=1,
                             evidence_sha256=F.snapshot()["sha256"],
                             budget_permits=True, latch_set=False,
                             dual_key_set=True, settlement_confirmed_markets=34)
    assert r["ORDER_PERMITTED"] is True
    assert r["NO_ORDER_REASON"] is None


def test_the_module_contains_no_order_verb():
    src = open(P.__file__).read()
    for verb in ("place_order", "submit_order", "cancel_order", "create_order"):
        assert f"def {verb}" not in src and f".{verb}(" not in src


# ------------------------------------------------- stop after one market

def test_the_probe_stops_after_the_first_market():
    assert P.STOP_AFTER_FIRST_MARKET is True
    ids = [s["id"] for s in P.stop_conditions()]
    assert "FIRST_MARKET_COMPLETE" in ids
    first = [s for s in P.stop_conditions()
             if s["id"] == "FIRST_MARKET_COMPLETE"][0]
    assert "Do NOT select a second market" in first["action"]


def test_every_required_stop_condition_is_defined_before_arming():
    ids = {s["id"] for s in P.stop_conditions()}
    assert {"UNEXPECTED_QUANTITY_OR_NOTIONAL", "UNKNOWN_ORDER_STATE",
            "RECONCILIATION_MISMATCH", "ARTIFACT_OR_CONFIG_MISMATCH",
            "RISK_ENVELOPE_BREACH", "UNEXPECTED_RESTART",
            "CANNOT_CANCEL"} <= ids


def test_success_is_defined_end_to_end_before_arming():
    stages = [s["stage"] for s in P.success_definition()]
    assert stages == ["submitted", "venue_acknowledged",
                      "filled_or_cleanly_terminal",
                      "exact_fee_rebate_attribution",
                      "venue_confirmed_settlement_if_filled",
                      "ledger_reconciliation"]


# ------------------------------------------------- owner gate isolation

def test_the_spring_dual_key_arms_nothing_else():
    """Verified over the source, not taken from a docstring."""
    import os
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(P.__file__)))
    root = os.path.dirname(os.path.dirname(root))
    out = subprocess.run(
        ["grep", "-rl", "--include=*.py", "PMUS_SPRING_LIVE",
         os.path.join(root, "src")],
        capture_output=True, text=True).stdout.split()
    names = {os.path.basename(f) for f in out}
    assert names, "grep found nothing -- the check would pass vacuously"
    assert names <= {"pmus_spring_l5_session.py", "pmus_spring_probe_readiness.py",
                     "pmus_spring_evidence_transfer.py"}, names


def test_neither_owner_gate_is_cleared_by_this_module():
    a = P.owner_gate_actions()
    assert a["neither_cleared_here"] is True
    assert a["both_required"] is True
    assert "WARNING" in "".join(a["SHUTDOWN_LATCH_CLEARED"].keys())
