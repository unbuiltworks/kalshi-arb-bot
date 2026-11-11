"""The blocked-arm restart must not let anything cross the switch.

WHY THIS GATE EXISTS. `L5_TS_COMPLETION_AT_TOUCH` is read once via
`os.getenv` at `l5_live_execution.py:7303` and nothing mutates it in
process, so a control arm and an at-touch arm cannot be interleaved per
window -- the comparison needs a RESTART between them. The restart is the
risk: anything live or unresolved when the process goes down crosses a
configuration boundary and is managed on the far side by a DIFFERENT
completion policy than the one that created it.

The owner asked for the procedure to be verified, including confirmed
settlement and retirement before restarting into at-touch mode. A prose
checklist cannot be verified, so the procedure is a predicate and these are
its proofs.

ONE PATH TO OK, and every other answer is a named blocker.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_arm_switch_gate as G

W1 = "cpc-btc-updown-15m-2026-11-03-1900z"
W2 = "cpc-btc-updown-15m-2026-11-03-1915z"


class FakeStore:
    """Only what the gate reads."""

    def __init__(self, state, exposure=(), uncertain=None, raise_exposure=False):
        self.state = state
        self._exposure = list(exposure)
        self._uncertain = uncertain
        self._raise = raise_exposure

    def markets_with_exposure(self):
        if self._raise:
            raise RuntimeError("venue read failed")
        return list(self._exposure)

    def orders_uncertain(self):
        return self._uncertain


def clean_state(*markets):
    return {"live_orders": {}, "pending_terminal": {}, "quarantine": {},
            "submitted": {}, "orders_uncertain": None,
            "retired_markets": {m: {"t": "2026-11-03T19:30:02Z",
                                    "settlement_pnl": "-0.0125",
                                    "yes_value": "1"} for m in markets}}


def ready(**kw):
    st = kw.pop("state", None) or clean_state(W1, W2)
    store = kw.pop("store", None) or FakeStore(st, **{
        k: kw.pop(k) for k in ("exposure", "uncertain", "raise_exposure")
        if k in kw})
    return G.switch_readiness(store, windows=kw.pop("windows", (W1, W2)),
                              halt_asserted=kw.pop("halt_asserted", True))


# ------------------------------------------------------------ the one pass

def test_a_fully_quiet_retired_book_is_the_only_pass():
    out = ready()
    assert out["ok"] is True
    assert out["blockers"] == []
    assert out["evidence"]["windows"][W1]["retired"] is True
    assert out["evidence"]["windows"][W1]["settlement_recorded"] is True


# ---------------------------------------------------------------- the latch

def test_an_unasserted_halt_blocks():
    assert "HALT_NOT_ASSERTED" in ready(halt_asserted=False)["blockers"]


def test_an_UNESTABLISHED_halt_blocks_and_is_named_differently():
    """None is not False. A latch we could not read is not a latch we know
    is off, and neither is a pass."""
    out = ready(halt_asserted=None)
    assert out["ok"] is False
    assert "HALT_STATE_UNESTABLISHED" in out["blockers"]


def test_the_gate_does_not_read_the_latch_itself():
    """It must not acquire a second, divergent reading of a latch HaltGate
    owns over an owner-controlled object."""
    import inspect
    src = inspect.getsource(G)
    # prose may NAME the owner of the latch; code may not reach for it
    for forbidden in ('os.getenv("L5_HALT_URL"', "HaltGate(",
                      "HttpHaltStore(", "import l5_halt",
                      "halt_gate("):
        assert forbidden not in src, forbidden
    # and the parameter is passed IN, which is the structural proof
    assert "halt_asserted" in inspect.signature(
        G.switch_readiness).parameters


# ------------------------------------------------------- the four row-books

@pytest.mark.parametrize("book,blocker", [
    ("live_orders", "LIVE_ORDERS_NOT_EMPTY"),
    ("pending_terminal", "PENDING_TERMINAL_NOT_EMPTY"),
    ("quarantine", "QUARANTINE_NOT_EMPTY"),
])
def test_every_order_book_blocks_on_its_own(book, blocker):
    """Each is written by a different path and each holds an order whose
    fate is unsettled, so any one of them is enough."""
    st = clean_state(W1, W2)
    st[book] = {"l5f-abc": {"market": W1, "remaining": "1"}}
    out = ready(state=st)
    assert out["ok"] is False
    assert blocker in out["blockers"]
    assert out["evidence"][book]["count"] == 1


def test_the_books_are_reported_even_when_they_pass():
    out = ready()
    for book in G.ORDER_BOOKS:
        assert out["evidence"][book] == {"count": 0, "keys": []}


# ------------------------------- `submitted` is NOT an open-order book

def test_SUBMITTED_IS_A_LIFETIME_LEDGER_AND_MUST_NOT_BLOCK():
    """THE DEFECT THE FIRST PRODUCTION EMISSION CAUGHT, in my own gate.

    At 2026-11-03T19:42:11Z the probe returned
    blockers ["HALT_NOT_ASSERTED", "SUBMITTED_NOT_EMPTY"] with
    submitted.count = 22848 while live_orders, pending_terminal, quarantine
    and markets_with_exposure were ALL empty. So the gate could never have
    passed again and the arm switch would have been blocked for ever.

    `submitted` is an append-only idempotency ledger: `record_submit` writes
    it for every coid ever sent, nothing pops or deletes from it, it backs
    `already_submitted(coid)` duplicate suppression, and it lives on a
    volume that outlives the process. Its size is a lifetime count.
    """
    st = clean_state(W1, W2)
    st["submitted"] = {"l5f-%026d" % i: {"market": W1} for i in range(5000)}
    out = ready(state=st)
    assert out["ok"] is True, out["blockers"]
    assert "SUBMITTED_NOT_EMPTY" not in out["blockers"]
    assert out["evidence"]["submitted"]["count"] == 5000
    assert out["evidence"]["submitted"]["blocking"] is False
    assert "submitted" not in G.ORDER_BOOKS
    assert "submitted" in G.EVIDENCE_ONLY_BOOKS


def test_excluding_submitted_loses_no_signal():
    """`record_submit` writes `submitted` and `live_orders` in the SAME call
    before one save, so a coid cannot be in one without the other; an order
    that leaves live_orders unresolved lands in pending_terminal; and a set
    we cannot enumerate is caught by orders_uncertain. Pinned on the source
    so a future change to record_submit cannot quietly break the argument."""
    import inspect
    from src import l5_live_execution as LE
    src = inspect.getsource(LE.InventoryStore.record_submit)
    assert 'setdefault("submitted", {})[coid]' in src
    assert 'setdefault("live_orders", {})[coid]' in src
    assert "self.save()" in src
    whole = inspect.getsource(LE)
    for prune in ('submitted"].pop', 'pop("submitted"'):
        assert prune not in whole, prune


# --------------------------------------------------------- uncertainty flag

def test_an_uncertain_order_set_blocks():
    """The one case where we cannot even enumerate what would cross."""
    out = ready(uncertain="LIST_ORDERS_FAILED_TWICE")
    assert out["ok"] is False
    assert "ORDERS_UNCERTAIN" in out["blockers"]
    assert out["evidence"]["orders_uncertain"] == "LIST_ORDERS_FAILED_TWICE"


# ------------------------------------------------------- outstanding book

def test_any_market_with_exposure_blocks():
    out = ready(exposure=[W1])
    assert out["ok"] is False
    assert "EXPOSURE_OUTSTANDING" in out["blockers"]


def test_exposure_covers_residual_inventory_a_quiet_order_book_hides():
    """markets_with_exposure unions committed, positions and leg_cost, so a
    residual leg with no order anywhere still blocks."""
    out = ready(exposure=["cpc-btc-updown-15m-2026-11-02-2300z"])
    assert out["ok"] is False
    assert out["evidence"]["markets_with_exposure"] == [
        "cpc-btc-updown-15m-2026-11-02-2300z"]


def test_an_unreadable_exposure_read_blocks_rather_than_passes():
    out = ready(raise_exposure=True)
    assert out["ok"] is False
    assert "EXPOSURE_UNREADABLE" in out["blockers"]


# ------------------------------- confirmed settlement AND retirement

def test_an_unretired_window_blocks_even_with_a_quiet_book():
    """THE OWNER'S EXPLICIT REQUIREMENT. An empty exposure list is also what
    a window that never traded looks like, so retirement is checked by name
    and not inferred from quiet."""
    st = clean_state(W1)                      # W2 missing
    out = ready(state=st)
    assert out["ok"] is False
    assert "WINDOW_NOT_RETIRED" in out["blockers"]
    assert out["evidence"]["windows"][W2]["why"] == "NOT_IN_RETIRED_MARKETS"


def test_a_retirement_without_a_settlement_figure_is_not_confirmed():
    """A key in retired_markets with no settlement recorded is a retirement
    we cannot show was a settlement."""
    st = clean_state(W1, W2)
    st["retired_markets"][W2] = {"t": "2026-11-03T19:30:02Z"}
    out = ready(state=st)
    assert out["ok"] is False
    assert "WINDOW_RETIRED_WITHOUT_SETTLEMENT_RECORD" in out["blockers"]
    assert out["evidence"]["windows"][W2]["settlement_recorded"] is False


def test_a_zero_settlement_value_still_counts_as_recorded():
    """A DOWN window settles at yes_value 0 and a flat pair settles at 0.00
    P&L. Neither is missing evidence, and a truthiness test would have read
    both as absent."""
    st = clean_state(W1, W2)
    st["retired_markets"][W2] = {"t": "2026-11-03T19:30:02Z",
                                 "settlement_pnl": "0.0000",
                                 "yes_value": "0"}
    assert ready(state=st)["ok"] is True


def test_naming_no_windows_checks_the_book_only():
    """A switch before any window traded is legitimate and must not demand
    a retirement that cannot exist."""
    st = clean_state()
    out = ready(state=st, windows=())
    assert out["ok"] is True
    assert "windows" not in out["evidence"]


def test_retirement_is_written_by_exactly_one_function():
    """The gate's strength is entirely that `retired_markets` cannot be
    written by anything that skipped the settlement refusals."""
    import inspect
    from src import l5_live_execution as LE
    src = inspect.getsource(LE)
    writers = [ln for ln in src.splitlines()
               if 'state["retired_markets"]' in ln and "=" in ln
               and "get(" not in ln]
    assert len(writers) <= 1, writers
    rsm = inspect.getsource(LE.InventoryStore.retire_settled_market)
    for refusal in ("ALREADY_RETIRED", "LIVE_ORDERS_IN_MARKET",
                    "UNBOOKED_TERMINAL_ORDERS"):
        assert refusal in rsm, refusal
    assert "binary_settlement(" in rsm
    assert "resolution_refusal(" in rsm


# --------------------------------------------------------- fails closed

def test_an_unreadable_store_blocks():
    out = G.switch_readiness(object(), windows=(W1,), halt_asserted=True)
    assert out["ok"] is False
    assert "STORE_UNREADABLE" in out["blockers"]


def test_blockers_accumulate_rather_than_short_circuit():
    """An operator should see everything wrong in one read, not fix one
    blocker to discover the next."""
    st = clean_state()                      # nothing retired
    st["live_orders"] = {"l5f-a": {"market": W1}}
    out = ready(state=st, halt_asserted=False, exposure=[W1],
                uncertain="X")
    for b in ("HALT_NOT_ASSERTED", "LIVE_ORDERS_NOT_EMPTY",
              "ORDERS_UNCERTAIN", "EXPOSURE_OUTSTANDING",
              "WINDOW_NOT_RETIRED"):
        assert b in out["blockers"], b


def test_the_gate_writes_nothing():
    import inspect
    src = inspect.getsource(G)
    for forbidden in ('state[', "save(", "submit(", "cancel(",
                      "retire_settled_market("):
        assert forbidden not in src, forbidden


def test_the_gate_does_not_mutate_the_store():
    import json
    st = clean_state(W1, W2)
    snap = json.dumps(st, sort_keys=True)
    ready(state=st)
    assert json.dumps(st, sort_keys=True) == snap


def test_the_switch_is_restart_only_and_that_is_why_this_exists():
    """The premise. If the flag could be flipped in process this gate would
    be unnecessary -- so pin that it cannot be."""
    import inspect
    from src import l5_live_execution as LE
    src = inspect.getsource(LE)
    assert "L5_TS_COMPLETION_AT_TOUCH" in src
    assert 'os.environ["L5_TS_COMPLETION_AT_TOUCH"]' not in src
    assert "os.environ.setdefault(\"L5_TS_COMPLETION_AT_TOUCH\"" not in src


# ============================================ the emission, on the heartbeat

class _R:
    """Just enough runner surface for the emission method."""
    ARM_SWITCH_PROBE_ENV = "L5_ARM_SWITCH_PROBE"
    ARM_SWITCH_WINDOWS_ENV = "L5_ARM_SWITCH_WINDOWS"

    def __init__(self, store, latch):
        self.store = store
        self._latch = latch

    def submission_gate(self):
        return {"blocked": True, "halt_latch": self._latch}


def _emit_once(monkeypatch, *, latch="HALT", windows="", probe="YES",
               store=None, boom=False):
    from src import l5_pmus_live_runner as R
    seen = []
    monkeypatch.setattr(R, "emit", lambda k, v: seen.append((k, v)))
    monkeypatch.setenv("L5_ARM_SWITCH_PROBE", probe)
    monkeypatch.setenv("L5_ARM_SWITCH_WINDOWS", windows)
    st = store if store is not None else FakeStore(clean_state(W1, W2))
    r = _R(st, latch)
    if boom:
        r.submission_gate = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    R.Runner.emit_arm_switch_readiness(r)
    return [v for k, v in seen if k == "L5_ARM_SWITCH_READINESS"]


def test_the_probe_is_opt_in_and_silent_by_default(monkeypatch):
    assert _emit_once(monkeypatch, probe="") == []
    assert _emit_once(monkeypatch, probe="no") == []


def test_the_probe_emits_ok_on_a_quiet_retired_book(monkeypatch):
    rows = _emit_once(monkeypatch, latch="HALT",
                      windows="%s,%s" % (W1, W2))
    assert len(rows) == 1
    assert rows[0]["ok"] is True
    assert rows[0]["windows_declared"] == [W1, W2]


def test_a_CLEAR_latch_is_not_an_asserted_halt(monkeypatch):
    rows = _emit_once(monkeypatch, latch="CLEAR", windows=W1 + "," + W2)
    assert rows[0]["ok"] is False
    assert "HALT_NOT_ASSERTED" in rows[0]["blockers"]


@pytest.mark.parametrize("latch", [None, "NO_GATE", "something-else"])
def test_an_unreadable_latch_is_UNESTABLISHED_not_a_pass(latch, monkeypatch):
    rows = _emit_once(monkeypatch, latch=latch, windows=W1 + "," + W2)
    assert rows[0]["ok"] is False
    assert "HALT_STATE_UNESTABLISHED" in rows[0]["blockers"]


def test_an_undeclared_window_is_not_silently_excused(monkeypatch):
    """Declaring no windows checks the book only -- which is correct for a
    switch before anything traded, and is why the variable must be SET for
    an arm switch that did trade."""
    rows = _emit_once(monkeypatch, windows="")
    assert rows[0]["ok"] is True
    assert rows[0]["windows_declared"] == []
    rows2 = _emit_once(monkeypatch, windows="cpc-btc-updown-15m-2026-11-03-2100z")
    assert rows2[0]["ok"] is False
    assert "WINDOW_NOT_RETIRED" in rows2[0]["blockers"]


def test_a_failed_probe_is_never_reported_as_ok(monkeypatch):
    rows = _emit_once(monkeypatch, boom=True)
    assert rows[0]["ok"] is False
    assert rows[0]["blockers"][0].startswith("PROBE_FAILED:")


def test_the_probe_runs_on_the_heartbeat_not_at_boot(monkeypatch):
    """The answer is only useful BEFORE the process goes down. A boot
    emission would describe the far side of the restart."""
    import inspect
    from src import l5_pmus_live_runner as R
    hb = inspect.getsource(R.Runner.heartbeat)
    assert "emit_arm_switch_readiness()" in hb
