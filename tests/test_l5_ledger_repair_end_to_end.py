"""Seed production's exact broken state, run the real repair/adjust/settle
chain, and assert the reconciled total. This is the regression that proves
the -$1.68 loss is preserved and the +$0.02 cannot be double-counted."""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as RUN

NFL = "aec-nfl-bal-atl-2026-10-11"
Z1245 = "cpc-btc-updown-15m-2026-10-12-1245z"
Z0030 = "cpc-btc-updown-15m-2026-10-12-0030z"
#: NFL fills only; both BTC15m fills carried commission exactly 0.0000
COMMISSIONS = D("0.0400")


def production_state():
    """Verbatim from the live L5_LIVE_HB at 2026-10-12T15:36:24Z."""
    return {
        "positions": {"%s|UP" % NFL: "2", "%s|DOWN" % NFL: "2.0000",
                      "%s|UP" % Z1245: "2.0000"},
        # leg_cost is FLAT, keyed "market|SIDE"
        "leg_cost": {"%s|UP" % NFL: "0.8800", "%s|DOWN" % NFL: "0",
                     "%s|UP" % Z1245: "0", "%s|DOWN" % Z1245: "0"},
        "committed": {NFL: "0.8800"},
        "realized_attributed": "1.6800", "realized_today": "1.6800",
        "retired_markets": {Z0030: {"t": "2026-10-12T00:45:00Z",
                                    "settlement_pnl": "1.6800"}},
        "live_orders": {}, "submitted": {}, "orders_uncertain": None,
    }


@pytest.fixture()
def store(tmp_path):
    p = os.path.join(str(tmp_path), "state.json")
    open(p, "w").write(json.dumps(production_state()))
    return LX.InventoryStore(p)


def _run_chain(s):
    RUN.repair_ledger_from_venue(s)
    RUN.apply_ledger_adjustments(s)
    out = {}
    for mkt, yes in ((NFL, D("1.0000")), (Z1245, D("0.0000"))):
        out[mkt] = s.retire_settled_market(mkt, yes_value=yes)
    return out


def test_the_whole_chain_reconciles_to_the_authoritative_net(store):
    _run_chain(store)
    att = D(store.realized_lines()["realized_attributed"])
    assert att == D("0.0800"), "gross own-outcome attributed"
    assert att - COMMISSIONS == D("0.0400"), \
        "net of commissions must equal the authoritative total"


def test_the_1245z_loss_is_recognised_not_erased(store):
    out = _run_chain(store)
    assert out[Z1245]["retired"] is True
    assert D(out[Z1245]["settlement_pnl"]) == D("-1.6800")


def test_nfl_settles_to_its_trade_margin(store):
    out = _run_chain(store)
    assert D(out[NFL]["settlement_pnl"]) == D("0.0600")


def test_the_adjustment_moves_realized_by_exactly_two_cents(store):
    RUN.repair_ledger_from_venue(store)
    before = D(store.state["realized_attributed"])
    RUN.apply_ledger_adjustments(store)
    assert D(store.state["realized_attributed"]) - before == D("0.0200")


def test_the_already_retired_market_still_refuses_a_second_settlement(store):
    _run_chain(store)
    again = store.retire_settled_market(Z0030, yes_value=D("0.0000"))
    assert again["retired"] is False and again["reason"] == "ALREADY_RETIRED"


def test_the_adjustment_is_not_applied_twice_across_the_chain(store):
    _run_chain(store)
    att = store.state["realized_attributed"]
    assert RUN.apply_ledger_adjustments(store) is None
    assert store.state["realized_attributed"] == att


def test_committed_capital_is_released(store):
    _run_chain(store)
    assert dict(store.state["committed"]) == {}


def test_a_half_repaired_book_refuses_to_settle(store):
    """If any repair refuses, settlement must raise rather than post a
    wrong P&L. This is what caught my own bad expect_cost."""
    store.state["leg_cost"]["%s|UP" % NFL] = "0"      # break one expectation
    RUN.repair_ledger_from_venue(store)
    with pytest.raises(LX.IncoherentBook):
        store.retire_settled_market(NFL, yes_value=D("1.0000"))


def test_the_chain_is_idempotent_end_to_end(store):
    _run_chain(store)
    att = store.state["realized_attributed"]
    _run_chain(store)        # everything must refuse the second time
    assert store.state["realized_attributed"] == att
