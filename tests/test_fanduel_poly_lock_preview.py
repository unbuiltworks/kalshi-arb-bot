from decimal import Decimal as D

from src.fanduel_poly_lock_preview import _actual_poly_side, _preview_payload


def test_normalized_yes_on_poly_yes_maps_to_underlying_yes() -> None:
    assert _actual_poly_side("slug#YES", "YES") == ("slug", "YES")


def test_normalized_no_on_poly_no_maps_to_underlying_yes() -> None:
    # A normalized identity sourced from underlying NO describes the opposite team.
    # Buying semantic NO therefore buys the underlying YES side.
    assert _actual_poly_side("slug#NO", "NO") == ("slug", "YES")


def test_preview_payload_is_q1_fok_and_not_post_only() -> None:
    row = _preview_payload("slug", "YES", D("0.40"))
    assert row["quantity"] == 1
    assert row["tif"] == "TIME_IN_FORCE_FILL_OR_KILL"
    assert row["participateDontInitiate"] is False
    assert row["intent"] == "ORDER_INTENT_BUY_LONG"
    assert row["price"]["value"] == "0.40"


def test_no_side_price_is_encoded_in_yes_space() -> None:
    row = _preview_payload("slug", "NO", D("0.35"))
    assert row["intent"] == "ORDER_INTENT_BUY_SHORT"
    assert row["price"]["value"] == "0.65"
