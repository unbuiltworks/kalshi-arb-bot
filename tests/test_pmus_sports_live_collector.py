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


def test_default_interleave_keeps_pins_well_inside_the_60s_gap_rule():
    """The default must clear the gate it exists for, with margin.

    Measured live on 2026-09-04 against the PMUS slate: every=15 left
    pinned markets at a 56.5s median / 127s worst inter-poll gap, still
    tripping analyze_market's 60s rule, while every=6 held them to a
    10.5s median / 11.9s worst. Expressed here as poll-slot spacing so
    the bound is checked without a live venue: at the ~2.3s/market
    observed sweep rate, a <=10-slot spacing is ~23s, comfortably under
    60s even if per-market latency doubles.
    """
    assert live.PIN_INTERLEAVE_DEFAULT <= 8
    targets = [{"slug": f"m{i}"} for i in range(64)]
    order = live.interleaved_order(targets, ["m3", "m7"],
                                   every=live.PIN_INTERLEAVE_DEFAULT)
    seq = [t["slug"] for t in order]
    for pin in ("m3", "m7"):
        pos = [i for i, s in enumerate(seq) if s == pin]
        assert max(b - a for a, b in zip(pos, pos[1:])) <= 10, pin
    # breadth is not sacrificed for cadence
    assert len(set(seq)) == 64
    assert sum(1 for s in seq if s == "m10") == 1


# ---- live incentive-program markets are pinned as they go live ----

def test_live_program_market_is_pinned_the_moment_it_appears(monkeypatch):
    """Every active PMUS liquidityProgram observed sits on cfb moneyline and
    only earns while its game is in play. A program market that lands in the
    rotating surplus trips the 60s gap rule for its first hour -- exactly the
    window the pool pays over."""
    monkeypatch.delenv(live.PIN_SLUGS_ENV, raising=False)
    live._auto_pins = []
    pins = live.priority_pins(["aec-cfb-ala-uk-2026-09-12"])
    assert [p["slug"] for p in pins] == ["aec-cfb-ala-uk-2026-09-12"]
    assert pins[0]["reason"] == "live_incentive_program"


def test_operator_pins_still_outrank_program_pins(monkeypatch):
    monkeypatch.setenv(live.PIN_SLUGS_ENV, "op-1")
    live._auto_pins = ["auto-1"]
    pins = live.priority_pins(["prog-1"])
    assert [p["reason"] for p in pins] == [
        "operator_pin", "live_incentive_program", "l5_candidate_auto_pin"]


def test_program_autopin_respects_pin_cap_and_can_be_disabled(monkeypatch):
    monkeypatch.delenv(live.PIN_SLUGS_ENV, raising=False)
    monkeypatch.setenv(live.PIN_MAX_ENV, "2")
    live._auto_pins = []
    assert len(live.priority_pins([f"p{i}" for i in range(9)])) == 2
    monkeypatch.setenv(live.INCENTIVE_AUTOPIN_ENV, "0")
    assert live.priority_pins([f"p{i}" for i in range(9)]) == []


def test_closed_program_markets_are_not_added_to_the_poll_set(monkeypatch):
    """Pre-game program markets are closed books; pinning is about cadence
    once live, never about polling a book that cannot trade."""
    monkeypatch.delenv(live.PIN_SLUGS_ENV, raising=False)
    live._auto_pins = []
    incentives = {"programs": [
        {"marketSlug": "aec-cfb-live-now-2026-09-05",
         "timePeriods": [{"status": "active"}]},
        {"marketSlug": "aec-cfb-not-started-2026-09-12",
         "timePeriods": [{"status": "active"}]},
    ]}
    events = [{"slug": "ev1", "live": True, "markets": [
        {"slug": "aec-cfb-live-now-2026-09-05"}]}]
    monkeypatch.setattr(col, "fetch_live_events",
                        lambda _get: (events, {"v1_live_error": None}))
    selected, diag = live.discover_poll_targets(incentives, True, cap=8)
    slugs = [t["slug"] for t in selected]
    assert "aec-cfb-live-now-2026-09-05" in slugs
    assert "aec-cfb-not-started-2026-09-12" not in slugs   # closed, not polled
    assert diag["incentive_programs_live"] == ["aec-cfb-live-now-2026-09-05"]
    assert diag["incentive_programs_known"] == 2
    assert diag["pinned_markets"][0]["slug"] == "aec-cfb-live-now-2026-09-05"


def test_run_interleaves_the_same_pin_set_selection_used():
    """Regression: run() derived its interleave pins from priority_pins()
    with NO program slugs, so a live program market was seated every cycle
    yet polled only once per sweep -- ~60s cadence, tripping the exact gap
    rule the pin exists to clear. The interleave must see the live program
    slugs that discovery reported."""
    import inspect
    src = inspect.getsource(live.run)
    assert "incentive_programs_live" in src, (
        "run() must feed live program slugs into its pin set")
    assert "priority_pins()" not in src, (
        "run() must not re-derive pins without the program slugs")


def test_program_pin_actually_recurs_in_the_poll_order(monkeypatch):
    """End-to-end on the ordering primitive: a program-pinned slug in a
    64-market sweep must recur many times, not once."""
    monkeypatch.delenv(live.PIN_SLUGS_ENV, raising=False)
    live._auto_pins = []
    targets = [{"slug": f"m{i}"} for i in range(64)]
    targets.append({"slug": "aec-cfb-live-2026-09-05"})
    pins = [p["slug"] for p in
            live.priority_pins(["aec-cfb-live-2026-09-05"])]
    order = [t["slug"] for t in live.interleaved_order(targets, pins)]
    pos = [i for i, s in enumerate(order) if s == "aec-cfb-live-2026-09-05"]
    assert len(pos) >= 10, f"program pin polled only {len(pos)}x per sweep"
    assert max(b - a for a, b in zip(pos, pos[1:])) <= 10


# ---- priority comes from MEASURED conditions, never market type ----

def _mkt(slug, prop, bid, ask, vol=None, liq=None):
    return {"slug": slug, "event_slug": "ev", "prop": prop,
            "market_meta": {"bestBidQuote": {"value": str(bid)},
                            "bestAskQuote": {"value": str(ask)},
                            "volume24hr": vol, "liquidity": liq}}


def _obs_rows(slug, n, bid, ask, cum_step, t0=1_788_600_000.0, dt=10.0):
    from datetime import datetime, timezone
    out = []
    for i in range(n):
        out.append({"kind": "bbo_book",
                    "t": datetime.fromtimestamp(t0 + i * dt,
                                                tz=timezone.utc).isoformat(),
                    "slug": slug, "bid": str(bid), "ask": str(ask),
                    "bid_touch_qty": "500", "ask_touch_qty": "500",
                    "shares_traded_cum": str(100 + i * cum_step)})
    return out


def test_market_type_is_not_an_input_to_priority():
    """A prop and a primary with IDENTICAL observable conditions must
    score identically. The earlier +40 primary bonus pre-decided that a
    moneyline beats a player prop, which the evidence never showed."""
    live._observed.clear()
    prop = _mkt("astatc-x-hits-gte2", True, 0.45, 0.47)
    prim = _mkt("aec-x", False, 0.45, 0.47)
    assert live._market_score(prop) == live._market_score(prim)


def test_a_prop_with_better_observed_conditions_outranks_a_primary():
    """Type-blind means a prop CAN win, and must, when it is the better
    L5 candidate on measured conditions."""
    live._observed.clear()
    now = 1_788_600_000.0 + 600
    live.update_observed(_obs_rows("astatc-live-prop", 30, 0.48, 0.50, 40),
                         now=1_788_600_000.0)
    for i, r in enumerate(_obs_rows("astatc-live-prop", 30, 0.48, 0.50, 40)):
        pass
    # rebuild with real timestamps so span/prints are meaningful
    live._observed.clear()
    for i in range(30):
        live.update_observed(
            [{"kind": "bbo_book", "slug": "astatc-live-prop",
              "bid": "0.48", "ask": "0.50", "bid_touch_qty": "500",
              "ask_touch_qty": "500", "shares_traded_cum": str(100 + i * 40)}],
            now=1_788_600_000.0 + i * 10)
        live.update_observed(
            [{"kind": "bbo_book", "slug": "aec-quiet-primary",
              "bid": "0.20", "ask": "0.24", "bid_touch_qty": "5",
              "ask_touch_qty": "5", "shares_traded_cum": "100"}],
            now=1_788_600_000.0 + i * 10)
    prop = _mkt("astatc-live-prop", True, 0.48, 0.50)
    prim = _mkt("aec-quiet-primary", False, 0.20, 0.24)
    assert live._market_score(prop, now) > live._market_score(prim, now)
    pins = live.top_candidate_pins([prop, prim], now)
    assert pins == ["astatc-live-prop"], pins


def test_null_metadata_means_unknown_not_dead():
    """A market the venue serves no volume for is UNMEASURED, not dead:
    it gets an exploration allowance so it can be observed and prove
    itself, rather than being ranked as if volume were zero."""
    live._observed.clear()
    unknown = _mkt("aec-unknown", False, 0.45, 0.47, vol=None, liq=None)
    known_zero = _mkt("aec-zero", False, 0.45, 0.47, vol=0, liq=0)
    assert live._market_score(unknown) > live._market_score(known_zero)
    assert live.UNKNOWN_EXPLORATION_BONUS > 0


def test_observed_tape_supersedes_served_metadata():
    live._observed.clear()
    now = 1_788_600_000.0 + 300
    for i in range(30):
        live.update_observed(
            [{"kind": "bbo_book", "slug": "aec-m", "bid": "0.45",
              "ask": "0.47", "bid_touch_qty": "900", "ask_touch_qty": "900",
              "shares_traded_cum": str(100 + i * 50)}],
            now=1_788_600_000.0 + i * 10)
    st = live.observed_stats("aec-m", now)
    assert st["prints_per_min"] > 0
    assert st["two_sided_rate"] == 1.0
    assert st["band_residency"] == 1.0
    assert st["median_spread"] is not None
    assert live.observed_stats("never-seen", now) is None


def test_out_of_band_touch_is_penalised_but_never_excluded():
    """A touch that cannot reach 35-65 should not consume budget ahead of
    markets that can -- but breadth is preserved: it stays in the
    universe and is still polled."""
    live._observed.clear()
    inband = _mkt("aec-a", False, 0.45, 0.46)
    faroff = _mkt("aec-b", False, 0.02, 0.03)
    assert live._market_score(inband) > live._market_score(faroff)
    sel, _ = live.select_for_poll([inband, faroff], 64, cycle=0)
    assert {t["slug"] for t in sel} == {"aec-a", "aec-b"}


def test_two_sided_and_spread_that_can_clear_the_bound_are_rewarded():
    """A maker pair edge equals the spread, so a sub-1c spread cannot
    reach the <=0.99 bound however much it trades."""
    live._observed.clear()
    wide = _mkt("aec-wide", False, 0.45, 0.47)     # 2c
    tight = _mkt("aec-tight", False, 0.45, 0.455)  # 0.5c
    onesided = _mkt("aec-one", True, 0.0, 0.06)
    assert live._market_score(wide) > live._market_score(tight)
    assert live._market_score(tight) > live._market_score(onesided)


def test_candidate_autopin_is_bounded_and_disableable(monkeypatch):
    live._observed.clear()
    monkeypatch.delenv(live.PIN_SLUGS_ENV, raising=False)
    live._auto_pins = []
    universe = [_mkt(f"m{i}", i % 2 == 0, 0.45, 0.47) for i in range(9)]
    monkeypatch.setenv(live.CANDIDATE_AUTOPIN_ENV, "2")
    assert len(live.top_candidate_pins(universe)) == 2
    monkeypatch.setenv(live.CANDIDATE_AUTOPIN_ENV, "0")
    assert live.top_candidate_pins(universe) == []


def test_pin_precedence_operator_then_program_then_candidate(monkeypatch):
    monkeypatch.setenv(live.PIN_SLUGS_ENV, "op-1")
    monkeypatch.setenv(live.PIN_MAX_ENV, "6")
    live._auto_pins = ["l5-1"]
    pins = live.priority_pins(["prog-1"], ["cand-1"])
    assert [p["reason"] for p in pins] == [
        "operator_pin", "live_incentive_program",
        "top_observed_candidate", "l5_candidate_auto_pin"]
