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
