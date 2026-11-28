"""Regression for issue #167 comment 5519422461 — coverage/cold-start:

1. discovery must admit ALL live-event markets (props included) with
   metadata preserved, not only hardcoded slug prefixes;
2. the venue subscription cap must become a core+rotation plan that
   covers the whole slate over cycles instead of silently truncating;
3. a recorder restart must hydrate gate evidence from the persisted
   authenticated tape instead of resetting MATCHED history to zero,
   with stale rows still aging out.
None of this loosens the gate itself."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from src import pmus_sports_collector as col
from src import pmus_ws_evidence_recorder as rec


# --------------------- 1. prefix-free discovery + labels ---------------------

def _events(markets):
    return {"mlb": [{"live": True, "eventState": {"score": "1-0"},
                     "markets": markets}]}


def test_empty_prefixes_admit_props_with_metadata():
    markets = [
        {"slug": "aec-mlb-sf-pit-2026-09-02", "name": "Moneyline"},
        {"slug": "strikeouts-webb-over-6-5", "name": "Webb Ks O6.5",
         "type": "player_prop", "category": "props"},
        {"slug": "a5c-mlb-sf-pit-2026-09-02"},
    ]
    t = col.discover_targets(_events(markets), {}, [], True)
    slugs = [x["slug"] for x in t]
    assert "strikeouts-webb-over-6-5" in slugs          # prop admitted
    by = {x["slug"]: x for x in t}
    assert by["strikeouts-webb-over-6-5"]["prop"] is True
    assert by["aec-mlb-sf-pit-2026-09-02"]["prop"] is False
    assert by["strikeouts-webb-over-6-5"]["market_meta"]["type"] == \
        "player_prop"
    assert by["strikeouts-webb-over-6-5"]["league"] == "mlb"
    assert by["strikeouts-webb-over-6-5"]["event_state"]["score"] == "1-0"


def test_explicit_prefixes_still_filter_for_legacy_callers():
    markets = [{"slug": "aec-x"}, {"slug": "prop-y"}]
    t = col.discover_targets(_events(markets), {}, ["aec-", "a5c-"], True)
    assert [x["slug"] for x in t] == ["aec-x"]


# ------------------- 2. subscription plan: core + rotation -------------------

def _targets(n, prop_from=50):
    return [{"slug": f"m-{i:03d}", "league": "mlb",
             "why": "live_league_market", "event_state": None,
             "prop": i >= prop_from, "market_meta": {}}
            for i in range(n)]


def test_under_cap_subscribes_everything():
    plan = rec.subscription_plan(_targets(30), {}, 40, 0)
    d = plan["diagnostics"]
    assert len(plan["subscribe"]) == 30 and plan["excluded"] == []
    assert d["discovered_total"] == 30 and d["subscribed_total"] == 30
    assert d["excluded_total"] == 0


def test_rotation_covers_the_full_slate_over_cycles():
    targets = _targets(100)
    now = 1_700_000_000.0
    seen: set = set()
    for cycle in range(30):
        plan = rec.subscription_plan(targets, {}, 40, cycle, now)
        assert len(plan["subscribe"]) == 40
        assert plan["diagnostics"]["excluded_total"] == 60
        assert plan["diagnostics"]["excluded_reasons"] == \
            ["rotation_pending"]
        seen.update(plan["subscribe"])
    assert seen == {t["slug"] for t in targets}     # nothing silently excluded


def test_active_markets_pin_to_core_and_props_are_counted():
    targets = _targets(100)
    now = 1_700_000_000.0
    # the busiest observed market is a PROP outside any prefix convention
    state = {"m-099": {"trades": [{"t": now - 10}] * 50,
                       "both_sides_served": True, "book_updates": 300}}
    for cycle in range(10):
        plan = rec.subscription_plan(targets, state, 40, cycle, now)
        assert "m-099" in plan["subscribe"]         # stays pinned every cycle
    d = plan["diagnostics"]
    assert d["props_discovered"] == 50
    assert d["props_subscribed"] >= 1


def test_status_line_carries_discovery_and_eligible_total():
    status = rec.build_status({}, True, 10, 40,
                              discovery={"discovered_total": 90,
                                         "subscribed_total": 40,
                                         "excluded_total": 50,
                                         "props_discovered": 30})
    assert status["discovery"]["discovered_total"] == 90
    assert status["eligible_total"] == 0
    # older callers without the discovery kwarg still work
    assert "discovery" not in rec.build_status({}, True, 1, 1)


# ---------------- 3. restart hydration from the persisted tape ----------------

def _tape_row(t_epoch, msg):
    return json.dumps({
        "kind": "ws",
        "t": datetime.fromtimestamp(t_epoch, timezone.utc).isoformat(),
        "msg": msg}, separators=(",", ":"))


def _book(slug, bid, ask):
    return {"marketData": {"marketSlug": slug,
                           "bids": [{"px": {"value": bid}, "qty": "500"}],
                           "offers": [{"px": {"value": ask}, "qty": "500"}]}}


def _trade(slug, price, side):
    return {"trade": {"marketSlug": slug, "price": {"value": price},
                      "quantity": "1", "taker": {"side": side}}}


def _write_tape(root, now, rows):
    path = rec.daily_path(root, datetime.fromtimestamp(now, timezone.utc))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n")


def test_hydration_rebuilds_matched_history_after_restart(tmp_path):
    now = 1_700_000_000.0
    slug = "aec-hyd"
    # evidence recorded ~25 minutes before the restart: book + two honest
    # fill/complement slot pairs (the shape that clears MATCHED>=2)
    rows = [
        _tape_row(now - 1500, _book(slug, "0.4500", "0.4550")),
        _tape_row(now - 1490, _trade(slug, "0.4500", "SELL")),
        _tape_row(now - 1400, _trade(slug, "0.5500", "BUY")),
        _tape_row(now - 1180, _trade(slug, "0.4500", "SELL")),
        _tape_row(now - 1100, _trade(slug, "0.5500", "BUY")),
        "not-json-at-all {{{",                       # must never crash
        _tape_row(now - 90000, _trade(slug, "0.9900", "BUY")),   # stale: out
    ]
    _write_tape(tmp_path, now, rows)
    state: dict = {}
    report = rec.hydrate_state(
        state, rec.iter_recent_tape_rows(tmp_path, now), now)
    assert report["hydrated_rows"] == 5
    assert report["skipped_rows"] == 1               # the stale row aged out
    g = rec.ws_gate(state[slug], now)
    assert g["slot_counts_theoretical"].get("MATCHED", 0) >= 2, g
    assert g["gate_status"] == "Q1_GATE_CLEARED", g
    assert g["trades_1h"] == 4                       # stale trade not counted


def test_cold_start_without_tape_is_clean(tmp_path):
    state: dict = {}
    report = rec.hydrate_state(
        state, rec.iter_recent_tape_rows(tmp_path, 1_700_000_000.0))
    assert report == {"hydrated_rows": 0, "skipped_rows": 0,
                      "hydrated_markets": 0}
    assert state == {}


def test_hydration_respects_byte_bound(tmp_path):
    now = 1_700_000_000.0
    rows = [_tape_row(now - 600 + i, _book("aec-b", "0.50", "0.51"))
            for i in range(50)]
    _write_tape(tmp_path, now, rows)
    bounded = list(rec.iter_recent_tape_rows(tmp_path, now, max_bytes=400))
    everything = list(rec.iter_recent_tape_rows(tmp_path, now))
    assert 0 < len(bounded) < len(everything) == 50
