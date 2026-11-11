"""The maturity criterion, which was fixed before the sample existed.

These pin the SHAPE of the rule, not a verdict: that rows 20 s apart are not
independent draws, that a partial block is not an equal draw, that the V1
baseline must reach its own steady state before any reduction is reportable,
and that nothing here can be quietly relaxed to declare victory.
"""
from __future__ import annotations

import pytest

from analysis.l5.v1_1_shadow import maturity as M


def cycles(n, *, rate=0.65, v1=0.90, rows=55, step=20.0, t0=1000.0, jitter=0):
    out = []
    for i in range(n):
        p = int(rows * rate) + (jitter if i % 2 else -jitter)
        out.append({"kind": "cycle", "t": t0 + i * step, "rows": rows,
                    "proposed": p, "v1_would_have": int(rows * v1),
                    "distinct_markets": 120})
    return out


# --------------------------------------------------------------------------
# the constants ARE the criterion

def test_the_preregistered_constants_are_what_the_document_says():
    assert (M.PRECISION_PP, M.MATERIAL_PP) == (2.0, 5.0)
    assert M.PRECISION_PP < M.MATERIAL_PP, "precision must sit inside material"
    assert (M.MIN_OBS, M.MIN_MARKETS, M.MIN_HOURS, M.MIN_BLOCKS) == \
        (25_000, 100, 3.0, 12)
    assert M.BLOCK_S == 900 and M.GUARD_OBS == 100_000
    doc = open("analysis/l5/v1_1_shadow/MATURITY.md", encoding="utf-8").read()
    for token in ("2.0 pp", "5.0 pp", "25,000", "100,000", "15-minute"):
        assert token in doc, token


# --------------------------------------------------------------------------
# the autocorrelation the rows hide

def test_the_interval_widens_with_between_block_dispersion():
    """THE WHOLE POINT. The same market re-observed every 20 s is not a new
    draw. Under realistic regime-to-regime dispersion the batch-means interval
    must exceed the binomial one, which is the understatement we are avoiding.

    Stated precisely rather than as a universal: batch means is not
    unconditionally wider. If blocks agreed MORE than independent sampling
    predicts it would be narrower, and that would be a real finding about the
    data rather than a bug. What matters is that genuine dispersion is not
    thrown away."""
    import math
    steady = M.blocks_of(cycles(600), "proposed")
    # regimes that actually differ, the way a busy hour differs from a quiet one
    swingy = [(int(d * (0.55 if i % 2 else 0.75)), d)
              for i, (_, d) in enumerate(steady)]
    a = M.batch_means_ci(steady)["half_width_pp"]
    b = M.batch_means_ci(swingy)["half_width_pp"]
    assert b > a, (a, b)
    n = sum(d for _, d in swingy)
    p = sum(x for x, _ in swingy) / n
    binom_pp = 100 * 1.96 * math.sqrt(p * (1 - p) / n)
    assert b > binom_pp, (b, binom_pp)
    assert b > 5 * binom_pp, "dispersion this large should dominate the binomial"


def test_a_partial_trailing_block_is_dropped_not_counted_as_equal():
    """A block covering two minutes is not an equal draw beside one covering
    fifteen; letting it in understates the spread exactly when the sample is
    youngest."""
    full = M.blocks_of(cycles(135), "proposed")           # 45 cycles/block
    plus_stub = M.blocks_of(cycles(138), "proposed")
    assert len(plus_stub) == len(full)


def test_a_window_shorter_than_one_block_yields_no_block_at_all():
    """Ten cycles is three minutes. That is not a fifteen-minute block, and it
    must not be treated as one."""
    assert M.blocks_of(cycles(10), "proposed") == []
    r = M.batch_means_ci([])
    assert r["half_width_pp"] is None and r["rate"] is None


def test_a_single_complete_block_reports_no_interval_rather_than_a_fake_one():
    """One block has no spread to measure. A number here would be invented."""
    r = M.batch_means_ci(M.blocks_of(cycles(50), "proposed"))
    assert r["blocks"] == 1
    assert r["half_width_pp"] is None and "spread" in r["reason"]
    assert r["rate"] is not None          # the point estimate is still real


# --------------------------------------------------------------------------
# the baseline artifact

def test_a_v1_baseline_still_pinned_at_one_fails_m2():
    """Every V1 WAIT is a risk cap. While the book is near flat V1 sits at
    1.00, and the reduction measured against it is overstated."""
    a = M.assess(cycles(600, v1=1.0), distinct_markets=120)
    assert a["tests"]["M2_baseline_steady"]["pass"] is False
    assert "M2_baseline_steady" in a["failing"]
    assert a["mature"] is False


def test_a_departed_and_stable_baseline_passes_m2():
    a = M.assess(cycles(600, v1=0.80), distinct_markets=120)
    assert a["tests"]["M2_baseline_steady"]["pass"] is True


# --------------------------------------------------------------------------
# the other gates

def test_coverage_floors_block_precision_on_a_degenerate_sample():
    a = M.assess(cycles(40), distinct_markets=3)
    m1 = a["tests"]["M1_coverage"]
    assert m1["pass"] is False and m1["distinct_markets"] == 3
    assert a["mature"] is False


def test_doubling_measures_the_most_recent_halving_of_the_sample():
    drift = cycles(300, rate=0.50) + cycles(300, rate=0.80, t0=1000 + 300 * 20)
    d = M.doubling(drift, "proposed")
    assert d["delta_pp"] > M.DOUBLING_PP
    assert M.doubling(cycles(600), "proposed")["delta_pp"] <= M.DOUBLING_PP


def test_trend_catches_drift_that_precision_and_doubling_can_both_pass():
    rows = []
    for w in range(6):                      # a slow ramp, window by window
        rows += cycles(90, rate=0.50 + 0.04 * w, t0=1000 + w * 90 * 20)
    t = M.trend(rows, "proposed")
    assert t["max_abs_dev_pp"] > M.TREND_PP


def test_a_restart_or_a_missing_stage_one_fails_validity_outright():
    for kw in ({"restarts": 1}, {"act_artifact_missing": 1}):
        a = M.assess(cycles(600), distinct_markets=120, **kw)
        assert a["tests"]["M0_validity"]["pass"] is False
        assert a["mature"] is False


def test_the_guard_reports_non_convergence_instead_of_running_forever():
    noisy = cycles(2200, rows=55, jitter=20)
    a = M.assess(noisy, distinct_markets=120)
    assert a["tests"]["M1_coverage"]["observations"] >= M.GUARD_OBS
    if not a["tests"]["M3_precision"]["pass"]:
        assert a["guard_tripped"] is True


def test_maturity_requires_every_gate_and_reports_which_failed():
    a = M.assess(cycles(30), distinct_markets=2)
    assert a["mature"] is False and len(a["failing"]) >= 2
    assert a["criterion"].endswith("preregistered 2026-09-16")
    assert "never what the controller does" in a["labels"]


def test_a_clean_large_stable_sample_can_actually_pass():
    """The criterion must be satisfiable, or it is not a criterion."""
    a = M.assess(cycles(600, rate=0.65, v1=0.80), distinct_markets=150)
    assert a["tests"]["M3_precision"]["pass"] is True
    assert a["tests"]["M4_doubling"]["pass"] is True
    assert a["tests"]["M5_no_trend"]["pass"] is True
