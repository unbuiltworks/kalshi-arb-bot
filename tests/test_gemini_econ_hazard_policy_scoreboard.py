"""Hazard calibration discipline, the cohort split, and scoreboard honesty."""
from decimal import Decimal as D

from src.gemini_execution.econ import hazard as H
from src.gemini_execution.econ import policy as P
from src.gemini_execution.econ import scoreboard as S
from src.gemini_execution.econ import veto as V

BOOK = {"yes": {"bid": "0.60", "ask": "0.65", "size": "5"},
        "no": {"bid": "0.35", "ask": "0.40", "size": "3"}}
PRINTS = [{"side": "buy", "qty": "1"}, {"side": "sell", "qty": "2"},
          {"side": "buy", "qty": "1"}, {"side": "sell", "qty": "1"}]


def _feat(**over):
    kw = dict(tte_s=400, held_leg="YES", held_leg_price="0.63",
              required_complement_price="0.34", book=BOOK, prints=PRINTS)
    kw.update(over)
    return H.features(**kw)


# ------------------------------------------------------------------ hazard

def test_an_uncalibrated_cell_returns_none_not_a_half():
    est = H.HazardEstimator().estimate(_feat())
    assert est["p"] is None
    assert est["calibrated"] is False
    assert "UNCALIBRATED" in est["why"]


def test_an_uncalibrated_hazard_never_justifies_waiting():
    est = H.HazardEstimator().estimate(_feat())
    assert H.admits_waiting(est, threshold=0.0)["wait"] is False


def test_the_estimator_speaks_only_past_the_evidence_floor():
    e = H.HazardEstimator()
    f = _feat()
    for i in range(H.MIN_CELL - 1):
        e.observe(f, resolved=True, filled_acceptably=(i % 2 == 0))
    assert e.estimate(f)["calibrated"] is False
    e.observe(f, resolved=True, filled_acceptably=True)
    got = e.estimate(f)
    assert got["calibrated"] is True and got["p"] is not None


def test_an_unresolved_state_is_counted_nowhere():
    """Counting it as a failure would make the estimate depend on when we
    happened to look."""
    e = H.HazardEstimator()
    f = _feat()
    for _ in range(100):
        e.observe(f, resolved=False, filled_acceptably=False)
    assert e.estimate(f)["n"] == 0
    assert e.estimate(f)["calibrated"] is False


def test_the_required_gap_drives_the_cell_not_the_raw_price():
    near = _feat(required_complement_price="0.34")     # 1 tick from the bid
    far = _feat(required_complement_price="0.20")      # 15 ticks away
    assert H.cell_of(near) != H.cell_of(far)


def test_one_sided_and_two_sided_flow_land_in_different_cells():
    two = _feat()
    one = _feat(prints=[{"side": "buy", "qty": "1"}] * 6)
    assert H.cell_of(two) != H.cell_of(one)


def test_the_estimator_survives_a_restart(tmp_path):
    e = H.HazardEstimator()
    f = _feat()
    for _ in range(H.MIN_CELL):
        e.observe(f, resolved=True, filled_acceptably=True)
    p = str(tmp_path / "hz.json")
    e.save(p)
    back = H.HazardEstimator.load(p)
    assert back.estimate(f)["p"] == 1.0


def test_a_missing_state_file_loads_empty_rather_than_raising(tmp_path):
    e = H.HazardEstimator.load(str(tmp_path / "nope.json"))
    assert e.estimate(_feat())["calibrated"] is False


# ------------------------------------------------------------------ policy

def test_btc15m_enters_across_the_whole_900_to_300_region():
    """The brief rules out mechanically waiting for the last five minutes."""
    pol = P.COHORTS["BTC15m"]
    for tte in (899, 800, 700, 600, 500, 400, 301):
        assert pol.mode(tte)["mode"] == P.ENTRY, tte
    assert pol.may_open(880) is True


def test_btc5m_is_stricter_than_btc15m_rather_than_just_faster():
    a, b = P.COHORTS["BTC5m"].thresholds, P.COHORTS["BTC15m"].thresholds
    assert a.min_alternation > b.min_alternation
    assert a.max_required_gap_ticks < b.max_required_gap_ticks
    assert a.max_largest_print_share < b.max_largest_print_share
    assert P.COHORTS["BTC5m"].max_residual < P.COHORTS["BTC15m"].max_residual
    assert (P.COHORTS["BTC5m"].toxicity_horizon_s
            < P.COHORTS["BTC15m"].toxicity_horizon_s)


def test_the_15m_band_is_the_preserved_v2_correction():
    assert P.COHORTS["BTC15m"].quote_band_s == (0.0, 900.0)
    assert P.COHORTS["BTC5m"].quote_band_s == (0.0, 300.0)


def test_entry_stops_before_withdrawal_in_both_cohorts():
    for pol in P.COHORTS.values():
        assert (pol.entry_until_tte_s > pol.mixed_until_tte_s
                > pol.withdraw_below_tte_s)
        assert pol.mode(pol.withdraw_below_tte_s - 1)["mode"] == P.WITHDRAW
        assert pol.may_open(pol.withdraw_below_tte_s - 1) is False


def test_an_unreadable_tte_withdraws_rather_than_quoting():
    for pol in P.COHORTS.values():
        assert pol.mode(None)["mode"] == P.WITHDRAW
        assert pol.may_rescue(None) is False


def test_an_unknown_cohort_inherits_nothing():
    """The exact defect TTE_PROVENANCE_1 records: a 5m number inherited by a
    15m instrument because nobody refused."""
    assert P.policy_for("ETH5m") is None
    assert P.policy_for(None) is None


# -------------------------------------------------------------- scoreboard

def _pair(cost, comp):
    return V.pair_economics(held_qty="0.01", held_avg_cost=cost,
                            complement_qty="0.01", complement_price=comp)


def test_a_pair_above_the_ceiling_is_counted_as_a_defect():
    sb = S.Scoreboard(cohort="BTC15m")
    sb.pair(_pair("0.63", "0.50"), both_maker=True, ceiling="0.35",
            complement_price="0.50")
    r = sb.report()
    assert r["PAIRS_ABOVE_CEILING_defect_counter"] == 1
    assert r["pairs_at_or_below_one"] == 0
    assert r["fee_positive_pairs"] == 0


def test_accrued_rebate_is_excluded_from_the_net():
    sb = S.Scoreboard(cohort="BTC5m")
    sb.pair(_pair("0.40", "0.55"), both_maker=True)
    r = sb.report()
    assert D(r["net_cents_excl_rebate_and_residual"]) == (
        D(r["trading_pnl_before_fees"]) - D(r["fees_paid"]))
    assert r["rebate_credited_venue_record"] is None


def test_an_accrual_can_never_reach_the_credited_field():
    sb = S.Scoreboard(cohort="BTC5m")
    sb.pair(_pair("0.40", "0.55"), both_maker=True)
    assert D(sb.report()["rebate_accrued_NOT_INCOME"]) >= 0
    assert sb.report()["rebate_credited_venue_record"] is None
    sb.credited("0.05")            # only a venue record gets here
    assert sb.report()["rebate_credited_venue_record"] == "0.05"


def test_no_capital_used_reports_none_not_zero():
    sb = S.Scoreboard(cohort="BTC5m")
    assert sb.report()["PRIMARY_net_cents_per_capital_dollar_minute"] is None


def test_complement_opportunities_and_acceptable_ones_are_counted_apart():
    sb = S.Scoreboard(cohort="BTC5m")
    for ok in (True, False, False, False):
        sb.complement_seen(acceptable=ok)
    r = sb.report()
    assert r["complement_opportunities"] == 4
    assert r["acceptable_complement_opportunities"] == 1
    assert r["acceptable_share_of_complement_opportunities"] == 0.25


def test_entry_refusals_are_attributed_to_the_gate_that_refused():
    sb = S.Scoreboard(cohort="BTC5m")
    sb.entry({"admitted": False, "failed_gates": ["FLOW_REVERSIBLE"]})
    sb.entry({"admitted": False, "failed_gates": ["FLOW_REVERSIBLE"]})
    sb.entry({"admitted": True})
    r = sb.report()
    assert r["entry_refusals_by_gate"]["FLOW_REVERSIBLE"] == 2
    assert r["entry_admission_rate"] == 1 / 3


def test_time_to_flat_is_a_median_not_a_mean():
    sb = S.Scoreboard(cohort="BTC15m")
    for v in (10, 10, 10, 800):
        sb.flat_after(v)
    assert sb.report()["median_time_first_fill_to_flat_s"] == 10.0


def test_the_stage_is_shadow_by_default_and_travels_on_the_report():
    sb = S.Scoreboard(cohort="BTC5m")
    r = sb.report()
    assert r["stage"] == "shadow"
    assert r["accounting_ladder"][0] == "historical"
    assert "realized" == r["accounting_ladder"][-1]
