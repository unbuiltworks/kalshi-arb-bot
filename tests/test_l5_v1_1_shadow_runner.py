"""The prospective loop: what it records, what it refuses to guess, and the
one number that is preregistered.
"""
from __future__ import annotations

import json
import re

import pytest

from src import l5_v1_1_shadow as V11
from src import l5_v1_1_shadow_runner as R
from src import pmus_l5_fidelity_live as F
from src import pmus_l5_persistence_v2 as pv2
from src.l5_v1_shadow_runner import ShadowBook

pytestmark = pytest.mark.real_mandate_env

BOOKS = {"a": {"bid": 0.34, "ask": 0.36, "bid_qty": 50, "ask_qty": 40},
         "b": {"bid": 0.61, "ask": 0.63, "bid_qty": 12, "ask_qty": 9}}


@pytest.fixture(scope="module")
def loaded():
    return pv2.load_v2(), F.load(), V11.load_act_artifact()


def test_the_act_horizon_is_preregistered_at_ten_seconds(loaded):
    """H=10s was fixed from the chronological OOS BEFORE this run started.
    Re-choosing it from prospective data is the tuning the owner ruled out
    while the first sample is being collected."""
    assert R.HORIZON_S == 10
    art = loaded[2]
    assert art, "act artifact must ship"
    assert art["act"]["10"]["threshold_from_train_only"] is not None


def test_whether_l5_acted_is_emitted_as_unknown_rather_than_guessed(loaded):
    """L5 trades Polymarket.com; this tape is Polymarket US sports. The honest
    value is null WITH a reason, never a fabricated label and never silence."""
    model, art, act = loaded
    rows = R.cycle(model, art, act, BOOKS, ShadowBook(),
                   V11.SequentialContext(), 1000.0)
    assert rows and all(r["l5_acted_subsequently"] is None for r in rows)
    assert all(r["l5_observability"].startswith("not_observable:") for r in rows)
    assert "Polymarket.com" in R.L5_NOT_OBSERVABLE


def test_every_evaluation_emits_a_row_including_the_waits(loaded):
    model, art, act = loaded
    rows = R.cycle(model, art, act, BOOKS, ShadowBook(),
                   V11.SequentialContext(), 1000.0)
    assert len(rows) == len(BOOKS)
    for r in rows:
        assert r["action"] in ("PROPOSE", "WAIT")
        assert r["act_decision"] in ("ACT", "WAIT")
        if r["action"] == "WAIT":
            assert r["wait_reason"], "a WAIT with no reason is unattributable"


def test_the_universe_snapshot_records_observations_not_decisions(tmp_path):
    """Entry-selection groundwork. It must not invent the negative it exists
    to make constructible later."""
    sink = tmp_path / "universe.jsonl"
    n = R.persist_universe(sink, {"t": "2026-09-16T13:05:00Z"}, BOOKS, 1000.0)
    rows = [json.loads(l) for l in sink.read_text().splitlines()]
    assert n == len(rows) == len(BOOKS)
    for row in rows:
        assert row["schema"] == "l5_universe_snapshot/v1"
        assert row["observed_utc"] == 1000.0
        assert row["recorder_status_utc"] == "2026-09-16T13:05:00Z"
        assert row["source"] and "NOT an inferred negative" in row["labels"]
        for invented in ("entered", "declined", "label", "l5_entered"):
            assert invented not in row, invented


def test_the_universe_snapshot_appends_rather_than_replaces(tmp_path):
    sink = tmp_path / "universe.jsonl"
    R.persist_universe(sink, {"t": "x"}, BOOKS, 1000.0)
    R.persist_universe(sink, {"t": "y"}, BOOKS, 1020.0)
    assert len(sink.read_text().splitlines()) == 2 * len(BOOKS)


def test_the_runner_cannot_trade():
    src = open(R.__file__, encoding="utf-8").read()
    code = re.sub(r'"""(?:.|\n)*?"""', "", src)
    for bad in ("place_limit_buy", "place_order", "submit_order", "Transport",
                "PMUS_API_KEY_ID", "has_credentials", "/v1/orders", "POST"):
        assert bad not in code, bad
    assert "urlopen" not in code          # only the V1 runner's GET handoff


def test_a_theoretical_fill_is_labelled_where_it_is_created(loaded):
    """Inventory has to move or the trajectory claim is vacuous, but a walked
    position is never a filled one."""
    model, art, act = loaded
    sb = ShadowBook()
    R.cycle(model, art, act, BOOKS, sb, V11.SequentialContext(), 1000.0)
    src = open(R.__file__, encoding="utf-8").read()
    assert "THEORETICAL" in src and "never called P&L" in src
    assert all(isinstance(v, float) for v in sb.spent.values())


def test_context_advances_only_on_a_decided_action(loaded):
    """A market we WAIT on has no previous action, so its lookup must stay at
    the frozen defaults rather than inheriting another market's."""
    model, art, act = loaded
    sb, ctx = ShadowBook(), V11.SequentialContext()
    for i in range(3):
        rows = R.cycle(model, art, act, BOOKS, sb, ctx, 1000.0 + 20 * i)
    for r in rows:
        if r["action"] == "WAIT" and ctx.since(r["slug"], 2000.0) is None:
            assert (r["last_rel"], r["last_size"]) == ("open", "q<5")


# --------------------------------------------------------------------------
# TTE: captured, and deliberately not used
#
# The venue serves no settlement timestamp for live sports and the nearest
# thing, market_state.endDate, reaches only the bounded spring_watch subset.
# Feeding it now would give a MIXED tte population, part measured and part
# sentinel, which is worse for a comparison than a uniform sentinel.

SW_STATUS = {"t": "2026-09-16T13:30:00Z",
             "spring_watch": [{"slug": "sw", "bid": 0.44, "ask": 0.46,
                               "market_state": {"endDate": "2026-09-16T15:00:00Z"}}],
             "top_markets": [{"slug": "tm", "bid": 0.61, "ask": 0.63}]}


def test_end_date_is_read_only_where_the_recorder_actually_supplies_it():
    ms = R.market_states(SW_STATUS)
    assert set(ms) == {"sw"}, "only spring_watch rows carry market_state"
    assert R.end_date_tte_s(ms["sw"], 1758030000.0) is not None
    assert R.end_date_tte_s(ms.get("tm"), 1758030000.0) is None


def test_an_absent_or_unparseable_end_date_is_none_never_a_guess():
    for bad in (None, {}, {"endDate": None}, {"endDate": ""},
                {"endDate": "not-a-date"}, {"endDate": 12345}):
        assert R.end_date_tte_s(bad, 1000.0) is None


def test_the_captured_tte_never_reaches_the_state_key(loaded):
    """The sample must run on the uniform sentinel. A tte that moved the state
    key would silently change the semantics of a sample already in flight."""
    model, art, act = loaded
    books = {"sw": {"bid": 0.44, "ask": 0.46}, "tm": {"bid": 0.61, "ask": 0.63}}
    ms = R.market_states(SW_STATUS)
    rows = R.cycle(model, art, act, books, ShadowBook(),
                   V11.SequentialContext(), 1758030000.0, ms)
    by = {r["slug"]: r for r in rows}
    assert by["sw"]["observed_end_date_tte_s"] is not None
    assert by["tm"]["observed_end_date_tte_s"] is None
    # both markets keyed on the SAME sentinel bucket regardless
    assert by["sw"]["state_l5_units"].split("|")[1] == "tte>10m"
    assert by["tm"]["state_l5_units"].split("|")[1] == "tte>10m"
    assert "NOT fed to the state key" in by["sw"]["tte_provenance"]


def test_the_v1_control_runner_is_not_touched_by_the_tte_capture():
    """Widening books_from_status would change what the control sees AND
    rebuild it mid-sample. The extraction lives on the V1.1 side for exactly
    that reason."""
    import inspect

    from src import l5_v1_shadow_runner as V1R
    assert "market_state" not in inspect.getsource(V1R.books_from_status)
    assert "market_state" in inspect.getsource(R.market_states)


def test_the_cycle_line_carries_its_own_clock_and_market_count():
    """The maturity reader must not have to infer a clock from the log
    transport, and distinct markets is a coverage floor."""
    src = open(R.__file__, encoding="utf-8").read()
    assert "t=now" in src and "distinct_markets=len(seen)" in src
