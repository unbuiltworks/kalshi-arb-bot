"""The shared intent-level boundary.

WHAT MUST HOLD, and every one of these is a way the refactor could have
made things worse rather than better:

  * L5's existing behaviour is UNCHANGED -- same refusals, same layers,
    same reason strings, same record shape, same executor call;
  * the gate ORDER is the deployed order and is pinned, so a reordering
    that let a weaker gate decide first would fail here;
  * a controller-generated intent is subject to EVERY gate an L5 one is,
    gate by gate, and cannot bypass a single one;
  * a controller intent is NEVER given an L5 row, and so can never be
    mistaken for L5's actual order;
  * every gate FAILS CLOSED -- a missing executor, store, registry or
    transport, and a safety reading that raises, all refuse;
  * nothing here submits.
"""
from __future__ import annotations

from decimal import Decimal as D
from unittest import mock

import pytest

from src import l5_intent_gate as G

MARKET = "cpc-btc-updown-15m-2026-10-19-1700z"
WINDOW = "1792429200"


class FakeStore:
    def __init__(self, pending=None):
        self._pending = pending or {}

    def owns_venue_order_id(self, oid):
        return False

    def pending_terminal(self):
        return self._pending


class FakeTransport:
    market_slug = MARKET
    armed = False


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def handle(self, row, authenticated, *, market, window, open_markets=()):
        self.calls.append({"row": row, "market": market, "window": window,
                           "open_markets": tuple(open_markets)})
        return {"ORDER": {"submitted": False, "reason": "NOT_ARMED"},
                "action_key": "k1"}


class FakeRunner:
    """Only what the gate reads. Nothing is duplicated: the gate asks this
    object the same questions it asks the real runner."""

    def __init__(self, **kw):
        self.ws = int(WINDOW)
        self.transport = kw.get("transport", FakeTransport())
        self.executor = kw.get("executor", FakeExecutor())
        self.store = kw.get("store", FakeStore())
        self.registry_server = kw.get("registry_server", object())
        self._halted = kw.get("halted", False)
        self._gate = kw.get("gate", {"shadow_lock": False,
                                     "halt_latch": "CLEAR",
                                     "owner_arm_token": True,
                                     "armed": True})
        self._exposure = kw.get("exposure", {"ok": True,
                                             "capacity_usd": "5"})

    def halted(self):
        return self._halted

    def submission_gate(self):
        if isinstance(self._gate, BaseException):
            raise self._gate
        return dict(self._gate)

    def account_exposure_lines(self):
        if isinstance(self._exposure, BaseException):
            raise self._exposure
        return dict(self._exposure)

    def open_markets(self):
        return []


def l5_intent(**kw):
    base = dict(provenance=G.L5_FOLLOWED, market=MARKET, window=WINDOW,
                side="UP", row={"is_sell": False}, source_ref="k1")
    base.update(kw)
    return G.Intent(**base)


def ctl_intent(**kw):
    base = dict(provenance=G.CONTROLLER_GENERATED, market=MARKET,
                window=WINDOW, side="NO", price=D("0.39"), size=D(1),
                relation="open", policy="RESIDUAL_URGENCY",
                scenario="CONSERVATIVE")
    base.update(kw)
    return G.Intent(**base)


# ============================================== THE ORDER IS THE DEPLOYED ONE

def test_the_gate_order_is_pinned():
    """A reordering that let a weaker gate decide before a stronger one is
    a safety regression, so the order is asserted literally."""
    assert G.GATE_ORDER == (
        "PROVENANCE_KNOWN", "CURRENT_WINDOW", "SELL_IN_PROBE_SCOPE",
        "OPERATOR_HALT", "SHADOW_ONLY_LOCK", "DURABLE_HALT_LATCH",
        "OWNER_ARM_TOKEN", "TRANSPORT_ARMED", "ACCOUNT_WIDE_EXPOSURE",
        "ORDER_REGISTRY_OWNERSHIP", "RECONCILIATION_CERTAIN",
        "EXECUTOR_BOUNDARY")


def test_halt_is_evaluated_before_anything_that_could_submit():
    """A halt must not depend on the rest of the chain working."""
    o = G.GATE_ORDER
    assert o.index("OPERATOR_HALT") < o.index("EXECUTOR_BOUNDARY")
    assert o.index("DURABLE_HALT_LATCH") < o.index("EXECUTOR_BOUNDARY")
    assert o.index("SHADOW_ONLY_LOCK") < o.index("EXECUTOR_BOUNDARY")


def test_the_runners_prefix_is_the_same_list_not_a_second_copy():
    """`Runner.handle` asks for a PREFIX of GATE_ORDER rather than keeping
    its own list, which is what stops the two from drifting apart."""
    g = G.IntentGate(FakeRunner())
    ev = g.evaluate_prefix(l5_intent(), upto="OPERATOR_HALT")
    assert [x["gate"] for x in ev["gates"]] == list(G.GATE_ORDER[:4])
    with pytest.raises(ValueError):
        g.evaluate_prefix(l5_intent(), upto="NO_SUCH_GATE")


# ====================================== BOTH PROVENANCES, EVERY GATE

@pytest.mark.parametrize("make", [l5_intent, ctl_intent], ids=["l5", "ctl"])
@pytest.mark.parametrize("kw,gate,reason", [
    ({"halted": True}, "OPERATOR_HALT", "OPERATOR_HALT"),
    ({"gate": {"shadow_lock": True, "halt_latch": "CLEAR",
               "owner_arm_token": True, "armed": True}},
     "SHADOW_ONLY_LOCK", "SHADOW_ONLY_LOCK"),
    ({"gate": {"shadow_lock": False, "halt_latch": "HALT",
               "halt_why": "halt_asserted", "owner_arm_token": True,
               "armed": True}}, "DURABLE_HALT_LATCH", "HALT:halt_asserted"),
    ({"gate": {"shadow_lock": False, "halt_latch": "CLEAR",
               "owner_arm_token": False, "armed": True}},
     "OWNER_ARM_TOKEN", "NO_OWNER_ARM_TOKEN"),
    ({"gate": {"shadow_lock": False, "halt_latch": "CLEAR",
               "owner_arm_token": True, "armed": False}},
     "TRANSPORT_ARMED", "NOT_ARMED"),
    ({"exposure": {"ok": False, "reason": "over"}},
     "ACCOUNT_WIDE_EXPOSURE", "ACCOUNT_EXPOSURE_REFUSED"),
    ({"registry_server": None}, "ORDER_REGISTRY_OWNERSHIP",
     "NO_ORDER_REGISTRY"),
    ({"store": FakeStore({"c1": {}})}, "RECONCILIATION_CERTAIN",
     "PENDING_TERMINAL_UNRESOLVED"),
])
def test_every_gate_refuses_both_provenances_identically(make, kw, gate,
                                                         reason):
    """THE CENTRAL CLAIM. A controller-generated intent is stopped by the
    same gate, with the same reason, as an L5-followed one. If any gate
    applied to only one of them this fails."""
    g = G.IntentGate(FakeRunner(**kw))
    ev = g.evaluate(make())
    assert ev["admissible"] is False
    assert ev["blocked_by"] == gate, ev
    assert ev["reason"] == reason, ev


@pytest.mark.parametrize("make", [l5_intent, ctl_intent], ids=["l5", "ctl"])
def test_a_wrong_window_refuses_both(make):
    g = G.IntentGate(FakeRunner())
    ev = g.evaluate(make(window="999"))
    assert ev["blocked_by"] == "CURRENT_WINDOW"
    assert ev["reason"] == "NOT_CURRENT_WINDOW"


@pytest.mark.parametrize("make", [l5_intent, ctl_intent], ids=["l5", "ctl"])
def test_a_wrong_market_refuses_both(make):
    g = G.IntentGate(FakeRunner())
    ev = g.evaluate(make(market="cpc-btc-updown-15m-2026-10-19-9999z"))
    assert ev["blocked_by"] == "CURRENT_WINDOW"


def test_a_sell_refuses_on_either_path():
    g = G.IntentGate(FakeRunner())
    assert g.evaluate(l5_intent(row={"is_sell": True}))["reason"] \
        == "SELL_NOT_IN_PROBE_SCOPE"
    assert g.evaluate(ctl_intent(side="SELL_YES"))["reason"] \
        == "SELL_NOT_IN_PROBE_SCOPE"


def test_an_unknown_provenance_refuses_first_of_all():
    """Fail closed on an origin we cannot reason about, before any other
    gate gets a chance to pass it."""
    g = G.IntentGate(FakeRunner())
    ev = g.evaluate(G.Intent(provenance="WISHFUL", market=MARKET,
                             window=WINDOW))
    assert ev["blocked_by"] == "PROVENANCE_KNOWN"
    assert ev["reason"] == "UNKNOWN_PROVENANCE"


# ============================ THE CONTROLLER CANNOT REACH THE VENUE

def test_a_controller_intent_is_refused_even_with_every_gate_satisfied():
    """ALL GREEN AND STILL NO. Shadow lock off, latch clear, owner token
    present, transport armed, exposure fine, registry up, nothing pending
    -- and the generated decision still cannot reach the executor, because
    no authorized adapter from an intent to a submission exists."""
    runner = FakeRunner()
    g = G.IntentGate(runner)
    ev = g.evaluate(ctl_intent())
    assert ev["admissible"] is False
    assert ev["blocked_by"] == "EXECUTOR_BOUNDARY"
    assert ev["reason"] == "CONTROLLER_INTENT_HAS_NO_AUTHORIZED_ADAPTER"
    # and the same intent through the submitting path touches nothing
    out = g.admit(ctl_intent())
    assert out["submitted"] is False
    assert out["blocked_by"] == "EXECUTOR_BOUNDARY"
    assert runner.executor.calls == [], runner.executor.calls


def test_an_l5_intent_with_everything_green_does_reach_the_executor():
    """THE POSITIVE CONTROL. Without it the test above would pass against a
    gate that simply refuses everything."""
    runner = FakeRunner()
    g = G.IntentGate(runner)
    ev = g.evaluate(l5_intent())
    assert ev["admissible"] is True, ev
    out = g.admit(l5_intent(), open_markets=["m1"])
    assert len(runner.executor.calls) == 1
    call = runner.executor.calls[0]
    assert call["market"] == MARKET and call["window"] == WINDOW
    assert call["open_markets"] == ("m1",)
    assert out["ORDER"]["reason"] == "NOT_ARMED"


def test_no_controller_intent_is_ever_given_an_l5_row():
    """Fabricating a row is how our own decision would become
    indistinguishable from L5's actual order in the tape and the ledger."""
    i = ctl_intent()
    assert i.row is None
    assert i.describe()["carries_l5_row"] is False
    assert i.describe()["provenance"] == "CONTROLLER_GENERATED"
    # an L5 intent without its row is refused rather than guessed at
    g = G.IntentGate(FakeRunner())
    ev = g.evaluate(l5_intent(row=None))
    assert ev["blocked_by"] == "EXECUTOR_BOUNDARY"
    assert ev["reason"] == "L5_INTENT_WITHOUT_ROW"


def test_the_described_intent_never_carries_the_l5_row_itself():
    """One copy of L5_NATIVE, in the money path's own record. A second copy
    here could diverge from it."""
    d = l5_intent().describe()
    assert d["carries_l5_row"] is True
    assert "row" not in d and "l5" not in d


# ============================================ FAULT INJECTION: FAIL CLOSED

@pytest.mark.parametrize("make", [l5_intent, ctl_intent], ids=["l5", "ctl"])
def test_a_safety_read_that_raises_is_a_refusal(make):
    """A gate that fails open is not a gate. An unreadable submission gate
    must refuse, not pass for want of information."""
    g = G.IntentGate(FakeRunner(gate=RuntimeError("latch unreachable")))
    ev = g.evaluate(make())
    assert ev["admissible"] is False
    assert ev["blocked_by"] == "SHADOW_ONLY_LOCK"
    assert ev["reason"].startswith("UNREADABLE:RuntimeError")


@pytest.mark.parametrize("make", [l5_intent, ctl_intent], ids=["l5", "ctl"])
def test_an_unreadable_exposure_reading_refuses(make):
    g = G.IntentGate(FakeRunner(exposure=OSError("no cash read")))
    ev = g.evaluate(make())
    assert ev["blocked_by"] == "ACCOUNT_WIDE_EXPOSURE"
    assert ev["reason"].startswith("UNREADABLE:OSError")


@pytest.mark.parametrize("make", [l5_intent, ctl_intent], ids=["l5", "ctl"])
def test_a_missing_exposure_reader_refuses_rather_than_assuming_room(make):
    # a runner that GENUINELY lacks the reader. Subclassing and deleting
    # the attribute does not work: the parent still has it and `hasattr`
    # finds it, which is how an earlier version of this test passed for the
    # wrong reason.
    class NoExposure:
        ws = int(WINDOW)
        transport = FakeTransport()
        store = FakeStore()
        registry_server = object()

        def __init__(self):
            self.executor = FakeExecutor()
            self.transport = FakeTransport()
            self.transport.armed = True

        def halted(self):
            return False

        def submission_gate(self):
            return {"shadow_lock": False, "halt_latch": "CLEAR",
                    "owner_arm_token": True, "armed": True}

        def open_markets(self):
            return []

    r = NoExposure()
    assert not hasattr(r, "account_exposure_lines")
    g = G.IntentGate(r)
    ev = g.evaluate(make())
    assert ev["blocked_by"] == "ACCOUNT_WIDE_EXPOSURE"
    assert ev["reason"] == "EXPOSURE_UNREADABLE"


@pytest.mark.parametrize("make", [l5_intent, ctl_intent], ids=["l5", "ctl"])
def test_a_missing_ownership_oracle_refuses(make):
    """A resting order the watchdog cannot prove is ours is uncancellable
    on a shared account."""
    class NoOracle:
        def pending_terminal(self):
            return {}

    g = G.IntentGate(FakeRunner(store=NoOracle()))
    ev = g.evaluate(make())
    assert ev["blocked_by"] == "ORDER_REGISTRY_OWNERSHIP"
    assert ev["reason"] == "NO_OWNERSHIP_ORACLE"


@pytest.mark.parametrize("make", [l5_intent, ctl_intent], ids=["l5", "ctl"])
def test_a_missing_executor_or_transport_refuses(make):
    for kw in ({"executor": None}, {"transport": None}):
        g = G.IntentGate(FakeRunner(**kw))
        ev = g.evaluate(make())
        assert ev["blocked_by"] == "CURRENT_WINDOW", (kw, ev)
        assert ev["reason"] == "NOT_CURRENT_WINDOW"


@pytest.mark.parametrize("make", [l5_intent, ctl_intent], ids=["l5", "ctl"])
def test_an_ambiguous_reconciliation_state_refuses(make):
    """A parked order whose outcome the venue has not confirmed means our
    own book is uncertain, and an uncertain book must not be added to."""
    g = G.IntentGate(FakeRunner(store=FakeStore({"coid-1": {"x": 1}})))
    ev = g.evaluate(make())
    assert ev["blocked_by"] == "RECONCILIATION_CERTAIN"


def test_evaluate_submits_nothing_under_any_condition():
    """It is a read. Across every fault above, the executor is never
    called."""
    for kw in ({}, {"halted": True}, {"registry_server": None},
               {"exposure": OSError("x")}, {"gate": RuntimeError("y")}):
        runner = FakeRunner(**kw)
        g = G.IntentGate(runner)
        g.evaluate(l5_intent())
        g.evaluate(ctl_intent())
        assert runner.executor.calls == [], (kw, runner.executor.calls)


def test_the_gate_holds_no_copy_of_any_limit_or_latch():
    """Every safety fact comes from the runner's own objects. A gate with
    its own notion of the rules is a gate that can disagree with the one
    that actually refuses."""
    g = G.IntentGate(FakeRunner())
    assert list(vars(g)) == ["runner"]


# ================== THE RUNNER'S OWN REFUSALS ARE BYTE-IDENTICAL

ROW = {"slug": "btc-updown-15m-1792429200", "timestamp": 1792429300,
       "side": "BUY", "outcome": "Up", "price": "0.41", "size": "20",
       "transactionHash": "0x" + "a" * 64, "conditionId": "0x" + "b" * 64}


def _runner(tmp_path, monkeypatch, **kw):
    from src import l5_pmus_live_runner as R
    monkeypatch.delenv("L5_ORDER_REGISTRY_PORT", raising=False)
    book = mock.MagicMock()
    book.snapshot.return_value = {"bid": "0.40", "ask": "0.42",
                                  "stream_age_s": 0.02,
                                  "market_state": "MARKET_STATE_OPEN",
                                  "source": "authenticated_ws"}
    book.resub_due.return_value = []
    book.stall_due.return_value = False
    book.trades.return_value = []
    return R.Runner(root=str(tmp_path / "live"), book=book,
                    feed=lambda: [], **kw)


def _emitted(monkeypatch):
    from src import l5_pmus_live_runner as R
    import json as _json
    out = []
    monkeypatch.setattr(R, "emit",
                        lambda k, d: out.append(
                            (k, _json.loads(_json.dumps(d, default=str)))))
    return out


@pytest.mark.parametrize("which,layer,reason", [
    ("window", "PMUS_TRANSLATION", "NOT_CURRENT_WINDOW"),
    ("sell", "SAFETY_OVERLAY", "SELL_NOT_IN_PROBE_SCOPE"),
    ("halt", "SAFETY_OVERLAY", "OPERATOR_HALT"),
])
def test_the_runners_three_refusals_keep_their_exact_layer_and_reason(
        tmp_path, monkeypatch, which, layer, reason):
    """THE REGRESSION THAT MATTERS. The tape, the per-key stats and every
    dashboard read these two strings. If the refactor changed either one,
    historical records stop being comparable with new ones."""
    from src import l5_pmus_live_runner as R
    runner = _runner(tmp_path, monkeypatch)
    row = dict(ROW)
    if which == "window":
        row["slug"] = "btc-updown-15m-1700000000"      # a different window
        runner.roll(1792429300.0)
    elif which == "sell":
        runner.roll(1792429300.0)
        row["side"] = "SELL"
    else:
        runner.roll(1792429300.0)
        monkeypatch.setattr(runner, "halted", lambda: True)
    out = _emitted(monkeypatch)
    runner.handle(row, 1792429301.0)
    dec = [d for k, d in out if k == "L5_LIVE_DECISION"]
    assert dec, [k for k, _ in out]
    assert dec[0]["key"] == "%s:%s" % (layer, reason), dec[0]
    assert runner.stats["%s:%s" % (layer, reason)] == 1


def test_the_refusal_record_still_carries_L5_NATIVE_and_the_action_key(
        tmp_path, monkeypatch):
    """Provenance of the OBSERVED action is unchanged: one L5_NATIVE block,
    produced by the money path as before."""
    runner = _runner(tmp_path, monkeypatch)
    runner.roll(1792429300.0)
    row = dict(ROW, side="SELL")
    out = _emitted(monkeypatch)
    runner.handle(row, 1792429301.0)
    import json as _json
    tape = [_json.loads(l) for l in open(runner.tape, encoding="utf-8")]
    rec = tape[-1]["record"]
    assert rec["refused_reason"] == "SELL_NOT_IN_PROBE_SCOPE"
    assert rec["refused_by_layer"] == "SAFETY_OVERLAY"
    assert rec["L5_NATIVE"] is not None
    assert rec["action_key"]
    # NEW, additive: which gate, and whose intent it was
    assert rec["refused_by_gate"] == "SELL_IN_PROBE_SCOPE"
    assert rec["intent_provenance"] == "L5_FOLLOWED"


def test_an_admissible_l5_action_still_reaches_the_executor_unchanged(
        tmp_path, monkeypatch):
    """The positive control at runner level: the same `ex.handle(row, {},
    market=, window=, open_markets=)` call, with the same arguments."""
    runner = _runner(tmp_path, monkeypatch)
    runner.roll(1792429300.0)
    seen = []
    real = runner.executor.handle

    def spy(row, authenticated, *, market, window, open_markets=()):
        seen.append((market, window, tuple(open_markets),
                     authenticated))
        return real(row, authenticated, market=market, window=window,
                    open_markets=open_markets)
    runner.executor.handle = spy
    _emitted(monkeypatch)
    runner.handle(dict(ROW), 1792429301.0)
    assert len(seen) == 1, seen
    market, window, open_markets, authenticated = seen[0]
    assert market == runner.transport.market_slug
    assert window == str(runner.ws)
    assert authenticated == {}
    assert open_markets == ()


def test_the_runner_builds_an_l5_intent_and_never_a_controller_one(
        tmp_path, monkeypatch):
    """The money path must not be able to emit a controller-provenance
    intent: that is the mislabelling this refactor exists to prevent."""
    import inspect
    from src import l5_pmus_live_runner as R
    src = inspect.getsource(R.Runner.handle)
    assert "GATE.L5_FOLLOWED" in src
    assert "CONTROLLER_GENERATED" not in src
