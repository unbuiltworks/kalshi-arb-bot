"""HOT/WARM/COLD priority-queue regressions (issue #167 comment
5521210994): cap allocation, hysteresis, cold wakeup, starvation
guarantee, missingness discipline, L5-state allocation cap, and the
deploy-isolation tripwire."""
from __future__ import annotations

import time
from pathlib import Path

from src import pmus_priority_queue as pq
from src import pmus_ws_evidence_recorder as rec


def _targets(n, prefix="m"):
    return [{"slug": f"{prefix}-{i:03d}"} for i in range(n)]


def _mstate(slug_activity: dict, now: float) -> dict:
    """slug -> trades count; builds minimal recorder state."""
    st = {}
    for slug, n in slug_activity.items():
        st[slug] = {"trades": [{"t": now - 1, "px": "0.5", "dir": "buy"}] * n,
                    "book_updates": n * 10, "both_sides_served": True,
                    "bid": "0.50", "ask": "0.51",
                    "bid_qty": "50", "ask_qty": "50"}
    return st


def test_cap_allocation_hot_warm_cold_and_props_included():
    now = time.time()
    targets = _targets(100) + [{"slug": "prop-quiet-xyz"}]
    state = _mstate({f"m-{i:03d}": 40 - i // 3 for i in range(30)}, now)
    p = pq.plan(targets, state, 40, 0, {})
    d = p["diagnostics"]
    assert len(p["subscribe"]) == 40
    assert d["hot"] + d["warm"] + d["cold_sampled"] >= 40
    assert d["hot"] == 24 and d["cold_sampled"] == 4     # 0.6*40, 40//10
    # the prop is in the universe and reachable by cold sampling
    assert p["per_market"]["prop-quiet-xyz"]["tier"] in ("cold", "idle")


def test_no_starvation_every_market_observed_within_bound():
    now = time.time()
    targets = _targets(120)
    state = _mstate({f"m-{i:03d}": 50 for i in range(20)}, now)  # hot core
    prev: dict = {}
    seen: set = set()
    for cycle in range(40):
        p = pq.plan(targets, state, 40, cycle, prev, now=now)
        prev = p["state"]
        seen.update(p["subscribe"])
    assert seen == {t["slug"] for t in targets}          # no starvation
    assert p["diagnostics"]["coverage_age_max"] <= 40


def test_cold_market_with_sudden_activity_promotes_rapidly():
    now = time.time()
    targets = _targets(80)
    state = _mstate({f"m-{i:03d}": 50 for i in range(30)}, now)
    prev: dict = {}
    for cycle in range(3):
        p = pq.plan(targets, state, 40, cycle, prev, now=now)
        prev = p["state"]
    sleeper = "m-079"
    assert prev[sleeper]["tier"] in ("cold", "idle")
    state[sleeper] = _mstate({sleeper: 10}, now)[sleeper]  # burst arrives
    p = pq.plan(targets, state, 40, 3, prev, now=now)
    assert sleeper in p["subscribe"]                     # promoted this cycle
    assert sleeper in p["diagnostics"]["wake_promotions"]


def test_hysteresis_prevents_subscription_thrash():
    now = time.time()
    targets = _targets(60)
    base = {f"m-{i:03d}": 40 - i for i in range(40)}
    prev: dict = {}
    p = pq.plan(targets, _mstate(base, now), 40, 0, prev, now=now)
    prev = p["state"]
    hot0 = {s for s, r in p["per_market"].items() if r["tier"] == "hot"}
    # transient dip: the mid-ranked hot members briefly score worse
    dipped = dict(base)
    for s in list(hot0)[10:20]:
        dipped[s] = 1
    p2 = pq.plan(targets, _mstate(dipped, now), 40, 1, prev, now=now)
    hot1 = {s for s, r in p2["per_market"].items() if r["tier"] == "hot"}
    churn = len(hot0 - hot1)
    assert churn <= 4                    # min-hold keeps the core stable


def test_missing_components_do_not_zero_a_market():
    now = time.time()
    # market observed with book only — trades/depth flow fine, but no
    # slot counts, no l5 info, no settlement clock: score must be present
    s = {"trades": [], "book_updates": 100, "both_sides_served": True,
         "bid": "0.50", "ask": "0.51", "bid_qty": "10", "ask_qty": "10"}
    sc = pq.score_market(s, now)
    assert sc["score"] is not None and sc["score"] > 0
    assert sc["missing"] >= 2                           # exposed, not faked
    assert sc["components"]["tte_phase"] is None
    assert sc["components"]["completion_prob"] is None
    # never-observed market: score None -> ranked last, still coverable
    sc2 = pq.score_market(None, now)
    assert sc2["score"] is None and sc2["present"] == 0


def test_l5_state_allocation_cap_and_status_wiring():
    now = time.time()
    targets = _targets(60)
    state = _mstate({f"m-{i:03d}": 30 for i in range(60)}, now)
    p = pq.plan(targets, state, 40, 0, {})
    assert len(p["l5_allocated"]) == 10                  # default cap
    assert set(p["l5_allocated"]) <= set(p["subscribe"])
    # build_status computes l5_shadow only for allocated markets
    status = rec.build_status(dict(list(state.items())[:12]), True, 1, 12,
                              now, l5_alloc=set(p["l5_allocated"][:3]))
    skipped = [r for r in status["top_markets"]
               if r["l5_shadow"].get("skipped")]
    assert skipped                                       # some not allocated


def test_planner_is_pure_no_network_no_arming():
    src = Path(pq.__file__).read_text()
    for banned in ("urllib", "websockets", "requests", "Transport",
                   "/v1/orders", "armed", "POST"):
        assert banned not in src, banned


def test_deploy_isolation_tripwire_baked_into_both_images():
    root = Path(rec.__file__).resolve().parents[1]
    recorder = (root / "deploy/pmus_ws_recorder/Dockerfile").read_text()
    collector = (root / "deploy/pmus_sports_collector/Dockerfile").read_text()
    assert "PMUS_SERVICE_ROLE=ws-recorder" in recorder
    assert "PMUS_SERVICE_ROLE=collector" in collector
    assert "src.pmus_ws_evidence_recorder" in recorder
    assert "src.pmus_sports_collector" in collector
    assert "src.pmus_ws_evidence_recorder" not in collector
    # the recorder image must carry everything its status emission imports
    for needed in ("pmus_l5_action_shadow.py", "pmus_l5_persistence_v2.py",
                   "polymarket_l5/action_model.py",
                   "l5_action_model.json", "l5_action_model_v2.json"):
        assert needed in recorder, needed
    assert (root / "deploy/RAILWAY_ISOLATION.md").exists()
