"""The one-shot preview service must be unable to trade, and must preview
the order that would actually be sent.

The failures these guard: an image that quietly contains a real order path;
a preview of a synthetic order that blesses a different body; a constraint
"proven" when the venue refused nothing; and a slug we invented being
treated as the executable market.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import l5_order_body as OB
from src import l5_preview_session as P
from src import pmus_auth as Q1
from scripts import pmus_preview_oneshot as R

SLUG = "cpc-btc-updown-15m-2026-09-24-1345z"
NOW = dt.datetime(2026, 9, 24, 13, 47, tzinfo=dt.timezone.utc)
INTENT = {"client_order_id": "c1", "side": "UP", "price": "0.47",
          "qty": "1.00", "mode": "MAKER"}


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv(Q1.KEY_ENV, "key-aaaaaaaaaaaa")
    monkeypatch.setenv(Q1.SECRET_ENV, "secret-bbbbbbbbbbbb")
    monkeypatch.delenv(Q1.LIVE_ENV, raising=False)


# ------------------------------------------------- the image has no order path

def copied_files():
    df = Path("deploy/pmus_preview/Dockerfile").read_text()
    return re.findall(r"^COPY (\S+)", df, re.M)


def test_the_image_excludes_every_module_that_can_send_an_order():
    banned = ("src/l5_pmus_transport.py", "src/pmus_q1_executor.py",
              "src/pmus_micro_executor.py")
    for b in banned:
        assert b not in copied_files(), "%s must not be in the preview image" % b


def test_no_file_in_the_image_can_actually_transmit_an_order():
    """NullTransport.submit exists but only raises; what must not exist is a
    path that can reach the venue's order endpoints."""
    for f in copied_files():
        p = Path(f)
        if not p.exists() or p.suffix != ".py":
            continue
        src = p.read_text()
        for marker in ('"POST", "/v1/orders"', "'POST', '/v1/orders'",
                       '"/v1/order/%s/cancel"'):
            assert marker not in src, "%s can transmit an order" % f


def test_the_runner_only_ever_posts_to_the_preview_path():
    src = Path("scripts/pmus_preview_oneshot.py").read_text()
    posts = re.findall(r'request\(\s*"POST",\s*([^,]+)', src)
    assert posts, "expected at least one POST call"
    assert all("PREVIEW_PATH" in p for p in posts), posts


# ----------------------------------------------- identity from the venue

class Book:
    def __init__(self, slug=SLUG, state="MARKET_STATE_OPEN",
                 bids=(("0.47", "100"),), offers=(("0.49", "80"),)):
        self.slug, self.state, self.bids, self.offers = slug, state, bids, offers

    def __call__(self, method, path, body=None, base=None):
        return {"marketData": {
            "marketSlug": self.slug, "state": self.state,
            "bids": [{"px": {"value": p}, "qty": q} for p, q in self.bids],
            "offers": [{"px": {"value": p}, "qty": q} for p, q in self.offers]}}


def transport_with(book):
    t = P.PreviewOnlyTransport()
    t.request = book
    return t


def test_the_candidate_slug_tracks_the_clock():
    assert R.window_slug(NOW) == SLUG
    assert R.window_slug(NOW.replace(minute=59)).endswith("-1345z")


def test_identity_is_confirmed_by_the_venue_echo_not_by_our_string():
    m = R.live_market(transport_with(Book()), NOW)
    assert m["identity_confirmed"] is True
    assert m["venue_market_slug"] == SLUG
    assert m["source"].startswith("authenticated")


def test_a_venue_echo_for_a_different_market_is_not_confirmed():
    m = R.live_market(transport_with(Book(slug="cpc-btc-updown-15m-x")), NOW)
    assert m["identity_confirmed"] is False


def test_tradability_comes_from_the_venue_state():
    assert R.live_market(transport_with(Book()), NOW)["open_tradable"] is True
    closed = R.live_market(
        transport_with(Book(state="MARKET_STATE_EXPIRED")), NOW)
    assert closed["open_tradable"] is False


# ------------------------------------------- the order shape is the real one

def test_the_proposed_order_joins_the_live_bid_and_is_envelope_bounded():
    m = R.live_market(transport_with(Book()), NOW)
    i = R.proposed_intent(m)
    from src import l5_probe_manifest as PM
    assert i["price"] == "0.47"                 # the live best bid
    assert i["mode"] == "MAKER" and i["side"] == "UP"
    assert D(i["price"]) * D(i["qty"]) <= PM.probe_limits().max_usd_per_market


def test_an_expensive_market_shrinks_the_quantity_to_the_cap():
    m = R.live_market(transport_with(Book(bids=(("0.99", "10"),))), NOW)
    i = R.proposed_intent(m)
    from src import l5_probe_manifest as PM
    assert D(i["price"]) * D(i["qty"]) <= PM.probe_limits().max_usd_per_market


# --------------------------------------------- the two representations

def test_legacy_is_exactly_what_submit_sent_before():
    b = OB.build(INTENT, SLUG, OB.LEGACY)
    assert b == {"clientOrderId": "c1", "marketSlug": SLUG, "outcome": "UP",
                 "side": "BUY", "price": "0.47", "quantity": "1.00",
                 "type": "LIMIT"}


def test_documented_matches_the_published_schema(creds):
    b = OB.build(INTENT, SLUG, OB.DOCUMENTED)
    assert b["type"] == "ORDER_TYPE_LIMIT"
    assert b["price"] == {"value": "0.47", "currency": "USD"}
    assert b["quantity"] == 1.0
    assert b["outcomeSide"] == "OUTCOME_SIDE_YES"
    assert b["action"] == "ORDER_ACTION_BUY"
    assert b["participateDontInitiate"] is True
    assert "outcome" not in b and "side" not in b


def test_the_two_representations_really_do_disagree(creds):
    d = OB.differences(OB.build(INTENT, SLUG, OB.DOCUMENTED),
                       OB.build(INTENT, SLUG, OB.LEGACY))
    assert not d["identical_but_for_preview_fields"]


def test_submit_has_no_body_of_its_own():
    """If submit ever serializes again, a preview stops proving anything."""
    src = Path("src/l5_pmus_transport.py").read_text()
    body = src.split("def submit(")[1].split("def cancel(")[0]
    assert "OB.build" in body
    assert "clientOrderId" not in body and "marketSlug" not in body


def test_default_schema_is_still_legacy_until_evidence():
    """Changing execution before the venue has answered is exactly what the
    preview exists to prevent."""
    assert OB.DEFAULT_SCHEMA == OB.LEGACY


# ------------------------------------------------------------ the run itself

def test_an_armed_session_refuses_to_run(monkeypatch, creds):
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "s1")
    monkeypatch.setenv(Q1.ARM_ENV, "s1")
    out = R.run()
    assert "ABORT" in out and out["armed"] is True


def test_no_confirmed_market_means_nothing_is_previewed(monkeypatch, creds):
    stub = transport_with(Book(slug="a-different-market"))
    monkeypatch.setattr(R.P, "PreviewOnlyTransport", lambda: stub)
    out = R.run()
    assert "ABORT" in out and "schema_trial" not in out
