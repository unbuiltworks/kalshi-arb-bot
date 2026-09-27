"""The checkpoint's eight reported rows must reconcile, and EXPECTED incentives
must never be mixed into the trading line.

The 2026-09-27 presentation carried one line called `total_net` and another
called `realized_equivalent`, the second being the first plus a MODELED maker
rebate. Two things were wrong with that shape: "realized" named something that
was never filled, let alone credited, and there was no line at all that was
free of an incentive term. These tests pin the replacement.

They check the identities BOTH ways: against the published ledger JSON, and
recomputed straight from the scorer, so a test cannot pass merely by reading
back the arithmetic that produced the file.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_completion_shadow as S           # noqa: E402
from scripts.audit_economic_complete import load, USABLE   # noqa: E402

THETA, VARIANT, FILL = D("0.96"), "B", "queue_aware"
CLIPS = (25, 50)
ROWS = ("ec_matched_gross", "passive_matched_gross", "matched_gross", "fees",
        "residual_pnl", "shadow_trading_net", "expected_maker_rebate",
        "shadow_modeled_net_including_expected_rebate")
TOL = D("0.0002")        # the rolling rows are quantized to 0.0001


def ledger(clip):
    p = "analysis/pmus/pmus_window_ledger_q%d.json" % clip
    if not os.path.exists(p):
        pytest.skip("%s not generated" % p)
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def checkpoints(doc):
    return [(k, v) for k, v in doc["rolling"].items() if isinstance(v, dict)]


@pytest.mark.parametrize("clip", CLIPS)
def test_every_reported_row_is_present_and_in_order(clip):
    doc = ledger(clip)
    assert doc["accounting"]["reporting_order"] == list(ROWS)
    for w in doc["per_window"]:
        for r in ROWS:
            assert r in w, (w["window"], r)
    for name, cp in checkpoints(doc):
        for r in ROWS:
            assert r in cp, (name, r)


@pytest.mark.parametrize("clip", CLIPS)
def test_the_two_mechanisms_sum_to_total_matched_gross(clip):
    for w in ledger(clip)["per_window"]:
        assert abs(D(w["ec_matched_gross"]) + D(w["passive_matched_gross"])
                   - D(w["matched_gross"])) < TOL, w["window"]


@pytest.mark.parametrize("clip", CLIPS)
def test_trading_net_identity_per_window(clip):
    """matched_gross - fees + residual_pnl = shadow_trading_net"""
    for w in ledger(clip)["per_window"]:
        lhs = D(w["matched_gross"]) - D(w["fees"]) + D(w["residual_pnl"])
        assert abs(lhs - D(w["shadow_trading_net"])) < TOL, w["window"]


@pytest.mark.parametrize("clip", CLIPS)
def test_modeled_net_identity_per_window(clip):
    """shadow_trading_net + expected_maker_rebate = shadow_modeled_net"""
    for w in ledger(clip)["per_window"]:
        lhs = D(w["shadow_trading_net"]) + D(w["expected_maker_rebate"])
        assert abs(lhs - D(w["shadow_modeled_net_including_expected_rebate"])) \
            < TOL, w["window"]


@pytest.mark.parametrize("clip", CLIPS)
def test_both_identities_hold_at_every_rolling_checkpoint(clip):
    doc = ledger(clip)
    assert checkpoints(doc), "no rolling checkpoint is available yet"
    for name, cp in checkpoints(doc):
        a = D(cp["matched_gross"]) - D(cp["fees"]) + D(cp["residual_pnl"])
        assert abs(a - D(cp["shadow_trading_net"])) < TOL, name
        b = D(cp["shadow_trading_net"]) + D(cp["expected_maker_rebate"])
        assert abs(b - D(cp["shadow_modeled_net_including_expected_rebate"])) \
            < TOL, name
        c = D(cp["ec_matched_gross"]) + D(cp["passive_matched_gross"])
        assert abs(c - D(cp["matched_gross"])) < TOL, name


@pytest.mark.parametrize("clip", CLIPS)
def test_fees_are_a_positive_cost_magnitude(clip):
    """The identity SUBTRACTS fees, so a negative fee would silently add revenue."""
    for w in ledger(clip)["per_window"]:
        assert D(w["fees"]) >= 0, (w["window"], w["fees"])
    for name, cp in checkpoints(ledger(clip)):
        assert D(cp["fees"]) >= 0, (name, cp["fees"])


@pytest.mark.parametrize("clip", CLIPS)
def test_the_trading_line_contains_no_incentive_term(clip):
    """shadow_trading_net must move only with trading terms.

    Recomputed from the scorer with no reference to the rebate at all: if an
    expected incentive ever leaks into the trading line, this diverges.
    """
    by = load()
    doc = ledger(clip)
    rows = {w["window"]: w for w in doc["per_window"]}
    for slug, winner in USABLE.items():
        w = rows.get(slug[-5:])
        if w is None:
            continue
        r = S.run_window(by[slug], clip=D(clip), theta=THETA, winner=winner,
                         variant=VARIANT, fill_model=FILL)
        mine = r["matched_pnl"] - r["fees"] + r["resid_pnl"]
        assert abs(mine - D(w["shadow_trading_net"])) < TOL, slug
        # and the scorer's own net is the same quantity, by a second route
        assert abs(r["net"] - D(w["shadow_trading_net"])) < TOL, slug


@pytest.mark.parametrize("clip", CLIPS)
def test_the_mechanism_split_comes_from_the_scorer(clip):
    by = load()
    rows = {w["window"]: w for w in ledger(clip)["per_window"]}
    for slug, winner in USABLE.items():
        w = rows.get(slug[-5:])
        if w is None:
            continue
        r = S.run_window(by[slug], clip=D(clip), theta=THETA, winner=winner,
                         variant=VARIANT, fill_model=FILL)
        assert abs(r["gross_margin"] - D(w["ec_matched_gross"])) < TOL, slug
        assert abs(r["matched_pnl"] - r["gross_margin"]
                   - D(w["passive_matched_gross"])) < TOL, slug


@pytest.mark.parametrize("clip", CLIPS)
def test_no_row_is_labelled_realized_or_credited(clip):
    """The lifecycle labels are not interchangeable; the file must not blur them."""
    doc = ledger(clip)
    banned = ("realized", "credited")
    for w in doc["per_window"]:
        for k in w:
            assert not any(b in k.lower() for b in banned), (w["window"], k)
    for name, cp in checkpoints(doc):
        for k in cp:
            assert not any(b in k.lower() for b in banned), (name, k)
    assert doc["accounting"]["labels"].startswith("SHADOW")


@pytest.mark.parametrize("clip", CLIPS)
def test_the_rebate_is_declared_expected_not_credited(clip):
    a = ledger(clip)["accounting"]["expected_vs_credited"]
    assert "EXPECTED" in a and "not CREDITED" in a and "not REALIZED" in a
