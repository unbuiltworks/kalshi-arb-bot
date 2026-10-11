"""OWNER 5570474405: opportunity_utilization classifies the campaign's time and names the gate."""
from __future__ import annotations

import json
import os

from src import l5_opportunity_utilization as ou

P = "PMUS_L5_CAMPAIGN "


def _dump(path, rows):
    json.dump({"deploy": [{"timestamp": ts, "message": P + json.dumps(j)} for ts, j in rows]}, open(path, "w"))


def _refused(n_closed, thin_slugs):
    rows = [{"slug": "old-%d" % i, "bid": "0.40", "reason": "strict_evidence_market_closed:True(status=MARKET_STATUS_RESOLVED)", "state_n": 7} for i in range(n_closed)]
    rows += [{"slug": s, "bid": "0.40", "reason": "strict_evidence_thin_flow:0", "state_n": 7} for s in thin_slugs]
    return rows


def test_timeline_intervals_and_gates(tmp_path):
    rows = [
        ("2026-09-07T08:00:00Z", {"kind": "session_end", "slug": "m1", "status": "SPRING_RESIDUAL_HOLD", "orders": [{"filled_qty": "5"}]}),
        ("2026-09-07T08:10:00Z", {"kind": "session_end", "slug": "m1", "status": "SPRING_RESIDUAL_HOLD", "orders": [{"filled_qty": "0"}]}),
        ("2026-09-07T08:20:00Z", {"kind": "session_end", "slug": "m1", "status": "SPRING_SKIP_NOT_QUALIFYING", "orders": []}),
        ("2026-09-07T08:30:00Z", {"kind": "settlement", "slug": "m1", "settled": True, "status": "REALIZED"}),
        ("2026-09-07T08:30:00Z", {"kind": "session_end", "slug": "x", "status": "SPRING_SKIP_NOT_QUALIFYING", "orders": []}),
        ("2026-09-07T08:40:00Z", {"kind": "session_end", "slug": "m2", "status": "SPRING_RESIDUAL_HOLD", "orders": [{"filled_qty": "5"}]}),
        ("2026-09-07T08:50:00Z", {"kind": "session_end", "slug": "m2", "status": "SPRING_MATCHED", "orders": [{"filled_qty": "5"}]}),
        ("2026-09-07T09:10:00Z", {"kind": "settlement", "slug": "m2", "settled": True, "status": "SETTLED_REALIZED_UNCONFIRMED"}),
        ("2026-09-07T09:30:00Z", {"kind": "selection", "status_t": "2026-09-07T09:29:00+00:00", "n_rows": 3, "chosen": None, "admitted": [],
                                  "game_progress": {"live_events": 40, "markets_with_progress": 100}, "refused": _refused(2, ["live-thin"])}),
        ("2026-09-07T10:00:00Z", {"kind": "funnel", "cycles": 10}),
    ]
    p = os.path.join(tmp_path, "d.json")
    _dump(p, rows)
    tl = ou.timeline([p])
    assert [m["slug"] for m in tl["markets"]] == ["m1", "m2"]
    assert tl["examined_not_acted"][0]["slug"] == "x"
    ivs = ou.intervals(tl, [(ou._iso("2026-09-07T08:12:00Z"), ou._iso("2026-09-07T08:15:00Z"), "down")])
    kinds = sorted((i["kind"], i.get("slug")) for i in ivs)
    assert kinds == sorted([("ACTIVE", "m1"), ("STOPPED", None), ("STRANDED", "m1"), ("IDLE", None), ("ACTIVE", "m2"), ("FLAT_WAIT", "m2"), ("IDLE", None)])
    by = {(i["kind"], i.get("slug")): i for i in ivs}
    assert by[("STRANDED", "m1")]["hours"] == round(20 / 60, 3)
    assert ivs[-1]["kind"] == "IDLE" and ivs[-1]["hours"] == round(50 / 60, 3)
    assert [i["t0"] for i in ivs] == sorted(i["t0"] for i in ivs)
    pool = ou.nearest_pool(tl["selections"], ivs[-1]["t0"], ivs[-1]["t1"])
    assert pool["samples_in_interval"] == 1 and pool["last"]["closed"] == 2 and pool["last"]["thin_flow"] == 1
    assert pool["thin_flow_slugs_seen"] == ["live-thin"]


def test_attribution_names_a_coverage_bottleneck_only_with_an_executable_universe():
    iv = {"kind": "IDLE", "t0": 0, "t1": 3600, "hours": 1.0, "gate": "CAMPAIGN_NO_CANDIDATE"}
    pool = {"last": {"n_rows": 80, "closed": 78, "thin_flow": 2}}
    uni = {"of_which_live": 14, "in_distribution": 257, "executable_on_distribution_depth_two_sided": 95}
    a = ou.attribute(iv, pool, uni)
    assert "bottleneck" in a["verdict"] and a["gate"].startswith("recorder pool composition")
    a2 = ou.attribute(iv, pool, {"of_which_live": 0})
    assert "bottleneck" not in a2["verdict"]
    a3 = ou.attribute({"kind": "FLAT_WAIT", "gate": "g"}, None, uni)
    assert "implementation gate" in a3["verdict"]


def test_r4_study_on_the_saved_dumps_finds_the_idle_bottleneck():
    logs = os.path.join(os.path.dirname(__file__), "..", "..", "r4_logs")
    if not os.path.isdir(logs):
        import pytest
        pytest.skip("scratch dumps not present")
    out = ou.study([logs], now=ou._iso("2026-09-07T12:17:10Z"))
    assert out["hours_by_kind"]["IDLE"] > 2.0
    assert out["opportunity_utilization"]["idle_bottleneck_hours"] > 2.0
    assert any(r["kind"] == "FLAT_WAIT" for r in out["intervals"])
