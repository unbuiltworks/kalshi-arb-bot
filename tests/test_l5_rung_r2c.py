"""OWNER 5560851070 (REPLICATE ok -> PROVE FIDELITY ok (behavioral) -> RUN):
the R2C caps rung -- $0.15 per market, residual 6 units, derived from the
R1 cohort -- and the plumbing that makes the caps follow the CAMPAIGN's
rung: the ladder's active-rung registry, the executor / session cap
readers, the shell label on every decision row, the evaluator reading
each row's own cap from its label, and the campaign applying the rung
from durable state at boot and from the consumed owner approval at the
advance gate. R1 stays the default everywhere; R1 rows keep their label."""
from __future__ import annotations

import itertools
import json
from decimal import Decimal as D

from src import l5_fidelity_gate as gate
from src import pmus_l5_continuous_session as cont
from src import pmus_l5_ramp_ladder as ld
from src import pmus_micro_executor as micro
from src import pmus_spring_l5_session as spring

R1_LABEL = "R1/cap0.05/residual3/conc1/maker-only/fee-safe0.98/rest30s"
R2C_LABEL = "R2C/cap0.15/residual6/conc1/maker-only/fee-safe0.98/rest30s"


def test_r2c_rung_is_defined_outside_the_l5_order_ladder_with_explicit_worst_case(
        monkeypatch):
    monkeypatch.delenv(ld.RUNG_ENV, raising=False)
    r = ld.rung("R2C")
    assert r["per_market_cap_usd"] == "0.15" and r["residual_units"] == 6
    assert r["concurrency"] == 1 and r["max_actions_per_session"] == 6
    assert r["campaigns_per_day"] == 1 and r["promote"] is None
    wc = {x["id"]: x["worst_case"] for x in ld.ladder()}
    assert wc["R2C"]["per_campaign_usd"] == "0.90"
    assert wc["R2C"]["per_day_bounded_by_daily_stop_usd"] == "0.90"
    assert wc["R2C"]["max_concurrent_open_exposure_usd"] == "0.15"
    assert D(wc["R2C"]["per_campaign_usd"]) <= -ld.DAILY_LOSS_STOP
    # the L5-order ladder and its promotion chain are untouched
    assert [x["id"] for x in ld.RUNGS] == ["R1", "R2", "R3", "R4", "R5"]
    assert ld.next_rung("R1")["id"] == "R2" and ld.next_rung("R5") is None
    assert all(x["residual_units"] == 3 for x in ld.RUNGS)
    # the default rung is still R1 and the label is the cohort's label
    assert ld.active()["id"] == "R1"
    assert ld.shell_version() == R1_LABEL == spring.SAFETY_SHELL_VERSION


def test_active_rung_drives_executor_and_session_caps_and_the_shell_label(
        monkeypatch):
    monkeypatch.delenv(ld.RUNG_ENV, raising=False)
    assert micro.per_market_first_leg_cap() == D("0.05") == micro.PER_MARKET_FIRST_LEG_CAP
    assert spring.session_notional_cap() == D("0.05") == spring.SESSION_NOTIONAL_CAP
    assert spring.max_residual_units() == 3 == spring.MAX_RESIDUAL_UNITS
    assert spring.safety_shell_version() == R1_LABEL
    ld.set_active("R2C")
    assert micro.per_market_first_leg_cap() == D("0.15")
    assert spring.session_notional_cap() == D("0.15")
    assert spring.max_residual_units() == 6
    assert spring.safety_shell_version() == R2C_LABEL
    # the R1 constants are untouched by the switch (R1 rows are immutable)
    assert spring.SESSION_NOTIONAL_CAP == D("0.05") and spring.MAX_RESIDUAL_UNITS == 3
    # unknown rung -> the authorized rung, never a cap outside the ladder
    ld.set_active("R9")
    assert spring.session_notional_cap() == D("0.05") and spring.max_residual_units() == 3
    ld.set_active(None)
    assert spring.safety_shell_version() == R1_LABEL


def test_evaluator_reads_each_rows_residual_cap_from_its_own_shell_label():
    spec = {"dimensions": {"action_distribution_relation": {"pass": {"l1_acted_vs_l5_max": 0.2}},
                           "post_fill_complete_gap": {"pass": {"abs_gap_pp_max_acted": 15.0}}}}

    def row(label, resid):
        return {"acted": "complete", "divergence": "policy_flag:cap_fallback_complete",
                "shadow": {"stochastic": {"sampled": "extend"}},
                "lookup": {"residual_units": resid},
                "relation_ranked": [{"relation": "extend", "p": 0.6},
                                    {"relation": "complete", "p": 0.4}],
                **({"safety_shell_version": label} if label else {})}
    assert gate.shell_residual_cap(row(None, 3)) == 3.0
    assert gate.shell_residual_cap(row(R1_LABEL, 3)) == 3.0
    assert gate.shell_residual_cap(row(R2C_LABEL, 6)) == 6.0
    # unlabelled / R1 rows at 3 = cap-forced; an R2C row at 6 = cap-forced;
    # an R2C row falling back at 3 is BELOW its own cap = the anomaly
    fb = gate.feasibility_decomposition([row(None, 3), row(R1_LABEL, 3), row(R2C_LABEL, 6)], spec)
    assert fb["cap_fallback_attribution"]["verdict"] == "ALL_CAP_FORCED"
    assert fb["forced_by_rule"] == {"residual_cap": 3}
    fb2 = gate.feasibility_decomposition([row(R2C_LABEL, 3)], spec)
    assert fb2["cap_fallback_attribution"]["anomalies_below_cap"] == 1
    assert fb2["cap_fallback_attribution"]["verdict"] == "IMPLEMENTATION_DEFECT_CANDIDATE"


def test_campaign_applies_the_approved_rung_at_the_gate_and_restores_it(
        monkeypatch, tmp_path):
    """The R1 continuation completes -> the R2C approval is consumed after
    the flat preflight -> the new campaign runs under R2C caps with the
    R2C shell label and a 6-market allowance; a restart restores R2C
    from durable state; the consumed approval is never re-run."""
    from tests.test_pmus_l5_continuous_session import _two_run_harness
    monkeypatch.delenv(ld.RUNG_ENV, raising=False)
    monkeypatch.delenv(cont.RUNG_MARKETS_ENV, raising=False)
    shares, tape = {"v": 100.0}, {"trades": 5}
    fake = _two_run_harness(monkeypatch, tmp_path, shares, tape)
    monkeypatch.setattr(cont, "status_has_trades", lambda: True)
    approvals = tmp_path / "approvals.json"
    approvals.write_text(json.dumps({"approvals": [{
        "campaign_id": "R2C-RUN-TEST", "approved_by": "owner",
        "rung": "R2C", "stop_rule": "rung_markets"}]}))
    monkeypatch.setattr(cont, "CAMPAIGN_APPROVALS_FILE", approvals)
    cont.save_campaign_state({
        "saved_utc": "x", "rung": "R1", "campaign_id": "R1-EVIDENCE-CONTINUATION-TEST",
        "stop_rule": "frozen_evidence_minimums", "previous_campaigns": [],
        "filled_markets": ["m1", "m2", "m3"], "settled_here": ["m1", "m2"],
        "outage": None, "liveness_baseline": {}, "recovery_gate": None, "burst": None})
    from src import pmus_settlement_auditor as auditor
    monkeypatch.setattr(auditor, "audit_market", lambda slug, t: {
        "market": slug, "settled": True, "status": "REALIZED",
        "settlement_key": "r", "realized_confirmed": True,
        "realized_usd": "-0.01", "position_net": "0",
        "resolution": {"side": "YES", "tradeId": "r", "updateTime": "t"}})
    ledger_unsettled = ["m3"]
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: list(ledger_unsettled))
    monkeypatch.setattr(cont, "sweep_orphan_orders", lambda t: {
        "seen": 0, "cancelled": [], "foreign": [], "errors": []})
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    met = {k: {"have": 203, "need": 200, "met": True} for k in cont.EVIDENCE_MINIMUM_KEYS}

    def fake_gate(now, force=False):
        cont._fidelity_last["minimums"] = dict(met)
        return None
    monkeypatch.setattr(cont, "fidelity_eval", fake_gate)
    seen = []

    def session(t, clock, sleep, wall, slug, gate_ok, deadline_s, **kw):
        # what the session layer would read at this moment
        seen.append((slug, str(spring.session_notional_cap()),
                     spring.max_residual_units(), spring.safety_shell_version()))
        ledger_unsettled.clear()
        return {"status": "SPRING_NO_FILL", "slug": slug, "orders": [],
                "decisions": [], "inventory": {"up": "0", "dn": "0"}}
    real_settle = cont.settle_held

    def settle(t, slug):
        out = real_settle(t, slug)
        if slug in ledger_unsettled:
            ledger_unsettled.remove(slug)
        return out
    monkeypatch.setattr(cont, "settle_held", settle)
    ticks = itertools.chain([0.0] + [1.0] * 60, itertools.repeat(1000.0))
    rep = cont.run_campaign(fake, clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=session)
    assert rep["continuations"][0]["campaign_id"] == "R2C-RUN-TEST"
    assert rep["continuations"][0]["preflight"]["ok"]
    assert rep["rung"]["ladder_rung"] == "R2C"
    assert rep["rung"]["per_market_cap_usd"] == "0.15"
    assert rep["rung"]["residual_cap_units"] == 6
    assert rep["rung"]["safety_shell_version"] == R2C_LABEL
    assert rep["rung"]["stop_rule"] == "rung_markets"
    assert rep["rung"]["markets_allowed"] == 6
    assert rep["rung"]["worst_case_usd"] == "0.90"
    assert rep["rung"]["daily_loss_stop_usd"] == "-1.00"
    assert rep["rung"]["concurrency"] == 1
    assert rep["rung"]["previous_campaigns"] == ["R1-EVIDENCE-CONTINUATION-TEST"]
    # the session of the new campaign saw the R2C caps and label
    assert seen and seen[-1][1:] == ("0.15", 6, R2C_LABEL)
    saved = json.loads((tmp_path / "l5_campaign_state.json").read_text())
    assert saved["rung"] == "R2C" and saved["campaign_id"] == "R2C-RUN-TEST"
    # restart: the rung is restored from durable state, the approval is
    # consumed, the label the next rows would carry is R2C
    ld.set_active(None)
    started2 = []
    ticks = itertools.chain([0.0] + [1.0] * 20, itertools.repeat(1000.0))
    rep2 = cont.run_campaign(fake, clock=lambda: next(ticks),
                             sleep=lambda s: None, wall=lambda: 1005.0,
                             campaign_s=500.0,
                             session_runner=lambda *a, **k: (
                                 started2.append(spring.safety_shell_version()) or
                                 {"status": "SPRING_NO_FILL", "slug": k.get("slug"),
                                  "orders": [], "decisions": [],
                                  "inventory": {"up": "0", "dn": "0"}}))
    assert rep2["rung"]["ladder_rung"] == "R2C" and rep2["rung"]["campaign_id"] == "R2C-RUN-TEST"
    assert "continuations" not in rep2
    assert all(lbl == R2C_LABEL for lbl in started2)


def test_r1_campaign_state_without_a_rung_stays_on_r1(monkeypatch, tmp_path):
    monkeypatch.delenv(ld.RUNG_ENV, raising=False)
    ld.set_active("R2C")                     # a stale process value never wins
    from tests.test_pmus_l5_continuous_session import _two_run_harness
    fake = _two_run_harness(monkeypatch, tmp_path, {"v": 100.0}, {"trades": 5})
    monkeypatch.setattr(cont, "status_has_trades", lambda: True)
    monkeypatch.setattr(cont, "CAMPAIGN_APPROVALS_FILE", tmp_path / "none.json")
    ticks = itertools.chain([0.0] + [1.0] * 5, itertools.repeat(1000.0))
    rep = cont.run_campaign(fake, clock=lambda: next(ticks), sleep=lambda s: None,
                            wall=lambda: 1005.0, campaign_s=500.0,
                            session_runner=lambda *a, **k: {
                                "status": "SPRING_NO_FILL", "slug": k.get("slug"),
                                "orders": [], "decisions": [],
                                "inventory": {"up": "0", "dn": "0"}})
    assert rep["rung"]["ladder_rung"] == "R1"
    assert rep["rung"]["per_market_cap_usd"] == "0.05"
    assert rep["rung"]["safety_shell_version"] == R1_LABEL


def test_repo_approval_record_names_the_r2c_rung_after_the_r1_continuation():
    appr = cont.load_campaign_approvals()
    ids = [a["campaign_id"] for a in appr]
    assert ids[:2] == ["R1-EVIDENCE-CONTINUATION-2026-09-06", "R2C-RUN-2026-09-06"]
    r2c = appr[1]
    assert r2c["rung"] == "R2C" and r2c["stop_rule"] == "rung_markets"
    assert r2c["shell"] == R2C_LABEL == ld.shell_version(ld.rung("R2C"))
    # consumed in order: R2C is offered only once the R1 continuation is done
    assert cont.approved_continuation("R1-INITIAL", [], appr)["campaign_id"] == \
        "R1-EVIDENCE-CONTINUATION-2026-09-06"
    assert cont.approved_continuation("R1-EVIDENCE-CONTINUATION-2026-09-06",
                                      ["R1-INITIAL"], appr)["campaign_id"] == \
        "R2C-RUN-2026-09-06"
    # OWNER 5561465636: the L5-sized rung is queued behind R2C, never before
    nxt = cont.approved_continuation("R2C-RUN-2026-09-06",
                                     ["R1-INITIAL", "R1-EVIDENCE-CONTINUATION-2026-09-06"],
                                     appr)
    assert nxt is None or nxt["campaign_id"] == "R3L-RUN-2026-09-06"
