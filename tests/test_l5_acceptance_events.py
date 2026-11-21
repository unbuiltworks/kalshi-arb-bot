"""The decomposition is an identity, so it must reconcile exactly or it is wrong."""
import pytest

from analysis.l5.acceptance import events as E


def _f(tx, outcome, ts, size, usd):
    return {"transactionHash": tx, "outcome": outcome, "timestamp": ts,
            "size": float(size), "usdcSize": float(usd),
            "price": float(usd) / float(size)}


def test_pair_cost_is_the_sum_of_two_running_vwaps():
    ev = E.events("btc-updown-15m-1000", [
        _f("a", "Up", 1000, 10, 5.0),      # up vwap 0.50
        _f("b", "Down", 1010, 10, 4.5)])   # dn vwap 0.45
    assert ev[1]["pair_after"] == pytest.approx(0.95)


def test_every_event_moves_exactly_one_side_so_deltas_reconcile():
    ev = E.events("btc-updown-15m-1000", [
        _f("a", "Up", 1000, 10, 5.0), _f("b", "Down", 1010, 10, 4.5),
        _f("c", "Up", 1020, 10, 4.0), _f("d", "Up", 1030, 20, 6.0),
        _f("e", "Down", 1040, 5, 2.0)])
    form = next(e for e in ev if e["class"] == E.MATCH_CREATION)
    moved = sum(e["d_pair"] for e in ev if e["d_pair"] is not None)
    assert moved == pytest.approx(ev[-1]["pair_after"] - form["pair_after"])


def test_buying_the_long_side_is_a_residual_extension_and_makes_no_match():
    """It creates NO matched inventory -- and still moves the pair cost. That
    is why a pair-forming gate cannot reach this mechanism at all."""
    ev = E.events("btc-updown-15m-1000", [
        _f("a", "Up", 1000, 10, 5.0), _f("b", "Down", 1010, 10, 5.0),
        _f("c", "Up", 1020, 10, 3.0)])        # already long Up, buys more Up
    last = ev[-1]
    assert last["class"] == E.RESIDUAL_EXTENSION
    assert last["d_matched"] == 0
    assert last["d_pair"] < 0                  # cheap buy still lowers the pair


def test_buying_the_short_side_expands_the_match():
    ev = E.events("btc-updown-15m-1000", [
        _f("a", "Up", 1000, 20, 10.0), _f("b", "Down", 1010, 5, 2.5),
        _f("c", "Down", 1020, 5, 2.0)])
    assert ev[-1]["class"].startswith("MATCH_EXPANSION")
    assert ev[-1]["d_matched"] == pytest.approx(5.0)


def test_expansion_is_labelled_improving_or_worsening_by_the_pair_move():
    worse = E.events("btc-updown-15m-1000", [
        _f("a", "Up", 1000, 20, 4.0), _f("b", "Down", 1010, 5, 1.0),
        _f("c", "Down", 1020, 5, 4.0)])        # dear buy on the short side
    assert worse[-1]["class"] == E.MATCH_EXPANSION_WORSENING \
        if hasattr(E, "MATCH_EXPANSION_WORSENING") else True
    assert worse[-1]["class"] == E.MATCH_WORSENING
    assert worse[-1]["d_pair"] > 0


def test_the_first_two_sided_event_is_match_creation():
    ev = E.events("btc-updown-15m-1000", [
        _f("a", "Up", 1000, 10, 5.0), _f("b", "Up", 1005, 10, 5.0),
        _f("c", "Down", 1010, 10, 4.5)])
    assert [e["class"] for e in ev][2] == E.MATCH_CREATION
    assert ev[0]["pair_after"] is None and ev[1]["pair_after"] is None


def test_run_length_one_is_exactly_first_after_switch():
    """The run-length and switch partitions must agree at the boundary, or one
    of them is mis-specified."""
    ev = E.events("btc-updown-15m-1000", [
        _f("a", "Up", 1000, 5, 2.0), _f("b", "Down", 1010, 5, 2.0),
        _f("c", "Down", 1020, 5, 2.0), _f("d", "Up", 1030, 5, 2.0)])
    for e in ev[1:]:
        assert (e["run_len"] == 1) == e["after_switch"]


def test_a_window_with_only_one_side_never_prices_a_pair():
    ev = E.events("btc-updown-15m-1000", [
        _f("a", "Up", 1000, 5, 2.0), _f("b", "Up", 1010, 5, 2.0)])
    assert all(e["pair_after"] is None for e in ev)
    assert all(e["d_pair"] is None for e in ev)
    assert all(e["class"] == E.RESIDUAL_EXTENSION for e in ev)
