"""OWNER 5558872678 #1/#4: one NEW Gemini window traced end to end --
fresh BBO/prints -> stochastic decision -> persisted shadow record with
the canonical lookup inputs -> /rolling -> the PMUS frozen evaluator --
and proven to survive a service restart and an on-disk duplicate while
being consumed exactly once (one canonical window, its decisions counted
once). Read-only throughout: orders_placed stays 0."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from src import gemini_l5_shadow_service as svc
from src import gemini_l5_shadow_v2 as gs
from src import l5_fidelity_gate as gate
from tests.test_gemini_l5_shadow_service import _scripted

ARM = "reprice_180s:stochastic"
# after the stochastic-arm freeze (2026-09-05T23:43:19Z): the frozen arm
# spec scores only windows entered at/after it
NOW0 = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc).timestamp()

# the persisted per-decision lookup contract (OWNER 5558872678 #2):
# canonical + v1 state keys, relation, both last_size values, pre/post-fill
# identity, residual in behavioral shares, price convention (NO-side
# transform named), v1 off flag + full key, lookup tier, model + code sha,
# and the raw state inputs (prices, TTE, residual, last own fill price)
CANONICAL_KEYS = ("lookup_version", "state", "state_v1", "last_rel",
                  "last_size_executed", "last_size_behavioral", "pre_fill",
                  "residual_shares_behavioral", "price_convention",
                  "off_distribution_v1", "full_key_v1", "lookup_tier",
                  "model_sha", "code_sha", "state_inputs")
DECISION_KEYS = ("t", "state", "sampled", "acted", "refusal", "tte_s",
                 "yes_bid", "yes_ask", "residual_units", "last_rel")


def _service(tmp_path, fetch, t):
    return svc.ShadowService(fetch=fetch, clock=lambda: t["v"],
                             sleep=lambda x: t.__setitem__("v", t["v"] + x),
                             root=tmp_path)


def _arm_records(tmp_path, sym):
    recs = [json.loads(l) for l in
            (tmp_path / "records.jsonl").read_text().splitlines()]
    return [r for r in recs if r["symbol"] == sym
            and f"{r['shell']}:{r['policy']}" == ARM]


def test_new_canonical_window_survives_restart_and_is_consumed_exactly_once(
        tmp_path):
    sym, exp, fetch = _scripted(NOW0)
    t = {"v": exp - 295.0}
    s1 = _service(tmp_path, fetch, t)
    s1.run(until=exp + 40.0)
    assert s1.status()["orders_placed"] == 0

    # (1) the persisted research-arm record: one window, entered, every
    # decision carrying the canonical lookup inputs / provenance
    arm = _arm_records(tmp_path, sym)
    assert len(arm) == 1
    rec = arm[0]
    assert rec["status"] in ("MATCHED", "RESIDUAL_HOLD", "NO_FILL")
    assert rec["decisions"], "the window produced decisions"
    for d in rec["decisions"]:
        lk = d["lookup"]
        assert all(k in lk for k in CANONICAL_KEYS), sorted(lk)
        assert lk["lookup_version"] == gs.LOOKUP_VERSION == 3
        assert lk["code_sha"] == gs.CODE_SHA and lk["model_sha"]
        assert "tte_s" in lk["state_inputs"] and "yes_bid" in lk["state_inputs"]
        assert all(k in d for k in DECISION_KEYS), sorted(d)
    n_dec = len(rec["decisions"])

    # (2) /rolling: the canonical window and its decisions are counted
    # once, separately from the raw/unique breadth counters
    rep1 = s1.rolling_report(t["v"])
    ra1 = rep1["research_arm_stochastic"]
    prov1 = ra1["lookup_provenance"]
    assert prov1["lookup_version_gate"] == 3
    assert prov1["v2_windows"] == 1 and prov1["v2_decisions"] == n_dec
    assert prov1["legacy_decisions"] == 0 and prov1["unknown_decisions"] == 0
    assert ra1["windows_entered_unique"] == 1 == ra1["shadow_unique_windows"]
    assert ra1["action_scoring_corrected_gate"]["scored_decisions"] == n_dec

    # (3) the PMUS frozen evaluator consumes it: canonical status, one
    # canonical window, the corrected gate's n == the window's decisions
    spec = gate.load_spec()
    sh1 = gate.score_shadow(rep1, spec)
    assert sh1["lookup_status"] == "OK:lookup_version_3"
    assert sh1["windows_entered_unique_v2"] == 1
    assert sh1["action_distribution_relation"]["n"] == n_dec
    assert sh1["shadow_unique_windows"] == 1

    # (4) restart inside the window's grace period + a duplicated record
    # line on disk: restored once, never replayed twice, same counts
    line0 = (tmp_path / "records.jsonl").read_text().splitlines()[0]
    with (tmp_path / "records.jsonl").open("a") as f:
        f.write(line0 + "\n")
    t["v"] = exp - 100.0
    s2 = _service(tmp_path, fetch, t)
    s2.run(until=exp + 40.0)
    assert len(_arm_records(tmp_path, sym)) == 1
    rep2 = s2.rolling_report(t["v"])
    ra2 = rep2["research_arm_stochastic"]
    assert ra2["lookup_provenance"]["v2_windows"] == 1
    assert ra2["lookup_provenance"]["v2_decisions"] == n_dec
    assert ra2["shadow_duplicates_dropped"] >= 1
    sh2 = gate.score_shadow(rep2, spec)
    assert sh2["windows_entered_unique_v2"] == 1
    assert sh2["action_distribution_relation"]["n"] == n_dec
    assert sh2["action_distribution_relation"]["value"] == \
        sh1["action_distribution_relation"]["value"]

    # (5) the full evaluate() path (what the probe logs) sees the same
    # single canonical window
    out = gate.evaluate([], rolling=rep2, spec=spec)
    assert out["shadow"]["windows_entered_unique_v2"] == 1
    assert out["shadow"]["lookup_status"] == "OK:lookup_version_3"
    assert out["evidence_progress"]["minimums"]["shadow_windows"]["have"] == 1
    assert out["evidence_progress"]["minimums"]["shadow_windows"]["met"] is False


def test_evaluator_fails_closed_when_the_service_gates_another_lookup_version():
    spec = gate.load_spec()
    rep = {"research_arm_stochastic": {
        "arm": ARM, "windows_entered_unique": 50,
        "lookup_provenance": {"lookup_version_gate": 2, "v2_decisions": 40,
                              "legacy_decisions": 0, "v2_windows": 20},
        "action_scoring_corrected_gate": {"scored_decisions": 40,
                                          "l1_sampled_vs_l5": 0.01,
                                          "l1_acted_vs_l5": 0.01,
                                          "off_distribution_rate_pct": 0.0}}}
    sh = gate.score_shadow(rep, spec)
    assert sh["lookup_status"].startswith("LOOKUP_VERSION_MISMATCH")
    assert sh["action_distribution_relation"]["status"] == "UNMEASURED"
    assert sh["off_distribution_rate"]["status"] == "UNMEASURED"
