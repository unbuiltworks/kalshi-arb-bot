"""Ablation must recompute, never inherit — and must never invent a fill."""
import pytest

from analysis.l5.acceptance import ablate as A
from analysis.l5.acceptance.events import events


def _f(tx, outcome, ts, size, usd):
    return {"transactionHash": tx, "outcome": outcome, "timestamp": ts,
            "size": float(size), "usdcSize": float(usd),
            "price": float(usd) / float(size)}


SEQ = [_f("a", "Up", 1000, 10, 5.0), _f("b", "Up", 1010, 10, 3.0),
       _f("c", "Down", 1020, 10, 4.5), _f("d", "Up", 1030, 10, 7.0)]


def test_control_reproduces_the_source_exactly():
    ev = events("btc-updown-15m-1000", SEQ)
    r = A.replay(ev, A.keep_all, winner="Up")
    assert r["events_retained"] == len(SEQ)
    assert r["spend"] == pytest.approx(19.5)
    assert r["up_qty"] == pytest.approx(30.0)
    assert r["dn_qty"] == pytest.approx(10.0)


def test_removing_an_event_never_substitutes_another():
    ev = events("btc-updown-15m-1000", SEQ)
    r = A.replay(ev, lambda e, s: e["n"] != 2, winner="Up")
    assert r["events_retained"] == 3
    assert r["spend"] == pytest.approx(16.5)      # exactly the dropped 3.0 less


def test_class_is_recomputed_not_inherited():
    """Event d is a RESIDUAL_EXTENSION in the original path. Drop the two
    earlier Up buys and the SAME event becomes a match expansion, because the
    retained inventory is different. Inheriting the original label would score
    the counterfactual against a path it no longer has."""
    ev = events("btc-updown-15m-1000", SEQ)
    assert ev[3]["class"] == "RESIDUAL_EXTENSION"
    seen = []
    A.replay(ev, lambda e, s: (seen.append((e["n"], s["d_matched"]))
                               or e["n"] in (3, 4)), winner="Up")
    d_matched_for_d = dict(seen)[4]
    assert d_matched_for_d > 0            # now it EXPANDS the match


def test_a_pair_forming_rule_can_never_take_its_first_position():
    """The decisive structural result: requiring every fill to expand a match
    means the first fill is refused, because there is no opposite side yet --
    so nothing ever accumulates and the strategy cannot bootstrap."""
    ev = events("btc-updown-15m-1000", SEQ)
    r = A.replay(ev, A.causal_no_residual, winner="Up")
    assert r["events_retained"] == 0
    assert r["spend"] == 0


def test_settlement_pays_only_the_winning_token():
    ev = events("btc-updown-15m-1000", SEQ)
    up = A.replay(ev, A.keep_all, winner="Up")
    dn = A.replay(ev, A.keep_all, winner="Down")
    assert up["settlement_cashflow"] == pytest.approx(30.0)   # up_qty
    assert dn["settlement_cashflow"] == pytest.approx(10.0)   # dn_qty


def test_an_unknown_winner_yields_no_economics_rather_than_a_guess():
    ev = events("btc-updown-15m-1000", SEQ)
    r = A.replay(ev, A.keep_all, winner=None)
    assert r["settlement_cashflow"] is None
    assert r["total_economics"] is None
    assert r["matched_qty"] > 0          # inventory is still measured


def test_run_cap_counts_the_retained_run_not_the_original():
    ev = events("btc-updown-15m-1000", SEQ)
    r = A.replay(ev, A.causal_run_cap(1), winner="Up")
    # a: run 1 kept. b: would be run 2 -> dropped. c: Down, run 1 kept.
    # d: Up again, and because b was dropped its run is 1 -> kept
    assert r["events_retained"] == 3


def test_total_economics_equals_cashflow_minus_spend():
    ev = events("btc-updown-15m-1000", SEQ)
    r = A.replay(ev, A.keep_all, winner="Up")
    assert r["total_economics"] == pytest.approx(
        r["settlement_cashflow"] - r["spend"])
