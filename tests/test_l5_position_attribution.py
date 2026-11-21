from decimal import Decimal
import pytest
from src.l5_position_attribution import AttributionError, attribute_fill, origin_from_client_order_id


def test_prefix_is_not_authentication():
    x = attribute_fill(client_order_id="b5m-x", venue_order_id="v1",
                       market="5m", quantity="2", unit_price="0.30",
                       authenticated_order_ids={})
    assert x["origin"] == "unknown"
    assert x["cost_basis_usd"] == Decimal("2")


def test_verified_5m_and_15m_separated():
    for coid, expected in [("b5m-x", "btc5m"), ("l5f-y", "btc15m")]:
        x = attribute_fill(client_order_id=coid, venue_order_id="v1",
                           market="market", quantity="2", unit_price="0.30",
                           authenticated_order_ids={"v1": {"client_order_id": coid, "market": "market"}})
        assert x["origin"] == expected
        assert x["cost_basis_usd"] == Decimal("0.60")


def test_wrong_market_or_order_is_unknown():
    x = attribute_fill(client_order_id="b5m-x", venue_order_id="v1",
                       market="different", quantity="1", unit_price="0.1",
                       authenticated_order_ids={"v1": {"client_order_id": "b5m-x", "market": "5m"}})
    assert x["origin"] == "unknown"


def test_unknown_manual_stays_in_account_risk():
    x = attribute_fill(client_order_id="manual", venue_order_id="v2",
                       market="market", quantity="5", unit_price="0.5",
                       authenticated_order_ids={})
    assert x["cost_basis_usd"] == Decimal("5")


@pytest.mark.parametrize("quantity,price", [("-1", "0.5"), ("1", "NaN"), ("1", "1.1")])
def test_bad_fills_fail_closed(quantity, price):
    with pytest.raises(AttributionError):
        attribute_fill(client_order_id="b5m-x", venue_order_id="v1",
                       market="5m", quantity=quantity, unit_price=price,
                       authenticated_order_ids={})
