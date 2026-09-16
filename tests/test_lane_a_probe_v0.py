"""LANE_A_EXECUTION_MEASUREMENT_PROBE_V0: prepared, pinned, and not armed."""
import pytest

from analysis.l5.lanes import first_probe_decision as F
from analysis.l5.lanes import lane_a_probe_v0 as P


def _rows():
    return F.snapshot()["spring_watch"]


# A lineage result that ACCEPTS. Shaped exactly like r1_source_lineage.verify()
# so a change to that shape shows up here rather than silently passing.
LINEAGE_OK = {"label": "R1_SOURCE_LINEAGE", "reviewed_artifact": "6adcc4c4",
              "copy_sources": 36, "identical": 32,
              "differing": ["src/pmus_spring_l5_session.py"],
              "absent_from_reviewed": [], "undisclosed": [],
              "forbidden_class": [], "ACCEPTANCE": True}


def _green(**over):
    """Every gate satisfiable, so a test can break exactly one of them."""
    kw = dict(rows=_rows(), serving_artifact=P.SERVING_ARTIFACT,
              active_rung="R1", markets_allowed=1,
              evidence_sha256=F.snapshot()["sha256"],
              budget_permits=True, latch_set=False, dual_key_set=True,
              settlement_confirmed_markets=34,
              start_command="exec python3 -m src.x",
              rearm_rung=P.SOURCE_REARM_RUNG, rearm_gate_enabled=False,
              lineage=LINEAGE_OK, dual_key_vars_unset=True)
    kw.update(over)
    return P.preorder_preflight(**kw)


def _blockers(r):
    return {b["gate"] for b in r["non_owner_blockers"]}


# ------------------------------------------------- scope and pins

def test_the_deployed_pair_target_is_not_modified():
    from src import pmus_spring_l5_session as sess
    assert sess.SPRING_PAIR_TARGET == P.DEPLOYED_PAIR_TARGET
    assert P.PAIR_TARGET_MODIFIED is False
    assert P.SCOPE == "EXECUTION_AND_ACCOUNTING_MEASUREMENT_ONLY"


def test_evidence_and_artifact_are_pinned():
    assert F.snapshot()["sha256"].startswith(P.EVIDENCE_SHA256_PREFIX)
    # SERVICE-SCOPED. The probe target moved to the isolated R1 service, so the
    # pin names that artifact -- and the legacy one is still recorded, so a
    # reader can see the pin was repointed at a real image and not at nothing.
    assert P.TARGET_SERVICE == "l5-r1-probe"
    assert P.SERVING_ARTIFACT == P.R1_SERVING_ARTIFACT == "f8b8fae2712b"
    assert P.LEGACY_SERVING_ARTIFACT == "6adcc4c43e67"
    assert P.LEGACY_SERVICE == "pmus-spring-l5-probe"


def test_moving_the_pin_did_not_drop_the_guarantee_it_carried():
    """A repointed identity pin is worthless unless something recomputes it.
    SOURCE_LINEAGE_ACCEPTED is that something, and it must be able to FAIL."""
    assert _green()["ORDER_PERMITTED"] is True
    bad = dict(LINEAGE_OK, ACCEPTANCE=False,
               undisclosed=["src/pmus_micro_executor.py"])
    r = _green(lineage=bad)
    assert r["ORDER_PERMITTED"] is False
    assert "SOURCE_LINEAGE_ACCEPTED" in _blockers(r)
    # and silence must not satisfy it
    assert "SOURCE_LINEAGE_ACCEPTED" in _blockers(_green(lineage=None))


def test_the_real_r1_image_lineage_accepts_against_the_reviewed_artifact():
    """Not a fixture: the actual COPY set, recomputed from git blobs."""
    from analysis.l5.lanes import r1_source_lineage as L
    v = L.verify()
    assert v["ACCEPTANCE"] is True, v
    assert v["undisclosed"] == [] and v["forbidden_class"] == []
    assert v["copy_sources"] > 0


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
    r = _green(active_rung="R5F")
    assert r["ORDER_PERMITTED"] is False
    assert "ACTIVE_RUNG_IS_R1" in _blockers(r)


def test_markets_allowed_must_be_exactly_one():
    r = _green(markets_allowed=3)
    assert r["ORDER_PERMITTED"] is False
    assert "MARKETS_ALLOWED_IS_ONE" in _blockers(r)


def test_changed_frozen_evidence_blocks_the_order():
    r = _green(evidence_sha256="deadbeef" * 8)
    assert r["ORDER_PERMITTED"] is False
    assert "EVIDENCE_SHA_PINNED" in _blockers(r)


# ------------------------------------------------- no order, ever, from here

def test_any_non_owner_gate_failing_means_no_order():
    """And it must be distinguishable from merely waiting on the owner."""
    r = _green(serving_artifact="WRONGSHA")
    assert r["ORDER_PERMITTED"] is False
    assert r["NO_ORDER_REASON"] == "a non-owner gate is not PASS"


def test_a_latched_container_blocks_on_the_owner_gate_and_the_unread_rung():
    r = P.preorder_preflight(rows=_rows(), serving_artifact=P.SERVING_ARTIFACT,
                             evidence_sha256=F.snapshot()["sha256"],
                             budget_permits=True, latch_set=True,
                             dual_key_set=False,
                             settlement_confirmed_markets=34)
    assert r["ORDER_PERMITTED"] is False
    assert set(r["owner_blockers"]) == {"SHUTDOWN_LATCH_CLEARED",
                                        "DUAL_KEY_ARMED"}
    # the rung cannot be read while the container is latched
    assert {"ACTIVE_RUNG_IS_R1", "MARKETS_ALLOWED_IS_ONE"} <= _blockers(r)


def test_todays_state_blocks_on_the_dual_key_alone():
    """The R1 service was never latched, so only the dual key is outstanding."""
    r = _green(dual_key_set=False)
    assert r["ORDER_PERMITTED"] is False
    assert r["non_owner_blockers"] == []
    assert r["owner_blockers"] == ["DUAL_KEY_ARMED"]
    assert r["NO_ORDER_REASON"] == "awaiting owner gates"


def test_all_gates_green_and_owner_gates_open_permits_an_order():
    """The only combination that returns True -- so the gate is real."""
    r = _green()
    assert r["ORDER_PERMITTED"] is True
    assert r["NO_ORDER_REASON"] is None


def test_omitting_any_new_gate_fails_closed():
    """Adding a gate must not be satisfiable by silence."""
    r = P.preorder_preflight(rows=_rows(), serving_artifact=P.SERVING_ARTIFACT,
                             active_rung="R1", markets_allowed=1,
                             evidence_sha256=F.snapshot()["sha256"],
                             budget_permits=True, latch_set=False,
                             dual_key_set=True, settlement_confirmed_markets=34)
    assert r["ORDER_PERMITTED"] is False


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


def test_no_owner_gate_is_cleared_by_this_module():
    a = P.owner_gate_actions()
    assert a["neither_cleared_here"] is True
    # The probe moved off the latched service, so the dual key is all that is
    # left -- and the legacy latch must be named as one to LEAVE ALONE.
    assert a["only_dual_key_remains"] is True
    assert a["both_required"] is False
    legacy = a["SHUTDOWN_LATCH_CLEARED"]["legacy_service_latch"]
    assert "DO NOT CLEAR THAT LATCH" in legacy
    assert "d84d9a68-2e5b-4f74-9694-5f422417c74f" in legacy
    # and the dual key is addressed to the R1 service, not the legacy one
    act = " ".join(a["DUAL_KEY_ARMED"]["action"])
    assert "6111a5d4-a2d9-4762-a057-31f02d07586f" in act
    assert "d84d9a68" not in act


# ------------------------------------------------- runtime identity

def test_a_start_command_that_mutates_source_blocks_the_order():
    """The commit hash describes the IMAGE, not what runs. This service's
    Railway startCommand overrides the image CMD and seds the source at boot."""
    sc = ('sh -c \'sed -i "s/^REARM_RUNG = \\"R5F\\"$/REARM_RUNG = \\"R5FT\\"/" '
          'src/pmus_l5_continuous_session.py && exec python3 -m '
          'src.pmus_l5_continuous_session\'')
    r = _green(start_command=sc)
    assert r["ORDER_PERMITTED"] is False
    assert "RUNTIME_IS_UNMUTATED_ARTIFACT" in _blockers(r)


def test_a_clean_start_command_passes_the_runtime_gate():
    assert _green(start_command="exec python3 -m src.x")["ORDER_PERMITTED"]


def test_a_rewritten_rearm_rung_blocks_the_order():
    """R5FT is exactly what the legacy boot `sed -i` produced. The constant in
    unmutated source is R5F, so anything else means the runtime was rewritten
    -- including when the start command itself looks innocent."""
    for bad in ("R5FT", "R2C", "R1"):
        r = _green(rearm_rung=bad)
        assert r["ORDER_PERMITTED"] is False, bad
        assert "REARM_RUNG_UNMUTATED_AND_INERT" in _blockers(r), bad


def test_the_unmutated_rearm_rung_passes_because_the_gate_is_inert():
    """PMUS_SPRING_REARM_GATE unset => boot_assertions() never runs => the
    constant gates nothing. The previous gate compared it to R1 and so could
    never pass on any real build; that was the bug."""
    assert P.SOURCE_REARM_RUNG == "R5F"
    from src import pmus_l5_continuous_session as C
    assert C.REARM_RUNG == P.SOURCE_REARM_RUNG      # the claim, not a fixture
    assert C.rearm_gate_enabled() is False          # dark unless the owner arms it
    assert _green(rearm_gate_enabled=False)["ORDER_PERMITTED"] is True


def test_enabling_the_rearm_gate_on_this_rung_blocks_the_order():
    """Fail-closed at boot (rearm_refused, hold halted) is still a probe that
    never submits, so it blocks here rather than being found at arming time."""
    r = _green(rearm_gate_enabled=True)
    assert r["ORDER_PERMITTED"] is False
    assert "REARM_RUNG_UNMUTATED_AND_INERT" in _blockers(r)


def test_unprovable_dual_key_vars_block_the_order():
    """If PMUS_SPRING_LIVE/_ARM/_SESSION_ID already hold arming values,
    clearing the latch ALONE arms the session."""
    r = _green(dual_key_vars_unset=None)
    assert r["ORDER_PERMITTED"] is False
    assert "DUAL_KEY_VARS_PROVEN_UNSET" in _blockers(r)
