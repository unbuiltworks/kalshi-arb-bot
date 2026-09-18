"""The invoker holds no bounds -- but it does decide the symbol and the clock,
which the driver cannot check for itself. These are those two decisions."""
import ast

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import run_continuous as RC


def test_an_unreadable_expiry_fails_closed_to_withdrawn(monkeypatch):
    """A clock we cannot read must drive the window to WITHDRAWN, not to a
    guess. -1.0 is below the withdraw floor, so the driver pulls everything."""
    monkeypatch.setattr(RC, "_expiry_epoch", lambda s: None)
    assert RC.tte_of("GEMI-BTC05M2609181300-UP")() == -1.0
    assert CP.window_phase(-1.0) == CP.WINDOW_WITHDRAWN


def test_the_clock_is_read_from_the_venue_on_every_call(monkeypatch):
    seen = []
    monkeypatch.setattr(RC, "_expiry_epoch",
                        lambda s: (seen.append(s) or 1_789_000_300.0))
    monkeypatch.setattr(RC.time, "time", lambda: 1_789_000_000.0)
    f = RC.tte_of("SYM")
    assert f() == 300.0 and f() == 300.0
    assert len(seen) == 2, "the expiry must be re-read, never cached"


def test_the_entry_band_leaves_room_for_a_full_rest_cycle():
    assert RC.MIN_ENTRY_TTE_S == CP.NO_NEW_OPEN_EXTEND_TTE_S + CP.STALE_ORDER_S
    assert RC.MIN_ENTRY_TTE_S == 90.0
    assert RC.MAX_ENTRY_TTE_S == 300.0


def test_a_contract_outside_the_band_is_refused(monkeypatch):
    monkeypatch.setattr(RC.MD, "current_btc5m_ticker", lambda t: "BTC05M1")
    monkeypatch.setattr(RC.MD, "sibling_ticker", lambda tk, m=5: None)
    monkeypatch.setattr(RC.MD, "terms_for",
                        lambda s: {"contract_status": "active",
                                   "market_state": "open"})
    monkeypatch.setattr(RC, "_expiry_epoch", lambda s: 1_789_000_010.0)
    got = RC.eligible_symbol(now=1_789_000_000.0)      # tte 10s
    assert got["eligible"] is False
    assert "entry band" in got["why"]


def test_a_contract_that_is_not_open_is_refused(monkeypatch):
    monkeypatch.setattr(RC.MD, "current_btc5m_ticker", lambda t: "BTC05M1")
    monkeypatch.setattr(RC.MD, "sibling_ticker", lambda tk, m=5: None)
    monkeypatch.setattr(RC.MD, "terms_for",
                        lambda s: {"contract_status": "settled",
                                   "market_state": "closed"})
    monkeypatch.setattr(RC, "_expiry_epoch", lambda s: 1_789_000_200.0)
    assert RC.eligible_symbol(now=1_789_000_000.0)["eligible"] is False


def test_an_eligible_contract_is_chosen(monkeypatch):
    monkeypatch.setattr(RC.MD, "current_btc5m_ticker", lambda t: "BTC05M1")
    monkeypatch.setattr(RC.MD, "terms_for",
                        lambda s: {"contract_status": "active",
                                   "market_state": "open"})
    monkeypatch.setattr(RC, "_expiry_epoch", lambda s: 1_789_000_200.0)
    got = RC.eligible_symbol(now=1_789_000_000.0)      # tte 200s
    assert got["eligible"] is True
    assert got["chosen"]["symbol"] == "GEMI-BTC05M1-UP"
    assert got["chosen"]["tte_s"] == 200.0


def test_unreadable_venue_reads_stay_none_and_never_become_empty():
    """The substitution that makes a restart look flat."""
    src = open(RC.__file__, encoding="utf-8").read()
    assert 'out["fills"] = None' in src
    assert 'out["open_orders"] = None' in src
    # and nothing turns a failed read into []
    assert 'out["fills"] = []' not in src
    assert 'out["open_orders"] = []' not in src


def test_filled_orders_map_to_the_shape_recover_consumes():
    legs = RC._legs_from_orders([
        {"outcome": "yes", "filledQuantity": "0.01", "price": "0.55"},
        {"outcome": "no", "filledQuantity": "0.01", "price": "0.40"},
        {"outcome": "yes", "filledQuantity": "0", "price": "0.50"},   # dropped
    ])
    assert [l["leg"] for l in legs] == ["YES", "NO"]
    inv, why = CP.recover("X", "GEMI-BTC05M1-UP", legs)
    assert inv is not None and inv.yes_qty == CP.D("0.01")


def test_the_invoker_enforces_no_bound_of_its_own():
    """Every limit belongs to the driver. If a number from the envelope
    appears here as a literal it is a second copy, and a second copy drifts."""
    tree = ast.parse(open(RC.__file__, encoding="utf-8").read())
    lits = {str(n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))}
    for bound in ("12", "0.15", "0.04", "0.1"):
        assert bound not in lits, (
            "%s looks like an envelope bound restated in the invoker" % bound)


def test_the_invoker_cannot_send_an_order_itself():
    tree = ast.parse(open(RC.__file__, encoding="utf-8").read())
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "request" not in called, (
        "the invoker must go through Driver.place/cancel, which is where the "
        "budget ledger and the authorization gate live")


def test_the_latch_is_consumed_before_the_session_connects():
    """Ordering is the whole guarantee: a mutation whose authorization is not
    durably spent is the one state that cannot be reconciled."""
    src = open(RC.__file__, encoding="utf-8").read()
    i_latch = src.index("latch.consume(auth_id")
    i_conn = src.index("await session.connect()")
    i_state = src.index("await authoritative_state(symbol)")
    assert i_latch < i_conn, "the latch must be spent before the socket opens"
    assert i_latch < i_state, "and before any venue read for this window"


def test_an_already_consumed_authorization_refuses_to_run_again():
    src = open(RC.__file__, encoding="utf-8").read()
    assert "latch.AlreadyConsumed" in src
    assert "never a re-run" in src


def test_a_latch_that_cannot_be_made_durable_sends_nothing():
    src = open(RC.__file__, encoding="utf-8").read()
    assert "make_durable(auth_id, rec0)" in src
    assert "could not be made durable" in src


def test_the_latch_records_the_authorized_shas():
    src = open(RC.__file__, encoding="utf-8").read()
    assert '"executable_sha": WC.executable_sha()' in src
    assert '"envelope_sha": WC.envelope_sha()' in src


# ===================== THE THREE DEFECTS THE FIRST LIVE RUN EXPOSED =========

def test_REGRESSION_an_async_terms_reader_is_loud_not_a_silent_none():
    """THE DEFECT THAT COST A LIVE AUTHORIZATION.

    MD.terms_status is a coroutine function. preflight_now is synchronous, so
    calling it there returned a coroutine whose .get() raised AttributeError,
    which the handler recorded as terms_accepted=None -- and an unread value
    is a DISCREPANCY, so the probe failed closed on EVERY run and could never
    have traded. Safe, and still completely broken.

    No test caught it because every test injected plain lambdas. This one
    passes the REAL reader.
    """
    from src.gemini_execution import continuous_preflight as PF
    from src.gemini_execution import metadata as MD
    import inspect
    assert inspect.iscoroutinefunction(MD.terms_status), (
        "if this ever becomes sync, the guard below stops testing anything")
    out = PF.preflight_now("SYM", readers={
        "terms_for": lambda s: {"quantityMinimum": "0.01",
                                "quantityIncrement": "0.01",
                                "priceMinimum": "0.01",
                                "priceIncrement": "0.01",
                                "contract_status": "active",
                                "market_state": "open"},
        "terms_status": MD.terms_status,
        "outcome_book": lambda s: {}})
    assert out["terms_accepted"] is None
    assert "coroutine" in out["terms_why"], out["terms_why"]
    assert "must await" in out["terms_why"]


def test_an_awaited_terms_value_makes_the_preflight_agree():
    from src.gemini_execution import continuous_preflight as PF
    out = PF.preflight_now("SYM", readers={
        "terms_for": lambda s: {"quantityMinimum": "0.01",
                                "quantityIncrement": "0.01",
                                "priceMinimum": "0.01",
                                "priceIncrement": "0.01",
                                "contract_status": "active",
                                "market_state": "open"},
        "terms_status": lambda: {"hasAcceptedLatest": True,
                                 "acceptedVersion": 1},
        "outcome_book": lambda s: {}})
    assert out["terms_accepted"] is True
    assert out["frozen_comparison"]["verdict"] == PF.AGREES


def test_the_invoker_awaits_the_terms_read_and_passes_a_value():
    src = open(RC.__file__, encoding="utf-8").read()
    assert "await MD.terms_status()" in src
    assert '"terms_status": (lambda: ts_value)' in src


def test_REGRESSION_the_preflight_runs_before_the_latch_is_spent():
    """A preflight is a READ. Consuming the latch first meant a fail-closed
    preflight burned the one authorization with no order ever attempted --
    which is what happened at 2026-09-18T13:33Z."""
    src = open(RC.__file__, encoding="utf-8").read()
    i_pf = src.index("pf = PF.preflight_now(symbol")
    i_latch = src.index("latch.consume(auth_id")
    i_conn = src.index("await session.connect()")
    assert i_pf < i_latch < i_conn
    assert "the authorization is untouched" in src


def test_a_refused_preflight_reports_the_latch_as_unspent():
    src = open(RC.__file__, encoding="utf-8").read()
    i_pf = src.index("pf = PF.preflight_now(symbol")
    i_latch = src.index("latch.consume(auth_id")
    between = src[i_pf:i_latch]
    assert '"consumed": False' in between
    assert "return rep" in between, "it must return, not fall through"


def test_REGRESSION_the_evidence_commit_survives_a_missing_artifact():
    """A window that attempts no mutation writes no ledger. `git add` on a
    missing pathspec exits 128, which killed the job and discarded the
    evidence it HAD gathered -- the record of a refusal."""
    import pathlib
    wf = pathlib.Path(".github/workflows/gemini-settlement-gate.yml").read_text()
    i = wf.index("Commit ONLY the evidence artifact")
    step = wf[i:i + 2500]
    assert 'if [ -e "$f" ]; then git add -- "$f"; fi' in step
    assert "git add -- 'analysis/gemini/continuous_ledger.jsonl'" not in step
