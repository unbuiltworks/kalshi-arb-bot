"""Persistence-v2 arm regressions (issue #167 comment 5519823659):
last_rel/last_size-conditioned distribution with the validated shrinkage
backoff, side-by-side with the untouched frozen v1, plus the THEORETICAL
action proposal mapping."""
from __future__ import annotations

import json
import time
from pathlib import Path

from src import pmus_l5_action_shadow as l5s
from src import pmus_l5_persistence_v2 as pv2


def _tiny_v2():
    base = "p45-55c|tte>10m|imb1-10"
    return {
        "full": {f"{base}|extend|q<5": {"extend|q<5": 60,
                                        "complete|q<5": 40},
                 f"{base}|complete|q<5": {"open|q<5": 5,
                                          "complete|q<5": 5}},
        "base": {base: {"extend|q<5": 65, "complete|q<5": 45,
                        "open|q<5": 5}},
        "marginal": {"extend|q<5": 70, "complete|q<5": 50, "open|q<5": 10,
                     "sell|q<5": 1},
        "cadence_modal": {f"{base}|extend|q<5": "gap<2s"},
        "_sha256_16": "v2testmodel00000",
    }


def test_committed_v2_artifact_loads_and_v1_is_untouched():
    m = pv2.load_v2()
    assert m["n_transitions"] > 60000 and m["n_full_states"] > 500
    assert "last_rel" in m["formulation"] and "last_size" in m["formulation"]
    assert len(m["_sha256_16"]) == 16
    # the frozen v1 artifact must not condition on last_rel — and must
    # not have been overwritten by the v2 build
    v1 = json.loads((pv2.ARTIFACT.parent
                     / "l5_action_model.json").read_text())
    assert "last_rel" not in json.dumps(v1["buckets"])
    assert all(k.count("|") == 2 for k in v1["states"])   # 3-part v1 keys


def test_v2_conditions_on_last_rel_in_the_committed_artifact():
    """The whole point of the arm: for at least one populated base state,
    the recommended relation DIFFERS by last_rel — proof that the arm
    conditions on it rather than merely storing it."""
    m = pv2.load_v2()
    diffs = 0
    for base in list(m["base"])[:200]:
        a = pv2.v2_dist(m, base, "extend", "q<5")
        b = pv2.v2_dist(m, base, "complete", "q<5")
        if a["backed_off"] or b["backed_off"]:
            continue
        ra = max(pv2.relation_mass(a["dist"]).items(), key=lambda kv: kv[1])
        rb = max(pv2.relation_mass(b["dist"]).items(), key=lambda kv: kv[1])
        diffs += ra[0] != rb[0]
    assert diffs > 0


def test_shrinkage_backoff_full_to_base_to_marginal():
    m = _tiny_v2()
    base = "p45-55c|tte>10m|imb1-10"
    d = pv2.v2_dist(m, base, "extend", "q<5")
    assert d["backed_off"] is False and d["n"] == 100
    assert d["dist"]["extend|q<5"] > d["dist"]["complete|q<5"]
    d2 = pv2.v2_dist(m, base, "open", "q>40")     # unseen full state
    assert d2["backed_off"] is True
    d3 = pv2.v2_dist(m, "p<10c|tte<30s|flat", "open", "q<5")
    assert d3["backed_off"] is True               # marginal fallback
    assert abs(sum(d3["dist"].values()) - 1.0) < 1e-9


def test_proposal_maps_relation_to_side_zone_size_timing():
    m = _tiny_v2()
    base = "p45-55c|tte>10m|imb1-10"
    p = pv2.proposal(m, base, "extend", "q<5", residual=2,
                     clone_avg_cost="0.48")
    assert p["recommended_relation"] == "extend"
    assert p["side"] == "YES" and p["price_zone"] == "maker_join_current_bid"
    assert p["persist_extend_p"] > p["switch_complete_p"] > 0
    assert p["next_reevaluation_s"] == 2.0        # historical modal cadence
    assert "q=1" in p["size"]
    assert "THEORETICAL" in p["labels"]
    # complete against a positive residual proposes the NO side at the bound
    m["full"][f"{base}|extend|q<5"] = {"complete|q<5": 90, "extend|q<5": 10}
    p2 = pv2.proposal(m, base, "extend", "q<5", residual=1,
                      clone_avg_cost="0.48")
    assert p2["recommended_relation"] == "complete"
    assert p2["side"] == "NO"
    assert "complement_bound(0.48)" in p2["price_zone"]


def test_shadow_rows_emit_v1_and_v2_side_by_side():
    s = {"bid": "0.4800", "ask": "0.4850",
         "trades": [{"t": time.time(), "px": "0.4800", "dir": "sell"}]}
    row = l5s.market_shadow_row(s, time.time(),
                                {"gate_status": "NOT_READY"})
    v2 = row["persistence_v2"]
    assert not v2.get("error"), v2
    assert v2["recommended_relation"] in ("open", "extend", "complete",
                                          "sell")
    assert "persist_extend_p" in v2 and "switch_complete_p" in v2
    assert v2["model_sha256_16"] != row["model_sha256_16"]  # two artifacts
    assert "next_reevaluation_s" in v2 and "price_zone" in v2


def test_v2_module_is_structurally_read_only():
    src = Path(pv2.__file__).read_text()
    for banned in ("urllib", "websockets", "requests", "Transport(",
                   "/v1/orders"):
        assert banned not in src, banned
