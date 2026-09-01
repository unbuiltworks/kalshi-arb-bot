from decimal import Decimal as D

import pytest

from src import gemini_short_crypto_inventory_settlement_reconcile as r


def test_exact_order_identity_helpers_accept_gemini_shapes():
    row = {
        "clientOrderId": "b5-abc-yes",
        "eventOutcome": "YES",
        "filledQuantity": "1.00",
        "avgExecutionPrice": "0.44",
    }
    assert r._client_id(row) == "b5-abc-yes"
    assert r._outcome(row) == "yes"
    assert r._filled(row) == D("1.00")
    assert r._price(row) == "0.44"


def test_outcome_helper_accepts_settled_position_shape():
    assert r._outcome({"outcome": "NO"}) == "no"


def test_distinct_maker_ids_encode_expected_outcomes():
    yes_id, no_id = r.maker_client_ids("crypto-gem-btc5m-inventory-example")
    assert yes_id != no_id
    assert yes_id.endswith("-yes")
    assert no_id.endswith("-no")
    assert len(yes_id) <= 36
    assert len(no_id) <= 36


def test_terminal_rows_use_exact_distinct_ids_and_max_fill():
    yes_id, no_id = "b5-x-yes", "b5-x-no"
    rows = [
        {"clientOrderId": yes_id, "filledQuantity": "0.25", "eventOutcome": "YES"},
        {"clientOrderId": yes_id, "filledQuantity": "1", "eventOutcome": "YES"},
        {"clientOrderId": no_id, "filledQuantity": "0", "eventOutcome": "NO"},
        {"clientOrderId": "other", "filledQuantity": "1", "eventOutcome": "YES"},
    ]
    y, n = r._terminal_exact_rows(rows, yes_id, no_id)
    assert r._filled(y) == D("1")
    assert r._filled(n) == D("0")


def test_fractional_binary_settlement_is_valid():
    row = {
        "outcome": "YES",
        "positionQuantity": "0.25",
        "costBasis": "0.11",
        "payout": "0.25",
        "netProfit": "0.14",
    }
    assert r._settled_for_fill([row], outcome="yes", quantity=D("0.25")) is row


def test_settlement_quantity_mismatch_fails_closed():
    row = {
        "outcome": "NO",
        "positionQuantity": "0.50",
        "costBasis": "0.20",
        "payout": "0",
        "netProfit": "-0.20",
    }
    with pytest.raises(RuntimeError, match="settled quantity mismatch"):
        r._settled_for_fill([row], outcome="no", quantity=D("1"))
