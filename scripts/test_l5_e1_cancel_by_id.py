"""Tests for the operator exact-ID emergency cancellation tool.

The dangerous properties are the ones tested hardest: that naming an id is
not enough to cancel it, that a foreign id refuses the WHOLE run rather
than being skipped, that the only reachable mutating route is the per-id
cancel, and that no outcome is called cancelled without the venue saying so.
"""
from __future__ import annotations

import json
import os

import pytest

from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.live_exec2 import orders as O
from scripts import l5_e1_cancel_by_id as C

SLUG = "cpc-btc-updown-15m-2026-10-08-1930z"
OURS = "CZH2FP5QMZ8K"
OURS2 = "CZH2A4G66Z7D"
THEIRS = "CZZZSOMEONEELSE"


def tape(tmp_path, ids=(OURS,), *, session="E1-TEST"):
    """A durable E1 tape with FIRST_LEG_SUBMITTED rows, as the recorder
    writes them."""
    d = tmp_path / "e1_cohort" / "tape"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "events.jsonl", "w", encoding="utf8") as fh:
        for i in ids:
            fh.write(json.dumps({
                "event": "FIRST_LEG_SUBMITTED", "session_id": session,
                "window": 3, "slot": 0,
                "detail": {"order_id": i, "latch_path": "x"}}) + "\n")
    return str(tmp_path / "e1_cohort")


def venue(rows):
    def reader(path):
        for oid, row in rows.items():
            if path == OS.ORDER_PATH % oid:
                return dict(row, id=oid)
        raise AssertionError("unexpected read %s" % path)
    return reader


def row(*, state="ORDER_STATE_NEW", cum="0", leaves="0.13", slug=SLUG):
    return {"state": state, "cumQuantity": cum, "leavesQuantity": leaves,
            "marketSlug": slug, "outcomeSide": "OUTCOME_SIDE_YES"}


class FakeCancel(object):
    """A transport that records the cancels it is asked for."""

    def __init__(self, *, raises=None, flips=True, rows=None):
        self.posts = []
        self.raises = raises
        self.flips = flips
        self.rows = rows if rows is not None else {}

    def request(self, method, path, body=None, base=None):
        assert method.upper() == "POST"
        assert path.endswith("/cancel"), path
        self.posts.append(path)
        if self.raises:
            raise self.raises
        oid = path.split("/")[3]
        if self.flips and oid in self.rows:
            self.rows[oid] = dict(self.rows[oid],
                                  state="ORDER_STATE_CANCELED",
                                  leavesQuantity="0")
        return {"canceledOrderIds": [oid]}


# ====================== 1. INPUT: REFUSE EMPTY / MALFORMED ==============

def test_no_ids_is_a_REFUSAL(tmp_path):
    rep = C.run(ids_raw="", slug=SLUG, execute=True,
                state_dir=tape(tmp_path))
    assert rep["REFUSED"].startswith("NO_ORDER_IDS")
    assert "results" not in rep


@pytest.mark.parametrize("raw", ["   ", ",,,", "abc", "lower-case",
                                 "CZH2FP5QMZ8K!", "short", "x" * 40,
                                 "CZH2FP5QMZ8K, bad-one"])
def test_malformed_ids_are_REFUSED_not_filtered(tmp_path, raw):
    rep = C.run(ids_raw=raw, slug=SLUG, execute=True,
                state_dir=tape(tmp_path))
    assert "REFUSED" in rep, rep
    assert "results" not in rep


def test_a_missing_or_malformed_slug_is_REFUSED(tmp_path):
    for slug in ("", "   ", "NOT A SLUG", "x"):
        rep = C.run(ids_raw=OURS, slug=slug, execute=True,
                    state_dir=tape(tmp_path))
        assert rep["REFUSED"].startswith("MISSING_OR_MALFORMED_SLUG"), slug


def test_duplicate_ids_are_collapsed(tmp_path):
    p = C.parse_ids("%s,%s , %s" % (OURS, OURS, OURS))
    assert p["ids"] == [OURS] and p["n"] == 1


# ============ 2. NAMING AN ID IS NOT AUTHORIZATION =====================

def test_a_FOREIGN_id_refuses_the_WHOLE_run(tmp_path):
    """The shared-account property. One unowned id and NOTHING is
    cancelled -- not even the id that was ours."""
    rep = C.run(ids_raw="%s,%s" % (OURS, THEIRS), slug=SLUG, execute=True,
                state_dir=tape(tmp_path, [OURS]))
    assert rep["REFUSED"].startswith("OWNERSHIP_NOT_ESTABLISHED")
    assert "NOT_IN_E1_DURABLE_RECORDS" in rep["REFUSED"]
    assert THEIRS in rep["REFUSED"]
    assert rep["nothing_was_cancelled"] is True
    assert "results" not in rep


def test_ownership_comes_from_the_DURABLE_TAPE(tmp_path):
    state = tape(tmp_path, [OURS, OURS2])
    own = C.ownership([OURS, OURS2], state_dir=state)
    assert own["ok"] is True
    assert sorted(own["owned"]) == sorted([OURS, OURS2])
    assert own["foreign"] == []
    assert own["n_known_on_tape"] == 2
    assert own["naming_an_id_is_not_authorization"] is True


def test_an_UNREADABLE_tape_FAILS_CLOSED(tmp_path):
    own = C.ownership([OURS], state_dir=str(tmp_path / "does-not-exist"))
    assert own["ok"] is False
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=True,
                state_dir=str(tmp_path / "does-not-exist"))
    assert rep["REFUSED"].startswith("OWNERSHIP_NOT_ESTABLISHED")
    assert rep["nothing_was_cancelled"] is True


def test_a_SLUG_MISMATCH_is_uncertain_identity_and_refuses_that_id(tmp_path):
    rows = {OURS: row(slug="cpc-btc-updown-15m-2026-10-08-1945z")}
    t = FakeCancel(rows=rows)
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=True,
                state_dir=tape(tmp_path), reader=venue(rows), transport=t)
    r = rep["results"][0]
    assert r["outcome"] == "REFUSED_SLUG_MISMATCH"
    assert r["venue_confirmed"] is False
    assert t.posts == [], "nothing may be cancelled on a mismatch"


# ===================== 3. ONLY THE PER-ID CANCEL ROUTE ==================

def test_the_transport_refuses_every_route_but_the_per_id_cancel():
    t = C.CancelOnlyTransport()
    for method, path in (("POST", O.SUBMIT_PATH),
                         ("POST", O.PREVIEW_PATH),
                         ("POST", O.DEADMAN_PATH),
                         ("POST", "/v1/orders/open/cancel"),
                         ("PUT", "/v1/order/X/cancel"),
                         ("DELETE", "/v1/order/X")):
        with pytest.raises(O.MutationRefused):
            t.request(method, path, {})


def test_the_module_never_names_a_market_or_account_wide_route():
    src = open(C.__file__.replace(".pyc", ".py"), encoding="utf8").read()
    body = src.split('"""', 2)[2]
    assert "deadman_cancel_all" not in body
    assert "DEADMAN_PATH" not in body
    assert "/v1/orders/open/cancel" not in body
    assert "SUBMIT_PATH" not in body
    assert "submit_one" not in body


def test_the_report_states_which_mechanisms_were_not_used(tmp_path):
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=False,
                state_dir=tape(tmp_path), reader=venue({OURS: row()}),
                transport=FakeCancel())
    assert rep["route"] == "POST /v1/order/{id}/cancel"
    assert rep["mechanism"] == "analysis.l5.live_exec2.orders:cancel_exact"
    assert rep["market_scoped_cancellation_used"] is False
    assert rep["account_wide_cancellation_used"] is False


# ========== 4. VENUE STATES: TERMINAL, PARTIAL, AMBIGUOUS, TIMEOUT =====

def test_a_live_order_is_cancelled_and_CONFIRMED_by_re_reading(tmp_path):
    rows = {OURS: row()}
    t = FakeCancel(rows=rows)
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=True,
                state_dir=tape(tmp_path), reader=venue(rows), transport=t)
    r = rep["results"][0]
    assert t.posts == ["/v1/order/%s/cancel" % OURS]
    assert r["outcome"] == "CANCELLED_CONFIRMED"
    assert r["venue_confirmed"] is True
    assert r["after"]["is_terminal"] is True
    assert rep["summary"]["confirmed_cancelled"] == [OURS]


def test_an_ALREADY_CANCELLED_order_is_not_cancelled_again(tmp_path):
    rows = {OURS: row(state="ORDER_STATE_CANCELED", leaves="0")}
    t = FakeCancel(rows=rows)
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=True,
                state_dir=tape(tmp_path), reader=venue(rows), transport=t)
    assert rep["results"][0]["outcome"] == \
        "ALREADY_TERMINAL_NOTHING_TO_CANCEL"
    assert t.posts == []


def test_an_ALREADY_FILLED_order_is_reported_as_inventory_not_cancelled(
        tmp_path):
    rows = {OURS: row(state="ORDER_STATE_FILLED", cum="0.13", leaves="0")}
    t = FakeCancel(rows=rows)
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=True,
                state_dir=tape(tmp_path), reader=venue(rows), transport=t)
    r = rep["results"][0]
    assert r["outcome"] == "ALREADY_TERMINAL_NOTHING_TO_CANCEL"
    assert r["residual_inventory_qty"] == "0.13"
    assert t.posts == []
    assert rep["summary"]["residual_inventory"] == {OURS: "0.13"}


def test_a_PARTIAL_fill_is_cancelled_and_its_fill_reported_as_residual(
        tmp_path):
    rows = {OURS: row(state="ORDER_STATE_PARTIALLY_FILLED", cum="0.05",
                      leaves="0.08")}
    t = FakeCancel(rows=rows)
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=True,
                state_dir=tape(tmp_path), reader=venue(rows), transport=t)
    r = rep["results"][0]
    assert r["before"]["is_partially_filled"] is True
    assert r["outcome"] == "CANCELLED_CONFIRMED"
    assert r["residual_inventory_qty"] == "0.05"
    assert "a cancel does not unwind a fill" in r["inventory_note"]


def test_an_AMBIGUOUS_ack_is_verified_by_re_reading_and_never_retried(
        tmp_path):
    """The POST raises, but the venue shows the order terminal anyway: the
    cancel landed. It is labelled as resolved AFTER an ambiguous ack, and
    the POST is sent exactly once."""
    rows = {OURS: row()}

    class Ambiguous(FakeCancel):
        def request(self, method, path, body=None, base=None):
            self.posts.append(path)
            rows[OURS] = dict(rows[OURS], state="ORDER_STATE_CANCELED",
                              leavesQuantity="0")
            raise OSError("timeout after the request left")

    t = Ambiguous(rows=rows)
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=True,
                state_dir=tape(tmp_path), reader=venue(rows), transport=t)
    r = rep["results"][0]
    assert len(t.posts) == 1, "an ambiguous cancel must never be retried"
    assert r["ack"]["AMBIGUOUS"] is True
    assert r["ack"]["never_retried"] is True
    assert r["outcome"] == "CANCELLED_CONFIRMED_AFTER_AMBIGUOUS_ACK"
    assert r["venue_confirmed"] is True


def test_an_ambiguous_ack_with_the_order_STILL_OPEN_escalates(tmp_path):
    rows = {OURS: row()}
    t = FakeCancel(raises=OSError("timeout"), flips=False, rows=rows)
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=True,
                state_dir=tape(tmp_path), reader=venue(rows), transport=t)
    r = rep["results"][0]
    assert r["outcome"] == "STILL_OPEN_AFTER_CANCEL_NEEDS_OPERATOR"
    assert r["venue_confirmed"] is False
    assert r["NEEDS_OPERATOR"] is True
    assert rep["summary"]["needs_operator"] == [OURS]


def test_a_cancel_the_venue_does_not_confirm_is_NEVER_called_cancelled(
        tmp_path):
    """The response claims success and the order is still live. The tool
    must believe the order state, not the acknowledgement."""
    rows = {OURS: row()}
    t = FakeCancel(flips=False, rows=rows)
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=True,
                state_dir=tape(tmp_path), reader=venue(rows), transport=t)
    r = rep["results"][0]
    assert r["ack"]["ok"] is True, "the venue said yes"
    assert r["outcome"] == "STILL_OPEN_AFTER_CANCEL_NEEDS_OPERATOR"
    assert r["venue_confirmed"] is False
    assert rep["summary"]["confirmed_cancelled"] == []
    assert rep["summary"]["a_cancel_response_is_not_proof"] is True


def test_a_failed_PRE_read_fails_closed_without_posting(tmp_path):
    def broken(path):
        raise OSError("venue unreachable")

    t = FakeCancel()
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=True,
                state_dir=tape(tmp_path), reader=broken, transport=t)
    r = rep["results"][0]
    assert r["outcome"] == "PRE_READ_FAILED_FAIL_CLOSED"
    assert r["venue_confirmed"] is False
    assert t.posts == []


def test_a_failed_POST_read_is_UNVERIFIED_not_success(tmp_path):
    rows = {OURS: row()}
    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        if calls["n"] == 1:
            return dict(rows[OURS], id=OURS)
        raise OSError("venue unreachable")

    t = FakeCancel(rows=rows)
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=True,
                state_dir=tape(tmp_path), reader=flaky, transport=t)
    r = rep["results"][0]
    assert r["cancel_posted"] is True
    assert r["outcome"] == "UNVERIFIED_POST_READ_FAILED"
    assert r["venue_confirmed"] is False
    assert r["NEEDS_OPERATOR"] is True


# ===================== 8. THE REHEARSAL: DRY RUN ========================

def test_the_DEFAULT_is_a_dry_run_that_posts_nothing(tmp_path):
    rows = {OURS: row()}
    t = FakeCancel(rows=rows)
    rep = C.run(ids_raw=OURS, slug=SLUG, execute=False,
                state_dir=tape(tmp_path), reader=venue(rows), transport=t)
    r = rep["results"][0]
    assert rep["dry_run"] is True
    assert r["outcome"] == "DRY_RUN_WOULD_CANCEL"
    assert r["would_post"] == "/v1/order/%s/cancel" % OURS
    assert t.posts == [], "a dry run must send no POST"
    assert rep["summary"]["would_cancel_dry_run"] == [OURS]
    # and it still did the real ownership work
    assert rep["ownership"]["ok"] is True


def test_main_requires_the_EXECUTE_flag_to_post(monkeypatch, tmp_path,
                                               capsys):
    monkeypatch.setenv(C.IDS_ENV, OURS)
    monkeypatch.setenv(C.SLUG_ENV, SLUG)
    monkeypatch.delenv(C.EXECUTE_ENV, raising=False)
    monkeypatch.setattr(C.L, "LATCH_DIR", tape(tmp_path))
    monkeypatch.setattr(C, "CancelOnlyTransport",
                        lambda: FakeCancel(rows={OURS: row()}))
    monkeypatch.setattr(C.AUTH, "ReadOnlyTransport",
                        lambda: type("R", (), {"request": staticmethod(
                            lambda m, p: dict(row(), id=OURS))})())
    assert C.main([]) == 0
    out = capsys.readouterr().out
    assert "DRY_RUN_WOULD_CANCEL" in out
    assert '"dry_run": true' in out


def test_main_exits_2_on_a_refusal(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(C.IDS_ENV, raising=False)
    monkeypatch.setenv(C.SLUG_ENV, SLUG)
    monkeypatch.setattr(C.L, "LATCH_DIR", tape(tmp_path))
    assert C.main([]) == 2
    assert "NO_ORDER_IDS" in capsys.readouterr().out


def test_main_exits_3_when_an_order_needs_an_operator(monkeypatch, tmp_path,
                                                     capsys):
    rows = {OURS: row()}
    monkeypatch.setenv(C.IDS_ENV, OURS)
    monkeypatch.setenv(C.SLUG_ENV, SLUG)
    monkeypatch.setenv(C.EXECUTE_ENV, "YES")
    monkeypatch.setattr(C.L, "LATCH_DIR", tape(tmp_path))
    monkeypatch.setattr(C, "CancelOnlyTransport",
                        lambda: FakeCancel(flips=False, rows=rows))
    monkeypatch.setattr(C.AUTH, "ReadOnlyTransport",
                        lambda: type("R", (), {"request": staticmethod(
                            lambda m, p: dict(rows[OURS], id=OURS))})())
    assert C.main([]) == 3
    assert "STILL_OPEN_AFTER_CANCEL_NEEDS_OPERATOR" in capsys.readouterr().out


def test_it_reads_no_arming_key(monkeypatch, tmp_path):
    """Refusing to clean up because a flag is unset is the dangerous
    direction, so this tool does not consult arming at all."""
    src = open(C.__file__.replace(".pyc", ".py"), encoding="utf8").read()
    for key in ("PMUS_E1_LIVE", "PMUS_E1_ARM", "PMUS_E1_GATE_EVIDENCE",
                "PMUS_E1_AUTHORIZED_FINGERPRINT", "armed("):
        assert key not in src, key


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
