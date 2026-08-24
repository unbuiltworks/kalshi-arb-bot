from decimal import Decimal

import pytest

from src.gemini_kalshi_graduated_probe_guard import validate_graduated_candidate
from src.sports_dynamic_quantity import QuantitySafetyGates

D = Decimal


def _gates(rule_fingerprint_certified: bool) -> QuantitySafetyGates:
    return QuantitySafetyGates(
        counterpart_certified=True,
        rule_fingerprint_certified=rule_fingerprint_certified,
        participation_certified=True,
        quote_fresh=True,
        quote_skew_ok=True,
        persistence_ok=True,
        final_revalidation_ok=True,
        capital_snapshot_fresh=True,
        no_unresolved_exposure=True,
    )


def test_q5_cannot_be_authorized_with_incomplete_rule_fingerprint():
    with pytest.raises(ValueError):
        validate_graduated_candidate(
            quantity=D("5"),
            locked_profit=D("0.50"),
            quote_skew_ms=1,
            authorized_max_quantity=5,
            gates=_gates(False),
        )
