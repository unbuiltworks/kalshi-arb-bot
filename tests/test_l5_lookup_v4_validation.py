"""Strict forward validation of the lookup-v4 candidate (OWNER 5577286614
item 4) on SYNTHETIC rows: no network (the collector is stubbed to fail),
the frozen artifacts are hashed before and after, nothing under
analysis/l5 is written except the two files the module owns (and the
tests redirect even those to tmp)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src import l5_lookup_v4_validation as val
from src import l5_shadow_loop as loop_mod
from src.polymarket_l5 import ops

START = 1_788_300_000
BASE = "p45-55c|tte2-5m|imb1-10"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    for name in ("OPS_DIR", "FAIL_DIR", "STATE", "HEARTBEAT"):
        monkeypatch.setattr(ops, name, getattr(ops, name))

    def _fail(*a, **k):
        raise AssertionError("network path reached in a test")
    monkeypatch.setattr(ops, "_fetch", _fail)
    monkeypatch.setattr(ops, "run_collect", _fail)
    monkeypatch.setattr(val, "deep_fetch", _fail)


def raw(ts: int, outcome: str, size: float, slug: str, price: float = 0.50) -> dict:
    r = {"type": "TRADE", "timestamp": ts, "slug": slug, "outcome": outcome, "side": "BUY",
         "price": price, "size": size, "usdcSize": round(price * size, 6),
         "transactionHash": f"0x{ts:x}{outcome[0]}{int(size)}", "proxyWallet": "0xtest"}
    r["_fp"] = ops._fingerprint(r)
    return r


def synthetic_fills(n_windows: int, start: int = START, pattern=("Up", "Up", "Down", "Down")) -> list:
    """5m windows every 5 minutes; each holds len(pattern) fills 10s apart
    from +10s: open Up -> extend Up -> complete Down -> complete Down."""
    out = []
    for w in range(n_windows):
        t0 = start + 300 * w
        slug = f"btc-updown-5m-{t0}"
        for i, o in enumerate(pattern):
            out.append(raw(t0 + 10 + 10 * i, o, 2, slug))
    return out


def tiny_model(extend_p: int = 95) -> dict:
    """A frozen-shaped lookup that recommends EXTEND from BASE after open/extend."""
    return {"marginal": {"extend|q<5": 60, "complete|q<5": 40},
            "base": {BASE: {"extend|q<5": 90, "complete|q<5": 10}},
            "full": {f"{BASE}|open|q<5": {"extend|q<5": extend_p, "complete|q<5": 100 - extend_p},
                     f"{BASE}|extend|q<5": {"extend|q<5": extend_p, "complete|q<5": 100 - extend_p},
                     f"{BASE}|complete|q<5": {"extend|q<5": 40, "complete|q<5": 60}},
            "cadence_modal": {f"{BASE}|open|q<5": "gap10-30"}, "_sha256_16": "synthetic0000000",
            "formulation": "synthetic", "n_transitions": 100}


def test_enriched_rows_and_window_level_split(tmp_path):
    fills = synthetic_fills(4)
    rows, rep = val.normalize(fills, tiny_model(), tmp_path / "work")
    assert rep["fills"] == 16 and rep["decisions"] == 12 and len(rows) == 12
    d = rows[0]
    # the loop's own keys + the enrichment fields
    assert d["base"] == BASE and d["full_key"] == f"{BASE}|open|q<5" and d["rel"] == "extend"
    assert d["outcome"] == "Up" and d["qty"] == 2 and d["size_bucket"] == "q<5"
    assert d["gap_s"] == 10 and d["gap_bucket"] == "gap10-30"      # bucket edges are exclusive
    assert d["resid_after"] == 4.0 and d["pair_cost_after"] is None
    assert d["window_end"] == START + 300 and d["t0"] == START
    assert rows[1]["rel"] == "complete" and rows[1]["pair_cost_after"] == pytest.approx(1.0)
    assert rows[1]["resid_after"] == 2.0 and rows[2]["resid_after"] == 0.0
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)
    # row-level split (the loop's rule) vs window-level split (strict)
    cut = START + 300 + 25                     # inside the second window
    row_split = val.split_rows(rows, cut, "row")
    assert len(row_split["train"]) == 4 and len(row_split["holdout"]) == 8
    win_split = val.split_rows(rows, cut, "window")
    assert len(win_split["train"]) == 3 and len(win_split["holdout"]) == 6
    assert win_split["dropped_straddling"] == 3          # the second window straddles the cut
    assert all(d["window_end"] <= cut for d in win_split["train"])
    assert all(d["t0"] >= cut for d in win_split["holdout"])
    assert val.strict_cut_ts(rows, 1 / 288) == pytest.approx(rows[-1]["ts"] - 300)


def test_scoring_dimensions_and_paired_statistics(tmp_path):
    """Frozen recommends EXTEND after every open/extend; L5 completes after
    the first extend. A candidate rebuilt on the training cohort must move
    toward L5 on the relation / COMPLETE dimensions, and the paired
    statistics must say so with a CI that excludes zero."""
    fills = synthetic_fills(120)
    frozen = tiny_model()
    rows, _ = val.normalize(fills, frozen, tmp_path / "work")
    split = val.split_rows(rows, val.strict_cut_ts(rows, 20 * 300 / val.DAY), "window")
    train, hold = split["train"], split["holdout"]
    assert len(hold) == 60 and len(train) >= 240
    cfg = loop_mod.load_config({})
    loop = loop_mod.ShadowLoop(cfg, tmp_path / "loop", dry_run=True, now_fn=lambda: float(rows[-1]["ts"] + 60),
                               model=frozen, out=lambda s: None)
    cand = loop.build_candidate(train)["candidate"]
    reps = val.size_representatives(train)
    assert reps["q<5"] == 2.0
    f = val.score_rows(frozen, hold, reps)
    c = val.score_rows(cand, hold, reps)
    # per window: extend (agree), complete after extend (frozen says extend), complete after complete (agree)
    assert f["relation"]["top1_agreement"] == pytest.approx(2 / 3, abs=1e-4)
    assert c["relation"]["top1_agreement"] == 1.0                 # the candidate learned the COMPLETE
    assert c["relation"]["mean_p_observed_action"] > f["relation"]["mean_p_observed_action"]
    assert c["relation"]["l1_observed_vs_expected"] < f["relation"]["l1_observed_vs_expected"]
    assert f["relation"]["observed_freq"] == {"open": 0.0, "extend": round(1 / 3, 4), "complete": round(2 / 3, 4), "sell": 0.0}
    assert f["side"]["open_n"] == 0 and f["side"]["post_fill_side_agreement"] == f["relation"]["top1_agreement"]
    assert f["size"]["top1_agreement"] == 1.0 and f["size"]["observed_freq"]["q<5"] == 1.0
    assert f["timing"]["observed_gap_freq"]["gap10-30"] == 1.0
    assert f["timing"]["cadence_modal_agreement"] == 1.0 == c["timing"]["cadence_modal_agreement"]
    assert f["inventory"]["observed_resid_after_freq"] == {"flat": round(1 / 3, 4), "imb1-10": round(2 / 3, 4), "imb10-50": 0.0, "imb>50": 0.0}
    assert c["inventory"]["l1_observed_vs_expected"] < f["inventory"]["l1_observed_vs_expected"]
    assert f["complete"]["post_fill_n"] == 60 and f["complete"]["p_complete_observed"] == pytest.approx(2 / 3, abs=1e-4)
    assert abs(c["complete"]["gap_pp"]) < abs(f["complete"]["gap_pp"])
    assert f["complete"]["pair_cost_at_complete"]["n"] == 40
    assert f["complete"]["pair_cost_at_complete"]["freq"]["pc>=1.00"] == 1.0
    assert f["complete"]["persistence"]["extend"]["n"] == 20 and f["complete"]["persistence"]["complete"]["n"] == 20
    assert f["price_aggressiveness"]["status"] == "UNMEASURED"
    assert set(f["by_state"]) == {BASE} and f["by_state"][BASE]["n"] == 60
    st = val.paired_stats(f, c, n_boot=200, seed=1)
    assert st["agreement_delta"] == pytest.approx(1 / 3, abs=1e-4)
    assert st["agreement_delta_ci"][0] > 0
    assert st["discordant"] == {"candidate_only_right": 20, "frozen_only_right": 0, "both_right": 40, "both_wrong": 0}
    assert st["mcnemar_exact_p_two_sided"] < 1e-5
    assert st["mean_p_action_delta"] > 0 and st["mean_p_action_delta_ci"][0] > 0
    # the frozen lookup is never mutated by scoring or by the candidate build
    assert frozen["full"][f"{BASE}|extend|q<5"] == {"extend|q<5": 95, "complete|q<5": 5}


def _fake_score(agreement_rows, p_rows, l1=0.05, off=0.0, gap=1.0):
    n = len(agreement_rows)
    return {"n": n, "relation": {"l1_observed_vs_expected": l1, "off_distribution_pct": off,
                                 "top1_agreement": sum(agreement_rows) / n,
                                 "mean_p_observed_action": sum(p_rows) / n},
            "complete": {"gap_pp": gap},
            "_per_row": {"agree": list(agreement_rows), "p_action": list(p_rows), "p_rel": list(p_rows)}}


def test_verdict_rules_promote_not_yet_reject():
    n = 1000
    frozen = _fake_score([1] * 700 + [0] * 300, [0.17] * n)
    ok = {"status": "PASS"}
    spec = {k: ok for k in ("action_distribution_relation", "post_fill_complete_gap", "off_distribution_rate")}
    # material: +5 pp agreement, CI excludes zero, no regression
    better = _fake_score([1] * 750 + [0] * 250, [0.19] * n, l1=0.04)
    st = val.paired_stats(frozen, better, n_boot=300, seed=3)
    v = val.verdict_for(frozen, better, st, n_train=1000, n_holdout_windows=100, spec_frozen=spec, spec_cand=spec)
    assert v["verdict"] == "PROMOTE_RECOMMENDED" and v["checks"]["agreement_gain_material"]
    # same gain but a regression on relation L1 -> NOT_YET
    worse_l1 = _fake_score([1] * 750 + [0] * 250, [0.19] * n, l1=0.20)
    v = val.verdict_for(frozen, worse_l1, st, n_train=1000, n_holdout_windows=100, spec_frozen=spec, spec_cand=spec)
    assert v["verdict"] == "NOT_YET" and not v["checks"]["relation_l1_ok"]
    # tiny gain inside the noise -> NOT_YET
    tiny = _fake_score([1] * 703 + [0] * 297, [0.171] * n)
    st2 = val.paired_stats(frozen, tiny, n_boot=300, seed=3)
    v = val.verdict_for(frozen, tiny, st2, n_train=1000, n_holdout_windows=100, spec_frozen=spec, spec_cand=spec)
    assert v["verdict"] == "NOT_YET"
    # measurably worse -> REJECT
    worse = _fake_score([1] * 640 + [0] * 360, [0.15] * n)
    st3 = val.paired_stats(frozen, worse, n_boot=300, seed=3)
    v = val.verdict_for(frozen, worse, st3, n_train=1000, n_holdout_windows=100, spec_frozen=spec, spec_cand=spec)
    assert v["verdict"] == "REJECT" and st3["agreement_delta_ci"][1] < 0
    # a new frozen-spec FAIL is a rejection even with a gain
    fail = {**spec, "post_fill_complete_gap": {"status": "FAIL"}}
    v = val.verdict_for(frozen, better, st, n_train=1000, n_holdout_windows=100, spec_frozen=spec, spec_cand=fail)
    assert v["verdict"] == "REJECT"
    # insufficient forward evidence
    v = val.verdict_for(frozen, better, st, n_train=1000, n_holdout_windows=5, spec_frozen=spec, spec_cand=spec)
    assert v["verdict"] == "NOT_YET" and v["reason"] == "insufficient forward evidence"


def test_output_guard_and_end_to_end_run_never_touch_frozen_files(tmp_path, monkeypatch):
    frozen_files = [p for p in loop_mod.FROZEN_PATHS if p.exists()]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen_files}
    with pytest.raises(loop_mod.FrozenPathViolation):
        val.guard_output(loop_mod.FROZEN_DIR / "l5_action_model_v2.json")
    with pytest.raises(loop_mod.FrozenPathViolation):
        val.guard_output(loop_mod.FROZEN_DIR / "some_other_artifact.json")
    with pytest.raises(loop_mod.FrozenPathViolation):
        val.guard_output(loop_mod.REPO_ROOT / "src" / "x.json")
    assert val.guard_output(val.OUT_JSON) == val.OUT_JSON.resolve()
    assert val.guard_output(tmp_path / "out.json") == (tmp_path / "out.json").resolve()
    # end to end on synthetic day files laid out as the collector would
    state = tmp_path / "state"
    fills = synthetic_fills(400)                         # ~33h of 5m windows
    loop_mod.write_fixture_day_files(fills, state / "ops")
    now = float(fills[-1]["timestamp"] + 60)
    rep = val.run(state, proposal_path=None, cache_dir=None, deep_fetch_days=None,
                  holdout_days=100 * 300 / val.DAY, n_boot=100, seed=2,
                  work_dir=tmp_path / "work", network=False, now_ts=now)
    assert rep["kind"] == "l5_lookup_v4_forward_validation"
    assert rep["data"]["network_fetch_worked"] is False and rep["data"]["merged_unique_fills"] == 1600
    assert rep["data"]["decision_rows"] == 1200 and rep["data"]["windows"] == 400
    assert rep["frozen_v3_identity"]["model_sha256_16"] == "bbb55bf90638d2bd"
    cuts = {c["cut"]: c for c in rep["cuts"]}
    assert set(cuts) == {"loop", "strict"}
    strict = cuts["strict"]
    assert strict["split_mode"] == "window" and strict["holdout_windows"] == 100
    assert strict["holdout_rows"] == 300 and strict["train_rows"] == 897   # one straddling window dropped
    assert strict["candidate"]["n_transitions"] == 69646 + 897
    assert strict["verdict"]["verdict"] in ("PROMOTE_RECOMMENDED", "NOT_YET", "REJECT")
    assert rep["verdict"]["rests_on"] == "strict cut" and rep["verdict"]["promotion"] == "REQUIRES_OWNER_DECISION"
    assert rep["verdict"]["verdict"] == strict["verdict"]["verdict"]
    assert cuts["loop"]["split_mode"] == "row" and cuts["loop"]["rule"].startswith("ShadowLoop._holdout_cut")
    md = val.render_md(rep)
    assert "## Cut `strict`" in md and "bbb55bf90638d2bd" in md and "materially improves" in md
    json.dumps(rep, default=str)
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen_files}
    assert after == before
    assert not list(loop_mod.FROZEN_DIR.glob("l5_lookup_v4_validation*.tmp"))
