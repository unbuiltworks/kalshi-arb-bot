"""The launcher driving a whole window against a mocked runtime and venue.

WHAT THESE TESTS ARE FOR. `test_l5_launch_orchestrator.py` covers the
decision functions in isolation. This file drives `run_launch` -- the state
machine that actually performs the sequence -- through a FakeRailway that
behaves like the real one: a heartbeat that changes as variables are
written, a deployment that has to be waited for, an arming transition that
takes time, and a venue that fills, partially fills, cancels and settles.

EVERY STEP IS REAL CODE. The only thing replaced is the transport. The
Railway client's own request building is tested separately in
`test_l5_railway_adapter.py` against a stubbed HTTP callable.

THE FAILURES IT GUARDS. Each named scenario is something that has happened
or that would be unrecoverable if it did: a halt nobody displayed, a
deployment that succeeded on the wrong commit, a stale SUCCESS mistaken for
a fresh one, a digest that drifted between approval and application, a
restart mid-window, the same approval applied twice.
"""
from __future__ import annotations

import copy
import datetime as dt
import json

import pytest

from src import l5_launch as L
from src import l5_railway as RW

import tests.test_l5_launch_orchestrator as O

SLUG = O.SLUG
WS = O.WS


class FakeRailway:
    """A runtime that CHANGES when you write to it.

    A double that returns a constant heartbeat cannot catch the mistakes
    that matter -- a digest that never lands, a container that never picks
    the values up, an arming transition that never happens. So this one
    models the parts that moved in the real incidents:

      * writing variables creates a NEW deployment with a later
        `createdAt`, which is what `wait_for_deployment(not_before=...)`
        exists to notice;
      * the heartbeat's campaign digest only changes after that
        deployment reaches SUCCESS, because the values take effect on the
        new container;
      * arming requires the shadow lock cleared AND the operator halt
        absent, and reports the runtime's own refusal reason otherwise.
    """

    def __init__(self, *, variables=None, halted=True, sha="a" * 40,
                 arm_after_polls=0, deploy_status="SUCCESS",
                 deploy_sha=None, fail_write=False, bounds=True,
                 now_fn=None, crash_on_redeploy=False,
                 redeploy_sha=None):
        self.vars = dict(variables or {"L5_LIVE_HALT": "1"})
        if not halted:
            self.vars.pop("L5_LIVE_HALT", None)
        self.sha = sha
        self.deploy_sha = deploy_sha or sha
        self.deploy_status = deploy_status
        self.fail_write = fail_write
        self.bounds = bounds
        self.crash_on_redeploy = bool(crash_on_redeploy)
        self.redeploy_sha = redeploy_sha
        self.arm_after_polls = int(arm_after_polls)
        self._polls = 0
        # ONE CLOCK for the fake and the run, so `heartbeat_is_fresh`
        # measures the thing it is meant to and not a fixture mismatch.
        self.now_fn = now_fn or fixed_now
        self._t = 0
        self._dep = 1
        self._published = None
        self.writes = []
        self.restarts = 0

    # --- time/deployment bookkeeping
    def _stamp(self):
        """A MONOTONE ISO stamp. `wait_for_deployment(not_before=...)`
        compares these as strings, so they must sort correctly."""
        self._t += 1
        return L._iso(self.now_fn() + dt.timedelta(seconds=self._t))

    def verify_scope(self):
        return {"ok": True, "project_id": "p", "environment_id": "e"}

    def latest_deployment(self):
        return {"id": "dep-%d" % self._dep, "status": self.deploy_status,
                "createdAt": getattr(self, "_created",
                                     self._created_default),
                "commitHash": self.deploy_sha,
                "branch": "b", "reason": "deploy"}

    @property
    def _created_default(self):
        return L._iso(self.now_fn())

    def wait_for_deployment(self, *, expect_sha=None, timeout_s=0,
                            poll_s=0, not_before=None):
        d = self.latest_deployment()
        if str(d["status"]) in ("FAILED", "CRASHED", "REMOVED"):
            raise RW.RailwayError("deployment %s is %s" % (d["id"],
                                                           d["status"]))
        if not_before is not None and not str(d["createdAt"]) > str(
                not_before):
            raise RW.RailwayError("no fresh deployment: %s is not after %s"
                                  % (d["createdAt"], not_before))
        if expect_sha and str(d["commitHash"]) != str(expect_sha):
            raise RW.RailwayError("succeeded on %s, expected %s"
                                  % (d["commitHash"], expect_sha))
        return d

    def variables(self):
        return dict(self.vars)

    def set_variables(self, values):
        if self.fail_write:
            raise RW.RailwayError("variableCollectionUpsert failed")
        self.writes.append(dict(values))
        self.vars.update({k: str(v) for k, v in values.items()})
        # a write redeploys: new container, later timestamp
        self._dep += 1
        self.restarts += 1
        # a rebuild can crash, or land on another commit -- the two things
        # `await_deployment` exists to catch, and neither is reachable
        # unless the fake can do them AFTER the write.
        if self.crash_on_redeploy:
            self.deploy_status = "CRASHED"
        if self.redeploy_sha:
            self.deploy_sha = self.redeploy_sha
        self._created = self._stamp()
        self._published = values.get("L5_CAMPAIGN_APPROVAL")
        self._polls = 0
        return {"written": sorted(values), "triggers_redeploy": True}

    # --- telemetry
    def heartbeat(self, **kw):
        hb = copy.deepcopy(O.hb())
        hb["utc"] = L._iso(self.now_fn())
        hb["market"] = SLUG
        halted = bool(str(self.vars.get("L5_LIVE_HALT") or "").strip())
        locked = str(self.vars.get("L5_SHADOW_ONLY", "1")).strip() != "0"
        digest = (RW and None)
        if self._published:
            from src import l5_campaign_approval as CA
            digest = CA.digest_of(str(self._published).encode())
        hb["campaign"] = dict(hb.get("campaign") or {},
                              document_sha256=digest,
                              approval_state="approved" if digest else None,
                              submissions_spent=0, max_submissions=500)
        self._polls += 1
        armable = (not halted and not locked
                   and self._polls > self.arm_after_polls)
        reason = ("SHADOW_ONLY_LOCK" if locked else
                  "HALT_GATE:HALT:" if halted else
                  ("OPEN" if armable else "NOT_ARMED"))
        hb["submission_gate"] = dict(hb["submission_gate"],
                                     blocked=not armable,
                                     armed=bool(armable), reason=reason,
                                     shadow_lock=locked)
        hb["halt"] = halted
        hb["halt_local_env"] = halted
        return hb

    def runtime_config(self, **kw):
        return O.cfg()

    def auth_preflight(self, **kw):
        return O.ap(market=SLUG)

    def venue_window_bounds(self, market, **kw):
        return O.bounds(WS) if self.bounds else None


def fixed_now():
    return dt.datetime(WS.year, WS.month, WS.day, 7, 40,
                       tzinfo=dt.timezone.utc)


def run(**over):
    kw = dict(client=FakeRailway(halted=False), expected_sha="a" * 40,
              window_start=WS, now_fn=fixed_now, sleeper=lambda s: None,
              arming_timeout_s=0.0, arming_poll_s=0.0)
    kw.update(over)
    return L.run_launch(**kw)


# =====================================================================
# WITHOUT AN APPROVAL IT STOPS
# =====================================================================

def test_with_no_approval_it_prepares_and_stops():
    """The default path, and the safe one."""
    out = run()
    assert out["verdict"] == L.READY
    assert out["stopped_at"] == "prepare"
    assert "applied" not in out
    assert out["prepared"]["document_digest"]


def test_with_no_approval_nothing_is_written():
    c = FakeRailway(halted=False)
    run(client=c)
    assert c.writes == []
    assert c.restarts == 0


def test_an_engaged_operator_halt_stops_at_prepare():
    """THE 08:30Z CASE, through the state machine. The halt is read from
    the VARIABLE, not from a preflight that may predate the fix."""
    c = FakeRailway(halted=True)
    out = run(client=c)
    assert out["verdict"] == L.BLOCKED
    assert out["stopped_at"] == "prepare"
    assert "local_operator_halt_clear" in out["prepared"]["blocking"]
    assert c.writes == []


def test_the_halt_is_read_from_the_variable_not_the_preflight():
    """A deployment older than 2c7c20b7 reports a verdict with no
    `local_operator_halt` row. Trusting it is the incident."""
    c = FakeRailway(halted=True)
    assert "local_operator_halt" not in json.dumps(c.auth_preflight())
    out = run(client=c)
    assert "local_operator_halt_clear" in out["prepared"]["blocking"]


# =====================================================================
# THE FULL SEQUENCE
# =====================================================================

def approved(c=None):
    """prepare once to obtain the artifact the owner would approve."""
    c = c or FakeRailway(halted=False)
    first = run(client=c)
    return c, first["prepared"], first["prepared"]["document_digest"]


def test_the_whole_sequence_runs_and_arms():
    c, plan, digest = approved()
    out = run(client=c, approved_digest=digest, approved_plan=plan,
              unlock_shadow=True)
    assert out["verdict"] == "EXECUTED", out.get("trail")
    steps = [t["step"] for t in out["trail"] if t["ok"]]
    for must in ("verify_scope", "read_runtime", "prepare", "apply",
                 "await_deployment", "reread_runtime", "verify_arming",
                 "monitor", "shutdown"):
        assert must in steps, (must, steps)


def test_it_publishes_the_document_and_the_new_container_confirms_it():
    c, plan, digest = approved()
    out = run(client=c, approved_digest=digest, approved_plan=plan,
              unlock_shadow=True)
    assert c.writes, "nothing was written"
    assert c.writes[0]["L5_CAMPAIGN_APPROVAL"] == plan["document_line"]
    reread = next(t for t in out["trail"] if t["step"] == "reread_runtime")
    assert reread["ok"] is True
    assert reread["approval_state"] == "approved"


def test_a_write_redeploys_and_the_run_waits_for_a_FRESH_success():
    """A stale SUCCESS left from the previous deployment must not count."""
    c, plan, digest = approved()
    before = c.latest_deployment()["createdAt"]
    run(client=c, approved_digest=digest, approved_plan=plan,
        unlock_shadow=True)
    assert c._created > before
    assert c.restarts == 1


def test_arming_is_the_runtimes_word_and_a_deploy_alone_is_not_arming():
    """It polls until `submission_gate` says armed. A succeeded deployment
    and a cleared lock are not arming -- that conflation is what 08:30Z
    looked like from outside."""
    c, plan, digest = approved(FakeRailway(halted=False,
                                           arm_after_polls=2))
    out = run(client=c, approved_digest=digest, approved_plan=plan,
              unlock_shadow=True, arming_timeout_s=30.0,
              arming_poll_s=0.0)
    assert out["verdict"] == "EXECUTED", out.get("trail")


def test_it_reports_NOT_ARMED_rather_than_claiming_success():
    """Shadow lock left ON: the configuration applied and the runtime did
    not arm. That is reported as NOT_ARMED, not as executed."""
    c, plan, digest = approved()
    out = run(client=c, approved_digest=digest, approved_plan=plan,
              unlock_shadow=False, arming_timeout_s=0.0)
    assert out["verdict"] == "NOT_ARMED"
    assert out["stopped_at"] == "verify_arming"
    assert out["gate"]["reason"] == "SHADOW_ONLY_LOCK"
    assert out["gate"]["blocked"] is True


# =====================================================================
# NEGATIVE PATHS
# =====================================================================

def test_a_wrong_approved_digest_halts_at_apply_with_nothing_written():
    c, plan, _ = approved()
    with pytest.raises(L.LaunchHalted) as ei:
        run(client=c, approved_digest="0" * 64, approved_plan=plan,
            unlock_shadow=True)
    assert ei.value.at == "apply"
    assert ei.value.reason == "APPROVED_DIGEST_MISMATCH"
    assert c.writes == []


def test_an_approved_plan_for_another_market_is_refused():
    c, plan, digest = approved()
    other = dict(plan, market="cpc-btc-updown-15m-2026-11-10-0815z")
    with pytest.raises(L.LaunchHalted) as ei:
        run(client=c, approved_digest=digest, approved_plan=other,
            unlock_shadow=True)
    assert ei.value.reason == "APPROVED_PLAN_NAMES_ANOTHER_MARKET"
    assert c.writes == []


def test_a_wrong_deployed_commit_BLOCKS_before_anything_is_written():
    """A CORRECTION OF THIS TEST'S FIRST VERSION, which expected a raise
    at `await_deployment`. The wrong commit is caught EARLIER, by
    `prepare`'s `deployed_sha_matches`, so the run returns BLOCKED and
    never writes. That is strictly better than halting mid-sequence, and
    asserting the worse behaviour would have locked it in."""
    c = FakeRailway(halted=False, sha="a" * 40, deploy_sha="b" * 40)
    out = run(client=c, expected_sha="a" * 40)
    assert out["verdict"] == L.BLOCKED
    assert "deployed_sha_matches" in out["prepared"]["blocking"]
    assert c.writes == []


def test_a_REBUILD_that_lands_on_another_commit_halts_at_await():
    """The case `await_deployment` is actually for: the pre-write
    deployment was right, and the rebuild the write triggered was not."""
    c, plan, digest = approved(FakeRailway(halted=False,
                                           redeploy_sha="b" * 40))
    with pytest.raises(L.LaunchHalted) as ei:
        run(client=c, approved_digest=digest, approved_plan=plan,
            unlock_shadow=True)
    assert ei.value.at == "await_deployment"
    assert "expected" in ei.value.reason


def test_a_CRASHED_deployment_BLOCKS_before_anything_is_written():
    c = FakeRailway(halted=False, deploy_status="CRASHED")
    out = run(client=c)
    assert out["verdict"] == L.BLOCKED
    assert "deployment_succeeded" in out["prepared"]["blocking"]
    assert c.writes == []


def test_a_rebuild_that_CRASHES_halts_at_await_and_is_not_retried():
    c, plan, digest = approved(FakeRailway(halted=False,
                                           crash_on_redeploy=True))
    with pytest.raises(L.LaunchHalted) as ei:
        run(client=c, approved_digest=digest, approved_plan=plan,
            unlock_shadow=True)
    assert ei.value.at == "await_deployment"
    assert "CRASHED" in ei.value.reason


def test_a_failed_variable_write_halts_at_apply():
    c, plan, digest = approved()
    c.fail_write = True
    with pytest.raises(L.LaunchHalted) as ei:
        run(client=c, approved_digest=digest, approved_plan=plan,
            unlock_shadow=True)
    assert ei.value.at == "apply"
    assert c.writes == []


def test_an_unreachable_runtime_halts_at_read_runtime():
    class Dead(FakeRailway):
        def heartbeat(self, **kw):
            return {}

    with pytest.raises(L.LaunchHalted) as ei:
        run(client=Dead(halted=False))
    assert ei.value.at == "read_runtime"
    assert ei.value.reason == "NO_HEARTBEAT"


def test_a_token_for_another_project_halts_first():
    class WrongScope(FakeRailway):
        def verify_scope(self):
            raise RW.RailwayError("token scope mismatch")

    with pytest.raises(L.LaunchHalted) as ei:
        run(client=WrongScope(halted=False))
    assert ei.value.at == "verify_scope"


def test_absent_venue_bounds_block_before_anything_is_written():
    c = FakeRailway(halted=False, bounds=False)
    out = run(client=c)
    assert out["verdict"] == L.BLOCKED
    assert "venue_confirmed_window_bounds" in out["prepared"]["blocking"]
    assert c.writes == []


# =====================================================================
# DUPLICATE APPROVAL, AND RESTART
# =====================================================================

def test_applying_the_same_approval_twice_writes_the_same_values():
    """IDEMPOTENT RETRY. A run that timed out after writing must be safe to
    repeat; the window ledger, not the launcher, counts windows.

    THE SHADOW LOCK IS LEFT ON DELIBERATELY, which is what makes this a
    retry rather than a second launch. `l5_probe_preflight` is a
    PRE-ARMING artifact: one of its rows asserts the submission gate is
    still closed, so once a run has armed, re-running `prepare` correctly
    reports not-ready. Discovered by this test's first version, which
    re-launched after arming and read the resulting BLOCKED as a defect.
    """
    c, plan, digest = approved()
    a = run(client=c, approved_digest=digest, approved_plan=plan,
            unlock_shadow=False, arming_timeout_s=0.0)
    first = len(c.writes)
    b = run(client=c, approved_digest=digest, approved_plan=plan,
            unlock_shadow=False, arming_timeout_s=0.0)
    assert a["verdict"] == b["verdict"] == "NOT_ARMED"
    assert a["applied"]["digest"] == b["applied"]["digest"] == digest
    assert c.writes[0] == c.writes[first]
    assert "L5_SHADOW_ONLY" not in c.writes[0]


def test_a_restart_between_prepare_and_apply_is_detected_by_the_digest():
    """The runtime restarted and lost the published approval. The re-read
    catches it rather than proceeding into a window with no authorization."""
    c, plan, digest = approved()

    class Amnesiac(type(c)):
        def heartbeat(self, **kw):
            hb = super().heartbeat(**kw)
            # the new container came up WITHOUT the approval
            hb["campaign"] = dict(hb["campaign"], document_sha256=None,
                                  approval_state=None)
            return hb

    c.__class__ = Amnesiac
    with pytest.raises(L.LaunchHalted) as ei:
        run(client=c, approved_digest=digest, approved_plan=plan,
            unlock_shadow=True)
    assert ei.value.at in ("apply", "reread_runtime")


# =====================================================================
# THE VENUE: ORDERS, FILLS, CANCELS, RECONCILIATION, SETTLEMENT
# =====================================================================

def venue_window(orders, *, realized="-15.61090000", live=0, unresolved=0):
    hb = copy.deepcopy(O.hb())
    hb["order_progress"] = {"live_orders": live, "parked_orders": 0}
    hb["unresolved_accounting"] = {"count": unresolved}
    hb["realized"] = {"realized_total": realized,
                      "verified_l5_status": "UNPROVEN_LEGACY_ATTRIBUTION"}
    hb["day_realized"] = {"would_halt": False, "room_usd": "5",
                          "utc_day_realized_usd": "0"}
    return L.execution_report(heartbeat=hb, market=SLUG, orders=orders)


def test_a_window_with_no_orders_reports_zeros():
    r = venue_window([])
    assert (r["orders_submitted"], r["venue_acknowledged"],
            r["orders_with_fills"], r["matched_pairs"]) == (0, 0, 0, "0")


def test_two_acknowledged_quotes_that_never_fill():
    r = venue_window([
        {"decision": True, "submitted": True, "venue_order_id": "v1",
         "side": "UP", "filled_qty": "0"},
        {"decision": True, "submitted": True, "venue_order_id": "v2",
         "side": "DOWN", "filled_qty": "0"}])
    assert r["venue_acknowledged"] == 2
    assert r["orders_with_fills"] == 0
    assert r["matched_pairs"] == "0"


def test_a_partial_fill_on_one_leg_is_residual():
    r = venue_window([
        {"decision": True, "submitted": True, "venue_order_id": "v1",
         "side": "UP", "filled_qty": "0.40"},
        {"decision": True, "submitted": True, "venue_order_id": "v2",
         "side": "DOWN", "filled_qty": "0"}])
    assert r["orders_with_fills"] == 1
    assert r["matched_pairs"] == "0"
    assert r["unmatched_residual"] == "0.4"


def test_a_completed_pair_with_a_cancelled_replacement():
    r = venue_window([
        {"decision": True, "submitted": True, "venue_order_id": "v1",
         "side": "UP", "filled_qty": "1"},
        {"decision": True, "submitted": True, "venue_order_id": "v2",
         "side": "DOWN", "filled_qty": "0", "cancelled": True},
        {"decision": True, "submitted": True, "venue_order_id": "v3",
         "side": "DOWN", "filled_qty": "1"}])
    assert r["matched_pairs"] == "1.0"
    assert r["unmatched_residual"] == "0.0"
    assert r["cancellations"] == 1
    assert r["venue_acknowledged"] == 3


def test_settlement_is_reported_as_the_ACCOUNT_figure_and_labelled():
    r = venue_window([], realized="-15.61090000")
    assert r["settled_realized_net_usd"] == "-15.61090000"
    assert "not this window's attribution" in r["labels"]


def test_a_fill_never_counts_as_settled_money():
    r = venue_window([
        {"decision": True, "submitted": True, "venue_order_id": "v1",
         "side": "UP", "filled_qty": "1"},
        {"decision": True, "submitted": True, "venue_order_id": "v2",
         "side": "DOWN", "filled_qty": "1"}], realized="0")
    assert r["matched_pairs"] == "1.0"
    assert r["settled_realized_net_usd"] == "0"


def test_shutdown_requires_cancellation_and_reconciliation_first():
    hb = copy.deepcopy(O.hb())
    hb["order_progress"] = {"live_orders": 2, "parked_orders": 1}
    hb["unresolved_accounting"] = {"count": 1}
    sd = L.shutdown_plan(heartbeat=hb, now=WS + dt.timedelta(minutes=20),
                         window_start=WS)
    acts = [a["action"] for a in sd["actions"]]
    assert acts[0] == "cancel_outstanding_by_exact_id"
    assert "halt_reentry_pending_reconciliation" in acts
    assert acts[-1] == "emit_final_execution_report"
    assert sd["outstanding_clear"] is False


def test_the_run_ends_with_a_shutdown_plan_and_an_execution_report():
    c, plan, digest = approved()
    out = run(client=c, approved_digest=digest, approved_plan=plan,
              unlock_shadow=True)
    assert out["execution"]["orders_submitted"] == 0
    assert out["shutdown"]["actions"]
    assert "EXECUTED means the sequence ran" in out["labels"]
