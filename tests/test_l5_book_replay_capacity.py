"""The deployed controller driven over RECORDED books at production limits.

WHY A THIRD HARNESS EXISTS, and why the other two could not answer this.
`round_strategy_compare.py` replays the ECONOMICS and knows nothing about
the gates. `test_l5_two_sided_wiring` drives the real gates but feeds ONE
STATIC BOOK every tick, so a refusal there can be an artifact of a book that
never moves -- and it was: on the static book the dominant refusal was
`PAIR_COST_ABOVE_CEILING` in 21 of 24 ticks, which says more about the
fixture than the engine.

This feeds recorded ladder frames into the real `FaithfulExecutor.handle` at
the exact deployed configuration and counts outcomes plus a HISTOGRAM OF
REFUSALS. The histogram is the point: the LAST refusal of a run is not the
binding one, and reading it as such is how an artifact becomes a "finding".

A CLAIM LEDGER PER WINDOW IS MANDATORY. The first version of this
measurement shared one `L5_LIVE_AUTH_LEDGER` across all 13 windows, so
claims leaked and every round read as spent -- producing
`ACCUMULATION_EXHAUSTED` 375 times and an entirely fictitious conclusion
that the round budget was the binding constraint. The isolation below is not
hygiene; it is the difference between a result and an artifact.

NOTHING HERE IS A VENUE FILL. The harness transport acknowledges and fills
synthetically, so these are MODELLED fills on REAL prices. No figure from
this file is realized money, and our confirmed settled realized net is
unchanged at -$15.6109.
"""
from __future__ import annotations

import sys
from collections import Counter
from decimal import Decimal as D

import pytest

sys.path.insert(0, ".")
from analysis.l5.issue206 import replay_executor_on_books as RB  # noqa: E402
from analysis.l5.issue195.queue_depletion_report import (  # noqa: E402
    OUT_OF_SAMPLE, _load)
import tests.test_l5_two_sided_wiring as W  # noqa: E402
from tests.test_l5_two_sided_wiring import probe  # noqa: E402,F401
from src.l5_live_validation_gates import ValidationGates as VG  # noqa: E402

#: measured at the deployed configuration over the 13 out-of-sample windows
#: with >= 10 recorded frames. Modelled fills on real prices.
EXPECT_SENT = 29
EXPECT_UP_ORDERS = 14
EXPECT_DOWN_ORDERS = 15
EXPECT_PAIRS = D("12")
EXPECT_RESIDUAL = D("5")


def _windows():
    return {s: f for p, sh in OUT_OF_SAMPLE
            for s, f in _load(p, sh).items() if len(f) >= 10}


def _drive(H, E, tmp_path, monkeypatch, limits=None, gates=None):
    out = {"sent": 0, "pairs": D(0), "residual": D(0), "hist": Counter(),
           "sides": Counter(), "windows": 0}
    for i, (slug, frames) in enumerate(sorted(_windows().items())):
        wd = tmp_path / ("w%d" % i)
        wd.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("L5_LIVE_AUTH_LEDGER", str(wd / "auth.jsonl"))
        g = VG(**dict(RB.PROD_GATES, **(gates or {})))
        ex, t = W.mk(H, E, wd, g=g)
        ex.limits = E.SafetyLimits(**dict(RB.PROD_LIMITS, **(limits or {})))
        for f in frames:
            bk = RB.frame_to_book(f)
            if bk is None:
                continue
            r = ex.handle(W._row(H), bk, market="m", window="21:00")
            why = r.get("refused_reason")
            out["hist"][str(why).split(":")[0] if why else "SUBMITTED"] += 1
        up = abs(D(str(ex.store.position("m", "UP") or 0)))
        dn = abs(D(str(ex.store.position("m", "DOWN") or 0)))
        out["sent"] += len(t.sent)
        out["pairs"] += min(up, dn)
        out["residual"] += abs(up - dn)
        out["windows"] += 1
        for s in t.sent:
            out["sides"][s["side"]] += 1
    return out


# =============== 1. SIMULTANEOUS TWO-SIDED QUOTING, ON REAL BOOKS

def test_both_sides_are_quoted_and_near_balanced(probe, tmp_path,
                                                 monkeypatch):
    """L5's own signature is 49.8/50.2 UP/DOWN. Ours must not be one-sided."""
    H, E = probe
    r = _drive(H, E, tmp_path, monkeypatch)
    assert r["windows"] == 13
    assert r["sides"]["UP"] == EXPECT_UP_ORDERS
    assert r["sides"]["DOWN"] == EXPECT_DOWN_ORDERS
    bal = abs(r["sides"]["UP"] - r["sides"]["DOWN"])
    assert bal <= 2, r["sides"]


def test_pairs_complete_and_most_orders_become_pair_halves(probe, tmp_path,
                                                            monkeypatch):
    H, E = probe
    r = _drive(H, E, tmp_path, monkeypatch)
    assert r["pairs"] == EXPECT_PAIRS
    assert r["residual"] == EXPECT_RESIDUAL
    # 2 orders per pair, so 24 of 29 sent became pair halves
    assert (r["pairs"] * 2) / D(r["sent"]) > D("0.8"), r


def test_more_than_one_pair_completes_in_the_sample(probe, tmp_path,
                                                     monkeypatch):
    """Replenishment: 12 pairs over 13 windows is not one-and-stop."""
    H, E = probe
    r = _drive(H, E, tmp_path, monkeypatch)
    assert r["pairs"] >= D("10")


# ========== 2. THE CAPITAL LIMITS DO NOT BIND ON REAL BOOKS

@pytest.mark.parametrize("limits", [
    {},                                            # production exact
    {"max_total_exposure_usd": D("12")},           # 12x the exposure bound
    {"max_total_exposure_usd": D("12"), "max_live_orders": 8},
])
def test_loosening_the_capital_limits_changes_nothing(probe, tmp_path,
                                                       monkeypatch, limits):
    """THE FINDING, and it reverses what a static-book harness suggested.

    On a static book the cash gate bound and more capital bought more pairs.
    On REAL books, 12x the exposure bound and 4x the live-order cap produce
    the IDENTICAL 29 orders, 12 pairs and 5 residual. The capital
    configuration is not what limits our pair count.
    """
    H, E = probe
    r = _drive(H, E, tmp_path, monkeypatch, limits=limits)
    assert r["sent"] == EXPECT_SENT
    assert r["pairs"] == EXPECT_PAIRS
    assert r["residual"] == EXPECT_RESIDUAL


def test_no_capital_or_inventory_refusal_appears_at_all(probe, tmp_path,
                                                         monkeypatch):
    H, E = probe
    r = _drive(H, E, tmp_path, monkeypatch)
    for never in ("CASH_COMMITMENT", "MAX_TOTAL_EXPOSURE",
                  "MAX_USD_PER_MARKET", "MAX_ABS_RESIDUAL",
                  "WORST_CASE_DEFICIT", "DAILY_REALIZED_LOSS_HALT"):
        assert r["hist"][never] == 0, (never, r["hist"])


# ============ 3. WHAT DOES BIND: THE PRICE GATES

def test_the_pair_ceiling_is_the_single_largest_refusal(probe, tmp_path,
                                                         monkeypatch):
    """`PAIR_COST_ABOVE_CEILING` dominates, which matches L5's own
    distribution: its median pair basis is 0.9644 and only 51% of its
    windows sit at or below our 0.96 ceiling."""
    H, E = probe
    r = _drive(H, E, tmp_path, monkeypatch)
    top = r["hist"].most_common(1)[0]
    assert top[0] == "PAIR_COST_ABOVE_CEILING"
    assert top[1] > 300, r["hist"].most_common(5)


def test_the_binding_refusals_are_all_about_PRICE_not_capital(probe,
                                                               tmp_path,
                                                               monkeypatch):
    H, E = probe
    r = _drive(H, E, tmp_path, monkeypatch)
    price_reasons = {"PAIR_COST_ABOVE_CEILING", "FIRST_LEG_ABOVE_MAX_PRICE",
                     "PRICE_OUT_OF_BAND", "FIRST_LEG_BELOW_MIN_PRICE"}
    priced = sum(v for k, v in r["hist"].items() if k in price_reasons)
    other = sum(v for k, v in r["hist"].items()
                if k not in price_reasons and k != "SUBMITTED")
    assert priced > 500, r["hist"].most_common(6)
    assert priced > other * 20, (priced, other)


def test_the_claim_ledger_must_be_isolated_per_window(probe, tmp_path,
                                                       monkeypatch):
    """THE ARTIFACT THIS GUARDS AGAINST, pinned so it cannot return.

    Sharing one claim ledger across windows makes every round read as spent
    and produces ACCUMULATION_EXHAUSTED in the hundreds -- a wholly
    fictitious 'the round budget is the constraint' conclusion. With
    isolation it does not appear at all.
    """
    H, E = probe
    r = _drive(H, E, tmp_path, monkeypatch)
    assert r["hist"]["ACCUMULATION_EXHAUSTED"] == 0, r["hist"].most_common(5)


# ============ 4. NOTHING HERE IS A VENUE FILL

def test_these_are_modelled_fills_on_real_prices_only():
    import inspect
    src = inspect.getsource(RB)
    assert "MODELLED fills on real prices" in src
    assert "never venue fills" in src
