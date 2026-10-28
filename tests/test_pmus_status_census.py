"""The tracked-market census that makes NFL absence diagnosable.

On 2026-10-11, with a live NFL game in progress, no NFL market appeared in
any `top_candidates` list and the logs could not say whether NFL was never
discovered, truncated by the live-events page cap, filtered out because the
venue had not marked the event live, or simply out-ranked by busier tennis.
`top_candidates` is a top-5 cut of 3,200+ tracked markets, so its silence
distinguishes none of those. A census over the WHOLE tracked set separates
the ranking case from the discovery cases immediately.
"""
from __future__ import annotations

from src import pmus_status_report as R


def test_a_league_present_in_the_census_but_not_the_top_five_is_a_ranking_miss():
    by = {"aec-nfl-phi-jax-2026-10-11": [1], "aec-atp-a-b-2026-10-11": [1]}
    meta = {"aec-nfl-phi-jax-2026-10-11": {"league": "nfl"},
            "aec-atp-a-b-2026-10-11": {"league": "atp"}}
    c = R._census(by, meta, "league")
    assert c["nfl"] == 1, "NFL tracked -- so top-5 silence is a ranking miss"


def test_counts_are_descending_so_the_busy_leagues_read_first():
    by = {"a": [1], "b": [1], "c": [1]}
    meta = {"a": {"league": "atp"}, "b": {"league": "atp"},
            "c": {"league": "nfl"}}
    assert list(R._census(by, meta, "league")) == ["atp", "nfl"]


def test_a_missing_league_is_reported_not_dropped():
    """A market with no league is itself a discovery-path fact."""
    by = {"a": [1], "b": [1]}
    meta = {"a": {"league": None}, "b": {}}
    c = R._census(by, meta, "league")
    assert c == {"unknown": 2}
    assert sum(c.values()) == len(by), "the census must cover every market"


def test_the_census_covers_every_tracked_market():
    by = {"m%d" % i: [1] for i in range(37)}
    meta = {k: {"league": "atp" if i % 2 else "nfl"}
            for i, k in enumerate(by)}
    assert sum(R._census(by, meta, "league").values()) == 37


def test_the_why_census_separates_the_discovery_paths():
    by = {"a": [1], "b": [1], "c": [1]}
    meta = {"a": {"why": "v1_live_event_market"},
            "b": {"why": "active_liquidity_program"},
            "c": {"why": "v1_live_event_market"}}
    c = R._census(by, meta, "why")
    assert c["v1_live_event_market"] == 2
    assert c["active_liquidity_program"] == 1


def test_the_status_payload_carries_both_censuses():
    import inspect
    src = inspect.getsource(R)
    assert '"tracked_by_league": _census(' in src
    assert '"tracked_by_why": _census(' in src
    i = src.index('"tracked_by_league"')
    assert "NFL" in src[max(0, i - 1200):i], \
        "the reason for the census must sit beside it"
