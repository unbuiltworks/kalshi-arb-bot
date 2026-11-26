"""An unknown cost basis is never a confirmed loss (owner 2026-11-26: "Ensure
unknown cost basis cannot become a fictitious confirmed realized loss.
Unresolved accounting must remain explicitly unresolved and fail closed")."""
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_venue_cost_attestation as VCA
from tests.test_l5_venue_round_trip import (  # noqa: F401
    UCONN, uconn, uconn_rows, Feed, run_until_idle, _no_retry_spacing)

REBASE = {"t": "2026-11-16T00:58:25+00:00", "attributed": "-40",
          "unattributed": "0", "cash_delta": "-40", "reason": "r"}
PRE = "aec-atp-colwel-zacste-2026-11-15"


@pytest.fixture
def anchored(uconn):
    uconn.state["accounting_rebase_log"] = [dict(REBASE)]
    uconn.state["cash_anchor"] = {"at_read_utc": "2026-11-16T00:11:52Z"}
    uconn.state["retired_markets"][PRE] = {
        "t": "2026-11-15T20:27:49+00:00", "settlement_pnl": "-1",
        "pnl_provenance": "UNKNOWN_COST_UPPER_BOUND"}
    return uconn


def test_a_bound_row_since_the_reanchor_is_unresolved(anchored):
    u = anchored.unresolved_bound()
    assert u["count"] == 1
    assert [r["market"] for r in u["markets"]] == [UCONN]
    assert D(u["booked_bound_usd"]) == D("-469")
    # the booked figure stays in the totals: nothing is hidden ...
    assert anchored.realized_today() == D("-45.9314") + D("-469")
    # ... and the confirmed figure excludes it
    assert D(u["confirmed_realized_usd"]) == D("-45.9314")


def test_a_row_before_the_reanchor_is_inside_the_cash_read(anchored):
    assert PRE not in [r["market"] for r in
                       anchored.unresolved_bound()["markets"]]


def test_the_venue_correction_resolves_it(anchored):
    run_until_idle(anchored, Feed([uconn_rows()]))
    u = anchored.unresolved_bound()
    assert u["count"] == 0
    assert D(u["confirmed_realized_usd"]) == D("-45.9314") + D("-20.63")


def test_the_owner_can_accept_a_named_bound(anchored, monkeypatch):
    monkeypatch.setenv(LX.InventoryStore.ACCEPT_BOUND_ENV, UCONN)
    u = anchored.unresolved_bound()
    assert u["count"] == 0 and u["accepted_by_owner"] == [UCONN]
    # accepting changes no figure: the bound stays booked
    assert anchored.realized_today() == D("-45.9314") + D("-469")


def test_an_unreadable_row_is_unknown_not_zero(anchored):
    anchored.state["retired_markets"]["x"] = {"t": "bad",
                                              "pnl_provenance":
                                              "UNKNOWN_COST_UPPER_BOUND"}
    assert anchored.unresolved_bound() is None


class _Runner:
    """Just enough of a Runner for account_risk_line."""
    account_risk_line = R.Runner.account_risk_line
    day_realized_line = R.Runner.day_realized_line
    _retired_results_line = R.Runner._retired_results_line
    _lifetime_basis_line = R.Runner._lifetime_basis_line

    def __init__(self, store):
        self.store = store

        class Lim:
            daily_realized_loss_halt_usd = D("50")
        self.limits = Lim()

        class VG:
            max_cumulative_loss_usd = D("75")

        class Ex:
            validation_gates = VG()
        self.executor = Ex()

    def equity_risk_lines(self):
        return {}


def test_the_published_risk_record_refuses_while_unresolved(
        anchored, monkeypatch):
    monkeypatch.setattr(R, "venue_basis_lines", lambda r: None)
    line = _Runner(anchored).account_risk_line()
    assert line["readable"] is False
    assert line["reason"] == "UNRESOLVED_COST_BASIS:1"
    assert line["unresolved_cost_basis"]["count"] == 1
    run_until_idle(anchored, Feed([uconn_rows()]))
    line = _Runner(anchored).account_risk_line()
    assert line["unresolved_cost_basis"]["count"] == 0
    assert line["reason"] != "UNRESOLVED_COST_BASIS:1"
