"""The holdout result must be reproducible by running a file.

The in-sample +$2.36 came from an inline script that no longer exists and
was wrong where it mattered. A result that reverses sign under honest
accounting is exactly the result that has to be pinned, so these tests
reproduce the -$0.6723 holdout figure from a committed corpus and fail if
anyone tunes the frozen rule.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal as D

import pytest

from src import l5_sequential_replay as R

FIXTURE = (pathlib.Path(__file__).parent / "fixtures"
           / "l5_sequential_oos_corpus_v1.json")


@pytest.fixture()
def corpus():
    doc = json.load(open(FIXTURE))
    rows = R.extract_corpus(doc)
    return rows, R.outcomes_from_terminal_book(rows)


# ================================================ the frozen rule is frozen

def test_the_frozen_constants_are_the_frozen_values():
    """If someone tunes the rule, this fails before any result does.

    These are the figures recorded in L5_SEQUENTIAL_RULE_FROZEN.md at
    1edbfbf7, before the holdout was examined.
    """
    assert R.FEE_THETA_TAKER == D("0.0695")
    assert R.FEE_EXTRA_PER_ORDER_USD == D("0.01")
    assert R.COMPLETION_TARGET == D("0.96")
    assert R.MAX_CASH_DEPLOYED_USD == D("4.64")
    assert R.OPENING_NOTIONAL_BUDGET_USD == D("0.80")
    assert R.OPENING_LEG_MAX_CONTRACTS == D("2")
    assert R.QUANTITY_INCREMENT == D("0.01")
    assert R.ENTRY_CUTOFF_S == 600.0
    assert R.COMPLETE_CUTOFF_S == 720.0
    assert R.MAX_LIVE_ORDERS == 1


def test_the_in_sample_windows_cannot_leak_into_a_validation_run():
    """Derivation windows are dropped at the extraction boundary, by name."""
    assert set(R.IN_SAMPLE_WINDOWS) == {
        "cpc-btc-updown-15m-2026-10-26-1445z",
        "cpc-btc-updown-15m-2026-10-26-1500z",
        "cpc-btc-updown-15m-2026-10-26-1515z"}
    poisoned = {"rows": [
        {"t": "2026-10-26T14:50:00+00:00",
         "market": "cpc-btc-updown-15m-2026-10-26-1445z",
         "side": "UP", "bid": "0.70", "ask": "0.71"},
        {"t": "2026-10-26T13:35:00+00:00",
         "market": "cpc-btc-updown-15m-2026-10-26-1330z",
         "side": "UP", "bid": "0.70", "ask": "0.71"}]}
    rows = R.extract_corpus(poisoned)
    assert [r["market"] for r in rows] == [
        "cpc-btc-updown-15m-2026-10-26-1330z"]


# ============================================== the holdout result, pinned

def test_the_corpus_is_the_out_of_sample_one(corpus):
    rows, _ = corpus
    assert len(rows) == 314
    assert len({r["market"] for r in rows}) == 5
    for w in R.IN_SAMPLE_WINDOWS:
        assert w not in {r["market"] for r in rows}


def test_it_reproduces_the_holdout_net_pnl_exactly(corpus):
    """THE NUMBER. -$0.6723 over 5 windows, one ledger, actual outcomes."""
    rows, outs = corpus
    out = R.replay(rows, outs)
    assert D(out["net_realized_usd"]) == D("-0.672300")
    assert out["n_completed"] == 11
    assert out["n_stranded"] == 3


def test_the_drawdown_and_capital_use_are_reproduced(corpus):
    rows, outs = corpus
    out = R.replay(rows, outs)
    assert D(out["worst_drawdown_usd"]) == D("-0.837500")
    assert D(out["peak_cash_deployed_usd"]) == D("0.800000")
    # the OPENING NOTIONAL budget binds, not the cash cap -- so raising
    # capital would not have changed this result
    assert D(out["peak_cash_deployed_usd"]) <= R.OPENING_NOTIONAL_BUDGET_USD
    assert out["refused_on_cash"] == 0


def test_every_completed_pair_is_at_or_under_the_target(corpus):
    rows, outs = corpus
    out = R.replay(rows, outs)
    for c in out["completed_pairs"]:
        assert D(c["pair"]) <= R.COMPLETION_TARGET
        # a completed pair is locked profit before fees, by construction
        assert D(c["pair"]) < D(1)


def test_all_three_strands_lost_which_is_what_decides_the_sign(corpus):
    """The honest statistical caveat, pinned as a fact about the sample.

    Three observations decide this result. Had one won, the net would be
    positive. The test records the sample's shape so nobody reads -$0.6723
    as a measured edge.
    """
    rows, outs = corpus
    out = R.replay(rows, outs)
    assert all(D(s["net_pnl"]) < 0 for s in out["stranded"])
    # and they stranded MID-PRICED, which is why they cost full notional
    for s in out["stranded"]:
        assert D("0.5") <= D(s["own_cost"]) <= D("0.6")


# =========================================== accounting and evidence grades

def test_settlement_evidence_is_labelled_on_every_strand(corpus):
    """Terminal-book inference must never pass as a venue settlement."""
    rows, outs = corpus
    out = R.replay(rows, outs)
    for s in out["stranded"]:
        assert s["settlement_evidence"] == R.TERMINAL_BOOK_INFERRED
    assert out["settlement_evidence_grades"] == [R.TERMINAL_BOOK_INFERRED]
    assert R.VENUE_SETTLEMENT != R.TERMINAL_BOOK_INFERRED


def test_a_venue_settlement_is_preferred_and_carried_through(corpus):
    """Where the venue states an outcome, it replaces the inference and the
    grade travels with it."""
    rows, _ = corpus
    outs = {m: {"winner": "UP", "evidence": R.VENUE_SETTLEMENT}
            for m in {r["market"] for r in rows}}
    out = R.replay(rows, outs)
    assert out["settlement_evidence_grades"] == [R.VENUE_SETTLEMENT]


def test_a_missing_outcome_is_named_not_assumed(corpus):
    rows, _ = corpus
    out = R.replay(rows, {})
    for s in out["stranded"]:
        assert s["settlement_evidence"] == "NO_OUTCOME_AVAILABLE"


def test_one_outstanding_order_is_enforced(corpus):
    """Capacity. 314 decisions yield 14 sequences, not 314 -- which is the
    single biggest correction to the in-sample figure."""
    rows, outs = corpus
    out = R.replay(rows, outs)
    assert out["n_completed"] + out["n_stranded"] == 14
    assert out["n_completed"] + out["n_stranded"] < len(rows)


def test_both_legs_are_charged_a_fee_at_their_own_price():
    q, a, b = D("1"), D("0.60"), D("0.36")
    gross = q * (D(1) - a - b)
    net = gross - R.taker_fee(q, a) - R.taker_fee(q, b)
    assert R.taker_fee(q, a) > 0 and R.taker_fee(q, b) > 0
    assert net < gross


def test_own_cost_is_computed_in_own_terms_not_wire_terms():
    """The 1215z reading error: summing wire prices across sides invents a
    loss on a trade that settled +$0.0984."""
    assert R.own_taker_cost("UP", D("0.87"), D("0.88")) == D("0.88")
    assert R.own_taker_cost("DOWN", D("0.87"), D("0.88")) == D("0.13")


def test_fee_sensitivity_is_reportable_without_being_an_alternative_rule(
        corpus):
    """Fees are not what makes this negative, and the test says so."""
    rows, outs = corpus
    with_fees = D(R.replay(rows, outs)["net_realized_usd"])
    no_fees = D(R.replay(rows, outs, charge_fees=False)["net_realized_usd"])
    # FEES DECIDE THE SIGN, and I first guessed the opposite. The gross rule
    # is +$0.3677 on the holdout; round-trip taker fees cost $1.0400 and take
    # it to -$0.6723. At a $0.80 opening notional the $0.01-per-order
    # allowance and the cent rounding dominate, so the taker round trip is
    # 10% of notional against a 4-tick gross edge of 8.3%.
    assert with_fees == D("-0.672300")
    assert no_fees == D("0.367700")
    assert no_fees > 0 > with_fees
    assert (no_fees - with_fees) == D("1.040000")


def test_the_replay_touches_no_venue():
    """Scanned over CODE only: the module docstring says "no socket, no
    transport" in prose, and a scan that cannot tell prose from an import
    is not a check."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(R))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("requests", "urllib", "http", "socket", "ssl"):
        assert forbidden not in imported, forbidden
    # The IMPORT set is the real guarantee: with no network module in
    # scope the module cannot reach a venue whatever it calls. A scan for
    # bare method names cannot: `get` is dict.get here, which is why the
    # first version of this check failed on its own false positive.
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for forbidden in ("submit", "cancel", "amend"):
        assert forbidden not in calls, forbidden
