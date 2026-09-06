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
