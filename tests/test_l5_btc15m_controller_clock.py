"""BTC15m adaptation of the inventory-driven controller: the real clock.

WHY. The controller in `pmus_sports_l5_decision` is genuinely
inventory-driven -- `plan_action` overrides the model's relation from OUR
residual, picks the DEFICIT side for a completion, prices it from OUR OWN
average cost via `complement_target`, extends the SURPLUS side, and stops at
MAX_RESIDUAL_UNITS. What it was not is time-aware: a sports market has no
settlement clock, so `_proposal` handed `state_key` the sentinel
SPORTS_TTE_DEFAULT_S = 1e9 and every lookup landed in `tte>10m`.

A BTC15m window is 900 SECONDS and expires. `action_model.TTE_BUCKETS` are
tte<30s / tte30-120s / tte2-5m / tte5-10m / tte>10m, so a real window
traverses FOUR of them while the sentinel reaches only the last. The
authorized placement band -- 600 to 450 seconds remaining -- is `tte5-10m`,
and under the sentinel it read `tte>10m`: a bucket L5's own BTC15m history
never occupied at that point in a window.

WHAT IS PINNED HERE: sports is unchanged by default; BTC15m gets the real
seconds-to-expiry at the moment of each decision and never `now` leaked into
a past state; the tighter spring pair cap tightens completion and the >=1c
hard bound still always participates.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_sports_l5_decision as dec
from src.polymarket_l5.action_model import TTE_BUCKETS, _bucket, state_key

WS = 1792415700                      # a real 1315z window start
WEND = WS + 900                      # its close


# ------------------------------------------------- the clock helper itself

def test_no_settlement_clock_keeps_the_sports_sentinel():
    """Sports must be untouched: None means the old behaviour exactly."""
    assert dec.tte_at(None, 0.0) == dec.SPORTS_TTE_DEFAULT_S
    assert dec.tte_at(None, WS + 123.0) == dec.SPORTS_TTE_DEFAULT_S


@pytest.mark.parametrize("elapsed,want", [
    (0.0, 900.0), (300.0, 600.0), (450.0, 450.0),
    (870.0, 30.0), (900.0, 0.0),
])
def test_the_clock_is_seconds_to_the_close(elapsed, want):
    assert dec.tte_at(WEND, WS + elapsed) == want


def test_past_the_close_is_floored_not_negative():
    """An expiring book is `tte<30s`, never a negative state."""
    assert dec.tte_at(WEND, WEND + 60.0) == 0.0
    assert _bucket(dec.tte_at(WEND, WEND + 60.0), TTE_BUCKETS) == "tte<30s"


# ---------------------------------------- the buckets a window really sees

def test_the_sentinel_reaches_only_one_bucket():
    seen = {_bucket(dec.tte_at(None, WS + e), TTE_BUCKETS)
            for e in range(0, 901, 15)}
    assert seen == {"tte>10m"}, seen


def test_the_real_clock_traverses_four_buckets():
    seen = {_bucket(dec.tte_at(WEND, WS + e), TTE_BUCKETS)
            for e in range(0, 901, 5)}
    assert seen == {"tte>10m", "tte5-10m", "tte2-5m",
                    "tte30-120s", "tte<30s"}, seen


@pytest.mark.parametrize("elapsed", [300.1, 375.0, 450.0])
def test_the_authorized_placement_band_is_tte5_10m(elapsed):
    """THE REGRESSION. Inside the band the real state is `tte5-10m`; the
    sentinel read `tte>10m`, so every decision there was looked up in a
    bucket L5's BTC15m history never occupied at that point in a window."""
    real = _bucket(dec.tte_at(WEND, WS + elapsed), TTE_BUCKETS)
    sentinel = _bucket(dec.tte_at(None, WS + elapsed), TTE_BUCKETS)
    assert real == "tte5-10m"
    assert sentinel == "tte>10m"
    assert real != sentinel


def test_the_band_opens_exactly_on_a_bucket_boundary():
    """`_bucket` is EXCLUSIVE at the upper edge, so tte of exactly 600s --
    the instant T+5:00, when the placement band opens -- is still
    `tte>10m`, and the next tick of the clock is `tte5-10m`.

    Not a defect, and worth pinning: the live order of 2026-10-19 1315z was
    placed at elapsed 301.494s (tte 598.5), which is inside `tte5-10m`. A
    placement at exactly T+5:00 would query the bucket above it.
    """
    assert _bucket(600.0, TTE_BUCKETS) == "tte>10m"
    assert _bucket(599.9, TTE_BUCKETS) == "tte5-10m"
    assert _bucket(dec.tte_at(WEND, WS + 301.494), TTE_BUCKETS) == "tte5-10m"


def test_the_state_key_differs_between_sentinel_and_real_clock():
    """The lookup key itself moves, which is the whole point."""
    k_real = state_key(0.50, dec.tte_at(WEND, WS + 375.0), 0.0)
    k_sent = state_key(0.50, dec.tte_at(None, WS + 375.0), 0.0)
    assert k_real != k_sent
    assert "tte5-10m" in k_real and "tte>10m" in k_sent


# ----------------------------------------------- no hindsight in the clock

def test_an_earlier_decision_gets_more_clock_not_less():
    """A decision made nine minutes ago was made with nine minutes more
    clock. Dating tte from `now` would leak the present into a past state."""
    early, late = dec.tte_at(WEND, WS + 60.0), dec.tte_at(WEND, WS + 600.0)
    assert early > late
    assert early == 840.0 and late == 300.0


def test_the_clock_is_monotone_decreasing_across_a_window():
    ts = [dec.tte_at(WEND, WS + e) for e in range(0, 901, 30)]
    assert ts == sorted(ts, reverse=True)


# ------------------------------------------------------- the pair cap

def test_the_spring_cap_is_the_operating_model_target():
    assert dec.BTC15M_PAIR_CAP == D("0.96")
    assert dec.BTC15M_PAIR_CAP < dec.PAIR_OBSERVATION_CAP


def test_the_tighter_cap_tightens_the_completion_price():
    avg = D("0.40")
    px_sports, _ = dec.complement_target(avg, None)
    px_btc, _ = dec.complement_target(avg, None,
                                      pair_cap=dec.BTC15M_PAIR_CAP)
    assert px_btc < px_sports
    assert px_btc == D("0.56")          # 0.96 - 0.40
    assert px_sports == D("0.58")       # 0.98 - 0.40


def test_the_hard_bound_always_participates_however_loose_the_cap():
    """A cap can only ever tighten: it enters a min() the >=1c hard bound
    is also in, so a wider cap cannot admit a sub-1c pair."""
    avg = D("0.40")
    px, econ = dec.complement_target(avg, None, pair_cap=D("1.50"))
    hard = dec.hard_pair_bound() - avg
    assert px <= hard
    assert D(econ["max_pair_cost"]) <= dec.hard_pair_bound()


def test_the_served_deficit_bid_still_caps_the_join():
    avg = D("0.40")
    px, _ = dec.complement_target(avg, D("0.12"),
                                  pair_cap=dec.BTC15M_PAIR_CAP)
    assert px == D("0.12")


def test_complement_target_default_is_unchanged_for_sports():
    avg = D("0.30")
    a, _ = dec.complement_target(avg, None)
    b, _ = dec.complement_target(avg, None,
                                 pair_cap=dec.PAIR_OBSERVATION_CAP)
    assert a == b


# ------------------------------------- plan_action keeps its sports default

def _book(bid="0.50", ask="0.51"):
    return {"bid": D(bid), "ask": D(ask),
            "bid_yes": D(bid), "ask_yes": D(ask)}


def test_plan_action_default_cap_matches_the_sports_constant():
    inv = dec.Inventory()
    inv.record(True, D("0.40"))                  # one YES, residual +1
    plan = dec.plan_action("complete", inv, _book())
    assert plan["decision"] in ("quote", "no_action")
    if plan["decision"] == "quote":
        assert D(plan["max_pair_cost"]) <= dec.PAIR_OBSERVATION_CAP


def test_plan_action_with_the_btc_cap_is_never_looser():
    inv = dec.Inventory()
    inv.record(True, D("0.40"))
    a = dec.plan_action("complete", inv, _book())
    b = dec.plan_action("complete", inv, _book(),
                        pair_cap=dec.BTC15M_PAIR_CAP)
    if a["decision"] == "quote" and b["decision"] == "quote":
        assert D(b["max_pair_cost"]) <= D(a["max_pair_cost"])


# -------------------------------- the controller is still inventory-driven

def test_a_flat_book_makes_any_relation_an_open():
    """`plan_action` overrides the model's label from OUR residual."""
    inv = dec.Inventory()
    assert inv.residual == 0
    plan = dec.plan_action("extend", inv, _book())
    assert plan.get("relation") == "open"


def test_extend_takes_the_surplus_side_and_complete_the_deficit():
    inv = dec.Inventory()
    inv.record(True, D("0.40"))                  # residual +1 (YES surplus)
    assert dec.plan_action("extend", inv, _book())["side"] == "YES"
    c = dec.plan_action("complete", inv, _book())
    if c["decision"] == "quote":
        assert c["side"] == "NO"                 # the DEFICIT side


def test_accumulation_stops_at_the_residual_cap():
    inv = dec.Inventory()
    for _ in range(dec.MAX_RESIDUAL_UNITS):
        inv.record(True, D("0.40"))
    plan = dec.plan_action("extend", inv, _book())
    assert plan["decision"] == "no_action"
    assert "residual_cap" in plan["reason"]
