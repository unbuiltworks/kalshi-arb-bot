"""Durable live status: readable WHILE the cohort runs, and inert.

COHORT-1 ran 57 minutes and the first thing anyone could say about window 4
was said when the job ended and committed its artifacts. When the owner asked
mid-window why Gemini showed no resting orders, the honest answer was that the
window's own record did not exist yet.

The two things every test here is really about:

  1. the status is on DISK, whole, after each cycle -- not at the end;
  2. it cannot change what the window does. It is fed rows the
     `DecisionObserver` has already completed, it makes no venue read, and a
     writer that raises is swallowed.
"""
import ast
import asyncio
import json
import os

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import live_envelope as LE
from src.gemini_execution import live_probe_spec as SPEC
from src.gemini_execution import live_status as LS
from src.gemini_execution import run_continuous as RC
from src.gemini_execution import ws_continuous as WC

SYMBOL = "GEMI-BTC15M2609220230-UP"

BOOK = {"readable": True, "identity_consistent": True, "source": "contract",
        "yes": {"bid": "0.35", "ask": "0.42"},
        "no": {"bid": "0.58", "ask": "0.65"},
        "passive_pair_cost": "0.93"}


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop(
    ).run_until_complete(coro)


@pytest.fixture(autouse=True)
def _no_bound_market_leak():
    """`_BOUND_MARKETS` is PROCESS-GLOBAL -- it is what enforces one market at
    a time. A test that binds and does not unbind leaves the next test's
    driver refusing on a market this file bound, which is a test-ordering bug
    that looks exactly like a real concurrency refusal."""
    saved = dict(WC._BOUND_MARKETS)
    try:
        yield
    finally:
        WC._BOUND_MARKETS.clear()
        WC._BOUND_MARKETS.update(saved)


def _driver(tmp_path, *, bind=True, **kw):
    """A driver taken through the REAL recover/bind gates, so `decide` builds
    a market state instead of refusing with "decide() before bind()"."""
    d = WC.Driver("OWNER-TEST", SYMBOL,
                  ledger_path=str(tmp_path / "ledger.jsonl"),
                  max_actions=12,
                  envelope=SPEC.BTC15M_Q1_CAP4_W60_880.envelope_obj,
                  quote_band=(60.0, 880.0), **kw)
    if bind:
        WC._BOUND_MARKETS.clear()
        rec = d.recover(venue_fills=[], venue_open=[], venue_orders=[])
        assert rec.get("recovered"), rec
        assert d.bind().get("bound"), d.why
    return d


def _observed(tmp_path, driver):
    """The PRODUCTION seam: a real observer, a real sink, a real writer."""
    w = LS.StatusWriter(str(tmp_path / "status.json"),
                        cohort={"cohort_id": "COHORT-TEST-W01"})
    obs = RC.DecisionObserver(sink=LS.sink_for(w))

    async def _refresh():
        return {}

    obs.attach(driver, _refresh)
    w.begin_window(window_index=1, symbol=SYMBOL, cohort_id="COHORT-TEST-W01")
    return w, obs


# ================================== the file exists DURING the window

def test_the_status_is_on_disk_after_the_very_first_decision(tmp_path):
    d = _driver(tmp_path)
    w, obs = _observed(tmp_path, d)
    d.decide(700.0, book=BOOK)

    assert os.path.exists(w.path), "nothing to read while the window runs"
    snap = json.loads(open(w.path).read())
    assert snap["label"] == LS.LABEL
    assert snap["window"]["symbol"] == SYMBOL
    assert snap["window"]["window_index"] == 1
    assert snap["cycle"]["cycle"] == 1
    assert snap["cycle"]["tte_s"] == 700.0
    assert len(obs.cycles) == 1


def test_every_field_the_owner_asked_for_is_present(tmp_path):
    d = _driver(tmp_path)
    w, _ = _observed(tmp_path, d)
    d.decide(700.0, book=BOOK)
    c = json.loads(open(w.path).read())["cycle"]

    assert c["yes"]["bid"] == "0.35" and c["yes"]["ask"] == "0.42"
    assert c["no"]["bid"] == "0.58" and c["no"]["ask"] == "0.65"
    for field in ("cycle", "tte_s", "desired", "resting", "n_resting",
                  "actions_used", "actions_of", "actions", "held_by"):
        assert field in c, field
    inv = c["inventory"]
    for field in ("yes_qty", "no_qty", "n_fills", "matched_qty",
                  "effective_matched_pair_cost", "directional_residual",
                  "gross_fill_spend_usd"):
        assert field in inv, field
    assert c["actions_of"] == 12
    assert "TURNOVER" in inv["labels"]


def test_the_history_keeps_every_cycle_not_just_the_last(tmp_path):
    d = _driver(tmp_path)
    w, _ = _observed(tmp_path, d)
    for tte in (800.0, 700.0, 600.0):
        d.decide(tte, book=BOOK)

    rows = [json.loads(l) for l in open(w.history_path) if l.strip()]
    cycles = [r["cycle"].get("cycle") for r in rows if r["cycle"]]
    assert cycles[-3:] == [1, 2, 3]
    assert json.loads(open(w.path).read())["cycle"]["cycle"] == 3, (
        "the snapshot is the LATEST cycle")


def test_the_snapshot_is_always_a_whole_document(tmp_path):
    """Replaced atomically, so a reader tailing it never sees half a write."""
    d = _driver(tmp_path)
    w, _ = _observed(tmp_path, d)
    for tte in (800.0, 700.0):
        d.decide(tte, book=BOOK)
        json.loads(open(w.path).read())          # raises if torn
    src = ast.parse(open("src/gemini_execution/live_status.py").read())
    fn = next(n for n in ast.walk(src)
              if isinstance(n, ast.FunctionDef) and n.name == "_write")
    called = {n.func.attr for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert {"replace", "fsync"} <= called


# ================================== it cannot change the window

def test_a_status_writer_that_explodes_never_reaches_the_window(tmp_path):
    """A recorder that can stop a live window is worse than no recorder."""
    d = _driver(tmp_path)

    def _boom(kind, row):
        raise RuntimeError("the status disk is full")

    obs = RC.DecisionObserver(sink=_boom)

    async def _refresh():
        return {}

    obs.attach(d, _refresh)
    out = d.decide(700.0, book=BOOK)             # must not raise

    assert out is not None
    assert len(obs.cycles) == 1, "the row is recorded before the sink runs"
    assert any("the status disk is full" in e for e in obs.errors)


def test_an_unwritable_path_is_recorded_and_never_raised(tmp_path):
    w = LS.StatusWriter(str(tmp_path / "nope" / "\0bad" / "s.json"))
    w.begin_window(window_index=1, symbol=SYMBOL)
    w.observe_cycle({"cycle": 1})
    assert w.errors, "a failed write must be visible"
    assert w.snapshot()["window"]["cycles_seen"] == 1


def test_the_window_report_is_identical_with_and_without_a_sink(tmp_path):
    a, b = _driver(tmp_path / "a"), _driver(tmp_path / "b")
    plain = RC.DecisionObserver()
    w = LS.StatusWriter(str(tmp_path / "s.json"))
    watched = RC.DecisionObserver(sink=LS.sink_for(w))

    async def _refresh():
        return {}

    plain.attach(a, _refresh)
    watched.attach(b, _refresh)
    for tte in (800.0, 700.0):
        a.decide(tte, book=BOOK)
        b.decide(tte, book=BOOK)

    # WALL-CLOCK FIELDS DIFFER BECAUSE THE TWO RUNS HAPPENED AT DIFFERENT
    # INSTANTS, not because the sink changed anything. Everything the
    # experiment turns on -- the decision, its classification, its holds and
    # the plan it produced -- must be identical.
    keys = ("cycle", "tte_s", "state", "window_phase", "why", "actions",
            "classification", "holds", "held_by", "plan",
            "inventory_before_decision", "working_orders_before",
            "budget_before_decision", "budget_after_decision",
            "unreadable_hold")

    def _strip(rows):
        return [{k: r.get(k) for k in keys} for r in rows]

    assert _strip(plain.cycles) == _strip(watched.cycles)
    assert not plain.errors and not watched.errors


def test_the_status_path_makes_no_venue_read_of_its_own(tmp_path):
    """It costs no maker resting time, because it asks the venue nothing."""
    src = ast.parse(open("src/gemini_execution/live_status.py").read())
    names = {n.id for n in ast.walk(src) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(src) if isinstance(n, ast.Attribute)}
    for forbidden in ("outcome_book", "contract_depth", "positions",
                      "public_flow", "resolution_for", "aiohttp", "requests",
                      "urlopen", "place", "cancel"):
        assert forbidden not in names, forbidden
    imported = {a.name.split(".")[0]
                for n in ast.walk(src) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {(n.module or "").split(".")[0]
                 for n in ast.walk(src) if isinstance(n, ast.ImportFrom)}
    assert imported <= {"json", "os", "tempfile", "datetime", "typing",
                        "__future__"}, imported


def test_the_sink_is_called_only_after_a_row_is_complete(tmp_path):
    """It is downstream of the append, so it can never sit between a decision
    and the order that decision asked for."""
    src = ast.parse(open("src/gemini_execution/run_continuous.py").read())
    for meth, listname in (("_record_decision", "cycles"),
                           ("_record_mutation", "mutations")):
        fn = next(n for n in ast.walk(src)
                  if isinstance(n, ast.FunctionDef) and n.name == meth)
        body = fn.body
        append_at = next(i for i, n in enumerate(body)
                         if "append" in ast.dump(n) and listname in ast.dump(n))
        emit_at = next(i for i, n in enumerate(body) if "_emit" in ast.dump(n))
        assert emit_at > append_at, meth


# ================================== labels are not relaxed here

def test_nothing_in_the_status_is_ever_called_realized(tmp_path):
    w = LS.StatusWriter(str(tmp_path / "s.json"))
    w.begin_window(window_index=1, symbol=SYMBOL)
    assert w.snapshot()["window"]["settlement_implied_net_usd"] is None
    w.end_window(stop_reason="end of quoting interval",
                 settlement_implied_net_usd=None,
                 economics={"state": "PENDING_SETTLEMENT"})
    snap = w.snapshot()
    assert snap["window"]["settlement_implied_net_usd"] is None, (
        "an unresolved contract's payout is never guessed")
    assert "REALIZED" in snap["labels"]
    assert "attributable cash movement" in snap["labels"]
    src = open("src/gemini_execution/live_status.py").read()
    assert '"realized_net_usd"' not in src or "null" in src


def test_a_refused_book_is_shown_as_refused_not_as_a_spread(tmp_path):
    d = _driver(tmp_path)
    w, _ = _observed(tmp_path, d)
    crossed = dict(BOOK, yes={"bid": "0.55", "ask": "0.42"})
    d.decide(700.0, book=crossed)
    c = json.loads(open(w.path).read())["cycle"]
    assert c["book_readable"] is False
    assert c["book_why"]
    assert c["yes"]["spread"] is None, "no spread is derived from a refusal"
    assert c["desired"] == [], "nothing is quoted into an unreadable book"


def test_render_is_one_line_and_never_feeds_a_decision(tmp_path):
    d = _driver(tmp_path)
    w, _ = _observed(tmp_path, d)
    d.decide(700.0, book=BOOK)
    line = LS.render(json.loads(open(w.path).read()))
    assert "\n" not in line and SYMBOL[-13:] in line
    src = ast.parse(open("src/gemini_execution/live_status.py").read())
    callers = [n for n in ast.walk(src)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "render"]
    assert not callers, "render is for a human tailing a log, nothing else"
