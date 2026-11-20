"""A leg that extends the imbalance needs its own reason once its partner
can no longer be bought.

Live window 2026-11-20 17:05Z: UP reached its $0.30 side cap at t=119 s.
From then only DOWN was quoted, and it filled eight more times at 0.20
falling to 0.01 while UP ran from 0.84 to 0.99. The dollar unmatched
ceiling did not bind, because cheap contracts are cheap.

THESE TESTS PIN A DECISION RULE, NOT AN EDGE. Nothing here says the lone
legs the rule admits are profitable; it says a lone leg must clear the
same independent-probability test the single-sided path already uses.
"""
from __future__ import annotations

import itertools
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X


@pytest.fixture(autouse=True)
def _controller_on(monkeypatch):
    monkeypatch.setenv("L5_BTC5M_CONTROLLER", "ON")


def inv(up_qty="0", up_cost="0", dn_qty="0", dn_cost="0"):
    return {C.UP: {"qty": up_qty, "cost": up_cost},
            C.DOWN: {"qty": dn_qty, "cost": dn_cost}}


def tup(i):
    return {s: (D(i[s]["qty"]), D(i[s]["cost"])) for s in C.SIDES}


def live_params(**kw):
    """The 17:05Z window's effective configuration."""
    base = dict(entry_mode=C.STRADDLE, straddle_price=D("0.08"),
                clip_usd=D("0.05"), hedge_enabled=False, reserve_mode="NONE",
                max_exposure_usd=D("0.60"), straddle_book_relative=True,
                straddle_fill_model_measured=True,
                straddle_pair_ceiling=D("0.99"),
                straddle_repeat_enabled=True,
                straddle_max_side_usd=D("0.30"),
                straddle_max_unmatched_usd=D("0.10"),
                straddle_requote_ticks=2,
                straddle_max_window_loss_usd=D("0.20"),
                straddle_leg_max_age_s=240.0,
                straddle_ev_waiver=C.EV_WAIVER_TOKEN)
    base.update(kw)
    return C.Params(**base)


def decide(inventory, bid, ask, *, p_up=None, p_source="VENUE_INDEX",
           p_age_s=1.0, resting=(), t=130.0, **kw):
    return C.decide(book={"bid": bid, "ask": ask, "age_s": 0.1},
                    t_in_window_s=t, inventory=inventory,
                    resting=list(resting), params=live_params(**kw),
                    p_up=p_up, p_source=p_source, p_age_s=p_age_s)


def legs(res, side=None):
    return [o for o in res["orders"] if o.get("op", "PLACE") == "PLACE"
            and o.get("reason") == "STRADDLE_LEG"
            and (side is None or o["side"] == side)]


#: inventory at t=130 s of the live window: UP at its side cap, DOWN level
CAPPED = inv("0.41", "0.2677", "0.43", "0.1154")
ON = dict(straddle_one_sided_control=True)


# ====================================================== the live defect

def test_the_live_behaviour_reproduced_without_the_control():
    r = decide(CAPPED, "0.82", "0.83", p_up="0.82")
    assert legs(r, C.UP) == []                     # UP is at its cap
    assert len(legs(r, C.DOWN)) == 1               # and DOWN keeps buying


def test_a_lone_leg_the_model_does_not_support_is_refused():
    r = decide(CAPPED, "0.82", "0.83", p_up="0.82", **ON)
    assert legs(r) == []
    v = r["evaluation"]["one_sided"][C.DOWN]
    assert v["why"] == "ONE_SIDED_NO_EDGE" and v["allowed"] is False
    assert v["partner_open"] is False
    #: p_down 0.18, price 0.17, adverse 0.06
    assert D(v["lone_edge"]) == D("0.18") - D("0.17") - D("0.06")
    assert r["evaluation"]["side_refusals"][C.DOWN] == "ONE_SIDED_NO_EDGE"


def test_it_is_not_a_ban_a_lone_leg_with_edge_is_placed():
    r = decide(CAPPED, "0.82", "0.83", p_up="0.60", **ON)
    assert len(legs(r, C.DOWN)) == 1
    v = r["evaluation"]["one_sided"][C.DOWN]
    assert v["why"] == "LONE_EDGE_OK"
    assert D(v["lone_edge"]) == D("0.40") - D("0.17") - D("0.06")


def test_the_edge_must_clear_the_minimum_not_merely_be_positive():
    #: edge = 0.24 - 0.17 - 0.06 = 0.01, exactly min_edge_per_share
    at = decide(CAPPED, "0.82", "0.83", p_up="0.76", **ON)
    assert len(legs(at, C.DOWN)) == 1
    below = decide(CAPPED, "0.82", "0.83", p_up="0.761", **ON)
    assert legs(below) == []


@pytest.mark.parametrize("kw,why", [
    (dict(p_up=None), "ONE_SIDED_P_UNKNOWN"),
    (dict(p_up="0.60", p_source="BOOK"), "ONE_SIDED_P_UNKNOWN"),
    (dict(p_up="0.60", p_source=None), "ONE_SIDED_P_UNKNOWN"),
    (dict(p_up="0.60", p_age_s=None), "ONE_SIDED_P_UNKNOWN"),
    (dict(p_up="0.60", p_age_s=5.1), "ONE_SIDED_P_UNKNOWN"),
    (dict(p_up="0.60", p_age_s=-1), "ONE_SIDED_P_UNKNOWN"),
    (dict(p_up="1.5"), "ONE_SIDED_P_UNKNOWN"),
    (dict(p_up="not-a-number"), "ONE_SIDED_P_UNKNOWN"),
])
def test_without_a_fresh_independent_probability_it_refuses(kw, why):
    r = decide(CAPPED, "0.82", "0.83", **ON, **kw)
    assert legs(r) == []
    assert r["evaluation"]["one_sided"][C.DOWN]["why"] == why


# ================================= what it must NOT interfere with

def test_two_sided_quoting_from_flat_is_untouched():
    off = decide(inv(), "0.50", "0.51", t=5.0)
    on = decide(inv(), "0.50", "0.51", t=5.0, **ON)        # no p at all
    assert legs(on) == legs(off) and len(legs(on)) == 2
    for s in C.SIDES:
        assert on["evaluation"]["one_sided"][s]["why"] == "PARTNER_QUOTABLE"


def test_temporary_imbalance_is_tolerated_while_the_partner_is_open():
    lop = inv("0.09", "0.0405", "0", "0")              # one UP fill, no DOWN
    off = decide(lop, "0.45", "0.47", t=12.0)
    on = decide(lop, "0.45", "0.47", t=12.0, **ON)
    assert legs(on) == legs(off)
    assert legs(on, C.UP), "the heavy side stopped quoting while DOWN is open"


def test_a_completing_leg_needs_no_probability():
    #: UP capped, DOWN still SHORT of it: buying DOWN completes pairs
    short = inv("0.41", "0.2677", "0.20", "0.066")
    r = decide(short, "0.82", "0.83", p_up=None, **ON)
    assert len(legs(r, C.DOWN)) == 1
    v = r["evaluation"]["one_sided"][C.DOWN]
    assert v["why"] == "COMPLETES_PAIR" and D(v["extends"]) == 0
    #: and the comparison it rests on is on the record
    assert D(v["pair_cost"]) == D("0.2677") / D("0.41") + D(v["price"])


def test_a_leg_that_partly_completes_is_judged_on_the_part_that_extends():
    part = inv("0.41", "0.2677", "0.39", "0.11")       # 0.02 short, clip 0.06
    r = decide(part, "0.82", "0.83", p_up="0.82", **ON)
    v = r["evaluation"]["one_sided"][C.DOWN]
    assert D(v["completes"]) == D("0.02") and D(v["extends"]) > 0
    assert v["allowed"] is False and legs(r) == []


def test_a_working_partner_quote_counts_as_open():
    up_resting = [{"id": "u1", "side": C.UP, "price": D("0.82"),
                   "qty": D("0.01"), "age_s": 1.0, "reason": "STRADDLE_LEG"}]
    r = decide(CAPPED, "0.82", "0.83", p_up="0.82", resting=up_resting, **ON)
    v = r["evaluation"]["one_sided"][C.DOWN]
    assert v is not None and v["why"] == "PARTNER_QUOTABLE"


def test_orders_already_working_on_this_side_count_toward_the_extension():
    level = inv("0.41", "0.2677", "0.35", "0.10")      # DOWN 0.06 short
    dn_resting = [{"id": "d1", "side": C.DOWN, "price": D("0.10"),
                   "qty": D("0.06"), "age_s": 1.0, "reason": "OTHER"}]
    v = C.one_sided_verdict(
        side=C.DOWN, price=D("0.17"), qty=D("0.06"), inv=tup(level),
        rest=dn_resting, partner_open=False, p_up=D("0.82"),
        p_source="VENUE_INDEX", p_age_s=1.0, params=live_params(**ON))
    assert D(v["extends"]) == D("0.06") and v["allowed"] is False


# ======================================== it can only add a refusal

BOOKS = [("0.50", "0.51"), ("0.82", "0.83"), ("0.20", "0.22"),
         ("0.95", "0.96")]
INVS = [inv(), CAPPED, inv("0.09", "0.0405"), inv("0", "0", "0.30", "0.09"),
        inv("0.41", "0.2677", "0.20", "0.066")]
PS = [None, "0.30", "0.60", "0.82"]


@pytest.mark.parametrize("book,inventory,p", list(
    itertools.product(BOOKS, INVS, PS)))
def test_it_never_adds_an_order_and_never_changes_one(book, inventory, p):
    off = decide(inventory, *book, p_up=p)
    on = decide(inventory, *book, p_up=p, **ON)
    key = lambda o: (o["side"], str(o["price"]), str(o["qty"]))  # noqa: E731
    assert {key(o) for o in legs(on)} <= {key(o) for o in legs(off)}
    cancels = lambda r: sorted(o["id"] for o in r["orders"]      # noqa: E731
                               if o.get("op") == "CANCEL")
    assert cancels(on) == cancels(off)


def test_off_is_the_default_and_leaves_no_trace_in_the_decision():
    assert C.Params().straddle_one_sided_control is False
    r = decide(CAPPED, "0.82", "0.83", p_up="0.82")
    assert r["evaluation"]["one_sided"] == {C.UP: None, C.DOWN: None}


def test_the_other_limits_still_bind_with_it_on():
    #: the side cap still stops UP whatever the model says
    r = decide(CAPPED, "0.82", "0.83", p_up="0.99", **ON)
    assert legs(r, C.UP) == []
    assert r["evaluation"]["side_refusals"][C.UP] == "SIDE_CAP_REACHED"


# ================================================= the switch

@pytest.mark.parametrize("raw,want", [
    (None, False), ("", False), ("1", False), ("true", False), ("YES", False),
    ("require_edge_when_partner_closed", False),
    (X.ONE_SIDED_TOKEN, True), (" %s " % X.ONE_SIDED_TOKEN, True)])
def test_only_the_exact_token_switches_it_on(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv(X.STRADDLE_ONE_SIDED_ENV, raising=False)
    else:
        monkeypatch.setenv(X.STRADDLE_ONE_SIDED_ENV, raw)
    assert X.straddle_one_sided_control() is want


# ===================================== the live window, replayed

LIVE = [  # t, DOWN price, qty, model p_down logged that step, filled?
    ("119.10", "0.15", "0.05", "0.241", False),
    ("124.50", "0.22", "0.06", "0.245", False),
    ("130.94", "0.20", "0.06", "0.184", True),
    ("136.41", "0.17", "0.06", "0.161", False),
    ("142.98", "0.17", "0.06", "0.169", True),
    ("148.42", "0.13", "0.05", "0.190", True),
    ("164.09", "0.08", "0.05", None, True),
    ("169.53", "0.08", "0.05", None, False),
    ("181.88", "0.11", "0.05", "0.189", True),
    ("188.82", "0.06", "0.05", "0.200", False),
    ("194.45", "0.04", "0.05", "0.099", True),
    ("200.04", "0.02", "0.05", "0.079", True),
    ("205.37", "0.01", "0.05", "0.042", True),
]


def test_the_thirteen_lone_legs_of_the_live_window():
    """What the rule would have said, on the probability logged AT THE
    TIME -- not on how the window settled."""
    refused, kept = [], []
    for t, px, q, p_dn, filled in LIVE:
        v = C.one_sided_verdict(
            side=C.DOWN, price=D(px), qty=D(q), inv=tup(CAPPED), rest=[],
            partner_open=False,
            p_up=None if p_dn is None else D("1") - D(p_dn),
            p_source="VENUE_INDEX", p_age_s=1.7, params=live_params(**ON))
        (kept if v["allowed"] else refused).append((t, px, q, filled))
    assert len(refused) == 10 and len(kept) == 3
    #: it is a rule about the price paid, not hindsight: three legs the
    #: model supported are kept although the window went against them
    assert [k[0] for k in kept] == ["119.10", "181.88", "188.82"]
    avoided = sum(D(px) * D(q) for _t, px, q, f in refused if f)
    assert avoided == D("0.0362")
    assert sum(1 for r in refused if r[3]) == 7
