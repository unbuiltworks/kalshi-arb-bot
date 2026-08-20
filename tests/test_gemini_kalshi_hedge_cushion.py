from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.gemini_kalshi_hedge_cushion import (
    best_fee_aware_kalshi_hedge,
    kalshi_cumulative_depth,
)
from src.gemini_kalshi_live_probe_guard import recompute_payload_locked_profit

D = Decimal


def _gemini(*, price: str, outcome: str):
    return {
        "symbol": "GEMI-BTC15M2608200300-UP",
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "FOK",
        "price": price,
        "quantity": "1",
        "eventOutcome": outcome,
        "clientOrderId": "g-test",
    }


def _kalshi(*, price: str, side: str):
    return {
        "ticker": "KXBTC15M-26AUG192300-00",
        "client_order_id": "k-test",
        "side": side,
        "count": "1.00",
        "price": price,
        "time_in_force": "fill_or_kill",
    }


def test_1054_style_yes_no_trade_spends_only_two_cents_of_five_cent_edge():
    # Production plan at 10:54: Gemini YES limit .32 + Kalshi NO cost .59.
    # Kalshi V2 ask is YES-space .41.  With the current fee formulas this is 5c
    # worst-case locked profit.  The fee-aware floor allows a .39 ask (NO cost .61)
    # but not .38, leaving exactly 3c worst-case locked profit.
    g = _gemini(price="0.32", outcome="YES")
    k = _kalshi(price="0.41", side="ask")
    result = best_fee_aware_kalshi_hedge(g, k, kalshi_fee_multiplier=D("1"))
    assert result.original_locked_profit == D("0.05")
    assert result.cushioned_yes_price == D("0.39")
    assert result.cushioned_locked_profit == D("0.03")
    assert result.cushion_cents == 2
    assert result.edge_spent == D("0.02")


def test_bid_direction_raises_yes_limit_but_never_breaks_floor():
    # Gemini NO + Kalshi YES. Pick an original payload with >3c of edge and assert
    # the chosen bid is the most aggressive cent price that still recomputes >=3c.
    g = _gemini(price="0.14", outcome="NO")
    k = _kalshi(price="0.78", side="bid")
    result = best_fee_aware_kalshi_hedge(g, k, kalshi_fee_multiplier=D("1"))
    assert result.cushioned_yes_price >= D("0.78")
    assert result.cushioned_locked_profit >= D("0.03")
    if result.cushioned_yes_price < D("0.99"):
        one_more = dict(k)
        one_more["price"] = str(result.cushioned_yes_price + D("0.01"))
        assert recompute_payload_locked_profit(
            g, one_more, kalshi_fee_multiplier=D("1")
        ) < D("0.03")


def test_exact_three_cent_trade_gets_no_cushion():
    g = _gemini(price="0.32", outcome="YES")
    # YES-space .39 means Kalshi NO cost .61; exact recompute is 3c.
    k = _kalshi(price="0.39", side="ask")
    result = best_fee_aware_kalshi_hedge(g, k, kalshi_fee_multiplier=D("1"))
    assert result.original_locked_profit == D("0.03")
    assert result.cushioned_yes_price == D("0.39")
    assert result.cushion_cents == 0


def test_original_below_floor_fails_closed():
    g = _gemini(price="0.32", outcome="YES")
    k = _kalshi(price="0.38", side="ask")
    with pytest.raises(ValueError, match="below profit floor"):
        best_fee_aware_kalshi_hedge(g, k, kalshi_fee_multiplier=D("1"))


def test_buy_yes_cumulative_depth_can_be_rescued_by_wider_limit():
    # NO bids .60 and .58 imply YES asks .40 and .42. Top has only .4 contracts;
    # a .42 limit reaches another .7 and makes q=1 FOK feasible.
    book = SimpleNamespace(
        yes={D("0.35"): D("3")},
        no={D("0.60"): D("0.4"), D("0.58"): D("0.7"), D("0.55"): D("5")},
    )
    old = kalshi_cumulative_depth(book, side="bid", yes_limit=D("0.40"))
    wider = kalshi_cumulative_depth(book, side="bid", yes_limit=D("0.42"))
    assert old["fillable"] is False
    assert old["cumulative_quantity"] == "0.4"
    assert wider["fillable"] is True
    assert wider["cumulative_quantity"] == "1.1"


def test_sell_yes_cumulative_depth_can_be_rescued_by_lower_limit():
    # Selling YES at .59 can hit only .4 at .60. Lowering limit to .58 reaches .7
    # more at .58 and makes q=1 FOK feasible.
    book = SimpleNamespace(
        yes={D("0.60"): D("0.4"), D("0.58"): D("0.7"), D("0.55"): D("5")},
        no={D("0.40"): D("3")},
    )
    old = kalshi_cumulative_depth(book, side="ask", yes_limit=D("0.59"))
    wider = kalshi_cumulative_depth(book, side="ask", yes_limit=D("0.58"))
    assert old["fillable"] is False
    assert wider["fillable"] is True
    assert wider["levels_crossed"] == 2
