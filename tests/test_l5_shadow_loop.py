"""The isolated 24/7 L5 shadow loop (issue #167 comment 5574408755 item 5).

Synthetic rows only; no network (the collector is stubbed to FAIL if
reached); the frozen model/lookup/spec are read, hashed before and after,
and must be byte-identical when the loop is done."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src import l5_shadow_loop as loop_mod
from src import pmus_l5_persistence_v2 as pv2
from src.polymarket_l5 import ops

START = 1_788_300_000                      # a 5m window start (epoch s)
SLUG = f"btc-updown-5m-{START}"


def raw(ts: int, outcome: str, size: float, price: float = 0.50, side: str = "BUY",
        slug: str = SLUG) -> dict:
    r = {"type": "TRADE", "timestamp": ts, "slug": slug, "outcome": outcome,
         "side": side, "price": price, "size": size, "usdcSize": round(price * size, 6),
         "transactionHash": f"0x{ts:x}{outcome[0]}{int(size)}", "proxyWallet": "0xtest"}
    r["_fp"] = ops._fingerprint(r)
    return r


def three_fill_window() -> list:
    # open Up(2) -> extend Up(2) -> complete Down(2); price 0.50 -> p45-55c,
    # tte 280/270 -> tte2-5m, residual 2 / 4 -> imb1-10
    return [raw(START + 10, "Up", 2), raw(START + 20, "Up", 2), raw(START + 30, "Down", 2)]


BASE = "p45-55c|tte2-5m|imb1-10"


def tiny_model() -> dict:
    """A synthetic lookup that always recommends EXTEND from BASE."""
    return {"marginal": {"extend|q<5": 60, "complete|q<5": 40},
            "base": {BASE: {"extend|q<5": 90, "complete|q<5": 10}},
            "full": {f"{BASE}|open|q<5": {"extend|q<5": 95, "complete|q<5": 5},
                     f"{BASE}|extend|q<5": {"extend|q<5": 95, "complete|q<5": 5}},
            "cadence_modal": {}, "_sha256_16": "synthetic0000000",
            "formulation": "synthetic", "n_transitions": 100}


def cfg(**over) -> dict:
    c = loop_mod.load_config({})
    c.update(over)
    return c


@pytest.fixture(autouse=True)
def _isolate_collector_paths(monkeypatch):
    """The loop rebinds the collector's module-level paths to its state dir;
    restore them, and make any real collection attempt fail loudly."""
    for name in ("OPS_DIR", "FAIL_DIR", "STATE", "HEARTBEAT"):
        monkeypatch.setattr(ops, name, getattr(ops, name))

    def _no_network(*a, **k):
        raise AssertionError("network path reached in a test")
    monkeypatch.setattr(ops, "_fetch", _no_network)
    monkeypatch.setattr(ops, "run_collect", _no_network)


def make_loop(tmp_path: Path, model=None, spec=None, **over) -> loop_mod.ShadowLoop:
    return loop_mod.ShadowLoop(cfg(**over), tmp_path / "state", dry_run=True,
                               now_fn=lambda: float(START + 400),
                               model=model if model is not None else tiny_model(),
                               spec=spec, out=lambda s: None)


def test_normalization_to_sequential_state(tmp_path):
    loop = make_loop(tmp_path)
    rep = loop.absorb(three_fill_window())
    assert rep == {"fills": 3, "decisions": 2, "skipped": 0, "evicted": 0}
    rows = list(loop.rows)
    assert [d["rel"] for d in rows] == ["extend", "complete"]
    assert [d["last_rel"] for d in rows] == ["open", "extend"]
    assert {d["base"] for d in rows} == {BASE}
    assert rows[0]["residual_units"] == 2.0 and rows[1]["residual_units"] == 4.0
    assert rows[0]["full_key"] == f"{BASE}|open|q<5"
    ctx = loop.windows[SLUG]
    assert (ctx.state.up_qty, ctx.state.dn_qty, ctx.state.seq) == (4.0, 2.0, 3)
    assert loop.last_activity_ts == START + 30
    # the same functions the live decision layer uses
    assert loop_mod.live_decision.state_key is loop_mod.state_key
    assert loop_mod.live_decision.pv2 is pv2


def test_agreement_tally_per_state_bucket(tmp_path):
    loop = make_loop(tmp_path)
    loop.absorb(three_fill_window())
    m = loop.metrics()
    assert m["rows"] == 2
    assert m["relation_agreement"] == 0.5          # extend agreed, complete diverged
    assert m["observed_relation_freq"]["extend"] == 0.5
    assert m["recommended_relation_freq"]["extend"] == 1.0
    per = m["_per_state"][BASE]
    assert (per["n"], per["agree"], per["diverge"], per["off"]) == (2, 1, 1, 0)
    assert per["observed"]["complete"] == 1 and per["recommended"]["extend"] == 2
    assert m["drift"]["verdict"] == "INSUFFICIENT_NEW_DATA"   # n < 30 per state
    assert m["frozen_spec_view"]["status"] == "OK"


def test_alarm_fires_below_agreement_floor_and_is_durable(tmp_path):
    lines = []
    loop = loop_mod.ShadowLoop(cfg(L5_SHADOW_MIN_DECISIONS=2, L5_SHADOW_MIN_AGREEMENT=0.9),
                               tmp_path / "state", dry_run=True,
                               now_fn=lambda: float(START + 400), model=tiny_model(),
                               out=lines.append)
    loop.absorb(three_fill_window())
    m = loop.metrics()
    alarms = loop.alarms_for(m, {"unchanged_since_boot": True}, {"rss_mb": 10.0})
    kinds = {a["kind"] for a in alarms}
    assert "AGREEMENT_BELOW_FLOOR" in kinds
    # no alarm below the evidence floor
    quiet = loop_mod.ShadowLoop(cfg(L5_SHADOW_MIN_DECISIONS=200, L5_SHADOW_MIN_AGREEMENT=0.9),
                                tmp_path / "state2", dry_run=True,
                                now_fn=lambda: float(START + 400), model=tiny_model(),
                                out=lambda s: None)
    quiet.absorb(three_fill_window())
    assert not [a for a in quiet.alarms_for(quiet.metrics(), {"unchanged_since_boot": True},
                                            {"rss_mb": 10.0})
                if a["kind"] == "AGREEMENT_BELOW_FLOOR"]
    loop.emit_alarms(alarms)
    stdout = [l for l in lines if l.startswith("L5_SHADOW_ALARM ")]
    assert stdout and json.loads(stdout[0][len("L5_SHADOW_ALARM "):])["kind"] == "AGREEMENT_BELOW_FLOOR"
    files = list((tmp_path / "state" / "alarms").glob("alarm_*_AGREEMENT_BELOW_FLOOR.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["rising_edge"] is True
    # a frozen-identity mismatch is itself an alarm
    bad = loop.alarms_for(m, {"unchanged_since_boot": False, "model_sha256_16": "x"}, {"rss_mb": 1})
    assert "FROZEN_IDENTITY_MISMATCH" in {a["kind"] for a in bad}


def test_memory_cap_evicts_oldest_and_writes_note(tmp_path):
    loop = make_loop(tmp_path, L5_SHADOW_MAX_ROWS=3)
    rows = [raw(START + 10 + 5 * i, "Up" if i % 2 == 0 else "Down", 2) for i in range(6)]
    rep = loop.absorb(rows)
    assert rep["decisions"] == 5 and rep["evicted"] == 2
    assert len(loop.rows) == 3 and loop.evicted_total == 2
    assert [d["ts"] for d in loop.rows] == [START + 25, START + 30, START + 35]
    notes = [json.loads(l) for l in (tmp_path / "state" / "notes.jsonl").read_text().splitlines()]
    assert notes[-1]["kind"] == "memory_cap_eviction" and notes[-1]["evicted_rows"] == 2
    assert len(loop.fps) == len(loop.rows) + 1      # first fill has no decision row


def test_proposal_never_touches_frozen_paths(tmp_path):
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in loop_mod.FROZEN_PATHS
              if p.exists()}
    assert pv2.ARTIFACT.resolve() in before and loop_mod.gate.SPEC_PATH.resolve() in before
    loop = make_loop(tmp_path, model=pv2.load_v2())      # the real frozen lookup, read-only
    loop.absorb(three_fill_window())
    path = loop.build_proposal(loop.metrics(), reason="test")
    prop = json.loads(path.read_text())
    assert path.parent == (tmp_path / "state" / "proposals").resolve()
    assert path.name.startswith(f"lookup_v{loop_mod.CANONICAL_LOOKUP_VERSION + 1}_proposal_")
    assert prop["promotion"] == "REQUIRES_OWNER_DECISION"
    assert prop["validation"]["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert prop["validation"]["candidate_meets_bar"] is False
    assert prop["validation"]["spec_frozen_utc"] == loop.spec["frozen_utc"]
    assert prop["frozen_identity"]["model_sha256_16"] == "bbb55bf90638d2bd"
    assert prop["candidate_artifact"]["n_transitions"] > loop.model["n_transitions"]
    cand = Path(prop["candidate_artifact"]["path"])
    assert cand.parent == path.parent and cand.exists()
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}
    assert after == before
    # the guard refuses every frozen path and anything under analysis/l5
    with pytest.raises(loop_mod.FrozenPathViolation):
        loop._guard_write(pv2.ARTIFACT)
    with pytest.raises(loop_mod.FrozenPathViolation):
        loop._guard_write(loop_mod.FROZEN_DIR / "l5_action_model_v2.v4.json")
    with pytest.raises(loop_mod.FrozenPathViolation):
        loop._guard_write(tmp_path / "outside.json")
    with pytest.raises(loop_mod.FrozenPathViolation):
        loop_mod.ShadowLoop(cfg(), loop_mod.FROZEN_DIR / "shadow", dry_run=True,
                            model=tiny_model(), out=lambda s: None)


def test_proposal_prints_owner_summary_line(tmp_path):
    """OWNER 5577286614 item 4: one L5_SHADOW_PROPOSAL_SUMMARY {json} stdout
    line per written proposal -- version, cohort sizes + cut, frozen vs
    candidate holdout numbers, top-10 lookup-key deltas, and the promotion
    label REQUIRES_OWNER_DECISION; built without any full-history object."""
    lines = []
    loop = loop_mod.ShadowLoop(cfg(), tmp_path / "state", dry_run=True,
                               now_fn=lambda: float(START + 400), model=pv2.load_v2(),
                               out=lines.append)
    # two windows so the 0.7 quantile cut leaves rows on both sides
    rows = three_fill_window() + [raw(START + 900 + i * 10, o, 2, slug=f"btc-updown-5m-{START + 600}")
                                  for i, o in enumerate(("Up", "Up", "Down", "Down"))]
    loop.absorb(rows)
    path = loop.build_proposal(loop.metrics(), reason="test")
    summ = [l for l in lines if l.startswith("L5_SHADOW_PROPOSAL_SUMMARY ")]
    assert len(summ) == 1
    s = json.loads(summ[0][len("L5_SHADOW_PROPOSAL_SUMMARY "):])
    prop = json.loads(path.read_text())
    assert s["promotion"] == "REQUIRES_OWNER_DECISION"
    assert s["proposed_lookup_version"] == loop_mod.CANONICAL_LOOKUP_VERSION + 1
    assert s["verdict"] == prop["validation"]["verdict"]
    assert s["train"] == prop["evidence"]["train_transitions"]
    assert s["holdout"] == prop["evidence"]["holdout_transitions"]
    assert s["train"] + s["holdout"] == len(loop.rows) and s["holdout"] >= 1
    assert s["holdout_cut_utc"] == prop["evidence"]["holdout_cut_utc"]
    assert s["train_span_utc"][1] < s["holdout_span_utc"][0] or s["train_span_utc"][1] <= s["holdout_cut_utc"]
    assert s["candidate_sha"] == prop["candidate_artifact"]["sha256_16"]
    assert s["frozen_model_sha"] == "bbb55bf90638d2bd" and s["lookup_version"] == 3
    for view in (s["frozen_on_holdout"], s["candidate_on_holdout"]):
        assert set(view) >= {"relation_agreement", "mean_prob_of_observed_action",
                             "relation_l1_observed_vs_expected", "off_distribution_pct"}
    assert s["frozen_on_holdout"]["relation_agreement"] == prop["validation"]["frozen_on_holdout"]["relation_agreement"]
    top = s["top_lookup_key_deltas"]
    assert 1 <= len(top) <= 10
    for t in top:
        assert set(t) >= {"key", "n", "frozen_dist", "candidate_dist"}
        assert set(t["n"]) == {"frozen", "candidate", "added"}
        assert t["n"]["candidate"] == t["n"]["frozen"] + t["n"]["added"]
        assert abs(sum(t["candidate_dist"].values()) - 1.0) < 1e-6
        assert len(t["candidate_dist"]) <= 16
    assert s["path"] == str(path)


def test_dry_run_completes_without_network(tmp_path, capsys):
    rc = loop_mod.main(["--dry-run", "--state-dir", str(tmp_path / "dry"),
                        "--fixture-rows", "120"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "L5_SHADOW_STATUS " in out and "L5_SHADOW_PROPOSAL " in out
    hb = json.loads((tmp_path / "dry" / "heartbeat.json").read_text())
    assert hb["dry_run"] is True and hb["read_only"] is True
    assert hb["rows_in_memory"] > 0 and hb["rss_mb"] > 0 and hb["cycle_latency_ms"] >= 0
    assert hb["frozen"]["unchanged_since_boot"] is True
    assert hb["frozen"]["lookup_version"] == 3
    assert hb["collect"] == {"skipped": "dry_run"}
    assert list((tmp_path / "dry" / "proposals").glob("lookup_v4_proposal_*.json"))
    assert (tmp_path / "dry" / "ops" / "state.json").exists() is False   # collector never ran
