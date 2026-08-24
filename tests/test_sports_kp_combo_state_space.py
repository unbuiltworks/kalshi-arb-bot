from decimal import Decimal as D

import pytest

from src.sports_kp_combo_state_space import Instrument, Predicate, solve_min_cost_cover, state_universe


def inst(venue, market, position, required, cost, fp):
    return Instrument(
        venue=venue,
        market_id=market,
        position=position,
        predicate=Predicate.from_mapping(required),
        all_in_cost=D(cost),
        executable_depth=D("1"),
        settlement_fingerprint=fp,
        received_ms=1,
    )


def approvals(*rows):
    return {f"{x.venue}:{x.market_id}": x.settlement_fingerprint for x in rows}


def test_two_event_state_vectors_are_exact():
    a = inst("kalshi", "A", "YES", {"A": 1}, "0.40", "fa")
    ab = inst("polymarket", "AB", "YES", {"A": 1, "B": 1}, "0.20", "fab")
    ab_no = inst("polymarket", "AB", "NO", {"A": 1, "B": 1}, "0.70", "fab")
    assert state_universe(("A", "B")) == ((0, 0), (0, 1), (1, 0), (1, 1))
    assert a.payoff_vector(("A", "B")) == (0, 0, 1, 1)
    assert ab.payoff_vector(("A", "B")) == (0, 0, 0, 1)
    assert ab_no.payoff_vector(("A", "B")) == (1, 1, 1, 0)


def test_finds_locked_two_event_synthetic_cover():
    y = inst("kalshi", "A-Y", "YES", {"A": 1}, "0.45", "fy")
    n = inst("polymarket", "A-N", "NO", {"A": 1}, "0.48", "fn")
    sol = solve_min_cost_cover(("A", "B"), [y, n], approved_fingerprints=approvals(y, n))
    assert sol is not None
    assert sol.total_cost == D("0.93")
    assert sol.state_payouts == (1, 1, 1, 1)
    assert sol.locked_profit == D("0.07")
    assert sol.status == "EXECUTABLE_ARBITRAGE"


def test_three_event_combo_cover():
    abc = inst("kalshi", "ABC", "YES", {"A": 1, "B": 1, "C": 1}, "0.10", "f1")
    not_abc = inst("polymarket", "ABC-NO", "NO", {"A": 1, "B": 1, "C": 1}, "0.82", "f2")
    sol = solve_min_cost_cover(
        ("A", "B", "C"), [abc, not_abc], approved_fingerprints=approvals(abc, not_abc)
    )
    assert sol is not None
    assert len(sol.states) == 8
    assert min(sol.state_payouts) == 1
    assert sol.total_cost == D("0.92")
    assert sol.locked_profit == D("0.08")


def test_rejects_unapproved_or_changed_rule_fingerprint():
    y = inst("kalshi", "A-Y", "YES", {"A": 1}, "0.45", "fy")
    n = inst("polymarket", "A-N", "NO", {"A": 1}, "0.48", "fn")
    with pytest.raises(PermissionError):
        solve_min_cost_cover(("A", "B"), [y, n], approved_fingerprints={})
    with pytest.raises(PermissionError):
        solve_min_cost_cover(
            ("A", "B"), [y, n],
            approved_fingerprints={"kalshi:A-Y": "fy", "polymarket:A-N": "CHANGED"},
        )


def test_rejects_insufficient_depth():
    bad = Instrument(
        venue="kalshi", market_id="A", position="YES",
        predicate=Predicate.from_mapping({"A": 1}),
        all_in_cost=D("0.1"), executable_depth=D("0.5"),
        settlement_fingerprint="f", received_ms=1,
    )
    with pytest.raises(ValueError):
        solve_min_cost_cover(("A", "B"), [bad], approved_fingerprints={"kalshi:A": "f"})


def test_no_arb_when_cover_cost_exceeds_one():
    y = inst("kalshi", "A-Y", "YES", {"A": 1}, "0.55", "fy")
    n = inst("polymarket", "A-N", "NO", {"A": 1}, "0.50", "fn")
    sol = solve_min_cost_cover(("A", "B"), [y, n], approved_fingerprints=approvals(y, n))
    assert sol is not None
    assert sol.status == "FULLY_COVERED_NOT_ARBITRAGE"
    assert sol.locked_profit == D("-0.05")


def test_lossless_dedup_keeps_cheapest_same_payoff():
    a1 = inst("kalshi", "A1", "YES", {"A": 1}, "0.50", "f1")
    a2 = inst("polymarket", "A2", "YES", {"A": 1}, "0.40", "f2")
    n = inst("kalshi", "AN", "NO", {"A": 1}, "0.50", "f3")
    sol = solve_min_cost_cover(
        ("A", "B"), [a1, a2, n], approved_fingerprints=approvals(a1, a2, n), max_instruments=2
    )
    assert sol is not None
    assert sol.total_cost == D("0.90")
    assert {leg.instrument.market_id for leg in sol.legs} == {"A2", "AN"}
