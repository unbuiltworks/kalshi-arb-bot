"""A COMPLETE may never imply a pair above 1.00.

OWNER 2026-09-10, superseding 5574762432's "reported, not gated", on the
evidence of asc-mlb-chc-mil-2026-09-09-pos-2pt5: the touch ran to 0.880
against a first leg averaging 0.3612, completing a pair at 1.2412. The
position went from +1.94/-5.97 to -1.25/-5.53 -- losing in every branch.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_spring_l5_session as ses


@pytest.fixture(autouse=True)
def _l5_touch(monkeypatch):
    monkeypatch.setattr(ses, "complement_pricing", lambda: "l5_touch")


TICK = D("0.005")


def _px(avg, touch, opp_ask="0.999"):
    return ses.spring_complement_price(D(avg), D(opp_ask), TICK, touch=D(touch))


def test_the_exact_live_order_is_now_refused_at_its_touch():
    """0.3612 + 0.880 = 1.2412. The order that cost us the winning branch."""
    px, info = _px("0.3612", "0.880")
    assert px is not None
    assert D("0.3612") + px <= D("1.00"), info
    assert info["capped_by_pair_ceiling"] is True
    assert px < D("0.880"), "must not follow the touch above the ceiling"


def test_it_still_follows_the_touch_while_the_pair_is_economic():
    """The behaviour the owner kept. Below the ceiling nothing changes."""
    px, info = _px("0.3612", "0.500")
    assert px == D("0.500")
    assert info["capped_by_pair_ceiling"] is False
    assert info["regime"] == "l5_touch"


def test_the_cap_price_is_the_best_the_ceiling_allows_not_a_refusal():
    """'Stop chasing' means hold our price, not stop posting: if the market
    comes back, the pair still completes at <= the ceiling."""
    px, _ = _px("0.40", "0.95")
    assert px == D("0.600")          # 1.00 - 0.40, floored to tick
    assert D("0.40") + px == D("1.00")


def test_a_first_leg_too_expensive_to_complete_economically_refuses(monkeypatch):
    px, info = _px("0.998", "0.90")
    assert px is None
    assert info["regime"] in ("outside_venue_price_range",
                             "above_completion_pair_ceiling")


def test_post_only_trim_still_applies_and_cannot_breach_the_ceiling():
    px, info = _px("0.30", "0.80", opp_ask="0.500")
    assert px < D("0.500"), "post-only: strictly below the opposite ask"
    assert D("0.30") + px <= D("1.00")


def test_ceiling_is_1_00_and_is_read_not_hardcoded_at_the_call_site():
    assert ses.completion_pair_ceiling() == D("1.00")
    import ast
    tree = ast.parse(open(ses.__file__).read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "spring_complement_price")
    src = ast.unparse(fn)
    assert "completion_pair_ceiling()" in src
    assert "Decimal('1.00')" not in src, "read the helper, do not restate it"


def test_the_gate_is_wired_not_merely_declared():
    """Mutation guard: a ceiling nothing consults is decoration. Removing the
    cap must change behaviour on the live case."""
    px_capped, _ = _px("0.3612", "0.880")
    px_free = (D("0.880") / TICK).to_integral_value() * TICK
    assert px_capped != px_free


@pytest.mark.parametrize("avg,touch", [("0.3612", "0.880"), ("0.50", "0.60"),
                                       ("0.20", "0.90"), ("0.45", "0.55")])
def test_no_input_can_produce_a_pair_above_the_ceiling(avg, touch):
    px, info = _px(avg, touch)
    if px is not None:
        assert D(avg) + px <= ses.completion_pair_ceiling(), info


def test_the_boot_row_proves_the_ceiling_like_it_proves_the_flag():
    """The rung prints pair_cost_max 0.98 from the LADDER, which is not the
    rule that binds. A preflight that reads only that row would confirm the
    wrong number -- the same gap that made complete_requote unprovable."""
    from src import pmus_l5_continuous_session as cont
    src = open(cont.__file__).read()
    i = src.index('_log("campaign_start"')
    row = src[i:src.index("rung=report", i)]
    assert "completion_pair_ceiling=str(base.completion_pair_ceiling())" in row
    assert "combined_loss_budget_usd=str(base.daily_loss_stop())" in row
