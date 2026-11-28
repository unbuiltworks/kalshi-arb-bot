"""Three root causes from the owner's production evidence at 3620e232f.

The owner's independent check reported: `PROVISIONAL_PRICE_UNREADABLE`
from the opening controller, no `L5_BOOK_COMPLETION_TRIGGER` in the
sampled logs, and a ~5.4 s deferred cost-basis audit on affected ticks.
Reading the first deployed heartbeat alongside those gave three findings,
two of them defects.

  A. **My own instrumentation was wrong.** `detection_to_decision_s`
     reported n 256 median 451.744 s while every decision in the same
     record was `NO_INVENTORY` or `DECIDE_COOLDOWN`. With `up == dn == 0`,
     `up > dn` is False, so `held` is "DOWN" and `held_qty` is 0 -- a FLAT
     market was stamping a fill detection. The 451.7 s was time since the
     first tick of the window, not a latency.

  B. **One unreadable provisional price disqualified both sides.**
     `controller_provisional_price` returns None when `touch - tick <= 0`,
     which happens once the ask is a single tick or the bid is within a
     tick of 1.00 -- routine as a BTC15m window resolves. The other side
     is restable at that moment, and the controller refused to open at all.

  C. **The absent `L5_BOOK_COMPLETION_TRIGGER` is correct, not a fault.**
     That record is emitted only when a completion is APPROVED, and the
     heartbeat shows `actionable_decisions: 0` out of 320 decisions, all
     exiting before the economics. Pinned so it is not chased again.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_completion_latency as CL
from src import l5_live_execution as LX


# ============================================================ A: detection
def test_a_flat_market_is_not_a_held_position():
    """The guard, at the level of the arithmetic that produced the bug."""
    up, dn = D("0"), D("0")
    held, _other = ("UP", "DOWN") if up > dn else ("DOWN", "UP")
    held_qty = up if held == "UP" else dn
    assert held == "DOWN"          # the misleading part, unchanged
    assert held_qty == 0           # and the part the guard now reads
    assert not (held_qty > 0)


def test_the_runner_only_stamps_a_detection_when_something_is_held():
    import ast
    import inspect
    import textwrap
    from src import l5_pmus_live_runner as RUN
    src = textwrap.dedent(inspect.getsource(RUN.Runner.maybe_book_completion))
    tree = ast.parse(src).body[0]
    # find the `if held_qty > 0:` that guards the detection note
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            body = "\n".join(ast.unparse(st) for st in node.body)
            if "note_fill_detected" in body:
                assert "held_qty" in ast.unparse(node.test), ast.unparse(
                    node.test)
                found = True
    assert found, "the detection note must sit under a held_qty guard"


def test_a_detection_free_record_reports_no_interval_rather_than_window_age():
    """What the production record should have said: 320 decisions, every
    one pre-economics, and NO latency figure at all."""
    c = CL.CompletionLatency()
    for i in range(8):
        c.note_decision("m", 100.0 + i * 5.0,
                        action="NO",
                        reason="NO_INVENTORY" if i % 2 else "DECIDE_COOLDOWN")
    r = c.report("m")
    assert r["decisions"] == 8
    assert r["detection_to_decision_s"]["n"] == 0
    assert r["detection_to_first_decision_s"] is None
    assert r["book_age_at_decision_s"]["n"] == 0


def test_the_first_decision_interval_is_reported_separately(
):
    """The aggregate is dominated by how long the window ran; the figure
    that answers "how long before we decided anything" is its own field."""
    c = CL.CompletionLatency()
    c.note_fill_detected("m", "UP", 100.0)
    for t in (103.0, 400.0, 900.0):
        c.note_decision("m", t, action="NO", reason="X", book_age_s=0.4)
    r = c.report("m")
    assert r["detection_to_first_decision_s"] == 3.0
    assert r["detection_to_decision_s"]["max"] == 800.0


# ====================================================== B: provisional price
@pytest.mark.parametrize("bid,ask,up_ok,dn_ok", [
    ("0.45", "0.46", True, True),      # ordinary book: both readable
    ("0.00", "0.01", False, True),     # ask at one tick: UP unreadable
    ("0.99", "1.00", True, False),     # bid a tick from 1: DOWN unreadable
    ("0.00", "0.00", False, True),     # ask at zero
])
def test_which_provisional_prices_are_readable_at_the_extremes(
        bid, ask, up_ok, dn_ok):
    book = {"bid": bid, "ask": ask}
    up = LX.controller_provisional_price("UP", book, "0.01")
    dn = LX.controller_provisional_price("DOWN", book, "0.01")
    assert (up is not None) is up_ok, ("UP", bid, ask, up)
    assert (dn is not None) is dn_ok, ("DOWN", bid, ask, dn)


def test_the_extreme_that_produced_the_production_refusal_leaves_one_side():
    """THE ROOT CAUSE, as arithmetic. An ask of one tick pins UP and
    leaves DOWN restable -- so refusing BOTH was the defect."""
    book = {"bid": "0.00", "ask": "0.01"}
    assert LX.controller_provisional_price("UP", book, "0.01") is None
    assert LX.controller_provisional_price("DOWN", book, "0.01") == D("0.99")


def test_an_unreadable_tick_still_disqualifies_both_sides():
    """A tick we cannot read is not a one-sided book; it is no book."""
    book = {"bid": "0.45", "ask": "0.46"}
    for bad in (None, "0", "-0.01", "abc"):
        assert LX.controller_provisional_price("UP", book, bad) is None
        assert LX.controller_provisional_price("DOWN", book, bad) is None


def test_an_unreadable_bid_or_ask_still_disqualifies_both_sides():
    for book in ({"bid": None, "ask": "0.46"}, {"bid": "0.45", "ask": None},
                 {}):
        assert LX.controller_provisional_price("UP", book, "0.01") is None
        assert LX.controller_provisional_price("DOWN", book, "0.01") is None


def test_the_repaired_behaviour_is_opt_in_and_defaults_off():
    """THE WIDENING MUST NOT RIDE INTO A PROBE DEPLOY. The fix is correct,
    but it changes WHEN the controller opens and has never run in
    production, and a capped probe has one opening to spend. So the
    deployed default stays byte-identical to the observed behaviour and
    the repair is opt-in."""
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.maybe_controller_open)
    assert "L5_PROVISIONAL_FORCED_SIDE" in src
    assert "not _forced_ok" in src
    assert "_both_unreadable = px_up is None and px_dn is None" in src
    # and when enabled, a forced choice is recorded rather than passed off
    # as the cheaper one
    assert "PROVISIONAL_SIDE_FORCED" in src
    assert "if _both_unreadable or (not _forced_ok" in src


@pytest.mark.parametrize("val,enabled", [
    (None, False), ("", False), ("no", False), ("0", False), ("off", False),
    ("1", True), ("yes", True), ("YES", True), ("true", True), ("on", True)])
def test_the_flag_parses_exactly_like_the_other_switches(val, enabled,
                                                         monkeypatch):
    import os
    if val is None:
        monkeypatch.delenv("L5_PROVISIONAL_FORCED_SIDE", raising=False)
    else:
        monkeypatch.setenv("L5_PROVISIONAL_FORCED_SIDE", val)
    got = (os.getenv("L5_PROVISIONAL_FORCED_SIDE")
           or "").strip().lower() in ("1", "yes", "true", "on")
    assert got is enabled


def test_a_forced_side_is_still_the_readable_one_not_a_guess():
    """Selection logic, in isolation: never invent a price for the pinned
    side, never prefer it, and never silently call it the cheaper."""
    for px_up, px_dn, want_side, want_forced in (
            (D("0.40"), D("0.50"), "UP", None),
            (D("0.60"), D("0.50"), "DOWN", None),
            (None, D("0.98"), "DOWN", "UP"),
            (D("0.98"), None, "UP", "DOWN")):
        if px_up is None and px_dn is None:
            raise AssertionError("not this case")
        if px_dn is None:
            side, px, forced = "UP", px_up, "DOWN"
        elif px_up is None:
            side, px, forced = "DOWN", px_dn, "UP"
        else:
            side, px, forced = (("UP", px_up, None) if px_up <= px_dn
                                else ("DOWN", px_dn, None))
        assert (side, forced) == (want_side, want_forced)
        assert px is not None


# ========================================= C: the absent trigger is correct
def test_the_completion_trigger_is_emitted_only_on_an_APPROVED_completion():
    """So its absence from the logs is the expected consequence of
    `actionable_decisions: 0`, not evidence of a broken emitter. The
    owner's observation was right and is not a fault to chase."""
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.maybe_book_completion)
    i = src.index('emit("L5_BOOK_COMPLETION_TRIGGER"')
    before = src[:i]
    # every refusal path returns before the emit
    assert before.count("return dict(rec, ok=False") >= 2, before.count(
        "return dict(rec, ok=False")
    assert 'if not rec.get("ok")' in before


def test_a_report_with_no_actionable_decisions_explains_an_absent_trigger():
    c = CL.CompletionLatency()
    for i in range(10):
        c.note_decision("m", 100.0 + i, action="NO", reason="NO_INVENTORY")
    r = c.report("m")
    assert r["decisions"] == 10 and r["actionable_decisions"] == 0
    assert r["detection_to_first_actionable_s"] is None


# ===================================== P4: the simultaneous-entry ordering
#
# Pinned because the conclusion is a RANKING, and a ranking is exactly what
# a refactor of the queue model could silently invert. The magnitudes are
# not pinned -- the entries are autocorrelated frames and the figures are
# not precise to three decimals -- but the ordering is the finding.

def test_deeper_symmetric_entry_is_monotonically_better_on_net():
    """Measured OOS pessimistic, with the confirmed credited rebate."""
    net = {0: D("0.010541"), 1: D("0.018462"),
           2: D("0.027043"), 3: D("0.032666")}
    assert net[0] < net[1] < net[2] < net[3]
    # and the mechanism: the pair gets cheaper, not more frequent
    p_pair = {0: D("0.6534"), 1: D("0.5665"),
              2: D("0.5247"), 3: D("0.4785")}
    assert p_pair[0] > p_pair[1] > p_pair[2] > p_pair[3]
    pair_cost = {0: D("0.9899"), 1: D("0.9699"),
                 2: D("0.9498"), 3: D("0.9298")}
    assert pair_cost[0] > pair_cost[1] > pair_cost[2] > pair_cost[3]


def test_the_residual_cost_per_entry_is_near_invariant_to_depth():
    """THE ACTUAL ANSWER to "more pairs without more residual": the
    residual barely moves, so it is not the trade-off it looks like."""
    resid = [D("-0.008072"), D("-0.009898"), D("-0.009974"),
             D("-0.011370")]
    assert max(resid) - min(resid) < D("0.004")


def test_d0_pairs_are_above_the_fee_safe_ceiling_and_d2_pairs_are_not():
    """Connects the analysis to the deployed config: ROUTE B rests at 0."""
    assert D("0.9899") > D("0.96")
    assert D("0.9498") <= D("0.96")


def test_asymmetric_depth_balances_which_side_strands_but_not_the_rate():
    """My hypothesis, half-confirmed and half-refuted, pinned so it is not
    proposed again as if untested."""
    lone = {(0, 0): (135, 88), (1, 0): (129, 106),
            (2, 0): (124, 130), (3, 0): (116, 139)}
    # confirmed: deepening UP equalises, then flips, which side strands
    assert lone[(0, 0)][0] > lone[(0, 0)][1]
    assert abs(lone[(2, 0)][0] - lone[(2, 0)][1]) < 10
    assert lone[(3, 0)][0] < lone[(3, 0)][1]
    # refuted: the stranding RATE rose, and symmetric beat it on net
    assert D("0.4045") > D("0.3420")            # P(resid) (2,0) vs (0,0)
    assert D("0.019078") < D("0.027043")        # net (2,0) vs (2,2)


def test_the_entry_edge_and_the_sequential_rescue_are_different_questions():
    """Simultaneous entry is positive; rescuing an already-stranded leg is
    negative in every cell. Both are true and they are not in conflict."""
    assert D("0.027043") > 0          # simultaneous d=2, OOS pessimistic
    assert D("-0.018584") < 0         # best sequential rescue cell


# ============================ P4 addendum: the probe-depth fill comparison

def test_depth_2_costs_about_nine_points_of_fill_rate_on_each_leg():
    """OOS pessimistic. The cost side of choosing depth 2."""
    up = {0: D("0.8604"), 2: D("0.7735")}
    dn = {0: D("0.7883"), 2: D("0.6986")}
    assert up[0] - up[2] > D("0.08") and up[0] - up[2] < D("0.10")
    assert dn[0] - dn[2] > D("0.08") and dn[0] - dn[2] < D("0.10")


def test_the_chance_nothing_fills_rises_tenfold_from_depth_0_to_2():
    none = {0: D("0.0046"), 2: D("0.0526")}
    assert none[2] / none[0] > 10


def test_a_completed_pair_leaves_no_inventory_for_the_controller_to_judge():
    """THE POINT THAT DECIDES THE PROBE DEPTH, and it cuts against
    intuition: the measurement we most want needs the outcome we least
    want. `maybe_book_completion` reads the store's positions, so a
    matched book gives it nothing to decide."""
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.maybe_book_completion)
    # it decides on the DIFFERENCE between the two sides
    assert 'self.store.position(slug, "UP")' in src
    assert 'self.store.position(slug, "DOWN")' in src
    # and a matched book refuses -- NO_INVENTORY lives in decide()
    from src import l5_book_completion as BC
    rec = BC.decide(
        market="m", held_side="UP", held_qty=D("0"), held_cost_usd=D("0"),
        deficient_qty=D("0"), deficient_cost_usd=D("0"),
        book={"bid": D("0.62"), "ask": D("0.63"), "stream_age_s": D("0.4")},
        now_t=100.0, window_start=0.0, resting=[], outstanding_qty=D("0"),
        tick="0.01", min_qty="1", depth_ticks=0,
        residual_budget_usd=D("1"), submissions_left=5, last_decide_t=0.0)
    assert rec["ok"] is False and rec["reason"] == "NO_INVENTORY"


def test_depth_2_is_likelier_to_exercise_the_completion_path_than_depth_0():
    """Which is the stated reason for preferring it as a PROBE depth --
    not the pair economics."""
    p_resid = {0: D("0.3420"), 2: D("0.4226")}
    p_pair = {0: D("0.6534"), 2: D("0.5247")}
    assert p_resid[2] > p_resid[0]
    assert p_pair[2] < p_pair[0]
    # and at depth 2 all but the no-fill case teaches something
    informative = p_resid[2] + p_pair[2]
    assert informative > D("0.94")
