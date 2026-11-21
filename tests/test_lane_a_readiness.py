"""Lane A readiness: separate counts, honest sizing, nothing relaxed."""
import pytest

from analysis.l5.lanes import lane_a_readiness as R


# ------------------------------------------------- the 0.96 / 0.98 split

def test_le_096_and_le_098_are_separate_counts():
    """The real observed evidence: three completions at 0.9800. Zero meet the
    0.96 discipline; all three meet the 0.98 venue target."""
    c = R.pair_cost_counts(["0.9800", "0.9800", "0.9800"])
    assert c["n_completions"] == 3
    assert c["le_096_completions"] == 0
    assert c["le_098_completions"] == 3
    assert c["meets_096_discipline"] is False
    assert c["meets_098_venue_target"] is True


def test_the_legacy_compatibility_field_is_never_read():
    """`spring_le_096_completions` counts <=0.98. A row that carries it saying
    3 must still report ZERO <=0.96 completions when the costs are 0.98."""
    row = {"slug": "m", "spring_category": "lmx",
           "spring_matched_slots": 3,
           "spring_pair_costs": ["0.9800", "0.9800", "0.9800"],
           # the trap: the legacy field claims 3, and it means <=0.98
           "spring_le_096_completions": 3}
    out = R.market_readiness(row)
    assert out["le_096_completions"] == 0, "legacy field leaked into the count"
    assert out["le_098_completions"] == 3
    assert out["legacy_field_ignored"] == "spring_le_096_completions"


def test_a_genuine_sub_096_completion_is_counted_as_one():
    c = R.pair_cost_counts(["0.9550", "0.9800", "0.9600", "0.9900"])
    assert c["le_096_completions"] == 2      # 0.9550 and 0.9600 (inclusive)
    assert c["le_098_completions"] == 3
    assert c["above_098_completions"] == 1
    assert c["cheapest"] == "0.9550"


def test_the_boundary_is_inclusive_on_both_thresholds():
    assert R.pair_cost_counts(["0.96"])["le_096_completions"] == 1
    assert R.pair_cost_counts(["0.98"])["le_098_completions"] == 1
    assert R.pair_cost_counts(["0.9601"])["le_096_completions"] == 0


# ------------------------------------------------- the category bar

def test_the_category_bar_is_not_relaxed_and_names_what_is_missing():
    rows = [{"slug": "m1", "spring_category": "lmx", "spring_matched_slots": 3,
             "spring_pair_costs": ["0.98", "0.98", "0.98"]}]
    bar = R.category_bar(rows)
    assert bar["bar"]["relaxed"] is False
    assert bar["any_bar_met"] is False
    assert bar["closest"]["distinct_markets"] == 1
    assert bar["closest"]["markets_still_needed"] == 1
    assert bar["closest"]["completions_still_needed"] == 0


def test_a_second_distinct_market_in_the_same_category_meets_the_bar():
    rows = [{"slug": "m1", "spring_category": "lmx", "spring_matched_slots": 3,
             "spring_pair_costs": ["0.98"] * 3},
            {"slug": "m2", "spring_category": "lmx", "spring_matched_slots": 1,
             "spring_pair_costs": ["0.98"]}]
    bar = R.category_bar(rows)
    assert bar["any_bar_met"] is True
    assert bar["closest"]["distinct_markets"] == 2


def test_two_completions_in_ONE_market_do_not_meet_the_bar():
    """The distinct-market requirement is the whole point of the bar."""
    rows = [{"slug": "m1", "spring_category": "lmx", "spring_matched_slots": 9,
             "spring_pair_costs": ["0.98"] * 9}]
    assert R.category_bar(rows)["any_bar_met"] is False


def test_markets_in_different_categories_do_not_combine():
    rows = [{"slug": "m1", "spring_category": "lmx", "spring_matched_slots": 2,
             "spring_pair_costs": ["0.98", "0.98"]},
            {"slug": "m2", "spring_category": "mlb", "spring_matched_slots": 2,
             "spring_pair_costs": ["0.98", "0.98"]}]
    assert R.category_bar(rows)["any_bar_met"] is False


# ------------------------------------------------- the size ladder

def test_q1_size_one_is_a_behavioral_unit_not_a_contract_count():
    s = R.size_resolution("0.27", "0.01")
    steps = {x["step"]: x["value"] for x in s["ladder"]}
    assert steps["behavioral_unit"] == 1
    assert steps["executable_contracts"] == "0.02"
    assert steps["entry_notional_usd"] == "0.0054"


def test_the_005_cap_is_not_violated_by_a_027_quote():
    """The apparent inconsistency: $0.27 looks like it breaks a $0.05 cap. It
    does not -- $0.27 is a PRICE, and the executable spend is $0.0054."""
    s = R.size_resolution("0.27", "0.01")
    assert s["executable"] is True
    assert s["fits_per_market_cap"] is True
    assert s["entries_before_cap"] == 9


def test_every_live_sports_price_stays_inside_the_cap():
    for px in ("0.10", "0.24", "0.30", "0.41", "0.50", "0.80"):
        s = R.size_resolution(px, "0.01")
        assert s["executable"] is True, px
        assert s["fits_per_market_cap"] is True, px


def test_a_whole_contract_minimum_would_fail_closed_not_overspend():
    """If the venue ever required whole contracts, the path must HALT rather
    than silently place an order 5x the cap."""
    s = R.size_resolution("0.27", "1")
    assert s["executable"] is False
    assert "FRACTIONAL_QTY_UNSUPPORTED" in (s.get("halt") or "")


# ------------------------------------------------- the arming preflight

def _rows():
    return [{"slug": "atc-lmx-pue-tol-2026-09-04-tol", "spring_category": "lmx",
             "gate_status": "Q1_GATE_CLEARED", "two_sided_trades": True,
             "spring_matched_slots": 3, "spring_matched_fraction": 0.75,
             "spring_pair_costs": ["0.9800"] * 3,
             "spring_evidence_missing": [],
             "spring_category_evidence": {"completions_60m": 3,
                                          "distinct_markets_60m": 1},
             "spring_category_bar_met": False}]


def test_todays_state_blocks_on_exactly_three_things():
    v = R.arming_preflight(rows=_rows(), entry_price="0.27",
                           serving_artifact="6adcc4c43e67", budget_permits=True,
                           latch_set=True, dual_key_set=False,
                           settlement_confirmed_markets=34)
    assert v["READY_TO_ARM"] is False
    assert {b["gate"] for b in v["blockers"]} == {
        "CATEGORY_BAR", "SHUTDOWN_LATCH_CLEARED", "DUAL_KEY_ARMED"}


def test_when_the_second_market_lands_only_owner_decisions_remain():
    """The point of preparing this: evidence completing should not start
    another engineering cycle."""
    rows = _rows() + [{"slug": "atc-lmx-other", "spring_category": "lmx",
                       "spring_matched_slots": 1,
                       "spring_pair_costs": ["0.9800"]}]
    v = R.arming_preflight(rows=rows, entry_price="0.27",
                           serving_artifact="6adcc4c43e67", budget_permits=True,
                           latch_set=True, dual_key_set=False,
                           settlement_confirmed_markets=34)
    assert {b["gate"] for b in v["blockers"]} == {
        "SHUTDOWN_LATCH_CLEARED", "DUAL_KEY_ARMED"}
    assert [d["id"] for d in v["owner_decisions"]] == [
        "CLEAR_SHUTDOWN_LATCH", "SET_DUAL_KEY", "ACCEPT_098_OR_HOLD_FOR_096"]


def test_an_unsupplied_gate_is_undetermined_and_still_blocks():
    v = R.arming_preflight(rows=_rows())
    states = {g["gate"]: g["state"] for g in v["gates"]}
    assert states["BUDGET_PERMITS"] == "UNDETERMINED"
    assert states["DUAL_KEY_ARMED"] == "UNDETERMINED"
    assert v["READY_TO_ARM"] is False


def test_the_preflight_arms_nothing_and_relaxes_nothing():
    v = R.arming_preflight(rows=_rows(), entry_price="0.27")
    assert v["category_bar"]["bar"]["relaxed"] is False
    assert "PREPARED, NOT EXECUTED" in v["labels"]
    assert v["verdict"] != "ARMED"
