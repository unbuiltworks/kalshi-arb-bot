"""The R5F cohort table must never let a derived figure be read as a
realized one, and must count each market exactly once (OWNER #167
5577286614 items 2 and 7)."""
from __future__ import annotations

import json
from decimal import Decimal as D

from src import l5_r5f_cohort as coh


def _settlement(slug, ts, *, status, confirmed, realized, residual,
                matched="10.00", pair_cost="0.95", contribution="0.5000"):
    body = {
        "kind": "settlement", "slug": slug, "status": status,
        "settled": status != "PENDING_SETTLEMENT",
        "realized_confirmed": confirmed, "realized_usd": realized,
        "breakdown": {
            "slug": slug, "fills": 4, "matched_pairs": matched,
            "matched_pair_cost": pair_cost,
            "matched_contribution_usd": contribution,
            "directional_residual": residual,
            "residual_contribution_usd": None,
            "cost_basis_usd": "9.50", "maker_fills": 4, "taker_fills": 0,
            "fees_usd": "venue_unreported",
        },
    }
    return {"timestamp": ts, "message": "PMUS_L5_CAMPAIGN " + json.dumps(body)}


def _dump(tmp_path, rows, name="logs.txt"):
    p = tmp_path / name
    p.write_text(json.dumps({"deploy": rows}))
    return str(p)


def test_classification_follows_evidence_authority(tmp_path):
    rows = [
        # the venue published a number: that is realized, full stop
        _settlement("venue", "2026-09-07T20:00:00Z", status="REALIZED",
                    confirmed=True, realized="-3.8778", residual="10.35"),
        # settled flat: locked by arithmetic even without a venue number
        _settlement("locked", "2026-09-07T21:00:00Z",
                    status="SETTLED_REALIZED_UNCONFIRMED", confirmed=False,
                    realized=None, residual="0.00", contribution="0.2164"),
        # settled with residual and no venue number: nothing may be claimed
        _settlement("indet", "2026-09-07T22:00:00Z",
                    status="SETTLED_REALIZED_UNCONFIRMED", confirmed=False,
                    realized=None, residual="5.07"),
        # not settled at all
        _settlement("pend", "2026-09-07T23:00:00Z",
                    status="PENDING_SETTLEMENT", confirmed=False,
                    realized=None, residual="1.00"),
    ]
    res = coh.build([_dump(tmp_path, rows)])
    by = {m["slug"]: m for m in res["markets"]}
    assert by["venue"]["class"] == coh.VENUE_CONFIRMED
    assert by["venue"]["value_usd"] == "-3.8778"
    assert by["locked"]["class"] == coh.LOCKED_ZERO_RESIDUAL
    assert by["locked"]["value_usd"] == "0.2164"
    assert "derived_locked_pre_fee" in by["locked"]["value_basis"]
    # the two that cannot be valued carry NO number at all
    assert by["indet"]["class"] == coh.INDETERMINATE
    assert by["indet"]["value_usd"] is None
    assert by["pend"]["class"] == coh.PENDING
    assert by["pend"]["value_usd"] is None
    # confirmed and derived are reported apart, and the combined figure
    # carries its warning in the payload itself
    assert res["venue_confirmed_realized_usd"] == "-3.8778"
    assert res["derived_locked_pre_fee_usd"] == "0.2164"
    assert "never be reported as one" in res["combined_indicative_usd"]["warning"]
    assert res["counts"] == {
        "settled_markets_in_cohort": 4, "venue_confirmed": 1,
        "locked_zero_residual": 1, "indeterminate": 1, "pending": 1,
        "markets_before_cohort": 0}


def test_a_market_logged_many_times_is_counted_once(tmp_path):
    """Settlement rows repeat every cycle as a market moves PENDING ->
    SETTLED. Summing the raw rows would multiply the cohort."""
    rows = [
        _settlement("m", "2026-09-07T20:00:00Z", status="PENDING_SETTLEMENT",
                    confirmed=False, realized=None, residual="0.00"),
        _settlement("m", "2026-09-07T20:30:00Z", status="PENDING_SETTLEMENT",
                    confirmed=False, realized=None, residual="0.00"),
        _settlement("m", "2026-09-07T21:00:00Z", status="REALIZED",
                    confirmed=True, realized="-1.2500", residual="3.00"),
    ]
    res = coh.build([_dump(tmp_path, rows)])
    assert len(res["markets"]) == 1
    assert res["markets"][0]["class"] == coh.VENUE_CONFIRMED
    assert res["venue_confirmed_realized_usd"] == "-1.2500"


def test_cohort_start_excludes_earlier_rungs(tmp_path):
    """R5F begins when its approval was consumed; earlier rungs are a
    different shell and must not be blended into the gate."""
    rows = [
        _settlement("r4", "2026-09-07T18:50:00Z", status="REALIZED",
                    confirmed=True, realized="-1.9912", residual="5.24"),
        _settlement("r5f", "2026-09-07T20:02:00Z", status="REALIZED",
                    confirmed=True, realized="-3.8778", residual="10.35"),
    ]
    res = coh.build([_dump(tmp_path, rows)])
    assert [m["slug"] for m in res["markets"]] == ["r5f"]
    assert [m["slug"] for m in res["markets_before_cohort"]] == ["r4"]
    assert res["venue_confirmed_realized_usd"] == "-3.8778"


def test_pairing_split_and_uncertainty_are_reported(tmp_path):
    rows = [
        _settlement("p1", "2026-09-07T20:00:00Z",
                    status="SETTLED_REALIZED_UNCONFIRMED", confirmed=False,
                    realized=None, residual="0.00", contribution="0.4000"),
        _settlement("p2", "2026-09-07T20:10:00Z",
                    status="SETTLED_REALIZED_UNCONFIRMED", confirmed=False,
                    realized=None, residual="0.00", contribution="0.6000"),
        _settlement("r1", "2026-09-07T20:20:00Z", status="REALIZED",
                    confirmed=True, realized="-2.0000", residual="4.00"),
        _settlement("r2", "2026-09-07T20:30:00Z", status="REALIZED",
                    confirmed=True, realized="-3.0000", residual="6.00"),
    ]
    res = coh.build([_dump(tmp_path, rows)])
    paired = res["pairing_split"]["paired_to_zero"]
    resid = res["pairing_split"]["left_residual"]
    assert paired["markets"] == 2 and paired["sum_usd"] == "1.0000"
    assert paired["negative_markets"] == 0
    assert resid["markets"] == 2 and resid["sum_usd"] == "-5.0000"
    assert resid["positive_markets"] == 0
    assert paired["per_market"]["mean"] == 0.5
    lo, hi = paired["per_market"]["ci95"]
    assert lo <= 0.5 <= hi
    # every mean is accompanied by n, and a single market is refused an interval
    single = coh._bootstrap_mean_ci([D("0.25")])
    assert single["n"] == 1 and single["ci95"] is None
    assert "no interval is meaningful" in single["note"]
    # the gate reads venue-confirmed expectancy ONLY
    assert res["expectancy_venue_confirmed_only"]["n"] == 2
    assert res["expectancy_venue_confirmed_only"]["mean"] == -2.5


def test_markdown_names_the_provenance_of_every_total(tmp_path):
    rows = [_settlement("m", "2026-09-07T20:00:00Z",
                        status="SETTLED_REALIZED_UNCONFIRMED", confirmed=False,
                        realized=None, residual="0.00", contribution="0.2164")]
    md = coh.render_markdown(coh.build([_dump(tmp_path, rows)]))
    assert "Venue-confirmed realized: 0.0000" in md
    assert "Derived, locked, pre-fee: 0.2164" in md
    assert "INDICATIVE ONLY" in md
    assert "PRE-FEE" in md
    assert "credited separately" in md
    assert "descriptive, not causal" in md


def test_unreadable_and_irrelevant_rows_never_crash_the_build(tmp_path):
    good = _settlement("m", "2026-09-07T20:00:00Z", status="REALIZED",
                       confirmed=True, realized="-1.0000", residual="2.00")
    rows = [
        {"timestamp": "2026-09-07T19:00:00Z", "message": "not json at all {{{"},
        {"timestamp": "2026-09-07T19:30:00Z",
         "message": 'PMUS_L5_CAMPAIGN {"kind":"funnel","cycles":3}'},
        # a settlement without a breakdown carries nothing to account for
        {"timestamp": "2026-09-07T19:45:00Z",
         "message": 'PMUS_L5_CAMPAIGN {"kind":"settlement","slug":"x"}'},
        good,
    ]
    p_bad = tmp_path / "missing.txt"
    res = coh.build([_dump(tmp_path, rows), str(p_bad)])
    assert [m["slug"] for m in res["markets"]] == ["m"]
