"""P1: the private-WS observer records and correlates. It never decides."""
import ast
import pathlib

import pytest

from src import pmus_ws_lifecycle_observer as O

SRC = pathlib.Path("src/pmus_ws_lifecycle_observer.py")


def snap(orders, eof=True):
    return {"subscriptionType": "SUBSCRIPTION_TYPE_ORDER_SNAPSHOT",
            "orderSubscriptionSnapshot": {"orders": orders, "eof": eof}}


def order(oid, state="ORDER_STATE_WORKING", cum="0", px=None, comm=None):
    r = {"id": oid, "state": state, "cumQuantity": cum, "quantity": "10"}
    if px is not None:
        r["avgPx"] = {"value": px, "currency": "USD"}
    if comm is not None:
        r["commissionNotionalTotalCollected"] = {"value": comm}
    return r


# ---- it cannot act -----------------------------------------------------

def test_it_imports_no_money_path_no_mandate_no_engine():
    tree = ast.parse(SRC.read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module or "")
    for m in mods:
        for banned in ("money_path", "live_mandate", "native_engine",
                       "native_venue", "l5_pmus_transport", "q1_executor"):
            assert banned not in m, m


def test_it_contains_nothing_that_could_submit_or_book():
    src = SRC.read_text()
    for bad in ("submit", "on_fill", "mark_terminal", "release_terminal",
                "/v1/orders", "POST"):
        assert bad not in src, bad


def test_only_subscribe_and_unsubscribe_may_be_sent():
    assert O.ALLOWED_KEYS == {"subscribe", "unsubscribe"}
    with pytest.raises(RuntimeError):
        O.guard_outbound({"order": {}})
    assert O.guard_outbound({"subscribe": {}}) == {"subscribe": {}}


def test_subscribe_frames_ask_for_snapshot_then_stream_then_positions():
    fr = O.subscribe_frames()
    got = [f["subscribe"]["subscription_type"] for f in fr]
    assert got == [O.NAME_ORDER_SNAPSHOT, O.NAME_ORDER, O.NAME_POSITION]
    for f in fr:
        O.guard_outbound(f)


# ---- never infer a fill from disappearance -----------------------------

def test_an_order_absent_from_a_snapshot_is_not_a_fill():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    o.on_ws(snap([order("A"), order("B")]))
    o.on_ws(snap([order("A")]))
    assert [v["order_id"] for v in o.vanished] == ["B"]
    v = o.vanished[0]
    assert v["classification"] == "VANISHED_FROM_SNAPSHOT"
    assert "NOT a fill" in v["note"]
    rep = o.report()
    # and it is not counted as a fill anywhere
    assert rep["agreement"]["cum_quantity"]["agree"] == 0


# ---- correlation -------------------------------------------------------

def test_agreement_when_ws_and_rest_match():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    o.on_ws(snap([order("A", "ORDER_STATE_FILLED", "10", "0.46", "-0.03")]))
    o.on_rest("A", {"state": "ORDER_STATE_FILLED", "filled_qty": "10",
                    "price_own": "0.46", "commission": "-0.03"})
    r = o.report()
    for f in ("state", "cum_quantity", "avg_px", "commission"):
        assert r["agreement"][f]["agree"] == 1, f
        assert r["agreement"][f]["disagree"] == 0
    assert o.disagreements() == []


def test_disagreement_is_surfaced_per_field():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    o.on_ws(snap([order("A", "ORDER_STATE_FILLED", "10", "0.46")]))
    o.on_rest("A", {"state": "ORDER_STATE_FILLED", "filled_qty": "7",
                    "price_own": "0.46"})
    d = o.disagreements()
    assert len(d) == 1
    assert d[0]["cum_quantity"] == {"ws": "10", "rest": "7", "agree": False}
    assert d[0]["state"]["agree"] is True


def test_numeric_fields_compare_by_value_not_string():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    o.on_ws(snap([order("A", "ORDER_STATE_FILLED", "10.0000", "0.4600")]))
    o.on_rest("A", {"state": "ORDER_STATE_FILLED", "filled_qty": "10",
                    "price_own": "0.46"})
    assert o.disagreements() == []


def test_missing_on_one_side_is_not_comparable_not_a_mismatch():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    o.on_ws(snap([order("A", "ORDER_STATE_WORKING", "0")]))
    o.on_rest("A", {"state": "ORDER_STATE_WORKING"})
    r = o.report()
    assert r["agreement"]["avg_px"]["disagree"] == 0
    assert r["agreement"]["avg_px"]["not_comparable"] == 1


def test_ws_only_and_rest_only_orders_are_named():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    o.on_ws(snap([order("WSONLY")]))
    o.on_rest("RESTONLY", {"state": "ORDER_STATE_FILLED"})
    r = o.report()
    assert r["ws_only"] == ["WSONLY"]
    assert r["rest_only"] == ["RESTONLY"]


def test_latency_is_measured_from_the_first_ws_sighting():
    t = [100.0]
    o = O.LifecycleObserver(clock=lambda: t[0])
    o.on_ws(snap([order("A")]))
    t[0] = 100.25
    o.on_ws(snap([order("A", cum="5")]))
    t[0] = 100.5
    o.on_rest("A", {"state": "ORDER_STATE_WORKING", "filled_qty": "5"})
    assert o.correlate()[0]["ws_to_rest_latency_s"] == 0.5


# ---- stream hygiene ----------------------------------------------------

def test_duplicates_are_counted():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    o.on_ws(snap([order("A", "ORDER_STATE_WORKING", "5")]))
    o.on_ws(snap([order("A", "ORDER_STATE_WORKING", "5")]))
    assert o.report()["duplicates"] == 1


def test_cumulative_quantity_going_backwards_is_flagged_not_applied():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    o.on_ws(snap([order("A", "ORDER_STATE_WORKING", "8")]))
    rows = o.on_ws(snap([order("A", "ORDER_STATE_WORKING", "3")]))
    assert rows[0]["anomaly"] == "CUM_QUANTITY_WENT_BACKWARDS"
    assert o.report()["out_of_order"] == 1


def test_reconnects_are_counted():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    o.on_reconnect()
    assert o.report()["reconnects"] == 1


def test_an_empty_snapshot_is_handled():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    assert o.on_ws(snap([])) == []
    assert o.report()["snapshots"] == 1


def test_an_unrecognised_shape_does_not_raise():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    for junk in (None, {}, [], {"weird": {"nested": [1, 2]}}, "text"):
        o.on_ws(junk)
    assert o.report()["ws_events"] == 0


def test_rest_is_declared_authoritative_in_the_report():
    o = O.LifecycleObserver(clock=lambda: 100.0)
    assert o.report()["authority"] == "REST"


# ---- R6 wiring: the observer runs in production but holds no authority --

def test_the_only_production_references_are_observation_wiring():
    import pathlib
    allowed = {"pmus_ws_lifecycle_observer.py": "the module itself",
               "pmus_native_money_path.py": "holds the on_rest hook",
               "l5_pmus_live_oneshot.py": "constructs, starts, journals, stops",
               #: the $0.01 schema probe exists to OBSERVE this socket: it uses
               #: the module's subscribe frames and outbound guard and nothing
               #: else. test_no_observer_call_site_can_reach_execution is
               #: extended to cover both files, so the safety property is
               #: enforced here rather than merely waived.
               "pmus_private_schema_probe.py": "imports the module for arming "
                                               "docs only",
               "pmus_schema_probe_run.py": "subscribes and guards the private "
                                           "socket; observation only"}
    hits = []
    for p in list(pathlib.Path("src").glob("*.py")) + \
            list(pathlib.Path("scripts").glob("*.py")):
        if p.name in allowed:
            continue
        if "pmus_ws_lifecycle_observer" in p.read_text():
            hits.append(p.name)
    assert hits == [], "unexpected production reference: %s" % (hits,)


def test_no_observer_call_site_can_reach_execution():
    import pathlib
    forbidden = ("submit", "cancel", "on_fill", "mark_terminal",
                 "release_terminal", "registry.record", "_post(")
    for f in ("src/pmus_native_money_path.py",
              "scripts/l5_pmus_live_oneshot.py",
              #: the schema probe is held to the same rule: no line that
              #: touches the private observer may also submit or cancel.
              "src/pmus_private_schema_probe.py",
              "scripts/pmus_schema_probe_run.py"):
        for i, line in enumerate(pathlib.Path(f).read_text().split("\n"), 1):
            if "ws_observer" not in line and "lifecycle_observer" not in line \
                    and " PV." not in line and "PV." not in line:
                continue
            for bad in forbidden:
                assert bad not in line, "%s:%d %r" % (f, i, line.strip())


def test_the_observer_receives_the_rest_verdict_unchanged():
    """`on_rest` is handed the authoritative verdict; the verdict returned to
    the caller must be byte-identical to what REST gave."""
    import inspect
    from src import pmus_native_money_path as MP
    src = inspect.getsource(MP.NativeMoneyPath._status_once)
    assert "self.ws_observer.on_rest(key, v)" in src
    # the verdict is not rebound after the observer sees it
    after = src.split("on_rest(key, v)")[1]
    assert "v =" not in after


def test_observation_failure_cannot_raise_into_the_trading_path():
    import inspect
    from src import pmus_native_money_path as MP
    for fn in (MP.NativeMoneyPath._status_once,
               MP.NativeMoneyPath.on_book_state):
        src = inspect.getsource(fn)
        for hook in ("ws_observer.on_rest", "decision_shadow.compare"):
            if hook not in src:
                continue
            seg = src.split(hook)[0]
            assert seg.rstrip().endswith(("try:",)) or "try:" in seg[-400:], hook


def test_the_private_stream_sends_only_subscribe_frames():
    o = O.LifecycleObserver()
    st = O.PrivateStream(o)
    assert st.health()["authority"].startswith("NONE")
    for f in O.subscribe_frames():
        O.guard_outbound(f)
    with pytest.raises(RuntimeError):
        O.guard_outbound({"order": {}})


def test_the_private_stream_holds_no_reference_to_the_money_path():
    import inspect
    src = inspect.getsource(O.PrivateStream)
    for bad in ("money_path", "registry", "eng.", "submit", "mandate"):
        assert bad not in src, bad
