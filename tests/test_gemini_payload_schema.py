"""The exact bytes that get signed, checked field by field before signing.

Canary #1 was rejected with HTTP 400 and three hypotheses for it are now
disproven by measurement: the size was valid, the price was valid, and the
fields did reach the venue. What is left is the one thing in that request the
documented OrderRequest schema does not admit -- a clientOrderId, which lives
in the RESPONSE schema only. These tests exist so that a single undocumented
property cannot reach a signer again.
"""
import json

import pytest

from src.gemini_execution import payload as P, spec


def _order(**over):
    o = P.build_order(symbol="GEMI-BTC05M2609180200-UP", price="0.69",
                      quantity="0.01")
    o.update(over)
    return o


# ------------------------------------------------------------- the payload

def test_the_payload_is_exactly_order_plus_auth():
    body = P.signing_payload(_order(), nonce=1789693639)
    assert set(body) == set(spec.AUTH_PAYLOAD_FIELDS) | set(
        P.ALLOWED_ORDER_FIELDS)
    assert body["request"] == spec.PLACE_PATH
    assert type(body["nonce"]) is int


def test_the_payload_carries_no_client_order_id():
    assert "clientOrderId" not in P.signing_payload(_order())


def test_the_rendered_payload_contains_nothing_secret():
    """Scanned over the PAYLOAD, not over the prose around it: the note
    explaining that no signature is present contains the word "signature",
    and a blanket substring scan flags exactly the sentence that says the
    right thing."""
    out = P.sanitized_decoded(_order(), nonce=1)
    body = out["decoded_payload"]
    blob = json.dumps(body).lower()
    for secret in ("apikey", "api_key", "signature", "secret", "x-gemini"):
        assert secret not in blob, secret
    assert set(body) == set(spec.AUTH_PAYLOAD_FIELDS) | set(
        P.ALLOWED_ORDER_FIELDS)
    # and the serialised form the module reports is the same object
    assert json.loads(out["decoded_payload_json"]) == body


def test_every_field_has_its_documented_value():
    o = _order()
    assert o["orderType"] == "limit"
    assert o["side"] == "buy"
    assert o["outcome"] == "yes"
    assert o["timeInForce"] == "good-til-cancel"
    assert o["makerOrCancel"] is True
    assert isinstance(o["quantity"], str) and isinstance(o["price"], str)


def test_the_allowed_set_is_a_subset_of_the_documented_schema():
    assert set(P.ALLOWED_ORDER_FIELDS) <= set(spec.ORDER_REQUEST_FIELDS)


def test_stop_price_is_documented_but_still_not_sent():
    """Containment in the schema is not permission: stopPrice belongs to
    stop-limit orders and this canary is a limit order."""
    assert "stopPrice" in spec.ORDER_REQUEST_FIELDS
    assert "stopPrice" not in P.ALLOWED_ORDER_FIELDS
    assert P.validate_structure(_order())["not_sent_though_documented"] == [
        "stopPrice"]


# ----------------------------------------------------------- fail closed

@pytest.mark.parametrize("field", sorted(P.FORBIDDEN))
def test_each_forbidden_field_is_refused_by_name(field):
    with pytest.raises(P.PayloadViolation) as e:
        P.validate_structure(_order(**{field: "whatever"}))
    assert field in str(e.value)


def test_an_unknown_field_nobody_anticipated_is_still_refused():
    with pytest.raises(P.PayloadViolation) as e:
        P.validate_structure(_order(someNewThing=1))
    assert "someNewThing" in str(e.value)


@pytest.mark.parametrize("field", list(P.ALLOWED_ORDER_FIELDS))
def test_a_missing_field_is_refused(field):
    o = _order()
    o.pop(field)
    with pytest.raises(P.PayloadViolation) as e:
        P.validate_structure(o)
    assert field in str(e.value)


@pytest.mark.parametrize("bad", [1, "true", "True", 1.0, None, "yes"])
def test_maker_or_cancel_must_be_a_real_boolean_true(bad):
    """True is an int in Python and "true" is truthy, so isinstance and
    truthiness both accept values the venue may not read as post-only."""
    with pytest.raises(P.PayloadViolation) as e:
        P.assert_types(_order(makerOrCancel=bad))
    assert "makerOrCancel" in str(e.value)


def test_maker_or_cancel_false_is_refused_too():
    with pytest.raises(P.PayloadViolation):
        P.assert_types(_order(makerOrCancel=False))


@pytest.mark.parametrize("bad", [0.69, 69, None, "", "  ", "abc"])
def test_price_must_be_a_decimal_string(bad):
    with pytest.raises(P.PayloadViolation) as e:
        P.assert_types(_order(price=bad))
    assert "price" in str(e.value)


@pytest.mark.parametrize("bad", [0.01, 1, None, "", "one"])
def test_quantity_must_be_a_decimal_string(bad):
    with pytest.raises(P.PayloadViolation) as e:
        P.assert_types(_order(quantity=bad))
    assert "quantity" in str(e.value)


@pytest.mark.parametrize("field,bad", [
    ("orderType", "stop-limit"),          # documented, but not this canary
    ("orderType", "market"),
    ("timeInForce", "immediate-or-cancel"),
    ("timeInForce", "fill-or-kill"),      # neither one means post-only
    ("timeInForce", "MOC"),               # the WebSocket spelling
    ("outcome", "YES"),                   # the WebSocket casing
    ("side", "BUY"),
    ("symbol", ""),
])
def test_a_wrong_enum_value_is_refused(field, bad):
    with pytest.raises(P.PayloadViolation) as e:
        P.assert_types(_order(**{field: bad}))
    assert field in str(e.value)


def test_a_non_integer_nonce_is_refused():
    with pytest.raises(P.PayloadViolation):
        P.signing_payload(_order(), nonce="1789693639")


def test_signing_validates_before_it_returns_anything():
    """The check sits BETWEEN construction and signing, not beside it."""
    with pytest.raises(P.PayloadViolation):
        P.signing_payload(_order(clientOrderId="canary-1"))
    with pytest.raises(P.PayloadViolation):
        P.sanitized_decoded(_order(makerOrCancel="true"))


def test_the_guard_notices_if_the_allowed_set_drifts_off_schema(monkeypatch):
    """A guard that cannot fail is not a guard."""
    monkeypatch.setattr(P, "ALLOWED_ORDER_FIELDS",
                        P.ALLOWED_ORDER_FIELDS + ("inventedField",))
    with pytest.raises(P.PayloadViolation) as e:
        P.validate_structure(_order(inventedField="x"))
    assert "drifted off the documented schema" in str(e.value)


def test_the_default_nonce_is_a_real_epoch_not_a_placeholder():
    """Rendering shows what would actually be signed. A 0 could never be
    sent, so showing one misrepresents the payload it claims to be exact
    about."""
    body = P.signing_payload(_order())
    assert type(body["nonce"]) is int
    assert body["nonce"] > 1_700_000_000        # a plausible epoch-seconds


def test_rendering_does_not_advance_the_signers_nonce():
    """A read must not have a side effect on the thing that signs."""
    import src.gemini_prediction_auth as A
    before = A._last_nonce
    P.sanitized_decoded(_order())
    assert A._last_nonce == before
