"""OWNER 5564173746: measure L5's OWN concurrency and market-switching
behaviour. These tests pin the measurement itself on synthetic tape (so a
wrong answer cannot hide behind the size of the real file) and then assert the
handful of headline findings on the canonical history."""
from __future__ import annotations

import os

import pytest

from src import l5_concurrency_study as st


def _row(wid, slug, asset, dur, seq, ts, resid, s2s=0, qty=1.0, px=0.5,
         maker="maker"):
    return {"window_id": wid, "market_slug": slug, "asset": asset,
            "duration_minutes": str(dur), "trade_seq": str(seq),
            "timestamp": str(ts), "seconds_to_settlement": str(s2s),
            "residual_qty_signed": str(resid), "matched_qty": "0",
            "quantity": str(qty), "price": str(px),
            "dollars": str(round(qty * px, 6)), "maker_taker": maker}


def _tape():
    """Two BTC-15m windows, strictly sequential, and one ETH-15m window that
    overlaps the second BTC one. A is worked, left with inventory open, and
    returned to."""
    return [
        _row("A", "btc-15m-1", "BTC", 15, 1, 1000, 5, s2s=200),
        _row("A", "btc-15m-1", "BTC", 15, 2, 1010, 7, s2s=190),
        _row("C", "eth-15m-1", "ETH", 15, 1, 1020, 3, s2s=400),
        _row("A", "btc-15m-1", "BTC", 15, 3, 1030, 0, s2s=170),
        _row("C", "eth-15m-1", "ETH", 15, 2, 1100, 9, s2s=320),
        _row("B", "btc-15m-2", "BTC", 15, 1, 1300, 4, s2s=100),
    ]


def test_episodes_are_one_per_market_with_dwell_settlement_and_residual():
    eps = st.episodes(_tape())
    assert [e["window_id"] for e in eps] == ["A", "C", "B"]
    a, c, b = eps
    assert a["t_first"] == 1000 and a["t_last"] == 1030 and a["dwell_s"] == 30
    assert a["t_settlement"] == 1200 and a["actions"] == 3
    assert a["final_residual_qty_signed"] == 0.0 and a["completed_inventory"] is True
    assert c["completed_inventory"] is False and c["t_settlement"] == 1420
    assert b["actions"] == 1 and b["maker_actions"] == 1
    assert round(a["gross_dollars"], 4) == 1.5


def test_concurrency_counts_only_real_overlap_and_splits_the_three_readings():
    eps = st.episodes(_tape())
    prof = st.concurrency_profiles(eps)
    # trading: A 1000-1030 overlaps C 1020-1100 for 10s; B traded once, so its
    # trading interval has no length and contributes no time (it is counted
    # instead, and separately, as a zero-length interval)
    tr = prof["trading"]
    assert tr["max_simultaneous"] == 2
    assert tr["seconds_at_level"] == {"1": 90, "2": 10}
    assert tr["zero_length_intervals"] == 1 and tr["active_seconds"] == 100
    # capital: A holds to 1200, C to 1420, B 1300-1400 -> A/C and C/B overlap
    cap = prof["capital"]
    assert cap["max_simultaneous"] == 2
    assert cap["share_at_2_or_more"] > tr["share_at_2_or_more"]
    # residual books: only C and B ended with inventory open; A completed
    assert prof["residual_books"]["intervals"] == 2


def test_same_lane_overlap_is_reported_separately_from_cross_lane():
    eps = st.episodes(_tape())
    comp = st.overlap_composition(eps, "trading")
    assert comp["same_lane_seconds"] == 0 and comp["cross_lane_seconds"] == 10
    assert comp["by_lane_pair_seconds"] == {"BTC-15m + ETH-15m": 10}
    assert comp["same_lane_share"] == 0.0
    # ... and a genuine same-lane overlap is caught, not silently folded in
    tape = _tape() + [_row("D", "btc-15m-3", "BTC", 15, 1, 1005, 2, s2s=50),
                      _row("D", "btc-15m-3", "BTC", 15, 2, 1025, 2, s2s=30)]
    comp2 = st.overlap_composition(st.episodes(tape), "trading")
    assert comp2["same_lane_seconds"] == 20
    assert comp2["by_lane_pair_seconds"]["BTC-15m + BTC-15m"] == 20
    assert comp2["same_lane_share"] > 0


def test_lane_concurrency_collapses_a_lane_to_one_track():
    eps = st.episodes(_tape())
    lc = st.lane_concurrency(eps, "trading")
    assert lc["lanes"] == ["BTC-15m", "ETH-15m"]
    assert lc["max_simultaneous"] == 2
    assert lc["max_markets_open_within_one_lane"] == 1
    # two BTC windows open at once is one lane, and is flagged as such
    tape = _tape() + [_row("D", "btc-15m-3", "BTC", 15, 1, 1005, 2, s2s=50),
                      _row("D", "btc-15m-3", "BTC", 15, 2, 1025, 2, s2s=30)]
    lc2 = st.lane_concurrency(st.episodes(tape), "trading")
    assert lc2["max_markets_open_within_one_lane"] == 2
    assert lc2["max_simultaneous"] == 2      # still only two lanes


def test_switching_counts_runs_revisits_and_departures_with_inventory_open():
    sw = st.switching(_tape())
    # runs: A(1000,1010) C(1020) A(1030) C(1100) B(1300)
    assert sw["runs"] == 5 and sw["switches"] == 4
    # A left at 1010 with residual 7 and more actions to come -> open-inventory
    # C left at 1020 with residual 3 and more actions to come -> open-inventory
    # A left at 1030 flat; C left at 1100 with nothing to return to
    assert sw["switches_leaving_open_inventory"] == 2
    assert sw["switches_leaving_nothing_to_return_to"] == 2
    assert sw["episodes_revisited_after_leaving"] == 2
    assert sw["visits_per_episode"]["max"] == 2
    assert sw["actions_per_run"]["mean"] == 1.2


def test_spray_counts_distinct_markets_per_wall_clock_bucket():
    sp = st.spray(_tape(), 300)
    assert sp["buckets_with_activity"] == 2          # 1000-1199, 1200-1499
    assert sp["distinct_markets_per_bucket"]["max"] == 2
    assert sp["histogram"] == {1: 1, 2: 1}
    assert sp["share_of_buckets_with_2_or_more"] == 0.5


def test_coverage_reports_gaps_as_missing_data_not_as_silence():
    rows = [_row("A", "s", "BTC", 15, 1, 1773914977, 1),      # 2026-03-19
            _row("B", "t", "BTC", 15, 1, 1788217879, 1)]      # 2026-08-31
    cov = st.coverage(st.episodes(rows))
    assert cov["distinct_trading_days"] == 2
    assert cov["gaps_over_one_day"][0]["days"] == 165
    assert "not proof of absence" in cov["note"]


def test_provenance_is_carried_and_names_the_fill_not_quote_limit():
    prov = st.provenance(_tape())
    assert prov["rows_read"] == 6 and prov["maker_taker_counts"] == {"maker": 6}
    assert any("not a quote" in x for x in prov["limits"])
    assert any("never treated as maker" in x for x in prov["limits"])


@pytest.mark.skipif(not os.path.exists(st.CANONICAL_PATH),
                    reason="canonical history not present")
def test_the_headline_findings_on_the_canonical_history():
    rows = st.load_rows()
    eps = st.episodes(rows)
    assert len(eps) == 2216 and len(rows) == 71984
    # 1. L5 never ran two markets in the SAME product lane at once
    for mode in ("trading", "capital"):
        assert st.overlap_composition(eps, mode)["same_lane_seconds"] == 0
        assert st.lane_concurrency(eps, mode)["max_markets_open_within_one_lane"] == 1
    # 2. concurrency never exceeded four, and four lanes is its ceiling
    prof = st.concurrency_profiles(eps)
    assert prof["trading"]["max_simultaneous"] == 4
    assert st.lane_concurrency(eps, "trading")["lanes"] == [
        "BTC-15m", "BTC-5m", "ETH-15m", "ETH-5m"]
    # 3. the early era ran strictly one market at a time, start to finish
    early = [e for e in eps if e["t_first"] < 1780000000]
    early_rows = [r for r in rows if r["window_id"] in {e["window_id"] for e in early}]
    assert st.concurrency_profiles(early)["trading"]["max_simultaneous"] == 1
    assert st.switching(early_rows)["switches_leaving_open_inventory"] == 0
    assert st.switching(early_rows)["visits_per_episode"]["mean"] == 1.0
    # 4. the mature era interleaved: most switches left inventory open
    late = [e for e in eps if e["t_first"] >= 1780000000]
    late_rows = [r for r in rows if r["window_id"] in {e["window_id"] for e in late}]
    assert st.concurrency_profiles(late)["trading"]["max_simultaneous"] == 4
    assert st.switching(late_rows)["share_of_switches_leaving_open_inventory"] > 0.75
    # 5. residual is the norm: L5 almost never flattened a market's inventory
    assert st.episode_shape(eps)["share_completing_inventory"] < 0.02
