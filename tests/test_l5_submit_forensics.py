"""When a submit's outcome is unknowable, the record must say WHERE.

Probe 03, 2026-09-25T02:15:50Z, armed with real money: one action reached
`submit()`, something raised, and the report said only
`{"SUBMIT_OUTCOME_UNKNOWN": 1}`. The cause went to a journal on a volume
that cannot be read once a one-shot container exits.

Recovering it took mounting the volume through a second deployment -- and
even then the exception message alone could not have answered the question,
because `str(HTTPError)` is "HTTP Error 400: Bad Request" and
`str(URLError)` is "<urlopen error timed out>". Neither carries the path. A
failed `/v1/order/preview` and a failed `/v1/orders` are the difference
between "nothing was ever sent" and "an order may be resting at the venue",
and the exception cannot tell them apart.

So the transport records which call is in flight, and the record carries it.
"""
from __future__ import annotations

import json
import urllib.error
from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX
from src import l5_pmus_transport as TP

MARKET = "cpc-btc-updown-15m-2026-09-25-0215z"

#: the intent Probe 03 actually built, to the digit
PROBE_03_INTENT = {"client_order_id": "l5f-c5f420ceb3262337ae5beaa1",
                   "market": MARKET, "side": "UP", "price": D("0.4200"),
                   "qty": D("0.40668775417984630725"), "mode": "TAKER",
                   "action_key": "k"}


class FakeHTTP:
    """Raises at a chosen point in the submit sequence."""

    def __init__(self, *, fail_on=None, exc=None, preview=None):
        self.fail_on = fail_on
        self.exc = exc or RuntimeError("boom")
        self.calls = []
        self.preview = preview

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path))
        if self.fail_on and path == self.fail_on:
            raise self.exc
        if path == "/v1/order/preview":
            if self.preview is not None:
                return self.preview
            # the envelope is {"request": <body>}; a clean venue echoes it back
            return {"order": dict((body or {}).get("request") or body or {})}
        return {"order_id": "v1", "filled_qty": "0"}


def armed_transport(http):
    tp = TP.PmusTransport(market_slug=MARKET, http=http)
    tp.armed = True
    return tp


def test_a_preview_transport_failure_names_the_preview(monkeypatch):
    http = FakeHTTP(fail_on="/v1/order/preview",
                    exc=urllib.error.HTTPError(
                        "https://api.polymarket.us/v1/order/preview",
                        503, "Service Unavailable", {}, None))
    tp = armed_transport(http)
    with pytest.raises(urllib.error.HTTPError):
        tp.submit(PROBE_03_INTENT)

    f = EX.submit_forensics(tp)
    assert f["failure_stage"] == "PREVIEW_REQUEST"
    assert f["preview_attempted"] is True
    assert f["order_post_attempted"] is False, "nothing was ever sent"
    assert ("POST", "/v1/orders") not in http.calls


def test_an_echo_refusal_names_the_echo_and_proves_no_order_was_sent():
    """Probe 03's actual failure, reconstructed."""
    from src import l5_order_body as OB
    body = OB.from_intent(PROBE_03_INTENT, MARKET)
    # the venue normalised 0.4066877541798463 to 0.4, as it did live
    echoed = dict(body, quantity=0.4)
    http = FakeHTTP(preview={"order": echoed})
    tp = armed_transport(http)

    from src import l5_preview_gate as PG
    with pytest.raises(PG.PreviewRefused):
        tp.submit(PROBE_03_INTENT)

    f = EX.submit_forensics(tp)
    assert f["failure_stage"] == "PREVIEW_ECHO_VALIDATION"
    assert f["preview_attempted"] is True
    assert f["preview_status"] == 200
    assert f["order_post_attempted"] is False
    assert ("POST", "/v1/orders") not in http.calls, (
        "the echo gate must refuse BEFORE the order post")


def test_an_order_post_failure_says_the_order_may_be_live():
    http = FakeHTTP(fail_on="/v1/orders",
                    exc=urllib.error.URLError(TimeoutError("timed out")))
    tp = armed_transport(http)
    with pytest.raises(urllib.error.URLError):
        tp.submit(PROBE_03_INTENT)

    f = EX.submit_forensics(tp)
    assert f["failure_stage"] == "ORDER_POST"
    assert f["preview_attempted"] is True
    assert f["order_post_attempted"] is True, (
        "this is the case where an order may be resting at the venue")
    assert ("POST", "/v1/orders") in http.calls


def test_a_transport_that_records_nothing_reads_as_unknown_not_as_false():
    """None and False are different answers and must not be collapsed."""
    f = EX.submit_forensics(object())
    assert f == {"failure_stage": None, "preview_attempted": None,
                 "preview_status": None, "order_post_attempted": None}


# ------------------------------------------------- nothing secret gets out

SECRETISH = [
    "X-PM-Access-Key: pk_live_abcdef123456",
    "X-PM-Signature: 9f8e7d6c5b4a3f2e1d0c",
    "X-PM-Timestamp: 1790302550",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
]


@pytest.mark.parametrize("header", SECRETISH)
def test_header_shaped_text_never_survives_the_scrubber(header):
    out = EX._redact("submit failed: %s" % header)
    assert "REDACTED" in out
    name = header.split(":")[0]
    assert name in out, "the header NAME may stay; its value may not"
    assert header.split(": ", 1)[1] not in out


def test_the_credential_values_themselves_are_scrubbed(monkeypatch):
    from src import pmus_auth as Q1
    monkeypatch.setenv(Q1.KEY_ENV, "KEYID-9f8e7d6c5b4a")
    monkeypatch.setenv(Q1.SECRET_ENV, "SECRET-0123456789abcdef")
    msg = ("request failed for key KEYID-9f8e7d6c5b4a signed with "
           "SECRET-0123456789abcdef")
    out = EX._redact(msg)
    assert "KEYID-9f8e7d6c5b4a" not in out
    assert "SECRET-0123456789abcdef" not in out
    assert out.count("[REDACTED]") >= 2


def test_the_record_carries_the_redacted_cause_and_the_stage(tmp_path,
                                                             monkeypatch):
    """End to end: an unknown submit produces a usable, safe record."""
    from src import pmus_auth as Q1
    monkeypatch.setenv(Q1.KEY_ENV, "KEYID-9f8e7d6c5b4a")
    monkeypatch.setenv(Q1.SECRET_ENV, "SECRET-0123456789abcdef")

    class Raising:
        armed = True
        name = "FAKE"
        quantity_increment = D("0.01")
        submit_stage = "ORDER_POST"
        preview_attempted = True
        preview_status = 200
        order_post_attempted = True

        def submit(self, intent):
            raise RuntimeError(
                "POST failed (X-PM-Signature: 9f8e7d6c5b4a3f2e1d0c) "
                "for key KEYID-9f8e7d6c5b4a")

        def open_orders(self):
            return []

        def positions(self):
            return []

    import tests.test_l5_live_execution as L
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    limits = EX.SafetyLimits(max_usd_per_market=D("100"),
                             max_abs_residual=D("100"),
                             max_total_exposure_usd=D("100"),
                             max_concurrent_markets=5,
                             daily_realized_loss_halt_usd=D("100"))
    ex = EX.FaithfulExecutor(store, limits, transport=Raising(),
                             now_fn=L.NOW)
    rec = ex.handle(L.row(), {}, market="m", window="16:15")

    o = rec["ORDER"]
    assert rec["refused_reason"] == "SUBMIT_OUTCOME_UNKNOWN"
    assert o["error"] == "RuntimeError"
    assert o["failure_stage"] == "ORDER_POST"
    assert o["preview_attempted"] is True
    assert o["order_post_attempted"] is True
    assert o["intent"]["side"] == "UP"

    wire = json.dumps(rec, default=str)
    assert "9f8e7d6c5b4a3f2e1d0c" not in wire, "signature reached the record"
    assert "KEYID-9f8e7d6c5b4a" not in wire, "key id reached the record"
    assert "SECRET-0123456789abcdef" not in wire
    assert "[REDACTED]" in o["detail"]
