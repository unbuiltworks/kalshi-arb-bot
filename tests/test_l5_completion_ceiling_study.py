"""OWNER #167 5574408755 item 2: completion ceiling from L5's own COMPLETEs.
The three pair-cost bases are pinned on a tiny synthetic window (so a wrong
basis cannot hide behind the size of the real file), the relation/state keys
are asserted to be the live model's, and the headline numbers on the
canonical history are pinned exactly (deterministic input, deterministic
output)."""
from __future__ import annotations

import os

import pytest

from src import l5_completion_ceiling_study as cc
from src.polymarket_l5.action_model import _relation, state_key


def _fills():
    # open Up 10@0.40, extend Up 10@0.42, complete Down 10@0.62, complete Down 15@0.55
    return [(1000, "Up", "BUY", 0.40, 10, 800), (1010, "Up", "BUY", 0.42, 10, 790),
            (1040, "Down", "BUY", 0.62, 10, 760), (1060, "Down", "BUY", 0.55, 15, 740)]


def test_three_bases_on_synthetic_window():
    rows = cc.synthetic_rows(_fills(), settlement="Up")
    comps, win = cc.window_walk(rows)
    assert [_relation(0, "Up", "BUY")] == ["open"]
    assert [c["outcome"] for c in comps] == ["Down", "Down"]
    first, second = comps
    # engine = 0.62 + avg(0.40, 0.42) = 1.03 ; marginal LIFO = 0.62 + 0.42 = 1.04
    assert first["engine_avg"] == 1.03 and first["marginal_lifo"] == 1.04
    assert first["preceding"] == "after_extend_x1" and first["overshoot_qty"] == 0.0
    assert first["inv_usd_before"] == pytest.approx(8.2) and first["imb_bucket"] == "imb10-50"
    # second: 0.55 + avg 0.41 = 0.96 ; LIFO consumes the remaining 0.40 lot -> 0.95; 5 shares overshoot
    assert second["engine_avg"] == 0.96 and second["marginal_lifo"] == 0.95
    assert second["completed_qty"] == 10 and second["overshoot_qty"] == 5 and second["preceding"] == "after_complete"
    # window realized = wavg_up 0.41 + wavg_down (6.2 + 8.25)/25 = 0.578 -> 0.988, assigned to both completes
    assert first["window_realized"] == second["window_realized"] == 0.988
    assert win["final_pair_cost"] == 0.988 and win["final_matched_qty"] == 20 and win["final_residual_qty"] == -5
    assert win["held_residual"] and win["any_complete"] and win["n_completes"] == 2


def test_state_key_is_the_live_models_key():
    rows = cc.synthetic_rows(_fills())
    comps, _ = cc.window_walk(rows)
    # band of the LAST OBSERVED fill price (0.42), tte at the complete (760s), |residual| before (20 shares)
    assert comps[0]["state"] == state_key(0.42, 760, 20) == "p25-45c|tte>10m|imb10-50"
    assert comps[0]["own_band"] == "p55-75c" and comps[0]["tte_bucket"] == "tte>10m"


def test_threshold_shares_count_pairs_and_dollars_and_exact_one_is_not_above():
    # window A: open Up 10@0.41, complete Down 10@0.59 -> pair exactly 1.00 (float noise must not push it above)
    # window B: open Up 10@0.30, complete Down 10@0.75 -> pair 1.05
    a = cc.synthetic_rows([(1000, "Up", "BUY", 0.41, 10, 800), (1010, "Down", "BUY", 0.59, 10, 790)], "A")
    b = cc.synthetic_rows([(2000, "Up", "BUY", 0.30, 10, 800), (2010, "Down", "BUY", 0.75, 10, 790)], "B")
    out = cc.study(a + b)
    d = out["overall"]["engine_avg"]
    assert d["n"] == 2 and d["p50"] == 1.0 and d["max"] == 1.05
    assert d["above"]["1.00"]["fills_pct"] == 50.0 and d["above"]["1.00"]["pairs_pct"] == 50.0
    # dollars: 5.90 at 1.00 vs 7.50 at 1.05 -> 55.97% of completed dollars above 1.00
    assert d["above"]["1.00"]["usd_pct"] == pytest.approx(100 * 7.5 / 13.4, abs=0.01)
    assert d["above"]["1.03"]["fills_pct"] == 50.0 and d["above"]["1.05"]["fills_pct"] == 0.0
    assert out["counts"]["relations_live_rule"] == {"open": 2, "complete": 2}
    assert "labels" in out and "economic_overlay_attachment_points" in out


def test_completion_vs_hold_and_matched_pairs_by_month():
    done = cc.synthetic_rows(_fills(), "W1", settlement="Up")                         # completes, holds 5 Down
    one_sided = cc.synthetic_rows([(1000, "Up", "BUY", 0.40, 10, 800),
                                   (1010, "Up", "BUY", 0.45, 10, 790)], "W2")         # never completes
    flat = cc.synthetic_rows([(1000, "Up", "BUY", 0.50, 10, 800),
                              (1010, "Down", "BUY", 0.48, 10, 790)], "W3")             # flat at settlement
    out = cc.study(done + one_sided + flat)
    cvh = out["completion_vs_hold"]["ALL"]
    assert cvh["windows"] == 3 and cvh["any_complete_pct"] == pytest.approx(66.67, abs=0.01)
    assert cvh["one_sided_pct"] == pytest.approx(33.33, abs=0.01)
    assert cvh["held_residual_ge_1share_pct"] == pytest.approx(66.67, abs=0.01)
    assert cvh["residual_usd_at_settlement_total"] == pytest.approx(2.89 + 8.5, abs=0.01)
    m = out["matched_pairs_by_month"]["ALL"]
    # window basis: (20 x 0.988 + 10 x 0.98) / 30 ; lot basis: (10x1.02 + 10x0.97 + 10x0.98) / 30
    assert m["matched_pairs"] == 30.0
    assert m["usd_wavg_pair_cost_window_basis"] == pytest.approx((20 * 0.988 + 10 * 0.98) / 30, abs=1e-4)
    assert m["usd_wavg_pair_cost_lot_lifo_basis"] == pytest.approx((10.2 + 9.7 + 9.8) / 30, abs=1e-4)
    assert cc.render_md(out).startswith("# L5 completion ceiling")


@pytest.mark.skipif(not os.path.exists(cc.CANONICAL_PATH), reason="canonical history not present")
def test_canonical_history_headline_numbers():
    out = cc.study()
    c = out["counts"]
    assert c["windows"] == 2216 and c["fills"] == 71984 and c["completes"] == 33765
    assert c["relations_live_rule"] == {"open": 2864, "extend": 35353, "complete": 33765, "sell": 2}
    e, m, w = (out["overall"][b] for b in cc.BASES)
    assert (e["p50"], e["p90"]) == (0.9697, 1.2204) and e["above"]["1.00"]["fills_pct"] == 42.7
    assert (m["p50"], m["p90"]) == (0.9823, 1.08) and m["above"]["1.00"]["fills_pct"] == 28.68
    assert (w["p50"], w["p90"]) == (0.9659, 1.0168) and w["above"]["1.00"]["fills_pct"] == 17.35
    # the > 1.00 mass is real on every basis and grows with the basis' memory of the surplus side
    assert w["above"]["1.00"]["usd_pct"] < m["above"]["1.00"]["usd_pct"] < e["above"]["1.00"]["usd_pct"]
    assert set(out["by_imbalance_bucket"]) == {"imb1-10", "imb10-50", "imb>50"}
    assert "|" in next(iter(out["by_state"]))       # keyed exactly like the live model
