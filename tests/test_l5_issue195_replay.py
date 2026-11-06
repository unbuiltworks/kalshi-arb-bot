"""The causal replay's conclusions, pinned so they cannot drift silently.

These are the numbers the PR for issue #195 reports. They come from archived
live records, so a change in the ranking, the fee model or the policy that
moves them will fail here rather than quietly restating the evidence.

Nothing here opens a socket, uses a credential, or submits anything: the
replay opens two committed JSON files and nothing else.
"""
from __future__ import annotations

import inspect
from decimal import Decimal as D

import pytest

from src import l5_inventory_decision as ID
from src import l5_issue195_replay as RP
from src import l5_taker_completion_policy as TCP

W0300 = "cpc-btc-updown-15m-2026-11-06-0300z"
W0315 = "cpc-btc-updown-15m-2026-11-06-0315z"


@pytest.fixture(scope="module")
def report():
    return RP.replay()


@pytest.fixture(scope="module")
def oos():
    return RP.replay_oos()


def _win(report, market):
    for w in report["windows"]:
        if w["market"] == market:
            return w
    raise AssertionError("%s missing from the replay" % market)


# =====================================================================
# 1. THE ARCHIVE IS REAL AND NAMED.
# =====================================================================

def test_the_replay_reads_the_archived_live_records(report):
    assert "L5_RESIDUAL_BOOK_SAMPLE" in report["source"]["samples"]
    assert "L5_TS_COMPLETION" in report["source"]["decisions"]
    assert "1603da04" in report["source"]["samples"]


def test_both_losing_windows_are_replayed(report):
    assert {w["market"] for w in report["windows"]} == {W0300, W0315}
    assert all(w["ok"] for w in report["windows"])


def test_the_replay_reads_nothing_but_files(report):
    """It must not be able to reach a venue, a store or an order path.

    CHECKED ON THE PARSED TREE. The module's docstring says it holds no
    transport, so a substring scan reads its own promise as a violation.
    """
    import ast
    tree = ast.parse(inspect.getsource(RP))
    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)
    for forbidden in ("submit", "cancel", "deadman", "PmusTransport",
                      "InventoryStore", "urlopen", "post", "request",
                      "positions", "account", "save"):
        assert forbidden not in called, forbidden
    # and it imports nothing that could reach a venue
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                imported.add(a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                imported.add(a.name)
    for forbidden in ("l5_pmus_transport", "l5_live_execution",
                      "pmus_q1_executor", "l5_pmus_live_runner"):
        assert forbidden not in imported, forbidden


# =====================================================================
# 2. THE LIVE ENGINE NEVER SUBMITTED, AND THE RECORD SAYS WHY.
# =====================================================================

@pytest.mark.parametrize("market,n_ok", [(W0300, 43), (W0315, 22)])
def test_the_engine_decided_yes_and_submitted_nothing(report, market, n_ok):
    live = _win(report, market)["live_engine"]
    assert live["decisions_ok"] == n_ok
    assert live["ever_submitted"] == 0
    assert live["crossed_values"] == ["False"]
    assert live["withheld_by"] == ["POLICY_DISABLED"]


# =====================================================================
# 3. BOTH RESIDUALS WERE PREVENTABLE FROM THE ACTUAL BOOKS.
# =====================================================================

def test_the_replay_concludes_the_residuals_were_preventable(report):
    assert report["preventable"] is True


@pytest.mark.parametrize("market,samples,actionable", [
    (W0300, 133, 117), (W0315, 123, 96)])
def test_most_recorded_books_offered_an_executable_completion(
        report, market, samples, actionable):
    w = _win(report, market)
    assert w["samples"] == samples
    assert w["executable_and_economic_samples"] == actionable


@pytest.mark.parametrize("market,impr,px", [
    (W0300, "0.6500", "0.3300"), (W0315, "0.3500", "0.6300")])
def test_the_first_actionable_book_in_each_window(report, market, impr, px):
    f = _win(report, market)["first_actionable"]
    assert f["chosen"] == ID.TAKER_COMPLETE
    assert f["worst_improvement_usd"] == impr
    assert f["own_px"] == px
    assert f["qty"] == "1"


@pytest.mark.parametrize("market", [W0300, W0315])
def test_the_haircut_book_covered_the_need_a_thousand_times_over(report,
                                                                 market):
    """Depth is the one thing that was never the problem: after discarding
    three quarters of the displayed size, the book still held more than a
    thousand times the single contract either window needed."""
    f = _win(report, market)["first_actionable"]
    assert D(f["depth_multiple"]) > D("1000")


# =====================================================================
# 4. THE COUNTERFACTUAL SETTLEMENT, AGAINST THE VENUE'S OWN OUTCOME.
# =====================================================================

@pytest.mark.parametrize("market,actual,counter,delta", [
    (W0300, "-0.59", "0.0600", "0.6500"),
    (W0315, "-0.38", "-0.0300", "0.3500")])
def test_each_window_would_have_settled_better(report, market, actual,
                                               counter, delta):
    c = _win(report, market)["counterfactual_at_first_actionable"]
    assert c["settled_up"] is False          # both windows settled DOWN
    assert c["actual_settled_usd"] == actual
    assert c["counterfactual_settled_usd"] == counter
    assert c["delta_usd"] == delta


def test_the_campaign_would_have_broken_even_instead_of_losing_a_dollar(
        report):
    c = report["campaign"]
    assert c["actual_settled_usd"] == "-0.97"
    assert D(c["counterfactual_at_first_actionable_usd"]) > 0
    assert c["delta_at_first_usd"] == "1.0000"


def test_no_fill_is_ever_claimed(report):
    assert "not a claimed fill" in _win(
        report, W0300)["counterfactual_at_first_actionable"]["basis"]
    assert "NOT OF EXECUTIONS" in report["labels"]


# =====================================================================
# 5. WIRING THE ROUTE IS NOT SUFFICIENT. This is the finding it would be
#    easiest to omit, so it is asserted.
# =====================================================================

def test_the_replay_says_the_route_alone_is_not_sufficient(report):
    assert report["sufficient"] is False
    assert "NOT sufficient" in report["why_not_sufficient"]
    assert "margin floor" in report["why_not_sufficient"]


@pytest.mark.parametrize("market", [W0300, W0315])
def test_the_deployed_profit_mode_refuses_both_rescues(report, market):
    v = _win(report, market)["policy_verdicts_on_the_live_decision"]
    assert v["profit_mode"]["authorized"] is False
    assert v["profit_mode"]["reason"] == "WORST_OUTCOME_NOT_POSITIVE"


@pytest.mark.parametrize("market", [W0300, W0315])
def test_the_loss_reduction_mode_authorizes_them(report, market):
    v = _win(report, market)["policy_verdicts_on_the_live_decision"]
    assert v["loss_reduction_mode"]["authorized"] is True
    assert v["loss_reduction_mode"]["reason"] == "AUTHORIZED_RESCUE"


def test_the_loss_reduction_mode_is_default_off(monkeypatch):
    monkeypatch.delenv(TCP.LOSS_REDUCTION_ENV, raising=False)
    assert TCP.loss_reduction_authorized() is False
    monkeypatch.setenv(TCP.LOSS_REDUCTION_ENV, "1")
    assert TCP.loss_reduction_authorized() is False
    monkeypatch.setenv(TCP.LOSS_REDUCTION_ENV, "YES")
    assert TCP.loss_reduction_authorized() is True


def test_the_rescue_needs_a_named_improvement_floor():
    """"Strictly better than carrying" by a hundredth of a cent is not a
    reason to spend cash, so the floor must be NAMED and positive."""
    dec = {"ok": True, "side": "DOWN", "qty": "1", "price": "0.33",
           "deficit": "1", "displayed_depth": "1312",
           "both_positive_after": False,
           "worst_before": "-0.65", "worst_after": "-0.02"}
    common = dict(decision=dec, enabled=True, owner_authorized=True,
                  held_up_qty=D(2), held_dn_qty=D(1),
                  held_up_px=D("0.62"), held_dn_px=D("0.35"),
                  maker_attempted=True, maker_rested_s=D(120),
                  window_outlay_spent_usd=D(0), halted=False)
    unset = TCP.authorize(**dict(common, loss_reduction=True))
    assert unset["authorized"] is False
    assert unset["reason"] == "RESCUE_IMPROVEMENT_FLOOR_UNSET"
    too_small = TCP.authorize(**dict(common, loss_reduction=True,
                                     min_worst_improvement_usd=D("5.00")))
    assert too_small["reason"] == "RESCUE_IMPROVEMENT_BELOW_FLOOR"
    ok = TCP.authorize(**dict(common, loss_reduction=True,
                              min_worst_improvement_usd=D("0.25")))
    assert ok["authorized"] is True and ok["reason"] == "AUTHORIZED_RESCUE"


def test_with_the_rescue_switch_off_the_policy_is_unchanged():
    """Byte-identical to today: the same refusal, by the same name."""
    dec = {"ok": True, "side": "DOWN", "qty": "1", "price": "0.33",
           "deficit": "1", "both_positive_after": False,
           "worst_before": "-0.65", "worst_after": "-0.02"}
    out = TCP.authorize(
        decision=dec, enabled=True, owner_authorized=True,
        held_up_qty=D(2), held_dn_qty=D(1), held_up_px=D("0.62"),
        held_dn_px=D("0.35"), maker_attempted=True, maker_rested_s=D(120),
        window_outlay_spent_usd=D(0), halted=False)
    assert out["authorized"] is False
    assert out["reason"] == "WORST_OUTCOME_NOT_POSITIVE"


# =====================================================================
# 6. THE DECAY, AND WHAT THE REPLAY CANNOT SEE.
# =====================================================================

def test_the_0315z_opportunity_decayed_across_the_window(report):
    d = _win(report, W0315)["decay"]
    assert d["first_improvement_usd"] == "0.3500"
    assert d["last_improvement_usd"] == "0.0400"


def test_the_replay_states_that_the_first_98_seconds_are_unobservable(report):
    """THE HONEST LIMIT. `sample_residual_book` runs after `roll` in the same
    tick, so the stall blocked the sampler too and the earliest recorded book
    is about +115 s. The gap is evidence nobody has, not evidence for us."""
    note = report["limitation_the_first_98_seconds_are_unobservable"]
    assert "BLOCKED THE SAMPLER" in note
    assert "gap in the evidence rather than a point in the fix's favour" in note


@pytest.mark.parametrize("market", [W0300, W0315])
def test_the_earliest_observable_book_is_after_the_stall(report, market):
    assert _win(report, market)["first_actionable"]["t_in_window_s"] > 100.0


# =====================================================================
# 7. OUT OF SAMPLE.
# =====================================================================

def test_the_out_of_sample_windows_are_later_and_untuned(oos):
    assert "OUT OF SAMPLE" in oos["purpose"]
    assert "1130z and 1145z" in oos["purpose"]
    assert oos["samples"] > 400


def test_the_ranking_discriminates_out_of_sample(oos):
    """`discriminates` false would mean it fires everywhere or nowhere,
    which would make it evidence of nothing."""
    assert oos["discriminates"] is True
    assert oos["chose_completion"] > 0 and oos["chose_hold"] > 0
    assert oos["hold_reasons"]


def test_the_out_of_sample_improvements_span_the_marginal_and_the_large(oos):
    assert D(oos["improvement_usd"]["min"]) <= D("0.05")
    assert D(oos["improvement_usd"]["max"]) >= D("1.00")


def test_an_improvement_floor_thins_the_out_of_sample_population(oos):
    """A ranking that surfaces a one-cent rescue is working correctly; acting
    on one is not. The floor is the owner's choice and the breakdown is what
    makes it a choice rather than an implication."""
    surv = oos["survivors_by_improvement_floor_usd"]
    counts = [surv[k] for k in ("0.05", "0.10", "0.25", "0.50", "1.00")]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] < oos["chose_completion"]
    assert counts[-1] > 0


def test_no_realized_pnl_is_claimed_out_of_sample(oos):
    assert "NO realized P&L is computed" in oos["labels"]
    assert "counterfactual_settled_usd" not in oos
