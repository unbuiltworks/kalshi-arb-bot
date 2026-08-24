from decimal import Decimal

from src.four_venue_threshold_middle import ThresholdObservedQuote, find_threshold_middles

D = Decimal
BASE = "baseball|mlb|a,b|game_total|full_contest|contest|contest|runs"


def q(venue: str, line: str, yes: str, no: str, *, comparator: str = "GT", source_side: str = "CONTRACT", rules: bool = False):
    return ThresholdObservedQuote(
        venue=venue,
        market_id=f"{venue}:{line}#YES" if not venue.startswith("sportsbook:") else f"fd:{line}",
        settlement_key=f"{BASE}|{comparator}|{line}",
        market_family="game_total",
        scope="full_contest",
        subject_type="contest",
        subject="contest",
        stat="runs",
        comparator=comparator,
        line=D(line),
        yes_cost=D(yes),
        no_cost=D(no),
        yes_depth=D("1"),
        no_depth=D("1"),
        scheduled_start_ms=1_800_000_000_000,
        received_ms=1_800_000_010_000,
        source_side=source_side,
        rules_complete=rules,
        rule_fingerprint="same-rule" if rules else "INCOMPLETE",
    )


def test_gt_locked_middle_uses_low_yes_high_no_and_floor_only_for_arb():
    rows = [
        q("fanduel" if False else "sportsbook:fanduel", "7.5", "0.44", "0.60"),
        q("polymarket", "8.5", "0.50", "0.50"),
    ]
    out = find_threshold_middles(rows)
    assert len(out) == 1
    c = out[0]
    assert c.classification == "SHADOW_LOCKED_MIDDLE_3C_PLUS"
    assert c.low_leg.semantic_action == "YES"
    assert c.high_leg.semantic_action == "NO"
    assert c.total_cost == D("0.94")
    assert c.floor_profit == D("0.06")
    assert c.middle_profit == D("1.06")
    assert c.middle_region == "(low, high]"
    assert not c.autoexecution_eligible
    assert "SPORTSBOOK_MANUAL_ONLY" in c.blockers
    assert "SETTLEMENT_RULE_AUDIT_REQUIRED" in c.blockers


def test_middle_ev_shape_is_not_mislabeled_arbitrage_when_outside_loses():
    rows = [q("gemini", "7.5", "0.57", "0.45"), q("kalshi", "8.5", "0.54", "0.51")]
    out = find_threshold_middles(rows)
    assert len(out) == 1
    c = out[0]
    assert c.classification == "SPORTS_MIDDLE_EV_SHAPE_NOT_ARBITRAGE"
    assert c.floor_profit == D("-0.08")
    assert c.middle_profit == D("0.92")


def test_lt_middle_reverses_semantic_actions():
    rows = [
        q("gemini", "7.5", "0.55", "0.43", comparator="LT"),
        q("polymarket", "8.5", "0.45", "0.57", comparator="LT"),
    ]
    out = find_threshold_middles(rows)
    assert len(out) == 1
    c = out[0]
    assert c.low_leg.semantic_action == "NO"
    assert c.high_leg.semantic_action == "YES"
    assert c.total_cost == D("0.88")
    assert c.floor_profit == D("0.12")
    assert c.middle_region == "[low, high)"


def test_source_side_no_maps_semantic_action_to_underlying_action():
    rows = [
        q("gemini", "7.5", "0.44", "0.60", source_side="NO"),
        q("kalshi", "8.5", "0.50", "0.50"),
    ]
    c = find_threshold_middles(rows)[0]
    assert c.low_leg.semantic_action == "YES"
    assert c.low_leg.actual_action == "NO"


def test_strict_comparator_and_start_skew_gates():
    left = q("gemini", "7.5", "0.40", "0.60", comparator="GT")
    right = q("polymarket", "8.5", "0.40", "0.60", comparator="GE")
    assert find_threshold_middles([left, right]) == []

    far = ThresholdObservedQuote(**{**left.__dict__, "venue": "kalshi", "line": D("8.5"), "settlement_key": f"{BASE}|GT|8.5", "scheduled_start_ms": left.scheduled_start_ms + 301_000})
    assert find_threshold_middles([left, far]) == []


def test_rule_certified_prediction_pair_can_be_marked_autoexecution_eligible_by_math_layer():
    rows = [q("gemini", "7.5", "0.42", "0.60", rules=True), q("kalshi", "8.5", "0.50", "0.50", rules=True)]
    c = find_threshold_middles(rows)[0]
    assert c.rule_certified
    assert c.autoexecution_eligible
    assert c.floor_profit == D("0.08")
