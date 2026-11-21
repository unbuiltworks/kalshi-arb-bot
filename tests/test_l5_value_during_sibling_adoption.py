"""The portfolio value stays published while the ONLY uncertainty is another
strategy's position this ledger had not booked yet.

2026-11-21 16:40Z: the 5-minute lane's first fill made this service mark the
order state uncertain for one reconciliation cycle; the published value was
withheld; the lane refused for 22 s, cancelled its resting bid and held its
first leg unquoted while UP went 0.41 -> 0.61.

Nothing here changes this service's own submission gate.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R

from tests.test_l5_equity_publication import (     # noqa: F401
    NOW, Rn, env, refusals, store, watched)

M5 = "cpc-btc-updown-5m-2026-11-21-1640z"
M15 = "cpc-btc-updown-15m-2026-11-21-1630z"


def scope(store, disputed, why="gross/net divergence", /, **over):
    sc = {"why": why, "disputed": list(disputed), "incoherent": [],
          "pending": [], "quarantine": []}
    sc.update(over)
    store.state["orders_uncertain"] = why
    store.state[LX.UNCERTAIN_SCOPE] = sc
    store.save()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(R.SIBLING_VALUE_ENV, R.SIBLING_VALUE_TOKEN)


# ------------------------------------------------------------ the store

def test_not_uncertain_is_none(store):
    assert store.uncertainty_confined_to_other_strategy() is None


def test_another_strategys_market_alone_is_recognised(store):
    scope(store, [M5])
    assert store.uncertainty_confined_to_other_strategy() == [M5]


@pytest.mark.parametrize("over", [
    {"incoherent": [M5]}, {"pending": ["coid-1"]},
    {"quarantine": ["coid-2"]}, {"disputed": []},
    {"disputed": [M5, M15]}, {"disputed": [M15]},
    {"disputed": ["aec-nba-det-sa-2026-11-21"]},
    {"disputed": "cpc-btc-updown-5m-"},
])
def test_anything_else_in_the_uncertainty_is_not(store, over):
    scope(store, [M5], **over)
    assert store.uncertainty_confined_to_other_strategy() is None


def test_a_later_mark_of_any_kind_no_longer_matches(store):
    scope(store, [M5])
    store.mark_uncertain("cancel outcome unknown")
    assert store.uncertainty_confined_to_other_strategy() is None


def test_a_scope_for_a_different_reason_does_not_match(store):
    scope(store, [M5])
    store.state["orders_uncertain"] = "something else"
    assert store.uncertainty_confined_to_other_strategy() is None


def test_a_live_order_of_ours_in_that_market_disqualifies_it(store):
    scope(store, [M5])
    store.state["live_orders"] = {"c1": {"market": M5, "side": "UP"}}
    assert store.uncertainty_confined_to_other_strategy() is None


def test_the_submission_gate_still_reads_uncertain(store):
    scope(store, [M5])
    assert store.orders_uncertain() == "gross/net divergence"


# ------------------------------------------------------ the publication

def test_off_by_default_the_value_is_withheld_as_before(store, env):
    scope(store, [M5])
    out = watched(store).equity_risk_lines(NOW)
    assert out["equity_value"]["unavailable_why"] == "ORDER_STATE_UNCERTAIN"
    assert refusals(out) == ("PORTFOLIO_VALUE_UNAVAILABLE",
                             "EQUITY_UNAVAILABLE")


@pytest.mark.parametrize("raw", ["1", "ON", "YES",
                                 "other_strategy_position_only"])
def test_only_the_exact_token_switches_it_on(store, env, monkeypatch, raw):
    monkeypatch.setenv(R.SIBLING_VALUE_ENV, raw)
    scope(store, [M5])
    out = watched(store).equity_risk_lines(NOW)
    assert out["equity_value"]["unavailable_why"] == "ORDER_STATE_UNCERTAIN"


def test_on_the_value_is_published_and_says_so(store, env, on):
    scope(store, [M5])
    store.state["positions"] = {M5 + "|DOWN": "0.08"}   # the adopted net
    out = watched(store).equity_risk_lines(NOW)
    ev = out["equity_value"]
    #: buying power only: an unmatched adopted leg adds nothing
    assert ev["usd"] == "321.4782" and ev["unavailable_why"] is None
    assert ev["published_while_uncertain"] == {
        "scope": R.SIBLING_VALUE_TOKEN, "markets": [M5]}
    assert out["portfolio_drawdown"]["refusal"] \
        != "PORTFOLIO_VALUE_UNAVAILABLE"
    assert out["equity_baseline"]["refusal"] != "EQUITY_UNAVAILABLE"


def test_on_the_drawdown_limit_still_binds_on_that_value(store, env, on):
    scope(store, [M5])
    out = watched(store).equity_risk_lines(NOW)
    #: the fixture's anchor is 336.68 with a 15 limit: breached, as before
    assert out["portfolio_drawdown"]["refusal"].startswith(
        "PORTFOLIO_DRAWDOWN_LIMIT")


@pytest.mark.parametrize("over", [
    {"pending": ["coid-1"]}, {"incoherent": [M5]},
    {"disputed": [M5, M15]}, {"quarantine": ["c"]}])
def test_on_any_wider_uncertainty_is_still_withheld(store, env, on, over):
    scope(store, [M5], **over)
    out = watched(store).equity_risk_lines(NOW)
    assert out["equity_value"]["unavailable_why"] == "ORDER_STATE_UNCERTAIN"
    assert "published_while_uncertain" not in out["equity_value"]


def test_on_an_ordinary_uncertain_mark_is_still_withheld(store, env, on):
    store.mark_uncertain("cancel outcome unknown")
    out = watched(store).equity_risk_lines(NOW)
    assert refusals(out) == ("PORTFOLIO_VALUE_UNAVAILABLE",
                             "EQUITY_UNAVAILABLE")


def test_on_and_not_uncertain_nothing_is_flagged(store, env, on):
    out = watched(store).equity_risk_lines(NOW)
    assert "published_while_uncertain" not in out["equity_value"]
    assert out["equity_value"]["usd"] == "321.4782"


# ------------------------- through the real reconciliation, end to end

def _row(market, bought, sold):
    net = float(bought) - float(sold)
    return {"marketSlug": market, "netPositionDecimal": "%.4f" % net,
            "qtyBoughtDecimal": bought, "qtySoldDecimal": sold,
            "netPosition": str(int(net)), "qtyBought": "0", "qtySold": "0",
            "cost": "0.0464", "avgPx": "0.42"}


def test_the_16_40z_sequence_end_to_end(store, env, on):
    """The lane's first DOWN fill appears at the venue; this ledger had
    never booked that market."""
    out = store.reconcile_from_venue([_row(M5, "0.0000", "0.0800")], [])
    assert out["ALERT"]                                  # adopted, as before
    assert store.orders_uncertain()                      # own gate: closed
    assert store.uncertainty_confined_to_other_strategy() == [M5]
    pub = watched(store).equity_risk_lines(NOW)
    assert pub["equity_value"]["usd"] == "321.4782"      # still published
    assert pub["equity_value"]["published_while_uncertain"]["markets"] \
        == [M5]
    #: the next reconciliation clears it, exactly as before
    store.reconcile_from_venue([_row(M5, "0.0000", "0.0800")], [])
    assert store.orders_uncertain() is None
    assert store.state[LX.UNCERTAIN_SCOPE] is None
    pub = watched(store).equity_risk_lines(NOW)
    assert "published_while_uncertain" not in pub["equity_value"]


def test_the_same_event_in_this_services_own_series_is_still_withheld(
        store, env, on):
    store.reconcile_from_venue([_row(M15, "0.0000", "0.0800")], [])
    assert store.orders_uncertain()
    assert store.uncertainty_confined_to_other_strategy() is None
    pub = watched(store).equity_risk_lines(NOW)
    assert pub["equity_value"]["unavailable_why"] == "ORDER_STATE_UNCERTAIN"
