"""A long unattended session must roll on through ordinary events and stop
dead on integrity ones.

The failure mode this guards is a session that either stops constantly (and
is therefore not unattended) or keeps producing shadow records after
something moved that should not have.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX
from src import l5_session_integrity as IG
from src import l5_session_journal as JN
from src import l5_probe_manifest as PM
from scripts import l5_pmus_realtime_probe as RT

MK = "cpc-btc-updown-15m-2026-09-24-0000z"


# --------------------------------------------------------------- integrity

def test_a_submitted_order_while_disarmed_halts_immediately():
    with pytest.raises(IG.Halt) as e:
        IG.check(window_market=MK, last_book_t=time.time(), submitted_live=1)
    assert e.value.reason == "UNEXPECTED_MUTATION"


def test_a_changed_frozen_rule_halts(tmp_path):
    (tmp_path / "src").mkdir()
    for rel in IG.FROZEN:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("tampered")
    with pytest.raises(IG.Halt) as e:
        IG.check(window_market=MK, last_book_t=time.time(),
                 submitted_live=0, root=tmp_path)
    assert e.value.reason == "FROZEN_RULE_CHANGED"


def test_the_real_frozen_files_are_intact():
    assert IG.frozen_ok()["ok"] is True


def test_a_stale_authenticated_feed_halts():
    old = time.time() - IG.FEED_STALE_S - 1
    with pytest.raises(IG.Halt) as e:
        IG.check(window_market=MK, last_book_t=old, submitted_live=0)
    assert e.value.reason == "AUTH_FEED_STALE"


def test_a_record_from_another_market_halts():
    with pytest.raises(IG.Halt) as e:
        IG.check(window_market=MK, last_book_t=time.time(), submitted_live=0,
                 records=[{"market": "cpc-btc-updown-15m-2026-09-24-0015z"}])
    assert e.value.reason == "MARKET_IDENTITY_MISMATCH"


def test_lost_credentials_halt():
    with pytest.raises(IG.Halt) as e:
        IG.check(window_market=MK, last_book_t=time.time(),
                 submitted_live=0, has_credentials=False)
    assert e.value.reason == "CREDENTIALS_LOST"


@pytest.mark.parametrize("case", [
    {"records": []},                                   # no L5 actions
    {"records": [{"market": MK, "refused_reason": "NO_BOOK"}]},
    {"last_book_t": None},                             # nothing yet
])
def test_ordinary_quiet_does_NOT_halt(case):
    kw = {"window_market": MK, "last_book_t": time.time(),
          "submitted_live": 0}
    kw.update(case)
    assert IG.check(**kw)["ok"] is True


# ----------------------------------------------------------------- journal

def test_the_journal_is_append_only_and_sequenced(tmp_path):
    p = tmp_path / "j.jsonl"
    j = JN.Journal(str(p))
    j.write("a", {"x": 1})
    j.write("b", {"x": 2})
    lines = [json.loads(l) for l in p.read_text().splitlines()]
    assert [r["seq"] for r in lines] == [1, 2]
    assert len({r["session_id"] for r in lines}) == 1


def test_a_restart_continues_the_file_and_says_so(tmp_path):
    p = tmp_path / "j.jsonl"
    JN.Journal(str(p)).write("a", {})
    j2 = JN.Journal(str(p))
    assert j2.resume_note()["resumed_from_seq"] == 1
    j2.write("b", {})
    assert [json.loads(l)["seq"] for l in p.read_text().splitlines()] == [1, 2]


def test_a_failing_journal_is_counted_not_fatal(tmp_path):
    j = JN.Journal(str(tmp_path / "sub" / "j.jsonl"))
    j.path = tmp_path / "nope" / "deep" / "j.jsonl"
    j.path.parent.mkdir(parents=True)
    j.path.mkdir()                      # a directory where a file belongs
    assert j.write("a", {}) is None
    assert j.writes_failed >= 1


# ------------------------------------------------------- shadow economics

def _rec(side, price, qty, mode="TAKER"):
    return {"market": MK,
            "PMUS_TRANSLATION": {"mode": mode},
            "ORDER": {"reason": "NOT_ARMED", "submitted": False,
                      "intent": {"side": side, "price": price, "qty": qty,
                                 "mode": mode}}}


def test_shadow_economics_are_labelled_and_never_realized():
    w = RT.Window(MK)
    w.records = [_rec("UP", D("0.40"), D("1")), _rec("DOWN", D("0.55"), D("1"))]
    e = w.shadow_economics()
    assert e["LABEL"] == "SHADOW_NOT_REALIZED"
    assert e["matched"] == "1" and e["residual"] == "0"
    assert e["opposite_side_acquired"] is True
    assert e["pair_cost_per_share"] == "0.95"
    assert "no fill, settlement or realization" in e["note"]


def test_residual_is_signed_and_matched_is_the_overlap():
    w = RT.Window(MK)
    w.records = [_rec("UP", D("0.40"), D("3")), _rec("DOWN", D("0.55"), D("1"))]
    e = w.shadow_economics()
    assert e["matched"] == "1" and e["residual"] == "2"


# ------------------------------------------------------------- the loop

def test_the_session_rolls_through_windows_without_stopping(monkeypatch,
                                                            tmp_path):
    """A window with no actions must advance the session, not end it."""
    clock = {"t": 1790208000.0}          # a window boundary
    monkeypatch.setattr(RT.time, "time", lambda: clock["t"])
    monkeypatch.setattr(RT, "new_l5_actions", lambda s, seen: [])
    monkeypatch.setattr(RT, "live_books", lambda s: [
        {"t": clock["t"], "bid": "0.4", "ask": "0.6", "bid_qty": "1",
         "ask_qty": "1", "status": "OK"}])
    monkeypatch.setattr(RT, "live_trades", lambda s: [])
    monkeypatch.setattr(RT, "_creds_present", lambda: True)
    monkeypatch.setattr(RT, "_default_transport",
                        lambda slug: _NoPreflight())

    def tick(_s):
        clock["t"] += 400.0              # advance; crosses windows

    done = RT.run_forever(str(tmp_path / "inv.json"),
                          str(tmp_path / "j.jsonl"),
                          max_windows=3, sleep_fn=tick)
    assert done == 3
    kinds = [json.loads(l)["kind"]
             for l in (tmp_path / "j.jsonl").read_text().splitlines()]
    assert kinds.count("window_summary") == 3
    assert "halt" not in kinds


class _NoPreflight:
    def preflight(self, **kw):
        return {"preflight": "FAILED", "checks": []}
