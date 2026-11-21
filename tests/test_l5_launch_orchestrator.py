"""The launch orchestrator: one approval, and every negative path.

#214's deliverable. The tests are organised by the failure each one would
have caught, because every gate in `l5_launch` exists because something
went wrong once:

  * the local operator halt      -- 2026-11-09 08:30Z, 162 refusals, zero
                                    orders, against a preflight reporting
                                    ok=true
  * `market=null` readiness      -- the startup verdict evaluates the
                                    document alone and says so
  * a reformatted document       -- the gate hashes what is STORED
  * a halt cleared as a side
    effect of launching          -- never; it is a separate explicit ask
  * a widened limit              -- the writable surface contains none

NOTHING HERE TOUCHES PRODUCTION. `prepare` is pure, and `apply_plan` takes
its writer and its reader as arguments, so these tests hand it recorders.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_campaign_approval as CA
from src import l5_launch as L
from src import l5_probe_preflight as PF

WS = dt.datetime(2026, 11, 10, 8, 30, tzinfo=dt.timezone.utc)
NOW = dt.datetime(2026, 11, 10, 7, 40, tzinfo=dt.timezone.utc)
SLUG = "cpc-btc-updown-15m-2026-11-10-0830z"


def hb(**over):
    """A heartbeat the WHOLE chain accepts.

    BUILT ON `test_l5_probe_preflight.live_hb`, which is the established
    fixture of gate values observed on the running deployment, rather than
    on a minimal stub. `prepare` folds `l5_probe_preflight.preflight` in as
    one gate, and that artifact has twenty-odd rows of its own -- so a stub
    that satisfies only the launcher's own gates reports BLOCKED on
    `runtime_preflight`, which is correct behaviour and a useless fixture.
    Reusing the real shape keeps the two artifacts from drifting apart.
    """
    import tests.test_l5_probe_preflight as PFT
    base = dict(PFT.live_hb())
    base["utc"] = L._iso(NOW)
    base["market"] = SLUG
    _sg = dict(base.get("submission_gate") or {})
    # MERGE the latch telemetry rather than replace it: the fixture's
    # `kind: PRESENT` is what `halt_latch_readable` reads, and overwriting
    # it made the whole chain BLOCKED on a detail of the test's own making.
    _sg["halt_latch_telemetry"] = dict(_sg.get("halt_latch_telemetry") or {},
                                       sha256="abc")
    _sg["owner_arm_token"] = True
    _sg["halt_latch"] = "CLEAR"
    base["submission_gate"] = _sg
    base["order_progress"] = {"live_orders": 0, "parked_orders": 0}
    base["unresolved_accounting"] = {"count": 0}
    base["account_exposure"] = {"ok": True,
                                "account_total_worst_case_usd": "0",
                                "max_total_exposure_usd": "1.00"}
    base["campaign"] = dict(base.get("campaign") or {},
                            submissions_spent=0, max_submissions=500)
    base["realized"] = dict(base.get("realized") or {},
                            realized_total="-15.61090000",
                            verified_l5_status="UNPROVEN_LEGACY_ATTRIBUTION")
    base["pending_terminal"] = []
    base["quarantined"] = []
    # THE RECORDER BLOCK, because the real heartbeat carries one and
    # `run_launch` reads `hb["fill_calibration"]` rather than being handed
    # a calibration separately. A fixture without it leaves
    # `recorder_can_calibrate` UNKNOWN and the whole launch BLOCKED on the
    # test's own omission. Figures are the shape production reported.
    base["fill_calibration"] = {
        "orders": 18593, "events_appended": 547, "errors": {},
        "events_replayed_from_disk": 378388,
        "readback": {"readable": True, "error": None,
                     "events_readable": 378935, "orders_readable": 18593,
                     "orders_with_full_capture": 0, "unreadable_lines": 0,
                     "events_appended_this_process": 547,
                     "events_replayed_from_disk": 378388}}
    base.update(over)
    return base


def cfg(**over):
    taker = {"taker_policy_enabled": False,
             "taker_policy_owner_cross_authorized": False,
             "taker_crossing_reachable": False,
             "defensive_execution_enabled": False}
    taker.update(over)
    return {"rows": [], "observed_not_graded":
            [{"name": k, "effective": v} for k, v in taker.items()]}


def _plan(**over):
    """The established good plan, re-pointed at this file's market."""
    import tests.test_l5_probe_preflight as PFT
    p = dict(PFT.good_plan())
    p["market"] = SLUG
    p.update(over)
    return p


def dep(sha="a" * 40, status="SUCCESS"):
    return {"id": "dep-1", "status": status, "commitHash": sha}


def ap(ok=True, market=SLUG, **over):
    base = {"ok": ok, "fail": [] if ok else ["campaign_approval"],
            "unknown": [], "evaluated_market": market}
    base.update(over)
    return base


def bounds(start=WS):
    return {"source": "VENUE", "start": L._iso(start),
            "end": L._iso(start + dt.timedelta(minutes=15)),
            "confirmed_by": "verify_alignment"}


def good(**over):
    kw = dict(heartbeat=hb(), deployment=dep(), expected_sha="a" * 40,
              window_start=WS, now=NOW, auth_preflight=ap(),
              operator_halted=False, runtime_config=cfg(),
              venue_confirmed_bounds=bounds(),
              calibration={"orders": 3, "events_appended": 9, "errors": {},
                           "readback": {"readable": True, "error": None,
                                        "events_readable": 9,
                                        "orders_readable": 3,
                                        "unreadable_lines": 0,
                                        "events_replayed_from_disk": 9}},
              plan=_plan())
    kw.update(over)
    return kw


def g(prep, name):
    return next(x for x in prep["gates"] if x["gate"] == name)


# =====================================================================
# THE DOCUMENT
# =====================================================================

def test_the_document_is_nine_fields_and_binds_one_window():
    d = L.build_document(window_start=WS, now=NOW)
    for f in CA.REQUIRED_FIELDS:
        assert f in d, f
    assert d["window_starts"] == [L._iso(WS)]
    assert d["max_windows"] == 1
    assert len(d) == 9


def test_the_digest_is_of_the_exact_published_line():
    d = L.build_document(window_start=WS, now=NOW)
    line = L.document_line(d)
    assert line == json.dumps(d, separators=(",", ":"))
    assert "\n" not in line
    assert L.document_digest(d) == CA.digest_of(line.encode())


def _real_clock_doc():
    """A document dated against the WALL CLOCK.

    `ApprovalGate.state()` carries its own `now_fn` and judges `issued_utc`
    against the real clock -- `campaign_decision(now=...)` steers only the
    WINDOW decision. So a fixture dated in the future is refused
    `approval_issued_in_the_future`, which is the gate being right and the
    fixture being wrong. The two tests that exercise the REAL gate
    therefore date themselves from now; the rest keep fixed dates because
    they never reach it.
    """
    now = dt.datetime.now(dt.timezone.utc)
    ws = L.next_full_window(now, lead_min=50)
    return L.build_document(window_start=ws, now=now), ws, L.slug_for_window(ws)


def test_the_real_gate_reports_the_same_digest(monkeypatch, tmp_path):
    """The reason the digest travels with the line: the gate hashes what is
    STORED, so a reformatted document is a different document."""
    d, ws, slug = _real_clock_doc()
    monkeypatch.setenv(CA.APPROVAL_ENV, L.document_line(d))
    monkeypatch.setenv(CA.ROOT_ENV, str(tmp_path))
    out = CA.campaign_decision(slug, now=ws + dt.timedelta(seconds=300),
                               reserve=False)
    assert out["ok"] is True, out
    assert out["document_sha256"] == L.document_digest(d)


def test_a_reformatted_document_has_a_different_digest(monkeypatch,
                                                       tmp_path):
    d, ws, slug = _real_clock_doc()
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(d, indent=1))
    monkeypatch.setenv(CA.ROOT_ENV, str(tmp_path))
    out = CA.campaign_decision(slug, now=ws + dt.timedelta(seconds=300),
                               reserve=False)
    assert out["document_sha256"] != L.document_digest(d)


@pytest.mark.parametrize("kw", [
    {"max_total_exposure_usd": "50"},
    {"max_daily_loss_usd": "25"},
    {"max_windows": 99},
])
def test_it_refuses_to_construct_a_document_above_a_code_ceiling(kw):
    with pytest.raises(ValueError):
        L.build_document(window_start=WS, now=NOW, **kw)


def test_it_refuses_a_series_outside_the_allowlist():
    with pytest.raises(ValueError):
        L.build_document(window_start=WS, now=NOW, series="cpceth-eth-")


# =====================================================================
# PHASE 1 -- THE HAPPY PATH, AND THAT IT WRITES NOTHING
# =====================================================================

def test_a_complete_runtime_is_READY():
    p = L.prepare(**good())
    assert p["verdict"] == L.READY, p["blocking"]
    assert p["blocking"] == []
    assert p["market"] == SLUG
    assert p["document_digest"]


def test_prepare_writes_nothing(monkeypatch):
    import os
    before = dict(os.environ)
    L.prepare(**good())
    assert dict(os.environ) == before


def test_every_gate_is_reported_pass_or_fail():
    """A check that appears only on failure cannot be confirmed as having
    run -- which is how the 08:30Z window was lost."""
    p = L.prepare(**good())
    names = [x["gate"] for x in p["gates"]]
    for must in ("local_operator_halt_clear", "watchdog_latch_clear",
                 "auth_preflight_is_market_specific", "taker_unreachable",
                 "venue_confirmed_window_bounds", "deployed_sha_matches"):
        assert must in names, must
    assert len(names) == len(set(names))


# =====================================================================
# PHASE 1 -- THE NEGATIVE PATHS #214 NAMES
# =====================================================================

def test_the_local_operator_halt_BLOCKS():
    """THE 08:30Z REGRESSION, at the launcher."""
    p = L.prepare(**good(operator_halted=True))
    assert p["verdict"] == L.BLOCKED
    assert "local_operator_halt_clear" in p["blocking"]
    assert g(p, "local_operator_halt_clear")["engaged"] is True


def test_the_watchdog_halt_BLOCKS_separately():
    p = L.prepare(**good(heartbeat=hb(submission_gate={
        "blocked": True, "halt_latch": "HALT", "owner_arm_token": True})))
    assert p["verdict"] == L.BLOCKED
    assert "watchdog_latch_clear" in p["blocking"]


def test_both_halts_are_distinct_gates_and_can_disagree():
    """At 08:30Z the latch was CLEAR and the local halt was engaged.
    Collapsing them is exactly how it hid."""
    p = L.prepare(**good(operator_halted=True))
    assert g(p, "watchdog_latch_clear")["ok"] is True
    assert g(p, "local_operator_halt_clear")["ok"] is False


def test_a_market_null_auth_verdict_is_NEVER_ready():
    """The startup preflight evaluates the document alone and says so."""
    p = L.prepare(**good(auth_preflight=ap(market=None)))
    assert p["verdict"] == L.BLOCKED
    assert "auth_preflight_is_market_specific" in p["blocking"]


def test_an_auth_verdict_for_ANOTHER_market_is_not_ready():
    p = L.prepare(**good(auth_preflight=ap(
        market="cpc-btc-updown-15m-2026-11-10-0815z")))
    assert p["verdict"] == L.BLOCKED
    assert "auth_preflight_is_market_specific" in p["blocking"]


def test_a_failing_auth_preflight_BLOCKS():
    p = L.prepare(**good(auth_preflight=ap(ok=False)))
    assert p["verdict"] == L.BLOCKED
    assert "auth_preflight_ok" in p["blocking"]


def test_a_wrong_deployment_sha_BLOCKS():
    p = L.prepare(**good(deployment=dep(sha="b" * 40)))
    assert p["verdict"] == L.BLOCKED
    assert "deployed_sha_matches" in p["blocking"]


def test_a_failed_deployment_BLOCKS():
    p = L.prepare(**good(deployment=dep(status="CRASHED")))
    assert p["verdict"] == L.BLOCKED
    assert "deployment_succeeded" in p["blocking"]


def test_a_stale_heartbeat_BLOCKS():
    old = L._iso(NOW - dt.timedelta(minutes=30))
    p = L.prepare(**good(heartbeat=hb(utc=old)))
    assert p["verdict"] == L.BLOCKED
    assert "heartbeat_is_fresh" in p["blocking"]


def test_a_missing_q1_handshake_BLOCKS():
    p = L.prepare(**good(heartbeat=hb(submission_gate={
        "blocked": True, "halt_latch": "CLEAR", "owner_arm_token": False})))
    assert p["verdict"] == L.BLOCKED
    assert "q1_handshake_present" in p["blocking"]


def test_an_uncertain_order_state_BLOCKS():
    p = L.prepare(**good(heartbeat=hb(
        unresolved_accounting={"count": 2})))
    assert p["verdict"] == L.BLOCKED
    assert "accounting_resolved" in p["blocking"]


def test_a_live_order_BLOCKS():
    p = L.prepare(**good(heartbeat=hb(
        order_progress={"live_orders": 1, "parked_orders": 0})))
    assert p["verdict"] == L.BLOCKED
    assert "no_live_orders" in p["blocking"]


def test_exposure_not_ok_BLOCKS():
    p = L.prepare(**good(heartbeat=hb(account_exposure={
        "ok": False, "account_total_worst_case_usd": None})))
    assert p["verdict"] == L.BLOCKED
    assert "exposure_within_cap" in p["blocking"]


def test_a_tripped_daily_halt_BLOCKS():
    p = L.prepare(**good(heartbeat=hb(day_realized={
        "would_halt": True, "room_usd": "0",
        "utc_day_realized_usd": "-5"})))
    assert p["verdict"] == L.BLOCKED
    assert "daily_loss_room" in p["blocking"]


@pytest.mark.parametrize("which", [
    "taker_policy_enabled", "taker_policy_owner_cross_authorized",
    "taker_crossing_reachable", "defensive_execution_enabled",
])
def test_ANY_taker_path_enabled_BLOCKS(which):
    p = L.prepare(**good(runtime_config=cfg(**{which: True})))
    assert p["verdict"] == L.BLOCKED, which
    assert "taker_unreachable" in p["blocking"]


def test_an_absent_runtime_config_is_UNKNOWN_and_not_ready():
    p = L.prepare(**good(runtime_config=None))
    assert p["verdict"] == L.BLOCKED
    assert g(p, "taker_unreachable")["ok"] is PF.UNKNOWN


def test_absent_venue_bounds_are_UNKNOWN_and_not_ready():
    """Honest before the window exists -- and UNKNOWN is never READY. The
    arming boundary refuses WINDOW_BOUNDS_UNKNOWN anyway."""
    p = L.prepare(**good(venue_confirmed_bounds=None))
    assert p["verdict"] == L.BLOCKED
    assert g(p, "venue_confirmed_window_bounds")["ok"] is PF.UNKNOWN


def test_venue_bounds_for_the_WRONG_start_BLOCK():
    p = L.prepare(**good(venue_confirmed_bounds=bounds(
        WS + dt.timedelta(minutes=15))))
    assert p["verdict"] == L.BLOCKED
    assert "venue_confirmed_window_bounds" in p["blocking"]


def test_bounds_not_sourced_from_the_VENUE_BLOCK():
    b = bounds()
    b["source"] = "CLOCK"
    p = L.prepare(**good(venue_confirmed_bounds=b))
    assert p["verdict"] == L.BLOCKED


def test_a_window_in_the_past_BLOCKS():
    p = L.prepare(**good(now=WS + dt.timedelta(minutes=1)))
    assert p["verdict"] == L.BLOCKED
    assert "window_is_in_the_future" in p["blocking"]


def test_an_exhausted_submission_budget_BLOCKS():
    p = L.prepare(**good(heartbeat=hb(campaign={
        "submissions_spent": 500, "max_submissions": 500})))
    assert p["verdict"] == L.BLOCKED
    assert "submission_budget_remaining" in p["blocking"]


def test_a_recorder_without_readback_BLOCKS_via_the_runtime_preflight():
    p = L.prepare(**good(calibration={"orders": 3, "events_appended": 9,
                                      "errors": {}}))
    assert p["verdict"] == L.BLOCKED
    assert "runtime_preflight" in p["blocking"]


# =====================================================================
# PHASE 2 -- ONE APPROVAL, CARRIED NOT MINTED
# =====================================================================

class Writer:
    def __init__(self, digest=None):
        self.calls = []
        self._digest = digest

    def __call__(self, variables):
        self.calls.append(dict(variables))

    def reader(self):
        return {"campaign": {"document_sha256": self._digest,
                             "approval_state": "approved"}}


def applied(prep, **over):
    w = Writer(digest=prep["document_digest"])
    kw = dict(prepared=prep, approved_digest=prep["document_digest"],
              set_variables=w, read_back=w.reader)
    kw.update(over)
    return w, L.apply_plan(**kw)


def test_apply_publishes_the_approved_document_and_verifies_read_back():
    p = L.prepare(**good())
    w, out = applied(p)
    assert out["applied"] is True
    assert out["digest"] == p["document_digest"]
    sent = w.calls[0]
    assert sent["L5_CAMPAIGN_APPROVAL"] == p["document_line"]
    assert sent["L5_TS_MARKET"] == SLUG
    assert any(s["step"] == "verify_durable_read_back" and s["ok"]
               for s in out["steps"])


def test_apply_REFUSES_a_digest_the_owner_did_not_approve():
    """It carries an approval; it does not mint one."""
    p = L.prepare(**good())
    with pytest.raises(L.ApplyRefused) as ei:
        applied(p, approved_digest="0" * 64)
    assert ei.value.reason == "APPROVED_DIGEST_MISMATCH"


@pytest.mark.parametrize("bad", ["", None, "   "])
def test_apply_REFUSES_an_absent_approval(bad):
    p = L.prepare(**good())
    with pytest.raises(L.ApplyRefused):
        applied(p, approved_digest=bad)


def test_apply_REFUSES_a_BLOCKED_plan():
    """Applying over a failed gate is the 08:30Z failure with extra steps."""
    p = L.prepare(**good(operator_halted=True))
    with pytest.raises(L.ApplyRefused) as ei:
        applied(p)
    assert ei.value.reason == "PLAN_NOT_READY"
    assert "local_operator_halt_clear" in ei.value.detail["blocking"]


def test_apply_REFUSES_when_the_read_back_digest_disagrees():
    p = L.prepare(**good())
    w = Writer(digest="f" * 64)
    with pytest.raises(L.ApplyRefused) as ei:
        L.apply_plan(prepared=p, approved_digest=p["document_digest"],
                     set_variables=w, read_back=w.reader)
    assert ei.value.reason == "READ_BACK_DIGEST_MISMATCH"


def test_applying_the_same_approval_twice_is_idempotent():
    p = L.prepare(**good())
    w = Writer(digest=p["document_digest"])
    a = L.apply_plan(prepared=p, approved_digest=p["document_digest"],
                     set_variables=w, read_back=w.reader)
    b = L.apply_plan(prepared=p, approved_digest=p["document_digest"],
                     set_variables=w, read_back=w.reader)
    assert a["digest"] == b["digest"]
    assert w.calls[0] == w.calls[1]


def test_apply_does_NOT_touch_the_shadow_lock_unless_asked():
    p = L.prepare(**good())
    w, out = applied(p)
    assert "L5_SHADOW_ONLY" not in w.calls[0]
    assert out["shadow_unlocked"] is False


def test_unlocking_the_shadow_lock_is_an_explicit_request():
    p = L.prepare(**good())
    w, out = applied(p, unlock_shadow=True)
    assert w.calls[0]["L5_SHADOW_ONLY"] == "0"
    assert out["shadow_unlocked"] is True


def test_apply_NEVER_writes_a_limit_or_a_taker_switch():
    p = L.prepare(**good())
    w, _ = applied(p, unlock_shadow=True)
    for name in w.calls[0]:
        assert name in L.WRITABLE or name == "L5_LIVE_HALT", name


def test_the_writable_set_contains_no_limit_or_taker_switch():
    """STRUCTURAL. The surface cannot be widened by a later caller
    without this failing."""
    for name in L.WRITABLE:
        low = name.lower()
        for banned in ("exposure", "loss", "cash", "deficit", "taker",
                       "cross", "pair_cost", "budget", "q1", "halt_url"):
            assert banned not in low, (name, banned)
    assert "L5_LIVE_HALT" not in L.WRITABLE


# =====================================================================
# PHASE 2 -- THE HALT IS NEVER CLEARED SILENTLY
# =====================================================================

def all_preconditions():
    return {name: True for name, _ in L.HALT_RESET_PRECONDITIONS}


def test_a_halt_is_not_cleared_as_a_side_effect_of_launching():
    p = L.prepare(**good())
    w, out = applied(p, unlock_shadow=True)
    assert "L5_LIVE_HALT" not in w.calls[0]
    assert out["operator_halt_reset"] is None


def test_a_halt_reset_requires_a_REASON():
    p = L.prepare(**good())
    with pytest.raises(L.ApplyRefused) as ei:
        applied(p, reset_operator_halt="  ",
                halt_reset_evidence=all_preconditions())
    assert ei.value.reason == "HALT_RESET_NEEDS_A_REASON"


@pytest.mark.parametrize("missing", [n for n, _ in
                                     L.HALT_RESET_PRECONDITIONS])
def test_a_halt_reset_refuses_on_ANY_unmet_precondition(missing):
    p = L.prepare(**good())
    ev = all_preconditions()
    ev[missing] = False
    with pytest.raises(L.ApplyRefused) as ei:
        applied(p, reset_operator_halt="provenance established in #214",
                halt_reset_evidence=ev)
    assert ei.value.reason == "HALT_RESET_PRECONDITIONS_UNMET"
    assert missing in ei.value.detail["unmet"]


def test_a_halt_reset_refuses_with_NO_evidence_at_all():
    p = L.prepare(**good())
    with pytest.raises(L.ApplyRefused):
        applied(p, reset_operator_halt="because")


def test_a_fully_evidenced_halt_reset_is_recorded_and_applied():
    p = L.prepare(**good())
    w, out = applied(p, reset_operator_halt="provenance established in #214",
                     halt_reset_evidence=all_preconditions())
    assert w.calls[0]["L5_LIVE_HALT"] == ""
    assert out["operator_halt_reset"]["reason"].startswith("provenance")
    assert any(s["step"] == "operator_halt_reset_authorized"
               for s in out["steps"])


# =====================================================================
# PHASE 3 -- EXECUTION AND SHUTDOWN
# =====================================================================

def test_zero_orders_reports_zeros_and_claims_nothing():
    r = L.execution_report(heartbeat=hb(), market=SLUG, orders=[])
    assert r["orders_submitted"] == 0
    assert r["venue_acknowledged"] == 0
    assert r["orders_with_fills"] == 0
    assert r["matched_pairs"] == "0"
    assert r["settled_realized_net_usd"] == "-15.61090000"


def test_a_WOULD_SUBMIT_is_not_an_order():
    r = L.execution_report(heartbeat=hb(), market=SLUG, orders=[
        {"decision": True, "state": "WOULD_SUBMIT"}])
    assert r["would_submit"] == 1
    assert r["orders_submitted"] == 0
    assert r["orders_with_fills"] == 0


def test_an_acknowledgement_is_not_a_fill():
    r = L.execution_report(heartbeat=hb(), market=SLUG, orders=[
        {"decision": True, "submitted": True, "venue_order_id": "v1",
         "side": "UP", "filled_qty": "0"}])
    assert r["venue_acknowledged"] == 1
    assert r["orders_with_fills"] == 0
    assert r["matched_pairs"] == "0"


def test_a_partial_fill_is_residual_not_a_pair():
    r = L.execution_report(heartbeat=hb(), market=SLUG, orders=[
        {"decision": True, "submitted": True, "venue_order_id": "v1",
         "side": "UP", "filled_qty": "1"}])
    assert r["orders_with_fills"] == 1
    assert r["matched_pairs"] == "0"
    assert r["unmatched_residual"] == "1.0"


def test_a_completed_pair_is_matched_and_leaves_no_residual():
    r = L.execution_report(heartbeat=hb(), market=SLUG, orders=[
        {"decision": True, "submitted": True, "venue_order_id": "v1",
         "side": "UP", "filled_qty": "1"},
        {"decision": True, "submitted": True, "venue_order_id": "v2",
         "side": "DOWN", "filled_qty": "1"}])
    assert r["matched_pairs"] == "1.0"
    assert r["unmatched_residual"] == "0.0"


def test_cancellations_are_counted_separately():
    r = L.execution_report(heartbeat=hb(), market=SLUG, orders=[
        {"decision": True, "submitted": True, "venue_order_id": "v1",
         "side": "UP", "filled_qty": "0", "cancelled": True}])
    assert r["cancellations"] == 1
    assert r["orders_with_fills"] == 0


def test_shutdown_is_due_at_the_window_end():
    s = L.shutdown_plan(heartbeat=hb(), now=WS + dt.timedelta(minutes=16),
                        window_start=WS)
    assert s["due"] is True
    assert s["deadline_is"] == "window_end"


def test_the_campaign_expiry_can_be_the_EARLIER_deadline():
    exp = L._iso(WS + dt.timedelta(minutes=5))
    s = L.shutdown_plan(heartbeat=hb(), now=WS, window_start=WS,
                        expires_utc=exp)
    assert s["deadline_is"] == "campaign_expiry"
    assert s["deadline_utc"] == exp


def test_shutdown_is_not_due_before_the_deadline():
    s = L.shutdown_plan(heartbeat=hb(), now=WS + dt.timedelta(minutes=1),
                        window_start=WS)
    assert s["due"] is False


def test_shutdown_always_restores_the_structural_lock():
    s = L.shutdown_plan(heartbeat=hb(), now=WS + dt.timedelta(minutes=16),
                        window_start=WS)
    acts = [a["action"] for a in s["actions"]]
    assert "restore_structural_submission_lock" in acts
    lock = next(a for a in s["actions"]
                if a["action"] == "restore_structural_submission_lock")
    assert lock["value"] == "1"


def test_an_outstanding_order_requires_cancellation_before_the_lock():
    """A lock blocks submission and does NOT flatten a resting order."""
    s = L.shutdown_plan(heartbeat=hb(order_progress={
        "live_orders": 1, "parked_orders": 0}),
        now=WS + dt.timedelta(minutes=16), window_start=WS)
    acts = [a["action"] for a in s["actions"]]
    assert acts.index("cancel_outstanding_by_exact_id") < acts.index(
        "restore_structural_submission_lock")
    assert s["outstanding_clear"] is False


def test_unresolved_accounting_halts_reentry_at_shutdown():
    s = L.shutdown_plan(heartbeat=hb(unresolved_accounting={"count": 1}),
                        now=WS + dt.timedelta(minutes=16), window_start=WS)
    acts = [a["action"] for a in s["actions"]]
    assert "halt_reentry_pending_reconciliation" in acts


# =====================================================================
# THE 08:30Z REGRESSION MUST REMAIN INTACT
# =====================================================================

def test_the_operator_halt_regression_from_2c7c20b7_still_holds(monkeypatch):
    """#214: "The operator-halt regression from SHA 2c7c20b7 must remain
    intact." Asserted here too so a launcher change cannot quietly undo
    it."""
    from src import l5_live_authorization as AUTH
    from src import l5_pmus_live_runner as R
    monkeypatch.setenv(R.LIVE_HALT_ENV, "1")
    pf = AUTH.authorization_preflight("")
    assert pf["ok"] is not True
    assert "local_operator_halt" in (pf["fail"] or [])
    row = next(r for r in pf["rows"]
               if r["check"] == "local_operator_halt")
    assert row["verdict"] == "FAIL"
    assert row["engaged"] is True
