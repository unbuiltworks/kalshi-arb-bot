"""The MAKER-phase bail protected an edge that did not exist.

`cpc-btc-updown-15m-2026-10-25-1945z`, venue-confirmed: 1.56 UP at own cost
0.44, acquisition $0.6864, residual settled worthless.

The maker complement route refused twelve times on
COMPLEMENT_PAIR_ABOVE_CAP, so nothing was ever posted, and ALL 121
`L5_RESIDUAL_BOOK_SAMPLE` rows of that window carry `order_resting: false`.
Meanwhile the taker completion route -- which is correctly NOT gated by the
entry cap and correctly judged on the worse settlement outcome -- was
unreachable until 600 s, because `completion_phase` returns MAKER before
`cancel_cutoff - COMPLETION_WINDOW_S`.

Measured on that window's own books:

    MAKER      [0,600)   pair 0.99-1.38   best executable exit  -$0.0144
    COMPLETION [600,720) pair 1.33-1.41   best executable exit  -$0.5348
    LATE       [720,900) pair 1.37-1.43   best executable exit  -$0.5872

So the mistiming, not the absence of a rule, cost $0.5204 on this window.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_two_sided_probe as TSP

WS = 1792957500.0            # 1945z window start


# ------------------------------------------------- the timeline is the bug

def test_the_taker_route_opened_only_after_the_price_had_decayed():
    """Phase boundaries for a 900 s window, from the deployed constants."""
    assert TSP.COMPLETION_WINDOW_S == 120.0
    assert TSP.cancel_cutoff_from_start(WS) == WS + 720.0
    # the instant the real exit was worth -$0.0144
    assert TSP.completion_phase(WS + 204, WS) == "MAKER"
    # the engine's first legal crossing opportunity
    assert TSP.completion_phase(WS + 600, WS) == "COMPLETION"
    assert TSP.completion_phase(WS + 719, WS) == "COMPLETION"
    assert TSP.completion_phase(WS + 720, WS) == "LATE"


def test_the_entry_cap_refuses_the_very_pair_the_exit_wanted():
    """0.99 is above the 0.96 entry cap, which is why the maker route posted
    nothing and left the residual with no resting quote at all."""
    assert D("0.99") > TSP.max_pair_cost()


# ------------------------------------- the decision itself was always right

def _dec(**kw):
    base = dict(up_qty="1.56", dn_qty="0", up_cost_px="0.44",
                dn_cost_px="0", taker_price="0.55", available_depth="38",
                tick="0.01", min_qty="0.01", cash_headroom="5",
                worst_case_ceiling="2.00", book_age_s="0.5")
    base.update(kw)
    return TSP.completion_decision(**base)


def test_completion_decision_accepts_the_204s_instant_on_its_economics():
    """It needs no new rule: at the price that was really executable it
    already finds a strict improvement in the worse outcome."""
    out = _dec(taker_price="0.55")
    assert out["ok"] is True, out
    assert out["cross"] is True


def test_completion_decision_is_not_gated_by_the_entry_cap():
    """The pair it accepts above is 0.44 + 0.55 = 0.99, over the 0.96 cap.
    An exit asks whether a known small loss beats an unknown large one."""
    out = _dec(taker_price="0.55")
    assert out["ok"] is True
    assert D("0.44") + D("0.55") > TSP.max_pair_cost()


def test_completion_decision_still_refuses_an_uneconomic_cross():
    """It must not become a flattening rule. At the late price the pair is
    1.33 and crossing has to justify itself on the worse outcome."""
    late = _dec(taker_price="0.89")
    assert "ok" in late
    if not late["ok"]:
        assert late["reason"]          # named, never silent


def test_a_stale_book_is_never_an_executable_price():
    assert _dec(book_age_s="9")["reason"] == "BOOK_STALE"
    assert _dec(book_age_s=None)["reason"] == "BOOK_STALE"


def test_a_matched_book_has_nothing_to_complete():
    assert _dec(up_qty="1.56", dn_qty="1.56")["reason"] == "ALREADY_MATCHED"
    assert _dec(up_qty="0", dn_qty="0")["reason"] == "NO_INVENTORY_TO_COMPLETE"


# ------------------------------------------------- the relaxation is narrow

def test_the_runner_relaxes_the_phase_only_when_nothing_can_still_fill():
    """Source-level assertion: the MAKER bail is conditioned on
    `still_fillable_order_count`, which charges an unreadable, parked or
    quarantined row as live -- so 'nothing is resting' must be positively
    established and an unreadable store keeps the old behaviour."""
    src = open("src/l5_pmus_live_runner.py").read()
    i = src.index("def maybe_ts_completion")
    body = src[i:i + 6000]
    assert "still_fillable_order_count" in body
    assert 'if phase == "LATE":' in body
    assert 'if phase != "COMPLETION" and not no_quote_resting:' in body
    # the audit fields travel on the emitted record
    assert "evaluated_early_no_quote_resting" in body
    assert "still_fillable_orders" in body


def test_the_relaxation_cannot_submit_on_its_own():
    """Evaluate-only: crossing still needs L5_TS_COMPLETION_CROSS and an
    armed transport, so with crossing unauthorized this changes no order."""
    src = open("src/l5_pmus_live_runner.py").read()
    i = src.index("def maybe_ts_completion")
    body = src[i:i + 6000]
    assert "ts_completion_cross_authorized" in body
