"""The admit() verdict contract, asserted against the REAL function.

Every consumer of `pmus_live_mandate.admit` must agree with it on one thing: the
key that says yes or no. `GuardedTransport` read `v["admits"]`;
`pmus_lock5_translation.submit` read `verdict.get("admitted")`, which is always
None, so it refused every order it was ever given. Fail-closed, so nothing was at
risk -- and completely non-functional.

39 tests of that translation passed because each faked `admit` as
`{"admitted": True}`. A fake written from the same wrong assumption as the code
under test cannot catch that assumption being wrong. These tests therefore use no
fake at all.
"""
from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from src import l5_order_body as OB
from src import pmus_live_mandate as M

RAW = {
    "authorization_id": "OWNER-CONTRACT-TEST",
    "authorized_at": "2026-09-28T00:00:00Z",
    "service": "l5-live-probe-oneshot",
    "strategy": "BTC15M_PROFIT_LOCK_5_CAPACITY_PROBE",
    "strategy_version": "contract-test",
    "max_markets": 1, "max_total_markets": 1,
    "max_spend_per_market": "1.00", "max_total_spend": "1.00",
    "max_residual": "1.00", "max_daily_realized_loss": "1.00",
    "expiry": "2099-01-01T00:00:00Z",
}
SLUG = "cpc-btc-updown-15m-2026-09-28-0600z"


@pytest.fixture()
def mandate():
    m, err = M.parse_mandate(json.dumps(RAW))
    assert err is None and m is not None, err
    return m


def body(price="0.50", qty="1"):
    return OB.build(slug=SLUG, side="up", price=D(price), qty=D(qty),
                    post_only=True)


def test_the_verdict_key_is_admits_and_there_is_no_admitted_key(mandate, tmp_path):
    v = M.admit(body(), state_dir=tmp_path, mandate=mandate,
                realized_today=D(0))
    assert "admits" in v
    assert "admitted" not in v, (
        "a key named 'admitted' now exists; every consumer reading `admits` "
        "must be re-checked, and vice versa")
    assert v["admits"] is True
    assert v["reason"] == "admitted"        # the word is a REASON, not a key


def test_a_refusal_also_uses_admits(mandate, tmp_path):
    v = M.admit(body(price="0.99", qty="5"), state_dir=tmp_path,
                mandate=mandate, realized_today=D(0))
    assert v["admits"] is False
    assert "admitted" not in v
    assert v["reason"] and v["reason"] != "admitted"


def _verdict_keys_read(path):
    """Keys the module actually READS off a verdict, by AST.

    AST and not text: a text scan cannot tell a call from the comment that
    explains it, and a guard that flags its own documentation is a guard that
    gets deleted the first time it cries wolf. This project has already made
    that mistake once, with the forbidden-capability check.
    """
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read())
    keys = set()
    for n in ast.walk(tree):
        # verdict.get("x")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "get" and n.args \
                and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str):
            keys.add(n.args[0].value)
        # verdict["x"]
        elif isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                and isinstance(n.slice.value, str):
            keys.add(n.slice.value)
    return keys


def test_the_translation_reads_the_same_key_the_mandate_writes():
    keys = _verdict_keys_read("src/pmus_lock5_translation.py")
    assert "admits" in keys
    assert "admitted" not in keys, (
        "the translation reads a key admit() never returns")


def test_the_guarded_transport_reads_the_same_key():
    keys = _verdict_keys_read("src/pmus_live_mandate.py")
    assert "admits" in keys


def test_the_translation_actually_submits_when_the_real_mandate_admits(
        mandate, tmp_path, monkeypatch):
    """END TO END through the REAL admit(), no fake. This is the test that
    would have caught the key mismatch on the day it was written."""
    from src import pmus_completion_shadow as SHADOW
    from src import pmus_lock5_translation as L5
    from src import pmus_probe_observability as OBS

    class Tp:
        def __init__(self):
            self.posts = []

        def request(self, method, path, payload=None):
            self.posts.append((method, path, payload))
            return {"order": {"orderId": "OID1", "filledQty": 0}}

    tp = Tp()
    mon = OBS.ProbeMonitor(str(tmp_path / "obs.jsonl"), market=SLUG,
                           clock=lambda: 1000.0)
    sess = L5.Lock5Session(market=SLUG, book=SHADOW.Book(), transport=tp,
                           mandate=mandate, state_dir=tmp_path, monitor=mon,
                           realized_today=lambda: D(0))
    out = sess.submit(body(), stage="contract_test")
    assert tp.posts, "the real mandate admitted, but nothing reached the wire"
    assert out["mandate"]["admits"] is True
    assert sess.submitted == 1


def test_the_translation_refuses_when_the_real_mandate_refuses(
        mandate, tmp_path):
    from src import pmus_completion_shadow as SHADOW
    from src import pmus_lock5_translation as L5
    from src import pmus_probe_observability as OBS

    class Tp:
        def __init__(self):
            self.posts = []

        def request(self, method, path, payload=None):
            self.posts.append((method, path, payload))
            return {}

    tp = Tp()
    mon = OBS.ProbeMonitor(str(tmp_path / "obs.jsonl"), market=SLUG,
                           clock=lambda: 1000.0)
    sess = L5.Lock5Session(market=SLUG, book=SHADOW.Book(), transport=tp,
                           mandate=mandate, state_dir=tmp_path, monitor=mon,
                           realized_today=lambda: D(0))
    with pytest.raises(L5.SafetyRefusal):
        sess.submit(body(price="0.99", qty="5"), stage="over_cap")
    assert tp.posts == [], "a refused order reached the wire"
