"""The missed-completion record: blocker class, and whether profit was lost.

OWNER 2026-11-02 asked for every missed profitable pairing opportunity to be
instrumented with the actual blocker named. These tests pin the two things
that make such a record evidence rather than a histogram: that a refusal is
classified into the owner's taxonomy without silently landing in a
neighbouring bucket, and that "we could not price the pair" is never
reported as "the pair was not worth having".
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_missed_pair_blocker as M


# ------------------------------------------------------------ classification

@pytest.mark.parametrize("reason,cls", [
    # the owner's taxonomy, one case each
    ("ORDER_ALREADY_RESTING", M.ORDER_CONCURRENCY),
    ("MAX_LIVE_ORDERS", M.ORDER_CONCURRENCY),
    ("ALREADY_RESTING_AT_OR_BETTER", M.REQUOTE_CHURN),
    ("DECIDE_COOLDOWN", M.REQUOTE_CHURN),
    ("PAIR_ABOVE_REDEMPTION", M.PAIR_CEILING),
    ("ECONOMICS:COMPLEMENT_PAIR_ABOVE_CAP", M.PAIR_CEILING),
    ("WORST_PAIR_COMBINATION", M.PAIR_CEILING),
    ("RESIDUAL_ABOVE_CAP", M.RESIDUAL_CAP),
    ("UNMATCHED_CONTRACTS:4>3", M.RESIDUAL_CAP),
    ("NO_SUBMISSIONS_LEFT", M.BUDGET),
    ("CAMPAIGN_ADDITIONAL_LOSS_LIMIT:10>=10", M.BUDGET),
    ("INSUFFICIENT_AVAILABLE_CASH:13>12", M.BUDGET),
    ("ECONOMICS:COMPLEMENT_DEPTH_INSUFFICIENT", M.LIQUIDITY),
    ("WOULD_CROSS", M.LIQUIDITY),
    ("LEG_UNUSABLE", M.LIQUIDITY),
    ("STALE_BOOK", M.DATA_QUALITY),
    ("BOOK_GAP_SUSPECTED", M.DATA_QUALITY),
    # correct refusals
    ("NO_INVENTORY", M.NOT_AN_OPPORTUNITY),
    ("ALREADY_MATCHED", M.NOT_AN_OPPORTUNITY),
    ("PAST_TERMINAL", M.NOT_AN_OPPORTUNITY),
])
def test_each_refusal_lands_in_its_class(reason, cls):
    assert M.classify(reason) == cls


def test_a_residual_refusal_is_not_read_as_a_pair_ceiling_one():
    """ORDERING MATTERS. "RESIDUAL_ABOVE_CAP" contains "ABOVE_CAP", so a
    naive substring pass would file a residual breach under the pair
    ceiling and send the next campaign chasing the wrong limit."""
    assert M.classify("RESIDUAL_ABOVE_CAP") == M.RESIDUAL_CAP
    assert M.classify("COMPLEMENT_PAIR_ABOVE_CAP") == M.PAIR_CEILING


def test_an_unanticipated_reason_is_OTHER_and_keeps_its_name():
    """"or another named refusal". A blocker nobody anticipated must stay
    countable and keep its name; being filed next to a plausible neighbour
    is worse than being unclassified."""
    assert M.classify("SOME_FUTURE_GATE_WE_HAVE_NOT_WRITTEN") == M.OTHER
    rec = M.record(market="m", reason="SOME_FUTURE_GATE_WE_HAVE_NOT_WRITTEN")
    assert rec["blocker_class"] == M.OTHER
    assert rec["reason"] == "SOME_FUTURE_GATE_WE_HAVE_NOT_WRITTEN"


@pytest.mark.parametrize("junk", [None, "", 0, [], {}, object()])
def test_classify_never_raises(junk):
    assert M.classify(junk) in (M.OTHER, M.NOT_AN_OPPORTUNITY)


def test_case_and_whitespace_do_not_change_the_class():
    assert M.classify("  no_inventory  ") == M.NOT_AN_OPPORTUNITY


# ---------------------------------------------------- the tri-state verdict

def test_a_cheap_pair_blocked_by_concurrency_is_a_missed_profitable_pair():
    """THE CASE THE CAMPAIGN IS READ ON. A 0.92 pair redeems at 1.00, so
    giving it up to an occupied order slot is lost profit."""
    r = M.record(market="m", reason="ORDER_ALREADY_RESTING",
                 decision={"pair_own_cost": "0.92"}, entry_ceiling="0.96")
    assert r["missed_profitable_pair"] == "YES"
    assert r["blocker_class"] == M.ORDER_CONCURRENCY
    assert r["pair"]["gain_per_contract_usd"] == "0.08"
    assert r["pair"]["inside_entry_ceiling"] == "YES"


def test_a_pair_above_redemption_is_correctly_NOT_missed_profit():
    """A 1.04 pair is a locked LOSS. Refusing it is the gate working."""
    r = M.record(market="m", reason="PAIR_ABOVE_REDEMPTION",
                 decision={"pair_own_cost": "1.04"}, entry_ceiling="0.96")
    assert r["missed_profitable_pair"] == "NO"
    assert r["pair"]["locks_gain_vs_redemption"] == "NO"


def test_a_pair_between_the_ceiling_and_redemption_is_still_missed_profit():
    """0.98 is above our 0.96 ENTRY discipline but still below redemption,
    so it was genuinely profitable and the two questions differ. Reporting
    it as NO would hide a real cost of the entry ceiling."""
    r = M.record(market="m", reason="ECONOMICS:COMPLEMENT_PAIR_ABOVE_CAP",
                 decision={"pair_own_cost": "0.98"}, entry_ceiling="0.96")
    assert r["missed_profitable_pair"] == "YES"
    assert r["pair"]["locks_gain_vs_redemption"] == "YES"
    assert r["pair"]["inside_entry_ceiling"] == "NO"
    assert r["blocker_class"] == M.PAIR_CEILING


def test_an_unpriced_refusal_is_UNKNOWN_and_never_NO():
    """THE DISTINCTION THIS MODULE EXISTS FOR. A refusal that fires before
    the complement is priced carries no pair cost. "We do not know" and "it
    was not profitable" differ by the whole question."""
    r = M.record(market="m", reason="STALE_BOOK")
    assert r["missed_profitable_pair"] == "UNKNOWN"
    assert r["missed_profitable_pair"] != "NO"
    assert r["pair"]["known"] is False
    assert r["pair"]["locks_gain_vs_redemption"] == "UNKNOWN"


def test_a_correct_refusal_is_NA_not_a_missed_pair():
    """Counting NO_INVENTORY as missed profit would manufacture a problem."""
    for reason in ("NO_INVENTORY", "ALREADY_MATCHED", "PAST_TERMINAL"):
        r = M.record(market="m", reason=reason)
        assert r["missed_profitable_pair"] == "N/A", reason


def test_not_an_opportunity_stays_NA_even_with_a_cheap_pair_attached():
    """The class decides first. There is nothing to complete, so a price
    carried alongside is not an opportunity that was given up."""
    r = M.record(market="m", reason="ALREADY_MATCHED",
                 decision={"pair_own_cost": "0.80"})
    assert r["missed_profitable_pair"] == "N/A"


@pytest.mark.parametrize("bad", [None, "", "0", "-0.5", "abc", "NaN", "Inf"])
def test_an_unusable_pair_cost_is_not_silently_a_zero(bad):
    v = M.pair_verdict(pair_own_cost=bad)
    assert v["known"] is False
    assert v["pair_own_cost"] is None
    assert v["gain_per_contract_usd"] is None


def test_an_absent_entry_ceiling_is_UNKNOWN_not_a_pass():
    v = M.pair_verdict(pair_own_cost="0.92", entry_ceiling=None)
    assert v["known"] is True
    assert v["inside_entry_ceiling"] == "UNKNOWN"
    assert v["locks_gain_vs_redemption"] == "YES"


def test_the_redemption_line_is_exactly_one_dollar():
    assert M.REDEMPTION == D("1.00")
    assert M.pair_verdict(
        pair_own_cost="1.00")["locks_gain_vs_redemption"] == "NO"
    assert M.pair_verdict(
        pair_own_cost="0.9999")["locks_gain_vs_redemption"] == "YES"


# ------------------------------------------------------------- the record

def test_the_record_is_json_safe_and_carries_no_decimal():
    r = M.record(market="cpc-btc-updown-15m-2026-11-02-1200z",
                 reason="ORDER_ALREADY_RESTING",
                 decision={"pair_own_cost": D("0.92"),
                           "locked_pnl_usd": D("0.1840"),
                           "need": {"side": "DOWN", "qty": D("2")}},
                 entry_ceiling=D("0.96"),
                 held_side="UP", held_qty=D("2.05"),
                 deficient_qty=D("0"))
    s = json.dumps(r, default=str)
    assert "Decimal" not in s, s
    assert r["locked_pnl_usd"] == "0.1840"
    assert r["inventory"]["held_qty"] == "2.05"
    assert r["stage"] == "DECISION"


def test_the_record_never_raises_and_reports_its_own_failure():
    class Hostile:
        def __str__(self):               # noqa: D105
            raise RuntimeError("boom")

    r = M.record(market=Hostile(), reason="ORDER_ALREADY_RESTING")
    assert r["label"] == "L5_MISSED_PAIR"
    assert r.get("captured") is False
    assert "RuntimeError" in r.get("error", "")


def test_a_hostile_decision_mapping_cannot_break_the_record():
    class BadMap(dict):
        def get(self, *a, **k):          # noqa: D102
            raise RuntimeError("nope")

    r = M.record(market="m", reason="STALE_BOOK", decision=BadMap())
    assert r["label"] == "L5_MISSED_PAIR"


def test_it_decides_nothing_and_holds_no_limit():
    """The module must not be a second author of any rule: the entry ceiling
    is passed IN, never kept here, so it cannot disagree with the gate that
    actually refuses."""
    import ast
    import inspect

    src = inspect.getsource(M)
    # the ceiling must arrive as a PARAMETER, never be stored here
    assert "entry_ceiling" in inspect.signature(M.pair_verdict).parameters
    assert "entry_ceiling" in inspect.signature(M.record).parameters

    # no module-level numeric constant other than the redemption value.
    # Checked on the parsed tree, not on the text: the docstring explains
    # the 0.96 entry ceiling and an earlier version of this test failed on
    # its own prose, which proved the text scan was measuring the wrong
    # thing.
    tree = ast.parse(src)
    numeric = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and isinstance(
                        node.value, ast.Call):
                    numeric[t.id] = ast.unparse(node.value)
    assert set(numeric) == {"REDEMPTION"}, numeric
    assert numeric["REDEMPTION"] == 'D(\'1.00\')', numeric

    # and it cannot reach the venue or the clock
    for forbidden in ("def submit", "import requests", "import time",
                      "datetime", "open("):
        assert forbidden not in src, forbidden


def test_the_label_is_stable_so_the_tape_can_be_queried():
    assert M.record(market="m", reason="X")["label"] == "L5_MISSED_PAIR"


# ===================================================== THE WIRING ITSELF
#
# The classifier is useless unwired, and the gap it closes was precisely a
# refusal path that emitted nothing. These pin that both refusal returns in
# `maybe_book_completion` now describe themselves, and that the describing
# cannot alter or break the refusal.

def _runner_src():
    import inspect
    from src import l5_pmus_live_runner as R
    return inspect.getsource(R.Runner.maybe_book_completion)


def test_both_refusal_returns_are_instrumented():
    """THE TWO PATHS THAT PREVIOUSLY EMITTED NOTHING.

    `if not rec.get("ok"): return rec` is the decision-level refusal, and
    the `resting` branch is the runner's own post-decision one. A refusal
    that returns without a record is the whole defect.
    """
    src = _runner_src()
    assert src.count("_note_missed(") >= 3, src          # def + two calls
    body = src.split("_note_missed")[0]
    # the generic refusal return is preceded by the note
    assert 'if not rec.get("ok"):\n            _note_missed(' in src, src
    assert '_note_missed("ORDER_ALREADY_RESTING")' in src, src


def test_no_refusal_return_remains_silent():
    """EVERY return that hands back a refusal must describe itself first.

    Checked structurally so a future refusal added to this method cannot
    quietly reintroduce the gap this module closes.

    TWO FORMS OF INSTRUMENTATION COUNT, and the distinction is the method's
    actual shape rather than a convenience. `_note_missed` covers the
    refusals; the method's FINAL `return rec` is the SUCCESS path, where the
    decision was ok and placement was attempted, and it is already paired
    with its outcome by `L5_BOOK_COMPLETION_RESULT`. An earlier version of
    this test treated every bare `return rec` as a refusal and flagged that
    success return -- the heuristic was wrong, not the code, and the fix is
    to recognise both records rather than to loosen the guard.
    """
    src = _runner_src()
    lines = src.splitlines()
    silent = []
    for i, ln in enumerate(lines):
        t = ln.strip()
        is_return = (t == "return rec"
                     or t.startswith("return dict(rec, ok=False"))
        if not is_return:
            continue
        window = "\n".join(lines[max(0, i - 25):i])
        if ("_note_missed" not in window
                and "L5_BOOK_COMPLETION_RESULT" not in window):
            silent.append((i + 1, t))
    assert not silent, silent


def test_the_success_return_is_the_one_covered_by_the_other_record():
    """Pins the division of labour the test above relies on, so that if the
    success path ever stops emitting its pairing record this fails here
    rather than silently satisfying the guard above."""
    src = _runner_src()
    assert "L5_BOOK_COMPLETION_RESULT" in src
    tail = src.split("L5_BOOK_COMPLETION_RESULT")[-1]
    assert tail.strip().endswith("return rec"), tail[-200:]


def test_the_emit_is_wrapped_so_it_cannot_break_the_money_path():
    """A failure to DESCRIBE a refusal must never change the refusal."""
    src = _runner_src()
    note = src.split("def _note_missed")[1].split("if not rec.get")[0]
    assert "try:" in note
    assert "except BaseException" in note
    assert "missed_pair_emit_failed" in note
    # and it must not be able to swallow a shutdown
    assert "KeyboardInterrupt, SystemExit" in note


def test_the_ceiling_is_passed_in_and_not_read_by_the_classifier():
    """So the record cannot disagree with the gate that actually refuses."""
    src = _runner_src()
    assert "max_pair_cost()" in src
    assert "entry_ceiling=_ceil" in src


def test_an_unreadable_ceiling_does_not_prevent_the_record():
    """The pair ceiling is a nicety; the BLOCKER is the point. Losing the
    ceiling must downgrade `inside_entry_ceiling` to UNKNOWN, not drop the
    record that something was refused."""
    r = M.record(market="m", reason="ORDER_ALREADY_RESTING",
                 decision={"pair_own_cost": "0.92"}, entry_ceiling=None)
    assert r["missed_profitable_pair"] == "YES"
    assert r["pair"]["inside_entry_ceiling"] == "UNKNOWN"


def test_the_runner_imports_it_so_the_image_closure_covers_it():
    """The Dockerfile COPY and the import must arrive together: the image
    must be EXACTLY the import closure, both directions, so a COPY without
    an import fails the closure test and an import without a COPY fails the
    boot."""
    import inspect
    from src import l5_pmus_live_runner as R
    assert "l5_missed_pair_blocker" in inspect.getsource(R)
    df = open("deploy/l5_pmus_live/Dockerfile").read()
    assert "COPY src/l5_missed_pair_blocker.py" in df


# ===================================================================
# THE WINDOW'S DEADLINES ARE NOT BLOCKERS.
#
# Observed live on 1915z, 2026-11-02, from the OPENING layer:
#
#   reason PAST_ENTRY_CUTOFF   blocker_class OTHER
#   pair_own_cost 0.9500  inside_entry_ceiling YES
#   missed_profitable_pair YES
#
# The pair was real and the engine was right to decline it -- the entry
# window had closed. `record` scores "N/A" only for
# NOT_AN_OPPORTUNITY, so an unmapped deadline fell through to OTHER and
# published a correct refusal as a missed one. At the OPENING layer the
# clock refuses far more often than any limit, so this does not merely
# add noise, it dominates the metric.
# ===================================================================

DEADLINES = ("PAST_ENTRY_CUTOFF", "PAST_COMPLETE_CUTOFF",
             "PAST_PLACE_CUTOFF", "WINDOW_NOT_OPEN", "BEFORE_PLACE_FLOOR")


def test_every_window_deadline_is_not_an_opportunity():
    for reason in DEADLINES + ("PAST_TERMINAL",):
        assert M.classify(reason) == M.NOT_AN_OPPORTUNITY, reason


def test_a_past_cutoff_refusal_is_not_a_missed_profitable_pair():
    """The exact live record, which used to read YES."""
    for reason in DEADLINES:
        rec = M.record(market="cpc-btc-updown-15m-2026-11-02-1915z",
                         reason=reason,
                         decision={"pair_own_cost": "0.9500"},
                         entry_ceiling=D("0.96"), stage="OPENING")
        assert rec["blocker_class"] == M.NOT_AN_OPPORTUNITY, reason
        assert rec["missed_profitable_pair"] == "N/A", reason
        # the pair is still PRICED -- the record stays informative, it
        # just stops claiming the refusal was a mistake
        assert rec["pair"]["pair_own_cost"] == "0.9500", reason
        assert rec["pair"]["inside_entry_ceiling"] == "YES", reason


def test_a_real_blocker_on_the_same_pair_still_reads_YES():
    """The fix must not silence genuine blockers."""
    for reason in ("ORDER_ALREADY_RESTING", "PAIR_ABOVE_REDEMPTION",
                   "NO_SUBMISSIONS_LEFT"):
        rec = M.record(market="m", reason=reason,
                         decision={"pair_own_cost": "0.9500"},
                         entry_ceiling=D("0.96"), stage="OPENING")
        assert rec["blocker_class"] != M.NOT_AN_OPPORTUNITY, reason
        assert rec["missed_profitable_pair"] == "YES", reason
