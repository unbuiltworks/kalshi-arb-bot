"""The wire format Attempt 5 got wrong, pinned so it cannot drift again.

Attempt 5 passed every frozen gate on live in-window data and was then
rejected by `POST /v1/order/preview` with HTTP 400. The cause was a
DUPLICATE SERIALIZER: EXEC-1-X hand-rolled its own order body instead of
delegating to `src.l5_order_body`, which every other PMUS order path in
this repository already uses.

These tests exist so the next live probe is not needed to establish the
wire shape. If the payload is wrong, this fails in CI in milliseconds and
for nothing, instead of costing a 15-minute window.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from analysis.l5.live_exec2 import latch as L
from analysis.l5.live_exec2 import orders as O
from analysis.l5.live_exec2 import spec as XSPEC
from analysis.l5.live_exec5 import fixture_a5 as F
from analysis.l5.live_exec5 import spec as W
from src import l5_order_body as OB

SID = "wire-test-session"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(XSPEC, "LATCH_DIR", str(tmp_path / "x"))
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "x"))
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", SID)


# ------------------------------------------------- the three A5 deviations

def _a5_body():
    return O.order_body(slug=F.A5["slug"],
                        outcome_side=F.A5["semantic_outcome_side"],
                        price=F.A5["semantic_price_own_terms"],
                        qty=F.A5["quantity"])


def test_the_body_now_matches_the_proven_serializer_exactly():
    assert _a5_body() == F.A5_CORRECTED_ORDER_BODY


def test_order_body_delegates_rather_than_reimplementing():
    """The fix is delegation, not a repaired copy. If someone reintroduces a
    local body builder, these two stop agreeing."""
    proven = OB.build(slug=F.A5["slug"], side="DOWN",
                      price=F.A5["semantic_price_own_terms"],
                      qty=F.A5["quantity"], post_only=True)
    assert _a5_body() == proven


def test_a5_differed_in_exactly_the_three_named_fields():
    sent, fixed = F.A5_SENT_PREVIEW_REQUEST, F.A5_CORRECTED_ORDER_BODY
    differing = sorted(k for k in set(sent) | set(fixed)
                       if sent.get(k) != fixed.get(k))
    assert differing == ["price", "quantity"]
    # and the third defect is the envelope, which is not a body field
    assert "request" not in sent
    assert set(F.A5_CORRECTED_PREVIEW_REQUEST) == {"request"}
    assert F.A5_DEFECTS == ("price.currency", "quantity",
                            "preview_envelope")


def test_price_carries_its_currency():
    assert _a5_body()["price"] == {"value": "0.51", "currency": "USD"}
    assert "currency" not in F.A5_SENT_PREVIEW_REQUEST["price"]


def test_quantity_is_a_json_number_not_a_string():
    q = _a5_body()["quantity"]
    assert isinstance(q, float) and not isinstance(q, str)
    assert q == 0.01
    assert json.dumps(_a5_body())[:0] == ""        # serializes at all
    assert '"quantity": 0.01' in json.dumps(_a5_body())
    assert isinstance(F.A5_SENT_PREVIEW_REQUEST["quantity"], str)


def test_preview_takes_the_request_envelope():
    b = _a5_body()
    assert O.preview_request(b) == {"request": b}


# --------------------------------------------- the complement, pinned once

def test_semantic_down_maps_to_no_side_and_the_complement_price():
    c = F.COMPLEMENT_CHAIN
    b = _a5_body()
    assert b["outcomeSide"] == c["wire_outcome_side"] == "OUTCOME_SIDE_NO"
    assert b["action"] == c["wire_action"] == "ORDER_ACTION_BUY"
    assert b["price"]["value"] == c["wire_price_value"] == "0.51"
    assert c["semantic_price_own_terms"] == "0.49"


def test_semantic_up_price_goes_on_the_wire_unchanged():
    b = O.order_body(slug="s", outcome_side="OUTCOME_SIDE_YES", price="0.49")
    assert b["outcomeSide"] == "OUTCOME_SIDE_YES"
    assert b["price"]["value"] == "0.49"


def test_the_complement_is_an_involution():
    """1 - (1 - x) == x, so the mapping cannot silently double-apply --
    which is the shape of the historical 24-of-24 rejection."""
    for px in ("0.01", "0.42", "0.49", "0.50", "0.99"):
        wire = OB.venue_price("OUTCOME_SIDE_NO", D(px))
        assert OB.venue_price("OUTCOME_SIDE_NO", wire) == D(px)


def test_a5_already_had_the_complement_right():
    """The defect was NOT the 1-X mapping. Saying so explicitly keeps a
    future reader from 'fixing' a correct conversion."""
    assert F.A5_SENT_PREVIEW_REQUEST["price"]["value"] == "0.51"
    assert F.A5_CORRECTED_ORDER_BODY["price"]["value"] == "0.51"
    assert F.COMPLEMENT_CHAIN["a5_got_this_right"] is True


def test_up_and_down_do_not_collide():
    up = O.order_body(slug="s", outcome_side="OUTCOME_SIDE_YES", price="0.49")
    dn = O.order_body(slug="s", outcome_side="OUTCOME_SIDE_NO", price="0.49")
    assert up["outcomeSide"] != dn["outcomeSide"]
    assert up["price"]["value"] != dn["price"]["value"]


# ------------------------------------------------ the venue error, captured

class _Resp:
    def __init__(self, headers):
        self._h = headers

    def items(self):
        return list(self._h.items())


class _Boom(Exception):
    pass


def _http_error(status=400, body=b'{"message":"quantity must be a number"}',
                headers=None):
    import urllib.error
    import io
    hdrs = headers or {"Content-Type": "application/json",
                       "X-Request-Id": "req-7",
                       "Set-Cookie": "session=supersecret",
                       "Authorization": "Bearer nope"}
    return urllib.error.HTTPError(
        "https://x/v1/order/preview", status, "Bad Request",
        _Resp(hdrs), io.BytesIO(body))


def test_the_venue_error_keeps_status_and_body():
    exc = O.VenueHTTPError(method="POST", path=O.PREVIEW_PATH, status=400,
                           reason="Bad Request",
                           body='{"message":"quantity must be a number"}',
                           headers={"content-type": "application/json"})
    d = exc.detail()
    assert d["status"] == 400
    assert "quantity must be a number" in d["response_body"]
    assert d["path"] == O.PREVIEW_PATH


def test_response_headers_are_allowlisted_not_denylisted():
    safe = O.safe_headers(_Resp({"Content-Type": "application/json",
                                 "X-Request-Id": "req-7",
                                 "Set-Cookie": "session=supersecret",
                                 "Authorization": "Bearer nope",
                                 "X-Some-New-Header": "whatever"}))
    assert safe == {"content-type": "application/json",
                    "x-request-id": "req-7"}
    # the point of an allowlist: an unknown header is dropped, not kept
    assert "x-some-new-header" not in safe
    assert not any("secret" in v for v in safe.values())


def test_request_header_values_are_never_recorded():
    got = O.redacted_request_header_names(
        {"Authorization": "Bearer SECRET", "Content-Type": "application/json",
         "PMUS-API-KEY-ID": "key-123"})
    flat = json.dumps(got)
    assert "SECRET" not in flat and "key-123" not in flat
    assert got["VALUES_NEVER_RECORDED"] is True
    assert "Authorization" in got["redacted"]
    assert "PMUS-API-KEY-ID" in got["redacted"]


# --------------------------- preview rejection is NOT submission ambiguity

class _FailingTransport(O.BoundedOrderTransport):
    """Fails at a chosen phase, so the classifier can be tested without a
    venue and without inferring anything from the latch."""

    def __init__(self, fail_at):
        super().__init__()
        self.fail_at = fail_at
        self.posts = []

    def request(self, method, path, body=None, base=None):
        self.posts.append(path)
        if path == O.PREVIEW_PATH:
            if self.fail_at == "preview":
                raise O.VenueHTTPError(
                    method="POST", path=path, status=400,
                    reason="Bad Request",
                    body='{"message":"quantity must be a number"}',
                    headers={"content-type": "application/json"})
            if self.fail_at == "preview_other":
                raise _Boom("socket died")
            return {"order": dict(body["request"])}
        if path == O.SUBMIT_PATH:
            if self.fail_at == "submit":
                raise O.VenueHTTPError(method="POST", path=path, status=503,
                                       reason="Unavailable", body="{}",
                                       headers={})
            if self.fail_at == "submit_other":
                raise TimeoutError("no answer")
            return {"id": "ord-1"}
        return {"ok": True}


def _submit(t):
    return O.submit_one(t, slug=F.A5["slug"],
                        outcome_side=F.A5["semantic_outcome_side"],
                        price=F.A5["semantic_price_own_terms"],
                        qty=F.A5["quantity"], checks={"ok": True})


@pytest.mark.parametrize("phase", ["preview", "preview_other"])
def test_a_preview_failure_is_preview_rejected_and_spends_no_latch(phase):
    t = _FailingTransport(phase)
    with pytest.raises(O.PreviewRejected) as ei:
        _submit(t)
    assert ei.value.crossed_order_boundary is False
    assert ei.value.latch_consumed is False
    assert ei.value.detail["phase"].startswith("PREVIEW")
    assert L.state()["spent"] is False
    assert O.SUBMIT_PATH not in t.posts


@pytest.mark.parametrize("phase", ["submit", "submit_other"])
def test_a_submit_failure_is_ambiguous_and_the_latch_is_spent(phase):
    t = _FailingTransport(phase)
    with pytest.raises(O.SubmissionAmbiguous) as ei:
        _submit(t)
    assert ei.value.crossed_order_boundary is True
    assert ei.value.latch_consumed is True
    assert ei.value.detail["phase"] == "SUBMIT"
    assert L.state()["spent"] is True


def test_preview_rejection_is_decided_by_type_not_by_the_latch():
    """The latch is EVIDENCE, not the decision procedure. A preview failure
    names itself, which is why A5 no longer needs to be read backwards out
    of a missing file."""
    t = _FailingTransport("preview")
    with pytest.raises(O.PreviewRejected) as ei:
        _submit(t)
    exc = ei.value
    # the verdict holds without consulting the latch at all
    assert exc.crossed_order_boundary is False
    assert isinstance(exc, O.PreviewRejected)
    assert not isinstance(exc, O.SubmissionAmbiguous)


def test_the_400_body_survives_into_the_diagnostic():
    t = _FailingTransport("preview")
    with pytest.raises(O.PreviewRejected) as ei:
        _submit(t)
    assert "quantity must be a number" in ei.value.detail["response_body"]
    assert ei.value.detail["status"] == 400
    assert ei.value.detail["sent"]["price"]["currency"] == "USD"


def test_a_good_preview_sends_the_envelope_and_echoes():
    t = _FailingTransport(None)
    out = _submit(t)
    assert out["order_id"] == "ord-1"
    assert t.posts[0] == O.PREVIEW_PATH and t.posts[1] == O.SUBMIT_PATH


# --------------------------------------------------- the frozen XR2W spec

def test_xr2w_is_frozen_and_pinned():
    W.assert_frozen()
    assert W.fingerprint() == W.XR2W_FINGERPRINT
    assert len(W.XR2W_FINGERPRINT) == 64


def test_xr2w_changes_nothing_economic():
    from analysis.l5.live_exec.spec import MAX_POSSIBLE_LOSS_USD
    assert str(MAX_POSSIBLE_LOSS_USD) == "0.0099"
    assert W.SPEC["side_rule"] == W.SPEC["side_rule"]   # inherited wholesale
    r = W.diff_report()
    assert r["max_possible_loss_usd_unchanged"] == "0.0099"
    # only the keys this correction is allowed to touch
    assert set(r["keys_added"]) <= {"wire_serialization",
                                    "venue_error_capture",
                                    "lifecycle_classification", "supersedes"}
    assert set(r["keys_changed"]) <= {"probe", "spec_version",
                                      "derived_from_probe",
                                      "derived_from_fingerprint",
                                      "correction_count", "correction"}
    assert r["keys_removed"] == []


def test_xr2w_declares_its_own_supersessions():
    must_not = W.SPEC["supersedes"]["must_not_be_rearmed"]
    assert "0f3340195d68a5faf42dca97450e88eef3e7964ce4ca794c6e00baed54c9042f" \
        in must_not
    assert W.XR2W_FINGERPRINT not in must_not


def test_xr2w_keeps_every_xr2_invariant_but_the_correction_count():
    from analysis.l5.live_exec3.spec import XR2_INVARIANTS
    inherited = {k: v for k, v in XR2_INVARIANTS if k != "correction_count"}
    mine = dict(W.XR2W_INVARIANTS)
    for k, v in inherited.items():
        assert mine[k] == v
    assert mine["correction_count"] == 2


def test_the_tte_invariant_is_still_the_frozen_one():
    assert W.SPEC["tte_invariant"]["cutoff_s"] == 120.0
    assert W.SPEC["tte_invariant"]["checked_before_post"] is True
    assert W.SPEC["tte_invariant"]["checked_before_latch_consumption"] is True


# ------------------------------- the image must carry what the code imports

def test_the_exec_image_copies_every_module_the_order_path_imports():
    """Attempt 5's fix would itself have failed at boot: the XR2 image did
    not COPY `src/l5_order_body.py`, so delegating to it would have been an
    ImportError in production. A missing COPY is invisible until the
    container starts, which is the most expensive place to find it."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    docker = (root / "deploy/l5_exec1xr2/Dockerfile").read_text()
    copied = {line.split()[1] for line in docker.splitlines()
              if line.startswith("COPY ")}

    # `from a.b import c` may import a MODULE c inside package a.b, or a
    # name out of module a.b. Accept either file, and ignore the package
    # directory itself -- Python 3 namespace packages need no __init__.
    needed = set()
    for mod in ("analysis/l5/live_exec2/orders.py",
                "analysis/l5/live_exec3/runner.py"):
        tree = ast.parse((root / mod).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                base = node.module
                if base.split(".")[0] not in ("src", "analysis", "scripts"):
                    continue
                for a in node.names:
                    needed.add(("%s/%s.py" % (base.replace(".", "/"), a.name),
                                "%s.py" % base.replace(".", "/")))
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in ("src", "analysis", "scripts"):
                        needed.add(("%s.py" % a.name.replace(".", "/"),))

    missing = sorted(alts[0] for alts in needed
                     if not any(alt in copied for alt in alts))
    assert missing == [], "not COPY'd into the image: %s" % missing
    assert "src/l5_order_body.py" in copied
