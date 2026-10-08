"""EITHER SIDE MAY OPEN. The lockout that kept E1 from ever trading.

E1 opened on YES unconditionally, and E9 requires the opening price to be at
or below the frozen $0.60 ceiling. So whenever the market leaned more than 60%
to UP, E1 was locked out of the entire window. That is exactly what happened
on 2026-10-08: the YES bid was 0.62, every one of 960 iterations refused with
E9_PRICE_ABOVE_CEILING:0.62, and nothing was placed -- while NO sat at about
0.36, cheaper and fully tradeable, giving the SAME matched pair.

The lockout was also CORRELATED: while BTC trends, consecutive windows lean
the same way, so several windows in a row could be unplaceable.

THE ARITHMETIC THAT MAKES THE FIX SAFE. NO bid = 1 - YES ask, so
`yes_bid + no_bid = 1 - spread`. Both sides can only exceed 0.60 if the book
is crossed by more than 0.20, which is not a market. So in any sane book at
least one side qualifies, and the ceiling stops being a lockout.

AND IT CARRIES NO DIRECTIONAL VIEW. A matched pair is side-agnostic: YES 0.59
then NO 0.37 and NO 0.37 then YES 0.59 are the same $0.96 position. Choosing
which leg opens is a question about which price fits the ceiling.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import pmus_book as B
from analysis.l5.e1_cohort import pmus_identity as PI
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import spec as SPC
from analysis.l5.e1_cohort import stopping as SP

from analysis.l5.e1_cohort.test_e1_live_path import (        # noqa: F401
    FakeTransport, _isolate, arm, fake_pin, _snapshot_from, BASE)

CEIL = SPC.MAX_FIRST_LEG_PRICE
SIDES = PI.outcome_sides({})


# ===================== THE CHOICE, AT UNIT LEVEL =========================

def test_the_2026_10_08_book_now_opens_on_down():
    """THE REGRESSION. 0.62/0.64 locked E1 out of a whole window."""
    c = B.choose_first_leg("0.62", "0.64", max_price=CEIL)
    assert c["eligible_side_exists"] is True
    assert c["side"] == "DOWN"
    assert c["own_side_bid"] == "0.36"          # 1 - 0.64
    assert c["up_own_side_bid"] == "0.62"       # above the ceiling, refused
    assert c["candidates"] == ["DOWN"]


def test_the_cheaper_side_opens_when_both_qualify():
    """A cheaper first leg leaves more headroom under the $0.96 pair."""
    c = B.choose_first_leg("0.50", "0.52", max_price=CEIL)
    assert c["both_sides_qualified"] is True
    # YES 0.50 vs NO 1 - 0.52 = 0.48 -> NO is cheaper
    assert c["side"] == "DOWN"
    assert c["own_side_bid"] == "0.48"


def test_up_opens_when_up_is_the_cheaper_side():
    c = B.choose_first_leg("0.36", "0.38", max_price=CEIL)
    assert c["side"] == "UP"
    assert c["own_side_bid"] == "0.36"


def test_a_tie_goes_to_up_so_the_choice_is_deterministic():
    # yes_bid 0.50, yes_ask 0.50 -> no_bid 0.50. Equal.
    c = B.choose_first_leg("0.50", "0.50", max_price=CEIL)
    assert c["side"] == "UP"
    assert c["both_sides_qualified"] is True
    # and it is stable across repeated calls
    for _ in range(5):
        assert B.choose_first_leg("0.50", "0.50",
                                  max_price=CEIL)["side"] == "UP"


@pytest.mark.parametrize("yb,ya", [
    ("0.62", "0.64"), ("0.50", "0.52"), ("0.36", "0.38"), ("0.01", "0.02"),
    ("0.98", "0.99"), ("0.60", "0.62"), ("0.59", "0.61"), ("0.75", "0.76"),
])
def test_every_sane_book_has_an_eligible_side(yb, ya):
    """The arithmetic claim, exercised across the whole price range."""
    c = B.choose_first_leg(yb, ya, max_price=CEIL)
    assert c["eligible_side_exists"] is True, (yb, ya, c)
    assert D(c["own_side_bid"]) <= CEIL


def test_a_crossed_book_by_more_than_the_ceiling_has_no_eligible_side():
    """The only shape with no candidate is not a market: bid above ask by
    more than 0.20. It is reported, never guessed, and E9 refuses it."""
    c = B.choose_first_leg("0.90", "0.30", max_price=CEIL)   # grossly crossed
    assert c["eligible_side_exists"] is False
    assert c["side"] == "UP"            # so the familiar E9 refusal fires
    assert c["candidates"] == []


def test_the_choice_is_not_a_forecast_and_says_so():
    c = B.choose_first_leg("0.62", "0.64", max_price=CEIL)
    assert c["is_a_forecast"] is False
    assert c["carries_no_directional_view"] is True


@pytest.mark.parametrize("yb,ya,no_up,no_down", [
    # a missing YES bid kills UP as a candidate; DOWN needs only the ask
    (None, "0.64", True, False),
    # a missing YES ask kills DOWN, because NO bid = 1 - YES ask. The YES
    # bid here is UNDER the ceiling, so UP survives for the right reason --
    # 0.62 would have been excluded by the ceiling instead, which would
    # have made this case pass without testing finiteness at all.
    ("0.40", None, False, True),
    ("", "", True, True),
    ("abc", "0.64", True, False),
    ("NaN", "0.64", True, False),
    ("0.40", "sNaN", False, True),
])
def test_absent_or_nonfinite_prices_yield_no_candidate_not_a_guess(
        yb, ya, no_up, no_down):
    """A price that cannot be compared removes its side from the running.
    It is never defaulted to zero, which is how a dead book once became
    eligible at 0.00."""
    c = B.choose_first_leg(yb, ya, max_price=CEIL)
    assert ("UP" in c["candidates"]) is (not no_up), c
    assert ("DOWN" in c["candidates"]) is (not no_down), c
    if no_up and no_down:
        assert c["eligible_side_exists"] is False
        assert c["side"] == "UP"        # so the familiar E9 refusal fires


# ================== THE BOOK PUBLISHES THE ORIENTATION ===================

class _Feed:
    """A PmusDecisionFeed stand-in with one settable quote."""

    def __init__(self, bid, ask):
        self.bid, self.ask = bid, ask

    def snapshot_for(self, slug):
        return {"bid": self.bid, "ask": self.ask, "stream_age_s": 0.01,
                "source": "authenticated_ws", "market_state": "OPEN",
                "bid_shares": "4.00", "ask_shares": "4.00"}


def _book(bid, ask, slug):
    feed = B.PmusDecisionFeed.__new__(B.PmusDecisionFeed)
    feed.clock = lambda: 1000.0
    feed._snapshot_for = lambda s: _Feed(bid, ask).snapshot_for(s)
    return B.PmusDecisionFeed.book(feed, {"slug": slug})


def test_book_puts_the_chosen_side_in_the_first_leg_slot():
    slug = PI.slug_for(int(BASE))
    bk = _book("0.62", "0.64", slug)
    assert bk["ok"] is True
    assert bk["first_leg"]["side"] == "DOWN"
    assert bk["first_leg"]["outcome_side"] == SIDES["down_is"]
    assert bk["first_leg"]["best_bid"] == "0.36"
    assert bk["complement"]["side"] == "UP"
    assert bk["complement"]["outcome_side"] == SIDES["up_is"]
    assert bk["complement"]["best_bid"] == "0.62"


def test_book_always_publishes_both_sides_under_their_own_names():
    """Whichever side opened, a reader can still see the whole book."""
    slug = PI.slug_for(int(BASE))
    for bid, ask in (("0.62", "0.64"), ("0.36", "0.38")):
        bk = _book(bid, ask, slug)
        assert bk["yes"]["side"] == "UP"
        assert bk["yes"]["best_bid"] == bid
        assert bk["no"]["side"] == "DOWN"
        assert bk["no"]["outcome_side"] == SIDES["down_is"]
        assert bk["first_leg_side_choice"]["rule"]


def test_the_two_legs_are_always_distinct_outcome_sides():
    slug = PI.slug_for(int(BASE))
    for bid, ask in (("0.62", "0.64"), ("0.50", "0.52"), ("0.10", "0.12")):
        bk = _book(bid, ask, slug)
        assert (bk["first_leg"]["outcome_side"]
                != bk["complement"]["outcome_side"])
        assert {bk["first_leg"]["outcome_side"],
                bk["complement"]["outcome_side"]} == {SIDES["up_is"],
                                                     SIDES["down_is"]}


# ============== END TO END: THE 0.62 BOOK NOW ACTUALLY PLACES ============

def _snap_sided(side, own_bid, own_ask, comp_bid, comp_ask, *, now=None):
    """A snapshot whose FIRST LEG is `side` at `own_bid`."""
    pin = fake_pin(int(BASE))
    s = _snapshot_from(pin)
    up_is, down_is = SIDES["up_is"], SIDES["down_is"]
    s["first_leg"] = {"side": side, "slug": pin["slug"],
                      "outcome_side": (up_is if side == "UP" else down_is),
                      "best_bid": own_bid, "best_ask": own_ask,
                      "size_at_our_price": "4.00"}
    s["complement"] = {"side": ("DOWN" if side == "UP" else "UP"),
                       "slug": pin["slug"],
                       "outcome_side": (down_is if side == "UP" else up_is),
                       "best_bid": comp_bid, "best_ask": comp_ask}
    s["first_leg_price"] = own_bid
    s["ws_state"] = dict(s["ws_state"],
                         first_leg={"best_bid": own_bid,
                                    "best_ask": own_ask},
                         complement={"best_bid": comp_bid,
                                     "best_ask": comp_ask},
                         book_unix=(s["ws_state"]["book_unix"]
                                    if now is None else float(now)))
    return s


def test_a_down_first_leg_is_eligible_and_places(monkeypatch, tmp_path):
    """THE WHOLE POINT. The 2026-10-08 state, opened on DOWN, places."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    arm(monkeypatch)
    # YES 0.62/0.64 -> DOWN own-side 0.36/0.38, complement YES 0.62/0.64
    snap = _snap_sided("DOWN", "0.36", "0.38", "0.62", "0.64")
    ok = R.e1_preflight(snap, SP.CohortTracker(), now=BASE + 10)
    assert ok["ok"] is True, ok["refusals"]

    out = R.run_cohort(snapshot_provider=lambda: snap,
                       tracker=SP.CohortTracker(),
                       transport_factory=FakeTransport,
                       now_fn=lambda: BASE + 10, sleep_fn=lambda s: None,
                       max_iterations=1)
    assert out["placement_count"] == 1, out["refusals"]
    post = FakeTransport.posts[0]
    assert post["post_only"] is True
    assert post["quantity"] == 0.13
    # THE WIRE CONVENTION, the one A5 died of and A6 proved: price.value is
    # always the LONG side's price, so a DOWN order at own-side 0.36 goes on
    # the wire as 1 - 0.36 = 0.64.
    assert post["price"] == "0.64", post


def test_the_same_state_opened_on_up_is_still_refused_by_the_ceiling(
        monkeypatch, tmp_path):
    """The ceiling is UNTOUCHED. Opening the 0.62 side is still refused."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    snap = _snap_sided("UP", "0.62", "0.64", "0.36", "0.38")
    checks = R.e1_preflight(snap, SP.CohortTracker(), now=BASE + 10)
    assert checks["ok"] is False
    assert any(str(x).startswith("E9_PRICE_ABOVE_CEILING")
               for x in checks["refusals"]), checks["refusals"]


def test_boundary_2_accepts_a_down_first_leg(monkeypatch, tmp_path):
    """The latent conflation this fix had to repair: boundary 2 is about the
    MARKET's two outcome sides, not which leg happens to be opening."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    arm(monkeypatch)
    from analysis.l5.e1_cohort import authorization as AUTH
    pin = fake_pin(int(BASE))
    auth = AUTH.SessionAuthorization(str(tmp_path / "e1_cohort"))
    auth.authorize_selected(pin)
    snap = _snap_sided("DOWN", "0.36", "0.38", "0.62", "0.64")
    out = R.submit_first_leg(snap, SP.CohortTracker(), now=BASE + 10,
                             transport_factory=FakeTransport,
                             session_auth=auth)
    assert out["order_id"]
    # the submitted body names the NO side and carries the LONG-side wire
    # price 1 - 0.36 = 0.64
    assert FakeTransport.posts[-1]["price"] == "0.64"
    assert out["e1_window"] == snap["window"]


def test_a_leg_naming_a_side_outside_the_pair_is_refused(monkeypatch,
                                                         tmp_path):
    """Fail closed: a first leg whose outcome side is not one of the
    market's two is refused before any transport exists."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    from analysis.l5.e1_cohort import authorization as AUTH
    pin = fake_pin(int(BASE))
    auth = AUTH.SessionAuthorization(str(tmp_path / "e1_cohort"))
    auth.authorize_selected(pin)
    snap = _snap_sided("DOWN", "0.36", "0.38", "0.62", "0.64")
    snap["first_leg"] = dict(snap["first_leg"],
                             outcome_side="OUTCOME_SIDE_SOMETHING_ELSE")
    with pytest.raises(AUTH.NotAuthorized):
        R.submit_first_leg(snap, SP.CohortTracker(), now=BASE + 10,
                           transport_factory=FakeTransport,
                           session_auth=auth)


# ===================== NOTHING ELSE MOVED ================================

def test_no_bound_changed_by_the_symmetry_fix():
    assert str(SPC.MAX_FIRST_LEG_PRICE) == "0.60"
    assert str(SPC.PAIR_COST_CEILING) == "0.96"
    assert str(SPC.FIRST_LEG_QTY) == "0.13"
    assert SPC.MAX_ATTEMPTS_PER_WINDOW == 20
    assert SPC.WINDOWS == 12
    assert str(SPC.MAX_EXPOSURE_PER_WINDOW_USD) == "1.56"
    assert str(SPC.AUTHORIZED_WINDOW_CEILING_USD) == "5.00"


def test_the_pair_is_still_capped_whichever_side_opened(monkeypatch,
                                                        tmp_path):
    """A DOWN first leg at 0.36 may only complete at a price that keeps the
    fee-inclusive pair at or under $0.96."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    snap = _snap_sided("DOWN", "0.36", "0.38", "0.62", "0.64")
    checks = R.e1_preflight(snap, SP.CohortTracker(), now=BASE + 10)
    assert checks["ok"] is True, checks["refusals"]
    plan = checks["e1"]["complement_plan"]
    assert plan is not None
    total = D(str(plan["price_usd"])) + D("0.36")
    assert total <= SPC.PAIR_COST_CEILING, (plan, total)
