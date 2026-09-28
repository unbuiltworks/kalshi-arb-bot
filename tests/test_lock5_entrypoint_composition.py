"""BLOCKER 1/2: prove the composition from the ENTRYPOINT, not from the class.

The previous candidate's 39 wiring tests all constructed `Lock5Session` directly.
Every one passed while the entrypoint did not import the translation at all, so a
green suite proved the class worked and NOT that the probe used it. These tests
start at `scripts/l5_pmus_live_oneshot` and walk outward.

Each positive assertion is paired with a NEGATIVE CONTROL that disconnects the
component and requires the test to fail -- otherwise the test proves nothing.
"""
from __future__ import annotations

import ast
import os
import sys
from collections import deque
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.l5_pmus_live_oneshot as ONESHOT                  # noqa: E402
from src import l5_order_body as OB                             # noqa: E402
from src import pmus_completion_shadow as SHADOW                # noqa: E402
from src import pmus_lock5_translation as LOCK5                 # noqa: E402
from src import pmus_probe_observability as OBS                 # noqa: E402
from src import pmus_q1_executor as Q1                          # noqa: E402

ENTRY = "scripts/l5_pmus_live_oneshot.py"
MKT = "cpc-btc-updown-15m-2026-09-28-0215z"


# ------------------------------------------------- the reachable import graph

def _closure(entry: str) -> set:
    def local(path):
        out = set()
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except Exception:                                      # noqa: BLE001
            return out
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.startswith(("src.", "scripts.")):
                        out.add(a.name)
            elif isinstance(n, ast.ImportFrom):
                m = n.module or ""
                if m in ("src", "scripts"):
                    for a in n.names:
                        out.add("%s.%s" % (m, a.name))
                elif m.startswith(("src.", "scripts.")):
                    out.add(m)
                    for a in n.names:
                        out.add("%s.%s" % (m, a.name))
        return out

    def path_of(m):
        p = m.replace(".", "/") + ".py"
        if os.path.exists(p):
            return p
        alt = p[:-3] + "/__init__.py"
        return alt if os.path.exists(alt) else None

    seen, q = {entry}, deque([entry])
    while q:
        for m in local(q.popleft()):
            p = path_of(m)
            if p and p not in seen:
                seen.add(p)
                q.append(p)
    return seen


@pytest.fixture(scope="module")
def closure():
    return _closure(ENTRY)


@pytest.mark.parametrize("mod", [
    "src/pmus_lock5_translation.py",
    "src/pmus_completion_shadow.py",
    "src/pmus_live_mandate.py",
    "src/pmus_probe_observability.py",
    "src/l5_order_body.py",
    "src/pmus_q1_executor.py",
    "src/pmus_arm_retry.py",
])
def test_every_required_component_is_reachable_from_the_entrypoint(closure, mod):
    assert mod in closure, sorted(closure)


def test_the_negative_control_detects_a_disconnected_component(tmp_path):
    """Disconnect the translation and the reachability test must FAIL."""
    src = open(ENTRY, encoding="utf-8").read()
    stub = tmp_path / "entry.py"
    stub.write_text(src.replace(
        "from src import pmus_lock5_translation as LOCK5\n", ""))
    c = _closure(str(stub))
    assert not any(p.endswith("pmus_lock5_translation.py") for p in c), (
        "the control must actually remove it")
    # and the same assertion the positive test makes now fails
    with pytest.raises(AssertionError):
        assert "src/pmus_lock5_translation.py" in c


# --------------------------------------------- BLOCKER 2: forbidden capability

FORBIDDEN = ("pmus_l5_ramp_ladder",)


def test_the_ramp_ladder_is_not_reachable_from_the_entrypoint(closure):
    """RAMP_LADDER_001 REGRESSION GUARD.

    2026-09-16: the R1 canary was authorized for ONE market at $0.05 and
    committed $19.3295, because `pmus_l5_ramp_ladder.active()` auto-loaded three
    ten-day-old owner rungs and silently replaced the live limits -- per-market
    $0.05 -> $7.50, daily stop -$1.00 -> -$15.00. The LOCK5 probe must have
    EXACTLY ONE risk/sizing authority: the reviewed mandate boundary.
    """
    for bad in FORBIDDEN:
        offenders = [p for p in closure if bad in p]
        assert offenders == [], offenders


#: identifiers that would mean a second, dynamic, rung-based sizing authority
FORBIDDEN_IDENTS = ("per_market_first_leg_cap", "per_market_cap_usd", "active")
FORBIDDEN_MODULES = ("src.pmus_l5_ramp_ladder", "pmus_l5_ramp_ladder")


def _capability_graph(path):
    """Identifiers and imports that are CODE, not prose.

    Deliberately AST-based. A text search flags this project's own docstrings --
    `pmus_lock5_translation` explains at length why it does not import the ladder
    -- and a guard that cannot tell an explanation from a call is a guard that
    gets weakened the first time it cries wolf. Reachability is a property of the
    import graph and the call graph, so that is what is inspected.
    """
    idents, modules = set(), set()
    tree = ast.parse(open(path, encoding="utf-8").read())
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                modules.add(a.name)
        elif isinstance(n, ast.ImportFrom):
            m = n.module or ""
            modules.add(m)
            for a in n.names:
                modules.add("%s.%s" % (m, a.name) if m else a.name)
                idents.add(a.asname or a.name)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            idents.add(n.name)
        elif isinstance(n, ast.Attribute):
            idents.add(n.attr)
        elif isinstance(n, ast.Name):
            idents.add(n.id)
    return idents, modules


def test_no_reachable_module_defines_or_calls_a_rung_based_cap(closure):
    """Not just the ladder module: any dynamic rung-based cap mechanism."""
    for path in sorted(closure):
        idents, modules = _capability_graph(path)
        for m in FORBIDDEN_MODULES:
            assert m not in modules, (path, m)
        assert "per_market_first_leg_cap" not in idents, path
        # `active` alone is too common to ban outright; ban it only where the
        # ladder name is also present, which is the pattern that caused 09-16
        if any("ramp_ladder" in m for m in modules):
            assert "active" not in idents, path


def test_the_negative_control_detects_a_reintroduced_rung_cap(tmp_path):
    """Re-import the micro executor and the guard must FAIL."""
    src = open(ENTRY, encoding="utf-8").read()
    stub = tmp_path / "entry2.py"
    stub.write_text(src + "\nfrom src import pmus_micro_executor as _MX\n")
    c = _closure(str(stub))
    assert any("pmus_l5_ramp_ladder" in p for p in c) or any(
        "per_market_first_leg_cap" in open(p, encoding="utf-8").read()
        for p in c), "the control must actually reintroduce the capability"
    with pytest.raises(AssertionError):
        for bad in FORBIDDEN:
            assert [p for p in c if bad in p] == []
        for path in sorted(c):
            assert "per_market_first_leg_cap" not in open(
                path, encoding="utf-8").read()


# ------------------------------------------- the entrypoint EXERCISES the path

class _Tp:
    def __init__(self):
        self.posts = []

    def request(self, method, path, payload=None):
        self.posts.append((method, path, payload))
        return {"order": {"orderId": "OID1", "filledQty": 0}}


class _Store:
    def realized_today(self):
        return D(0)

    def live_orders(self):
        return {}


class _Journal:
    def __init__(self):
        self.rows = []

    def write(self, kind, payload):
        self.rows.append((kind, dict(payload)))


def _probe(tmp_path):
    p = ONESHOT.Probe.__new__(ONESHOT.Probe)
    p.market, p.tp, p.store = MKT, _Tp(), _Store()
    p.journal, p.clock = _Journal(), (lambda: 1000.0)
    p.lock5 = p.monitor = None
    return p


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(LOCK5.MANDATE, "admit",
                        lambda body, **k: {"admitted": True, "reason": "OK"})
    p = _probe(tmp_path)
    bk = SHADOW.Book()
    bk.add("up", D(100), D(100) * D("0.40"), maker=True)
    bk.add("down", D(100), D(100) * D("0.55"), maker=True)
    p.build_lock5(book=bk, mandate=object(), state_dir=tmp_path)
    return p


def test_the_entrypoint_composes_the_frozen_floor_by_identity(wired):
    assert wired.lock5.floor_now is not None
    assert LOCK5.settlement_independent_floor is \
        SHADOW.settlement_independent_floor
    assert wired.lock5.floor_now() == SHADOW.settlement_independent_floor(
        wired.lock5.book)


def test_the_entrypoint_exercises_lock_and_then_refuses_new_risk(wired):
    assert wired.lock5_may_open_risk("EXTEND") == (False, "LOCKED_NO_NEW_RISK")
    assert wired.lock5_may_open_risk("COMPLETE")[0] is True
    assert wired.lock5.locked is True


def test_an_uncomposed_probe_refuses_rather_than_deciding_locally(tmp_path):
    """NEGATIVE CONTROL: without the composition there is no local fallback."""
    p = _probe(tmp_path)
    assert p.lock5_may_open_risk("EXTEND") == (False, "LOCK5_NOT_COMPOSED")
    with pytest.raises(Exception):
        p.lock5_complete(outcome_side="OUTCOME_SIDE_NO", ask=D("0.23"),
                         qty=D(5), first_cost=D("0.44"))


def test_the_entrypoint_exercises_observability(wired):
    assert isinstance(wired.monitor, OBS.ProbeMonitor)
    wired.lock5_may_open_risk("EXTEND")
    assert os.path.exists(wired.monitor.path)
    assert wired.monitor.seen["LOCK"] >= 1


def test_the_entrypoint_exercises_a_permitted_taker_completion(wired):
    out = wired.lock5_complete(outcome_side="OUTCOME_SIDE_NO", ask=D("0.23"),
                               qty=D(5), first_cost=D("0.44"))
    body = wired.tp.posts[0][2]["request"]
    assert body == OB.build(slug=MKT, side="OUTCOME_SIDE_NO", price=D("0.23"),
                            qty=D(5), post_only=False)
    assert Q1.taker_completion_ok(D("0.44"), D("0.23")) is True
    assert out["stage"].startswith("micro_complement_taker_")


def test_the_entrypoint_path_refuses_where_the_frozen_policy_refuses(wired):
    r = wired.lock5_complete(outcome_side="OUTCOME_SIDE_NO", ask=D("0.99"),
                             qty=D(5), first_cost=D("0.95"))
    assert r["refused"] == "FROZEN_POLICY_DOES_NOT_PERMIT_COMPLETION"
    assert wired.tp.posts == []


def test_the_entrypoint_path_goes_through_mandate_admission(tmp_path,
                                                            monkeypatch):
    seen = {}

    def admit(body, **k):
        seen["hit"] = True
        return {"admitted": False, "reason": "PER_MARKET_CAP"}
    monkeypatch.setattr(LOCK5.MANDATE, "admit", admit)
    p = _probe(tmp_path)
    p.build_lock5(book=SHADOW.Book(), mandate=object(), state_dir=tmp_path)
    with pytest.raises(LOCK5.SafetyRefusal):
        p.lock5_complete(outcome_side="OUTCOME_SIDE_NO", ask=D("0.23"),
                         qty=D(5), first_cost=D("0.44"))
    assert seen.get("hit") is True and p.tp.posts == []


def test_the_entrypoint_handles_authoritative_fill_and_reconciliation(wired):
    wired.lock5.apply_authoritative_fill(side="down", qty=D(5),
                                         price=D("0.23"), source="VENUE_FILL")
    wired.lock5.apply_authoritative_fill(side="up", qty=D(5), price=D("0.44"),
                                         source="RECONCILIATION")
    with pytest.raises(ValueError):
        wired.lock5.apply_authoritative_fill(side="up", qty=D(1),
                                             price=D("0.5"), source="ACK")
    assert wired.monitor.seen["FILL"] == 2
    assert wired.monitor.seen["INVENTORY"] == 2


def test_the_entrypoint_does_not_reimplement_lock5(closure):
    """It must COMPOSE. A local floor or threshold here is the failure mode."""
    text = open(ENTRY, encoding="utf-8").read()
    body = text.split("class Probe")[1]
    assert "settlement_independent_floor(" not in body
    assert "5.00" not in body and 'D("5")' not in body
    assert "LOCK5.LOCK_USD" in body, "the threshold must be READ, not restated"
