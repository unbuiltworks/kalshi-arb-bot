from __future__ import annotations

import os

from src import pmus_sports_live_collector as live
from src import pmus_sports_collector as col


def _event(i: int, markets: int = 3) -> dict:
    return {
        "id": str(i),
        "slug": f"sport-{i}",
        "live": True,
        "ended": False,
        "eventState": {"period": "live", "score": "0-0"},
        "markets": [
            {
                "slug": f"m-{i}-{j}",
                "volume24hr": 1000 - j,
                "liquidity": 100 + j,
                "bestBidQuote": {"value": "0.49"},
                "bestAskQuote": {"value": "0.51"},
            }
            for j in range(markets)
        ],
    }


def test_cap_64_covers_all_50_live_events_before_extra_markets():
    universe = live.live_targets([_event(i) for i in range(50)])
    picked, diag = live.select_for_poll(universe, 64, cycle=0)
    assert len(picked) == 64
    assert diag["discovered_events"] == 50
    assert diag["discovered_markets"] == 150
    assert diag["selected_events"] == 50
    assert len({r["event_slug"] for r in picked}) == 50


def test_surplus_rotates_without_losing_event_floor():
    universe = live.live_targets([_event(i) for i in range(50)])
    first, d0 = live.select_for_poll(universe, 64, cycle=0)
    second, d1 = live.select_for_poll(universe, 64, cycle=1)
    assert len({r["event_slug"] for r in first}) == 50
    assert len({r["event_slug"] for r in second}) == 50
    # The first 50 rows are the deterministic one-per-event floor; only the
    # 14 surplus seats should rotate between discovery cycles.
    assert {r["slug"] for r in first[:50]} == {r["slug"] for r in second[:50]}
    assert {r["slug"] for r in first[50:]} != {r["slug"] for r in second[50:]}
    assert d0["rotation_cycle"] == 0
    assert d1["rotation_cycle"] == 1


def test_healthy_live_surface_never_admits_incentive_only_slug(monkeypatch):
    events = [_event(1, markets=2)]
    incentives = {
        "programs": [{
            "marketSlug": "stale-incentive-only",
            "timePeriods": [{"status": "active"}],
        }]
    }

    def fake_get(path):
        if path.startswith(col.LIVE_EVENTS_PATH):
            return {"events": events}
        raise AssertionError(path)

    monkeypatch.setattr(col, "http_get_json", fake_get)
    monkeypatch.delenv(col.PREFIXES_ENV, raising=False)
    picked, diag = live.discover_poll_targets(incentives, True, 64, cycle=0)
    assert {r["slug"] for r in picked} == {"m-1-0", "m-1-1"}
    assert "stale-incentive-only" not in {r["slug"] for r in picked}
    assert diag["source_mode"] == "v1_live_sports_authoritative"
    assert diag["incentive_only_admitted"] == 0


def test_explicit_prefix_filter_is_respected_but_default_is_full_slate(monkeypatch):
    ev = _event(2, markets=2)
    ev["markets"][0]["slug"] = "aec-keep"
    ev["markets"][1]["slug"] = "prop-keep-by-default"

    def fake_get(path):
        if path.startswith(col.LIVE_EVENTS_PATH):
            return {"events": [ev]}
        raise AssertionError(path)

    monkeypatch.setattr(col, "http_get_json", fake_get)
    monkeypatch.delenv(col.PREFIXES_ENV, raising=False)
    full, _ = live.discover_poll_targets({}, False, 64, cycle=0)
    assert {r["slug"] for r in full} == {"aec-keep", "prop-keep-by-default"}

    monkeypatch.setenv(col.PREFIXES_ENV, "aec-")
    filtered, _ = live.discover_poll_targets({}, False, 64, cycle=0)
    assert {r["slug"] for r in filtered} == {"aec-keep"}


def test_authoritative_runner_defaults_to_at_least_64_seats(monkeypatch):
    """Owner directive 5534173817 item 2: without any deployment env the
    authoritative runner must not fall back to the legacy 24-seat cap."""
    monkeypatch.delenv(col.MAX_MARKETS_ENV, raising=False)
    assert live.DEFAULT_LIVE_MAX_MARKETS >= 64
    assert int(os.getenv(col.MAX_MARKETS_ENV,
                         str(live.DEFAULT_LIVE_MAX_MARKETS))) >= 64


def test_production_image_runs_the_all_sports_path():
    """Directive item 9: production run() actually uses the all-sports
    path — the deployed CMD is this module, whose discovery is
    v1_live_sports_authoritative with visible fallback only."""
    from pathlib import Path
    root = Path(live.__file__).resolve().parents[1]
    docker = (root / "deploy/pmus_sports_collector/Dockerfile").read_text()
    assert 'CMD ["python3", "-m", "src.pmus_sports_live_collector"]' in docker
    src = Path(live.__file__).read_text()
    assert "v1_live_sports_authoritative" in src
    assert "legacy_visible_fallback" in src


# ---- bounded continuously-polled priority set (5541422910) ----

def _uni(n_events=50, per_event=3):
    """A universe far larger than the cap, so rotation is forced."""
    rows = []
    for e in range(n_events):
        for m in range(per_event):
            rows.append({"slug": f"aec-atp-e{e:02d}-m{m}",
                         "event_slug": f"ev-{e:02d}",
                         "market_meta": {"volume24hr": 1000 - e,
                                         "liquidity": 500}})
    return rows


def test_pinned_markets_are_selected_every_cycle_while_rest_rotate():
    """Acceptance 3: a pinned slug must never be rotated out, which is
    what keeps its inter-poll gap under the 60s readiness threshold."""
    universe = _uni()
    pins = [{"slug": "aec-atp-e40-m2", "reason": "operator_pin"}]
    seen_rotating = set()
    for cycle in range(12):
        sel, diag = live.select_for_poll(universe, 64, cycle, pins=pins)
        slugs = [r["slug"] for r in sel]
        assert "aec-atp-e40-m2" in slugs, f"dropped on cycle {cycle}"
        assert len(sel) == 64                       # cap unchanged
        assert diag["pinned_count"] == 1
        assert diag["pinned_markets"][0]["reason"] == "operator_pin"
        seen_rotating.update(slugs)
    # acceptance 2: breadth preserved — rotation still sweeps the slate
    assert len(seen_rotating) > 64


def test_pin_set_is_hard_capped_and_absences_are_reported(monkeypatch):
    universe = _uni()
    monkeypatch.setenv(live.PIN_MAX_ENV, "2")
    pins = [{"slug": f"aec-atp-e0{i}-m0"} for i in range(5)]
    sel, diag = live.select_for_poll(universe, 64, 0, pins=pins)
    assert diag["pinned_count"] == 2 == diag["pin_cap"]
    # a pin that is not in the live universe is reported, never invented
    sel2, diag2 = live.select_for_poll(
        universe, 64, 0, pins=[{"slug": "aec-atp-not-live"}])
    assert diag2["pinned_absent_from_universe"] == ["aec-atp-not-live"]
    assert all(r["slug"] != "aec-atp-not-live" for r in sel2)


def test_priority_set_merges_operator_pins_then_l5_candidates(monkeypatch):
    monkeypatch.setenv(live.PIN_SLUGS_ENV, "op-1, op-2")
    live.update_auto_pins({"l5_sports_decisions": {"rows": [
        {"slug": "cand-1"}, {"slug": "op-1"}, {"slug": "cand-2"}]}})
    pins = live.priority_pins()
    assert [p["slug"] for p in pins][:2] == ["op-1", "op-2"]
    assert {"slug": "cand-1", "reason": "l5_candidate_auto_pin"} in pins
    # no duplicate when an operator pin also ranks in the candidate path
    assert sum(1 for p in pins if p["slug"] == "op-1") == 1


def test_auto_pins_are_bounded_and_empty_report_is_safe(monkeypatch):
    monkeypatch.setenv(live.PIN_MAX_ENV, "3")
    live.update_auto_pins({"l5_sports_decisions": {
        "rows": [{"slug": f"c{i}"} for i in range(9)]}})
    assert len(live._auto_pins) == 3
    assert live.update_auto_pins(None) == []
    assert live.update_auto_pins({}) == []


def test_pins_do_not_change_any_gate_or_economics():
    """Acceptance 1: the pin mechanism is selection-only."""
    import inspect
    src = inspect.getsource(live)
    for banned in ("MIN_EDGE", "GAP_S", "q1_depth", "Q1_GATE_CLEARED",
                   "pmus_spring", "complement", "armed"):
        assert banned not in src, banned


def test_interleaved_order_makes_pin_cadence_a_fraction_of_the_sweep():
    """Being in every cycle is not enough: a 64-market sequential sweep
    runs ~60s, right at the gap threshold. Pins must recur WITHIN the
    sweep so their inter-poll gap is a fraction of it."""
    targets = [{"slug": f"m{i}"} for i in range(64)]
    order = live.interleaved_order(targets, ["m3", "m7"], every=15)
    seq = [t["slug"] for t in order]
    pos = [i for i, s in enumerate(seq) if s == "m3"]
    assert len(pos) >= 4, "pin must recur several times per sweep"
    assert max(b - a for a, b in zip(pos, pos[1:])) <= 20
    # every unpinned market is still polled exactly once per sweep
    assert sum(1 for s in seq if s == "m10") == 1
    assert len({s for s in seq}) == 64          # full breadth retained


def test_interleaving_is_a_noop_without_pins():
    targets = [{"slug": f"m{i}"} for i in range(10)]
    order = live.interleaved_order(targets, [], every=3)
    assert [t["slug"] for t in order] == [f"m{i}" for i in range(10)]


def test_interleave_every_zero_disables_recurrence(monkeypatch):
    monkeypatch.setenv(live.PIN_INTERLEAVE_ENV, "0")
    targets = [{"slug": f"m{i}"} for i in range(10)]
    order = live.interleaved_order(targets, ["m2"])
    assert sum(1 for t in order if t["slug"] == "m2") == 1
