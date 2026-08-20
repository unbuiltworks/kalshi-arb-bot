from decimal import Decimal
from types import SimpleNamespace
import pytest
from src.gemini_kalshi_hedge_cushion import best_fee_aware_kalshi_hedge, kalshi_cumulative_depth
from src.gemini_kalshi_live_probe_guard import recompute_payload_locked_profit
D=Decimal

def _gemini(price,outcome):
    return {"symbol":"GEMI-BTC15M2608200300-UP","side":"BUY","type":"LIMIT","timeInForce":"FOK","price":price,"quantity":"1","eventOutcome":outcome,"clientOrderId":"g-test"}
def _kalshi(price,side):
    return {"ticker":"KXBTC15M-26AUG192300-00","client_order_id":"k-test","side":side,"count":"1.00","price":price,"time_in_force":"fill_or_kill"}

def test_1054_style_yes_no_trade_spends_only_two_cents_of_five_cent_edge():
    r=best_fee_aware_kalshi_hedge(_gemini("0.32","YES"),_kalshi("0.41","ask"),kalshi_fee_multiplier=D("1"))
    assert r.original_locked_profit==D("0.05"); assert r.cushioned_yes_price==D("0.39"); assert r.cushioned_locked_profit==D("0.03"); assert r.cushion_cents==2

def test_bid_direction_never_breaks_floor():
    g=_gemini("0.14","NO"); k=_kalshi("0.78","bid"); r=best_fee_aware_kalshi_hedge(g,k,kalshi_fee_multiplier=D("1"))
    assert r.cushioned_yes_price>=D("0.78"); assert r.cushioned_locked_profit>=D("0.03")
    if r.cushioned_yes_price<D("0.99"):
        k2=dict(k); k2["price"]=str(r.cushioned_yes_price+D("0.01")); assert recompute_payload_locked_profit(g,k2,kalshi_fee_multiplier=D("1"))<D("0.03")

def test_exact_three_cent_trade_gets_no_cushion():
    r=best_fee_aware_kalshi_hedge(_gemini("0.32","YES"),_kalshi("0.39","ask"),kalshi_fee_multiplier=D("1"))
    assert r.original_locked_profit==D("0.03"); assert r.cushion_cents==0

def test_original_below_floor_fails_closed():
    with pytest.raises(ValueError,match="below profit floor"):
        best_fee_aware_kalshi_hedge(_gemini("0.32","YES"),_kalshi("0.38","ask"),kalshi_fee_multiplier=D("1"))

def test_buy_yes_cumulative_depth_can_be_rescued_by_wider_limit():
    book=SimpleNamespace(yes={D("0.35"):D("3")},no={D("0.60"):D("0.4"),D("0.58"):D("0.7"),D("0.55"):D("5")})
    old=kalshi_cumulative_depth(book,side="bid",yes_limit=D("0.40")); wider=kalshi_cumulative_depth(book,side="bid",yes_limit=D("0.42"))
    assert old["fillable"] is False; assert wider["fillable"] is True; assert wider["cumulative_quantity"]=="1.1"

def test_sell_yes_cumulative_depth_can_be_rescued_by_lower_limit():
    book=SimpleNamespace(yes={D("0.60"):D("0.4"),D("0.58"):D("0.7"),D("0.55"):D("5")},no={D("0.40"):D("3")})
    old=kalshi_cumulative_depth(book,side="ask",yes_limit=D("0.59")); wider=kalshi_cumulative_depth(book,side="ask",yes_limit=D("0.58"))
    assert old["fillable"] is False; assert wider["fillable"] is True; assert wider["levels_crossed"]==2
