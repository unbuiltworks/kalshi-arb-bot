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

#: EVERY module the image is allowed to be STARTED from. The closure guard is
#: set equality against the union of these, which is what makes it a real
#: guard in both directions: nothing reachable may be missing from the image,
#: and nothing may be copied in that no declared entrypoint can reach.
#:
#: It used to walk `ENTRY` alone, so the six read-only auxiliary scripts the
#: image also carries were permanently reported as "copied but unreachable"
#: -- a red guard that everyone learned to ignore, which is worse than no
#: guard. They are entrypoints; they are declared as such here.
ENTRYPOINTS = [
    ENTRY,
    #: the bounded q1 HOLD_OPEN_EXTEND canary -- the probe's start command
    "src/pmus_q1_canary.py",
    "scripts/pmus_oos_settlement_sweep.py",
    "scripts/r5_signature_extract.py",
    "scripts/pmus_balance_admission_probe.py",
    "scripts/pmus_completeness_rule.py",
    "scripts/audit_economic_complete.py",
]
MKT = "cpc-btc-updown-15m-2026-09-28-0215z"


# ------------------------------------------------- the reachable import graph

def _closure(entry: str) -> set:
    def local(path):
        out = set()
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except Exception:                                      # noqa: BLE001
            return out
        # RELATIVE IMPORTS COUNT (2026-09-28). This helper used to look only at
        # `n.module`, so every `from . import x` was invisible to it. The gap was
        # not academic: the image built from the closure it computed was missing
        # `src/polymarket_l5/config.py`, which `polymarket_l5` submodules import
        # as `from . import config`, and the probe died with ImportError inside
        # the observing stage. A reachability guard that cannot see half the
        # import syntax reports a clean closure for an artifact that cannot run.
        pkg = os.path.dirname(path).replace(os.sep, ".")
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.startswith(("src.", "scripts.")):
                        out.add(a.name)
            elif isinstance(n, ast.ImportFrom):
                if n.level:
                    base = pkg.split(".")
                    if n.level > 1:
                        base = base[: len(base) - (n.level - 1)]
                    m = ".".join(base + ([n.module] if n.module else []))
                else:
                    m = n.module or ""
                if m in ("src", "scripts"):
                    # the package itself too: `from src import x` needs
                    # src/__init__.py in the image or the import fails, even
                    # though no module is named plain `src`.
                    out.add(m)
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
    out = set()
    for entry in ENTRYPOINTS:
        out |= _closure(entry)
        out.add(entry)
    return out


@pytest.mark.parametrize("mod", [
    "src/pmus_lock5_translation.py",
    "src/pmus_completion_shadow.py",
    "src/pmus_live_mandate.py",
    "src/pmus_probe_observability.py",
    "src/l5_order_body.py",
    "src/pmus_q1_executor.py",
    "src/pmus_arm_retry.py",
    "src/pmus_paired_log.py",
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


class _Mandate:
    """A Rung-A shaped mandate: the ONLY monetary authority the probe may use."""
    max_spend_per_market = D("1.00")
    max_total_spend = D("1.00")
    max_residual = D("1.00")
    max_daily_realized_loss = D("1.00")
    max_markets = 1
    max_total_markets = 1


def _probe(tmp_path):
    p = ONESHOT.Probe.__new__(ONESHOT.Probe)
    p.market, p.tp, p.store = MKT, _Tp(), _Store()
    p.journal, p.clock = _Journal(), (lambda: 1000.0)
    p.lock5 = p.monitor = None
    return p


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(LOCK5.MANDATE, "admit",
                        lambda body, **k: {"admits": True, "reason": "OK"})
    p = _probe(tmp_path)
    bk = SHADOW.Book()
    bk.add("up", D(100), D(100) * D("0.40"), maker=True)
    bk.add("down", D(100), D(100) * D("0.55"), maker=True)
    p.build_lock5(book=bk, mandate=_Mandate(), state_dir=tmp_path)
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
    # the BARE body -- see test_pmus_lock5_translation for why
    body = wired.tp.posts[0][2]
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
        return {"admits": False, "reason": "PER_MARKET_CAP"}
    monkeypatch.setattr(LOCK5.MANDATE, "admit", admit)
    p = _probe(tmp_path)
    p.build_lock5(book=SHADOW.Book(), mandate=_Mandate(), state_dir=tmp_path)
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


# ===================================================================== PAIRED
# The shadow-vs-live paired log is part of the money path, so it gets the same
# treatment LOCK5 got after BLOCKER 1: proven reachable from the ENTRYPOINT's
# closure, and proven to fail when disconnected. A paired log that exists in the
# repository but not in the artifact would let the first live window be written
# up against a record that was never kept.

def test_the_entrypoint_module_actually_binds_the_paired_log():
    assert getattr(ONESHOT, "PAIRED", None) is not None
    assert ONESHOT.PAIRED.__name__ == "src.pmus_paired_log"
    assert hasattr(ONESHOT.PAIRED, "PairedLog")


def test_build_lock5_composes_a_paired_log_and_pair_intent_uses_it(wired):
    assert isinstance(wired.paired, ONESHOT.PAIRED.PairedLog)
    row = wired.pair_intent(action="OPEN", side="up", shadow_price="0.48",
                            shadow_qty="50", shadow_filled=True)
    assert wired.paired.rows == [row]
    # the live side is UNKNOWN even though the shadow model counted a fill
    assert row.shadow_filled is True and row.live_filled is False
    # and the frozen floor came from LOCK5, not a second implementation here
    assert row.shadow_floor == wired.lock5.floor_now()


def test_the_window_binding_is_recorded_on_the_paired_log(wired):
    assert wired.paired.window == MKT
    assert wired.paired.market == MKT


def test_negative_control_disconnecting_the_paired_log_fails_pair_intent(
        tmp_path, monkeypatch):
    """Deliberate disconnection. If this passes, the test above is vacuous."""
    monkeypatch.setattr(LOCK5.MANDATE, "admit",
                        lambda body, **k: {"admits": True, "reason": "OK"})
    monkeypatch.setattr(ONESHOT.PAIRED, "PairedLog", lambda **kw: None)
    p = _probe(tmp_path)
    bk = SHADOW.Book()
    bk.add("up", D(100), D(100) * D("0.40"), maker=True)
    # the disconnection is caught at COMPOSITION time -- build_lock5 journals
    # the paired log's path, so a probe cannot finish composing without one.
    with pytest.raises(AttributeError):
        p.build_lock5(book=bk, mandate=_Mandate(), state_dir=tmp_path)
    assert p.paired is None
    # and a probe that somehow got past that still cannot open a paired row
    with pytest.raises(Exception):
        p.pair_intent(action="OPEN", side="up", shadow_price="0.48",
                      shadow_qty="50", shadow_filled=True)


def test_negative_control_the_paired_log_cannot_promote_a_shadow_fill(wired):
    row = wired.pair_intent(action="OPEN", side="up", shadow_price="0.48",
                            shadow_qty="50", shadow_filled=True)
    with pytest.raises(ONESHOT.PAIRED.PromotionRefused):
        row.venue_fill(price="0.48", qty="50", source="SHADOW")
    assert row.live_state == "UNKNOWN"


def test_negative_control_removing_the_paired_log_breaks_reachability(tmp_path):
    """Strip the import and the reachability assertion must fail."""
    src = open(ENTRY).read().replace(
        "from src import pmus_paired_log as PAIRED", "")
    stub = tmp_path / "no_paired.py"
    stub.write_text(src)
    assert "src/pmus_paired_log.py" not in _closure(str(stub))


# ============================================ THE PACKAGED IMAGE, MECHANICALLY
# 2026-09-28. The reviewed artifact e48960f7 built cleanly, reported a 44-file
# COPY list "byte-identical to the closure", and then died at runtime with
# ImportError: cannot import name 'config' from 'src.polymarket_l5'. Both halves
# of that comparison were computed by the same tool with the same blind spot, so
# they agreed with each other and disagreed with Python. Nothing compared the
# Dockerfile to the closure mechanically inside the suite -- it was done by hand,
# in a shell, once. These tests move that comparison into the suite.

DOCKERFILE = "deploy/l5_pmus_lock5_probe/Dockerfile"


def _dockerfile_pyfiles() -> set:
    import re
    out = set()
    for line in open(DOCKERFILE, encoding="utf-8"):
        if not line.startswith("COPY "):
            continue
        out.update(re.findall(r"(?:src|scripts)/[A-Za-z0-9_/]+\.py", line))
    return out


def test_the_dockerfile_copies_exactly_the_entrypoint_closure(closure):
    """Set equality, both directions, with the offenders named.

    `closure` is the union over EVERY declared entrypoint (see ENTRYPOINTS),
    so a module reachable from any start command must be present, and a
    module no start command can reach must not be.
    """
    copied, reachable = _dockerfile_pyfiles(), set(closure)
    missing = sorted(reachable - copied)
    extra = sorted(copied - reachable)
    assert not missing, "reachable but NOT in the image: %s" % (missing,)
    assert not extra, "copied but unreachable (conceals dead capability): %s" % (extra,)


def test_the_closure_follows_relative_imports(tmp_path):
    """The exact blind spot that shipped an unrunnable image, pinned.

    A package whose __init__ is reached and whose sibling is imported with
    `from . import sib` must pull the sibling in. Before the fix this returned
    only the __init__.
    """
    pkg = tmp_path / "src" / "relpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from . import sib\n")
    (pkg / "sib.py").write_text("VALUE = 1\n")
    entry = tmp_path / "scripts"
    entry.mkdir()
    ep = entry / "ep.py"
    ep.write_text("from src.relpkg import __init__ as _\nfrom src import relpkg\n")
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        c = _closure("scripts/ep.py")
    finally:
        os.chdir(cwd)
    assert "src/relpkg/__init__.py" in c
    assert "src/relpkg/sib.py" in c, "relative import not followed: %s" % (sorted(c),)


def test_polymarket_l5_config_is_reachable_and_copied(closure):
    """The specific file whose absence crashed the probe at 04:15:07Z."""
    assert "src/polymarket_l5/config.py" in closure
    assert "src/polymarket_l5/config.py" in _dockerfile_pyfiles()


# ==================================== ONE CLOSURE IMPLEMENTATION, NOT THREE
# The local `_closure` above predates src/l5_import_closure and is kept only so
# these tests keep their own independent reading of the graph. These tests pin the
# two to each other: if they ever disagree, one of them has a blind spot, which is
# exactly the condition that shipped an unrunnable image.

def test_the_local_helper_and_the_shared_module_agree():
    """Per entrypoint, so a union in the fixture cannot hide a disagreement."""
    from src import l5_import_closure as C
    for entry in ENTRYPOINTS:
        mine = _closure(entry) | {entry}
        theirs = C.closure(entry) | {entry}
        assert mine == theirs, (
            "closure implementations disagree on %s: %s"
            % (entry, sorted(mine ^ theirs)))


def test_the_shared_closure_knows_the_package_markers_carry_no_behaviour():
    from src import l5_import_closure as C
    pkg = C.closure(ENTRY)
    markers = sorted(f for f in pkg if not C.behaviour_bearing(f))
    assert markers == ["src/__init__.py", "src/polymarket_l5/__init__.py"]
    # src/__init__.py is 0 bytes. It must be PACKAGED, and it can never be the
    # reason a test is called artifact-relevant.
    assert os.path.getsize("src/__init__.py") == 0
    assert "src/__init__.py" in pkg


def test_overinclusion_is_classified_with_a_path_proof():
    """A test reaching the closure ONLY through an empty package marker is
    repository debt, not a Rung-A blocker -- and the claim must show its path."""
    from src import l5_import_closure as C
    pkg = C.closure(ENTRY)
    verdict, proof = C.classify("tests/test_deploy_fallthrough_guard.py", pkg)
    assert verdict == "TEST_GRAPH_OVERINCLUSION"
    assert proof and proof[-1] == "src", proof


def test_a_genuinely_packaged_module_classifies_as_artifact_behaviour():
    """Negative control: the classifier must not call everything overinclusion."""
    from src import l5_import_closure as C
    pkg = C.closure(ENTRY)
    verdict, proof = C.classify("tests/test_pmus_lock5_translation.py", pkg)
    assert verdict == "ARTIFACT_BEHAVIOUR"
    assert proof and proof[-1].startswith("src.pmus_"), proof


def test_the_failing_forensics_behaviour_is_unreachable_from_the_entrypoint():
    """The one artifact-behaviour-set failure, pinned as unreachable.

    tests/test_window_forensics_stream.py enters the set through
    src.polymarket_l5.transfer_backtest, but the behaviour its failing assertion
    exercises lives in analysis.l5.window_forensics.eligibility, which the
    packaged entrypoint cannot reach at all. Recorded as a test so the exclusion
    stops holding the moment that stops being true.
    """
    from src import l5_import_closure as C
    for unreachable in ("analysis.l5.window_forensics.eligibility",
                        "src.l5_window_forensics",
                        "src.l5_research_supervisor"):
        assert C.reaches(ENTRY, {unreachable}) is None, unreachable


# ================================ the q1 canary must be RUNNABLE in the image

CANARY = "src/pmus_q1_canary.py"


def test_the_canary_is_in_the_image_closure(closure):
    """DEFECT 3: the probe cannot start a module the image does not carry."""
    assert CANARY in closure
    assert CANARY in _dockerfile_pyfiles(), "canary is not COPYed into the image"


def test_the_canarys_own_imports_are_all_in_the_image():
    """Its WS feed and the reviewed executor must travel with it."""
    need = _closure(CANARY)
    copied = _dockerfile_pyfiles()
    for mod in ("src/pmus_q1_executor.py", "src/pmus_ws_live_state.py",
                "src/pmus_ws_evidence_recorder.py"):
        assert mod in need, f"{mod} is not reachable from the canary"
        assert mod in copied, f"{mod} is reachable but not in the image"


def test_the_image_start_command_targets_the_canary():
    """A correct COPY list with the old CMD still runs the wrong program."""
    text = open(DOCKERFILE, encoding="utf-8").read()
    cmd = [l for l in text.splitlines() if l.startswith("CMD ")]
    assert len(cmd) == 1, cmd
    assert "src.pmus_q1_canary" in cmd[0], cmd[0]
    assert "l5_pmus_live_oneshot" not in cmd[0], \
        "CMD still targets the previous entrypoint"


def test_the_canary_imports_cleanly_with_no_credentials(monkeypatch):
    """Import must not require env, network, or a venue -- the image builds
    and starts before any of those exist."""
    import importlib
    monkeypatch.delenv("PMUS_API_KEY_ID", raising=False)
    monkeypatch.delenv("PMUS_API_SECRET_KEY", raising=False)
    mod = importlib.import_module("src.pmus_q1_canary")
    assert mod.canary_armed() is False
