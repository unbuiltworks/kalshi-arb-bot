"""The preview session must be unable to trade, and unable to overclaim.

Two failure classes motivate every test here. A session that can reach
`/v1/orders` puts real money on the venue while calling itself a preflight.
And a session that reports a constraint as satisfied when the venue never
actually refused the bad shape hands the probe a green gate built on
nothing -- which is worse than a red one, because it is trusted.
"""
from __future__ import annotations

import json

import pytest

from src import l5_preview_session as P
from src import pmus_auth as Q1

SLUG = "cpc-btc-updown-15m-2026-09-24-1315z"
INTENT = {"client_order_id": "coid-1", "side": "UP", "price": "0.47",
          "qty": "1.00", "mode": "MAKER"}


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv(Q1.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(Q1.SECRET_ENV, "secret-bbbbbbbbbbbb")


class Venue:
    """Accepts the preview unless the shape is one it should refuse."""

    def __init__(self, *, tick="0.01", min_qty="0.01", outcome_text="Up",
                 enforce=True):
        self.tick, self.min_qty = tick, min_qty
        self.outcome_text, self.enforce = outcome_text, enforce
        self.seen = []

    def post(self, path, body, base):
        from decimal import Decimal as D
        req = body["request"]
        self.seen.append(req)
        px = D(req["price"]["value"])
        qty = D(str(req["quantity"]))
        if self.enforce and px % D(self.tick) != 0:
            raise RuntimeError("400 Bad request - invalid order preview")
        if self.enforce and qty < D(self.min_qty):
            raise RuntimeError("400 Bad request - invalid order preview")
        return {"order": {"marketSlug": req["marketSlug"],
                          "outcomeSide": req["outcomeSide"],
                          "price": req["price"], "quantity": req["quantity"],
                          "state": "ORDER_STATE_PENDING_NEW",
                          "marketMetadata": {"slug": req["marketSlug"],
                                             "outcome": self.outcome_text}}}


def tx(venue=None):
    v = venue or Venue()
    return P.PreviewOnlyTransport(http_post=v.post,
                                  http_get=lambda p, b: {}), v


# ---------------------------------------------- structurally cannot trade

@pytest.mark.parametrize("path", [
    "/v1/orders", "/v1/orders/batched", "/v1/orders/open/cancel",
    "/v1/orders/batched/cancel", "/v1/orders/batched/modify",
    "/v1/order/close-position", "/v1/order/abc123/cancel",
    "/v1/order/abc123/modify",
])
def test_every_other_post_path_is_refused(path):
    t, _ = tx()
    with pytest.raises(P.MutationRefused):
        t.request("POST", path, {"marketSlug": SLUG})


def test_the_documented_mutating_paths_are_all_covered():
    """If the venue adds a money-moving path, the allowlist still refuses it,
    but the explicit list should not silently fall behind either."""
    t, _ = tx()
    for path in P.MUTATING_PATHS:
        with pytest.raises(P.MutationRefused):
            t.request("POST", path, {})


def test_delete_and_put_are_refused():
    t, _ = tx()
    for m in ("DELETE", "PUT", "PATCH"):
        with pytest.raises(P.MutationRefused):
            t.request(m, P.PREVIEW_PATH, {})


def test_the_module_exposes_no_submit_or_cancel():
    for name in ("submit", "cancel", "place_limit_buy", "create_order",
                 "cancel_all", "close_position"):
        assert not hasattr(P, name), "%s must not exist here" % name


def test_no_mutating_order_path_appears_in_executable_code():
    """Prose may name the paths it refuses; executable code may not build one."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(P))
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            d = ast.get_docstring(n, clean=False)
            if d:
                docs.add(d)
    lits = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value not in docs]
    # the only order path that may appear as a built string is the preview,
    # plus the refusal allowlist which exists to be rejected
    for s in lits:
        if s.startswith("/v1/order") and s != P.PREVIEW_PATH:
            assert s in P.MUTATING_PATHS, "unexpected order path: %r" % s


# --------------------------------------------- previews the REAL order

def test_the_preview_is_the_order_the_executor_proposes(creds):
    t, v = tx()
    P.preview(t, INTENT, SLUG)
    req = v.seen[0]
    assert req["marketSlug"] == SLUG
    assert req["price"] == {"value": "0.47", "currency": "USD"}
    assert req["quantity"] == 1.0
    assert req["outcomeSide"] == "OUTCOME_SIDE_YES"      # UP via contract
    assert req["participateDontInitiate"] is True         # MAKER
    assert req["type"] == "ORDER_TYPE_LIMIT"


def test_a_taker_intent_is_not_previewed_as_maker(creds):
    t, v = tx()
    P.preview(t, dict(INTENT, mode="TAKER"), SLUG)
    assert v.seen[0]["participateDontInitiate"] is False


def test_the_market_slug_comes_from_the_caller_not_from_parsing(creds):
    """Contract semantics must never be derived from slug text."""
    t, v = tx()
    P.preview(t, INTENT, "cpc-btc-updown-15m-2026-09-24-9999z")
    assert v.seen[0]["marketSlug"] == "cpc-btc-updown-15m-2026-09-24-9999z"


def test_down_maps_to_no(creds):
    assert P.outcome_side_for("DOWN") == "OUTCOME_SIDE_NO"
    assert P.outcome_side_for("UP") == "OUTCOME_SIDE_YES"


def test_an_unmapped_side_raises_rather_than_guessing():
    with pytest.raises(ValueError):
        P.outcome_side_for("SIDEWAYS")


# ------------------------------------------------------------ provenance

def test_no_credential_reaches_the_record(creds):
    t, _ = tx()
    rec = P.preview(t, INTENT, SLUG)
    blob = json.dumps(rec, default=str)
    assert "key-aaaaaaaaaaaa" not in blob
    assert "secret-bbbbbbbbbbbb" not in blob
    assert rec["api_key_fingerprint"] and len(rec["api_key_fingerprint"]) == 12


def test_a_credential_inside_an_error_is_redacted(creds):
    def boom(path, body, base):
        raise RuntimeError("401 for key-aaaaaaaaaaaa")
    t = P.PreviewOnlyTransport(http_post=boom)
    rec = P.preview(t, INTENT, SLUG)
    assert rec["accepted"] is False
    assert "key-aaaaaaaaaaaa" not in json.dumps(rec, default=str)


def test_the_record_carries_request_and_response_and_says_it_sent_nothing(creds):
    t, _ = tx()
    rec = P.preview(t, INTENT, SLUG)
    assert rec["request"]["request"]["marketSlug"] == SLUG
    assert rec["echo"]["outcomeSide"] == "OUTCOME_SIDE_YES"
    assert "no order was submitted" in rec["LABEL"]
    assert len(rec["request_sha256_16"]) == 16


# ------------------------------------- constraints: proven, or UNPROVEN

def test_a_venue_that_enforces_both_constraints_proves_them(creds):
    t, _ = tx(Venue(enforce=True))
    pr = P.constraint_probe(t, INTENT, SLUG, tick="0.01", min_qty="0.01")
    assert pr["tick_enforced"] and pr["min_qty_enforced"] and pr["proven"]


def test_a_venue_that_accepts_an_off_tick_price_leaves_tick_UNPROVEN(creds):
    """The dangerous case: everything returns 200, so a naive reading calls
    the constraint satisfied when the venue never refused anything."""
    t, _ = tx(Venue(enforce=False))
    pr = P.constraint_probe(t, INTENT, SLUG, tick="0.01", min_qty="0.01")
    assert pr["tick_enforced"] is False
    assert pr["min_qty_enforced"] is False
    assert pr["proven"] is False
    assert "never 'satisfied'" in pr["LABEL"]


def test_the_probe_actually_sends_an_off_tick_and_a_sub_minimum_shape(creds):
    t, v = tx(Venue(enforce=False))
    P.constraint_probe(t, INTENT, SLUG, tick="0.01", min_qty="0.01")
    from decimal import Decimal as D
    prices = [D(r["price"]["value"]) for r in v.seen]
    qtys = [D(str(r["quantity"])) for r in v.seen]
    assert any(p % D("0.01") != 0 for p in prices), "no off-tick shape sent"
    assert any(q < D("0.01") for q in qtys), "no sub-minimum shape sent"


def test_a_failing_valid_shape_cannot_prove_a_constraint(creds):
    """If the real order itself is refused, a refusal of the bad shapes
    proves nothing about tick or quantity."""
    def refuse_all(path, body, base):
        raise RuntimeError("400 Bad request")
    t = P.PreviewOnlyTransport(http_post=refuse_all)
    pr = P.constraint_probe(t, INTENT, SLUG, tick="0.01", min_qty="0.01")
    assert pr["proven"] is False


# ------------------------------------- the market_readable replacement

def ok_probe():
    return {"tick_enforced": True, "min_qty_enforced": True, "proven": True,
            "valid": {"echo": {"market_outcome_text": "Up"}}}


def test_all_five_legs_must_hold(creds):
    ev = P.contract_evidence(probe=ok_probe(), market_slug=SLUG,
                             feed_slug=SLUG, book_state_open=True,
                             book_two_sided=True)
    assert ev["contract_proven"] is True and ev["blocking_failures"] == []


def test_a_slug_that_is_not_the_feeds_market_fails_identity(creds):
    ev = P.contract_evidence(probe=ok_probe(), market_slug=SLUG,
                             feed_slug="cpc-btc-updown-15m-2026-09-24-1330z",
                             book_state_open=True, book_two_sided=True)
    assert "exact_market_identity" in ev["blocking_failures"]


def test_an_unproven_constraint_blocks(creds):
    ev = P.contract_evidence(
        probe={"tick_enforced": False, "min_qty_enforced": True},
        market_slug=SLUG, feed_slug=SLUG, book_state_open=True,
        book_two_sided=True)
    assert "tick_enforced_by_venue" in ev["blocking_failures"]
    assert ev["contract_proven"] is False


def test_tradability_comes_from_the_BOOK_not_the_preview(creds):
    """A preview's `state` is the ORDER's state, not the market's. A closed
    market must fail this leg even when the preview was accepted."""
    ev = P.contract_evidence(probe=ok_probe(), market_slug=SLUG,
                             feed_slug=SLUG, book_state_open=False,
                             book_two_sided=True)
    assert "market_open_tradable" in ev["blocking_failures"]
    assert ev["legs"]["market_open_tradable"]["source"].endswith(
        "NOT the preview")


def test_the_outcome_leg_names_the_contract_not_the_preview(creds):
    ev = P.contract_evidence(probe=ok_probe(), market_slug=SLUG,
                             feed_slug=SLUG, book_state_open=True,
                             book_two_sided=True)
    leg = ev["legs"]["outcome_mapping_up_down"]
    assert "contract definition" in leg["source"]
    assert "does not prove YES is Up" in leg["detail"]["note"]


def test_the_static_contract_carries_its_provenance():
    for p in P.STATIC_CONTRACT["provenance"]:
        assert p["url"].startswith("https://docs.polymarket.us")
        assert p["quote"]
    assert "documentation, not a live read" in P.STATIC_CONTRACT["LABEL"]


def test_documented_family_defaults_are_never_used_as_order_values(creds):
    """They are recorded for comparison only. The order's price and quantity
    come from the executor's intent, never from the documentation."""
    t, v = tx()
    P.preview(t, INTENT, SLUG)
    assert v.seen[0]["price"]["value"] == "0.47"
    assert "not a per-instrument read" in P.STATIC_FAMILY_ATTRS["LABEL"]


# ------------------- the observer must never gain this POST path by accident

def test_the_preview_module_is_not_in_the_recorder_image():
    """The 24/7 observer's guarantee is that its image contains no path that
    can POST. This module contains one. It must therefore never be COPY'd
    into that image, and never appear in the service's watchPatterns."""
    from pathlib import Path
    df = Path("deploy/pmus_ws_recorder/Dockerfile").read_text()
    assert "l5_preview_session" not in df, (
        "the preview session was added to the recorder image; that removes "
        "the observer's structural no-order-path guarantee")


def test_the_recorder_transport_still_cannot_post_at_all():
    """Unchanged property, asserted from here so that relaxing it to make a
    preview work inside the observer breaks a test."""
    t = Q1.ReadOnlyTransport()
    for m in ("POST", "DELETE", "PUT", "PATCH"):
        with pytest.raises(PermissionError):
            t.request(m, P.PREVIEW_PATH, {})
