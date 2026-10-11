"""OWNER 5569845340 A: the economic scoreboard is built from settlement rows,
labels its bases, and never manufactures a venue number."""
from __future__ import annotations

import json
import os

from src import l5_economic_scoreboard as sb

PREFIX = "PMUS_L5_CAMPAIGN "


def _dump(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"deploy": [{"timestamp": ts, "message": PREFIX + json.dumps(j)} for ts, j in rows]}, f)


def _settlement(slug, realized, trades, breakdown, resolution, log_ts):
    j = {"kind": "settlement", "slug": slug, "settled": True, "status": "REALIZED" if realized is not None
         else "SETTLED_REALIZED_UNCONFIRMED", "realized_usd": realized,
         "settled_by": "position_resolution_activity" if realized is not None else "venue_market_resolved_account_flat",
         "trades": {"rows": trades}, "resolution": resolution, "breakdown": breakdown}
    return log_ts, j


def _fixture(tmp_path):
    rows = [
        ("2026-09-06T19:31:11Z", {"kind": "campaign_continuation_start", "campaign_id": "R2C-RUN-2026-09-06"}),
        ("2026-09-06T15:52:18Z", {"kind": "campaign_continuation_start", "campaign_id": "R1-EVIDENCE-CONTINUATION-2026-09-06",
                                  "preflight": {"checks": {"venue_settled": {"markets": {
                                      "aec-mlb-old-2026-09-05": {"status": "REALIZED", "settled": True, "settled_by": "position_resolution_activity",
                                                                 "realized_confirmed": True, "realized_usd": "0.0191"}}}}}}),
        # a venue-realized market with a residual, decisions captured under R2C
        _settlement("asc-cfb-a-b-2026-09-06-pos-2pt5", "-0.0133",
                    [{"t": "2026-09-06T21:39:11Z", "px": "0.81", "qty": "0.01"},
                     {"t": "2026-09-06T21:33:28Z", "px": "0.52", "qty": "0.01"}],
                    {"bought_yes": "0.02", "bought_no": "0.06", "avg_yes": "0.495", "avg_no": "0.390",
                     "cost_basis_usd": "0.0333", "matched_pairs": "0.02", "matched_pair_cost": "0.885",
                     "matched_contribution_usd": "0.0023", "directional_residual": "-0.04",
                     "resolution_side": "POSITION_RESOLUTION_SIDE_LONG", "realized_usd_venue": "-0.0133",
                     "residual_contribution_usd": "-0.0156"},
                    {"side": "POSITION_RESOLUTION_SIDE_LONG", "updateTime": "2026-09-06T22:33:28Z"},
                    "2026-09-06T21:47:30Z"),
        ("2026-09-06T21:40:00Z", {"kind": "session_end", "slug": "asc-cfb-a-b-2026-09-06-pos-2pt5",
                                  "decisions": [{"safety_shell_version": "R2C/cap0.50"}],
                                  "orders": [{"order_id": "o1", "side": "NO", "px": "0.48", "filled_qty": "0.01"}]}),
        # a flat-account market in the R1 window with a pre-game public settlement_set_time
        _settlement("aec-cs2-x-y-2026-09-06", None,
                    [{"t": "2026-09-06T16:18:11Z", "px": "0.45", "qty": "0.02"},
                     {"t": "2026-09-06T15:53:19Z", "px": "0.45", "qty": "0.02"}],
                    {"bought_yes": "0.02", "bought_no": "0.02", "avg_yes": "0.45", "avg_no": "0.55",
                     "cost_basis_usd": "0.02", "matched_pairs": "0.02", "matched_pair_cost": "1.00",
                     "matched_contribution_usd": "0", "directional_residual": "0.00",
                     "realized_usd_venue": None, "residual_contribution_usd": None},
                    {"side": None, "public_settlement_px": "0.45", "public_settlement_set_time": "2026-09-06T11:05:14Z"},
                    "2026-09-06T16:50:46Z"),
    ]
    p = os.path.join(tmp_path, "dump.json")
    _dump(p, rows)
    reb = os.path.join(tmp_path, "reb.json")
    with open(reb, "w") as f:
        json.dump({"rows": [{"our_commission": "-0.01"}], "by_market": {"asc-cfb-a-b-2026-09-06-pos-2pt5": {"credited_usd": "0.01", "prints": 1}},
                   "audits": ["a"]}, f)
    return p, reb


def test_venue_realized_market_row_keeps_every_term_separate(tmp_path):
    p, reb = _fixture(str(tmp_path))
    out = sb.scoreboard([p], reb)
    r = {m["slug"]: m for m in out["markets"]}["asc-cfb-a-b-2026-09-06-pos-2pt5"]
    assert r["cohort"] == "R2C" and r["cohort_source"] == "session_decisions"
    assert r["realized_usd_venue"] == -0.0133 and r["net_basis"] == "venue_realized"
    assert r["rebate_credited_usd"] == 0.01 and r["net_usd"] == round(-0.0133 + 0.01, 4)
    assert r["pair_contribution_usd"] == 0.0023 and r["residual_contribution_usd"] == -0.0156
    assert r["fees_usd"] == 0.0 and r["liquidity_rewards_credited_usd"] == 0.0
    assert r["resolution_time_source"] == "position_resolution"
    # first print 21:33:28 -> resolution 22:33:28 = 1 h x cost basis 0.0333
    assert r["capital_hours_first_fill_bound"] == 0.033 and r["residual_duration_h"] == 1.0
    assert r["price_band"] == "p0.35-0.45"


def test_flat_account_market_uses_the_derived_contribution_and_labels_it(tmp_path):
    p, reb = _fixture(str(tmp_path))
    out = sb.scoreboard([p], reb)
    r = {m["slug"]: m for m in out["markets"]}["aec-cs2-x-y-2026-09-06"]
    assert r["realized_usd_venue"] is None and r["net_basis"] == "derived_matched_pairs_flat_account"
    assert r["net_usd"] == 0.0 and r["rebate_credited_usd"] == 0.0
    # decisions not captured: the campaign timeline puts a 15:53 first print in R1
    assert r["cohort"] == "R1" and r["cohort_source"] == "campaign_timeline"
    # the public settlement_set_time (11:05, pre-game) is before the last print: not a resolution time
    assert r["resolution_time_source"] == "audit_row_time_upper_bound"
    assert r["capital_hours_first_fill_bound"] >= 0


def test_venue_only_rows_carry_the_venue_number_and_no_invented_turnover(tmp_path):
    p, reb = _fixture(str(tmp_path))
    out = sb.scoreboard([p], reb)
    r = {m["slug"]: m for m in out["markets"]}["aec-mlb-old-2026-09-05"]
    assert r["breakdown_source"] == "preflight_venue_settled_snapshot_only"
    assert r["realized_usd_venue"] == 0.0191 and r["net_usd"] == 0.0191
    assert r["filled_usd_turnover"] == 0.0 and r["net_per_turnover"] is None
    assert out["coverage"]["venue_realized_only"] == ["aec-mlb-old-2026-09-05"]
    assert out["coverage"]["markets"] == 3 and out["coverage"]["with_settlement_breakdown"] == 2


def test_aggregates_sum_the_terms_and_the_all_block_covers_every_market(tmp_path):
    p, reb = _fixture(str(tmp_path))
    out = sb.scoreboard([p], reb)
    assert out["all"]["markets"] == 3
    assert out["all"]["net_usd"] == round(0.0191 + (-0.0133 + 0.01) + 0.0, 4)
    assert out["all"]["rebates_credited_usd"] == 0.01 and out["all"]["fees_usd"] == 0.0
    assert set(out["by_cohort"]) == {"R1", "R2C"}
    assert out["by_cohort"]["R2C"]["residual_contribution_usd"] == -0.0156
    assert out["rebate_census"]["credited_prints"] == 1 and out["rebate_census"]["credited_usd"] == 0.01


def test_cohort_by_timeline_is_r1_before_the_first_logged_campaign():
    starts = {"R2C-RUN-2026-09-06": "2026-09-06T19:31:11Z", "R3L-RUN-2026-09-06": "2026-09-07T01:21:25Z"}
    assert sb.cohort_by_timeline(sb._iso("2026-09-06T12:00:00Z"), starts) == "R1"
    assert sb.cohort_by_timeline(sb._iso("2026-09-06T20:00:00Z"), starts) == "R2C"
    assert sb.cohort_by_timeline(sb._iso("2026-09-07T02:00:00Z"), starts) == "R3L"
    assert sb.cohort_by_timeline(None, starts) is None
