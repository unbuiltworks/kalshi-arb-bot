"""One loss budget for a multi-window test, enforced in code.

It counts losses only, takes the worse of the lane's ledger and the
venue's row, does not reset at midnight, survives a restart through the
ledger, and refuses whenever its basis is missing, stale or unreconciled.
"""
import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_btc5m_execution as X
from tests.test_l5_btc5m_hold_release import (          # noqa: F401
    T0, armed, live, rig, wander)

NOW = dt.datetime(2026, 11, 21, 23, 50, tzinfo=dt.timezone.utc)
START = NOW - dt.timedelta(hours=3)
A, B, C_ = ("cpc-btc-updown-5m-2026-11-21-2100z",
            "cpc-btc-updown-5m-2026-11-21-2200z",
            "cpc-btc-updown-5m-2026-11-21-2300z")
BEFORE = "cpc-btc-updown-5m-2026-11-20-1705z"


def doc(venue=None, **over):
    d = {"campaign_id": "four-windows", "started_utc": START.isoformat(),
         "issued_utc": (NOW - dt.timedelta(minutes=10)).isoformat(),
         "expires_utc": (NOW + dt.timedelta(minutes=30)).isoformat(),
         "max_loss_usd": "0.60", "venue_results": dict(venue or {})}
    d.update(over)
    return d


def hist(**rows):
    when = {A: START + dt.timedelta(minutes=10),
            B: START + dt.timedelta(minutes=70),
            C_: NOW - dt.timedelta(minutes=2),          # after the document
            BEFORE: START - dt.timedelta(days=1)}
    return {m: {"at": when[m], "pnl": D(v)} for m, v in rows.items()}


def verdict(d, h, now=NOW):
    return X.campaign_loss_verdict(d, h, now)


# ------------------------------------------------------------ the budget

def test_a_fresh_campaign_has_its_whole_budget():
    r = verdict(doc(), {})
    assert r["ok"] and D(r["room_usd"]) == D("0.60")


def test_losses_accumulate_across_windows():
    r = verdict(doc({A: "-0.20", B: "-0.15"}),
                hist(**{A: "-0.20", B: "-0.15"}))
    assert r["ok"] and D(r["lost_usd"]) == D("0.35")
    assert D(r["room_usd"]) == D("0.25")


def test_a_winning_window_gives_nothing_back():
    r = verdict(doc({A: "-0.20", B: "0.30"}),
                hist(**{A: "-0.20", B: "0.30"}))
    assert D(r["lost_usd"]) == D("0.20")


def test_it_stops_at_the_limit():
    r = verdict(doc({A: "-0.40", B: "-0.20"}),
                hist(**{A: "-0.40", B: "-0.20"}))
    assert r["ok"] is False and r["reason"] == "CAMPAIGN_LOSS_EXHAUSTED"


def test_it_does_not_reset_at_midnight_utc():
    tomorrow = NOW + dt.timedelta(minutes=20)
    assert tomorrow.date() != NOW.date()
    r = verdict(doc({A: "-0.40", B: "-0.15"}),
                hist(**{A: "-0.40", B: "-0.15"}), now=tomorrow)
    assert r["ok"] and D(r["room_usd"]) == D("0.05")


def test_a_result_from_before_the_campaign_is_not_counted():
    r = verdict(doc(), hist(**{BEFORE: "-0.50"}))
    assert r["ok"] and D(r["room_usd"]) == D("0.60")


def test_the_worse_of_ledger_and_venue_is_used():
    r = verdict(doc({A: "-0.204"}), hist(**{A: "-0.20"}))
    assert D(r["lost_usd"]) == D("0.204")
    r = verdict(doc({A: "-0.20"}), hist(**{A: "-0.203"}))
    assert D(r["lost_usd"]) == D("0.203")


def test_a_window_settled_after_the_document_is_counted_from_the_ledger():
    r = verdict(doc({A: "-0.20"}), hist(**{A: "-0.20", C_: "-0.30"}))
    assert r["ok"] and D(r["lost_usd"]) == D("0.50")


# ------------------------------------------------------------ fails closed

@pytest.mark.parametrize("d,why", [
    (None, "CAMPAIGN_LOSS_HALT_NOT_SET"),
    ("UNREADABLE", "CAMPAIGN_LOSS_HALT_UNREADABLE"),
    ({}, "CAMPAIGN_LOSS_HALT_UNREADABLE"),
    (doc(max_loss_usd="0"), "CAMPAIGN_LOSS_HALT_LIMIT_OUT_OF_RANGE"),
    (doc(max_loss_usd="5.01"), "CAMPAIGN_LOSS_HALT_LIMIT_OUT_OF_RANGE"),
    (doc(max_loss_usd="abc"), "CAMPAIGN_LOSS_HALT_UNREADABLE"),
    (doc(venue_results=None), "CAMPAIGN_LOSS_HALT_UNREADABLE"),
    (doc(campaign_id=""), "CAMPAIGN_LOSS_HALT_UNREADABLE"),
    (doc(started_utc="yesterday"), "CAMPAIGN_LOSS_HALT_UNREADABLE"),
    (doc(expires_utc=(NOW - dt.timedelta(seconds=1)).isoformat()),
     "CAMPAIGN_LOSS_HALT_EXPIRED"),
    (doc(issued_utc=(NOW + dt.timedelta(minutes=1)).isoformat()),
     "CAMPAIGN_LOSS_HALT_NOT_YET_VALID"),
    (doc(expires_utc=(START + dt.timedelta(hours=37)).isoformat()),
     "CAMPAIGN_LOSS_HALT_DATES_INCOHERENT"),
    (doc(issued_utc=(START - dt.timedelta(minutes=1)).isoformat()),
     "CAMPAIGN_LOSS_HALT_DATES_INCOHERENT")])
def test_a_missing_stale_or_malformed_document_refuses(d, why):
    r = verdict(d, {})
    assert r["ok"] is False and r["reason"] == why


def test_an_unreadable_ledger_history_refuses():
    assert verdict(doc(), None)["reason"] == "CAMPAIGN_HISTORY_UNREADABLE"


def test_a_window_the_venue_settled_that_the_ledger_lacks_refuses():
    #: a lost or replaced ledger must not read as "nothing lost"
    r = verdict(doc({A: "-0.20"}), {})
    assert r["reason"] == "CAMPAIGN_HISTORY_MISSING" and r["market"] == A


def test_ledger_and_venue_disagreeing_refuses():
    r = verdict(doc({A: "-0.20"}), hist(**{A: "-0.10"}))
    assert r["reason"] == "CAMPAIGN_RESULT_DISAGREES"


def test_an_unreadable_venue_figure_refuses():
    r = verdict(doc({A: "n/a"}), hist(**{A: "-0.10"}))
    assert r["reason"] == "CAMPAIGN_VENUE_RESULT_UNREADABLE"


def test_a_window_that_was_never_reconciled_blocks_the_next_arm():
    #: A settled inside the campaign BEFORE this document was written and
    #: the document does not carry its venue result
    r = verdict(doc(), hist(**{A: "-0.20"}))
    assert r["reason"] == "CAMPAIGN_RESULT_NOT_RECONCILED" and r["market"] == A


def test_the_env_reader(monkeypatch):
    monkeypatch.delenv(X.CAMPAIGN_LOSS_HALT_ENV, raising=False)
    assert X.campaign_loss_halt_doc() is None
    monkeypatch.setenv(X.CAMPAIGN_LOSS_HALT_ENV, "{not json")
    assert X.campaign_loss_halt_doc() == "UNREADABLE"
    monkeypatch.setenv(X.CAMPAIGN_LOSS_HALT_ENV, "[1]")
    assert X.campaign_loss_halt_doc() == "UNREADABLE"
    monkeypatch.setenv(X.CAMPAIGN_LOSS_HALT_ENV, json.dumps(doc()))
    assert X.campaign_loss_halt_doc()["campaign_id"] == "four-windows"


# ------------------------------------------- through the real executor path

def _reasons(steps):
    return {r for s in steps for r in s["refused"]}


def test_with_the_document_present_orders_are_placed(rig):
    assert sum(s["placed"] for s in wander(rig, 4)) > 0


def test_without_the_document_nothing_is_submitted(rig, monkeypatch):
    monkeypatch.delenv(X.CAMPAIGN_LOSS_HALT_ENV)
    steps = wander(rig, 4)
    assert sum(s["placed"] for s in steps) == 0 and rig.venue.sent == []
    assert "CAMPAIGN_LOSS_HALT:CAMPAIGN_LOSS_HALT_NOT_SET" in _reasons(steps)


def test_an_expired_document_submits_nothing(rig, monkeypatch):
    d = json.loads(X.campaign_loss_halt_doc() and
                   json.dumps(X.campaign_loss_halt_doc()))
    d["expires_utc"] = (T0 - dt.timedelta(minutes=1)).isoformat()
    d["issued_utc"] = d["started_utc"]
    monkeypatch.setenv(X.CAMPAIGN_LOSS_HALT_ENV, json.dumps(d))
    steps = wander(rig, 3)
    assert rig.venue.sent == []
    assert "CAMPAIGN_LOSS_HALT:CAMPAIGN_LOSS_HALT_EXPIRED" in _reasons(steps)


def test_a_budget_spent_on_an_earlier_day_still_stops_it_after_a_restart(
        rig, monkeypatch):
    #: a settled loss in the lane's DURABLE ledger, inside the campaign
    #: but on the PREVIOUS UTC day, so the envelope's daily room -- which
    #: starts again at midnight -- no longer sees it
    d = X.campaign_loss_halt_doc()
    lost_mkt = "cpc-btc-updown-5m-2026-01-01-0000z"
    settled = T0 - dt.timedelta(hours=25)
    assert settled.date() != T0.date()
    d["started_utc"] = (T0 - dt.timedelta(hours=26)).isoformat()
    rig.ex._ledger["settled"][lost_mkt] = {
        "settled_at": settled.isoformat(), "settlement_pnl": "-0.60"}
    assert rig.ex._save_or_halt()
    assert rig.ex.day_settled_pnl(T0) == 0          # the daily room is blind
    d["venue_results"] = {lost_mkt: "-0.60"}
    monkeypatch.setenv(X.CAMPAIGN_LOSS_HALT_ENV, json.dumps(d))
    steps = wander(rig, 3)
    assert rig.venue.sent == []
    assert "CAMPAIGN_LOSS_HALT:CAMPAIGN_LOSS_EXHAUSTED" in _reasons(steps)
    assert "ENVELOPE:WORST_CASE_DAILY_LOSS" not in _reasons(steps)
    #: the figure comes from disk, not from this process
    again = X.Btc5mExecutor(rig.ex.transport, rig.ex.ledger_path)
    assert lost_mkt in again.settled_history()
    assert X.campaign_loss_verdict(
        X.campaign_loss_halt_doc(), again.settled_history(),
        T0)["reason"] == "CAMPAIGN_LOSS_EXHAUSTED"


def test_the_remaining_room_caps_what_can_be_at_risk(rig, monkeypatch):
    d = X.campaign_loss_halt_doc()
    d["max_loss_usd"] = "0.10"
    monkeypatch.setenv(X.CAMPAIGN_LOSS_HALT_ENV, json.dumps(d))
    steps = wander(rig, 20)
    assert sum(s["placed"] for s in steps) > 0
    assert max(s["held"] for s in steps) <= D("0.10")
