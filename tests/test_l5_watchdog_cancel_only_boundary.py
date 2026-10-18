"""The watchdog's submit-free boundary, pinned independently of credentials.

WHY A SEPARATE FILE. `test_l5_watchdog_credential_free.py` pins the image as
deployed on 2026-10-14: no signing code at all, so cancel is unavailable by
construction. The owner has since asked for an authenticated cancel-only
watchdog, which means `src/pmus_auth.py` and `cryptography` would enter the
image and those assertions would flip.

What must NOT flip, whichever way that decision goes, is that this service
cannot assemble or send an order. That property rests on two things that have
nothing to do with holding a credential:

  1. the image carries no submit-capable module, so there is no code here
     that could build an order body; and
  2. `CancelOnlyTransport` refuses every method and route except GET and a
     single POST /v1/order/{id}/cancel.

These tests pin exactly those two, so the boundary is enforced before, during
and after any change to the credential decision.
"""
from __future__ import annotations

import pathlib

import pytest

from src import l5_watchdog as W

DOCKERFILE = pathlib.Path("deploy/l5_halt_watchdog/Dockerfile")

#: every module that can build or transmit an order. None may ever be in the
#: watchdog image, with or without signing code beside it.
SUBMIT_CAPABLE = (
    "src/l5_pmus_transport.py",      # _SignedHTTP: can POST /v1/orders
    "src/l5_live_execution.py",      # the executor and its ledger
    "src/l5_order_body.py",          # the order serializer
    "src/l5_preview_gate.py",        # the preview/echo gate
    "src/l5_maker_execution.py",     # maker intents
    "src/l5_probe_manifest.py",      # the arming envelope
    "scripts/l5_pmus_live_oneshot.py",
)


def _copy_specs():
    out = []
    for line in DOCKERFILE.read_text().splitlines():
        if line.startswith("COPY"):
            out.append(line.split()[1])
    return out


def test_the_image_can_never_assemble_an_order():
    """Independent of credentials: no order-building code is packaged."""
    specs = _copy_specs()
    for forbidden in SUBMIT_CAPABLE:
        assert forbidden not in specs, (
            "%s is submit-capable and must never be in the watchdog image"
            % forbidden)


def test_the_halt_asserter_has_no_clear_method():
    """The watchdog may latch and may not lift. Also credential-independent."""
    from src import l5_halt_gate as HG
    for name in ("clear", "clear_halt", "unhalt", "reset", "release"):
        assert not hasattr(HG.HaltAsserter, name), name


# --------------------------------------------- the transport's route matrix

def transport():
    """A CancelOnlyTransport with the network replaced by a recorder."""
    sent = []
    t = W.CancelOnlyTransport(
        opener=lambda m, p, b, base: sent.append((m, p)) or {"ok": 1})
    return t, sent


PERMITTED = [
    ("POST", "/v1/order/ABC123/cancel"),
    ("GET", "/v1/orders/open"),
    ("GET", "/v1/portfolio/positions"),
]

REFUSED = [
    ("POST", "/v1/orders"),                      # submit
    ("POST", "/v1/order/preview"),               # preview
    ("POST", "/v1/orders/cancel"),               # plural: not the one route
    ("POST", "/v1/order/ABC123/cancel_all"),     # account-wide shape
    ("POST", "/v1/order//cancel"),               # empty id
    ("POST", "/v1/order/ABC 123/cancel"),        # space in id
    ("POST", "/v1/order/ABC123/cancel?all=1"),   # query tacked on
    ("DELETE", "/v1/order/ABC123"),
    ("PUT", "/v1/order/ABC123"),
    ("PATCH", "/v1/order/ABC123/cancel"),
    ("POST", "/v1/positions/close"),
]


@pytest.mark.parametrize("method,path", PERMITTED)
def test_the_transport_permits_only_reads_and_one_cancel(method, path):
    t, sent = transport()
    t.request(method, path, None if method == "GET" else {})
    assert sent == [(method, path)]


@pytest.mark.parametrize("method,path", REFUSED)
def test_the_transport_refuses_everything_else(method, path):
    t, sent = transport()
    with pytest.raises(W.MutationRefused):
        t.request(method, path, None if method == "GET" else {})
    assert sent == [], "nothing may reach the venue on a refused route"


def test_the_transport_exposes_no_submit_or_arm():
    t, _ = transport()
    for name in ("submit", "arm", "place", "modify", "cancel_all"):
        assert not hasattr(t, name), name


def test_the_cancel_route_is_anchored():
    """An unanchored pattern would admit /v1/orders/x/cancel/and/more."""
    pat = W.CANCEL_ROUTE.pattern
    assert pat.startswith("^") and pat.endswith("$"), pat
    assert W.CANCEL_ROUTE.match("/v1/order/A1/cancel")
    assert not W.CANCEL_ROUTE.match("/v1/order/A1/cancel/extra")
    assert not W.CANCEL_ROUTE.match("/x/v1/order/A1/cancel")


# ------------------------------------------------------- fail-closed on build

def test_without_signing_code_the_transport_cannot_be_constructed(monkeypatch):
    """Today's deployed image: no pmus_auth, so cancel is unavailable."""
    monkeypatch.setattr(W, "A", None)
    with pytest.raises(RuntimeError) as e:
        W.CancelOnlyTransport()
    assert "pmus_auth" in str(e.value)


def test_no_oracle_means_no_cancel(monkeypatch):
    """Ownership is the gate that protects the owner's manual orders."""
    monkeypatch.delenv("L5_ORDER_REGISTRY_URL", raising=False)
    assert W.oracle_from_env() is None
