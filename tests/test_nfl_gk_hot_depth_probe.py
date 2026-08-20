from decimal import Decimal

from src.gemini_kalshi_execution_shadow import TopOfBook
from src.nfl_gk_hot_depth_probe import (
    best_candidate,
    nfl_kalshi_taker_fee,
    same_limits,
)

D=Decimal


def book(venue, market, bid, bid_size, ask, ask_size, ts=1000):
    return TopOfBook(
        venue=venue,market_id=market,ts_ms=ts,
        yes_bid=D(str(bid)),yes_bid_size=D(str(bid_size)),
        yes_ask=D(str(ask)),yes_ask_size=D(str(ask_size)),
    )


def test_nfl_kalshi_taker_fee_uses_centicent_rounding():
    # 0.07 * .36 * .64 = .016128 -> .0162
    assert nfl_kalshi_taker_fee(D("0.36"),D("1"))==D("0.0162")
    # 0.07 * .29 * .71 = .014413 -> .0145
    assert nfl_kalshi_taker_fee(D("0.29"),D("1"))==D("0.0145")


def test_q1_candidate_requires_displayed_depth_on_both_needed_legs():
    g=book("gemini","g",.48,10,.51,10)
    k=book("kalshi","k",.35,10,.36,10)
    c=best_candidate(g,k,max_skew_ms=50)
    assert c is not None
    assert c.quantity==D("1")
    assert c.locked_profit>0

    # Remove the Kalshi YES depth needed by KALSHI_YES_GEMINI_NO; the opposite
    # direction is not profitable, so there must be no executable q=1 candidate.
    k_no_depth=book("kalshi","k",.35,10,.36,0)
    assert best_candidate(g,k_no_depth,max_skew_ms=50) is None


def test_same_limits_survival_fails_on_price_worsening_or_depth_loss():
    eg=book("gemini","g",.48,3,.51,3)
    ek=book("kalshi","k",.35,3,.36,3)
    # K YES + G NO: G NO ask = 1-.48 = .52.
    good_g=book("gemini","g",.49,2,.51,3)  # NO ask improves to .51
    good_k=book("kalshi","k",.35,3,.35,2)  # K YES ask improves
    assert same_limits("KALSHI_YES_GEMINI_NO",eg,ek,good_g,good_k)

    worse_k=book("kalshi","k",.35,3,.37,2)
    assert not same_limits("KALSHI_YES_GEMINI_NO",eg,ek,good_g,worse_k)

    no_depth_g=book("gemini","g",.49,.5,.51,3)
    assert not same_limits("KALSHI_YES_GEMINI_NO",eg,ek,no_depth_g,good_k)


def test_quote_skew_gate_is_enforced():
    g=book("gemini","g",.48,10,.51,10,ts=1000)
    k=book("kalshi","k",.35,10,.36,10,ts=1060)
    assert best_candidate(g,k,max_skew_ms=50) is None
