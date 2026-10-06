"""LIVE-L5-EXEC-1-X: the frozen derivation, the durable latch, the four routes.

The point of these tests is that the dangerous properties are the ones
asserted: that the economics did not move, that a second submission cannot
happen even across a restart, and that the order path cannot be pointed
anywhere else.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from analysis.l5.live_exec import spec as E
from analysis.l5.live_exec.spec import SPEC as EXEC1_SPEC
from analysis.l5.live_exec2 import latch as L
from analysis.l5.live_exec2 import orders as O
from analysis.l5.live_exec2 import spec as X

ARM = ("PMUS_EXEC1_LIVE", "PMUS_EXEC1_SESSION_ID", "PMUS_EXEC1_ARM",
       "PMUS_EXEC1_GATE_EVIDENCE")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Every test gets its own latch directory and a clean, DISARMED env."""
    monkeypatch.setattr(X, "LATCH_DIR", str(tmp_path / "exec1x"))
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "exec1x"))
    for k in ARM:
        monkeypatch.delenv(k, raising=False)
    yield


# ------------------------------------------------------------ frozen identity

def test_fingerprint_is_the_hardcoded_literal():
    assert X.fingerprint() == X.EXEC1X_FINGERPRINT
    assert X.EXEC1X_FINGERPRINT == (
        "d2f322760699f22e50a16975179a484b48c1523e3a667a77579ed6c5dd3a6c79")


def test_assert_frozen_is_not_vacuous(monkeypatch):
    monkeypatch.setitem(X.SPEC, "probe", "TAMPERED")
    with pytest.raises(SystemExit):
        X.assert_frozen()


def test_exec1_must_also_still_be_frozen(monkeypatch):
    monkeypatch.setitem(EXEC1_SPEC, "spec_version", 99)
    with pytest.raises(SystemExit):
        X.assert_frozen()


def test_fingerprint_differs_from_exec1():
    assert X.EXEC1X_FINGERPRINT != E.EXEC1_FINGERPRINT


def test_spec_is_derived_not_retyped():
    """Every key EXEC-1 has, this has -- unless DIFF changed it."""
    touched = {d.split(".")[0] for d in X.DIFF}
    for key, value in EXEC1_SPEC.items():
        if key in touched:
            continue
        assert X.SPEC[key] == value, key


# -------------------------------------------------------- the money cannot move

def test_loss_envelope_unchanged():
    assert X.SPEC["size"]["MAX_POSSIBLE_LOSS_USD"] == "0.0099"
    assert X.SPEC["size"]["quantity_shares"] == "0.01"
    assert X.SPEC["size"]["max_entry_price"] == "0.99"


@pytest.mark.parametrize("dotted,want", X.INVARIANTS)
def test_each_invariant_survives_the_derivation(dotted, want):
    assert X._get(X.SPEC, dotted) == want


def test_envelope_guard_catches_a_quantity_change():
    bad = json.loads(json.dumps(X.SPEC))
    bad["size"]["quantity_shares"] = "1.00"
    with pytest.raises(SystemExit):
        X.assert_envelope_preserved(bad)


def test_envelope_guard_catches_a_second_order():
    bad = json.loads(json.dumps(X.SPEC))
    bad["orders"]["max_orders_this_experiment"] = 2
    with pytest.raises(SystemExit):
        X.assert_envelope_preserved(bad)


def test_diff_report_names_only_the_two_changes_plus_provenance():
    rep = X.diff_report()
    assert rep["keys_removed"] == []
    assert set(rep["keys_changed"]) == {
        "probe", "spec_version", "behaviour.no_second_order",
        "deadman.mechanism"}
    assert "latch" in rep["keys_added"]
    assert "mutation_path" in rep["keys_added"]
    assert rep["max_possible_loss_usd_unchanged"] == "0.0099"


# ------------------------------------------------------------- durable latch

def test_latch_requires_a_session_id():
    with pytest.raises(L.LatchUnavailable):
        L.consume(slug="s", side="UP", price="0.5", qty="0.01")


def test_consume_then_refuse(monkeypatch):
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "sid-a")
    rec = L.consume(slug="s", side="UP", price="0.5", qty="0.01")
    assert rec["NOT_RELEASED_ON_FAILURE"] is True
    with pytest.raises(L.LatchSpent):
        L.consume(slug="s", side="UP", price="0.5", qty="0.01")


def test_latch_survives_a_simulated_restart(monkeypatch):
    """A fresh reader with no in-memory state sees it spent."""
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "sid-b")
    L.consume(slug="s", side="UP", price="0.5", qty="0.01")
    import importlib
    fresh = importlib.reload(L)
    monkeypatch.setattr(fresh, "LATCH_DIR", L.LATCH_DIR)
    try:
        assert fresh.state()["spent"] is True
        with pytest.raises(fresh.LatchSpent):
            fresh.consume(slug="s", side="UP", price="0.5", qty="0.01")
    finally:
        importlib.reload(L)


def test_a_different_session_id_gets_its_own_latch(monkeypatch):
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "sid-c")
    L.consume(slug="s", side="UP", price="0.5", qty="0.01")
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "sid-d")
    assert L.state()["spent"] is False
    L.consume(slug="s", side="UP", price="0.5", qty="0.01")


def test_sanitised_ids_cannot_collide(monkeypatch):
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "a/b")
    one = L.latch_path()
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "a:b")
    assert L.latch_path() != one


def test_state_never_creates_a_latch(monkeypatch):
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "sid-e")
    for _ in range(3):
        assert L.state()["spent"] is False
    assert not os.path.exists(L.latch_path())


def test_unwritable_store_fails_closed(monkeypatch):
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "sid-f")
    monkeypatch.setattr(L, "LATCH_DIR", "/proc/definitely/not/writable")
    with pytest.raises(L.LatchUnavailable):
        L.consume(slug="s", side="UP", price="0.5", qty="0.01")


def test_latch_module_has_no_release_or_delete():
    src = open(L.__file__, encoding="utf8").read()
    assert "os.unlink" not in src and "os.remove" not in src
    assert not [n for n in dir(L)
                if n.startswith(("release", "clear", "reset", "delete"))]


# --------------------------------------------------------- the four routes

@pytest.mark.parametrize("method,path", [
    ("POST", "/v1/orders/cancel-all"), ("POST", "/v1/positions"),
    ("POST", "/v1/wallet/withdraw"), ("DELETE", "/v1/orders"),
    ("PUT", "/v1/orders"), ("PATCH", "/v1/order/x"),
    ("POST", "/v1/order/x/amend"), ("POST", "/v1/orders/open/cancel/all"),
    ("POST", "/v1/order/preview/x"), ("POST", "/"),
])
def test_foreign_routes_are_refused(method, path):
    with pytest.raises(O.MutationRefused):
        O.BoundedOrderTransport().request(method, path, {})


@pytest.mark.parametrize("path", [O.PREVIEW_PATH, O.SUBMIT_PATH,
                                  O.DEADMAN_PATH, "/v1/order/abc/cancel"])
def test_the_four_routes_are_permitted(path):
    assert O.BoundedOrderTransport()._allowed("POST", path)


def test_second_submit_in_one_process_refused(monkeypatch):
    t = O.BoundedOrderTransport()
    sent = []
    # intercept signing and the network, keeping the counter under test
    monkeypatch.setattr(O.A, "signed_headers", lambda m, p: {})
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("no network in tests")))
    for i in range(2):
        try:
            t.request("POST", O.SUBMIT_PATH, {})
        # MutationRefused subclasses RuntimeError, so it must be caught FIRST
        # or the network stub's RuntimeError handler swallows it.
        except O.MutationRefused as exc:
            assert "second POST" in str(exc)
            assert i == 1
            return
        except RuntimeError:
            sent.append(i)
    pytest.fail("a second submission was not refused")


# ------------------------------------------------- DISARMED on the real path

def _fresh_kw(**over):
    kw = dict(slug="cpc-btc-updown-15m-2026-10-06-1500z", outcome_side="UP",
              price="0.50", qty=D("0.01"), min_qty="0.01", tick="0.01",
              book_bid="0.50", book_ask="0.51", book_t=1000.0, now=1000.5)
    kw.update(over)
    return kw


def test_preflight_refuses_while_disarmed():
    v = O.preflight_submission(**_fresh_kw())
    assert v["ok"] is False
    assert "DISARMED" in v["refusals"]


def _arm(monkeypatch, slug):
    monkeypatch.setenv("PMUS_EXEC1_LIVE", "YES")
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "sid-armed")
    monkeypatch.setenv("PMUS_EXEC1_ARM", "sid-armed")
    monkeypatch.setenv("PMUS_EXEC1_GATE_EVIDENCE", slug)


def test_armed_preflight_passes_so_the_refusals_are_real(monkeypatch):
    kw = _fresh_kw()
    _arm(monkeypatch, kw["slug"])
    assert O.preflight_submission(**kw)["ok"] is True


@pytest.mark.parametrize("over,reason", [
    ({"qty": D("0.02")}, "QTY_NOT_VENUE_MINIMUM"),
    ({"price": "1.00"}, "PRICE_ABOVE_CEILING"),
    ({"price": "0.515"}, "PRICE_OFF_GRID"),
    ({"min_qty": None}, "MIN_QTY_ABSENT"),
    ({"tick": None}, "TICK_ABSENT"),
    ({"min_qty": "1"}, "MIN_QTY_NOT_LESS_THAN_1"),
    ({"book_t": 1000.0, "now": 1003.0}, "BOOK_NOT_EXECUTABLE"),
    ({"book_ask": None}, "BOOK_NOT_EXECUTABLE"),
    ({"price": "0.51"}, "WOULD_CROSS"),
])
def test_each_frozen_gate_refuses_even_when_armed(monkeypatch, over, reason):
    kw = _fresh_kw(**over)
    _arm(monkeypatch, kw["slug"])
    v = O.preflight_submission(**kw)
    assert v["ok"] is False
    assert any(reason in r for r in v["refusals"]), v["refusals"]


def test_spent_latch_refuses_even_when_armed(monkeypatch):
    kw = _fresh_kw()
    _arm(monkeypatch, kw["slug"])
    L.consume(slug=kw["slug"], side="UP", price="0.5", qty="0.01")
    v = O.preflight_submission(**kw)
    assert v["ok"] is False
    assert "DURABLE_LATCH_SPENT" in v["refusals"]


def test_gate_evidence_must_name_this_slug(monkeypatch):
    kw = _fresh_kw()
    _arm(monkeypatch, "cpc-btc-updown-15m-2026-10-06-9999z")
    assert O.preflight_submission(**kw)["ok"] is False


def test_submit_one_refuses_a_failed_preflight():
    with pytest.raises(O.MutationRefused):
        O.submit_one(O.BoundedOrderTransport(), slug="s", outcome_side="UP",
                     price="0.50", checks={"ok": False, "refusals": ["X"]})


def test_submit_one_does_not_consume_the_latch_when_refused(monkeypatch):
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "sid-g")
    with pytest.raises(O.MutationRefused):
        O.submit_one(O.BoundedOrderTransport(), slug="s", outcome_side="UP",
                     price="0.50", checks={"ok": False, "refusals": ["X"]})
    assert L.state()["spent"] is False


# ------------------------------------------------------------- the wire body

def test_body_is_post_only_limit_buy_at_the_minimum():
    b = O.order_body(slug="s", outcome_side="OUTCOME_SIDE_YES", price="0.42")
    assert b["participateDontInitiate"] is True
    assert b["type"] == "ORDER_TYPE_LIMIT"
    assert b["action"] == "ORDER_ACTION_BUY"
    assert b["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL"
    # The venue documents number<double> for quantity, and a price object
    # carrying its currency. A5 sent a string quantity and no currency,
    # and the preview answered 400.
    assert b["quantity"] == 0.01 and isinstance(b["quantity"], float)
    assert b["price"] == {"value": "0.42", "currency": "USD"}


def test_no_side_is_sent_as_one_minus_x():
    b = O.order_body(slug="s", outcome_side="OUTCOME_SIDE_NO", price="0.42")
    assert b["price"]["value"] == "0.58"


def test_spec_names_exactly_four_mutations():
    assert len(X.ALLOWED_MUTATIONS) == 4
    assert X.SPEC["mutation_path"]["allowed"] == [
        "POST /v1/order/preview", "POST /v1/orders",
        "POST /v1/order/{id}/cancel", "POST /v1/orders/open/cancel"]
