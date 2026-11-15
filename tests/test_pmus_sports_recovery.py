"""Recovery decision engine: one function, every action compared."""
from decimal import Decimal as D

from src import pmus_sports_recovery as R

T = D("0.01")


def dec(**kw):
    base = dict(lead="YES", lead_px=D("0.44"), need=D("1"), bid=D("0.47"),
                ask=D("0.48"), tick=T, age_s=0, late=False)
    base.update(kw)
    return R.decide(**base)


def test_fresh_lone_yes_works_a_maker_completion_not_a_taker():
    d = dec(taker_allowed=True)
    # fresh: low risk cost, so it rests one tick under the NO bid (0.52)
    assert d["action"] == "COMPLETE_MAKER" and d["px"] == D("0.51")
    assert D(d["scores"]["COMPLETE_TAKER"]["value"]) < 0


def test_aging_moves_the_maker_completion_up_to_the_bid():
    assert dec(age_s=0)["px"] == D("0.51")
    assert dec(age_s=1200)["px"] == D("0.52")


def test_lone_no_mirrors_lone_yes():
    d = dec(lead="NO", lead_px=D("0.52"), taker_allowed=True)
    assert d["action"] == "COMPLETE_MAKER" and d["px"] == D("0.46")


def test_sell_is_an_alias_of_completion_on_a_netting_venue():
    d = dec(taker_allowed=True, age_s=5000)
    assert d["scores"]["SELL_TAKER"]["alias_of"] in ("COMPLETE_TAKER",
                                                     "PARTIAL_REBALANCE")
    assert d["scores"]["SELL_MAKER"]["alias_of"] == "COMPLETE_MAKER"


def test_old_position_takes_when_authorized_and_risk_outweighs_cost():
    d = dec(taker_allowed=True, age_s=3600)
    assert d["action"] == "COMPLETE_TAKER" and d["px"] == D("0.53")


def test_taker_never_chosen_without_authorization():
    d = dec(taker_allowed=False, age_s=10 ** 6, late=True)
    assert d["action"] in ("COMPLETE_MAKER", "HOLD")
    assert d["scores"]["COMPLETE_TAKER"]["why"] == "not_authorized"


def test_late_game_raises_urgency_to_take():
    early = dec(taker_allowed=True, age_s=600)
    late = dec(taker_allowed=True, age_s=600, late=True)
    assert D(late["urgency"]) == 3 * D(early["urgency"])
    assert late["action"] == "COMPLETE_TAKER"


def test_partial_rebalance_when_taker_size_is_capped():
    d = dec(need=D("10"), taker_allowed=True, age_s=3600,
            taker_chunk=D("4"))
    assert d["action"] == "PARTIAL_REBALANCE" and d["qty"] == D("4")


def test_add_complementary_inventory_is_always_rejected():
    d = dec(taker_allowed=True, age_s=3600)
    s = d["scores"]["ADD_COMPLEMENTARY_INVENTORY"]
    assert s["value"] is None and "rejected" in s["why"]
    # and no action ever sizes above the unmatched quantity
    assert d["qty"] <= D("1")


def test_lock_loss_cap_blocks_a_bad_data_taker_and_maker():
    # complement ask 0.80 against a 0.44 lead = 1.24 pair: beyond the cap
    d = dec(bid=D("0.20"), ask=D("0.21"), taker_allowed=True, age_s=10 ** 6)
    assert d["scores"]["COMPLETE_TAKER"]["why"] == "over_lock_loss_cap"
    assert d["action"] == "HOLD"


def test_book_gone_keeps_resting_and_adds_nothing():
    d = dec(bid=None, ask=None, resting_px=D("0.52"), taker_allowed=True)
    assert d["action"] == "KEEP" and d["reason"] == "no_usable_book"
    d = dec(bid=None, ask=None, taker_allowed=True)
    assert d["action"] == "HOLD"


def test_resting_at_chosen_price_is_kept_for_queue_priority():
    d = dec(resting_px=D("0.52"))
    assert d["action"] == "KEEP"


def test_moved_market_reprices_the_resting_completion():
    d = dec(bid=D("0.40"), ask=D("0.41"), resting_px=D("0.52"))
    assert d["action"] == "REPRICE_COMPLETION" and d["px"] == D("0.58")


def test_tiny_improvement_keeps_the_resting_order():
    d = dec(bid=D("0.47"), ask=D("0.49"), resting_px=D("0.51"),
            params={"reprice_min_usd": D("0.05")})
    assert d["action"] == "KEEP" and d["px"] == D("0.51")


def test_abrupt_game_event_completion_follows_the_new_book():
    calm = dec(bid=D("0.47"), ask=D("0.48"))
    jump = dec(bid=D("0.30"), ask=D("0.31"))     # lead YES collapsed
    assert calm["px"] == D("0.51") and jump["px"] == D("0.68")
    # 0.44 + 0.68 = 1.12 is inside the 0.15 lock cap: a small justified loss
    assert jump["action"] == "COMPLETE_MAKER"


def test_halt_new_exposure_threshold():
    assert R.halt_new_exposure(D("2.0"), D("2"))
    assert not R.halt_new_exposure(D("1.99"), D("2"))
    assert not R.halt_new_exposure(D("99"), D("0"))


def test_flat_position_needs_nothing():
    assert dec(need=D("0"))["action"] == "HOLD"


def test_one_tick_flicker_does_not_reprice_a_resting_completion():
    # live 18:30Z: choice flipped 0.46 <-> 0.47 as the spread flickered
    for bid, ask in (("0.52", "0.55"), ("0.52", "0.54"), ("0.53", "0.54")):
        d = dec(bid=D(bid), ask=D(ask), resting_px=D("0.46"), age_s=1200)
        assert d["action"] == "KEEP", (bid, ask, d)


def test_two_tick_move_still_reprices():
    d = dec(bid=D("0.40"), ask=D("0.41"), resting_px=D("0.52"))
    assert d["action"] == "REPRICE_COMPLETION"
