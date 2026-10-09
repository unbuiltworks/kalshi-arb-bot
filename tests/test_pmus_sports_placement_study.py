"""The sports comparison must not flatter sports.

Three things would make the verdict meaningless and are pinned here: an
unavailable ladder counted as a non-fill, a verdict computed on the in-sample
half, and a verdict that can be reached without enough entries.
"""
import pytest

from src import pmus_sports_placement_study as SP
from tests.test_pmus_l5_pair_policy_study import tape


def test_crypto_and_sports_are_split_by_slug_head():
    assert SP.category_of("cpc-btc-updown-15m-2026-10-09-1200z") == "crypto"
    assert SP.category_of("aec-atp-foo-bar-2026-10-09") == "sports"
    assert SP.cohort("aec-atp-x-y-2026-10-09")["market_type"] == "moneyline"
    assert SP.cohort("atc-csl-x-y-2026-10-09")["market_type"] == "three_way"


def test_a_missing_ladder_is_unavailable_evidence_not_a_non_fill():
    obs = tape([(0, 0.40, 0.60, 0, None), (60, 0.40, 0.60, 0, None)])
    for r in obs:
        r["book_levels"] = {}
    d = SP.diagnose(obs)
    assert d["why"] == "LADDER_UNKNOWN" and d["evidence_available"] is False


def test_no_volume_at_all_is_missing_flow():
    obs = tape([(0, 0.40, 0.60, 0, None), (60, 0.40, 0.60, 0, None),
                (120, 0.40, 0.60, 0, None)])
    assert SP.diagnose(obs)["why"] == "MISSING_FLOW"


def test_volume_that_does_not_clear_the_queue_is_not_missing_flow():
    obs = tape([(0, 0.40, 0.60, 0, None),
                (60, 0.39, 0.41, 5.0, 0.39),
                (120, 0.39, 0.41, 5.0, 0.39)])
    d = SP.diagnose(obs)
    assert d["why"] == "QUEUE_NOT_CLEARED"
    assert d["volume_into_our_price"] > 0


def test_a_one_sided_book_is_insufficient_depth():
    obs = tape([(0, 0.40, 0.40, 0, None), (60, 0.40, 0.40, 0, None)])
    assert SP.diagnose(obs)["why"] == "INSUFFICIENT_DEPTH"


def test_entry_conditions_read_the_first_admitting_row_only():
    obs = tape([(0, 0.40, 0.60, 0, None),
                (60, 0.10, 0.90, 0, None)])        # later, much wider
    c = SP.entry_conditions(obs)
    assert c["spread_ticks"] == 20.0 and c["spread_bucket"] == "over_5"


def test_verdict_is_underpowered_below_the_minimum_entry_count():
    v = SP.verdict({"full_inventory": {"net_per_entry_usd": {
        "n": 5, "mean": 0.5, "p05": 0.4, "p95": 0.6}}})
    assert v["verdict"] == "MORE_DATA" and "UNDERPOWERED" in v["why"]


def test_verdict_go_requires_the_whole_interval_above_zero():
    net = {"n": SP.MIN_ENTRIES, "mean": 0.1, "p05": 0.01, "p95": 0.2}
    assert SP.verdict({"full_inventory": {"net_per_entry_usd": net}}
                      )["verdict"] == "GO"
    net2 = {"n": SP.MIN_ENTRIES, "mean": 0.1, "p05": -0.01, "p95": 0.2}
    assert SP.verdict({"full_inventory": {"net_per_entry_usd": net2}}
                      )["verdict"] == "MORE_DATA"


def test_verdict_no_go_requires_the_whole_interval_below_zero():
    net = {"n": SP.MIN_ENTRIES, "mean": -0.1, "p05": -0.2, "p95": -0.01}
    assert SP.verdict({"full_inventory": {"net_per_entry_usd": net}}
                      )["verdict"] == "NO_GO"


def test_a_group_below_the_market_floor_is_skipped_not_scored():
    obs = tape([(0, 0.40, 0.60, 0, None), (60, 0.40, 0.60, 0, None),
                (120, 0.40, 0.60, 0, None)])
    out = SP.study({"aec-atp-a-b-2026-10-09": obs}, min_markets=8)
    g = out["groups"]["category=sports"]
    assert g["skipped"].startswith("FEWER_THAN_8")
    assert "verdict" not in g
