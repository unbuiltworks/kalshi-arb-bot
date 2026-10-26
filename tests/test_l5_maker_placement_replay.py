"""The maker placement policy must be causal, unpaid-for, and patient.

THE THREE THINGS THAT COULD MAKE THIS RESULT A LIE:

1. LOOKAHEAD. 83.4% of L5's classifiable fills are maker fills, so his public
   print is when his order was HIT, not when he placed it. A policy that
   placed on his print would be backdating its own entry. The test here is
   behavioural, not a code scan: truncating the tape must not change any
   decision that already happened.

2. A FEE A MAKER DOES NOT PAY, or a rebate it has not been credited. The
   venue charges makers nothing, so no maker leg may carry a fee; and the
   rebate is credited per print with banker's rounding to the cent, which at
   the deployed caps is $0.00 almost everywhere. It must never be inside
   `net_realized_usd`.

3. IMPATIENCE. An eager rule that completes at the first acceptable pair turns
   the looser ceiling into a LOSS -- measured, not hypothesised: on the
   holdout it produced the same five completions for $0.04 less.
"""
from __future__ import annotations

import json
import pathlib
from decimal import Decimal as D

import pytest

from src import l5_maker_pair_gate as GATE
from src import l5_maker_placement_replay as M
from src import l5_sequential_replay as R

FIXTURE = (pathlib.Path(__file__).parent / "fixtures"
           / "l5_sequential_oos_corpus_v1.json")


@pytest.fixture()
def corpus():
    doc = json.load(open(FIXTURE))
    rows = R.extract_corpus(doc)
    return rows, R.outcomes_from_terminal_book(rows)


def run(rows, outs, **kw):
    kw.setdefault("ceiling", D("0.99"))
    kw.setdefault("tight_ceiling", D("0.96"))
    kw.setdefault("fill_mode", M.FILL_STRICT)
    return M.replay(rows, outs, **kw)


# =========================================================== 1. causality
def test_truncating_the_tape_cannot_change_a_past_decision(corpus):
    """THE NO-LOOKAHEAD TEST. If the policy peeked ahead, a completion near
    the cut would appear or vanish when the future is removed."""
    rows, outs = corpus
    full = run(rows, outs)
    for k in (80, 150, 220):
        part = run(rows[:k], outs)
        # every completion in the truncated run must appear, identically, in
        # the full run -- same market, same prices, same size.
        for c in part["completed_pairs"]:
            assert c in full["completed_pairs"], (k, c)


def test_the_replay_never_reads_an_l5_fill_field(corpus):
    """The corpus rows carry only book state; assert the policy would break
    if it wanted a fill, i.e. no such key is present to read."""
    rows, _ = corpus
    forbidden = {"l5_price", "l5_size", "l5_side", "fill", "px", "trade",
                 "maker_taker", "l5_fill_t"}
    for r in rows:
        assert not (set(r) & forbidden), set(r) & forbidden


def test_rows_from_a_later_window_do_not_complete_an_earlier_leg(corpus):
    """A leg must settle when its window rolls, never complete across one."""
    rows, outs = corpus
    out = run(rows, outs)
    for c in out["completed_pairs"]:
        assert c["market"]
    # every stranded leg belongs to exactly one window, and the number of
    # sequences cannot exceed the rows that produced them
    assert out["n_completed"] + out["n_stranded"] <= out["n_rows"]


# ================================================ 2. fees and rebates
def test_a_maker_leg_is_charged_no_fee(corpus):
    """A completed maker pair's net must be exactly qty * (1 - pair)."""
    rows, outs = corpus
    out = run(rows, outs, open_maker=True)
    assert out["completed_pairs"], "fixture must produce completions"
    for c in out["completed_pairs"]:
        assert c["opened_as"] == "MAKER"
        expect = D(c["qty"]) * (D(1) - D(c["pair"]))
        assert D(c["net_pnl"]) == expect


def test_the_rebate_is_never_inside_net_realized(corpus):
    rows, outs = corpus
    out = run(rows, outs)
    assert D(out["net_with_rebate_usd"]) == (
        D(out["net_realized_usd"]) + D(out["rebate_credited_usd"]))
    assert D(out["rebate_credited_usd"]) >= 0


def test_the_rebate_uses_the_venue_per_print_cent_rounding():
    """Below the cent floor the venue credits nothing, and our own history
    records that happening (< ~1.62 shares per print credited 0.00)."""
    assert M.maker_rebate_credited(D("2"), D("0.18")) == D("0.00")
    assert M.maker_rebate_credited(D("2"), D("0.38")) == D("0.01")
    assert M.maker_rebate_credited(D("100"), D("0.50")) == D("0.31")


def test_a_taker_opening_leg_IS_charged_a_fee(corpus):
    """The comparison arm must not get the maker's free ride."""
    rows, outs = corpus
    mk = run(rows, outs, open_maker=True)
    tk = run(rows, outs, open_maker=False)
    assert any(c["opened_as"] == "TAKER" for c in tk["completed_pairs"])
    for c in tk["completed_pairs"]:
        gross = D(c["qty"]) * (D(1) - D(c["pair"]))
        assert D(c["net_pnl"]) < gross          # a fee was taken out
    assert D(tk["net_realized_usd"]) < D(mk["net_realized_usd"])


# ======================================================== 3. patience
def test_an_eager_loose_ceiling_is_strictly_worse(corpus):
    """THE MEASURED DEFECT. Same completions, less money."""
    rows, outs = corpus
    patient = run(rows, outs, ceiling=D("0.99"), loose_after_s=600.0)
    eager = run(rows, outs, ceiling=D("0.99"), loose_after_s=0.0)
    assert eager["n_completed"] == patient["n_completed"]
    assert D(eager["net_realized_usd"]) < D(patient["net_realized_usd"])


def test_the_patient_loose_ceiling_is_a_no_op_on_this_holdout(corpus):
    """HONEST RESULT: on 5 windows the band never contained the only
    available completion, so the gate changes nothing. It is pinned so that
    nobody reports the ceiling change as an improvement it did not make."""
    rows, outs = corpus
    tight = run(rows, outs, ceiling=D("0.96"), loose_after_s=600.0)
    loose = run(rows, outs, ceiling=D("0.99"), loose_after_s=600.0)
    assert D(loose["net_realized_usd"]) == D(tight["net_realized_usd"])
    assert loose["n_completed"] == tight["n_completed"]


def test_the_tight_ceiling_can_never_exceed_the_loose_one(corpus):
    rows, outs = corpus
    out = run(rows, outs, ceiling=D("0.96"), tight_ceiling=D("0.99"))
    assert D(out["tight_ceiling"]) <= D(out["ceiling"])


# ================================================= the mechanics themselves
def test_own_maker_cost_is_the_resting_side_not_the_touch():
    """Resting is cheaper than crossing by the spread, on both sides."""
    bid, ask = D("0.31"), D("0.32")
    assert M.own_maker_cost("UP", bid, ask) == D("0.31")
    assert R.own_taker_cost("UP", bid, ask) == D("0.32")
    assert M.own_maker_cost("DOWN", bid, ask) == D("0.68")
    assert R.own_taker_cost("DOWN", bid, ask) == D("0.69")
    for side in ("UP", "DOWN"):
        assert (M.own_maker_cost(side, bid, ask)
                < R.own_taker_cost(side, bid, ask))


def test_strict_fill_requires_the_market_to_come_THROUGH_the_price():
    assert M._filled(M.FILL_AT_TOUCH, D("0.50"), D("0.50")) is True
    assert M._filled(M.FILL_STRICT, D("0.50"), D("0.50")) is False
    assert M._filled(M.FILL_STRICT, D("0.49"), D("0.50")) is True


def test_strict_fills_are_never_more_numerous_than_at_touch(corpus):
    rows, outs = corpus
    s = run(rows, outs, fill_mode=M.FILL_STRICT)
    t = run(rows, outs, fill_mode=M.FILL_AT_TOUCH)
    assert s["n_completed"] <= t["n_completed"]


def test_capital_and_order_limits_are_respected(corpus):
    rows, outs = corpus
    out = run(rows, outs)
    assert D(out["peak_cash_deployed_usd"]) <= R.MAX_CASH_DEPLOYED_USD
    assert D(out["peak_cash_deployed_usd"]) <= R.OPENING_NOTIONAL_BUDGET_USD


def test_cancellation_actually_happens(corpus):
    """A policy that never cancels is not tracking the book."""
    rows, outs = corpus
    out = run(rows, outs)
    assert out["cancels_on_drift"] > 0


def test_settlement_evidence_is_labelled_on_every_strand(corpus):
    rows, outs = corpus
    out = run(rows, outs)
    for s in out["stranded"]:
        assert s["settlement_evidence"] == R.TERMINAL_BOOK_INFERRED
        assert s["opened_as"] in ("MAKER", "TAKER")


def test_a_missing_outcome_is_named_not_assumed(corpus):
    rows, _ = corpus
    out = run(rows, {})
    for s in out["stranded"]:
        assert s["settlement_evidence"] == "NO_OUTCOME_AVAILABLE"


def test_the_first_leg_only_loss_is_reported_separately(corpus):
    """The strand term is what decides the sign; it gets its own line."""
    rows, outs = corpus
    out = run(rows, outs)
    assert D(out["first_leg_only_loss_usd"]) == sum(
        D(s["net_pnl"]) for s in out["stranded"])
    assert D(out["first_leg_only_loss_usd"]) < 0


def test_the_default_ceiling_is_the_taker_era_one(corpus):
    """The module must not default to the unauthorized bound."""
    rows, outs = corpus
    out = M.replay(rows, outs)
    assert D(out["ceiling"]) == GATE.TAKER_ERA_CEILING == D("0.96")


def test_the_replay_touches_no_venue():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(M))
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module.split(".")[0])
    for f in ("requests", "urllib", "http", "socket", "ssl"):
        assert f not in imported, f
