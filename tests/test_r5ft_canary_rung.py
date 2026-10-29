"""R5F-T: R5F with its own approval honoured, sized as a canary.

OWNER 2026-09-11: "go 3" -- go live now with a q=1 canary at a relaxed target
-- then, asked which target: "Join the touch, hard cap <=1.00."

WHAT THIS RUNG IS, AND IS NOT. It is not a new strategy. The R5F approval row
of 2026-09-07 reads "L5-touch COMPLETE pricing; above-par pair cost is
reported/accounted, NOT behaviorally gated". R5F shipped `pair_cost_max: None`,
which falls through to DEFAULT_PAIR_COST_MAX 0.98, so the complement price has
been gated about 2c inside the touch on 93% of live preflights since. This rung
states the ceiling explicitly at par instead of inheriting a default.

Par is still absolute. pair_cost_max() floors EVERY rung at 1.00, so no rung,
including this one, can buy a pair that settlement cannot return.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import pmus_l5_ramp_ladder as L


def _r():
    return L.rung("R5FT")


def test_the_ceiling_is_stated_at_par_not_inherited():
    """The whole change. R5F inherits 0.98 from the default; R5FT says 1.00."""
    assert L.pair_cost_max(_r()) == D("1.00")
    assert L.pair_cost_max(L.rung("R5F")) == D("0.98")
    assert _r()["pair_cost_max"] == "1.00", "stated, not None"


def test_par_remains_an_absolute_floor_over_every_rung():
    """A rung asking for more than par still gets par. This is the guardrail
    that makes relaxing the target safe to try at all: we can move toward the
    touch, and we can never pay more than a pair can return."""
    assert L.pair_cost_max({"pair_cost_max": "1.25"}) == D("1.00")
    assert L.pair_cost_max({"pair_cost_max": "1.01"}) == D("1.00")


def test_only_the_price_lever_moves_BEHAVIORALLY():
    """Every R5F lever that changes what the engine DOES is copied
    unchanged, so a fill difference is attributable to price and to
    nothing else. The owner separately raised two SIZE limits (cap and
    daily stop) on 2026-09-11; those change how much capital the same
    behaviour may commit, not the behaviour, and are asserted in their
    own tests below."""
    a, b = L.rung("R5F"), _r()
    for k in ("complement_pricing", "rest_policy", "rest_floor_s",
              "concurrency", "max_unmatched_usd", "residual_units"):
        assert a[k] == b[k], k
    assert b["lever"] == "price"


def test_it_is_still_one_market_with_small_actions():
    """The owner raised the CAPITAL cap, not the canary shape. One market,
    no repeat, and an action SMALLER than R5F's -- $30 is reached by many
    $1.00 maker actions, which is CLAUDE.md's 'frequency before size'."""
    b = _r()
    assert b["markets_per_campaign"] == 1, "one market, then stop"
    assert b["campaigns_per_day"] == 1, "no automatic repeat"
    assert b["concurrency"] == 1
    assert D(b["target_notional_usd"]) < D(L.rung("R5F")["target_notional_usd"])
    assert D(b["max_leg_notional_usd"]) < D(L.rung("R5F")["max_leg_notional_usd"])
    assert b["max_actions_per_session"] < L.rung("R5F")["max_actions_per_session"]


def test_the_owner_raised_the_cap_and_the_stop_and_reversed_himself():
    """He said on 2026-09-11 "Keep the daily stop at -$15. It did exactly
    what we wanted." Later the same day, told the day had spent all but
    ~$5.63 of it, he said "lets raise it to $30 or something" and, asked
    which number, answered BOTH.

    Recorded here rather than silently overwritten: a reader who finds
    the -$15 quote elsewhere in this repo needs to see that it was
    superseded by name, by the same person, and when."""
    b = _r()
    assert b["daily_loss_stop_usd"] == "-30.00"
    assert D(b["per_market_cap_usd"]) == D("30.00")
    # the worst DAY is the stop plus one in-flight market
    w = L.worst_case(b)
    assert D(w["daily_loss_stop_usd"]) == D("-30.00")
    assert D(w["per_market_usd"]) == D("30.00")
    assert D(w["daily_loss_stop_usd"]).copy_abs() + D(w["per_market_usd"]) \
        == D("60.00")


def test_the_raise_is_the_canarys_alone_and_r5f_keeps_minus_fifteen():
    """R5F is off. If it is ever run again it must not inherit $30 or
    -$30 by proximity."""
    a = L.rung("R5F")
    assert a["daily_loss_stop_usd"] == "-15.00"
    assert D(a["per_market_cap_usd"]) == D("7.50")


def test_the_rung_records_why_it_exists_against_measured_evidence():
    src = L.rung("R5FT")["derived_from"]
    assert "2026-09-07 approval" in src
    assert "instead of inheriting 0.98" in src


def test_rung_ids_stay_unique_so_activation_cannot_be_ambiguous():
    ids = [r["id"] for r in L.ALL_RUNGS]
    assert len(ids) == len(set(ids))
    assert "R5FT" in ids


def test_an_unknown_rung_still_falls_back_to_the_authorized_one():
    """Adding a rung must not change what an unrecognised id resolves to."""
    assert L.rung("NOPE")["id"] == L.RUNGS[0]["id"]
