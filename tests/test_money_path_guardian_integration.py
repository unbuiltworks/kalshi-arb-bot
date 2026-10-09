"""The money path must persist an INTENT before the POST leaves.

These drive `_post` directly with a fake transport, because the question is
ordering: does a durable record exist at the moment the network call is made?
R6 proves that if it does not, an order can outlive the process unrecorded.
"""
import json

import pytest

from src import pmus_order_guardian as G


class _Intent(dict):
    """Minimal stand-in for E.Intent: a dict with a `kind`."""

    def __init__(self, kind, **kw):
        super().__init__(**kw)
        self.kind = kind


class _MP:
    """Just the `_post` collaborators, so the test needs no engine."""

    BALANCE_EVIDENCE_THRESHOLD_USD = 10 ** 9

    def __init__(self, guardian, transport, market="M"):
        self.guardian = guardian
        self.tp = transport
        self.market = market
        self.journal = None
        self.timing = {}
        self.submitted = []
        self.live_orders = {}
        self.venue = None

        class _Reg:
            def as_dict(self):
                return {"cumulative_submitted": "0"}

            def record(self, *a, **k):
                return None

        self.registry = _Reg()

    def _balance_evidence(self, **k):
        return {}

    def _order_id(self, ack):
        return (ack or {}).get("id") or ""


def _post_of(mp, intent):
    from src import pmus_native_money_path as MP
    return MP.NativeMoneyPath._post(mp, intent)


def test_intent_is_durable_at_the_moment_the_post_is_made(tmp_path):
    s = G.OwnershipStore(str(tmp_path / "own.json"))
    seen = {}

    class T:
        def submit_body(self, body):
            # the store is read from DISK here, mid-POST
            reopened = G.OwnershipStore(str(tmp_path / "own.json"))
            seen["on_disk"] = list(reopened.orders.values())
            return {"id": "V1"}

    mp = _MP(s, T())
    _post_of(mp, _Intent("REST", side="up", price="0.50", qty="10"))
    assert len(seen["on_disk"]) == 1
    assert seen["on_disk"][0]["state"] == "INTENT"
    assert seen["on_disk"][0]["market"] == "M"
    # and it is bound afterwards
    assert [o["venue_order_id"] for o in s.orders.values()] == ["V1"]


def test_a_crash_inside_submit_leaves_an_unbound_intent_and_uncertainty(
        tmp_path):
    s = G.OwnershipStore(str(tmp_path / "own.json"))

    class T:
        def submit_body(self, body):
            raise OSError("connection reset mid-POST")

    mp = _MP(s, T())
    with pytest.raises(OSError):
        _post_of(mp, _Intent("REST", side="down", price="0.69", qty="10"))
    reopened = G.OwnershipStore(str(tmp_path / "own.json"))
    assert len(G.unbound_intents(reopened)) == 1
    assert any("SUBMIT_OUTCOME_UNKNOWN" in u for u in reopened.uncertain)
    assert reopened.may_submit()["may_submit"] is False


def test_an_ack_without_an_order_id_is_not_treated_as_a_refusal(tmp_path):
    s = G.OwnershipStore(str(tmp_path / "own.json"))

    class T:
        def submit_body(self, body):
            return {}                      # accepted? refused? unknowable

    mp = _MP(s, T())
    _post_of(mp, _Intent("REST", side="up", price="0.50", qty="10"))
    assert any("ACK_WITHOUT_ORDER_ID" in u for u in s.uncertain)
    assert s.may_submit()["may_submit"] is False


def test_the_money_path_still_works_with_no_guardian(tmp_path):
    """Optional by construction: every existing caller is unchanged."""
    class T:
        def submit_body(self, body):
            return {"id": "V1"}

    mp = _MP(None, T())
    rec = _post_of(mp, _Intent("REST", side="up", price="0.50", qty="10"))
    assert rec["oid"] == "V1"
