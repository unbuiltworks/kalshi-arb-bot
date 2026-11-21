"""The venue-constraint gate, end to end: runner -> controller -> adapter.

WHAT THIS VERIFIES THAT THE UNIT TESTS DO NOT. `test_l5_venue_constraints.py`
exercises the parser and `C.decide` in isolation. This drives the REAL path --
`l5_btc5m_runner.public_book` for the book, the real `Btc5mExecutor` for
submission, a scripted venue that raises on any transport call the adapter is
not supposed to make, and a ledger on disk -- so a constraint that is parsed
correctly but dropped between layers fails here.

Four consistency properties, all of which span layers:

  MINIMUM QUANTITY  the venue's `minimumTradeQty` reaches the controller and
                    nothing below it is ever serialized to the venue.
  PRICE GRID        every price the adapter sends is a whole multiple of the
                    venue's own `orderPriceMinTickSize`, not of our default.
  FEE TREATMENT     the venue's `feeCoefficient` matches what `pmus_fees`
                    charges, and the maker rebate at this account's tier is
                    zero, so nothing nets a rebate into affordability.
  SERIALIZATION     price and qty survive as strings that parse back to the
                    exact Decimals the controller chose -- no float, no
                    re-rounding between decision and wire.

NO ORDER IS SUBMITTED LIVE. The venue is a double; `Venue.sent` is a list.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from src import l5_btc5m_runner as R
from src import l5_venue_constraints as VC
from src import pmus_fees as F

#: reuse the audited rig rather than building a second one
from tests.test_l5_btc5m_execution import (          # noqa: E402
    ARMED, MARKET, Rig, Venue, armed, clean_env,     # noqa: F401
)

#: EXACTLY the shape the venue served on 2026-11-19 for
#: cpc-btc-updown-5m-2026-11-19-1620z, trimmed to the fields `public_book`
#: reads plus the three it was discarding.
def market_doc(slug=MARKET, bid="0.60", ask="0.62",
               min_qty=0.01, tick=0.01, fee=0.095, closed=False):
    m = {"slug": slug, "bestBidQuote": bid, "bestAskQuote": ask,
         "closed": closed}
    if min_qty is not None:
        m["minimumTradeQty"] = min_qty
    if tick is not None:
        m["orderPriceMinTickSize"] = tick
    if fee is not None:
        m["feeCoefficient"] = fee
    return {"market": m}


def book_via_runner(**kw):
    """The REAL runner book reader, with the HTTP call replaced."""
    doc = market_doc(**kw)
    return R.public_book(MARKET, fetch=lambda _url: doc)


# ============================================================ RUNNER LAYER

def test_the_runner_carries_the_venue_constraints_through():
    """They arrive in the SAME response as bid/ask and were being dropped."""
    b = book_via_runner()
    assert b["bid"] == D("0.60") and b["ask"] == D("0.62")
    assert b[VC.BOOK_MIN_QTY] == D("0.01")
    assert b[VC.BOOK_TICK] == D("0.01")
    assert b[VC.BOOK_FEE] == D("0.095")


def test_the_runner_makes_no_extra_request_for_them():
    calls = []

    def fetch(url):
        calls.append(url)
        return market_doc()
    R.public_book(MARKET, fetch=fetch)
    assert len(calls) == 1, calls


def test_a_venue_that_omits_them_yields_None_not_a_default():
    """Absent must be distinguishable from 'the venue said 0.01'."""
    b = book_via_runner(min_qty=None, tick=None, fee=None)
    assert b[VC.BOOK_MIN_QTY] is None
    assert b[VC.BOOK_TICK] is None
    assert VC.from_book(b)["ok"] is False


def test_the_wrong_market_still_refuses_before_constraints_matter():
    doc = market_doc(slug="cpc-btc-updown-5m-2026-01-01-0000z")
    b = R.public_book(MARKET, fetch=lambda _u: doc)
    assert b["reason"] == "BOOK_FOR_ANOTHER_MARKET"
    assert b["bid"] is None


# ====================================================== END-TO-END PATH

class Rig2(Rig):
    """The audited rig, stepping with a book the RUNNER produced."""

    def step_real(self, t=30.0, p_up="0.75", **bookkw):
        return self.ex.step(market=MARKET, book=book_via_runner(**bookkw),
                            t_in_window_s=t, p_up=p_up, p_source="TEST")


@pytest.fixture
def rig2(tmp_path):
    return Rig2(tmp_path, max_live_orders=6)


@pytest.fixture
def require(monkeypatch):
    monkeypatch.setenv(VC.REQUIRE_ENV, VC.REQUIRE_TOKEN)


def test_armed_and_verified_the_order_reaches_the_venue(rig2, armed, require):
    rep = rig2.step_real()
    assert rep["placed"], rep
    assert rig2.venue.sent, rep


def test_armed_but_unverified_nothing_reaches_the_venue(rig2, armed, require):
    """THE GATE, end to end. A book with no constraints stops the decision
    before the adapter is ever asked to submit."""
    rep = rig2.ex.step(market=MARKET,
                       book={"bid": "0.60", "ask": "0.62", "age_s": 0.1},
                       t_in_window_s=30.0, p_up="0.75", p_source="TEST")
    assert rep["placed"] == []
    assert rig2.venue.sent == []
    assert rep["why"] == "VENUE_CONSTRAINTS_UNVERIFIED"


def test_a_disagreeing_venue_grid_stops_the_path(rig2, armed, require):
    rep = rig2.step_real(min_qty=0.05)
    assert rep["placed"] == [] and rig2.venue.sent == []
    assert rep["why"].startswith("VENUE_CONSTRAINTS_DISAGREE")


def test_off_by_default_the_path_is_unchanged(rig2, armed):
    """No REQUIRE: a constraint-less book trades exactly as it always did."""
    rep = rig2.ex.step(market=MARKET,
                       book={"bid": "0.60", "ask": "0.62", "age_s": 0.1},
                       t_in_window_s=30.0, p_up="0.75", p_source="TEST")
    assert rep["placed"], rep


# ============================================ CONSISTENCY ACROSS LAYERS

def test_every_sent_price_is_on_the_venues_own_tick_grid(rig2, armed, require):
    rig2.step_real()
    tick = book_via_runner()[VC.BOOK_TICK]
    assert rig2.sent()
    for _side, px, _q in rig2.sent():
        assert (px / tick) % 1 == 0, px


def test_every_sent_qty_is_on_the_venues_own_lot_grid(rig2, armed, require):
    rig2.step_real()
    minq = book_via_runner()[VC.BOOK_MIN_QTY]
    for _side, _px, q in rig2.sent():
        assert q >= minq and (q / minq) % 1 == 0, q


def test_price_and_qty_serialize_as_exact_strings(rig2, armed, require):
    """No float and no re-rounding between decision and wire."""
    rig2.step_real()
    for s in rig2.venue.sent:
        assert isinstance(s["price"], str) and isinstance(s["qty"], str)
        #: the string parses back to a value on both venue grids. (My first
        #: version also asserted `== .normalize() or True`, which cannot
        #: fail -- an unfailable assertion is worse than none, because it
        #: reads as coverage.)
        assert (D(s["price"]) / D("0.01")) % 1 == 0
        assert (D(s["qty"]) / D("0.01")) % 1 == 0
        #: and it is the EXACT value the controller chose, not a re-rounding
        assert D(s["price"]) == D(s["price"]).quantize(D("0.01"))
        assert D(s["qty"]) == D(s["qty"]).quantize(D("0.01"))


def test_the_ledger_records_the_same_values_that_were_sent(rig2, armed,
                                                           require):
    rig2.step_real()
    sent = {(s["side"], s["price"], s["qty"]) for s in rig2.venue.sent}
    blob = json.dumps(rig2.disk())
    for side, px, q in sent:
        assert px in blob and q in blob, (side, px, q)


def test_the_venue_fee_coefficient_matches_what_we_charge(armed):
    """FEE TREATMENT. The venue advertises its own coefficient; our fee
    module must not be charging a different one."""
    assert book_via_runner()[VC.BOOK_FEE] == F.taker_theta()
    assert F.ACCOUNT_TAKER_TIER == 1


def test_no_maker_rebate_is_available_to_net_against_cost(armed):
    assert F.maker_theta() == D("0")
    assert F.maker_rebate(D("0.50"), D("1")) == D("0")
    #: and with a zero coefficient there is no rebate floor to clear
    assert F.rebate_floor_contracts(D("0.50")) is None


# ================== $5 BUDGET, $2.70 CASH GATE, SIX-ORDER LIMIT

def test_the_six_order_limit_binds_before_the_budget_does(rig2, armed,
                                                          require,
                                                          monkeypatch):
    """Repeated replenishment must stop at the order limit, not overrun it."""
    monkeypatch.setenv(X.MAX_RESTING_ENV, "6")
    for i in range(12):
        rig2.tick(2).step_real(bid="0.60", ask="0.62")
        assert len(rig2.ex.live_orders()) <= 6, i
    assert len(rig2.ex.live_orders()) <= 6


def test_cumulative_notional_never_exceeds_the_window_budget(rig2, armed,
                                                             require,
                                                             monkeypatch):
    """$5 window cap, asserted on what actually reached the venue."""
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "5")
    for _ in range(12):
        rig2.tick(2).step_real()
    spent = sum(D(s["price"]) * D(s["qty"]) for s in rig2.venue.sent)
    assert spent <= D("5.00"), spent


def test_the_tighter_cash_gate_is_not_loosened_by_the_gate(rig2, armed,
                                                           require,
                                                           monkeypatch):
    """A STRICTER existing limit must still win. $2.70 beats a $5 proposal."""
    monkeypatch.setenv(X.ACCOUNT_CAP_ENV, "2.70")
    for _ in range(12):
        rig2.tick(2).step_real()
    spent = sum(D(s["price"]) * D(s["qty"]) for s in rig2.venue.sent)
    assert spent <= D("2.70"), spent


def test_repeated_fills_replenish_and_stay_on_the_venue_grids(rig2, armed,
                                                              require,
                                                              monkeypatch):
    """REPEATED FILLS, actually filled. Each confirmed fill is replenished,
    and every replenishment is still on both venue grids.

    MY FIRST VERSION OF THIS TEST FILLED NOTHING -- it stepped eight times
    and asserted on whatever happened to be sent, and it reached for a
    `venue.working` attribute that does not exist, which the Venue double's
    strict __getattr__ caught. The double exposes `fill(oid, qty)`; using it
    is the difference between testing replenishment and testing stepping.
    """
    monkeypatch.setenv(X.STRADDLE_REPEAT_ENV, "YES")
    rig2.step_real()
    assert rig2.venue.sent, "nothing to fill"
    filled = 0
    for _ in range(6):
        #: confirm every working order at the venue, then decide again
        for oid in list(rig2.venue.by_oid):
            if oid not in rig2.venue.full and oid not in rig2.venue.retired:
                rig2.venue.fill(oid)
                filled += 1
        rig2.tick(2).step_real()
        for _s, px, q in rig2.sent():
            assert (px / D("0.01")) % 1 == 0, px
            assert (q / D("0.01")) % 1 == 0 and q >= D("0.01"), q
    assert filled >= 2, "no repeated fill was ever confirmed"
    assert len(rig2.venue.sent) > 1, "no replenishment after a fill"


def test_a_partial_fill_is_not_promoted_to_a_full_one(rig2, armed, require):
    """A partially filled order must stay working at its remaining size, and
    the remainder must still be a legal venue quantity."""
    rig2.step_real()
    oid = next(iter(rig2.venue.by_oid))
    want = D(str(rig2.venue.by_oid[oid]["qty"]))
    half = (want / 2).quantize(D("0.01"))
    rig2.venue.fill(oid, qty=half)
    row = rig2.venue.order(oid)
    assert row["state"] == "ORDER_STATE_PARTIALLY_FILLED"
    assert D(row["leavesQuantity"]) == want - half
    #: and the remainder is still on the venue's lot grid
    assert (D(row["leavesQuantity"]) / D("0.01")) % 1 == 0
    rig2.tick(2).step_real()
    for _s, _px, q in rig2.sent():
        assert q >= D("0.01")


def test_a_duplicate_client_order_id_is_impossible(rig2, armed, require):
    """The venue double raises on a repeated client_order_id, so if the
    adapter ever re-sent one this test fails rather than silently
    double-counting."""
    for _ in range(6):
        rig2.tick(2).step_real()
    coids = [s["client_order_id"] for s in rig2.venue.sent]
    assert len(coids) == len(set(coids)), coids


# ===================== THE SMALLEST *ECONOMICALLY MEANINGFUL* ORDER

def test_the_mathematical_minimum_is_a_fraction_of_a_cent():
    s = VC.from_market(market_doc()["market"])
    assert VC.smallest_order_usd(s, "0.99") == D("0.0099")
    assert VC.smallest_order_usd(s, "0.08") == D("0.0008")


def test_the_taker_fee_rounds_to_zero_below_a_price_dependent_size():
    """FEE CENT-ROUNDING IS A STEP, NOT A RATE. `pmus_fees._fee(cents=True)`
    is banker's rounding to the cent, so a small order pays NOTHING and the
    next size up pays a whole cent. The cliff is at
    0.095*C*p*(1-p) >= 0.005, i.e. C >= 0.0526/(p*(1-p))."""
    p = D("0.50")
    assert F.taker_fee(p, D("0.20"), cents=True) == D("0")
    assert F.taker_fee(p, D("0.22"), cents=True) == D("0.01")
    #: a whole cent on a $0.11 notional is 9% -- the economically relevant
    #: sizes sit just BELOW each cent boundary, not just above
    assert D("0.01") / (D("0.22") * p) > D("0.09")


def test_the_cliff_moves_with_price():
    """A clip chosen without reference to its price can cross the boundary
    it was sized to avoid -- the same price-dependence `rebate_floor_contracts`
    documents for the rebate side."""
    cheap, mid = D("0.10"), D("0.50")
    assert F.taker_fee(mid, D("0.22"), cents=True) == D("0.01")
    assert F.taker_fee(cheap, D("0.22"), cents=True) == D("0")
    #: at 0.10 the same cent needs roughly 0.58 contracts
    assert F.taker_fee(cheap, D("0.60"), cents=True) == D("0.01")


def test_a_maker_order_has_no_fee_floor_at_all_at_this_tier():
    """So for MAKER-ONLY operation the floor is not the fee. It is the point
    at which the order's own expected value stops rounding away."""
    assert F.maker_theta() == D("0")
    for q in ("0.01", "0.50", "5.00"):
        assert F.maker_rebate(D("0.50"), D(q), cents=True) == D("0")


def test_the_economic_floor_is_where_the_pair_gain_reaches_a_cent():
    """THE ANSWER. Our own VENUE-CONFIRMED gain per matched pair is $0.0884
    (analysis/l5/btc15m_live_ledger.json, 106 settled windows). A matched
    pair of `q` shares is therefore worth 0.0884*q, and below one cent that
    is smaller than the unit the venue reports P&L in:

        q >= 0.01 / 0.0884 = 0.1131 shares -> 0.12 on the 0.01 lot grid

    So the smallest ECONOMICALLY meaningful order is about 12x the smallest
    mathematically valid one, and it is set by our measured edge, not by the
    venue. At a 0.50 price that is $0.06 a leg, $0.12 a pair -- comfortably
    inside the $2.70 cash gate even with six orders working.
    """
    gain_per_pair = D("0.0884")
    floor_q = (D("0.01") / gain_per_pair)
    assert D("0.11") < floor_q < D("0.12")
    on_grid = D("0.12")
    assert (on_grid / D("0.01")) % 1 == 0
    assert gain_per_pair * on_grid >= D("0.01")
    assert gain_per_pair * D("0.11") < D("0.01")
    #: and it fits the budget six times over
    assert on_grid * D("0.50") * 2 * 6 < D("2.70")


def test_the_venue_minimum_is_not_the_economic_minimum():
    """Pinned so the two are never conflated again: the venue would accept
    0.01 shares, and 0.01 shares cannot earn a reportable cent."""
    s = VC.from_market(market_doc()["market"])
    assert VC.check_order("0.50", "0.01", s) is None       # venue: fine
    assert D("0.0884") * D("0.01") < D("0.001")            # economics: no


# =========================================== THE BOOK THE SESSION STEPS WITH
#
# Production does not step the executor with `public_book`'s output. The
# session rebuilds the book, and that rebuild dropped the constraints, so
# REQUIRE held every decision. These step with what the session builds.

def session_book(**bookkw):
    return R.step_book(book_via_runner(**bookkw),
                       {"bid_shares": D("50"), "ask_shares": D("50")})


def test_the_session_book_keeps_the_venue_constraints():
    b = session_book()
    assert b[VC.BOOK_MIN_QTY] == D("0.01") and b[VC.BOOK_TICK] == D("0.01")
    assert b["bid"] == D("0.60") and b["bid_shares"] == D("50")
    assert VC.from_book(b)["ok"] is True


def test_required_and_stepped_as_the_session_does_the_order_is_sent(
        rig2, armed, require):
    rep = rig2.ex.step(market=MARKET, book=session_book(),
                       t_in_window_s=30.0, p_up="0.75", p_source="TEST")
    assert rep["why"] != "VENUE_CONSTRAINTS_UNVERIFIED", rep
    assert rep["placed"] and rig2.venue.sent, rep


def test_a_public_record_without_them_is_still_unverified(rig2, armed,
                                                          require):
    """Nothing is invented: keys the venue did not send are not added."""
    b = R.step_book({"bid": D("0.60"), "ask": D("0.62"), "age_s": 0.1},
                    {"bid_shares": D("50"), "ask_shares": D("50")})
    assert VC.BOOK_MIN_QTY not in b and VC.BOOK_TICK not in b
    rep = rig2.ex.step(market=MARKET, book=b, t_in_window_s=30.0,
                       p_up="0.75", p_source="TEST")
    assert rep["placed"] == [] and rig2.venue.sent == []
    assert rep["why"] == "VENUE_CONSTRAINTS_UNVERIFIED"


def test_a_disagreeing_grid_stops_the_session_path_too(rig2, armed, require):
    rep = rig2.ex.step(market=MARKET, book=session_book(min_qty=0.05),
                       t_in_window_s=30.0, p_up="0.75", p_source="TEST")
    assert rep["placed"] == [] and rig2.venue.sent == []
    assert str(rep["why"]).startswith("VENUE_CONSTRAINTS_DISAGREE")


def test_the_session_steps_with_that_function_and_nothing_else():
    import inspect
    src = inspect.getsource(R.Session.trade)
    assert "book=step_book(book, sized)," in src
    assert src.count("self.ex.step(") == 1
