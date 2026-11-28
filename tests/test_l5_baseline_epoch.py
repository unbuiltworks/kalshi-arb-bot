"""The evidence-backed L5 baseline epoch and the L5-scoped loss ceilings."""
from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_live_execution as X             # noqa: E402
from src import l5_live_validation_gates as VG     # noqa: E402

SRC = "AUTHORITATIVE_ACCOUNT_READS"


def _store(tmp_path, realized="-63.30"):
    s = X.InventoryStore(str(tmp_path / "s.json"))
    s.state["realized_attributed"] = realized
    s.state["realized_unattributed"] = "0"
    s.state["realized_today"] = realized
    s.state["leg_cost_basis_unknown"] = ["mA|DOWN", "mB|UP"]
    return s


def _ok(s, **kw):
    args = dict(cash_usd="347.98215", l5_open_orders=[], positions={},
                outstanding_obligations_usd="0", source=SRC)
    args.update(kw)
    return s.establish_l5_baseline(**args)


# ------------------------------------------------- it is NOT a claim of zero
def test_the_baseline_preserves_pre_t_history_verbatim(tmp_path):
    s = _store(tmp_path)
    out = _ok(s)
    assert out["established"] is True
    b = out["baseline"]
    assert b["pre_baseline_realized_total"] == "-63.30"
    assert b["pre_baseline_realized_attributed"] == "-63.30"
    assert b["pre_baseline_unknown_basis_keys"] == ["mA|DOWN", "mB|UP"]
    # history itself is untouched
    assert s.state["realized_attributed"] == "-63.30"
    assert s.realized_lines()["realized_attributed"] == "-63.30"
    # and still never presented as verified L5
    assert s.realized_lines()["verified_l5_realized_net"] is None
    assert s.realized_lines()["verified_l5_status"] == \
        "UNPROVEN_LEGACY_ATTRIBUTION"


def test_without_a_baseline_the_scoped_measure_is_none_not_zero(tmp_path):
    s = _store(tmp_path)
    assert s.l5_realized_since_baseline() is None
    assert s.l5_baseline_lines()["l5_realized_since_baseline"] is None


def test_with_a_baseline_and_no_l5_settlement_the_measure_is_a_counted_zero(
        tmp_path):
    s = _store(tmp_path)
    _ok(s)
    assert s.l5_realized_since_baseline() == D(0)
    lines = s.l5_baseline_lines()
    assert lines["attested_l5_settlements_since_baseline"] == []
    assert lines["post_baseline_settlements_NOT_attested"] == []
    assert lines["pre_baseline_realized_total"] == "-63.30"


# ------------------------------------------------------- required evidence
@pytest.mark.parametrize("kw,reason", [
    ({"source": "somewhere"}, "BASELINE_SOURCE_INVALID"),
    ({"cash_usd": None}, "BASELINE_READS_UNAVAILABLE"),
    ({"cash_usd": "nonsense"}, "BASELINE_READS_UNAVAILABLE"),
    ({"outstanding_obligations_usd": None}, "BASELINE_READS_UNAVAILABLE"),
    ({"l5_open_orders": None}, "BASELINE_READS_UNAVAILABLE"),
    ({"positions": None}, "BASELINE_READS_UNAVAILABLE"),
])
def test_missing_or_bad_evidence_refuses_a_baseline(tmp_path, kw, reason):
    s = _store(tmp_path)
    out = _ok(s, **kw)
    assert out["established"] is False and out["reason"] == reason
    assert "l5_baseline" not in s.state
    assert s.l5_realized_since_baseline() is None


def test_an_l5_order_still_working_refuses_a_baseline(tmp_path):
    s = _store(tmp_path)
    out = _ok(s, l5_open_orders=["A1"])
    assert out["reason"] == "L5_ORDERS_STILL_OPEN_AT_BASELINE"
    assert out["orders"] == ["A1"]


def test_the_baseline_is_struck_once(tmp_path):
    s = _store(tmp_path)
    _ok(s)
    again = _ok(s, cash_usd="999")
    assert again["established"] is False
    assert again["reason"] == "BASELINE_ALREADY_SET"
    assert s.state["l5_baseline"]["cash_usd"] == "347.98215"


def test_the_baseline_survives_a_reload(tmp_path):
    s = _store(tmp_path)
    _ok(s)
    again = X.InventoryStore(str(tmp_path / "s.json"))
    assert again.l5_realized_since_baseline() == D(0)


# ------------------------------------- post-T attribution is never assumed
def test_only_attested_l5_settlements_after_t_are_counted(tmp_path):
    s = _store(tmp_path)
    _ok(s)
    t0 = s.state["l5_baseline"]["t"]
    s.state["retired_markets"] = {
        "pre": {"t": "2000-01-01T00:00:00+00:00", "settlement_pnl": "-9.00",
                "l5_owner_attribution_verified": True},
        "postL5": {"t": "2999-01-01T00:00:00+00:00", "settlement_pnl": "-1.25",
                   "l5_owner_attribution_verified": True,
                   "pnl_provenance": "VERIFIED_L5_GROSS_SETTLEMENT"},
        "postManual": {"t": "2999-01-01T00:00:00+00:00",
                       "settlement_pnl": "-40.00",
                       "l5_owner_attribution_verified": False,
                       "pnl_provenance": "UNKNOWN_COST_UPPER_BOUND"},
    }
    assert t0 < "2999-01-01T00:00:00+00:00"
    assert s.l5_realized_since_baseline() == D("-1.25")   # manual excluded
    lines = s.l5_baseline_lines()
    assert [r["market"] for r in
            lines["attested_l5_settlements_since_baseline"]] == ["postL5"]
    # THE PROPERTY THAT MATTERS: an unattributable post-T settlement is
    # surfaced, never silently read as an L5 zero
    assert [r["market"] for r in
            lines["post_baseline_settlements_NOT_attested"]] == ["postManual"]


def test_an_unreadable_settlement_makes_the_scoped_sum_unknown(tmp_path):
    s = _store(tmp_path)
    _ok(s)
    s.state["retired_markets"] = {
        "bad": {"t": "2999-01-01T00:00:00+00:00", "settlement_pnl": "oops",
                "l5_owner_attribution_verified": True}}
    assert s.l5_realized_since_baseline() is None


# ------------------------------------------------------------- the gates
def _gate(**kw):
    args = dict(max_cash_deployed_usd=D("7"), max_unmatched_contracts=D("5"),
                max_cumulative_loss_usd=D("20"),
                max_l5_daily_loss_usd=D("5"),
                max_l5_cumulative_loss_usd=D("20"),
                cash_policy=VG.NO_RECYCLING)
    args.update(kw)
    return VG.ValidationGates(**args)


def _refuse(g, **kw):
    args = dict(order_qty=D("1"), order_price=D("0.44"),
                order_intent="BUY_LONG", filled_legs={}, outstanding=[],
                book_uncertain=False, unmatched_after=D(0),
                cumulative_realized=D(0), cumulative_unrealized_loss=D(0),
                available_cash_usd=D("347"))
    args.update(kw)
    return g.refuse(**args)


def test_a_configured_l5_ceiling_with_no_scoped_figure_refuses():
    """MISSING IS NOT ZERO, at the gate too."""
    assert _refuse(_gate(), l5_scoped_realized=None) == \
        "L5_SCOPED_REALIZED_UNAVAILABLE"


def test_the_account_wide_check_still_runs_first_and_is_not_replaced():
    """OWNER 2026-10-16: no silent replacement of the account-wide control."""
    r = _refuse(_gate(), cumulative_realized=D("-63.30"),
                l5_scoped_realized=D(0))
    assert r is not None and r.startswith("CUMULATIVE_LOSS_LIMIT")


def test_a_counted_l5_zero_passes_the_scoped_ceilings():
    assert _refuse(_gate(), l5_scoped_realized=D(0)) is None


def test_the_l5_daily_ceiling_binds_at_five():
    assert _refuse(_gate(), l5_scoped_realized=D("-4.99")) is None
    r = _refuse(_gate(), l5_scoped_realized=D("-5.00"))
    assert r == "L5_DAILY_LOSS_LIMIT:5.00>=5"


def test_the_authorized_worst_case_fits_under_the_scoped_daily_ceiling():
    """-3.60 is the authorized worst case; 5 leaves 1.40 of headroom."""
    assert _refuse(_gate(), l5_scoped_realized=D("-3.60")) is None
    assert _refuse(_gate(), l5_scoped_realized=D("-3.60"),
                   cumulative_unrealized_loss=D("1.40")) == \
        "L5_DAILY_LOSS_LIMIT:5.00>=5"


def test_the_scoped_ceilings_are_absent_unless_configured():
    g = VG.ValidationGates(max_cash_deployed_usd=D("7"),
                           max_unmatched_contracts=D("5"),
                           max_cumulative_loss_usd=D("20"),
                           cash_policy=VG.NO_RECYCLING)
    assert g.max_l5_daily_loss_usd is None
    assert _refuse(g, l5_scoped_realized=None) is None   # not configured


def test_the_runner_baseline_caller_is_gated_and_reads_before_striking():
    """The caller must be env-gated, use authoritative reads, and run where
    the transport exists.

    REWRITTEN FOR THE DEFERRED QUEUE. The baseline is no longer struck
    inside `roll`: it made two venue reads on the window's opening, where
    the cost-basis audit beside it was spending 93 seconds (issue #195).
    `roll` now ENQUEUES it and the tick drains it after every trading stage.

    The ordering property survives and is checked on the enqueue, which is
    still between the transport and the executor, plus two new ones: `roll`
    must not strike the baseline itself, and the job must read the LIVE
    transport rather than one captured when it was queued.
    """
    import inspect
    from src import l5_pmus_live_runner as RUN
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "src/l5_pmus_live_runner.py")).read()
    t = src.index("self.transport = T.PmusTransport(")
    b = src.index("self.enqueue_roll_diagnostics()")
    e = src.index("self.executor = LX.FaithfulExecutor(")
    assert t < b < e, "the baseline must be queued after the transport exists"
    assert 'os.getenv("L5_ESTABLISH_BASELINE"' in src
    roll_src = inspect.getsource(RUN.Runner.roll)
    assert "establish_l5_baseline" not in roll_src
    step = inspect.getsource(RUN.Runner._establish_baseline_step)
    assert "self.transport.account()" in step
    assert "self.transport.positions()" in step
    assert ".account()" in src and ".positions()" in src
    # obligations must NOT come from the not-yet-built executor
    assert "self.executor_cash_legs()" not in src
    assert "self.store.committed()" in src
    # ownership from our OWN record, not account-wide open orders
    assert "self.store.live_orders()" in src
    # and the gates stay opt-in
    assert '"L5_MAX_L5_DAILY_LOSS_USD"' in src
    assert '"L5_MAX_L5_CUMULATIVE_LOSS_USD"' in src


# ---------------------------------- L5-ONLY flatness, manual stays untouched
def _prov(s, market):
    s.state.setdefault("fill_provenance", {})[market] = {
        "legs": {}, "entries": [{"coid": "ours1", "l5_owner_verified": True}]}


def test_manual_positions_do_not_block_the_baseline(tmp_path):
    """OWNER 2026-10-16: the owner must not be asked to pause trading."""
    s = _store(tmp_path)
    pos = {"cpc-btc-updown-1d-2026-10-15-2100z|DOWN": "2.04",
           "cpcxrp-xrp-updown-15m-2026-10-16-0215z|DOWN": "5.28"}
    out = _ok(s, positions=pos)
    assert out["established"] is True
    b = out["baseline"]
    # snapshotted, and explicitly NOT L5's
    assert b["manual_or_unknown_positions_at_baseline"] == {
        "cpc-btc-updown-1d-2026-10-15-2100z|DOWN": "2.04",
        "cpcxrp-xrp-updown-15m-2026-10-16-0215z|DOWN": "5.28"}
    assert b["l5_owned_positions_at_baseline"] == {}
    assert "MANUAL_OR_UNKNOWN" in b["manual_inventory_labels"]
    # and the L5 measure starts from a counted zero, not from their P&L
    assert s.l5_realized_since_baseline() == D(0)


def test_an_unresolved_l5_owned_position_still_blocks(tmp_path):
    s = _store(tmp_path)
    _prov(s, "mL5")
    out = _ok(s, positions={"mL5|UP": "2", "manualMkt|DOWN": "9"})
    assert out["established"] is False
    assert out["reason"] == "L5_POSITIONS_UNRESOLVED_AT_BASELINE"
    assert out["l5_owned_positions"] == {"mL5|UP": "2"}
    assert "l5_baseline" not in s.state


def test_a_settled_l5_leg_at_zero_does_not_block(tmp_path):
    s = _store(tmp_path)
    _prov(s, "mL5")
    out = _ok(s, positions={"mL5|UP": "0"})
    assert out["established"] is True
    assert out["baseline"]["l5_owned_positions_at_baseline"] == {"mL5|UP": "0"}


def test_an_unreadable_l5_quantity_blocks_rather_than_reading_as_zero(
        tmp_path):
    s = _store(tmp_path)
    _prov(s, "mL5")
    out = _ok(s, positions={"mL5|UP": "nonsense"})
    assert out["reason"] == "L5_POSITIONS_UNRESOLVED_AT_BASELINE"


def test_outstanding_l5_obligations_block_the_baseline(tmp_path):
    s = _store(tmp_path)
    out = _ok(s, outstanding_obligations_usd="2.50")
    assert out["established"] is False
    assert out["reason"] == "L5_OBLIGATIONS_OUTSTANDING_AT_BASELINE"
    assert out["outstanding_obligations_usd"] == "2.50"


def test_unknown_ownership_is_never_promoted_to_l5(tmp_path):
    """Absence of a provenance record must not make a leg L5's."""
    s = _store(tmp_path)
    split = s.classify_inventory_ownership({"noRecord|UP": "3"})
    assert split["l5_owned"] == {}
    assert split["manual_or_unknown"] == {"noRecord|UP": "3"}


def test_post_t_manual_settlement_is_excluded_and_surfaced(tmp_path):
    """A manual position open at T settles after T and must not count."""
    s = _store(tmp_path)
    _ok(s, positions={"manualMkt|DOWN": "2.04"})
    s.state["retired_markets"] = {
        "manualMkt": {"t": "2999-01-01T00:00:00+00:00",
                      "settlement_pnl": "-2.04",
                      "l5_owner_attribution_verified": False,
                      "pnl_provenance": "UNKNOWN_COST_UPPER_BOUND"}}
    assert s.l5_realized_since_baseline() == D(0)      # not -2.04
    lines = s.l5_baseline_lines()
    assert [r["market"] for r in
            lines["post_baseline_settlements_NOT_attested"]] == ["manualMkt"]


# ------------------------- the production response shape that refused at 02:39
def test_the_production_balances_shape_yields_cash_not_none():
    """THE DEFECT PRODUCTION FOUND. /v1/account/balances returns `balances`
    as a LIST carrying buyingPower. The first baseline read looked for
    availableBalance/cash/buyingPower on the TOP-LEVEL dict, never inside
    balances[], so cash was None and the baseline refused
    BASELINE_READS_UNAVAILABLE at 02:39:36Z with venue_positions_read=true."""
    from decimal import Decimal
    from src import l5_pmus_transport as T
    prod = {"balances": [{"buyingPower": "347.47255"}]}
    assert T.PmusTransport.buying_power(prod) == Decimal("347.47255")
    # the shape my first version assumed simply is not there
    assert prod.get("availableBalance") is None
    # older shapes still accepted
    assert T.PmusTransport.buying_power(
        {"balances": {"buyingPower": "12.5"}}) == Decimal("12.5")
    assert T.PmusTransport.buying_power({"buyingPower": "9"}) == Decimal("9")
    # and a genuinely unreadable balance is still None -- not substituted
    assert T.PmusTransport.buying_power({"balances": []}) is None
    assert T.PmusTransport.buying_power({}) is None


def test_the_runner_uses_the_proven_parser_not_guessed_key_names():
    """Read off the baseline step itself rather than off a byte window of
    the file: the block moved out of `roll` into its own deferred job, and
    a character offset is not what this test is about."""
    import inspect
    from src import l5_pmus_live_runner as RUN
    tail = inspect.getsource(RUN.Runner._establish_baseline_step)
    assert "T.PmusTransport.buying_power(acct)" in tail
    # the guessed-key list must be gone from the baseline block
    assert '"availableBalance", "available", "cash"' not in tail


def test_an_unreadable_cash_still_refuses_the_baseline(tmp_path):
    """Validation is not weakened: None cash still refuses."""
    s = _store(tmp_path)
    out = _ok(s, cash_usd=None)
    assert out["established"] is False
    assert out["reason"] == "BASELINE_READS_UNAVAILABLE"
    assert "l5_baseline" not in s.state
