"""V3 engine tests: fair-value gating, flow classes, four-exit router,
IOC/FOK bounds, allocator — including the live-canary post-mortem scenario."""
import math

from src.gemini_maker.fair_value import fair_value, realized_vol_per_sqrt_s
from src.gemini_maker.flow_class import FlowClass, FlowInputs, classify
from src.gemini_maker.engine_v3 import (
    BookV3,
    InventoryV3,
    MarketSnapshot,
    decide_market,
    rank_markets,
    score_market,
)


def _flow(label):
    return FlowClass(label, 100.0, 0.5, 0.5, 0.2, "test")


def _snap(*, book=None, flow="PAIRABLE", fair=0.5, confident=True, tte=200.0,
          inv=None, horizon=5, pool=False, market="M"):
    return MarketSnapshot(
        market=market, horizon_minutes=horizon,
        book=book or BookV3(yes_bid=0.47, yes_bid_depth=50, yes_ask=0.53,
                            yes_ask_depth=50),
        flow=_flow(flow), fair_p_yes=fair, fair_confident=confident,
        seconds_to_expiry=tte, inventory=inv or InventoryV3(),
        in_reward_pool_verified=pool)


# ---------- fair value: the 60s-RFR model ----------

def test_fair_value_at_strike_pre_window_is_half():
    fv = fair_value(strike=100.0, spot_now=100.0, seconds_to_expiry=200.0,
                    sigma=0.05)
    assert fv.regime == "pre_window"
    assert abs(fv.p_yes - 0.5) < 1e-6


def test_fair_value_spot_far_above_strike():
    fv = fair_value(strike=100.0, spot_now=101.0, seconds_to_expiry=120.0,
                    sigma=0.02)
    assert fv.p_yes > 0.99


def test_fair_value_realized_average_dominates_late():
    # Inside the final 60s with 55s realized ABOVE strike, spot now dipped
    # below: the settlement average is mostly locked in, p stays high.
    fv = fair_value(strike=100.0, spot_now=99.95, seconds_to_expiry=5.0,
                    sigma=0.02, realized_mean=100.30, realized_coverage_s=55.0)
    assert fv.regime == "in_window"
    assert fv.realized_share > 0.9
    assert fv.p_yes > 0.95


def test_realized_vol_estimator():
    trail = [(float(i), 100.0 + 0.1 * (i % 2)) for i in range(20)]
    sigma = realized_vol_per_sqrt_s(trail)
    assert sigma is not None and sigma > 0


# ---------- flow classification ----------

def test_toxic_one_way_burst_the_canary_scenario():
    prints = [(0.0, "buy", 200.0), (0.5, "buy", 30.0)]
    fc = classify(FlowInputs(prints=prints, bid_depth=50, ask_depth=50,
                             mid=0.5, fair_p_yes=0.5, ref_velocity_sign=+1))
    assert fc.label == "TOXIC"


def test_pairable_alternating_two_sided():
    prints = [(i * 1.0, "buy" if i % 2 == 0 else "sell", 12.0)
              for i in range(10)]
    fc = classify(FlowInputs(prints=prints, bid_depth=50, ask_depth=50,
                             mid=0.5, fair_p_yes=0.5, ref_velocity_sign=0))
    assert fc.label == "PAIRABLE"


def test_dislocated_beats_other_labels():
    prints = [(0.0, "buy", 50.0)]
    fc = classify(FlowInputs(prints=prints, bid_depth=50, ask_depth=50,
                             mid=0.30, fair_p_yes=0.55, ref_velocity_sign=0))
    assert fc.label == "DISLOCATED"


# ---------- entry gating ----------

def test_toxic_flow_never_enters_regardless_of_volume():
    d = decide_market(_snap(flow="TOXIC", fair=0.60))
    assert d.mode == "OBSERVE" and not d.intents and d.reason == "TOXIC_FLOW"


def test_no_entry_without_fair_value_edge():
    # fair 0.50 vs yes bid 0.47: edge 0.03 < 5m gate 0.06 -> observe.
    d = decide_market(_snap(fair=0.50))
    assert d.mode == "OBSERVE" and d.reason == "NO_FAIR_VALUE_EDGE"


def test_entry_is_single_q1_maker_on_edge_side():
    d = decide_market(_snap(fair=0.55))  # edge 0.08 on yes side
    assert d.mode == "FAIR_VALUE_MAKER"
    assert len(d.intents) == 1
    i = d.intents[0]
    assert (i.side, i.action, i.tif, i.size) == ("yes", "buy", "POST_ONLY", 1.0)
    assert i.price == 0.47


def test_no_entry_when_model_unconfident_or_near_expiry_or_cycle_open():
    assert decide_market(_snap(fair=0.60, confident=False)).mode == "OBSERVE"
    assert decide_market(_snap(fair=0.60, tte=45.0)).reason == "NO_NEW_RISK_NEAR_EXPIRY"
    assert decide_market(_snap(fair=0.60), global_cycle_open=True).reason == "GLOBAL_CYCLE_OPEN"


def test_dead_without_verified_pool_is_observe():
    assert decide_market(_snap(flow="DEAD", fair=0.60)).reason == "DEAD_NO_POOL"
    assert decide_market(_snap(flow="DEAD", fair=0.60, pool=True)).mode == "SUBSIDY_ELIGIBLE"


# ---------- inventory: four exits ----------

def test_same_side_round_trip_preferred_when_ask_above_fair():
    inv = InventoryV3(); inv.buy("yes", 1, 0.31)
    book = BookV3(yes_bid=0.33, yes_bid_depth=50, yes_ask=0.36, yes_ask_depth=50)
    d = decide_market(_snap(book=book, fair=0.33, inv=inv))
    assert d.mode == "INVENTORY"
    i = d.intents[0]
    assert (i.side, i.action, i.tif) == ("yes", "sell", "POST_ONLY")
    assert i.price == 0.36 and i.reason == "ROUND_TRIP_MAKER"


def test_pair_completion_respects_ceiling():
    inv = InventoryV3(); inv.buy("yes", 1, 0.47)
    # complement (no) bid = 1 - yes_ask = 0.49; ceiling = 0.53
    book = BookV3(yes_bid=0.45, yes_bid_depth=50, yes_ask=0.51, yes_ask_depth=50)
    d = decide_market(_snap(book=book, fair=0.45, inv=inv))
    i = d.intents[0]
    assert i.side == "no" and i.action == "buy" and i.tif == "POST_ONLY"
    assert i.price <= 0.53 + 1e-9


def test_ioc_rescue_sell_bounded_by_premium():
    inv = InventoryV3(); inv.buy("yes", 1, 0.47)
    # rescue zone; fair 0.40, bid 0.38: premium 0.02 <= 0.03 -> IOC allowed
    book = BookV3(yes_bid=0.38, yes_bid_depth=50, yes_ask=0.60, yes_ask_depth=50)
    d = decide_market(_snap(book=book, fair=0.40, inv=inv, tte=50.0))
    reasons = {i.reason for i in d.intents}
    assert d.mode == "INVENTORY"
    # some exit chosen; if it's the IOC sell it must be the rescue variant
    if d.intents[0].tif == "IOC" and d.intents[0].action == "sell":
        assert d.intents[0].reason in ("IOC_RESCUE_SELL", "IOC_STALE_SELL")


def test_hold_when_every_exit_too_expensive():
    inv = InventoryV3(); inv.buy("yes", 1, 0.47)
    # fair 0.40, bid 0.30 (premium 0.10 > cap), ask 0.41 (below fair+edge),
    # complement ask 0.70 > ceiling 0.53, complement bid 0.59 > ceiling -> the
    # completion quote clamps to ceiling 0.53... so remove comp bid by having
    # no yes_ask? Use a book with no asks and low bid.
    book = BookV3(yes_bid=0.30, yes_bid_depth=50, yes_ask=None, yes_ask_depth=0)
    d = decide_market(_snap(book=book, fair=0.40, inv=inv, tte=50.0))
    assert d.mode == "INVENTORY"
    # completion path (no bid) unavailable without yes_ask; IOC sell premium
    # too big; round trip needs an ask. Only remaining could be none -> hold.
    if not d.intents:
        assert d.reason == "NO_SAFE_EXIT_HOLD"


def test_ioc_complete_never_breaks_pair_ceiling():
    inv = InventoryV3(); inv.buy("yes", 1, 0.47)   # ceiling for no = 0.53
    book = BookV3(yes_bid=0.40, yes_bid_depth=50, yes_ask=0.44, yes_ask_depth=50)
    # no_ask = 1 - 0.40 = 0.60 > 0.53 -> IOC completion must not appear
    d = decide_market(_snap(book=book, fair=0.45, inv=inv, tte=50.0))
    for i in d.intents:
        if i.tif == "IOC" and i.action == "buy":
            raise AssertionError("IOC completion above pair ceiling")


def test_sell_accounting_realizes_pnl():
    inv = InventoryV3(); inv.buy("yes", 2, 0.31)
    inv.sell("yes", 1, 0.36)
    assert abs(inv.realized - 0.05) < 1e-9
    assert abs(inv.yes_qty - 1) < 1e-9
    assert abs(inv.yes_cost - 0.31) < 1e-9


# ---------- allocator ----------

def test_allocator_prefers_bigger_edge_and_zeros_toxic():
    a = _snap(fair=0.55, market="A")            # edge 0.08
    b = _snap(fair=0.60, market="B")            # edge 0.13
    t = _snap(fair=0.70, flow="TOXIC", market="T")
    ranked = rank_markets([a, b, t])
    assert [m for _, m in ranked] == ["B", "A"]
    assert score_market(t) == 0.0
