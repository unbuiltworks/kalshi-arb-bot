"""Lifting the offer: prepared, measured at the ask, and off by default.

The maker ladder rests BELOW the ask and fills only if the market comes
down to it. Measured over 11 windows and 120 live books: a 0.97 limit
rests at the front in 3 of 120 moments and would CROSS in 80. The same
price is a 2.5% opportunity as a maker and a 67% one as a taker.

What makes this dangerous rather than merely different is that crossing
ALWAYS fills, so a negative edge arrives as a stream of successful-
looking orders. Hence two independent switches and an edge computed at
the price actually paid.
"""
from decimal import Decimal as D

import pytest

from src import l5_late_favorite as LF
from src import pmus_fees as FEES


def fee(price, contracts=D(1)):
    return FEES.taker_fee(D(str(price)), D(str(contracts)))


def ev(**over):
    kw = dict(p_up=D("0.995"), p_age_s=D("0.4"), t_in_window_s=D("265"),
              window_s=D("300"),
              book={"bid": D("0.97"), "ask": D("0.98"),
                    "bid_shares": D("500"), "ask_shares": D("500")},
              inventory={"UP": {"qty": D(0), "cost": D(0)},
                         "DOWN": {"qty": D(0), "cost": D(0)}},
              resting=[], room_usd=D("5.00"), loss_room_usd=D("5.00"),
              clip_usd=D("2.00"), fee_per_share=D("0"),
              min_edge_per_share=D("0.004"), late_window_s=D("150"),
              taker_allowed=True, taker_fee_fn=fee)
    kw.update(over)
    return LF.evaluate(**kw)


# ---- off by default, twice over ---------------------------------------

def test_off_unless_the_variable_says_yes(monkeypatch):
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    for v in (None, "", "Y", "1", "TRUE", "NO", "off"):
        if v is None:
            monkeypatch.delenv(LF.MARKETABLE_ENV, raising=False)
        else:
            monkeypatch.setenv(LF.MARKETABLE_ENV, v)
        assert ev()["marketable"]["reason"] == "MARKETABLE_OFF", v


def test_it_obeys_the_same_spelling_rule_as_the_strategy_flag(monkeypatch):
    #: one rule for both flags; a case-sensitive variant here would make
    #: `=yes` work on one and silently fail on the other
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    for v in ("YES", "yes", " Yes "):
        monkeypatch.setenv(LF.MARKETABLE_ENV, v)
        assert ev()["marketable"]["ok"] is True, v


def test_the_envelope_can_refuse_it_even_when_the_flag_is_on(monkeypatch):
    #: two INDEPENDENT switches: the operator flag and the owner's
    #: standing envelope. Either one off means no crossing.
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    monkeypatch.setenv(LF.MARKETABLE_ENV, "YES")
    out = ev(taker_allowed=False)
    assert out["marketable"]["reason"] == "TAKER_NOT_AUTHORIZED_BY_ENVELOPE"


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")
    monkeypatch.setenv(LF.MARKETABLE_ENV, "YES")


# ---- the edge is measured at the ASK, never at the limit --------------

def test_the_price_paid_is_the_ask_not_the_limit(on):
    #: THE LESSON FROM THE TAPE. Across 80 crossable moments the average
    #: offer was 0.8219, not the 0.97 limit. Judging the trade at the
    #: limit would call an 0.84 purchase a 0.97 one.
    m = ev()["marketable"]
    assert m["ok"] is True, m
    assert m["price_paid"] == "0.98"
    assert m["order"]["price"] == "0.98"
    assert m["order"]["post_only"] is False
    assert m["order"]["execution"] == "MARKETABLE_LIMIT"


def test_the_real_venue_fee_is_charged_at_that_price(on):
    m = ev()["marketable"]
    assert m["taker_fee_per_share"] == str(fee("0.98"))
    assert D(m["break_even_p"]) == D("0.98") + fee("0.98")
    assert D(m["ev_per_share"]) == D("0.995") - D("0.98") - fee("0.98")


def test_the_0_82_average_entry_is_now_refused_outright(on):
    """The measured reality that prompted the band.

    Across 80 crossable moments the average offer was 0.8219. Under the
    old ceiling-only rule every one of them was admissible; the band
    refuses them all, which is the intended loss of "opportunity".
    """
    for ask in ("0.82", "0.8219", "0.84"):
        m = _at(ask, "0.9995")
        assert m["ok"] is False
        assert m["reason"] == "ASK_BELOW_BAND", ask


def test_an_edge_that_only_exists_before_fees_is_refused(on):
    #: p_win just above the ask, but not above ask + fee + margin
    f = fee("0.98")
    out = ev(p_up=D("0.98") + f + D("0.001"))
    assert out["marketable"]["ok"] is False
    assert out["marketable"]["reason"] == "EDGE_BELOW_MARGIN"


def test_the_fee_bites_hardest_in_the_middle(on):
    #: 1.03c at 0.82 against 0.20c at 0.97 -- a material share of a thin
    #: edge, which is why it is charged rather than noted afterwards
    assert fee("0.82") > fee("0.97") * 4


def test_no_fee_function_means_no_trade(on):
    out = ev(taker_fee_fn=None)
    assert out["marketable"]["reason"] == "TAKER_FEE_UNKNOWN"


def test_a_fee_function_that_raises_refuses(on):
    def boom(*a, **k):
        raise RuntimeError("no fee schedule")
    assert ev(taker_fee_fn=boom)["marketable"]["reason"] == \
        "TAKER_FEE_UNREADABLE"


# ---- bounds ------------------------------------------------------------

def test_it_never_pays_above_the_dearest_ladder_level(on):
    out = ev(book={"bid": D("0.99"), "ask": D("1.00"),
                   "bid_shares": D("500"), "ask_shares": D("500")})
    assert out["marketable"]["reason"] == "ASK_ABOVE_LIMIT"


def test_it_never_takes_more_than_is_offered(on):
    out = ev(book={"bid": D("0.97"), "ask": D("0.98"),
                   "bid_shares": D("500"), "ask_shares": D("0.5")})
    m = out["marketable"]
    assert m["ok"] is True
    assert D(m["qty"]) <= D("0.5")


def test_an_offer_too_thin_for_one_lot_is_refused(on):
    out = ev(book={"bid": D("0.97"), "ask": D("0.98"),
                   "bid_shares": D("500"), "ask_shares": D("0.001")})
    assert out["marketable"]["reason"] == "DEPTH_BELOW_ONE_LOT"


def test_it_is_bounded_by_the_clip_and_both_budgets(on):
    m = ev(clip_usd=D("2.00"))["marketable"]
    assert D(m["cost_usd"]) <= D("2.00")
    m2 = ev(room_usd=D("0.50"))["marketable"]
    assert D(m2["cost_usd"]) <= D("0.50")
    m3 = ev(loss_room_usd=D("0.25"))["marketable"]
    assert D(m3["cost_usd"]) <= D("0.25")


def test_no_ask_or_no_depth_refuses(on):
    #: `evaluate` refuses these before the marketable branch is reached,
    #: so the refusal is the strategy's own and there is no order either
    #: way -- which is the property that matters
    a = ev(book={"bid": D("0.97"), "ask": None,
                 "bid_shares": D("5"), "ask_shares": D("5")})
    assert a["ok"] is False and a["reason"] == "NO_ASK_ON_FAVOURITE"
    assert a.get("marketable", {}).get("ok") is not True
    b = ev(book={"bid": D("0.97"), "ask": D("0.98"),
                 "bid_shares": D("5"), "ask_shares": None})
    assert b["ok"] is False and b["reason"] == "NO_DEPTH"
    assert b.get("marketable", {}).get("ok") is not True


def test_every_other_gate_still_applies(on):
    #: marketable does not bypass the strategy's own preconditions
    assert ev(p_age_s=D("9"))["reason"] == "PROBABILITY_STALE"
    assert ev(t_in_window_s=D("10"))["reason"] == "TOO_EARLY"
    held = {"UP": {"qty": D("1"), "cost": D("0.9")},
            "DOWN": {"qty": D(0), "cost": D(0)}}
    assert ev(inventory=held)["reason"] == "ALREADY_HOLD_FAVOURITE"


# ---- the strict executable band: the ladder's own range ---------------
#
# A ceiling alone admits anything cheaper, and that is how this went
# wrong: `ask <= 0.99` bought a 0.22 offer against a coin-flip model and
# lost. The band is now [min(levels), max(levels)] = [0.97, 0.99].

def _at(ask, p_win, on_fixture=None, **over):
    """Evaluate with the favourite's ask set exactly, model confident."""
    a = D(str(ask))
    kw = dict(book={"bid": a - D("0.01"), "ask": a,
                    "bid_shares": D("500"), "ask_shares": D("500")},
              p_up=D(str(p_win)))
    kw.update(over)
    return ev(**kw)["marketable"]


@pytest.mark.parametrize("ask,expect", [
    ("0.22", "ASK_BELOW_BAND"),     # the 2145z trade that lost
    ("0.89", "ASK_BELOW_BAND"),     # the only genuine favourite sampled
    ("0.90", "ASK_BELOW_BAND"),     # the floor I first proposed -- refused
    ("0.96", "ASK_BELOW_BAND"),     # one tick under the band
    ("0.97", None),                 # the floor itself: ADMITTED
    ("0.98", None),
    ("0.99", None),                 # the ceiling itself: ADMITTED
    ("1.00", "ASK_ABOVE_LIMIT"),    # one tick over
])
def test_only_the_ladder_band_is_executable(on, ask, expect):
    m = _at(ask, "0.9995")
    if expect is None:
        assert m["ok"] is True, m
        assert m["price_paid"] == str(D(ask))
    else:
        assert m["ok"] is False
        assert m["reason"] == expect, (ask, m["reason"])


def test_the_band_is_inclusive_at_both_ticks(on):
    #: 0.97 and 0.99 are IN; 0.96 and 1.00 are out. The boundary is the
    #: ladder's own endpoints, so a one-tick error cannot slip through.
    assert _at("0.97", "0.9995")["ok"] is True
    assert _at("0.99", "0.9995")["ok"] is True
    assert _at("0.96", "0.9995")["reason"] == "ASK_BELOW_BAND"
    assert _at("1.00", "0.9995")["reason"] == "ASK_ABOVE_LIMIT"


def test_the_band_follows_the_ladder_rather_than_a_constant(on):
    #: derived, not written down twice -- a narrower ladder narrows the
    #: band, so the two can never disagree
    m = ev(prices=(D("0.98"), D("0.99")),
           book={"bid": D("0.96"), "ask": D("0.97"),
                 "bid_shares": D("500"), "ask_shares": D("500")},
           p_up=D("0.9995"))["marketable"]
    assert m["reason"] == "ASK_BELOW_BAND"
    assert m["floor"] == "0.98"


def test_a_cheap_offer_is_refused_however_large_the_modelled_edge(on):
    #: THE 2145z FAILURE, pinned. p_win 0.5025 against a 0.22 ask scored
    #: +27c and lost. Edge alone must never be sufficient.
    m = _at("0.22", "0.5025")
    assert m["ok"] is False
    assert m["reason"] == "ASK_BELOW_BAND"
    m2 = _at("0.22", "0.9999")          # even a huge edge
    assert m2["reason"] == "ASK_BELOW_BAND"


# ---- fees and the edge inside the band --------------------------------

@pytest.mark.parametrize("ask", ["0.97", "0.98", "0.99"])
def test_inside_the_band_the_real_fee_is_charged_at_the_ask(on, ask):
    m = _at(ask, "0.9999")
    f = fee(ask)
    assert m["taker_fee_per_share"] == str(f)
    assert D(m["break_even_p"]) == D(ask) + f
    assert D(m["ev_per_share"]) == D("0.9999") - D(ask) - f


@pytest.mark.parametrize("ask", ["0.97", "0.98", "0.99"])
def test_break_even_inside_the_band_needs_near_certainty(on, ask):
    #: at 0.97 break-even is ~97.2% AFTER the fee; a model one tick под
    #: that is refused, which is the whole point of the band
    f = fee(ask)
    be = D(ask) + f
    assert _at(ask, str(be + D("0.0001")))["reason"] == "EDGE_BELOW_MARGIN"
    assert _at(ask, str(be + D("0.005")))["ok"] is True


def test_the_fee_is_smallest_at_the_band_edges(on):
    #: theta*p*(1-p): 0.20c at 0.97 and 0.07c at 0.99, against 1.03c at
    #: 0.82. Inside the band the fee is nearly free -- which is exactly
    #: why the band, not the fee, has to do the selecting.
    assert fee("0.99") < fee("0.98") < fee("0.97") < fee("0.82")
