"""V1 risk scale: the multiplier, and the claim that it keeps L5's shape.

The headline claim these tests exist to keep honest is counter-intuitive and
was NOT what I expected before measuring: R1's sizing distortion was not
caused by trading small. It was caused by not multiplying at all. A constant
venue-minimum size destroys relative sizing at ANY scale; a genuine multiplier
preserves it far below the scale R1 ran at.
"""
from __future__ import annotations

import pytest

from analysis.l5.live_v1 import risk_scale as R


@pytest.fixture(scope="module")
def windows():
    return R.load_windows()


@pytest.fixture(scope="module")
def envelope(windows):
    return R.corpus_envelope(windows)


@pytest.fixture(scope="module")
def usable(windows):
    return [t for t in windows.values() if len(t) >= 6]


# --------------------------------------------------------------------------
# quantization

def test_quantization_floors_at_the_venue_minimum_and_never_zero():
    assert R.quantize(0.0) == 0.0
    assert R.quantize(1e-9) == R.VENUE_MIN_QTY    # cannot place below it
    assert R.quantize(0.024) == 0.02
    assert R.quantize(0.026) == 0.03
    assert R.quantize(5.0, max_leg=0.05) == 0.05  # per-leg clamp binds


# --------------------------------------------------------------------------
# THE finding

def test_a_constant_size_destroys_relative_sizing_at_every_scale(usable):
    """R1's actual rule: 'one L5 size unit = one venue-minimum contract'.
    Constant sizes have no rank, so the correlation is not merely low --
    it is UNDEFINED, on every window."""
    defined = 0
    for t in usable:
        if R._spearman([r["qty"] for r in t], [0.02] * len(t)) is not None:
            defined += 1
    assert defined == 0, "a constant series cannot carry relative sizing"


def test_a_true_multiplier_preserves_sizing_at_the_same_effective_scale(usable):
    """Same scale R1 effectively ran at (~0.0016), but multiplied rather than
    pinned. The difference is the whole finding."""
    rows = [R.shape_fidelity(t, 0.0016) for t in usable]
    ranks = [r["size_rank"] for r in rows if r["size_rank"] is not None]
    assert len(ranks) > 0.9 * len(rows)          # rank is DEFINED almost everywhere
    assert sum(ranks) / len(ranks) > 0.90
    # and the inventory path is far closer than the constant rule's ~0.24
    mad = sum(r["path_mad"] for r in rows) / len(rows)
    assert mad < 0.10


def test_the_strict_shape_floor_is_where_weak_windows_stop_holding(windows):
    """The floor is set on the p10 window, not the mean: a scale that works
    on the average window and fails on the awkward one is not a floor."""
    rows = R.sweep(windows, [0.004, 0.006, 0.02])
    by_k = {r["k"]: r for r in rows}
    assert by_k[0.004]["collapse_share_mean"] > 0.05
    assert by_k[0.006]["size_rank_p10"] >= 0.90
    assert by_k[0.006]["fully_collapsed_windows"] <= 0.01
    # and it keeps improving above the floor, monotonically in path distance
    assert by_k[0.02]["path_mad_p90"] < by_k[0.006]["path_mad_p90"]


def test_shape_thresholds_were_fixed_before_the_sweep():
    """They are module constants, not arguments tuned per call."""
    assert R.SHAPE_THRESHOLDS == {"size_rank_mean_min": 0.90,
                                  "path_mad_mean_max": 0.05,
                                  "fully_collapsed_windows_max": 0.05}


# --------------------------------------------------------------------------
# the scale comes from the envelope, not from a per-order cap

def test_the_scale_is_derived_from_ceilings_and_names_the_binding_one(envelope):
    s = R.risk_scale(envelope, max_residual_usd=3.00, max_exposure_usd=30.00,
                     daily_loss_stop_usd=6.00, windows_per_day=2, basis="max")
    assert s["binding_constraint"] in s["candidates"]
    assert s["k"] == min(s["candidates"].values())
    assert s["k"] == pytest.approx(0.00619, abs=1e-4)


def test_a_tighter_envelope_lowers_the_scale_monotonically(envelope):
    big = R.risk_scale(envelope, max_residual_usd=3.0, max_exposure_usd=30.0,
                       daily_loss_stop_usd=6.0, windows_per_day=2)
    small = R.risk_scale(envelope, max_residual_usd=2.0, max_exposure_usd=20.0,
                         daily_loss_stop_usd=4.0, windows_per_day=2)
    assert small["k"] < big["k"]


def test_the_R1_envelope_cannot_preserve_the_shape_at_any_multiplier(envelope):
    """The number that decides the next stage. $0.05 is not a small version of
    L5's trajectory; it is too small to BE one."""
    s = R.risk_scale(envelope, max_residual_usd=0.05, max_exposure_usd=0.05,
                     daily_loss_stop_usd=1.00, basis="max")
    assert s["k"] < 1e-4
    assert 0.006 / s["k"] > 500      # ~582x below the floor


def test_max_basis_is_never_looser_than_p99(envelope):
    """The hard guarantee must not be the permissive one."""
    hard = R.risk_scale(envelope, max_residual_usd=3.0, max_exposure_usd=30.0,
                        daily_loss_stop_usd=6.0, basis="max")
    soft = R.risk_scale(envelope, max_residual_usd=3.0, max_exposure_usd=30.0,
                        daily_loss_stop_usd=6.0, basis="p99")
    assert hard["k"] <= soft["k"]


# --------------------------------------------------------------------------
# it must not quietly become a trading rule

def test_the_module_places_nothing_and_invents_no_signal():
    src = open(R.__file__, encoding="utf-8").read()
    for verb in ("place_limit_buy", "place_order", "submit", "cancel_exact",
                 "Transport", "requests", "urllib"):
        assert verb not in src, verb


def test_scaling_never_changes_side_or_ordering(usable):
    """Relative size and trajectory shape may compress; the SEQUENCE of L5's
    decisions may not be reordered or flipped by the scale."""
    t = max(usable, key=len)
    path = R.scaled_path(t, 0.01)
    assert len(path["qty"]) == len(t)
    assert all(q > 0 for q in path["qty"])
    signs_l5, signs_ours, a, b = [], [], 0.0, 0.0
    for r, q in zip(t, path["qty"]):
        s = 1 if r["outcome"] == "Up" else -1
        a += s * r["qty"]; b += s * q
        signs_l5.append(a >= 0); signs_ours.append(b >= 0)
    agree = sum(1 for x, y in zip(signs_l5, signs_ours) if x == y) / len(signs_l5)
    assert agree > 0.95, "scaling flipped the side of the inventory path"
