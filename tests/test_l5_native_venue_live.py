"""L5's own venue, measured live. These tests pin the venue terms and the
capital arithmetic, and pin that the module authorises nothing."""
from decimal import Decimal as D

from src import l5_native_venue_live as L


def test_l5s_markets_are_live_and_accepting_orders():
    assert L.MARKETS_ARE_LIVE is True
    assert L.SLUG_PATTERN[1] == ("btc", "eth")
    assert len(L.LIVE_SAMPLE) == 8
    assert all(v["liquidity_usd"] > 0 for v in L.LIVE_SAMPLE.values())


def test_the_book_is_nothing_like_the_sports_lane():
    seen = [v["pair_cost"] for v in L.LIVE_SAMPLE.values()
            if v["pair_cost"] is not None]
    assert seen, "no pair cost measured"
    assert max(seen) <= D("1.02")
    assert min(seen) >= D("1.01")


def test_the_maker_pays_no_fee_and_that_is_structural():
    assert L.VENUE_TERMS["fee_taker_only"] is True
    assert L.VENUE_TERMS["maker_rebates_fee_share_bps"] == 10000
    assert "pays no fee and takes the whole fee share" in \
        " ".join(L.MAKER_PAYS_NO_FEE.split())
    assert "structural difference, not a rate difference" in \
        " ".join(L.MAKER_PAYS_NO_FEE.split())


def test_the_capital_thresholds_come_from_venue_fields():
    assert L.VENUE_TERMS["order_min_size_shares"] == 5
    assert L.VENUE_TERMS["rewards_min_size_shares"] == 50
    assert L.VENUE_TERMS["rewards_max_spread_cents"] == D("4.5")
    assert L.min_legal_order_usd(D("0.50")) == D("2.50")
    assert L.min_reward_qualifying_usd(D("0.50")) == D("50.00")
    assert L.min_reward_qualifying_usd(D("0.50"), two_sided=False) == D("25.00")


def test_the_cap_cannot_buy_even_the_minimum_order():
    s = L.shortfall(D("0.50"))
    assert s["cap_usd"] == "0.05"
    assert s["cap_is_x_below_min_order"] == "50"
    assert s["cap_is_x_below_reward_min"] == "1000"


def test_the_blocker_is_named_as_money_not_software():
    assert "CAPITAL PER MARKET" in L.THE_BLOCKER
    assert "money decision rather than a software one" in L.THE_BLOCKER


def test_share_of_pool_is_still_open_but_the_prior_is_stronger_here():
    assert "our share of the reward pool" in L.STILL_OPEN
    assert "$71,551.74 credited across 203 events" in \
        " ".join(L.STILL_OPEN.split())
    assert "never observed a credited payout at all" in L.STILL_OPEN


def test_the_module_authorises_nothing():
    assert L.READ_ONLY is True
    assert "places no order, changes no cap and arms nothing" in \
        " ".join(L.NOT_A_GO.split())
    import ast, inspect
    tree = ast.parse(inspect.getsource(L))
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and hasattr(n.func, "attr")}
    for forbidden in ("post", "delete", "put", "create_order", "place_order"):
        assert forbidden not in calls, forbidden
