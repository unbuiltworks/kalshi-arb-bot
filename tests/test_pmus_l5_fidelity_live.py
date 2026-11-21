"""OWNER 5553674206: the live shadow fields are pure lookups -- seeded,
reproducible, never acted on -- and degrade to `unavailable` without the
artifact instead of changing anything."""
from __future__ import annotations

import json

from src import pmus_l5_fidelity_live as fid


def _art(tmp_path):
    p = tmp_path / "fid.json"
    p.write_text(json.dumps({
        "size_policy": {
            "by_state_relation": {"p45-55c|tte>10m|flat|open": {
                "n": 40, "shares_p25": 5.0, "shares_p50": 10.0,
                "shares_p75": 15.0, "usd_p50": 5.0, "usd_p75": 7.5}},
            "by_relation": {"extend": {"n": 900, "shares_p50": 8.0,
                                       "usd_p50": 4.0}}},
        "aggressiveness_policy": {
            "p_taker_by_tte": {"tte<30s": {"n": 600, "p_taker": 0.585},
                               "tte>10m": {"n": 16000, "p_taker": 0.0275}},
            "p_taker_by_tte_relation": {"tte<30s|complete": {"n": 300,
                                                             "p_taker": 0.7}}},
    }))
    return p


def test_sample_relation_is_seeded_and_reproducible():
    ranked = [{"relation": "extend", "p": 0.7556},
              {"relation": "complete", "p": 0.2443},
              {"relation": "open", "p": 0.0001}]
    seed = fid.seed_material("asc-cfb-x", 3, "abc")
    a = fid.sample_relation(ranked, seed)
    b = fid.sample_relation(ranked, seed)
    assert a == b and a["sampled"] in ("extend", "complete", "open")
    assert a["rank"] in (1, 2, 3) and 0.0 <= a["u"] < 1.0
    # the draw walks the CDF over sorted relation names: u=0 -> 'complete'
    assert fid.sample_relation(ranked, "00000000")["sampled"] == "complete"
    assert fid.sample_relation(ranked, "ffffffff")["sampled"] == "open"
    assert fid.sample_relation([], seed)["sampled"] is None


def test_shadow_fields_degrade_without_artifact():
    out = fid.shadow_fields(None, slug="m", decision_idx=0, model_sha="x",
                            base_state="p45-55c|tte>10m|flat",
                            relation="open", relation_ranked=[
                                {"relation": "open", "p": 0.9}],
                            tte_s=1e9)
    assert out["artifact_sha"] is None
    assert out["l5_target_size"] == {"source": "unavailable"}
    assert out["l5_target_aggressiveness"]["source"] == "unavailable"
    assert out["stochastic"]["sampled"] == "open"


def test_shadow_fields_measure_size_and_aggressiveness_gap(tmp_path):
    art = fid.load(_art(tmp_path))
    out = fid.shadow_fields(
        art, slug="m", decision_idx=2, model_sha="x",
        base_state="p45-55c|tte>10m|flat", relation="open",
        relation_ranked=[{"relation": "open", "p": 0.9}], tte_s=20.0,
        executed_qty="0.02", executed_px="0.45")
    assert out["l5_target_size"]["source"] == "state|relation"
    assert out["executed_safe_size"] == {"contracts": "0.02", "usd": "0.0090"}
    assert out["size_ratio_l5_over_ours"] == 500.0
    assert out["l5_target_aggressiveness"]["tte_bucket"] == "tte<30s"
    assert out["l5_target_aggressiveness"]["p_taker"] == 0.585
    comp = fid.l5_target_aggressiveness(art, 20.0, "complete")
    assert comp["source"] == "tte|relation" and comp["p_taker"] == 0.7
    assert fid.l5_target_size(art, "nowhere", "extend")["source"] == "relation"


def test_taker_completion_economics_is_theoretical_and_fee_aware():
    e = fid.taker_completion_economics("0.4867", "0.52")
    assert e["available"] and e["pair_cost"] == "1.0067"
    assert e["fee_safe"] is False
    ok = fid.taker_completion_economics("0.40", "0.55")
    # 0.06 * 0.55 * 0.45 = 0.01485 -> 0.0148 (half-even), edge 0.0352
    assert ok["taker_fee_per_contract"] == "0.0148" and ok["fee_safe"] is True
    assert fid.taker_completion_economics(None, "x")["available"] is False
