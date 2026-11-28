"""The BTC 5-minute inventory controller: a pure decision function.

Deterministic and offline. Every case states the book, inventory and
resting orders it starts from and the one action that must come out.
"""
from __future__ import annotations

import copy
import inspect
import random
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C

FLAT = {"UP": {"qty": "0", "cost": "0"}, "DOWN": {"qty": "0", "cost": "0"}}


def book(bid, ask, age=0.0):
    return {"bid": str(bid), "ask": str(ask), "age_s": age}


def inv(up=("0", "0"), down=("0", "0")):
    return {"UP": {"qty": up[0], "cost": up[1]},
            "DOWN": {"qty": down[0], "cost": down[1]}}


def order(oid, side, price, qty="0.16", age=1.0):
    return {"id": oid, "side": side, "price": str(price), "qty": str(qty), "age_s": age}


def run(bk, *, t=60.0, inventory=FLAT, resting=(), **kw):
    return C.decide(book=bk, t_in_window_s=t, inventory=inventory,
                    resting=list(resting), **kw)


def places(d):
    return [o for o in d["orders"] if o["op"] == "PLACE"]


def cancels(d):
    return [o["id"] for o in d["orders"] if o["op"] == "CANCEL"]


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(C.CONTROLLER_ENV, "ON")


# ------------------------------------------------------------ the switch

def test_off_when_env_is_unset(monkeypatch):
    monkeypatch.delenv(C.CONTROLLER_ENV, raising=False)
    assert C.enabled() is False
    d = run(book("0.62", "0.63"), p_up="0.70")
    assert d["action"] == C.DISABLED and d["orders"] == []


@pytest.mark.parametrize("value", ["", "on", "On", "1", "true", "YES", "ON ", " ON", "OFF"])
def test_off_for_anything_but_exact_on(monkeypatch, value):
    monkeypatch.setenv(C.CONTROLLER_ENV, value)
    assert C.enabled() is False
    d = run(book("0.62", "0.63"), p_up="0.70", resting=[order("a", "UP", "0.60")])
    assert d["action"] == C.DISABLED and d["orders"] == []


def test_on_only_for_exact_on(on):
    assert C.enabled() is True


def test_param_defaults():
    p = C.Params()
    assert (p.band_lo, p.band_hi, p.depth_ticks) == (D("0.51"), D("0.90"), 2)
    assert (p.clip_usd, p.max_exposure_usd, p.hedge_reserve_usd) == (D("0.10"), D("5.00"), D("1.50"))
    assert (p.min_edge_per_share, p.adverse_selection_per_share) == (D("0.01"), D("0.06"))
    assert (p.hedge_target_pair, p.hard_pair_ceiling, p.flip_margin) == (D("0.98"), D("1.00"), D("0.05"))
    assert (p.quote_ttl_s, p.stale_book_s, p.no_new_entry_after_s, p.window_s) == (20.0, 3.0, 270.0, 300.0)
    assert p.taker_allowed is False and p.taker_fee_coef == D("0.095")
    with pytest.raises(Exception):
        p.taker_allowed = True          # frozen


# ------------------------------------------------- default: mostly hold

def test_defaults_with_book_only_probability_hold(on):
    d = run(book("0.62", "0.63"))
    assert d["action"] == C.HOLD_OR_SKIP and d["why"] == "EV_BELOW_MIN"
    assert d["orders"] == []
    assert d["p_source"] == "BOOK" and d["p_up"] == D("0.625") and d["favorite"] == "UP"
    ev = d["evaluation"]
    assert ev["independent_estimate"] is False
    # mid 0.625 - price 0.60 - allowance 0.06
    assert ev["entry"]["price"] == D("0.60") and ev["entry"]["ev_per_share"] == D("-0.035")
    assert ev["entry"]["chosen"] is False


@pytest.mark.parametrize("bid", ["0.51", "0.55", "0.70", "0.85", "0.90"])
def test_book_only_holds_across_the_band_on_a_tight_book(on, bid):
    d = run(book(bid, D(bid) + D("0.01")))
    assert d["action"] == C.HOLD_OR_SKIP and d["orders"] == []


# ------------------------------------------------------------- opening

def test_independent_probability_opens_two_ticks_below_bid(on):
    d = run(book("0.62", "0.63"), p_up="0.70", p_source="MODEL")
    assert d["action"] == C.OPEN_FAVORITE
    assert d["p_source"] == "MODEL" and d["evaluation"]["independent_estimate"] is True
    (o,) = d["orders"]
    assert o["op"] == "PLACE" and o["side"] == "UP" and o["post_only"] is True
    assert o["price"] == D("0.60")
    assert o["qty"] == D("0.16")                   # floor(0.10 / 0.60) to 0.01
    assert d["evaluation"]["entry"]["ev_per_share"] == D("0.04")
    assert d["evaluation"]["entry"]["chosen"] is True


def test_ev_exactly_at_minimum_opens_and_just_below_does_not(on):
    assert run(book("0.62", "0.63"), p_up="0.67")["action"] == C.OPEN_FAVORITE
    d = run(book("0.62", "0.63"), p_up="0.669")
    assert d["action"] == C.HOLD_OR_SKIP and d["why"] == "EV_BELOW_MIN"


def test_caller_passing_book_source_is_not_independent(on):
    d = run(book("0.62", "0.63"), p_up="0.70", p_source="BOOK")
    assert d["evaluation"]["independent_estimate"] is False


@pytest.mark.parametrize("bid,ask,p,action,why", [
    ("0.51", "0.52", "0.70", C.OPEN_FAVORITE, None),
    ("0.90", "0.91", "0.99", C.OPEN_FAVORITE, None),
    ("0.50", "0.51", "0.70", C.HOLD_OR_SKIP, "OUT_OF_BAND"),
    ("0.91", "0.92", "0.99", C.HOLD_OR_SKIP, "OUT_OF_BAND"),
])
def test_band_edges(on, bid, ask, p, action, why):
    d = run(book(bid, ask), p_up=p)
    assert d["action"] == action
    if why:
        assert d["why"] == why and d["orders"] == []
    else:
        assert places(d)[0]["price"] == D(bid) - D("0.02")


def test_down_favorite_uses_own_prices(on):
    # YES 0.37/0.38 -> DOWN bid 0.62, DOWN ask 0.63.
    d = run(book("0.37", "0.38"), p_up="0.30")
    assert d["favorite"] == "DOWN" and d["action"] == C.OPEN_FAVORITE
    q = d["evaluation"]["quotes"]["DOWN"]
    assert q == {"bid": D("0.62"), "ask": D("0.63")}
    (o,) = d["orders"]
    assert (o["side"], o["price"], o["qty"], o["post_only"]) == ("DOWN", D("0.60"), D("0.16"), True)
    assert d["evaluation"]["entry"]["ev_per_share"] == D("0.04")   # 0.70 - 0.60 - 0.06


def test_down_favorite_book_only_holds(on):
    d = run(book("0.37", "0.38"))
    assert d["favorite"] == "DOWN" and d["action"] == C.HOLD_OR_SKIP
    assert d["evaluation"]["entry"]["ev_per_share"] == D("-0.035")


def test_add_when_favorite_inventory_exists(on):
    # avg 0.62 + DOWN bid 0.37 = 0.99: no hedge, so the favorite is added to.
    d = run(book("0.62", "0.63"), p_up="0.70", inventory=inv(up=("0.16", "0.0992")))
    assert d["action"] == C.ADD_FAVORITE
    (o,) = d["orders"]
    assert (o["side"], o["price"], o["qty"]) == ("UP", D("0.60"), D("0.16"))
    assert d["evaluation"]["hedge"]["why"] == "PAIR_ABOVE_TARGET"


def test_quantity_below_minimum_is_refused(on):
    d = run(book("0.62", "0.63"), p_up="0.70", params=C.Params(clip_usd=D("0.005")))
    assert d["action"] == C.HOLD_OR_SKIP and d["why"] == "QTY_BELOW_MIN" and d["orders"] == []


def test_price_below_one_cent_is_never_quoted(on):
    d = run(book("0.02", "0.03"), p_up="0.90", params=C.Params(band_lo=D("0.01")))
    assert d["action"] == C.HOLD_OR_SKIP and d["why"] == "PRICE_OUT_OF_RANGE"


# ------------------------------------------------------------ exposure

def test_exposure_cap_keeps_the_hedge_reserve(on):
    # 5.00 cap less 1.50 reserve leaves 3.50 for entries.
    ok = run(book("0.62", "0.63"), p_up="0.70", inventory=inv(up=("5", "3.10")))
    assert ok["action"] == C.ADD_FAVORITE
    full = run(book("0.62", "0.63"), p_up="0.70", inventory=inv(up=("5.5", "3.45")))
    assert full["action"] == C.HOLD_OR_SKIP and full["why"] == "NO_EXPOSURE_ROOM"
    assert full["exposure"]["total_exposure_usd"] < C.Params().max_exposure_usd


def test_exposure_cap_counts_resting_orders(on):
    # 3.10 filled + 0.33 resting + 0.096 new > 3.50, though 3.10 + 0.096 fits.
    d = run(book("0.62", "0.63"), p_up="0.70", inventory=inv(up=("5", "3.10")),
            resting=[order("old", "UP", "0.55", qty="0.60")])
    assert d["exposure"]["resting_notional_usd"] == D("0.33")
    assert d["exposure"]["total_exposure_usd"] == D("3.43")
    assert d["action"] == C.REPRICE and cancels(d) == ["old"] and places(d) == []
    assert d["evaluation"]["entry"]["why"] == "NO_EXPOSURE_ROOM"


def test_hedge_may_use_the_reserve_but_not_exceed_the_cap(on):
    d = run(book("0.62", "0.63"), inventory=inv(up=("6", "3.60")))
    assert d["action"] == C.HEDGE_UNDERDOG
    (o,) = d["orders"]
    assert o["qty"] == D("3.78")                         # floor(1.40 / 0.37)
    assert D("3.60") + o["price"] * o["qty"] <= D("5.00")


def test_exposure_fields():
    e = C.exposure(inv(up=("1", "0.60"), down=("0.4", "0.15")),
                   [order("h", "DOWN", "0.38", qty="0.5")])
    assert e["filled_cost_usd"] == D("0.75")
    assert e["resting_notional_usd"] == D("0.19")
    assert e["total_exposure_usd"] == D("0.94")
    assert (e["up_qty"], e["down_qty"]) == (D("1"), D("0.4"))
    assert (e["unmatched_side"], e["unmatched_qty"], e["matched_qty"]) == ("UP", D("0.6"), D("0.4"))
    assert e["matched_cost_per_pair"] == D("0.975")
    assert e["worst_case_settlement_loss_usd"] == D("0.35")        # DOWN wins: 0.75 - 0.4
    assert e["worst_case_if_resting_fill_usd"] == D("0.54")


def test_exposure_flat_and_unreadable():
    e = C.exposure(FLAT, [])
    assert e["total_exposure_usd"] == 0 and e["unmatched_side"] is None
    assert e["matched_cost_per_pair"] is None
    assert C.exposure(None, []) is None
    assert C.exposure(inv(up=("-1", "0")), []) is None
    assert C.exposure(FLAT, [{"id": "x", "side": "UP"}]) is None


def test_resting_hedge_does_not_reduce_worst_case_until_filled():
    held = inv(up=("1", "0.60"))
    bare = C.exposure(held, [])
    with_hedge = C.exposure(held, [order("h", "DOWN", "0.38", qty="1")])
    assert bare["worst_case_settlement_loss_usd"] == D("0.60")
    assert with_hedge["worst_case_settlement_loss_usd"] == D("0.60")
    assert with_hedge["worst_case_if_resting_fill_usd"] >= D("0.60")
    filled = C.exposure(inv(up=("1", "0.60"), down=("1", "0.38")), [])
    assert filled["worst_case_settlement_loss_usd"] == D("-0.02")  # locked once filled
    assert filled["matched_cost_per_pair"] == D("0.98")


# ---------------------------------------------------------------- book

def test_stale_book_cancels_everything_and_places_nothing(on):
    rest = [order("a", "UP", "0.60"), order("b", "DOWN", "0.37")]
    d = run(book("0.62", "0.63", age=3.01), p_up="0.70", resting=rest,
            inventory=inv(up=("0.16", "0.096")))
    assert d["action"] == C.HOLD_OR_SKIP and d["why"] == "BOOK_STALE"
    assert cancels(d) == ["a", "b"] and places(d) == []


def test_book_at_the_stale_limit_is_still_used(on):
    assert run(book("0.62", "0.63", age=3.0), p_up="0.70")["action"] == C.OPEN_FAVORITE


@pytest.mark.parametrize("bad", [
    None, {}, "0.62/0.63",
    {"bid": "0.62", "ask": "0.63"},                    # no age
    {"bid": None, "ask": "0.63", "age_s": 0},
    {"bid": "0.62", "ask": None, "age_s": 0},
    {"bid": "0", "ask": "0.63", "age_s": 0},
    {"bid": "-0.01", "ask": "0.63", "age_s": 0},
    {"bid": "0.63", "ask": "0.63", "age_s": 0},        # locked
    {"bid": "0.64", "ask": "0.63", "age_s": 0},        # crossed
    {"bid": "0.62", "ask": "1", "age_s": 0},
    {"bid": "0.62", "ask": "1.00", "age_s": 0},
    {"bid": "nan", "ask": "0.63", "age_s": 0},
    {"bid": "x", "ask": "0.63", "age_s": 0},
    {"bid": True, "ask": "0.63", "age_s": 0},
    {"bid": "0.62", "ask": "0.63", "age_s": float("nan")},
    {"bid": "0.62", "ask": "0.63", "age_s": -1},
])
def test_unusable_books_cancel_everything(on, bad):
    d = run(bad, p_up="0.70", resting=[order("a", "UP", "0.60")])
    assert d["action"] == C.HOLD_OR_SKIP and d["why"] == "BOOK_UNUSABLE"
    assert cancels(d) == ["a"] and places(d) == []


@pytest.mark.parametrize("kw,why", [
    ({"t": None}, "TIME_UNREADABLE"),
    ({"t": -1.0}, "TIME_UNREADABLE"),
    ({"t": 300.0}, "WINDOW_OVER"),
    ({"inventory": None}, "INVENTORY_UNREADABLE"),
    ({"inventory": inv(up=("x", "0"))}, "INVENTORY_UNREADABLE"),
    ({"inventory": inv(up=("0", "0.10"))}, "INVENTORY_UNREADABLE"),
    ({"p_up": "abc"}, "P_UP_UNREADABLE"),
    ({"p_up": float("nan")}, "P_UP_UNREADABLE"),
])
def test_unreadable_inputs_fail_closed(on, kw, why):
    args = {"p_up": "0.70", "resting": [order("a", "UP", "0.60")]}
    args.update(kw)
    d = run(book("0.62", "0.63"), **args)
    assert d["action"] == C.HOLD_OR_SKIP and d["why"] == why
    assert cancels(d) == ["a"] and places(d) == []


def test_unreadable_resting_order_fails_closed(on):
    rest = [order("a", "UP", "0.60"), {"id": "b", "side": "SIDEWAYS", "price": "0.5",
                                       "qty": "1", "age_s": 0}]
    d = run(book("0.62", "0.63"), p_up="0.70", resting=rest)
    assert d["action"] == C.HOLD_OR_SKIP and d["why"] == "RESTING_UNREADABLE"
    assert cancels(d) == ["a", "b"] and places(d) == []


def test_probability_is_clamped(on):
    assert run(book("0.62", "0.63"), p_up="1.7")["p_up"] == D("1")
    d = run(book("0.62", "0.63"), p_up="-3")
    assert d["p_up"] == D("0") and d["favorite"] == "DOWN"


# ---------------------------------------------------------- late window

def test_late_window_blocks_entries(on):
    assert run(book("0.62", "0.63"), p_up="0.70", t=269.9)["action"] == C.OPEN_FAVORITE
    for t in (270.0, 285.0, 299.9):
        d = run(book("0.62", "0.63"), p_up="0.70", t=t)
        assert d["action"] == C.HOLD_OR_SKIP and d["why"] == "ENTRY_WINDOW_CLOSED"
        assert d["orders"] == [] and d["evaluation"]["entry_window_open"] is False


def test_late_window_cancels_resting_entries(on):
    d = run(book("0.62", "0.63"), p_up="0.70", t=280.0, resting=[order("e", "UP", "0.60")])
    assert d["action"] == C.REPRICE and d["why"] == "ENTRY_WINDOW_CLOSED"
    assert cancels(d) == ["e"] and places(d) == []


def test_late_window_still_allows_a_hedge(on):
    d = run(book("0.62", "0.63"), p_up="0.70", t=285.0, inventory=inv(up=("0.16", "0.096")),
            resting=[order("e", "UP", "0.60")])
    assert d["action"] == C.HEDGE_UNDERDOG
    assert cancels(d) == ["e"]
    (o,) = places(d)
    assert (o["side"], o["price"], o["qty"], o["post_only"]) == ("DOWN", D("0.37"), D("0.16"), True)


# ---------------------------------------------------------------- hedge

def test_hedge_when_pair_at_or_below_target(on):
    # avg 0.61 + DOWN bid 0.37 = 0.98 exactly.
    d = run(book("0.62", "0.63"), p_up="0.70", inventory=inv(up=("0.16", "0.0976")))
    assert d["action"] == C.HEDGE_UNDERDOG and d["why"] == "PAIR_AT_OR_BELOW_TARGET"
    h = d["evaluation"]["hedge"]
    assert (h["side"], h["price"], h["pair_cost"], h["chosen"]) == ("DOWN", D("0.37"), D("0.98"), True)
    (o,) = d["orders"]
    assert (o["side"], o["price"], o["qty"], o["post_only"]) == ("DOWN", D("0.37"), D("0.16"), True)


def test_no_hedge_when_pair_above_target_and_not_flipped(on):
    d = run(book("0.62", "0.63"), inventory=inv(up=("0.16", "0.0992")))
    h = d["evaluation"]["hedge"]
    assert h["pair_cost"] == D("0.99") and h["chosen"] is False and h["why"] == "PAIR_ABOVE_TARGET"
    assert d["action"] == C.HOLD_OR_SKIP and d["orders"] == []


def test_hedge_holding_down_buys_up_at_the_yes_bid(on):
    d = run(book("0.37", "0.38"), inventory=inv(down=("0.16", "0.096")))
    assert d["action"] == C.HEDGE_UNDERDOG
    (o,) = d["orders"]
    assert (o["side"], o["price"], o["qty"]) == ("UP", D("0.37"), D("0.16"))


def test_hedge_on_flip_up_to_the_ceiling(on):
    # Held UP at 0.45; YES 0.44/0.45 -> p_held 0.445 < 0.45, DOWN bid 0.55, pair 1.00.
    d = run(book("0.44", "0.45"), inventory=inv(up=("0.20", "0.09")))
    assert d["action"] == C.HEDGE_UNDERDOG and d["why"] == "FAVORITE_FLIPPED_PAIR_WITHIN_CEILING"
    h = d["evaluation"]["hedge"]
    assert h["flipped"] is True and h["pair_cost"] == D("1.00")
    (o,) = d["orders"]
    assert (o["side"], o["price"], o["qty"]) == ("DOWN", D("0.55"), D("0.20"))


def test_never_hedge_above_the_ceiling_even_when_flipped(on):
    # Same flip, held at 0.46: pair 1.01.
    d = run(book("0.44", "0.45"), inventory=inv(up=("0.20", "0.092")))
    h = d["evaluation"]["hedge"]
    assert h["flipped"] is True and h["pair_cost"] == D("1.01")
    assert h["chosen"] is False and h["why"] == "PAIR_ABOVE_CEILING"
    assert d["action"] == C.HOLD_OR_SKIP and places(d) == []
    # And far above it.
    far = run(book("0.39", "0.40"), inventory=inv(up=("0.16", "0.096")))
    assert far["evaluation"]["hedge"]["why"] == "PAIR_ABOVE_CEILING" and places(far) == []


def test_small_flip_inside_the_margin_does_not_force_a_hedge(on):
    # p_held 0.47 is not below 0.45, and pair 1.00 is above the 0.98 target.
    d = run(book("0.44", "0.45"), p_up="0.47", inventory=inv(up=("0.20", "0.09")))
    assert d["evaluation"]["hedge"]["flipped"] is False
    assert d["evaluation"]["hedge"]["why"] == "PAIR_ABOVE_TARGET"
    assert d["action"] == C.HOLD_OR_SKIP


def test_hedge_quantity_never_exceeds_unmatched(on):
    held = inv(up=("0.50", "0.30"), down=("0.20", "0.074"))     # 0.30 unmatched
    d = run(book("0.62", "0.63"), inventory=held)
    assert d["action"] == C.HEDGE_UNDERDOG and places(d)[0]["qty"] == D("0.30")


def test_hedge_accounts_for_a_resting_hedge(on):
    held = inv(up=("0.50", "0.30"), down=("0.20", "0.074"))
    part = run(book("0.62", "0.63"), inventory=held, resting=[order("h", "DOWN", "0.37", qty="0.10")])
    assert part["action"] == C.HEDGE_UNDERDOG and cancels(part) == []
    assert places(part)[0]["qty"] == D("0.20")
    assert part["evaluation"]["hedge"]["already_working_qty"] == D("0.10")

    full = run(book("0.62", "0.63"), inventory=held, resting=[order("h", "DOWN", "0.37", qty="0.30")])
    assert full["action"] == C.HOLD_OR_SKIP and full["orders"] == []
    assert full["evaluation"]["hedge"]["why"] == "HEDGE_ALREADY_WORKING"

    over = run(book("0.62", "0.63"), inventory=held, resting=[order("h", "DOWN", "0.37", qty="0.50")])
    assert over["action"] == C.REPRICE and cancels(over) == ["h"] and places(over) == []
    assert over["why"] == "HEDGE_EXCEEDS_UNMATCHED"


def test_drifted_hedge_is_cancelled_before_it_is_replaced(on):
    # The old hedge could still fill, so it counts against unmatched until gone.
    held = inv(up=("0.16", "0.096"))
    d = run(book("0.62", "0.63"), inventory=held, resting=[order("h", "DOWN", "0.33")])
    assert d["action"] == C.REPRICE and d["why"] == "PRICE_DRIFT"
    assert cancels(d) == ["h"] and places(d) == []
    nxt = run(book("0.62", "0.63"), inventory=held)
    assert nxt["action"] == C.HEDGE_UNDERDOG and places(nxt)[0]["price"] == D("0.37")


def test_partial_fill_inventories(on):
    d = run(book("0.62", "0.63"), inventory=inv(up=("0.07", "0.042")),
            resting=[order("e", "UP", "0.60", qty="0.09")], p_up="0.70")
    assert d["action"] == C.HEDGE_UNDERDOG and cancels(d) == []
    assert places(d)[0]["qty"] == D("0.07")

    odd = run(book("0.62", "0.63"), inventory=inv(up=("0.163", "0.0978"), down=("0.05", "0.0185")))
    assert odd["exposure"]["unmatched_qty"] == D("0.113")
    assert places(odd)[0]["qty"] == D("0.11")            # floored, never rounded up

    dust = run(book("0.62", "0.63"), inventory=inv(up=("0.165", "0.099"), down=("0.16", "0.0592")))
    assert dust["evaluation"]["hedge"]["why"] == "QTY_BELOW_MIN" and places(dust) == []


# ------------------------------------------------------------- reprice

def test_favorite_flip_cancels_the_old_side_entry(on):
    d = run(book("0.44", "0.45"), resting=[order("e", "UP", "0.50")])
    assert d["favorite"] == "DOWN"
    assert d["action"] == C.REPRICE and d["why"] == "WRONG_SIDE"
    assert cancels(d) == ["e"] and places(d) == []
    assert d["evaluation"]["resting"][0]["verdict"] == "CANCEL"


def test_ttl_expiry_cancels_and_may_replace(on):
    fresh = run(book("0.62", "0.63"), p_up="0.70", resting=[order("e", "UP", "0.60", age=20.0)])
    assert fresh["action"] == C.HOLD_OR_SKIP and fresh["why"] == "ENTRY_ALREADY_RESTING"
    assert fresh["orders"] == []

    old = run(book("0.62", "0.63"), p_up="0.70", resting=[order("e", "UP", "0.60", age=20.5)])
    assert old["action"] == C.OPEN_FAVORITE
    assert old["orders"][0] == {"op": "CANCEL", "id": "e"}
    assert places(old)[0]["price"] == D("0.60")
    assert old["evaluation"]["resting"][0]["why"] == "TTL_EXPIRED"


def test_price_drift_reprices(on):
    for near in ("0.59", "0.60", "0.61"):               # within one tick: left alone
        d = run(book("0.62", "0.63"), p_up="0.70", resting=[order("e", "UP", near)])
        assert d["action"] == C.HOLD_OR_SKIP and d["orders"] == [], near
    for far in ("0.57", "0.58", "0.62"):
        d = run(book("0.62", "0.63"), p_up="0.70", resting=[order("e", "UP", far)])
        assert d["action"] == C.OPEN_FAVORITE and cancels(d) == ["e"], far
        assert places(d)[0]["price"] == D("0.60")


def test_cancel_without_a_passing_replacement_is_reprice(on):
    # EV at the new target is 0.005: the stale quote goes, nothing replaces it.
    d = run(book("0.62", "0.63"), p_up="0.665", resting=[order("e", "UP", "0.57")])
    assert d["action"] == C.REPRICE and d["why"] == "PRICE_DRIFT"
    assert cancels(d) == ["e"] and places(d) == []


def test_resting_entry_that_no_longer_clears_ev_is_cancelled(on):
    d = run(book("0.62", "0.63"), resting=[order("e", "UP", "0.60")])
    assert d["action"] == C.REPRICE and d["why"] == "EV_BELOW_MIN" and cancels(d) == ["e"]


# ---------------------------------------------------------------- taker

EXIT_CASE = dict(inventory=inv(up=("0.16", "0.128")), p_up="0.70")   # held at 0.80


def test_exit_is_never_emitted_with_taker_disabled(on):
    d = run(book("0.80", "0.81"), **EXIT_CASE)
    assert d["action"] == C.HOLD_OR_SKIP and d["orders"] == []
    assert d["evaluation"]["exit"]["blocked"] == "TAKER_DISABLED"


def test_exit_can_be_emitted_when_taker_is_allowed(on):
    d = run(book("0.80", "0.81"), params=C.Params(taker_allowed=True),
            resting=[order("e", "UP", "0.78")], **EXIT_CASE)
    assert d["action"] == C.EXIT_OR_FLIP
    assert cancels(d) == ["e"]
    (o,) = places(d)
    assert (o["side"], o["price"], o["qty"]) == ("UP", D("0.80"), D("0.16"))
    assert o["post_only"] is False and o["intent"] == "SELL"
    x = d["evaluation"]["exit"]
    assert x["taker_fee_per_share"] == D("0.0152")       # 0.095 * 0.8 * 0.2
    assert x["net_per_share"] == D("0.7848") and x["chosen"] is True


def test_exit_needs_the_bid_to_beat_value_after_the_fee(on):
    d = run(book("0.80", "0.81"), params=C.Params(taker_allowed=True),
            inventory=inv(up=("0.16", "0.128")), p_up="0.78")
    assert d["action"] != C.EXIT_OR_FLIP
    assert d["evaluation"]["exit"]["why"] == "BID_NOT_ABOVE_VALUE"


def test_hedge_takes_priority_over_exit(on):
    d = run(book("0.80", "0.81"), params=C.Params(taker_allowed=True),
            inventory=inv(up=("0.16", "0.096")), p_up="0.70")
    assert d["action"] == C.HEDGE_UNDERDOG
    assert all(o["post_only"] for o in places(d))


def _random_case(rng):
    bid = D(rng.randint(1, 97)) / 100
    ask = bid + D(rng.randint(1, min(8, 99 - int(bid * 100)))) / 100
    inventory = {}
    for side in ("UP", "DOWN"):
        qty = D(rng.randint(0, 300)) / 100 if rng.random() < 0.6 else D("0")
        inventory[side] = {"qty": qty, "cost": (qty * D(rng.randint(5, 95)) / 100)}
    resting = [{"id": "r%d" % i, "side": rng.choice(["UP", "DOWN"]),
                "price": D(rng.randint(1, 99)) / 100, "qty": D(rng.randint(1, 60)) / 100,
                "age_s": rng.choice([0.0, 5.0, 19.0, 25.0])}
               for i in range(rng.randint(0, 3))]
    p_up = None if rng.random() < 0.4 else D(rng.randint(0, 100)) / 100
    return dict(book={"bid": bid, "ask": ask, "age_s": rng.choice([0.0, 1.0, 2.9, 4.0])},
                t_in_window_s=rng.choice([0.0, 30.0, 150.0, 269.0, 271.0, 299.0]),
                inventory=inventory, resting=resting, p_up=p_up)


def test_randomized_sweep_never_emits_a_taker_order(on):
    rng = random.Random(20261111)
    seen = set()
    for _ in range(600):
        case = _random_case(rng)
        d = C.decide(**case)
        seen.add(d["action"])
        assert d["action"] != C.EXIT_OR_FLIP
        e = d["exposure"]
        for o in places(d):
            assert o["post_only"] is True and o["intent"] == "BUY"
            assert D("0.01") <= o["price"] <= D("0.99")
            assert o["price"] == o["price"].quantize(D("0.01"))
            assert o["qty"] >= D("0.01") and o["qty"] == o["qty"].quantize(D("0.01"))
            assert o["qty"].as_tuple().exponent == -2
            assert e["total_exposure_usd"] + o["price"] * o["qty"] <= D("5.00")
        if d["action"] == C.HEDGE_UNDERDOG:
            (o,) = places(d)
            h = d["evaluation"]["hedge"]
            assert o["side"] != e["unmatched_side"]
            assert o["qty"] + h["already_working_qty"] <= e["unmatched_qty"]
            assert h["pair_cost"] <= D("1.00")
        if d["action"] in (C.OPEN_FAVORITE, C.ADD_FAVORITE):
            (o,) = places(d)
            assert o["side"] == d["favorite"]
            assert e["total_exposure_usd"] + o["price"] * o["qty"] <= D("3.50")
            assert d["evaluation"]["entry"]["ev_per_share"] >= D("0.01")
        if d["why"] in ("BOOK_STALE", "BOOK_UNUSABLE"):
            assert places(d) == [] and len(cancels(d)) == len(case["resting"])
    # The sweep is only worth something if it reaches the placing branches.
    assert {C.HOLD_OR_SKIP, C.HEDGE_UNDERDOG, C.REPRICE} <= seen
    assert seen & {C.OPEN_FAVORITE, C.ADD_FAVORITE}


def test_randomized_sweep_with_taker_allowed_only_sells_on_exit(on):
    rng = random.Random(7)
    exits = 0
    for _ in range(600):
        d = C.decide(params=C.Params(taker_allowed=True), **_random_case(rng))
        for o in places(d):
            assert (o["post_only"] is False) == (d["action"] == C.EXIT_OR_FLIP)
        exits += d["action"] == C.EXIT_OR_FLIP
    assert exits > 0


# --------------------------------------------------------------- purity

def test_inputs_are_not_mutated_and_output_is_deterministic(on):
    args = dict(book=book("0.62", "0.63"), t_in_window_s=42.0,
                inventory=inv(up=("0.50", "0.30"), down=("0.20", "0.074")),
                resting=[order("h", "DOWN", "0.37", qty="0.10"), order("e", "UP", "0.50", age=30.0)],
                p_up="0.70", p_source="MODEL")
    before = copy.deepcopy(args)
    first = C.decide(**args)
    assert args == before
    assert C.decide(**args) == first
    assert C.decide(**copy.deepcopy(before)) == first
    # The result shares no mutable state with the inputs.
    first["orders"].clear()
    assert args == before and C.decide(**args)["orders"]


def test_module_touches_no_network_or_execution_code():
    src = inspect.getsource(C)
    for banned in ("requests", "urllib", "l5_live_execution", "l5_pmus_live_runner",
                   "l5_railway", "transport"):
        assert banned not in src, banned


# ---------------------------------------------------------- probability

def test_p_up_from_spot():
    assert C.p_up_from_spot(100, 100, 60, 1.0) == D("0.5")
    assert C.p_up_from_spot(101, 100, 1, 1.0) == D("0.841345")     # z = 1
    assert C.p_up_from_spot(99, 100, 1, 1.0) == D("0.158655")
    lo = C.p_up_from_spot("74300", "74287.35", 200, 3.0)
    hi = C.p_up_from_spot("74300", "74287.35", 20, 3.0)
    assert D("0.5") < lo < hi < D("1")                  # less time, more certain


def test_p_up_from_spot_at_expiry_and_unreadable():
    assert C.p_up_from_spot(100, 100, 0, 1.0) == D("1")            # tie goes to UP
    assert C.p_up_from_spot(100.01, 100, -5, None) == D("1")
    assert C.p_up_from_spot(99.99, 100, 0, 1.0) == D("0")
    for bad in [(None, 100, 10, 1), (100, "x", 10, 1), (100, 100, None, 1),
                (100, 100, 10, 0), (100, 100, 10, -1), (100, 100, 10, None),
                (float("nan"), 100, 10, 1), (100, float("inf"), 10, 1), (True, 100, 10, 1)]:
        assert C.p_up_from_spot(*bad) is None, bad
