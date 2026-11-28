"""The immutable-baseline account-wide delta-loss policy.

OWNER 2026-10-16 required proof of three things, and they are the three
sections at the end of this file:

  * historical losses cannot be silently erased;
  * the baseline cannot be moved opportunistically;
  * new losses trip the ceiling at its configured threshold.

The policy is DEVELOPED, NOT ACTIVATED: the ceiling defaults to None, which
makes the check inert, and a test asserts that.
"""
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_live_validation_gates as G

T0 = "2026-10-16T02:45:12.565947+00:00"
PRE = "2026-10-15T19:20:48.000000+00:00"
POST = "2026-10-16T03:33:39.000000+00:00"


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "inv.json"))
        s.state["l5_baseline"] = {"t": T0,
                                  "pre_baseline_realized_total": "-79.01000000"}
        s.state["retired_markets"] = {
            "pre-mkt": {"t": PRE, "yes_value": "1.0000", "up_qty": "1",
                        "dn_qty": "0", "cost_total": "1.00",
                        "settlement_pnl": "0"},
            "post-mkt": {"t": POST, "yes_value": "1.0000", "up_qty": "1",
                         "dn_qty": "0", "cost_total": "1.00",
                         "settlement_pnl": "0"},
        }
        # realized_today() is attributed + unattributed, and
        # correct_cost_basis updates BOTH keys, so a fixture that moves only
        # one is not a state the verb can produce.
        s.state["realized_today"] = "-233.98540000"
        s.state["realized_attributed"] = "-233.98540000"
        s.state["realized_unattributed"] = "0"
        yield s


def _set_realized(store, value):
    """Move realized the way the verb does: both lines together."""
    store.state["realized_today"] = value
    store.state["realized_attributed"] = value


def _gates(**kw):
    base = dict(max_cash_deployed_usd=D("1000"),
                max_cumulative_loss_usd=D("1000"))
    base.update(kw)
    return G.ValidationGates(**base)


def _refuse(gates, **kw):
    args = dict(order_qty=D("0.01"), order_price=D("0.31"),
                order_intent="UP", filled_legs={}, outstanding={},
                unmatched_after=D("0"), cumulative_realized=D("0"),
                cumulative_unrealized_loss=D("0"), windows_used=0,
                book_uncertain=False, available_cash_usd=D("1000"))
    args.update(kw)
    return gates.refuse(**args)


# -- the read -------------------------------------------------------------

def test_the_delta_is_realized_minus_the_frozen_anchor(store):
    assert store.account_wide_delta_realized() == D("-154.97540000")


def test_no_baseline_is_unknown_not_zero(store):
    store.state["l5_baseline"] = {}
    assert store.account_wide_delta_realized() is None


def test_an_unreadable_anchor_is_unknown(store):
    for bad in (None, "", "abc", "NaN"):
        store.state["l5_baseline"]["pre_baseline_realized_total"] = bad
        assert store.account_wide_delta_realized() is None, bad


def test_lifetime_reporting_is_preserved_alongside_the_delta(store):
    lines = store.account_wide_delta_lines()
    assert lines["account_wide_realized_lifetime"] == "-233.98540000"
    assert lines["account_wide_delta_realized"] == "-154.97540000"
    assert lines["baseline_anchor_realized"] == "-79.01000000"


# -- 1. HISTORICAL LOSSES CANNOT BE SILENTLY ERASED -----------------------

def test_a_pre_baseline_correction_does_not_create_post_baseline_headroom(store):
    """The trap. A pre-T restatement raises realized_today() without anything
    happening after T, so the naive subtraction would credit it to the post-T
    window. Measured on the real account the naive delta reads +54.68840000
    where the true figure is +6.41460000 -- a $48.27380000 overstatement."""
    _set_realized(store, "-24.32160000")
    store.state["cost_basis_corrections"] = [
        {"id": "a", "market": "pre-mkt", "delta": "48.27380000"},
        {"id": "b", "market": "post-mkt", "delta": "161.39000000"},
    ]
    naive = D("-24.32160000") - D("-79.01000000")
    assert naive == D("54.68840000"), "the naive figure this must NOT return"
    assert store.account_wide_delta_realized() == D("6.41460000")


def test_the_lifetime_figure_still_shows_the_whole_history_after_correction(store):
    _set_realized(store, "-24.32160000")
    store.state["cost_basis_corrections"] = [
        {"id": "a", "market": "pre-mkt", "delta": "48.27380000"}]
    lines = store.account_wide_delta_lines()
    assert lines["account_wide_realized_lifetime"] == "-24.32160000"
    assert lines["baseline_anchor_realized"] == "-79.01000000"
    assert lines["cost_basis_corrections"] == 1


def test_a_correction_whose_period_cannot_be_determined_fails_closed(store):
    for bad in ({"id": "x", "market": "ghost", "delta": "1"},
                {"id": "x", "market": "pre-mkt", "delta": None},
                {"id": "x", "market": "pre-mkt"},
                {"id": "x", "delta": "1"},
                "not-a-record"):
        store.state["cost_basis_corrections"] = [bad]
        assert store.account_wide_delta_realized() is None, bad


def test_a_retired_row_with_no_timestamp_fails_closed(store):
    store.state["retired_markets"]["pre-mkt"].pop("t")
    store.state["cost_basis_corrections"] = [
        {"id": "a", "market": "pre-mkt", "delta": "1"}]
    assert store.account_wide_delta_realized() is None


# -- 2. THE BASELINE CANNOT BE MOVED OPPORTUNISTICALLY --------------------

def test_the_baseline_is_write_once(store):
    out = store.establish_l5_baseline(
        cash_usd="100", l5_open_orders=[], positions=[],
        outstanding_obligations_usd="0",
        source=store.L5_BASELINE_SOURCE)
    assert out["established"] is False
    assert out["reason"] == "BASELINE_ALREADY_SET"
    assert store.state["l5_baseline"]["t"] == T0


def test_the_baseline_requires_the_exact_source_token(store):
    store.state["l5_baseline"] = {}
    for bad in ("", "manual", "owner", None, store.L5_BASELINE_SOURCE + "x"):
        out = store.establish_l5_baseline(
            cash_usd="100", l5_open_orders=[], positions=[],
            outstanding_obligations_usd="0", source=bad)
        assert out["established"] is False, bad
        assert out["reason"] == "BASELINE_SOURCE_INVALID"


def test_moving_the_baseline_forward_would_not_help_because_it_cannot_move(store):
    """An opportunistic re-strike after a loss would reset the delta to zero.
    It is refused, and the original epoch is still the one on record."""
    _set_realized(store, "-500.00")
    out = store.establish_l5_baseline(
        cash_usd="100", l5_open_orders=[], positions=[],
        outstanding_obligations_usd="0", source=store.L5_BASELINE_SOURCE)
    assert out["established"] is False
    assert store.account_wide_delta_realized() == D("-420.99")


# -- 3. NEW LOSSES TRIP THE CEILING AT ITS THRESHOLD ----------------------

def test_the_ceiling_is_inert_until_configured():
    g = _gates()
    assert g.max_account_wide_delta_loss_usd is None
    assert _refuse(g, account_wide_delta_usd=None) is None
    assert _refuse(g, account_wide_delta_usd=D("-9999")) is None


def test_a_new_loss_at_the_threshold_refuses():
    g = _gates(max_account_wide_delta_loss_usd=D("1"))
    assert _refuse(g, account_wide_delta_usd=D("-0.99")) is None
    out = _refuse(g, account_wide_delta_usd=D("-1.00"))
    assert out is not None and out.startswith("ACCOUNT_WIDE_DELTA_LOSS_LIMIT")


def test_unrealized_loss_counts_toward_the_delta_ceiling():
    g = _gates(max_account_wide_delta_loss_usd=D("1"))
    out = _refuse(g, account_wide_delta_usd=D("-0.50"),
                  cumulative_unrealized_loss=D("0.60"))
    assert out is not None and out.startswith("ACCOUNT_WIDE_DELTA_LOSS_LIMIT")


def test_a_missing_delta_refuses_rather_than_reading_as_zero():
    g = _gates(max_account_wide_delta_loss_usd=D("1"))
    for bad in (None, D("NaN"), "abc", ""):
        out = _refuse(g, account_wide_delta_usd=bad)
        assert out == "ACCOUNT_WIDE_DELTA_UNAVAILABLE", bad


def test_a_post_baseline_profit_does_not_create_negative_loss():
    g = _gates(max_account_wide_delta_loss_usd=D("1"))
    assert _refuse(g, account_wide_delta_usd=D("6.41460000")) is None


# -- it does not weaken anything that already exists ----------------------

def test_the_lifetime_ceiling_still_refuses_with_the_delta_satisfied():
    """The decisive non-regression: a clean delta does NOT unlock trading
    while the lifetime ceiling is breached."""
    g = G.ValidationGates(max_cash_deployed_usd=D("1000"),
                          max_cumulative_loss_usd=D("20"),
                          max_account_wide_delta_loss_usd=D("1"))
    out = _refuse(g, cumulative_realized=D("-233.98540000"),
                  account_wide_delta_usd=D("6.41460000"))
    assert out is not None and out.startswith("CUMULATIVE_LOSS_LIMIT"), out


def test_the_delta_check_runs_after_the_lifetime_check():
    import inspect
    src = inspect.getsource(G.ValidationGates.refuse)
    assert src.index("ACCOUNT_WIDE_DELTA_LOSS_LIMIT") > src.index(
        "CUMULATIVE_LOSS_LIMIT")


def test_every_other_control_still_runs_after_it():
    import inspect
    src = inspect.getsource(G.ValidationGates.refuse)
    after = src[src.index("ACCOUNT_WIDE_DELTA_LOSS_LIMIT"):]
    for later in ("CASH_COMMITMENT", "INSUFFICIENT_AVAILABLE_CASH",
                  "WORST_CASE_DEFICIT", "UNRESOLVED_UNKNOWN"):
        assert later in after, later


def test_the_delta_block_reads_no_cash_or_exposure_control():
    import inspect
    src = inspect.getsource(G.ValidationGates.refuse)
    block = src[src.index("THE ACCOUNT-WIDE DELTA CEILING"):
                src.index("L5-SCOPED CEILINGS")]
    for forbidden in ("max_cash_deployed", "max_live_orders", "available_cash",
                      "max_usd_per_market", "max_total_exposure",
                      "max_abs_residual", "halt", "HALT"):
        assert forbidden not in block, forbidden


# -- wiring: supplied alongside the lifetime figure, never instead of it ---

def test_the_call_site_passes_both_the_lifetime_figure_and_the_delta():
    import inspect
    src = inspect.getsource(LX)
    i = src.index("account_wide_delta_usd=(")
    block = src[i - 700:i]
    assert "cumulative_realized=self.store.realized_today()" in block, (
        "the lifetime figure must still be supplied at the same call site")


def test_the_ceiling_is_read_from_an_unset_env_so_it_ships_inert():
    from src import l5_pmus_live_runner as RUN
    import inspect
    src = inspect.getsource(RUN)
    assert "L5_MAX_ACCOUNT_WIDE_DELTA_LOSS_USD" in src
    assert 'None if not os.getenv("L5_MAX_ACCOUNT_WIDE_DELTA_LOSS_USD"' in src
    assert os.getenv("L5_MAX_ACCOUNT_WIDE_DELTA_LOSS_USD") is None


def test_the_lifetime_ceiling_is_still_configured_in_the_runner():
    from src import l5_pmus_live_runner as RUN
    import inspect
    src = inspect.getsource(RUN)
    assert "max_cumulative_loss_usd=D(str(os.getenv(" in src
    assert '"L5_MAX_CUMULATIVE_LOSS_USD", "20"' in src
