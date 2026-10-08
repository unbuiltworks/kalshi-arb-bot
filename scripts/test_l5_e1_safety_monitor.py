"""Tests for the independent read-only E1 safety monitor.

The detections are a PURE function of venue rows plus a clock, so they are
tested without a venue and without waiting: `evaluate` takes the rows and
returns the alerts. The sink is tested against a fake HTTP layer, so
delivery and non-delivery are both proven rather than assumed.
"""
from __future__ import annotations

import json
import os

import pytest

from scripts import l5_e1_safety_monitor as M

MKT = "cpc-btc-updown-15m-2026-10-08-1900z"
OTHER = "cpc-btc-updown-15m-2026-10-08-1915z"
T0 = 1_800_000_000.0


def order(oid, *, slug=MKT, state="ORDER_STATE_NEW", qty="0.13"):
    return {"id": oid, "marketSlug": slug, "state": state,
            "quantity": qty, "leavesQuantity": qty, "cumQuantity": "0",
            "outcomeSide": "OUTCOME_SIDE_YES"}


def pos(qty, *, slug=MKT):
    return {"marketSlug": slug, "quantity": qty,
            "outcomeSide": "OUTCOME_SIDE_YES"}


def ev(**kw):
    base = dict(open_rows=[], position_rows=[], market=MKT,
                known_ids=["E1-A"], now=T0, handover_unix=None,
                executor_deadline_unix=None)
    base.update(kw)
    return M.evaluate(**base)


def kinds(out):
    return [a["kind"] for a in out["alerts"]]


# ===================== 3. ORDER / POSITION DETECTION =====================

def test_a_resting_order_before_handover_is_not_yet_an_alert():
    out = ev(open_rows=[order("E1-A")], handover_unix=T0 + 60)
    assert out["open_in_market"] == 1 and out["ours_open"] == 1
    assert kinds(out) == []


def test_an_order_open_AT_handover_is_CRITICAL():
    out = ev(open_rows=[order("E1-A")], handover_unix=T0)
    assert "A1_OUTSTANDING_ORDER_AT_OR_AFTER_HANDOVER" in kinds(out)
    a = out["alerts"][0]
    assert a["severity"] == "CRITICAL"
    assert a["our_open_ids"] == ["E1-A"]
    assert a["monitor_will_not_cancel_anything"] is True


def test_an_order_open_AFTER_handover_is_CRITICAL():
    out = ev(open_rows=[order("E1-A")], handover_unix=T0 - 1)
    assert "A1_OUTSTANDING_ORDER_AT_OR_AFTER_HANDOVER" in kinds(out)


def test_a_position_in_the_probe_market_is_reported():
    out = ev(position_rows=[pos("0.13")])
    assert "A3_POSITION_IN_THE_PROBE_MARKET" in kinds(out)
    assert out["positions_in_market"] == 1


def test_a_zero_position_is_not_an_alert():
    out = ev(position_rows=[pos("0")])
    assert out["positions_in_market"] == 0
    assert kinds(out) == []


def test_another_market_is_ignored_entirely():
    out = ev(open_rows=[order("X", slug=OTHER)],
             position_rows=[pos("9.0", slug=OTHER)], handover_unix=T0 - 1)
    assert out["open_in_market"] == 0 and out["positions_in_market"] == 0
    assert kinds(out) == []


def test_a_terminal_order_is_not_outstanding():
    for st in ("ORDER_STATE_CANCELED", "ORDER_STATE_FILLED",
               "ORDER_STATE_REJECTED"):
        out = ev(open_rows=[order("E1-A", state=st)], handover_unix=T0 - 1)
        assert out["open_in_market"] == 0, st
        assert kinds(out) == [], st


def test_a_partially_filled_order_IS_outstanding():
    out = ev(open_rows=[order("E1-A", state="ORDER_STATE_PARTIALLY_FILLED")],
             handover_unix=T0 - 1)
    assert out["open_in_market"] == 1
    assert "A1_OUTSTANDING_ORDER_AT_OR_AFTER_HANDOVER" in kinds(out)


# ============== 5. SHARED ACCOUNT: NEVER OURS, NEVER CANCELLED ==========

def test_an_unattributed_order_is_POTENTIALLY_RELEVANT_and_never_ours():
    out = ev(open_rows=[order("SOMEONE-ELSE")], handover_unix=None)
    a = [x for x in out["alerts"]
         if x["kind"] == "A5_UNATTRIBUTED_ORDER_IN_THE_PROBE_MARKET"]
    assert a, kinds(out)
    assert a[0]["ids"] == ["SOMEONE-ELSE"]
    assert a[0]["never_cancelled"] is True
    assert out["ours_open"] == 0
    assert out["unattributed_open"] == 1


def test_an_unattributed_order_at_handover_still_escalates_but_stays_theirs():
    out = ev(open_rows=[order("SOMEONE-ELSE")], handover_unix=T0 - 1)
    crit = [x for x in out["alerts"]
            if x["kind"] == "A1_OUTSTANDING_ORDER_AT_OR_AFTER_HANDOVER"]
    assert crit
    assert crit[0]["our_open_ids"] == []
    assert crit[0]["unattributed_open_ids"] == ["SOMEONE-ELSE"]


def test_with_no_known_ids_nothing_is_claimed_as_ours():
    out = ev(open_rows=[order("E1-A")], known_ids=[], handover_unix=T0 - 1)
    assert out["ours_open"] == 0 and out["unattributed_open"] == 1


# ===================== 2. EXECUTOR-FAILURE DETECTION ====================

def test_executor_presumed_failed_when_the_deadline_passes_with_exposure():
    out = ev(open_rows=[order("E1-A")], executor_deadline_unix=T0)
    a = [x for x in out["alerts"]
         if x["kind"] == "A2_EXECUTOR_PRESUMED_FAILED_WITH_EXPOSURE_OPEN"]
    assert a and a[0]["severity"] == "CRITICAL"


def test_executor_presumed_failed_on_an_open_POSITION_too():
    """A dead process with a FILLED leg is the 1215z failure, and it has no
    resting order to notice. Inventory alone must raise it."""
    out = ev(position_rows=[pos("0.13")], executor_deadline_unix=T0)
    assert "A2_EXECUTOR_PRESUMED_FAILED_WITH_EXPOSURE_OPEN" in kinds(out)


def test_a_passed_deadline_with_NO_exposure_is_a_clean_finish():
    out = ev(executor_deadline_unix=T0 - 100)
    assert kinds(out) == []


# ======================== 4. MONITORING STALENESS =======================

def test_a_failed_poll_reports_not_ok_and_names_the_path():
    class Broken(object):
        @staticmethod
        def request(method, path, body=None, base=None):
            raise OSError("venue unreachable")

    p = M.poll_once(Broken())
    assert p["ok"] is False
    assert "OPEN:OSError" in p["errors"]
    assert "POSITIONS:OSError" in p["errors"]
    assert p["open_rows"] == [] and p["position_rows"] == []


def test_a_partial_failure_is_still_not_ok():
    class Half(object):
        @staticmethod
        def request(method, path, body=None, base=None):
            if path == M.OPEN_PATH:
                return []
            raise OSError("positions down")

    p = M.poll_once(Half())
    assert p["ok"] is False and p["errors"] == ["POSITIONS:OSError"]


def test_the_stale_threshold_is_small_enough_to_matter_in_one_window():
    # a 15-minute window at a 10s poll is ~90 polls; going blind must be
    # reported in seconds, not minutes.
    assert M.STALE_AFTER_FAILURES <= 3
    assert M.DEFAULT_POLL_S <= 15.0


# ============================ ALERT DELIVERY ============================

def test_without_a_webhook_alerts_are_LOG_ONLY_and_SAY_SO(capsys):
    sink = M.AlertSink(None)
    rec = sink.send("A1_TEST", {"x": 1})
    assert rec["delivered"] is False
    assert rec["why"] == "NO_WEBHOOK_CONFIGURED"
    assert "MISSING_SETUP" in json.dumps(sink.report())
    assert sink.report()["MISSING_SETUP"]
    out = capsys.readouterr().out
    assert "E1_ALERT" in out and "A1_TEST" in out


def test_a_webhook_delivery_reports_the_HTTP_STATUS(monkeypatch, capsys):
    sent = {}

    class Resp(object):
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, context=None, timeout=None):
        sent["url"] = req.full_url
        sent["method"] = req.method
        sent["body"] = json.loads(req.data.decode())
        return Resp()

    monkeypatch.setattr(M.urllib.request, "urlopen", fake_urlopen)
    sink = M.AlertSink("https://hooks.example.com/abc")
    rec = sink.send("A1_TEST", {"market": MKT})
    assert rec == {"alert": "A1_TEST", "delivered": True, "status": 204}
    assert sent["method"] == "POST"
    assert sent["body"]["alert"] == "A1_TEST"
    assert "E1_ALERT_DELIVERY" in capsys.readouterr().out


def test_a_failed_delivery_is_reported_as_UNDELIVERED(monkeypatch):
    def boom(req, context=None, timeout=None):
        raise OSError("dns")

    monkeypatch.setattr(M.urllib.request, "urlopen", boom)
    sink = M.AlertSink("https://hooks.example.com/abc")
    rec = sink.send("A1_TEST", {})
    assert rec["delivered"] is False and rec["why"] == "OSError"


def test_the_alert_path_can_never_become_a_venue_path():
    host = M.AUTH.API.split("//")[-1].split("/")[0]
    with pytest.raises(SystemExit):
        M.AlertSink("https://%s/v1/orders" % host)


def test_a_plain_http_webhook_is_refused():
    with pytest.raises(SystemExit):
        M.AlertSink("http://hooks.example.com/abc")


def test_no_credential_is_ever_placed_in_an_alert(monkeypatch):
    seen = {}

    class Resp(object):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setenv("PMUS_API_SECRET_KEY", "SUPER-SECRET-VALUE")
    monkeypatch.setenv("PMUS_API_KEY_ID", "KEY-ID-VALUE")

    def fake_urlopen(req, context=None, timeout=None):
        seen["body"] = req.data.decode()
        seen["headers"] = dict(req.headers)
        return Resp()

    monkeypatch.setattr(M.urllib.request, "urlopen", fake_urlopen)
    M.AlertSink("https://hooks.example.com/abc").send(
        "A1_TEST", {"market": MKT, "our_open_ids": ["E1-A"]})
    assert "SUPER-SECRET-VALUE" not in seen["body"]
    assert "KEY-ID-VALUE" not in seen["body"]
    assert "SUPER-SECRET-VALUE" not in json.dumps(seen["headers"])


# ======================== INDEPENDENCE / CAPABILITY =====================

def test_the_monitor_REFUSES_to_run_beside_arming_values(monkeypatch):
    for k in M.FORBIDDEN_ARMING_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PMUS_E1_LIVE", "YES")
    with pytest.raises(SystemExit) as exc:
        M.assert_not_armed()
    assert "PMUS_E1_LIVE" in str(exc.value)


def test_a_clean_container_passes_the_independence_assertion(monkeypatch):
    for k in M.FORBIDDEN_ARMING_KEYS:
        monkeypatch.delenv(k, raising=False)
    assert M.assert_not_armed()["arming_keys_present"] == []


def test_the_only_transport_REFUSES_a_post():
    with pytest.raises(PermissionError):
        M.AUTH.ReadOnlyTransport().request("POST", "/v1/orders", {"a": 1})
    for method in ("PUT", "PATCH", "DELETE"):
        with pytest.raises(PermissionError):
            M.AUTH.ReadOnlyTransport().request(method, "/v1/order/x/cancel")


def test_the_module_names_no_cancel_or_submit_route():
    src = open(M.__file__.replace(".pyc", ".py"), encoding="utf8").read()
    body = src.split('"""', 2)[2]          # past the module docstring
    for forbidden in ("/cancel", "/v1/orders/open/cancel", "SUBMIT_PATH"):
        assert forbidden not in body, forbidden


def test_the_monitor_reads_exactly_two_venue_paths():
    assert M.OPEN_PATH == "/v1/orders/open"
    assert M.POSITIONS_PATH == "/v1/portfolio/positions"


def test_the_monitor_refuses_without_a_market(monkeypatch, capsys):
    for k in M.FORBIDDEN_ARMING_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv(M.MARKET_ENV, raising=False)
    assert M.main([]) == 2
    assert "E1_MONITOR_REFUSED" in capsys.readouterr().out


def test_the_monitor_loop_keeps_running_and_never_writes(monkeypatch,
                                                         capsys):
    """THE INDEPENDENCE PROPERTY, in-process: the monitor polls, alerts and
    finishes on ITS OWN clock. Nothing about the executor is consulted, so
    an executor that has stopped changes nothing here."""
    for k in M.FORBIDDEN_ARMING_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(M.MARKET_ENV, MKT)
    monkeypatch.setenv(M.KNOWN_IDS_ENV, "E1-A")
    monkeypatch.setenv(M.HANDOVER_ENV, "1")        # already past
    monkeypatch.setenv(M.POLL_ENV, "0.01")
    monkeypatch.setenv(M.RUNTIME_ENV, "0.05")
    monkeypatch.delenv(M.WEBHOOK_ENV, raising=False)

    calls = []

    class Reader(object):
        @staticmethod
        def request(method, path, body=None, base=None):
            calls.append((method, path))
            assert method == "GET", "the monitor must only ever GET"
            if path == M.OPEN_PATH:
                return [order("E1-A")]
            return []

    # the POLLING seam only. `assert_cannot_mutate` still exercises the
    # real ReadOnlyTransport, so a fake reader cannot fake the capability
    # proof -- which is the one thing this test must not be able to do.
    monkeypatch.setattr(M, "_reader", lambda: Reader())
    assert M.main([]) == 0
    out = capsys.readouterr().out
    assert "E1_MONITOR_BOOT" in out
    assert "A1_OUTSTANDING_ORDER_AT_OR_AFTER_HANDOVER" in out
    assert "E1_MONITOR_END" in out
    assert calls and all(m == "GET" for m, _ in calls)
    assert '"orders_cancelled_by_this_monitor": 0' in out


def test_the_self_test_emits_a_labelled_synthetic_alert(monkeypatch,
                                                       capsys):
    for k in M.FORBIDDEN_ARMING_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(M.MARKET_ENV, MKT)
    monkeypatch.setenv(M.SELFTEST_ENV, "YES")
    monkeypatch.setenv(M.POLL_ENV, "0.01")
    monkeypatch.setenv(M.RUNTIME_ENV, "0.01")
    monkeypatch.delenv(M.WEBHOOK_ENV, raising=False)
    monkeypatch.setattr(M, "_reader",
                        lambda: type("R", (), {
                            "request": staticmethod(
                                lambda *a, **k: [])})())
    assert M.main([]) == 0
    out = capsys.readouterr().out
    assert "A0_SELF_TEST_SYNTHETIC" in out
    assert '"IS_A_TEST": true' in out
    assert "E1_MONITOR_SELF_TEST_RESULT" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
