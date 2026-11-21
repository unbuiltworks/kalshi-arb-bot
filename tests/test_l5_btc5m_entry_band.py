"""A side is bid only while its quote price is inside the entry band.

THIS PINS A MECHANISM, NOT A FINDING. The hypothesis it was built for --
bid only the favourite, 0.55-0.85 -- did NOT survive: through this
controller on 187 windows it loses more per fill than quoting both sides,
and in Bonereaper's real fills it is negative per fill and per dollar on
the larger date. The only robust result is that a side priced 0.15-0.45
loses. The band stays off.
"""
import itertools
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from tests.test_l5_btc5m_one_sided_control import (     # noqa: F401
    _controller_on, decide, inv, legs)

BAND = dict(straddle_entry_bid_min=D("0.55"), straddle_entry_bid_max=D("0.85"))


def sides(r):
    return sorted(o["side"] for o in legs(r))


def test_without_a_band_both_sides_are_quoted():
    assert sides(decide(inv(), "0.70", "0.71", t=20.0)) == ["DOWN", "UP"]


def test_only_the_side_priced_inside_the_band_is_quoted():
    r = decide(inv(), "0.70", "0.71", t=20.0, **BAND)
    assert sides(r) == ["UP"]                      # UP 0.70, DOWN 0.29
    assert r["evaluation"]["side_refusals"]["DOWN"] == "ENTRY_BAND"


def test_it_follows_the_favourite_to_the_other_side():
    r = decide(inv(), "0.30", "0.31", t=20.0, **BAND)
    assert sides(r) == ["DOWN"]                    # DOWN 0.69


def test_near_even_neither_side_is_quoted():
    r = decide(inv(), "0.50", "0.51", t=20.0, **BAND)
    assert legs(r) == []
    assert set(r["evaluation"]["side_refusals"].values()) == {"ENTRY_BAND"}


def test_above_the_band_neither_side_is_quoted():
    assert legs(decide(inv(), "0.92", "0.93", t=20.0, **BAND)) == []


@pytest.mark.parametrize("bid,expect", [
    ("0.54", []), ("0.55", ["UP"]), ("0.85", ["UP"]), ("0.86", [])])
def test_the_edges_are_inclusive(bid, expect):
    r = decide(inv(), bid, str(D(bid) + D("0.01")), t=20.0, **BAND)
    assert sides(r) == expect


def test_it_applies_to_a_completing_leg_too():
    #: holding UP; the completing DOWN leg at 0.29 is outside the band
    held = inv("0.07", "0.049")
    r = decide(held, "0.70", "0.71", t=40.0, **BAND)
    assert "DOWN" not in sides(r)


@pytest.mark.parametrize("lo,hi", [("0.85", "0.55"), ("0", "0.85"),
                                   ("0.55", "1"), ("-0.1", "0.5"),
                                   ("0.55", "0")])
def test_an_inverted_or_out_of_range_band_refuses_every_leg(lo, hi):
    r = decide(inv(), "0.70", "0.71", t=20.0,
               straddle_entry_bid_min=D(lo), straddle_entry_bid_max=D(hi))
    assert legs(r) == []
    assert set(r["evaluation"]["side_refusals"].values()) \
        == {"ENTRY_BAND_UNREADABLE"}


BOOKS = [("0.10", "0.11"), ("0.30", "0.31"), ("0.50", "0.51"),
         ("0.70", "0.71"), ("0.90", "0.91")]
INVS = [inv(), inv("0.07", "0.049"), inv("0", "0", "0.07", "0.02"),
        inv("0.41", "0.2677", "0.43", "0.1154")]


@pytest.mark.parametrize("book,held", list(itertools.product(BOOKS, INVS)))
def test_it_never_adds_or_reprices_an_order(book, held):
    off = decide(held, *book, t=40.0)
    on = decide(held, *book, t=40.0, **BAND)
    key = lambda o: (o["side"], str(o["price"]), str(o["qty"]))  # noqa: E731
    assert {key(o) for o in legs(on)} <= {key(o) for o in legs(off)}
    for o in legs(on):
        assert D("0.55") <= D(str(o["price"])) <= D("0.85")


def test_the_other_limits_still_bind_inside_the_band():
    capped = inv("0.41", "0.2677", "0.43", "0.1154")     # UP at its side cap
    r = decide(capped, "0.70", "0.71", t=130.0, **BAND)
    assert legs(r) == []
    assert r["evaluation"]["side_refusals"]["UP"] == "SIDE_CAP_REACHED"


def test_off_is_the_default():
    p = C.Params()
    assert p.straddle_entry_bid_min == 0 and p.straddle_entry_bid_max == 0


@pytest.mark.parametrize("raw,want", [
    (None, (D("0"), D("0"))), ("", (D("0"), D("0"))),
    ("0.55:0.85", (D("0.55"), D("0.85"))),
    (" 0.6:0.8 ", (D("0.6"), D("0.8"))),
    ("0.85:0.55", X.ENTRY_BAND_REFUSE_ALL), ("0.55", X.ENTRY_BAND_REFUSE_ALL),
    ("a:b", X.ENTRY_BAND_REFUSE_ALL), ("0:0.85", X.ENTRY_BAND_REFUSE_ALL),
    ("0.55:1", X.ENTRY_BAND_REFUSE_ALL), ("0.5:0.6:0.7",
                                          X.ENTRY_BAND_REFUSE_ALL)])
def test_the_switch(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv(X.STRADDLE_ENTRY_BID_BAND_ENV, raising=False)
    else:
        monkeypatch.setenv(X.STRADDLE_ENTRY_BID_BAND_ENV, raw)
    assert X.straddle_entry_bid_band() == want


def test_a_typo_in_production_stops_quoting_rather_than_removing_the_band():
    r = decide(inv(), "0.70", "0.71", t=20.0,
               straddle_entry_bid_min=X.ENTRY_BAND_REFUSE_ALL[0],
               straddle_entry_bid_max=X.ENTRY_BAND_REFUSE_ALL[1])
    assert legs(r) == []
