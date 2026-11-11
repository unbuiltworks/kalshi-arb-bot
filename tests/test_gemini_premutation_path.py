"""POSITIVE PATH. Does a fully valid state actually REACH the mutation boundary?

THIS IS THE TEST THAT WAS MISSING, TWICE. Both defects that made the driver
permanently incapable of trading -- the unarmable self-referential hash, and
the un-awaited async terms reader -- survived a large adversarial suite because
every test asserted the safe path REFUSES. Refusing is also what broken looks
like, so the suite could not tell the two apart.

Every test here drives the real production path with readers of the REAL
SHAPE, including the async ones, and asserts it gets all the way to the
boundary with authorization as the only thing left. The refusal tests elsewhere
stay exactly as they are; these are the other half of the pair.
"""
import ast
import asyncio

import pytest

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import metadata as MD
from src.gemini_execution import premutation_probe as PP
from src.gemini_execution import run_continuous as RC
from src.gemini_execution import transport as T
from src.gemini_execution import ws_continuous as WC

SYM = "GEMI-BTC05M2609181400-UP"
T0 = 1_789_000_000.0
EXPIRY = T0 + 200.0            # tte 200s -> WINDOW_OPEN


# --------------------------------------------------------------------------
# a venue with the PRODUCTION SHAPES. terms_status, order_history and
# active_orders are async here because they are async there; that is the whole
# point of this fixture.
# --------------------------------------------------------------------------

def _terms(_symbol):
    return {"quantityMinimum": "0.01", "quantityIncrement": "0.01",
            "priceMinimum": "0.01", "priceIncrement": "0.01",
            "contract_status": "active", "market_state": "open",
            "expiry": "2026-09-18T14:00:00.000Z"}


async def _terms_status():                      # ASYNC, like production
    return {"hasAcceptedLatest": True, "acceptedVersion": 1,
            "latestVersion": 1}


def _book(_symbol):
    return {"readable": True, "identity_consistent": True,
            "yes": {"bid": "0.55", "ask": "0.60"},
            "no": {"bid": "0.40", "ask": "0.45"}}


async def _order_history(**_kw):                # ASYNC, like production
    return {"readable": True, "verdict": "NO_ORDERS", "total_rows": 0,
            "per_status": [{"status_filter": "filled", "orders": []}]}


async def _active_orders(_symbol=None):         # ASYNC, like production
    return {"ok": True, "status": 200, "request_shape": "json_body",
            "body": {"orders": []}}


@pytest.fixture
def live_venue(monkeypatch, tmp_path):
    monkeypatch.setattr(MD, "terms_for", _terms)
    monkeypatch.setattr(MD, "terms_status", _terms_status)
    monkeypatch.setattr(MD, "outcome_book", _book)
    monkeypatch.setattr(MD, "order_history", _order_history)
    monkeypatch.setattr(MD, "current_btc5m_ticker", lambda t: "BTC05M2609181400")
    monkeypatch.setattr(MD, "sibling_ticker", lambda tk, m=5: None)
    monkeypatch.setattr(T, "active_orders", _active_orders)
    monkeypatch.setattr(RC, "_expiry_epoch", lambda s: EXPIRY)
    monkeypatch.setattr(RC.time, "time", lambda: T0)
    # the arm stays ABSENT -- that is the gate under test
    monkeypatch.delenv("GEMINI_CANARY_LIVE", raising=False)
    monkeypatch.delenv("GEMINI_CANARY_AUTH", raising=False)
    return str(tmp_path / "ledger.jsonl")


def _prove(ledger):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        PP.prove(SYM, ledger_path=ledger, now=T0))


# ============================================ THE POSITIVE PATH ITSELF

def test_a_fully_valid_state_REACHES_the_mutation_boundary(live_venue):
    rep = _prove(live_venue)
    assert rep["verdict"] == PP.READY, (
        "the production path did NOT reach the boundary; failed gates: %s"
        % rep.get("failed_gates"))
    assert rep["failed_gates"] == []
    assert all(g["ok"] for g in rep["gates"]), rep["gates"]


def test_the_async_terms_reader_is_actually_awaited(live_venue):
    """The exact defect that cost a live authorization."""
    rep = _prove(live_venue)
    gate = next(g for g in rep["gates"]
                if g["gate"] == "terms_awaited_and_accepted")
    assert gate["ok"] is True
    assert gate["awaited"] is True
    assert gate["accepted"] is True
    assert gate["version"] == 1


def test_authorization_is_the_SOLE_remaining_blocker(live_venue):
    rep = _prove(live_venue)
    b = rep["boundary"]
    assert b["refused"] is True
    assert b["by"] == "NotAuthorized", (
        "something OTHER than the authorization gate blocked the mutation, "
        "which means this run proves less than it claims: %r" % b)
    assert b["reached_socket"] is False
    assert "ALARM" not in b


def test_the_controller_produced_the_expected_first_surface(live_venue):
    rep = _prove(live_venue)
    d = rep["decision"]
    assert d["relations"] == [CP.OPEN, CP.OPEN]
    legs = sorted(s["leg"] for s in d["desired_surface"])
    assert legs == ["NO", "YES"]
    assert {s["qty"] for s in d["desired_surface"]} == {"0.01"}


def test_the_diff_plans_exactly_two_places_from_flat(live_venue):
    rep = _prove(live_venue)
    assert rep["plan"]["mutations"] == 2
    assert len(rep["plan"]["place"]) == 2
    assert rep["plan"]["keep"] == [] and rep["plan"]["cancel"] == []
    assert rep["slots_that_would_be_consumed"] == 2
    assert rep["slots_remaining_after"] == CP.MAX_ACTIONS - 2


def test_the_rendered_frames_are_maker_only_buys(live_venue):
    rep = _prove(live_venue)
    assert len(rep["would_send"]) == 2
    for f in rep["would_send"]:
        p = f["params"]
        assert p["side"] == "BUY" and p["type"] == "LIMIT"
        assert p["timeInForce"] == "MOC"
        assert p["symbol"] == SYM
        assert p["quantity"] == "0.01"


def test_recovery_and_budget_succeed_on_a_flat_account(live_venue):
    rep = _prove(live_venue)
    assert rep["authoritative_reads"]["fills_readable"] is True
    assert rep["authoritative_reads"]["open_orders_readable"] is True
    assert rep["recovery"]["budget_consumed"] == 0
    assert rep["recovery"]["budget_left"] == CP.MAX_ACTIONS
    assert rep["starting_inventory"]["yes_qty"] == "0"


def test_nothing_was_spent_or_sent(live_venue):
    rep = _prove(live_venue)
    assert rep["frames_sent"] == 0
    assert rep["ledger_rows"] == 0
    assert rep["latch_consumed"] is False


# ================= CORRECTLY FAIL-CLOSED vs PERMANENTLY INCAPABLE ==========

def test_an_UNREADABLE_terms_read_is_fail_closed_NOT_incapable(live_venue,
                                                                monkeypatch):
    """A venue that will not answer must block -- and the SAME code must still
    reach the boundary when the venue does answer. That pairing is what
    distinguishes a working gate from a broken one."""
    async def _no_terms():
        raise RuntimeError("venue said no")
    monkeypatch.setattr(MD, "terms_status", _no_terms)
    bad = _prove(live_venue)
    assert bad["verdict"] == PP.BLOCKED
    assert "terms_awaited_and_accepted" in bad["failed_gates"]

    monkeypatch.setattr(MD, "terms_status", _terms_status)
    good = _prove(live_venue)
    assert good["verdict"] == PP.READY, (
        "the gate blocks but never passes -- that is PERMANENTLY INCAPABLE, "
        "not fail-closed")


def test_an_UNREADABLE_inventory_is_fail_closed_NOT_incapable(live_venue,
                                                               monkeypatch):
    async def _hist_fails(**_kw):
        raise RuntimeError("unreadable")
    monkeypatch.setattr(MD, "order_history", _hist_fails)
    bad = _prove(live_venue)
    assert bad["verdict"] == PP.BLOCKED
    assert "inventory_readable" in bad["failed_gates"]

    monkeypatch.setattr(MD, "order_history", _order_history)
    assert _prove(live_venue)["verdict"] == PP.READY


def test_a_CLOSED_contract_is_fail_closed_NOT_incapable(live_venue,
                                                         monkeypatch):
    monkeypatch.setattr(MD, "terms_for",
                        lambda s: dict(_terms(s), contract_status="settled",
                                       market_state="closed"))
    bad = _prove(live_venue)
    assert bad["verdict"] == PP.BLOCKED
    assert "contract_active_open" in bad["failed_gates"]

    monkeypatch.setattr(MD, "terms_for", _terms)
    assert _prove(live_venue)["verdict"] == PP.READY


def test_a_WITHDRAWN_window_is_fail_closed_NOT_incapable(live_venue,
                                                          monkeypatch):
    monkeypatch.setattr(RC, "_expiry_epoch", lambda s: T0 + 10.0)   # tte 10s
    bad = _prove(live_venue)
    assert bad["verdict"] == PP.BLOCKED
    assert "tte_readable_and_executable" in bad["failed_gates"]

    monkeypatch.setattr(RC, "_expiry_epoch", lambda s: EXPIRY)
    assert _prove(live_venue)["verdict"] == PP.READY


def test_EVERY_gate_can_both_pass_and_fail(live_venue):
    """A gate that can only ever fail proves nothing. This asserts the READY
    run exercises every gate in the PASSING direction at least once."""
    rep = _prove(live_venue)
    names = [g["gate"] for g in rep["gates"]]
    for required in ("symbol_selected", "contract_active_open",
                     "increments_agree", "terms_awaited_and_accepted",
                     "preflight_agrees", "inventory_readable",
                     "open_orders_readable", "ledger_recovery_readable",
                     "budget_reconstructed",
                     "starting_exposure_within_envelope",
                     "window_guard_bound", "tte_readable_and_executable",
                     "controller_produced_a_surface",
                     "diff_produced_a_mutation_plan",
                     "authorization_is_the_sole_blocker",
                     "no_mutation_was_recorded"):
        assert required in names, required
        assert next(g for g in rep["gates"] if g["gate"] == required)["ok"]


# ======================================= THE PROBE CANNOT SEND, STRUCTURALLY

def test_the_probe_imports_no_session():
    tree = ast.parse(open(PP.__file__, encoding="utf-8").read())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
            mods |= {a.name for a in n.names}
    assert not any("ws_session" in m for m in mods), mods
    assert "GeminiMakerSession" not in mods


def test_the_probe_never_calls_request_or_connect():
    tree = ast.parse(open(PP.__file__, encoding="utf-8").read())
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not (called & {"request", "connect", "send", "cancel"}), called
    # place() IS called -- deliberately, to be refused
    assert "place" in called


def test_the_proof_id_is_not_an_authorization():
    assert "NOT-AN-AUTHORIZATION" in PP.PROOF_ID
    assert PP.PROOF_ID != WC.REQUIRED_AUTH_ID
    src = open(PP.__file__, encoding="utf-8").read()
    assert "latch.consume" not in src, "a proof must never spend anything"
