"""A loss control that can fire, and stays out of realized accounting.

The stop it replaces was keyed to `realized_usd`, which has never moved on
this venue and cannot. These tests are mostly about the three properties that
make the replacement real: it fires from observed state, it fails closed on
an unreadable outcome, and it never touches the realized ledger.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import governor as G


def _pos(sym="A", leg="YES", qty="0.01", price="0.31", resolved=False,
         won=None, expired=False):
    return {"symbol": sym, "leg": leg, "qty": qty, "price": price,
            "resolved": resolved, "won": won, "expired": expired}


# =========================================== the defect this replaces

def test_every_bound_fires_from_state_the_venue_actually_gives_us():
    """Fills, published contract outcomes and inventory. Not one of them
    waits on a cash confirmation, because none has ever arrived."""
    g = G.RiskGovernor()
    assert "never waits" in g.labels() or "none of them waits" in g.labels()
    for field in ("max_capital_at_risk_usd", "max_determined_loss_usd",
                  "max_residual_units", "max_total_committed_usd"):
        assert hasattr(g, field)
    assert not hasattr(g, "max_realized_loss_usd")


def test_the_governor_cannot_widen_itself():
    g = G.RiskGovernor()
    with pytest.raises(Exception):
        g.max_total_committed_usd = "999"          # frozen dataclass
    public = sorted(m for m in dir(g) if not m.startswith("_"))
    assert public == ["labels", "max_capital_at_risk_usd",
                      "max_determined_loss_usd", "max_residual_units",
                      "max_total_committed_usd"]


# ================================================= capital at risk

def test_capital_at_risk_counts_only_UNRESOLVED_contracts():
    """Cash on a resolved contract is no longer at risk -- it is won or lost,
    and the determined-loss governor owns it."""
    got = G.capital_at_risk([_pos(qty="1", price="0.30"),
                             _pos(sym="B", qty="1", price="0.50",
                                  resolved=True, won=True)])
    assert got["capital_at_risk_usd"] == "0.30"


def test_capital_at_risk_is_NOT_called_a_loss():
    got = G.capital_at_risk([_pos()])
    assert "NOT a loss" in got["labels"]


def test_an_unreadable_position_is_reported_not_skipped_silently():
    got = G.capital_at_risk([{"symbol": "X", "qty": None, "price": "0.3"}])
    assert got["positions"][0]["why"] == "qty or price unreadable"
    assert got["capital_at_risk_usd"] == "0"


# ============================================== determined loss

def test_a_contract_RESOLVED_AGAINST_us_is_a_determined_loss_of_its_cost():
    """A binary contract pays $1 per winning unit and $0 per losing one, so
    the loss is exactly the cost. No mark, no model, no estimate."""
    got = G.determined_loss([_pos(qty="1", price="0.31", resolved=True,
                                  won=False)])
    assert got["determined_loss_usd"] == "0.31"
    assert got["resolved_against_usd"] == "0.31"
    assert got["positions"][0]["basis"] == "RESOLVED_AGAINST"


def test_a_contract_resolved_FOR_us_charges_nothing():
    got = G.determined_loss([_pos(resolved=True, won=True)])
    assert got["determined_loss_usd"] == "0"


def test_DETERMINED_IS_NEVER_CALLED_REALIZED():
    got = G.determined_loss([_pos(resolved=True, won=False)])
    assert "DETERMINED, NOT REALIZED" in got["labels"]
    assert "never" in got["labels"] and "realized P&L" in got["labels"]


def test_AN_EXPIRED_CONTRACT_WITH_AN_UNREADABLE_OUTCOME_FAILS_CLOSED():
    """THE PROPERTY THAT MAKES IT A CONTROL. A governor that waits for a
    readable outcome is a governor that does not fire. The position is
    charged at full cost until the outcome can be read."""
    got = G.determined_loss([_pos(qty="1", price="0.44", resolved=False,
                                  expired=True)])
    assert got["determined_loss_usd"] == "0.44"
    assert got["assumed_pending_outcome_usd"] == "0.44"
    assert got["positions"][0]["basis"] == \
        "EXPIRED_OUTCOME_UNREADABLE_ASSUMED_LOSS"
    assert "does not fire" in got["fail_closed"]


def test_the_assumed_charge_is_RELEASED_once_the_outcome_reads_as_a_win():
    before = G.determined_loss([_pos(qty="1", price="0.44", expired=True)])
    after = G.determined_loss([_pos(qty="1", price="0.44", expired=True,
                                    resolved=True, won=True)])
    assert before["determined_loss_usd"] == "0.44"
    assert after["determined_loss_usd"] == "0"


def test_a_contract_still_running_charges_nothing():
    got = G.determined_loss([_pos()])
    assert got["positions"][0]["basis"] == "NOT_YET_RESOLVED"
    assert got["determined_loss_usd"] == "0"


# ============================================== residual exposure

def test_matched_legs_on_the_SAME_contract_offset():
    got = G.residual_exposure([_pos(leg="YES", qty="0.02"),
                               _pos(leg="NO", qty="0.02")])
    assert D(got["residual_units"]) == D("0")
    assert got["by_symbol"][0]["matched"] == "0.02"


def test_legs_on_DIFFERENT_contracts_do_NOT_offset():
    """They are different bets. Netting them would report a flat book while
    carrying two directional positions."""
    got = G.residual_exposure([_pos(sym="A", leg="YES", qty="0.01"),
                               _pos(sym="B", leg="NO", qty="0.01")])
    assert got["residual_units"] == "0.02"


def test_the_residual_carries_why_it_is_the_one_worth_bounding():
    got = G.residual_exposure([_pos()])
    assert "0.3421" in got["labels"]


# ================================================= the decision

def test_a_clean_state_admits():
    got = G.evaluate(G.RiskGovernor(), [_pos()], total_committed_usd="0.0031")
    assert got["admits"] is True and got["state"] is None


def test_the_determined_loss_stop_FIRES_where_the_realized_one_could_not():
    """The whole point: same evidence, and this one actually stops."""
    g = G.RiskGovernor(max_determined_loss_usd="0.25")
    pos = [_pos(sym=str(i), qty="1", price="0.30", resolved=True, won=False)
           for i in range(1)]
    got = G.evaluate(g, pos, total_committed_usd="0.30")
    assert got["admits"] is False
    assert got["state"] == G.DETERMINED_LOSS_STOP
    assert "max_determined_loss_usd breached: 0.30 > 0.25" in got["why"]


def test_the_outermost_bound_is_checked_first():
    g = G.RiskGovernor(max_total_committed_usd="0.10",
                       max_determined_loss_usd="0.01")
    pos = [_pos(qty="1", price="0.30", resolved=True, won=False)]
    got = G.evaluate(g, pos, total_committed_usd="5.00")
    assert got["state"] == G.TOTAL_COMMITTED_EXCEEDED


def test_capital_at_risk_can_stop_a_run_on_its_own():
    g = G.RiskGovernor(max_capital_at_risk_usd="0.05")
    got = G.evaluate(g, [_pos(qty="1", price="0.30")],
                     total_committed_usd="0.30")
    assert got["state"] == G.CAPITAL_AT_RISK_EXCEEDED


def test_residual_exposure_can_stop_a_run_on_its_own():
    g = G.RiskGovernor(max_residual_units="0.04")
    pos = [_pos(sym=str(i), qty="0.01") for i in range(9)]
    got = G.evaluate(g, pos, total_committed_usd="0.09")
    assert got["state"] == G.RESIDUAL_EXPOSURE_EXCEEDED


def test_every_check_is_reported_even_when_none_breached():
    got = G.evaluate(G.RiskGovernor(), [_pos()])
    names = {c["bound"] for c in got["checks"]}
    assert names == {"max_total_committed_usd", "max_determined_loss_usd",
                     "max_capital_at_risk_usd", "max_residual_units"}
    assert all(c["breached"] is False for c in got["checks"])


# ===================================== separation from realized accounting

def test_the_governor_states_its_separation_from_realized_P_AND_L():
    got = G.evaluate(G.RiskGovernor(), [_pos()])
    assert "NOT REALIZED ACCOUNTING" in got["separation"]
    assert "never" in got["separation"] or "no governor figure" in \
        got["separation"]


def test_the_governor_never_touches_the_seven_line_ledger():
    """Scanned over the CODE, not the prose. An earlier structural test in
    this repo failed because it matched its own explanatory comment; the
    module is allowed to SAY it leaves the ledger alone, and must not call
    into it."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(G))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            imported |= {a.name for a in n.names}
            imported.add(n.module)
    for bad in ("realized_ledger", "realized_net_pnl",
                "confirmed_maker_rebates"):
        assert bad not in names, bad
    assert not any("rebates" in m for m in imported), imported


def test_the_ledger_total_is_unaffected_by_any_governor_number():
    from src.gemini_execution import rebates as RB
    dl = G.determined_loss([_pos(qty="1", price="0.30", resolved=True,
                                 won=False)])
    assert dl["determined_loss_usd"] == "0.30"
    led = RB.realized_ledger(
        matched_pair_pnl="0", directional_residual_pnl="0",
        spread_capture="0", confirmed_maker_rebates="0",
        confirmed_other_rewards="0", fees="0", settlement_cashflow="0")
    assert led["realized_net_pnl"] == "0"


# ============================== it reads the SAME fills the controller does

def test_positions_come_from_the_CANONICAL_execution_view():
    """A governor looking at a different set of fills from the thing it
    governs is not governing it."""
    from src.gemini_execution import executions as EX
    view = EX.canonical_executions(
        [{"orderId": 1, "status": "filled", "symbol": "S", "outcome": "yes",
          "price": "0.31", "filledQuantity": "0.01", "quantity": "0.01"}],
        None, symbol="S")
    pos = G.positions_from_executions(
        view, resolutions={"S": {"resolved": True, "resolution_side": "no"}})
    assert len(pos) == 1
    assert pos[0]["leg"] == "YES" and pos[0]["won"] is False
    got = G.determined_loss(pos)
    assert got["determined_loss_usd"] == "0.0031"


def test_an_unresolved_contract_from_the_view_is_not_assumed_lost():
    from src.gemini_execution import executions as EX
    view = EX.canonical_executions(
        [{"orderId": 1, "status": "filled", "symbol": "S", "outcome": "yes",
          "price": "0.31", "filledQuantity": "0.01", "quantity": "0.01"}],
        None, symbol="S")
    pos = G.positions_from_executions(view)
    assert pos[0]["resolved"] is False and pos[0]["won"] is None
    assert G.determined_loss(pos)["determined_loss_usd"] == "0"
