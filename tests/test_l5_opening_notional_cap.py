"""The worst-case opening-notional cap: the replay's one approved change.

WHAT IT IS FOR. 0130z opened DOWN 1.87 at own 0.62 -- the dearer leg of a
compliant 0.95 plan -- its complement never filled, and it settled worthless
for -$1.1594, which is 90.2% of everything the seven completed pairs earned.
The cap bounds `qty * dear`, so the worst single-leg loss becomes the budget
by construction rather than by luck.

WHAT IT MUST NOT DO, and each is a test here: throttle a risk-reducing
complement, round a quantity UP, exceed the budget after rounding, become no
bound when it cannot be parsed, be raisable by a typo, or size to zero
silently instead of refusing by name.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

SLUG = "cpc-btc-updown-15m-2026-10-25-0130z"


@pytest.fixture(autouse=True)
def _armed(monkeypatch):
    """The probe is inert without BOTH an experiment and a market."""
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "test-notional-cap")
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-btc-updown-15m-*")
    monkeypatch.delenv(TS.OPENING_NOTIONAL_ENV, raising=False)
    monkeypatch.delenv("L5_TS_ROUND_ALLOCATIONS", raising=False)
    monkeypatch.delenv(TS.OPENING_LEG_MAX_ENV, raising=False)


def build(**kw):
    base = dict(market=SLUG, bid="0.49", ask="0.51",
                bid_depth="5000", ask_depth="5000",
                tick="0.01", min_qty="0.01", depth_ticks=2,
                cash_envelope="100", max_worst_case_loss="100",
                book_age_s=0.05)
    base.update(kw)
    return TS.build_orders(**base)


def dearer(plan) -> D:
    """The dearer leg's OWN price -- `price` in a leg is the own price for
    both sides, UP at bid-minus-depth and DOWN at 1 - (ask-plus-depth)."""
    return max(D(l["price"]) for l in plan["legs"])


# ------------------------------------------------------------- the knob

def test_absent_is_inert_so_the_deployed_default_is_unchanged(monkeypatch):
    assert TS.opening_notional_budget_usd() is None
    plan = build()
    assert plan["ok"] is True
    assert plan["opening_notional_budget_usd"] is None
    assert plan["opening_notional_contracts"] is None
    assert plan["opening_notional_binds"] is False


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_is_also_inert(monkeypatch, raw):
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, raw)
    assert TS.opening_notional_budget_usd() is None


@pytest.mark.parametrize("raw", ["abc", "-1", "nan", "inf", "0.8.0", "1e"])
def test_unreadable_reads_as_zero_and_refuses_rather_than_guessing(
        monkeypatch, raw):
    """A bound we cannot parse must not silently become no bound."""
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, raw)
    assert TS.opening_notional_budget_usd() == D(0)
    plan = build()
    assert plan["ok"] is False
    assert plan["reason"] == "OPENING_NOTIONAL_CAP_BELOW_VENUE_MINIMUM"


def test_a_typo_cannot_raise_it_above_the_approved_exposure(monkeypatch):
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "9999")
    assert TS.opening_notional_budget_usd() == TS.OPENING_NOTIONAL_MAX_USD
    assert TS.OPENING_NOTIONAL_MAX_USD == D("5")


# ------------------------------------------------------------- it sizes

def test_the_cap_binds_and_reduces_the_clip(monkeypatch):
    uncapped = build()
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    capped = build()
    assert capped["ok"] is True
    assert D(capped["qty_per_leg"]) < D(uncapped["qty_per_leg"])
    assert capped["opening_notional_binds"] is True
    assert capped["sized_by"] == "OPENING_NOTIONAL_CAP"


@pytest.mark.parametrize("budget", ["0.40", "0.50", "0.60", "0.70", "0.80",
                                    "0.90", "1.00", "2.00"])
def test_worst_case_opening_notional_never_exceeds_the_budget(
        monkeypatch, budget):
    """THE WHOLE POINT, asserted on the emitted plan rather than inferred."""
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, budget)
    plan = build()
    assert plan["ok"] is True, plan.get("reason")
    assert D(plan["worst_case_opening_notional_usd"]) <= D(budget)
    assert D(plan["qty_per_leg"]) * dearer(plan) <= D(budget)


@pytest.mark.parametrize("bid,ask", [("0.49", "0.51"), ("0.30", "0.32"),
                                     ("0.60", "0.62"), ("0.25", "0.27"),
                                     ("0.70", "0.72")])
def test_it_holds_at_every_price_the_book_can_offer(monkeypatch, bid, ask):
    """Price-independence is the reason it was chosen over a price cap."""
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    plan = build(bid=bid, ask=ask)
    if not plan["ok"]:
        return
    assert D(plan["qty_per_leg"]) * dearer(plan) <= D("0.80")


# ----------------------------------------------------------- it rounds

@pytest.mark.parametrize("mq", ["0.01", "0.05", "0.1", "0.25", "1"])
def test_the_clip_stays_on_the_grid_and_rounds_only_down(monkeypatch, mq):
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    plan = build(min_qty=mq)
    if not plan["ok"]:
        return
    q, step = D(plan["qty_per_leg"]), D(mq)
    assert q % step == 0, (q, step)
    assert q * dearer(plan) <= D("0.80")


def test_rounding_down_can_only_lower_the_exposure(monkeypatch):
    """A coarse grid must never round the clip UP through the budget."""
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    fine = build(min_qty="0.01")
    coarse = build(min_qty="0.25")
    assert fine["ok"] and coarse["ok"]
    assert D(coarse["qty_per_leg"]) <= D(fine["qty_per_leg"])


# -------------------------------------- the cap vs the venue minimum

def test_a_cap_that_cannot_fund_one_grid_step_refuses_by_name(monkeypatch):
    """REJECT, not size-to-zero. One grid step at own 0.47 costs 0.47, so a
    $0.10 budget cannot open at this price and must say exactly that."""
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.10")
    plan = build(min_qty="1")
    assert plan["ok"] is False
    assert plan["reason"] == "OPENING_NOTIONAL_CAP_BELOW_VENUE_MINIMUM"
    assert D(plan["min_notional_usd"]) > D(plan["opening_notional_budget_usd"])
    assert plan["min_qty"] == "1"


def test_the_refusal_names_both_numbers_the_operator_needs(monkeypatch):
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.20")
    plan = build(min_qty="1")
    assert plan["ok"] is False
    for k in ("opening_notional_budget_usd", "min_qty", "dearer_leg_own_px",
              "min_notional_usd"):
        assert plan.get(k) is not None, k


def test_exactly_one_grid_step_is_allowed_when_it_just_fits(monkeypatch):
    """The boundary must be inclusive, or a budget set to the exact cost of
    one step would refuse a leg it is meant to permit."""
    plan0 = build(min_qty="1")
    d = dearer(plan0) if plan0["ok"] else D("0.47")
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, str(d))
    plan = build(min_qty="1")
    assert plan["ok"] is True, plan.get("reason")
    assert D(plan["qty_per_leg"]) == D("1")


# ------------------------------- partial fills and the complement path

def test_a_partial_fill_is_strictly_less_exposure_than_the_bound(monkeypatch):
    """The cap bounds the WORST case -- the dearer leg filling alone, in
    full. Any partial fill is inside it, which is what makes the bound a
    bound rather than an estimate."""
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    plan = build()
    q, d = D(plan["qty_per_leg"]), dearer(plan)
    for frac in (D("0.01"), D("0.25"), D("0.5"), D("0.99"), D(1)):
        assert (q * frac) * d <= D("0.80")


def test_the_cap_never_throttles_a_risk_reducing_complement(monkeypatch):
    """Throttling a complement is what stranded 0130z. The completion paths
    must not read this knob at all -- asserted on the source, because the
    guarantee is structural and not incidental."""
    import inspect
    for fn in (TS.completion_decision, TS.complement_order):
        src = inspect.getsource(fn)
        assert TS.OPENING_NOTIONAL_ENV not in src, fn.__name__
        assert "opening_notional" not in src, fn.__name__


def test_completion_is_unchanged_with_the_cap_set(monkeypatch):
    """Same inputs, same completion decision, cap on or off."""
    kw = dict(up_qty="0", dn_qty="1.87", up_cost_px="0", dn_cost_px="0.62",
              taker_price="0.34", available_depth="1277.39",
              tick="0.01", min_qty="0.01",
              cash_headroom="100", worst_case_ceiling="100",
              book_age_s=0.05)
    off = TS.completion_decision(**kw)
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    on = TS.completion_decision(**kw)
    assert on == off, "the cap must not reach the completion path"
    # and the 0130z shape specifically: completing 1.87 DOWN held at own 0.62
    # is a $0.6358 outlay, far above the $0.80 cap's per-leg clip, and must
    # still be permitted because it REDUCES residual
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.10")
    assert TS.completion_decision(**kw) == off


# ------------------------------------- aggregate opening exposure

def test_aggregate_opening_exposure_stays_inside_the_approved_bounds(
        monkeypatch):
    """ROUND_ALLOCATIONS lets a window open more than one leg. The product of
    the budget and the allocation count is the aggregate worst case, and it
    must stay inside the $2 worst-case deficit and the $5 instantaneous
    exposure that are already approved. If a future budget breaks that, this
    fails rather than the live gate discovering it."""
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", "2")
    budget = TS.opening_notional_budget_usd()
    allocs = TS.round_allocations()
    assert budget * allocs <= D("2"), "breaches the $2 worst-case deficit"
    assert budget * allocs <= D("5"), "breaches the $5 exposure ceiling"


def test_each_round_is_individually_capped_too(monkeypatch):
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    monkeypatch.setenv("L5_TS_ROUND_ALLOCATIONS", "2")
    plan = build()
    assert plan["ok"] is True
    assert D(plan["worst_case_opening_notional_usd"]) <= D("0.80")


# --------------------------------------------- it changes nothing else

def test_no_approved_number_moves(monkeypatch):
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    monkeypatch.delenv("L5_TS_MAX_PAIR_COST", raising=False)
    assert TS.max_pair_cost() == D("0.96")
    assert TS.entry_cutoff_s() == 600.0
    assert TS.order_ttl_s() == 120.0
    assert TS.min_first_leg_own_price() == D("0.20")
    assert TS.opening_leg_max_contracts() <= D("3")


def test_the_pair_is_still_two_maker_legs_under_the_cap(monkeypatch):
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    plan = build()
    assert plan["ok"] is True
    assert [l["side"] for l in plan["legs"]] == ["UP", "DOWN"]
    assert D(plan["pair_cost"]) <= D("0.96")
    for leg in plan["legs"]:
        assert leg.get("post_only", True) is not False


def test_the_cap_is_reported_so_an_operator_can_see_it_bind(monkeypatch):
    monkeypatch.setenv(TS.OPENING_NOTIONAL_ENV, "0.80")
    plan = build()
    assert plan["opening_notional_budget_usd"] == "0.80"
    assert plan["opening_notional_contracts"] is not None
    assert "dearer leg" in plan["opening_notional_basis"]
