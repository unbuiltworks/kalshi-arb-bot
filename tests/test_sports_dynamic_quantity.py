from decimal import Decimal

from src.sports_capacity_math import CapacityDirection, CapacityLeg, evaluate_direction
from src.sports_dynamic_quantity import QuantitySafetyGates, choose_quantity

D = Decimal
ZERO_FEE = lambda price, quantity: D("0")


def _capacity(depth: str = "100"):
    direction = CapacityDirection(
        "A_YES_B_NO",
        "key",
        (
            CapacityLeg("a", "a-m", "YES", D("0.40"), D(depth), ZERO_FEE),
            CapacityLeg("b", "b-m", "NO", D("0.50"), D(depth), ZERO_FEE),
        ),
    )
    return evaluate_direction(
        direction,
        available_by_venue={"a": D("1000"), "b": D("1000")},
    )


def _gates(**overrides):
    values = dict(
        counterpart_certified=True,
        rule_fingerprint_certified=True,
        participation_certified=True,
        quote_fresh=True,
        quote_skew_ok=True,
        persistence_ok=True,
        final_revalidation_ok=True,
        capital_snapshot_fresh=True,
        no_unresolved_exposure=True,
    )
    values.update(overrides)
    return QuantitySafetyGates(**values)


def test_default_authorization_keeps_q1_even_when_q100_capacity_exists():
    decision = choose_quantity(_capacity(), gates=_gates())
    assert decision.quantity == 1
    assert decision.authorized_max_quantity == 1
    assert decision.available_capacity_tier == 100


def test_explicit_q25_authorization_can_select_q25_but_not_q50():
    decision = choose_quantity(_capacity(), gates=_gates(), authorized_max_quantity=25)
    assert decision.quantity == 25
    assert decision.status == "PROPOSED"


def test_any_safety_failure_blocks_all_quantity():
    decision = choose_quantity(
        _capacity(),
        gates=_gates(final_revalidation_ok=False),
        authorized_max_quantity=100,
    )
    assert decision.quantity == 0
    assert decision.status == "BLOCKED"
    assert "final_revalidation_ok" in decision.reason


def test_depth_caps_authorized_quantity_to_available_tier():
    decision = choose_quantity(_capacity("10"), gates=_gates(), authorized_max_quantity=100)
    assert decision.quantity == 10
    assert decision.available_capacity_tier == 10


def test_max_net_cost_can_block_large_tier_and_fall_back_smaller():
    decision = choose_quantity(
        _capacity(),
        gates=_gates(),
        authorized_max_quantity=100,
        max_net_cost=D("5"),
    )
    # Each contract costs $0.90 in this zero-fee fixture, so q10 costs $9 and
    # must be rejected. q5 costs $4.50 and is the largest allowed tier <= $5.
    assert decision.quantity == 5
