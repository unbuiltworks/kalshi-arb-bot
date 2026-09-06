"""OWNER 5556815940: fidelity evidence must be deduplicated by immutable
provenance and idempotent under re-ingestion; the seeded draw must not
repeat across resumed sessions."""
from __future__ import annotations

import copy
import json

from src import l5_fidelity_gate as gate
from tests.test_l5_fidelity_gate import _dec, _order


def _report(sid, slug, n, t0="2026-09-06T02:00:0"):
    decs = []
    for i in range(n):
        d = _dec(slug, "p45-55c|tte>10m|imb1-10", "extend", "extend", "extend",
                 t=f"{t0}{i}+00:00", up="0.02")
        d["session_id"] = sid
        d["decision_idx"] = i
        decs.append(d)
    return {"kind": "session_report", "slug": slug, "session_id": sid,
            "decisions": decs, "orders": [{**_order("extend"), "order_id": f"{sid}-{i}"}
                                          for i in range(n)]}


def test_reingesting_the_same_report_n_times_is_idempotent():
    rep = _report("s1", "m1", 6)
    once = gate.evaluate([rep])
    thrice = gate.evaluate([rep, copy.deepcopy(rep), copy.deepcopy(rep)])
    # overlapping cumulative files: the same rows again plus the file itself
    cumulative = gate.evaluate([rep] + [rep] * 4)
    for out in (thrice, cumulative):
        assert out["live"]["scored_decisions"] == once["live"]["scored_decisions"] == 6
        assert out["live"]["timing_cadence"]["n"] == once["live"]["timing_cadence"]["n"] == 5
        assert out["live"]["action_distribution_relation"]["value"] == \
            once["live"]["action_distribution_relation"]["value"]
        assert out["live"]["size"]["n"] == once["live"]["size"]["n"] == 6
        assert out["evidence_progress"]["minimums"]["live_scored_decisions"]["have"] == 6
    assert thrice["evidence_progress"]["accounting"]["decision_duplicates_dropped"] == 12
    assert thrice["evidence_progress"]["accounting"]["status"] == "OK"


def test_genuinely_new_decisions_increment_exactly_once():
    a = _report("s1", "m1", 6)
    b = _report("s2", "m1", 6, t0="2026-09-06T02:10:0")
    c = _report("s3", "m2", 4, t0="2026-09-06T02:20:0")
    out = gate.evaluate([a, b, a, c, b])
    prog = out["evidence_progress"]
    assert prog["minimums"]["live_scored_decisions"]["have"] == 16
    assert prog["minimums"]["live_markets"]["have"] == 2
    assert prog["accounting"]["per_market"] == {"m1": 12, "m2": 4}
    assert prog["accounting"]["first_t_utc"].startswith("2026-09-06T02:00:00")
    assert prog["accounting"]["last_t_utc"].startswith("2026-09-06T02:20:03")


def test_intervals_only_between_consecutive_unique_decisions_of_one_session():
    a = _report("s1", "m1", 6)
    b = _report("s2", "m1", 6, t0="2026-09-06T02:10:0")
    out = gate.evaluate([a, a, b, b])
    # 5 intervals per session, none across the 10-minute session boundary
    # and none between duplicate copies
    assert out["live"]["timing_cadence"]["n"] == 10
    assert out["live"]["timing_cadence"]["value"]["median_interval_s"] == 1.0


def test_rows_without_provenance_are_reported_not_counted_and_block_the_verdict():
    rep = _report("s1", "m1", 3)
    legacy = {"kind": "session_report", "slug": "m1",
              "decisions": [{k: v for k, v in d.items() if k not in ("t_utc", "decision_id")}
                            for d in rep["decisions"]], "orders": []}
    out = gate.evaluate([rep, legacy])
    acct = out["evidence_progress"]["accounting"]
    assert acct["provenance_missing"] == 3
    assert acct["status"] == "EVIDENCE_ACCOUNTING_INVALID"
    assert out["evidence_progress"]["minimums"]["live_scored_decisions"]["status"] == \
        "EVIDENCE_ACCOUNTING_INVALID"
    assert out["verdict"]["blocked_by"] == "EVIDENCE_ACCOUNTING_INVALID"
    assert out["verdict"]["label"] == "L5 replication in validation"


def test_seed_offset_makes_resumed_sessions_draw_new_indices(monkeypatch, tmp_path):
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _ledger_with_fill)
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    from src import pmus_spring_l5_session as spring
    from src import pmus_l5_persistence_v2 as pv2
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")

    def prop(model, base_state, last_rel, last_size, residual, clone_avg_cost=None):
        return {"recommended_relation": "extend", "persist_extend_p": 0.5,
                "switch_complete_p": 0.5, "next_reevaluation_s": 2.0,
                "relation_ranked": [{"relation": "extend", "p": 0.5},
                                    {"relation": "complete", "p": 0.5}],
                "state_n": 40, "off_distribution": False}
    monkeypatch.setattr(pv2, "proposal", prop)
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "3")

    def run(offset):
        fake = Fake(bid="0.4300", ask="0.4350", fill_all=False)
        fake.positions["held"] = {"netPositionDecimal": "0.02"}
        rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                                 wall=lambda: 1000.0, slug="held", gate_ok=True,
                                 resume=True, decision_seed_offset=offset)
        return rep
    r0, r0b, r3 = run(0), run(0), run(3)
    seeds = lambda r: [d["shadow"]["stochastic"]["seed"] for d in r["decisions"]]
    assert seeds(r0) == seeds(r0b)                      # replayable
    assert seeds(r0) != seeds(r3)                       # not a repeat
    assert [d["decision_idx"] for d in r3["decisions"]] == [3, 4, 5]
    assert r3["decision_seed_offset"] == 3 and len(r3["session_id"]) == 16
    ids = {d["decision_id"] for d in r0["decisions"] + r3["decisions"]}
    assert len(ids) == 6                                # unique provenance


def test_campaign_passes_the_ledger_decision_count_as_the_seed_offset(monkeypatch, tmp_path):
    from src import pmus_l5_continuous_session as cont
    from src import pmus_q1_executor as q1b
    monkeypatch.setenv(q1b.STATE_ENV, str(tmp_path))
    rows = [{"kind": "session_report", "slug": "m1", "decisions": [{}] * 6},
            {"kind": "session_report", "slug": "m1", "decisions": [{}] * 6},
            {"kind": "session_report", "slug": "m2", "decisions": [{}] * 2}]
    (tmp_path / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert cont.ledger_decision_count("m1") == 12
    assert cont.ledger_decision_count("m2") == 2
    assert cont.ledger_decision_count("m3") == 0


def test_read_only_counterfactuals_and_side_telemetry_are_recorded(monkeypatch, tmp_path):
    """OWNER 5556751941 #1/#2/#3: 2 s cadence counterfactual, COMPLETE
    L5 target-price counterfactual and OPEN side-mapping telemetry are
    recorded read-only; live orders are unchanged."""
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _ledger_with_fill,
                                                        _open_proposal)
    from tests.test_pmus_spring_l5_session import Fake, _ticking, _script_proposals
    from src import pmus_spring_l5_session as spring
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    _script_proposals(monkeypatch, ["complete"])
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "1")
    fake = Fake(bid="0.4300", ask="0.4350", fill_all=False)
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    w = {"t": 1000.0}

    def wall():
        w["t"] += 0.7
        return w["t"]
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=wall, slug="held", gate_ok=True, resume=True)
    d = rep["decisions"][0]
    cf = d["counterfactual_l5_price"]
    # NO complete: L5 would join the NO touch (1 - yes ask = 0.565); the
    # live order used the fee-safe bound 0.55
    assert cf["l5_touch_px"] == "0.565" and cf["executed_px"] == "0.55"
    assert cf["class"] == "safety_override:fee_safe_complement_bound"
    assert cf["labels"].startswith("READ-ONLY")
    o = rep["orders"][0]
    assert o["px"] == "0.55"                          # live order unchanged
    assert o["l5_touch_px"] == "0.565"
    assert o["rest_book"]["polls"] >= 1 and o["rest_book"]["min_opp_ask"] == "0.5700"
    assert o["l5_touch_reached_by_opposite_ask"] is False
    cf2 = rep["counterfactual_2s"]
    assert cf2 and all(c["labels"].startswith("SYNTHETIC") for c in cf2)
    assert cf2[0]["v2_recommended"] == "complete"      # scripted fixture has no masses
    # the evaluator scores them SEPARATELY and never as live decisions
    out = gate.evaluate([{"slug": "held", "session_id": rep["session_id"],
                          "decisions": rep["decisions"], "orders": rep["orders"],
                          "counterfactual_2s": cf2}])
    assert out["counterfactual_2s"]["n"] == len(cf2)
    assert out["live"]["scored_decisions"] == 0          # scripted fixture: no masses -> not scoreable
    # OPEN side telemetry on a fresh session
    _open_proposal(monkeypatch)
    fake2 = Fake(bid="0.5000", ask="0.5050")
    rep2 = spring.run_session(fake2, clock=_ticking(), sleep=lambda s: None,
                              wall=lambda: 1000.0, slug="mkt-x", gate_ok=True)
    side = rep2["decisions"][0]["open_side"]
    assert side["convention_side"] == side["executed_side"] == rep2["orders"][0]["side"]
    assert side["no_touch"] == "0.4950" and side["yes_touch"] == "0.5000"
    assert side["convention_side"] == "NO"            # cheaper touch


def test_open_side_convention_is_deterministic():
    from decimal import Decimal as D
    from src import pmus_sports_l5_decision as l5d
    assert l5d.open_side_yes({"bid": D("0.30"), "ask": D("0.305")}) is True    # YES 0.30 < NO 0.695
    assert l5d.open_side_yes({"bid": D("0.60"), "ask": D("0.605")}) is False   # NO 0.395 < YES 0.60
    assert l5d.open_side_yes({"bid": D("0.50"), "ask": D("0.50")}) is True     # tie -> YES


def test_summary_snapshots_are_rejected_by_kind_and_counted(tmp_path):
    """OWNER 5557786528 #2/#3/#5: periodic fidelity summaries are
    snapshots, never evidence rows -- even when they carry a `decisions`
    key -- and the rejection is visible in the accounting block."""
    rep = _report("s1", "m1", 4)
    snap = {"kind": "fidelity_gate", "label": "L5 replication in validation",
            "decisions": rep["decisions"],                     # a snapshot echo
            "evidence_progress": {"minimums": {"live_scored_decisions": {"have": 240}}}}
    snap2 = {"kind": "L5_FIDELITY", "decisions": rep["decisions"]}
    p = tmp_path / "ledger.jsonl"
    p.write_text(json.dumps(rep) + "\n" + json.dumps(snap) + "\n"
                 + json.dumps({"kind": "funnel", "submitted": 3}) + "\n"
                 + "PMUS_L5_CAMPAIGN " + json.dumps(snap2) + "\n"
                 + json.dumps(rep) + "\n")
    ing = gate.read_reports_accounted(p)
    assert len(ing["reports"]) == 2 and ing["rows_total"] == 5
    assert ing["summary_rows_ignored"] == 3 and ing["other_rows_ignored"] == 0
    assert ing["raw_decision_rows"] == 8
    out = gate.evaluate(ing["reports"], ingest=ing)
    acct = out["evidence_progress"]["accounting"]
    assert acct["raw_decision_rows"] == 8 and acct["unique_decisions"] == 4
    assert acct["duplicates_dropped"] == 4 + 4          # decisions + orders
    assert acct["identity_missing_rows"] == 0
    assert acct["summary_rows_ignored"] == 3
    assert acct["second_pass_idempotent"] is True
    assert acct["evidence_accounting_valid"] is True
    # evaluate() itself refuses summary kinds even when handed directly
    assert gate.evaluate([rep, snap, snap2])["live"]["scored_decisions"] == 4


def _verdict_inputs(out):
    keep = {k: out[k] for k in ("live", "evidence_progress", "divergence_classes",
                                "counterfactual_2s", "largest_mismatch")}
    keep["evidence_progress"] = {k: v for k, v in keep["evidence_progress"].items()}
    keep["evidence_progress"]["accounting"] = {
        k: v for k, v in keep["evidence_progress"]["accounting"].items()
        if k not in ("duplicates_dropped", "decision_duplicates_dropped",
                     "order_duplicates_dropped", "raw_decision_rows", "ingest")}
    keep["verdict"] = {k: v for k, v in out["verdict"].items()}
    return json.dumps(keep, sort_keys=True, default=str)


def test_same_corpus_once_twice_and_in_overlapping_chunks_is_byte_identical():
    """OWNER 5557786528 #5 acceptance: a second evaluator pass over an
    unchanged corpus cannot advance any minimum by even one count."""
    a = _report("s1", "m1", 6)
    b = _report("s2", "m1", 6, t0="2026-09-06T02:10:0")
    c = _report("s3", "m2", 4, t0="2026-09-06T02:20:0")
    corpus = [a, b, c]
    once = gate.evaluate(corpus)
    twice = gate.evaluate(corpus + copy.deepcopy(corpus))
    chunks = gate.evaluate([a, b] + [b, c] + [a, c] + corpus)
    snapshot = gate.evaluate(corpus + [{"kind": "fidelity_gate",
                                        "decisions": a["decisions"]}])
    ref = _verdict_inputs(once)
    for other in (twice, chunks, snapshot):
        assert _verdict_inputs(other) == ref
        mins = other["evidence_progress"]["minimums"]
        assert mins["live_scored_decisions"]["have"] == 16
        assert mins["live_timed_intervals"]["have"] == 13
        assert mins["live_markets"]["have"] == 2
    assert twice["evidence_progress"]["accounting"]["decision_duplicates_dropped"] == 16


def test_non_idempotent_identity_fails_closed(monkeypatch):
    """OWNER 5557786528 #4: if the second pass does not reproduce the
    first, evidence_accounting_valid=false and no verdict is emitted."""
    rep = _report("s1", "m1", 5)
    calls = {"n": 0}
    real = gate.dedupe

    def flaky(decs, orders):
        calls["n"] += 1
        out = real(decs, orders)
        if calls["n"] == 2:
            out["decisions"] = out["decisions"][:-1]
        return out
    monkeypatch.setattr(gate, "dedupe", flaky)
    out = gate.evaluate([rep])
    acct = out["evidence_progress"]["accounting"]
    assert acct["second_pass_idempotent"] is False
    assert acct["evidence_accounting_valid"] is False
    assert acct["status"] == "EVIDENCE_ACCOUNTING_INVALID"
    assert out["verdict"]["blocked_by"] == "EVIDENCE_ACCOUNTING_INVALID"
    # ingest that disagrees with the flattened raw rows also fails closed
    out2 = gate.evaluate([rep], ingest={"raw_decision_rows": 99, "rows_total": 1,
                                        "summary_rows_ignored": 0})
    assert out2["evidence_progress"]["accounting"]["evidence_accounting_valid"] is False


def test_shadow_counters_expose_raw_unique_and_dropped():
    spec = gate.load_spec()
    rolling = {"research_arm_stochastic": {
        "arm": "reprice_180s:stochastic", "windows_since_freeze": 100,
        "window_rows": 100, "unique_window_ids": 100,
        "windows_entered_unique": 92, "shadow_raw_windows": 103,
        "shadow_unique_windows": 92, "shadow_duplicates_dropped": 3,
        "first_entry_utc": "2026-09-06T02:11:00+00:00",
        "last_entry_utc": "2026-09-06T07:28:00+00:00",
        "action_scoring": {"scored_decisions": 141, "l1_sampled_vs_l5": 0.118,
                           "l1_acted_vs_l5": 0.489, "off_distribution_rate_pct": 16.0}}}
    sh = gate.score_shadow(rolling, spec)
    assert sh["windows_since_freeze"] == 92 == sh["shadow_unique_windows"]
    assert sh["shadow_raw_windows"] == 103 and sh["shadow_duplicates_dropped"] == 3
    prog = gate.evidence_progress({"scored_decisions": 0}, sh, spec)
    assert prog["minimums"]["shadow_windows"]["have"] == 92


def test_declared_maintenance_window_is_a_planned_outage_not_evidence(tmp_path):
    """OWNER 5557988072 #6: the declared window classifies the outage,
    excludes any interval overlapping it from cadence evidence, and
    reports (never counts) decisions time-stamped inside it."""
    from src import pmus_l5_continuous_session as cont
    wins = cont.load_maintenance_windows()
    assert wins and wins[0]["id"] == "pmus-2026-09-06-scheduled"
    assert wins[0]["venue"] == "polymarket_us"
    # the live outage's first error (07:15:29Z) is inside; 06:00Z is not
    o = cont.classify_outage({"start_utc": "2026-09-06T07:15:29.386001+00:00",
                              "first_error": "Halt:GET_RETRIES_EXHAUSTED"})
    assert o["class"] == "scheduled_maintenance"
    assert o["maintenance"]["id"] == "pmus-2026-09-06-scheduled"
    assert cont.classify_outage({"start_utc": "2026-09-06T06:00:00+00:00"})["class"] == "unplanned"
    assert cont.classify_outage({"start_utc": "2026-09-06T10:00:01+00:00"})["class"] == "unplanned"
    assert cont.classify_outage(None) is None
    # cadence: a session straddling the window contributes no interval
    # across it; decisions inside it are reported as an anomaly
    rep = _report("s1", "m1", 3, t0="2026-09-06T07:10:0")      # 07:10:00..02
    late = _report("s1", "m1", 2, t0="2026-09-06T10:05:0")     # after the window
    for i, d in enumerate(late["decisions"]):
        d["decision_idx"] = 10 + i
    inside = _report("s1", "m1", 1, t0="2026-09-06T08:00:0")
    inside["decisions"][0]["decision_idx"] = 20
    corpus = [{**rep, "decisions": rep["decisions"] + inside["decisions"] + late["decisions"],
               "orders": []}]
    plain = gate.evaluate(corpus)
    marked = gate.evaluate(corpus, maintenance=wins)
    # without the declaration the straddling gaps would count as intervals
    assert plain["live"]["timing_cadence"]["n"] == 5
    assert marked["live"]["timing_cadence"]["n"] == 3         # 2 excluded
    m = marked["evidence_progress"]["accounting"]["maintenance"]
    assert m == {"windows_declared": 1, "decisions_inside_maintenance": 1,
                 "intervals_excluded_maintenance": 2,
                 "class": m["class"]}
    # the exclusion never changes any other dimension or the decision count
    assert marked["live"]["scored_decisions"] == plain["live"]["scored_decisions"] == 6
    assert marked["live"]["action_distribution_relation"] == plain["live"]["action_distribution_relation"]
