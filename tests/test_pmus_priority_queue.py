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


def _seeded_target(slug, bid=None, ask=None, vol=None, live=None):
    meta = {}
    if bid is not None:
        meta["bestBidQuote"] = {"value": bid}
    if ask is not None:
        meta["bestAskQuote"] = {"value": ask}
    if vol is not None:
        meta["volume24hr"] = vol
    t = {"slug": slug, "market_meta": meta}
    if live is not None:
        t["event_state"] = {"live": live}
    return t


def test_pre_ws_seed_ranks_never_observed_markets(monkeypatch):
    """Issue #167 comment 5521333079: two never-WS-observed markets with
    different discovery metadata — the stronger cheap-screen candidate
    gets the earlier WARM/HOT seat; all-missing targets still age fairly
    through cold sampling."""
    now = time.time()
    strong = _seeded_target("seed-strong", "0.50", "0.505", 800, live=True)
    weak = _seeded_target("seed-weak", "0.30", "0.40", 5, live=False)
    blanks = [{"slug": f"blank-{i:02d}"} for i in range(30)]
    targets = [strong, weak] + blanks
    sc_strong = pq.score_market(None, now, target=strong)
    sc_weak = pq.score_market(None, now, target=weak)
    assert sc_strong["score"] > sc_weak["score"] > 0
    assert sc_strong["components"]["seed_spread_quality"] > 0.8
    p = pq.plan(targets, {}, 8, 0, {}, now=now)
    assert "seed-strong" in p["subscribe"]
    r_strong = p["per_market"]["seed-strong"]["rank"]
    r_weak = p["per_market"]["seed-weak"]["rank"]
    assert r_strong < r_weak
    # all-missing markets score None but are still coverable by aging
    prev = p["state"]
    seen: set = set()
    for cycle in range(1, 30):
        p = pq.plan(targets, {}, 8, cycle, prev, now=now)
        prev = p["state"]
        seen.update(p["subscribe"])
    assert {b["slug"] for b in blanks} <= seen        # fairness preserved


def test_authenticated_evidence_dominates_seed_once_observed():
    now = time.time()
    great_seed = _seeded_target("m", "0.50", "0.505", 900, live=True)
    observed_poor = {"trades": [], "book_updates": 5,
                     "both_sides_served": False, "bid": "0.10",
                     "ask": "0.40", "bid_qty": "1", "ask_qty": "1"}
    sc = pq.score_market(observed_poor, now, target=great_seed)
    assert sc["components"]["seed_spread_quality"] is None   # seed dropped
    assert sc["components"]["seed_activity"] is None
    assert sc["components"]["book_rate"] is not None         # auth used
    assert sc["score"] < 0.5


def test_public_metadata_change_wakes_cold_market():
    now = time.time()
    sleeper = _seeded_target("pub-sleeper", "0.50", "0.52", 10)
    busy = [{"slug": f"busy-{i:02d}"} for i in range(20)]
    state = _mstate({f"busy-{i:02d}": 40 for i in range(20)}, now)
    targets = busy + [sleeper]
    prev: dict = {}
    for cycle in range(3):
        p = pq.plan(targets, state, 12, cycle, prev, now=now)
        prev = p["state"]
    assert prev["pub-sleeper"]["tier"] in ("cold", "idle")
    # public BBO moves + volume jumps on the still-unobserved sleeper
    woke = _seeded_target("pub-sleeper", "0.55", "0.555", 400)
    p = pq.plan(busy + [woke], state, 12, 3, prev, now=now)
    assert "pub-sleeper" in p["diagnostics"]["wake_promotions"]
    assert "pub-sleeper" in p["subscribe"]


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
    # owner directive 5534173817: the collector image's CMD is the
    # authoritative all-live-sports runner, which delegates to the base
    # collector module (still COPY'd into the image)
    assert "src.pmus_sports_live_collector" in collector
    assert "src/pmus_sports_collector.py" in collector
    assert "src.pmus_ws_evidence_recorder" not in collector
    # the recorder image must carry everything its status emission imports
    for needed in ("pmus_l5_action_shadow.py", "pmus_l5_persistence_v2.py",
                   "polymarket_l5/action_model.py",
                   "l5_action_model.json", "l5_action_model_v2.json"):
        assert needed in recorder, needed
    assert (root / "deploy/RAILWAY_ISOLATION.md").exists()


# --- fast intra-cycle L5 allocation (issue #167 comment 5524827113) ---

def test_fast_intra_cycle_l5_realloc_swaps_without_resubscribe():
    """Market A holds an L5 seat from discovery with zero trades; market B
    is subscribed but unallocated. Once B earns authenticated activity,
    the fast per-status-tick allocation swaps B in — while the subscribe
    set stays byte-for-byte unchanged and no planner state is touched."""
    now = time.time()
    subscribed = [f"m-{i:02d}" for i in range(9)] + \
        ["a-zero", "z-later", "z-tail"]
    frozen = list(subscribed)
    state = _mstate({f"m-{i:02d}": 20 for i in range(9)}, now)
    alloc0 = pq.allocate_l5(subscribed, state, now=now)
    assert len(alloc0) == 10                       # default bound
    assert "a-zero" in alloc0                      # rode in on aging rank
    assert "z-later" not in alloc0
    # B gets authenticated activity between discovery cycles
    state["z-later"] = _mstate({"z-later": 30}, now)["z-later"]
    alloc1 = pq.allocate_l5(subscribed, state, now=now)
    assert "z-later" in alloc1                     # swapped in this tick
    assert "a-zero" not in alloc1
    assert set(alloc1) <= set(subscribed)
    assert subscribed == frozen                    # no subscription change
    # only already-subscribed slugs are ever rankable
    assert "outsider" not in pq.allocate_l5(
        subscribed, {**state, "outsider": state["m-00"]}, now=now)


def test_fast_realloc_diagnostics_wired_into_recorder_status_path():
    src = Path(rec.__file__).read_text()
    assert "allocate_l5" in src
    assert "l5_fast_realloc_count" in src
    assert "l5_fast_realloc_changed" in src


def test_l5_allocation_zero_means_no_allocation(monkeypatch):
    """OWNER 5554106892: no hardcoded reserved allocation; 0 is valid."""
    monkeypatch.setenv(pq.L5_ALLOC_ENV, "0")
    now = time.time()
    targets = _targets(60)
    state = _mstate({f"m-{i:03d}": 30 for i in range(60)}, now)
    p = pq.plan(targets, state, 40, 0, {})
    assert p["l5_allocated"] == []
    assert len(p["subscribe"]) == 40                     # coverage unchanged
    monkeypatch.setenv(pq.L5_ALLOC_ENV, "50")
    assert len(pq.plan(targets, state, 40, 0, {})["l5_allocated"]) == 20


def test_stale_observed_markets_are_evicted_from_hot_and_warm_seats():
    """issue #167 comment 5571181823: yesterday's resolved games kept
    their HOT seats through a lifetime book count; a market whose
    authenticated stream has been silent longer than PMUS_WS_STALE_S
    cannot hold or win HOT/WARM, and its book_rate is a recent-window
    rate, so today's live markets get the seats."""
    now = time.time()
    targets = _targets(60)
    state = _mstate({f"m-{i:03d}": 40 - i // 2 for i in range(30)}, now)
    # the ten highest-activity markets went silent 5 hours ago (frozen books)
    for i in range(10):
        s = state[f"m-{i:03d}"]
        s["trades"] = [dict(t, t=now - 18000) for t in s["trades"]]
        s["bbo_history"] = [{"t": now - 18000 - k} for k in range(300)]
    # the rest are live: fresh history
    for i in range(10, 30):
        state[f"m-{i:03d}"]["bbo_history"] = [{"t": now - k} for k in range(40)]
    prev = {f"m-{i:03d}": {"tier": "hot", "since": 0} for i in range(10)}   # they sat HOT
    p = pq.plan(targets, state, 40, 5, prev)
    stale = {f"m-{i:03d}" for i in range(10)}
    for slug in stale:
        assert p["per_market"][slug]["stale"] is True
        assert p["per_market"][slug]["tier"] in ("cold", "idle")
    assert p["diagnostics"]["stale_evicted"] == 10
    # every live observed market is seated, and HOT is full
    live = {f"m-{i:03d}" for i in range(10, 30)}
    assert live <= set(p["subscribe"])
    assert p["diagnostics"]["hot"] == 24
    # a frozen book scores 0 on book_rate even with a large lifetime count
    frozen = pq.score_market(state["m-000"], now)
    assert frozen["components"]["book_rate"] == 0.0
    assert pq.is_stale(state["m-000"], now) and not pq.is_stale(state["m-020"], now)
    assert pq.is_stale(None, now) is False                    # never observed is not stale


def test_stale_markets_recover_through_cold_sampling_when_they_wake():
    now = time.time()
    targets = _targets(20)
    state = _mstate({f"m-{i:03d}": 5 for i in range(20)}, now)
    for i in range(20):
        s = state[f"m-{i:03d}"]
        s["bbo_history"] = [{"t": now - 18000}]                          # all silent
        s["trades"] = [dict(x, t=now - 18000) for x in s["trades"]]
    p = pq.plan(targets, state, 8, 0, {})
    assert p["diagnostics"]["stale_evicted"] == 20
    assert p["subscribe"] == []                       # nothing fresh, nothing live: no seat is spent
    # the venue lists m-007's event as live again -> COLD re-samples it
    targets[7]["event_state"] = {"live": True}
    p1 = pq.plan(targets, state, 8, 1, p["state"])
    assert p1["per_market"]["m-007"]["tier"] == "cold"
    # fresh authenticated updates arrive -> seated HOT next cycle
    state["m-007"]["bbo_history"] = [{"t": now - k} for k in range(30)]
    p2 = pq.plan(targets, state, 8, 2, p1["state"])
    assert p2["per_market"]["m-007"]["stale"] is False
    assert p2["per_market"]["m-007"]["tier"] == "hot"


def test_stale_resolved_markets_leave_every_tier_but_a_discovered_live_one_keeps_cold(monkeypatch):
    """OWNER GO 5571884826: evict resolved/closed markets from HOT/WARM/COLD;
    a market the venue still lists as LIVE whose stream went quiet is a feed
    question and keeps COLD eligibility so it is re-sampled."""
    now = time.time()
    targets = _targets(30)
    targets[5]["event_state"] = {"live": True}                 # m-005: venue says live
    state = _mstate({f"m-{i:03d}": 5 for i in range(30)}, now)
    for i in range(30):
        s = state[f"m-{i:03d}"]
        s["bbo_history"] = [{"t": now - 18000}]
        s["trades"] = [dict(x, t=now - 18000) for x in s["trades"]]
    p = pq.plan(targets, state, 8, 0, {})
    d = p["diagnostics"]
    assert d["stale_evicted"] == 29 and d["stale_but_discovered_live"] == 1
    tiers = {slug: p["per_market"][slug]["tier"] for slug in p["per_market"]}
    assert tiers["m-005"] == "cold"
    assert all(t == "idle" for slug, t in tiers.items() if slug != "m-005")
    assert p["subscribe"] == ["m-005"] and d["fresh_live_subscribed"] == 0


def test_fresh_live_market_displaces_a_stale_resolved_hot_member():
    now = time.time()
    targets = _targets(4)
    state = _mstate({"m-000": 30, "m-001": 2}, now)
    state["m-000"]["bbo_history"] = [{"t": now - 20000}]          # resolved yesterday, huge history
    state["m-000"]["trades"] = [dict(x, t=now - 20000) for x in state["m-000"]["trades"]]
    state["m-000"]["book_updates"] = 9000
    state["m-001"]["bbo_history"] = [{"t": now - k} for k in range(20)]
    prev = {"m-000": {"tier": "hot", "since": 0}}
    p = pq.plan(targets, state, 2, 4, prev)
    assert p["per_market"]["m-001"]["tier"] == "hot"
    assert p["per_market"]["m-000"]["tier"] == "idle" and p["per_market"]["m-000"]["stale"] is True
    # stale-score decay: the frozen book's recent-window book_rate is 0 despite 9000 lifetime updates
    assert pq.score_market(state["m-000"], now)["components"]["book_rate"] == 0.0
    assert pq.score_market(state["m-001"], now)["components"]["book_rate"] > 0.0
