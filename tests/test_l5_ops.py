"""Deterministic tests for the 24/7 read-only ops layer (ops.py).

Network is stubbed by monkeypatching ops._fetch; all paths are redirected
into tmp_path so no repo data is touched.
"""
from __future__ import annotations

import json

import pytest

from src.polymarket_l5 import ops


@pytest.fixture()
def ops_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(ops, "OPS_DIR", tmp_path / "ops")
    monkeypatch.setattr(ops, "FAIL_DIR", tmp_path / "ops" / "failures")
    monkeypatch.setattr(ops, "STATE", tmp_path / "ops" / "state.json")
    monkeypatch.setattr(ops, "HEARTBEAT", tmp_path / "ops" / "heartbeat.json")
    return tmp_path / "ops"


def _rows():
    # two TRADE fills in one 5m window + one non-window row
    return [
        {"type": "TRADE", "timestamp": 1000070, "outcome": "Down",
         "side": "BUY", "price": 0.35, "size": 4, "usdcSize": 1.4,
         "slug": "btc-updown-5m-1000000", "transactionHash": "0x2"},
        {"type": "TRADE", "timestamp": 1000060, "outcome": "Up",
         "side": "BUY", "price": 0.60, "size": 10, "usdcSize": 6.0,
         "slug": "btc-updown-5m-1000000", "transactionHash": "0x1"},
        {"type": "TRADE", "timestamp": 1000050, "outcome": "Yes",
         "side": "BUY", "price": 0.50, "size": 1, "usdcSize": 0.5,
         "slug": "will-x-happen", "transactionHash": "0x0"},
    ]


def test_collect_is_idempotent_and_restart_safe(ops_dirs, monkeypatch):
    calls = {"n": 0}

    def fake_fetch(url, retries=3):
        calls["n"] += 1
        return _rows() if "offset=0" in url else []

    monkeypatch.setattr(ops, "_fetch", fake_fetch)
    r1 = ops.run_collect("polymarket", "0xwallet")
    assert r1["rows_new"] == 3 and r1["cursor_ts"] == 1000070
    # second run: same feed -> zero new rows, cursor unchanged
    r2 = ops.run_collect("polymarket", "0xwallet")
    assert r2["rows_new"] == 0 and r2["cursor_ts"] == 1000070
    # state survives "restart" (fresh read from disk)
    assert ops._read_state()["rows_collected_total"] == 3
    day_files = list(ops_dirs.glob("activity_*.jsonl"))
    assert len(day_files) == 1
    lines = day_files[0].read_text().strip().splitlines()
    assert len(lines) == 3
    # provenance stamped on every raw row
    row = json.loads(lines[0])
    assert row["_venue"] == "polymarket" and row["_fp"] and row["_collected_utc"]
    hb = json.loads((ops_dirs / "heartbeat.json").read_text())
    assert hb["status"] == "ok" and hb["read_only"] is True


def test_shadow_scores_new_fills_against_baselines(ops_dirs, monkeypatch):
    monkeypatch.setattr(ops, "_fetch",
                        lambda url, retries=3: _rows() if "offset=0" in url else [])
    ops.run_collect("polymarket", "0xwallet")
    # freeze "now" near the synthetic timestamps so day files are found
    monkeypatch.setattr(ops.time, "time", lambda: 1000070.0 + 60)
    report = ops.run_shadow("polymarket")
    # the will-x-happen row is filtered by the adapter; 2 canonical fills
    assert report["fills_processed"] == 2 and report["windows"] == 1
    assert report["live_both_sides_pct"] == 100.0
    assert report["live_median_pair_cost"] == pytest.approx(0.95)
    assert report["action_model"]["decisions_scored"] == 1
    # cursor advanced: re-run processes nothing new
    assert ops.run_shadow("polymarket")["fills_processed"] == 0


def test_failure_artifact_and_heartbeat_flip(ops_dirs, monkeypatch):
    def boom(url, retries=3):
        raise RuntimeError("synthetic outage")

    monkeypatch.setattr(ops, "_fetch", boom)
    # run_collect tolerates fetch returning None, so simulate a hard failure
    try:
        raise RuntimeError("synthetic outage")
    except RuntimeError as exc:
        path = ops._failure("collect", exc, extra="ctx")
    saved = json.loads(path.read_text())
    assert saved["stage"] == "collect" and "synthetic outage" in saved["error"]
    assert saved["context"] == {"extra": "ctx"}
    hb = json.loads((ops_dirs / "heartbeat.json").read_text())
    assert hb["status"] == "error" and hb["failure_artifact"] == str(path)


# ---------- true ex-ante scorer ----------

def _fake_model():
    from src.polymarket_l5 import exante_scorer as ex
    model = {
        "since_epoch": 1774569600, "n_decisions": 100,
        "states": {
            "sA": {"n": 80, "action_probs": {"complete|q5-15": 0.6,
                                             "extend|q5-15": 0.4},
                   "modal_action": "complete|q5-15"},
            "sB": {"n": 20, "action_probs": {"extend|q<5": 1.0},
                   "modal_action": "extend|q<5"},
        },
    }
    # pooled marginal: complete|q5-15 48/100, extend|q5-15 32/100, extend|q<5 20/100
    import collections
    pooled = collections.defaultdict(float)
    tot = 0.0
    for st in model["states"].values():
        for a, p in st["action_probs"].items():
            pooled[a] += st["n"] * p
        tot += st["n"]
    model["_marginal"] = {a: v / tot for a, v in pooled.items()}
    model["_vocab"] = sorted(pooled)
    model["_sha256_16"] = "testsha"
    return model


def test_exante_scoring_arms_and_off_distribution():
    from src.polymarket_l5 import exante_scorer as ex
    model = _fake_model()
    pred = ex._predict(model, "sA")
    row = ex._score(pred, model, "complete|q5-15")
    assert row["p_conditional"] == 0.6
    assert row["p_marginal"] == 0.48
    assert row["exact_hit_conditional"] is True
    assert row["relation_hit_conditional"] is True
    # observed action the state never produced
    row2 = ex._score(pred, model, "extend|q<5")
    assert row2["p_conditional"] == 0.0 and row2["exact_hit_conditional"] is False
    # unvisited state -> off-distribution, conditional metrics absent
    row3 = ex._score(ex._predict(model, "sZ"), model, "complete|q5-15")
    assert row3["off_distribution"] is True
    assert row3["p_conditional"] is None and row3["p_marginal"] == 0.48


def test_exante_summarize_predeclared_comparison(tmp_path):
    from src.polymarket_l5 import exante_scorer as ex
    model = _fake_model()
    tape = tmp_path / "tape.jsonl"
    rows = [{"kind": "session_start", "utc": "t", "model_sha256_16": "testsha",
             "model_decisions": 100, "predeclared": "x"}]
    pred = ex._predict(model, "sA")
    for i, actual in enumerate(["complete|q5-15"] * 6 + ["extend|q5-15"] * 4):
        r = ex._score(pred, model, actual)
        r.update({"kind": "scored", "window_id": "w", "fill_ts": i,
                  "pred_cycle": i, "score_cycle": i + 1,
                  "strict_temporal": True, "seq": i})
        rows.append(r)
    tape.write_text("\n".join(json.dumps(r) for r in rows))
    s = ex.summarize(tape)
    a = s["all_transitions"]
    assert a["n_scored"] == 10 and a["off_distribution"] == 0
    # actual mix matches sA's conditional -> conditional beats marginal on logp
    assert a["mean_logp_conditional"] > a["mean_logp_marginal"]
    assert a["exact_top1_pct_conditional"] == 60.0
    assert s["strict_temporal_subset"]["n_scored"] == 10
