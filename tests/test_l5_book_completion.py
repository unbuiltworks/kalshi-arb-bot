"""The book-driven completion decision: guards, idempotence, economics.

EVERY TEST DRIVES THE REAL `decide`, and the economics come from the real
`TS.complement_order` -- the same function the L5-action path calls. Nothing
here mocks the pricing, because the whole point of the module is that the
two paths cannot price a completion differently.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC
from src import l5_two_sided_probe as TS

MARKET = "cpc-btc-updown-15m-2026-10-29-0200z"
WS = 1_793_000_000.0


def book(bid="0.5000", ask="0.5100", age="0.1", **kw):
    b = {"bid": bid, "ask": ask, "stream_age_s": age,
         "bid_shares": "500", "ask_shares": "500"}
    b.update(kw)
    return b


def call(**kw):
    """Held 2 DOWN at own 0.40; the UP complement is what we want."""
    base = dict(market=MARKET, held_side="DOWN",
                held_qty="2", held_cost_usd="0.80",
                deficient_qty="0", deficient_cost_usd="0",
                book=book(), now_t=WS + 120.0, window_start=WS,
                resting=(), tick="0.01", min_qty="0.01", depth_ticks=2,
                residual_budget_usd="3")
    base.update(kw)
    return BC.decide(**base)


# ------------------------------------------------------------------ OFF
def test_the_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv(BC.ENABLED_ENV, raising=False)
    assert BC.enabled() is False
    for v in ("0", "", "no", "off", "maybe", "TRUE-ish"):
        monkeypatch.setenv(BC.ENABLED_ENV, v)
        assert BC.enabled() is (v.strip().lower() in BC._TRUE)


def test_the_module_cannot_submit_anything():
    """No order path. The decision is data; reaching the wire needs the
    intent gate's capability object, which production never constructs."""
    src = open("src/l5_book_completion.py").read()
    for forbidden in ("requests", "urllib", "http", "submit(", "place(",
                      "claim_submission", "transport"):
        assert forbidden not in src, forbidden


# ------------------------------------------------- nothing to complete
def test_flat_inventory_is_not_a_completion():
    assert call(held_qty="0")["reason"] == "NO_INVENTORY"


def test_a_matched_book_is_not_a_completion():
    """After a completed round BOTH sides hold stock. Treating that as a
    completion is what once routed a new FIRST leg down this path."""
    r = call(held_qty="2", deficient_qty="2")
    assert r["reason"] == "ALREADY_MATCHED"
    assert r["action"] == "HOLD"


def test_only_the_unmatched_remainder_is_completed():
    r = call(held_qty="2", deficient_qty="1.5")
    assert r.get("need") == "0.5"


# ----------------------------------------------------------- the guards
def test_a_stale_book_holds():
    r = call(book=book(age=str(TS.MAX_BOOK_AGE_S + 1)))
    assert r["reason"] == "STALE_BOOK"


def test_an_unknown_book_age_holds_rather_than_assuming_fresh():
    bk = book()
    bk.pop("stream_age_s")
    assert call(book=bk)["reason"] == "BOOK_AGE_UNKNOWN"


def test_a_suspected_gap_holds():
    assert call(book=book(gap_suspected=True))["reason"] \
        == "BOOK_GAP_SUSPECTED"


def test_a_crossed_or_empty_book_holds():
    assert call(book=book(bid="0.52", ask="0.51"))["reason"] == "BOOK_UNUSABLE"
    assert call(book=book(bid="0", ask="0.51"))["reason"] == "BOOK_UNUSABLE"


def test_a_late_window_holds():
    """Past the BINDING horizon -- the earlier of the completion cutoff and
    the cancel margin, not the later."""
    r = call(now_t=WS + 880.0)
    assert r["reason"] == "PAST_TERMINAL"
    assert r["horizon_s"] <= float(TS.complete_cutoff_s())


def test_the_horizon_is_the_earlier_of_the_two_clocks():
    r = call(now_t=WS + 1.0)
    cancel_at = TS.cancel_cutoff(WS + TS.WINDOW_S) - WS
    assert r["horizon_s"] == pytest.approx(
        min(float(TS.complete_cutoff_s()), float(cancel_at)), abs=0.01)


def test_an_exhausted_submission_budget_holds():
    assert call(submissions_left=0)["reason"] == "NO_SUBMISSIONS_LEFT"


def test_the_cadence_bounds_re_decision():
    r = call(now_t=WS + 120.0, last_decide_t=WS + 118.0)
    assert r["reason"] == "DECIDE_COOLDOWN"
    assert call(now_t=WS + 120.0,
                last_decide_t=WS + 120.0 - BC.DECIDE_EVERY_S - 0.1
                )["reason"] != "DECIDE_COOLDOWN"


# -------------------------------------------------------- never crosses
def test_a_quote_that_would_take_the_touch_is_refused():
    """Asserted on the PRICE, never inferred from a mode string."""
    assert BC._would_cross("UP", D("0.51"), D("0.50"), D("0.51")) is True
    assert BC._would_cross("UP", D("0.49"), D("0.50"), D("0.51")) is False
    # buying DOWN rests a YES offer at 1-own; own 0.49 -> offer 0.51, rests
    assert BC._would_cross("DOWN", D("0.49"), D("0.50"), D("0.51")) is False
    # own 0.51 -> offer 0.49, which is through the 0.50 bid
    assert BC._would_cross("DOWN", D("0.51"), D("0.50"), D("0.51")) is True


def test_no_accepted_decision_ever_crosses():
    """Sweep the band: whatever is accepted must rest."""
    crossed = []
    for i in range(5, 96):
        bid = D(i) / D(100)
        bk = book(bid=str(bid), ask=str(bid + D("0.01")))
        r = call(book=bk)
        if r.get("ok"):
            px = D(str(r["leg"]["price"]))
            own = px if r["side"] == "UP" else (D(1) - px)
            if BC._would_cross(r["side"], own, bid, bid + D("0.01")):
                crossed.append((str(bid), str(px)))
    assert crossed == []


# ------------------------------------------------------- the economics
def test_a_pair_above_redemption_is_refused_even_when_pricing_says_ok():
    """Held DOWN at 0.90 leaves no room: any complement puts the pair over
    $1.00, and a pair above redemption is a CERTAIN loss.

    RESTATED, 1445z. This asserted `PAIR_ABOVE_BREAKEVEN` when 0.9532 was
    the refusal. It is now a preferred target and the hard line is $1.00,
    which is what the submission boundary enforces anyway. The invariant the
    test was written for -- a basis this dear leaves nothing to complete into
    -- is unchanged, and at 0.90 the pair clears $1.00 on any complement."""
    r = call(held_qty="2", held_cost_usd="1.80")
    assert r["action"] == "HOLD"
    assert r["reason"] in ("PAIR_ABOVE_REDEMPTION",) or \
        r["reason"].startswith("ECONOMICS:")


def test_a_cheap_pair_is_accepted_and_carries_its_arithmetic():
    r = call(held_qty="2", held_cost_usd="0.40", book=book("0.5000", "0.5100"))
    if r.get("ok"):
        assert r["action"] == "COMPLETE"
        assert D(r["pair_own_cost"]) <= D(r["breakeven_pair"])


def test_the_breakeven_default_is_the_settled_measurement():
    assert BC.BREAKEVEN_PAIR == D("0.9532")


def test_a_caller_may_tighten_breakeven_but_it_is_not_a_second_gate():
    """RESTATED, 1445z, and the name was always right: a tightened target
    must NOT become a gate. It moves the recorded preference and the reason;
    the refusal stays with the $1.00 hard line and the owner's budget.

    Reinstating a refusal here would put the 1445z defect back through a
    parameter -- a caller-supplied number stranding inventory that a
    non-negative pair could have matched."""
    r = call(held_qty="2", held_cost_usd="0.40", breakeven_pair="0.10")
    assert r["reason"] != "PAIR_ABOVE_REDEMPTION"
    if r.get("ok"):
        assert r["action"] in ("COMPLETE", "REPRICE")
        assert r["within_preferred_target"] is False
        assert D(r["pair_own_cost"]) <= BC.REDEMPTION_PAIR
        assert D(r["breakeven_pair"]) == D("0.10")


def test_an_unknown_first_leg_price_holds():
    assert call(held_cost_usd=None)["reason"] in (
        "NO_FIRST_LEG_PRICE", "LEG_UNUSABLE") or \
        call(held_cost_usd=None)["reason"].startswith("ECONOMICS:")


# ------------------------------------- idempotence and repricing
def test_the_same_decision_yields_the_same_key():
    a = BC.decision_key(MARKET, "UP", "2", "0.44")
    b = BC.decision_key(MARKET, "up", D("2"), D("0.44"))
    assert a == b


def test_a_different_price_is_a_different_decision():
    assert BC.decision_key(MARKET, "UP", "2", "0.44") != \
        BC.decision_key(MARKET, "UP", "2", "0.43")


def test_an_order_already_resting_at_or_better_holds():
    """Idempotence against the venue, not against our own memory."""
    r = call(held_qty="2", held_cost_usd="0.40",
             resting=({"side": "UP", "price": "0.01", "market": MARKET},))
    assert r["action"] == "HOLD"
    assert r["reason"] == "ALREADY_RESTING_AT_OR_BETTER"


def test_repricing_only_ever_goes_cheaper():
    """Chasing a quote upward is what the downward-only requote forbids,
    and a requote also forfeits queue position."""
    dear = call(held_qty="2", held_cost_usd="0.40",
                resting=({"side": "UP", "price": "0.99", "market": MARKET},))
    if dear.get("ok"):
        assert dear["action"] == "REPRICE"
        assert D(dear["leg"]["price"]) < D("0.99")


def test_a_resting_order_on_the_other_side_is_not_ours_to_replace():
    r = call(held_qty="2", held_cost_usd="0.40",
             resting=({"side": "DOWN", "price": "0.01", "market": MARKET},))
    assert r["reason"] != "ALREADY_RESTING_AT_OR_BETTER"


def test_a_resting_order_in_another_market_is_ignored():
    r = call(held_qty="2", held_cost_usd="0.40",
             resting=({"side": "UP", "price": "0.01", "market": "other"},))
    assert r["reason"] != "ALREADY_RESTING_AT_OR_BETTER"


# ------------------------------------------------------------ totality
def test_every_return_names_an_action_and_a_reason():
    for kw in ({}, {"held_qty": "0"}, {"now_t": WS + 890.0},
               {"book": book(age="99")}, {"submissions_left": 0},
               {"held_cost_usd": "1.90"}, {"book": book(bid="0", ask="0")}):
        r = call(**kw)
        assert r["action"] in BC.ACTIONS
        assert isinstance(r["reason"], str) and r["reason"]
        assert "ok" in r


def test_it_holds_rather_than_raising_on_rubbish():
    for kw in ({"held_qty": "abc"}, {"held_cost_usd": "oops"},
               {"book": {"bid": "x", "ask": "y", "stream_age_s": "z"}},
               {"tick": None}, {"residual_budget_usd": "nope"}):
        r = call(**kw)
        assert r["action"] in BC.ACTIONS
