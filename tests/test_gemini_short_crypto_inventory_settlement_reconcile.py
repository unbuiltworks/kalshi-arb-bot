from decimal import Decimal as D

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
