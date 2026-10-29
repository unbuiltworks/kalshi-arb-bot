"""OWNER 5569709706 #3: read-only scorecards, no direction, fail closed."""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_market_scorecard as sc


def _row(**kw):
    base = {"slug": "aec-atpcq-a-b-2026-09-07", "sport": "tennis", "bid": "0.45", "ask": "0.47", "bid_touch_qty": 10,
            "ask_qty": 12, "pair_at_touch": 0.98, "rebate_eligible": True, "game_proximity": 0.2,
            "off_distribution": False, "state_n": 7, "state": "p45-55c|tte>10m|flat|open|q<5", "pair_score": 0.37, "rank": 3}
    base.update(kw)
    return base


def test_executability_terms_are_bounded_and_read_off_public_features():
    ex = sc.executability(_row())
    t = ex["terms"]
    assert t["two_sided"] == 1.0 and t["depth"] == 1.0 and t["spread"] == 0.8 and t["incentive"] == 1.0
    assert t["recycling"] == 0.2 and t["on_distribution"] == 1.0
    assert ex["score"] == round((1 + 1 + 0.8 + 1 + 0.2 + 1) / 6, 4)
    assert ex["rebate_per_5sh_print_usd"] == 0.02                       # 0.0125*5*0.47*0.53 = 0.0156 -> 0.02
    # a pair at touch above 1.00 does not pass, nor thin depth, nor a 10 c spread, nor a 0.10 price for rebates
    assert sc.executability(_row(pair_at_touch=1.03))["terms"]["two_sided"] == 0.0
    assert sc.executability(_row(bid_touch_qty=2))["terms"]["depth"] == 0.4
    assert sc.executability(_row(bid="0.37", pair_at_touch=1.10))["terms"]["spread"] == 0.0
    assert sc.executability(_row(ask="0.05"))["terms"]["incentive"] == 0.0
    assert sc.executability(_row(state_n=2))["terms"]["on_distribution"] == 0.0


def test_rebate_per_print_uses_the_cent_rounding():
    assert sc.rebate_per_print(D("0.50")) == D("0.02")
    assert sc.rebate_per_print(D("0.10")) == D("0.01")                   # 0.005625 -> 0.01 (banker's: 0.0056 rounds up)
    assert sc.rebate_per_print(D("0.05")) == D("0.00")


def test_cards_rank_by_executability_then_campaign_score_and_carry_priors(tmp_path):
    import json, os
    rank = {"t_utc": "t", "in_distribution": 2, "ranked": [_row(slug="aec-atpcq-x-y-2026-09-07", pair_score=0.40, state_n=1),
                                                          _row(slug="aec-atpcq-a-b-2026-09-07", pair_score=0.37)]}
    ext = {"extend_conditioned": {"band": {"p0.45-0.55": {"productive_share_of_usd": 0.89, "net_per_usd": 0.0008, "lots": 3119}},
                                  "tte": {"tte>10m": {"productive_share_of_usd": 0.99, "net_per_usd": 0.0001, "lots": 9028}}}}
    sb = {"by_shape": {"match_winner": {"markets": 2, "turnover_usd": 18.6, "net_per_turnover": 0.115}},
          "by_category": {"tennis": {"markets": 2, "turnover_usd": 18.6, "net_per_turnover": 0.115}}}
    p = {}
    for name, obj in (("rank", rank), ("ext", ext), ("sb", sb)):
        p[name] = os.path.join(tmp_path, name + ".json")
        json.dump(obj, open(p[name], "w"))
    out = sc.scorecards(p["rank"], p["ext"], p["sb"], top=5)
    top = out["top"]
    assert [c["slug"] for c in top] == ["aec-atpcq-a-b-2026-09-07", "aec-atpcq-x-y-2026-09-07"]   # on-distribution first
    c = top[0]
    assert c["l5_prior_by_band"]["completed_share"] == 0.89 and c["l5_prior_by_tte"]["n"] == 9028
    assert c["our_shape_economics"]["status"] == "INSUFFICIENT" and c["our_sport_economics"]["markets"] == 2
    assert out["distribution"]["cards"] == 2 and out["distribution"]["on_distribution"] == 1
    assert "no direction" in out["labels"]
    md = sc.render_md(out)
    assert "aec-atpcq-a-b-2026-09-07" in md
