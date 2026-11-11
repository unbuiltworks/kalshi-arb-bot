"""A DECIDE_COOLDOWN refusal must carry the book it already had.

WHAT WAS WRONG. `l5_book_completion.decide` returns DECIDE_COOLDOWN at step
0, BEFORE the book is read, so every cooldown record carried `pair: None`
and `executable_evidence: false`. On the 2000z window of
camp1.5b2fa83e916c04d7e2871bca that was 98 of 201 refusal records -- and
the 98 were the ones covering REAL held inventory, while the 101 OPENING
refusals in the same window carried the band, the pair and the evidence
basis. So the one question worth asking of that window, "did the cadence
decline a completion that was actually priceable", was unanswerable from
the logs in either direction.

WHAT THIS CHANGE IS, AND IS NOT. Reporting only. The cadence CONDITION is
untouched -- the same 5-second interval, the same refusal, the same HOLD --
and `cooldown_observation` consults no gate, prices no order and returns {}
on any failure. These tests pin both halves: that the record gained the
book, and that nothing about the decision moved.

A PRICE IS NOT A FILL, and these tests assert that the record says so. The
venue publishes no queue position, so at_touch / behind_touch / marketable
are prices a complement COULD be quoted or crossed at, never executions.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC

BOOK = {"bid": "0.62", "ask": "0.64", "stream_age_s": "0.05",
        "bid_shares": "500", "ask_shares": "400"}


def obs(**kw):
    base = dict(held_side="UP", held_qty="2", held_cost_usd="0.77",
                deficient_qty="1", book=dict(BOOK), tick="0.01",
                depth_ticks=1, now_t=1000.0, last_decide_t=997.5)
    base.update(kw)
    return BC.cooldown_observation(**base)


def cooled(**kw):
    """A decide() call that is guaranteed to hit the cadence branch."""
    base = dict(market="m", held_side="UP", held_qty="2",
                held_cost_usd="0.77", deficient_qty="1", book=dict(BOOK),
                now_t=1000.0, window_start=600.0,
                last_decide_t=997.5, depth_ticks=1)
    base.update(kw)
    return BC.decide(**base)


# ================================================ 1. THE CADENCE IS UNCHANGED
def test_the_five_second_cadence_still_refuses():
    r = cooled(now_t=1000.0, last_decide_t=997.5)      # 2.5s < 5.0s
    assert r["reason"] == "DECIDE_COOLDOWN"
    assert r["action"] == "HOLD" and r["ok"] is False


def test_just_inside_the_cadence_still_refuses():
    r = cooled(now_t=1000.0, last_decide_t=995.01)     # 4.99s
    assert r["reason"] == "DECIDE_COOLDOWN"


def test_at_and_beyond_the_cadence_the_branch_is_not_taken():
    """5.0s is NOT a cooldown -- the gate is strictly less-than."""
    r = cooled(now_t=1000.0, last_decide_t=995.0)      # exactly 5.0s
    assert r["reason"] != "DECIDE_COOLDOWN"


def test_no_last_decide_time_is_never_a_cooldown():
    r = cooled(last_decide_t=None)
    assert r["reason"] != "DECIDE_COOLDOWN"


def test_the_cadence_still_precedes_the_inventory_read():
    """Step 0 before step 1: a cooldown fires even with nothing held, which
    is what makes it a cadence gate rather than an inventory one."""
    r = cooled(held_qty="0", held_cost_usd="0", deficient_qty="0")
    assert r["reason"] == "DECIDE_COOLDOWN"


def test_a_cooldown_refusal_carries_no_order():
    r = cooled()
    assert "leg" not in r
    assert r.get("action") == "HOLD"


# ============================================ 2. THE RECORD GAINED THE BOOK
def test_the_cooldown_record_now_carries_the_pair_and_the_evidence():
    r = cooled()
    assert r["evidence"]["executable_evidence"] is True
    assert r["evidence"]["basis"] == "AUTHENTICATED_WS"
    assert "pair_if" in r and "complement_own" in r
    assert r["complement_side"] == "DOWN"


def test_cooldown_remaining_is_reported():
    r = cooled(now_t=1000.0, last_decide_t=997.5)
    assert r["cooldown_remaining_s"] == pytest.approx(2.5)
    assert r["decide_every_s"] == 5.0


# ==================================== 3. THE OWN-PRICE CONVENTION IS CORRECT
def test_held_up_prices_a_down_complement_as_one_minus_wire():
    o = obs(held_side="UP")
    assert o["complement_side"] == "DOWN"
    # ask 0.64 -> DOWN own 0.36; one tick behind means ask 0.65 -> own 0.35
    assert D(o["complement_own"]["at_touch"]) == D("0.36")
    assert D(o["complement_own"]["behind_touch"]) == D("0.35")
    # crossing takes the bid: 1 - 0.62 = 0.38
    assert D(o["complement_own"]["marketable"]) == D("0.38")


def test_held_down_prices_an_up_complement_at_the_wire():
    o = obs(held_side="DOWN")
    assert o["complement_side"] == "UP"
    assert D(o["complement_own"]["at_touch"]) == D("0.62")
    assert D(o["complement_own"]["behind_touch"]) == D("0.61")
    assert D(o["complement_own"]["marketable"]) == D("0.64")


@pytest.mark.parametrize("held", ["UP", "DOWN"])
def test_behind_touch_is_cheaper_and_marketable_is_dearer(held):
    o = obs(held_side=held)
    c = o["complement_own"]
    assert D(c["behind_touch"]) < D(c["at_touch"]) < D(c["marketable"])


def test_the_pair_is_basis_plus_complement():
    o = obs(held_side="UP")          # basis 0.77/2 = 0.385
    assert D(o["held_avg_basis"]) == D("0.385")
    assert D(o["pair_if"]["at_touch"]) == D("0.385") + D("0.36")
    assert D(o["pair_if"]["behind_touch"]) == D("0.385") + D("0.35")
    assert D(o["pair_if"]["marketable"]) == D("0.385") + D("0.38")


def test_the_pair_block_names_the_thresholds_it_is_read_against():
    o = obs()
    assert o["pair_if"]["breakeven_pair"] == str(BC.BREAKEVEN_PAIR)
    assert o["pair_if"]["redemption"] == str(BC.REDEMPTION_PAIR)


# ============================== 4. A PRICE IS NEVER REPORTED AS A FILL
def test_the_record_says_a_price_is_not_a_fill():
    o = obs()
    assert "not fills" in o["complement_own"]["labels"]
    assert "queue" in o["complement_own"]["labels"]
    assert "Conditional, never realized" in o["pair_if"]["labels"]


# ========================================== 5. DEGRADES, NEVER RAISES
def test_an_unusable_book_reports_not_established_and_prices_nothing():
    o = obs(book={"bid": None, "ask": None})
    assert o["evidence"]["executable_evidence"] is False
    assert o["evidence"]["basis"] == "NOT_ESTABLISHED"
    assert "complement_own" not in o and "pair_if" not in o


def test_a_crossed_book_is_unusable():
    o = obs(book={"bid": "0.70", "ask": "0.65", "stream_age_s": "0.1"})
    assert o["book"]["usable"] is False
    assert "complement_own" not in o


def test_a_stale_book_is_not_executable_evidence():
    o = obs(book=dict(BOOK, stream_age_s="999"))
    assert o["evidence"]["executable_evidence"] is False
    # still priced, because the price is an observation, not a permission
    assert "complement_own" in o


def test_a_gapped_book_is_not_executable_evidence():
    o = obs(book=dict(BOOK, gap_suspected=True))
    assert o["evidence"]["executable_evidence"] is False
    assert o["book"]["gap_suspected"] is True


def test_a_missing_stream_age_is_not_executable_evidence():
    b = dict(BOOK)
    b.pop("stream_age_s")
    o = obs(book=b)
    assert o["evidence"]["executable_evidence"] is False


def test_an_unattested_basis_prices_the_legs_but_not_the_pair():
    o = obs(held_cost_usd="0")
    assert o["held_avg_basis"] is None
    assert "complement_own" in o
    assert "pair_if" not in o


@pytest.mark.parametrize("bad", [
    {"book": "not a mapping"},
    {"held_qty": "nonsense"},
    {"tick": object()},
    {"depth_ticks": "two"},
    {"last_decide_t": None},
])
def test_garbage_input_returns_empty_rather_than_raising(bad):
    out = obs(**bad)
    assert isinstance(out, dict)


def test_a_failing_observation_cannot_change_the_refusal():
    """The whole point: a diagnostic that can alter a decision is not one."""
    r = cooled(book="not a mapping")
    assert r["reason"] == "DECIDE_COOLDOWN"
    assert r["ok"] is False and r["action"] == "HOLD"


# ================================================= 6. STRUCTURAL PIN
def test_the_cadence_branch_merges_the_observation_and_nothing_else():
    """Pins that the observation is merged into the record rather than
    replacing the refusal, so a future edit cannot turn it into a gate."""
    import inspect
    src = inspect.getsource(BC.decide)
    i = src.index("DECIDE_EVERY_S")
    branch = src[i:i + 700]
    assert "rec.update(cooldown_observation(" in branch
    assert 'return _no("DECIDE_COOLDOWN", **rec)' in branch
    # the observation must not be able to decide anything
    osrc = inspect.getsource(BC.cooldown_observation)
    for forbidden in ("complement_order", "_would_cross", "action",
                      '"ok": True', "submit"):
        assert forbidden not in osrc, forbidden
