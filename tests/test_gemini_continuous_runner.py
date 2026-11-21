"""The continuous lifecycle: bounds that only tighten, and a rollover gate
that demands positive evidence.

CONTINUOUS-5 stopped its controller and left two maker orders resting; one
filled 57 seconds later and settled against us. A continuous system that
rolls forward without proving the previous window is flat would do that on a
loop, so the gate is the load-bearing part and these tests are about what it
refuses.
"""
import inspect

import pytest

from src.gemini_execution import continuous_runner as CR


# ============================================== bounds tighten or stop, never grow

def test_the_budget_cannot_be_mutated_at_all():
    """A budget that can raise itself is not a budget."""
    b = CR.RiskBudget()
    with pytest.raises(Exception):
        b.max_windows = 999                                    # frozen
    with pytest.raises(Exception):
        b.max_total_spend_usd = "1000"


def test_no_method_on_the_budget_widens_a_bound():
    """Structural: the only public callable is `labels`. A `grant`, `raise`
    or `extend` appearing here later is the thing to catch."""
    public = [n for n, _ in inspect.getmembers(CR.RiskBudget, callable)
              if not n.startswith("_")]
    assert public == ["labels"], public


def test_the_per_window_envelope_is_the_C8B_ONE_unchanged():
    """Continuous operation multiplies WINDOWS, never size."""
    b = CR.RiskBudget()
    assert b.max_spend_per_window_usd == "0.15"
    assert b.action_size == "0.01"
    assert b.max_residual_units == "0.04"
    assert b.max_imbalance_units == "0.04"
    assert b.max_concurrent_markets == 1


@pytest.mark.parametrize("field,value,state", [
    ("windows_started", 12, CR.RISK_BUDGET_EXHAUSTED),
    ("total_spend_usd", CR.D("1.00"), CR.RISK_BUDGET_EXHAUSTED),
    ("realized_usd", CR.D("-0.25"), CR.DAILY_LOSS_STOP),
])
def test_each_bound_refuses_the_next_window_and_names_itself(field, value,
                                                            state):
    b, st = CR.RiskBudget(), CR.RunState()
    setattr(st, field, value)
    got = CR.admits_another_window(b, st, now=st.started_at)
    assert got["admits"] is False and got["state"] == state
    assert got["why"]


def test_a_healthy_run_is_admitted():
    got = CR.admits_another_window(CR.RiskBudget(), CR.RunState())
    assert got["admits"] is True


def test_the_run_clock_stops_it_rather_than_retrying():
    b = CR.RiskBudget(max_run_seconds=60.0)
    st = CR.RunState(started_at=1000.0)
    assert CR.admits_another_window(b, st, now=1061.0)["admits"] is False


def test_a_stopped_run_stays_stopped():
    st = CR.RunState(stopped=CR.UNKNOWN_TERMINAL, why="an order vanished")
    got = CR.admits_another_window(CR.RiskBudget(), st)
    assert got["admits"] is False and got["state"] == CR.UNKNOWN_TERMINAL


# ==================================================== the rollover gate

def _ok_state(**kw):
    base = {"open_orders": [], "executions": {"readable": True,
                                              "contradictions": []}}
    base.update(kw)
    return base


def test_a_clear_readable_book_rolls_over():
    got = CR.rollover_safe("SYM", _ok_state())
    assert got["safe"] is True


def test_an_UNREADABLE_open_order_list_is_not_an_empty_one():
    """The single most important refusal in this file."""
    got = CR.rollover_safe("SYM", _ok_state(open_orders=None))
    assert got["safe"] is False and got["state"] == CR.STATE_UNREADABLE
    assert "not an empty one" in got["why"]


def test_a_live_strategy_order_blocks_the_rollover():
    """C5's shape: the controller stopped, the order did not."""
    st = _ok_state(open_orders=[{"clientOrderId": "l5cp-owner-2026-no-x",
                                 "price": "0.46"}])
    got = CR.rollover_safe("SYM", st, our_ids=["l5cp-owner-2026-no-x"])
    assert got["safe"] is False
    assert got["state"] == CR.ORDERS_LIVE_AFTER_STOP
    assert "still executable" in got["why"]


def test_an_unknown_resting_order_also_blocks_when_we_cannot_name_ours():
    """With no id list to match against, ANY resting order blocks. Assuming
    a stranger's order is a stranger's is exactly the guess that costs."""
    st = _ok_state(open_orders=[{"orderId": 1}])
    got = CR.rollover_safe("SYM", st, our_ids=[])
    assert got["safe"] is False and got["state"] == CR.ORDERS_LIVE_AFTER_STOP


def test_a_contradictory_execution_view_blocks_the_rollover():
    st = _ok_state(executions={"readable": False,
                               "contradictions": [{"kind": "Q"}]})
    got = CR.rollover_safe("SYM", st)
    assert got["safe"] is False
    assert got["state"] == CR.EXECUTION_CONTRADICTION


def test_an_unreadable_execution_view_blocks_the_rollover():
    st = _ok_state(executions={"readable": False, "contradictions": [],
                               "why": "filled history UNREADABLE"})
    got = CR.rollover_safe("SYM", st)
    assert got["safe"] is False and got["state"] == CR.STATE_UNREADABLE


# ========================================================= modes and evidence

def test_shadow_is_the_default_and_live_must_be_asked_for():
    assert CR.ContinuousRunner().mode == CR.SHADOW


def test_an_unknown_mode_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError):
        CR.ContinuousRunner(mode="probably_fine")


def test_every_failure_state_is_terminal_for_the_RUN():
    """There is no retry loop. Each of these ends the run and says why."""
    for s in (CR.ENTRY_BAND_UNREACHABLE, CR.PREFLIGHT_DISAGREES,
              CR.STATE_UNREADABLE, CR.EXECUTION_CONTRADICTION,
              CR.UNKNOWN_TERMINAL, CR.ORDERS_LIVE_AFTER_STOP,
              CR.RISK_BUDGET_EXHAUSTED, CR.DAILY_LOSS_STOP):
        assert s in CR.TERMINAL_RUN_STATES


def test_the_first_stop_reason_is_the_one_that_is_kept():
    r = CR.ContinuousRunner()
    r.stop(CR.UNKNOWN_TERMINAL, "first")
    r.stop(CR.RISK_BUDGET_EXHAUSTED, "second")
    assert r.state.stopped == CR.UNKNOWN_TERMINAL and r.state.why == "first"


def test_a_shadow_cycle_row_reconstructs_the_whole_chain(monkeypatch):
    """market state -> book/depth/flow -> inventory -> L5 action ->
    execution plan, in one row."""
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import metadata as MD
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})
    book = {"readable": True, "identity_consistent": True,
            "yes": {"bid": "0.31", "ask": "0.58"},
            "no": {"bid": "0.42", "ask": "0.69"},
            "passive_pair_cost": "0.73", "crossing_pair_cost": "1.27",
            "source": "events contract prices"}
    ms = CP.state_from_venue("SYM", 265.0, CP.Inventory(), now=1.0, book=book)
    depth = {"readable": True,
             "bids": {"best": "0.31", "best_qty": "483.0", "n_levels": 2,
                      "levels": [{"price": "0.31", "qty": "483.0"},
                                 {"price": "0.30", "qty": "5.0"}]},
             "asks": {"best": "0.58", "best_qty": "10.0", "n_levels": 1,
                      "levels": [{"price": "0.58", "qty": "10.0"}]}}
    flow = {"readable": True, "n_prints": 7, "volume": "3.5",
            "buy_prints": 4, "sell_prints": 3}
    decision = {"turn": {"actions": [{"leg": "YES", "price": "0.31",
                                      "qty": "0.01", "action": CP.OPEN}],
                         "holds": []},
                "plan": {"keep": [], "cancel": [],
                         "place": [{"rung": {"leg": "YES", "price": "0.31"}}],
                         "mutations": 1}}
    r = CR.ContinuousRunner(evidence_dir=None)
    row = r.observe_cycle(symbol="SYM", tte_s=265.0, market_state=ms,
                          depth=depth, flow=flow,
                          inventory=CP.Inventory().report(), working=[],
                          decision=decision)

    assert row["book"]["yes"]["bid"] == "0.31"
    assert row["book"]["yes"]["spread"] == "0.27"
    assert row["l5_action"] == [{"leg": "YES", "price": "0.31",
                                 "qty": "0.01", "action": CP.OPEN}]
    assert row["execution_plan"]["mutations"] == 1
    assert row["execution_plan"]["SHADOW_NOT_SENT"] is True
    assert row["habitat"]["flow"]["n_prints"] == 7
    assert row["habitat"]["ladder"]["bids"]["cum_within_1_ticks"] == "488.0"
    assert row["inventory"]["n_fills"] == 0


def test_shadow_rows_never_claim_an_order_was_sent():
    r = CR.ContinuousRunner()
    rep = r.report()
    assert rep["mode"] == CR.SHADOW
    assert "REALIZED" in rep["accounting_labels"]
    assert rep["total_spend_usd"] == "0" and rep["realized_usd"] == "0"


# ======================================== the soak cannot reach a mutation path

def test_the_soak_has_no_mutation_path():
    """It lives inside `src/gemini_execution/`, which is the one package the
    repo-wide write-path guard exempts -- so that guard does not scan it and
    this proves the property directly instead.

    The claim is about what it CALLS, not about a flag it sets: no session is
    ever constructed, and `place`/`cancel` never appear as calls.
    """
    import ast
    import pathlib

    src = pathlib.Path("src/gemini_execution/shadow_soak.py").read_text()
    tree = ast.parse(src)

    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)
    for forbidden in ("place", "cancel", "cancel_and_confirm",
                      "GeminiMakerSession", "run_window", "submit_once",
                      "submit_once_ws"):
        assert forbidden not in called, forbidden

    # and it never even imports the module that holds the write path
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            imported |= {"%s.%s" % (n.module, a.name) for a in n.names}
        elif isinstance(n, ast.Import):
            imported |= {a.name for a in n.names}
    assert "src.gemini_execution.ws_session" not in imported
    assert "src.gemini_execution.ws_continuous" not in imported


def test_the_soak_reports_zero_mutations_by_construction():
    src = __import__("pathlib").Path(
        "src/gemini_execution/shadow_soak.py").read_text()
    assert '"mutations_sent": 0' in src
