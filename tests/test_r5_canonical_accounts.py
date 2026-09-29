"""The R5 1115z window as a production accounting fixture.

Every number here came from the authoritative read-only audit of
authorization OWNER-2026-09-29-NATIVEQ10-R5: 13 `mandate_commit` rows each
linked to a venue order id, and one `GET /v1/order/{id}` per order. Nothing
is modelled.

The R5 report was incoherent in a specific, reproducible shape and this
fixture is that shape: gross UP 30 / DOWN 50 against a venue net of DOWN 20.
A reconciler that compares the venue's net against a single gross leg calls
that a disagreement; the identity net = long - short says it agrees.
"""
from decimal import Decimal as D

import pytest

from src import pmus_canonical_accounts as CA

MARKET = "cpc-btc-updown-15m-2026-09-29-1115z"

#: the eight orders the venue reported ORDER_STATE_FILLED, own-terms priced.
#: DOWN own price is 1 - wire, which the audit confirmed independently
#: against each order's mandate cash_at_risk.
R5_BOOKED_FILLS = [
    {"oid": "CSPEJNMW2YC6", "side": "up", "qty": "10",
     "price_own": "0.5000", "cost": "5.0000", "commission": "-0.0300"},
    {"oid": "CSPEJ9AG8YAQ", "side": "down", "qty": "10",
     "price_own": "0.4900", "cost": "4.9000", "commission": "-0.0300"},
    {"oid": "CSPE7YNY6YB5", "side": "up", "qty": "10",
     "price_own": "0.4500", "cost": "4.5000", "commission": "-0.0300"},
    {"oid": "CSPEJNQ68YC6", "side": "down", "qty": "10",
     "price_own": "0.5400", "cost": "5.4000", "commission": "-0.0300"},
    {"oid": "CSPENGF2MYC3", "side": "up", "qty": "10",
     "price_own": "0.3900", "cost": "3.9000", "commission": "-0.0300"},
    {"oid": "CSPF4H8JRYBY", "side": "down", "qty": "10",
     "price_own": "0.5000", "cost": "5.0000", "commission": "-0.0300"},
    {"oid": "CSPF4Q6E6YBQ", "side": "down", "qty": "10",
     "price_own": "0.4900", "cost": "4.9000", "commission": "-0.0300"},
    {"oid": "CSPF4Q834YBQ", "side": "down", "qty": "10",
     "price_own": "0.4700", "cost": "4.7000", "commission": "-0.0300"},
]

#: the venue's NET position after the window: DOWN 20.0000
R5_VENUE_POSITIONS = [{"market": MARKET, "side": "DOWN", "qty": "20.0000"}]

#: the order census the audit established
R5_CENSUS = dict(submitted_orders=13, terminal_filled_orders=8,
                 rejected_orders=4, no_fill_terminal_orders=1,
                 open_orders_at_venue=0)


@pytest.fixture()
def accounts():
    return CA.CanonicalAccounts.from_booked_fills(R5_BOOKED_FILLS,
                                                  market=MARKET)


def test_canonical_truth_matches_the_audit(accounts):
    assert accounts.filled_orders == 8
    assert accounts.filled_contracts == D(80)
    assert accounts.gross[CA.UP] == D(30)
    assert accounts.gross[CA.DOWN] == D(50)
    assert accounts.matched == D(30)
    assert accounts.residual == D(20)
    assert accounts.residual_side == CA.DOWN
    assert accounts.filled_cash == D("38.3000")


def test_venue_net_is_derived_in_down_terms():
    assert CA.venue_net_down(R5_VENUE_POSITIONS, market=MARKET) == D("20.0000")


def test_absent_market_is_not_divergence():
    assert CA.venue_net_down([], market=MARKET) is None


def test_r5_reconciles_coherently(accounts):
    out = CA.reconcile(
        accounts,
        venue_net=CA.venue_net_down(R5_VENUE_POSITIONS, market=MARKET),
        **R5_CENSUS)
    assert out["coherent"] is True, out["failed_checks"]
    assert out["failed_checks"] == []


def test_gross_leg_is_never_compared_against_venue_net(accounts):
    """The exact R5 defect: venue net 20 vs gross DOWN 50 is NOT a compare.

    If anything ever reverts to comparing a gross leg, this identity breaks
    while the window is in fact perfectly reconciled.
    """
    assert accounts.gross[CA.DOWN] != D("20.0000")
    assert accounts.our_net_down == D("20.0000")


def test_fees_are_the_venues_signed_field(accounts):
    # eight filled orders, -0.0300 each. Reported as the venue reported it
    # and never renamed a credited rebate.
    assert accounts.fees == D("-0.2400")
    assert "not a credited" in accounts.as_dict()["labels"]


def test_average_prices(accounts):
    assert accounts.avg_price(CA.UP) == D("13.4000") / D(30)
    assert accounts.avg_price(CA.DOWN) == D("24.9000") / D(50)


def test_matched_pair_cost_stays_below_one(accounts):
    """The economics the gross book exists to preserve."""
    dn_avg = accounts.avg_price(CA.DOWN)
    matched_cost = accounts.cost[CA.UP] + accounts.matched * dn_avg
    assert matched_cost / accounts.matched < D(1)


# ---- fail-closed behaviour for GENUINE disagreement ---------------------

def test_venue_net_disagreement_fails_closed(accounts):
    out = CA.reconcile(accounts, venue_net=D(10), **R5_CENSUS)
    assert out["coherent"] is False
    assert "venue_net_matches_our_net" in out["failed_checks"]


def test_position_without_submission_fails_closed(accounts):
    """The R4 shape: the venue holds shares, the report says submitted 0."""
    census = dict(R5_CENSUS, submitted_orders=0, terminal_filled_orders=8,
                  rejected_orders=0, no_fill_terminal_orders=0)
    out = CA.reconcile(accounts, venue_net=D(20), **census)
    assert out["coherent"] is False
    assert "submission_explains_position" in out["failed_checks"]


def test_order_census_must_close(accounts):
    out = CA.reconcile(accounts, venue_net=D(20),
                       **dict(R5_CENSUS, rejected_orders=3))
    assert out["coherent"] is False
    assert "order_census_closes" in out["failed_checks"]


def test_fill_count_mismatch_fails_closed(accounts):
    out = CA.reconcile(accounts, venue_net=D(20),
                       **dict(R5_CENSUS, terminal_filled_orders=7,
                              rejected_orders=5))
    assert out["coherent"] is False
    assert "filled_orders_agree" in out["failed_checks"]


def test_shares_without_cost_basis_fail_closed():
    """Exactly how 20 DOWN shares arrived with no cost basis."""
    acc = CA.CanonicalAccounts.from_booked_fills(
        [{"oid": "X", "side": "down", "qty": "20", "cost": "0",
          "commission": None}], market=MARKET)
    out = CA.reconcile(acc, venue_net=D(20), submitted_orders=1,
                       terminal_filled_orders=1, rejected_orders=0,
                       no_fill_terminal_orders=0, open_orders_at_venue=0)
    assert out["coherent"] is False
    assert "cost_basis_present" in out["failed_checks"]


def test_one_fill_per_order_is_not_double_counted():
    """Booking the same order twice must not invent quantity."""
    rows = [dict(R5_BOOKED_FILLS[0]), dict(R5_BOOKED_FILLS[0], qty="0")]
    acc = CA.CanonicalAccounts.from_booked_fills(rows, market=MARKET)
    assert acc.gross[CA.UP] == D(10)
    assert acc.filled_orders == 1


def test_unknown_side_is_refused_not_guessed():
    with pytest.raises(ValueError):
        CA.CanonicalAccounts.from_booked_fills(
            [{"oid": "X", "side": "sideways", "qty": "1", "cost": "1"}],
            market=MARKET)


# ---- STRUCTURAL: there must not be two answers to "what do we own?" ------

def test_report_inventory_comes_from_the_canonical_feed():
    """The R5 defect was structural, so the guard is structural.

    `report()` must not read the L5-action lane's position accumulators when
    a native lane is present. If someone re-adds `self.store.position(...)`
    to the native branch, gross and net can disagree again.
    """
    import pathlib
    src = pathlib.Path("scripts/l5_pmus_live_oneshot.py").read_text()
    head, _, tail = src.partition("def canonical_accounts")
    body, _, _ = tail.partition("\n    def report(")
    # the canonical builder itself must not read the action-lane positions
    assert "self.store.position(" not in body
    assert "CanonicalAccounts.from_booked_fills" in body
    # and the report's inventory must be the canonical one
    assert '"inventory": canonical_inventory,' in src
    assert '"canonical_accounts": canonical,' in src


def test_venue_net_sign_is_converted_exactly_once():
    """`_net_by_market` is UP-positive; the canonical residual is DOWN-positive.

    R5 printed venue_net -20.0000 for a net long of 20 DOWN. If the single
    negation is dropped, a perfectly reconciled window reports a 40-share
    disagreement.
    """
    import pathlib
    src = pathlib.Path("scripts/l5_pmus_live_oneshot.py").read_text()
    assert 'net = None if raw_net is None else -D(str(raw_net))' in src


def test_r5_venue_net_in_up_terms_reconciles(accounts):
    """End to end through the real sign convention the venue printed."""
    up_terms = D("-20.0000")            # exactly what R5's report carried
    out = CA.reconcile(accounts, venue_net=-up_terms, **R5_CENSUS)
    assert out["coherent"] is True, out["failed_checks"]
