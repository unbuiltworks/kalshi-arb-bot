"""LIVE 2026-09-07T08:21:02Z, R4 first market, deploy 82429504: the resumed
session HALTed in its own preflight with
    Halt:GET_RETRIES_EXHAUSTED:/v1/orders/open:HTTP Error 429: Too Many Requests
zero orders, no inventory rebuilt -- and the campaign took a session HALT as
CAMPAIGN_STOP:HALT, so the process exited (restart is NEVER) with the 5.10
YES / USD 2.447 leg held and nobody watching. The same read had failed the
same way at 08:06:14 (the orphan sweep's list:Halt) and cleared 70 s later.

Cause, from the timestamps: the hourly incentive audit ran AT BOOT (08:20:23)
and walked the program list twice, 200 pages each, back to back -- ~400 GETs
in ~35 s -- right before the preflight's authenticated reads, which then
answered 429 for longer than five one-second retries.

Four bounded changes, none of them a cap, gate, threshold or order-path
change:
  1. transport: transient GET retries back off 1,2,4,8,16 s (31 s) not 5x1 s
  2. audit: one PACED walk of the program list per audit, shared
  3. campaign: the first audit waits 10 min after boot (hourly unchanged)
  4. campaign: a session HALT on a read-only GET exhaustion BEFORE any order
     is the venue read failure held_market() already raises between
     sessions -> the same bounded wait; a halt after an order still stops.
"""
from __future__ import annotations

import itertools

from src import pmus_incentive_audit as ia
from src import pmus_l5_continuous_session as cont
from src import pmus_q1_executor as q1
from tests.test_pmus_l5_continuous_session import (_arm, _model, _row,
                                                   _status, entry, pv2)


# ---- 1. transport backoff ----

def test_transient_get_retries_back_off_exponentially_then_halt():
    class T:
        def __init__(self):
            self.n = 0

        def request(self, method, path, body=None, base=q1.API):
            self.n += 1
            exc = RuntimeError("HTTP Error 429: Too Many Requests")
            exc.code = 429
            raise exc
    slept = []
    t = T()
    try:
        q1._get_with_retry(t, "/v1/orders/open", sleep=slept.append)
        raise AssertionError("expected Halt")
    except q1.Halt as h:
        assert str(h).startswith("GET_RETRIES_EXHAUSTED:/v1/orders/open:")
    assert t.n == q1.GET_RETRY_MAX == 5
    assert slept == [1.0, 2.0, 4.0, 8.0, 16.0]          # 31 s of patience, was 5 s
    assert sum(slept) == 31.0


def test_a_non_transient_error_is_still_raised_at_once():
    class T:
        def request(self, method, path, body=None, base=q1.API):
            exc = RuntimeError("HTTP Error 401")
            exc.code = 401
            raise exc
    slept = []
    try:
        q1._get_with_retry(T(), "/v1/orders/open", sleep=slept.append)
        raise AssertionError("expected the 401 to propagate")
    except RuntimeError as exc:
        assert "401" in str(exc) and not isinstance(exc, q1.Halt)
    assert slept == []


# ---- 2. one paced walk per audit ----

PAGES = {
    "/v1/incentives": {"programs": [
        {"marketSlug": "astatc-atp-a-b", "timePeriods": [
            {"programId": "us_open", "status": "active", "rewardPool": 400}]}],
        "nextPageToken": "t2"},
    "/v1/incentives?pageToken=t2": {"programs": [
        {"marketSlug": "tsc-mlb-stl-sf-7pt5", "timePeriods": [
            {"programId": "mlb_live", "status": "active", "rewardPool": 900}]}],
        "nextPageToken": "t3"},
    "/v1/incentives?pageToken=t3": {"programs": [
        {"marketSlug": "astatc-wta-c-d", "timePeriods": [
            {"programId": "us_open", "status": "active", "rewardPool": 400}]}]},
}


class _T:
    def __init__(self):
        self.paths = []

    def request(self, method, path, body=None, base=q1.API):
        self.paths.append(path)
        if path in PAGES:
            return PAGES[path]
        raise RuntimeError(f"HTTP 404 {path}")


def test_program_list_walk_is_paced_between_pages(monkeypatch):
    monkeypatch.setattr(ia, "PROGRAM_LIST_PAGE_PAUSE_S", 0.3)
    slept = []
    out = ia.program_list(_T(), sleep=slept.append)
    assert out["pages"] == 3 and out["exhausted"] is True
    assert slept == [0.3, 0.3]                     # between pages, not after the last
    # a page cap ends the walk without a trailing pause
    slept.clear()
    capped = ia.program_list(_T(), max_pages=2, sleep=slept.append)
    assert capped["pages"] == 2 and capped["truncated_at_page_cap"] is True
    assert slept == [0.3]


def test_one_audit_walks_the_program_list_once_not_twice(monkeypatch, tmp_path):
    monkeypatch.setattr(ia, "PROGRAM_LIST_PAGE_PAUSE_S", 0.0)
    monkeypatch.setattr(q1, "state_dir", lambda: tmp_path)
    t = _T()
    out = ia.incentive_audit(t)
    walks = [p for p in t.paths if p.startswith("/v1/incentives") and "earnings" not in p]
    assert walks == list(PAGES)                    # 3 pages, exactly once
    assert out["program_list_walks"] == 1
    # both consumers still saw the whole list (the MLB row is on page two)
    assert out["programs"]["ok"] and out["programs"]["programs"] == 3   # market rows
    assert out["mlb_scan"]["program_list_census"]["by_league"] == {
        "atp": 1, "mlb": 1, "wta": 1}


# ---- 3. the first audit is not at boot ----

def test_first_incentive_audit_waits_after_boot_hourly_unchanged(monkeypatch,
                                                                tmp_path):
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (_status([]), "status_url:test"))
    audits = []
    monkeypatch.setattr(cont, "run_incentive_audit",
                        lambda t, log: audits.append(1) or {"ok": True})
    ticks = itertools.chain([0.0] + [1.0] * 30, itertools.repeat(1000.0))
    cont.run_campaign(Fake(), clock=lambda: next(ticks), sleep=lambda s: None,
                      wall=lambda: 1005.0, campaign_s=500.0,
                      session_runner=lambda *a, **k: {})
    assert audits == []                            # nothing within the first seconds
    assert cont.INCENTIVE_AUDIT_BOOT_DELAY_S == 600.0
    assert cont.INCENTIVE_AUDIT_EVERY_S == 3600.0
    # the loop's own test: clock - last >= EVERY, with last seeded at
    # start - EVERY + BOOT_DELAY, first fires at start + BOOT_DELAY (10 min
    # after boot) and hourly from there
    start = 0.0
    last = start - cont.INCENTIVE_AUDIT_EVERY_S + cont.INCENTIVE_AUDIT_BOOT_DELAY_S
    assert 599.0 - last < cont.INCENTIVE_AUDIT_EVERY_S
    assert 600.0 - last >= cont.INCENTIVE_AUDIT_EVERY_S


# ---- 4. a read halt before any order continues the campaign ----

READ_HALT = ("Halt:GET_RETRIES_EXHAUSTED:/v1/orders/open:"
             "HTTP Error 429: Too Many Requests")


def _resume_setup(monkeypatch, tmp_path):
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    st = _status([_row("fresh-candidate", touch="9000")])
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (st, "status_url:test"))
    fake = Fake()
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    return fake


def test_read_halt_in_the_resume_preflight_is_waited_out_not_a_campaign_stop(
        monkeypatch, tmp_path):
    fake = _resume_setup(monkeypatch, tmp_path)
    ran = []

    def session(t, clock, sleep, wall, slug, gate_ok, deadline_s, **kw):
        ran.append(slug)
        if len(ran) == 1:                          # the 08:21:02Z report, verbatim shape
            return {"status": "HALT", "reason": READ_HALT, "slug": slug,
                    "resumed_inventory": None, "orders": [], "decisions": [],
                    "inventory": {"up": "0", "dn": "0"}}
        return {"status": "SPRING_RESIDUAL_HOLD", "slug": slug,
                "inventory": {"up": "0.02", "dn": "0"}, "actions": []}
    slept, logged = [], []
    monkeypatch.setattr(cont, "_log",
                        lambda kind, **kw: logged.append((kind, kw)))
    ticks = itertools.chain([0.0] + [1.0] * 12, itertools.repeat(1000.0))
    rep = cont.run_campaign(fake, clock=lambda: next(ticks),
                            sleep=slept.append, wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=session)
    assert ran[:2] == ["held", "held"]             # resumed again after the wait
    assert not rep["status"].startswith("CAMPAIGN_STOP")
    assert rep["status"].startswith("CAMPAIGN_RESUMING")
    assert cont.ITERATION_ERROR_BACKOFF_S in slept
    kinds = [k for k, _ in logged]
    assert "session_read_halt" in kinds and "campaign_iteration_error" in kinds
    rh = dict(logged[kinds.index("session_read_halt")][1])
    assert rh["resumed"] is True and "429" in rh["reason"]
    # the halted session stays in the record; it is not relabelled
    assert rep["sessions"][0]["status"] == "HALT"


def test_a_halt_after_an_order_was_submitted_still_stops_the_campaign(
        monkeypatch, tmp_path):
    fake = _resume_setup(monkeypatch, tmp_path)

    def session(t, clock, sleep, wall, slug, gate_ok, deadline_s, **kw):
        return {"status": "HALT", "reason": READ_HALT, "slug": slug,
                "orders": [{"id": "o1", "filled": "0"}], "decisions": [],
                "inventory": {"up": "0.02", "dn": "0"}}
    ticks = itertools.chain([0.0] + [1.0] * 12, itertools.repeat(1000.0))
    rep = cont.run_campaign(fake, clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=session)
    assert rep["status"] == "CAMPAIGN_STOP:HALT"


def test_a_halt_for_any_other_reason_still_stops_the_campaign(monkeypatch,
                                                             tmp_path):
    fake = _resume_setup(monkeypatch, tmp_path)

    def session(t, clock, sleep, wall, slug, gate_ok, deadline_s, **kw):
        return {"status": "HALT", "reason": "Halt:FILL_WITHOUT_PRICE:o1",
                "slug": slug, "orders": [], "decisions": []}
    ticks = itertools.chain([0.0] + [1.0] * 12, itertools.repeat(1000.0))
    rep = cont.run_campaign(fake, clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=session)
    assert rep["status"] == "CAMPAIGN_STOP:HALT"


def test_read_halt_classifier_is_narrow():
    assert cont._read_halt({"status": "HALT", "reason": READ_HALT, "orders": []})
    assert not cont._read_halt({"status": "HALT", "reason": READ_HALT,
                                "orders": [{"id": "o1", "filled": "1"}]})
    assert not cont._read_halt({"status": "HALT", "reason": "Halt:x", "orders": []})
    assert not cont._read_halt({"status": "DAILY_LOSS_STOP", "reason": READ_HALT})
    assert not cont._read_halt({"status": "SPRING_RESIDUAL_HOLD", "reason": None})
