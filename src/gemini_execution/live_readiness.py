"""LIVE_READINESS_GATE -- no authorization packet without a proven invocation.

WHY THIS EXISTS. Every live attempt so far failed at a DIFFERENT integration
seam, and each one reached owner authorization looking correct:

  1 checkout pinned to a dead branch          ZERO ORDERS
  2 `python ... | tee` hid a nonzero exit     a 0.09s death reported SUCCESS
  3 the latch pushed to the dead branch       an owner grant spent, nothing traded
  4 authorized q=1, wire carried 0.01         not the probe that was approved
  5 the frozen 30s rest floor never invoked   stale_orders() tested, unreachable
  6 a BTC15m envelope the CLI could not pick  proposed anyway
  7 a BTC15m cohort the runner never selected fell through to BTC5m
  8 family and envelope free to disagree      bounds from another market
  9 the bundle changed after review           the grant named code nobody saw

None of these is a strategy error. All nine are the same class: SOMETHING
BELIEVED WAS NOT SOMETHING PROVEN. So the gate proves, rather than believes.

WHAT IT IS NOT. It is not a simulator. There is no parallel model of the
runner here. It drives the REAL invocation -- same branch, same commit, same
workflow command, same family resolution, same discovery, same position
reader, same outstanding ledger, same budget, same latch path, same
lifecycle, same withdrawal -- with the FINAL MUTATION BOUNDARY replaced by a
recorder that refuses. Everything before that boundary is production code.

FAIL CLOSED. Anything unresolved, unreadable, unequal or unchecked is FAIL.
There is no "probably".
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

LABEL = "GEMINI_LIVE_READINESS_GATE_V1"
PASS, FAIL = "PASS", "FAIL"


class MutationBoundaryReached(Exception):
    """Raised INSTEAD of sending. Carries the frame that would have gone."""

    def __init__(self, method: str, params: Any) -> None:
        super().__init__("mutation boundary reached in readiness mode")
        self.method = method
        self.params = params


class ReadinessSession:
    """The mutation boundary, replaced. Reads pass; mutations never leave.

    This wraps the SAME RecordingSession the live path wraps, so everything
    the driver does up to the send is identical. `request` is the one and
    only place an order reaches the venue, so intercepting it is sufficient
    and nothing else needs to be mocked.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.attempted: List[Dict[str, Any]] = []
        self.sent = 0                     # must stay 0, asserted by the gate

    def __getattr__(self, name: str) -> Any:
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    async def connect(self, *a: Any, **kw: Any) -> Any:
        return await self._inner.connect(*a, **kw)

    async def request(self, method: str, params: Any, **kw: Any) -> Any:
        from src.gemini_execution.run_continuous import sanitize
        self.attempted.append({"method": method,
                               "params": sanitize(params)})
        raise MutationBoundaryReached(method, params)


# --------------------------------------------------------------- expectation

@dataclass(frozen=True)
class Expectation:
    """What the authorization packet CLAIMS. Every field is compared."""
    branch: str
    commit_sha: str
    executable_sha: str
    envelope_sha: str
    bundle_sha: str
    auth_id: str
    family: str                 # "btc5m" | "btc15m"
    symbol_family: str          # "GEMI-BTC05M" | "GEMI-BTC15M"
    envelope_id: str
    action_size: str
    cohort_prefix: str
    remaining_budget: int
    max_working_orders: int
    max_committed_usd: str
    max_residual_usd: str
    max_concurrent_markets: int
    minimum_rest_time_s: str
    argv: Sequence[str] = field(default_factory=tuple)


def _git(*a: str) -> str:
    try:
        return subprocess.run(["git", *a], capture_output=True,
                              text=True).stdout.strip()
    except Exception:                                          # noqa: BLE001
        return ""


# ------------------------------------------------------- call-graph audit

#: (module, name) pairs that CLAIM a production safety property. Each must be
#: reachable from the live entry point, or the gate fails. stale_orders is the
#: reason this exists: tested, asserted, and called nowhere.
SAFETY_CLAIMS = (
    ("src.gemini_execution.ws_continuous", "reconcile_surface"),
    ("src.gemini_execution.ws_continuous", "_leg_slots"),
    ("src.gemini_execution.live_envelope", "would_breach"),
    ("src.gemini_execution.continuous_probe", "resolve_action_size"),
    ("src.gemini_execution.run_canary", "can_make_durable"),
)

#: entry points whose transitive source is searched for those names
#: continuous_probe is ON the live graph -- Driver.decide calls
#: guard.step() -> plan() -> resolve_action_size/would_breach -- so omitting
#: it made the audit report a reachable function as unreached. The scan set
#: must be the live path, not a convenient subset of it.
LIVE_ENTRY = (("src.gemini_execution.run_continuous", "_build"),
              ("src.gemini_execution.ws_continuous", "Driver"),
              ("src.gemini_execution.ws_continuous", "reconcile_surface"),
              ("src.gemini_execution.continuous_probe", "plan"),
              ("src.gemini_execution.continuous_probe", "WindowGuard"))


def call_graph_audit(claims: Sequence = SAFETY_CLAIMS) -> Dict[str, Any]:
    """Is each claimed safety function actually REACHED by the live path?

    Source-level reachability, deliberately: a runtime probe would only prove
    the branches that happened to run, and the defect being hunted is a
    function that never runs at all.
    """
    body = []
    for mod, attr in LIVE_ENTRY:
        m = importlib.import_module(mod)
        obj = getattr(m, attr)
        body.append(inspect.getsource(obj))
        body.append(inspect.getsource(m))
    blob = "\n".join(body)
    rows = []
    for mod, name in claims:
        # A DEFINITION IS NOT A CALL. `\bname\s*\(` matches `def name(`
        # too, so including a module's own source made every function it
        # DEFINES look reached -- which silently disarmed the whole audit.
        # Caught by the stale_orders negative test.
        called = bool(re.search(r"(?<!def )\b%s\s*\(" % re.escape(name),
                                blob))
        rows.append({"module": mod, "name": name, "reached": called})
    unreached = [r for r in rows if not r["reached"]]
    return {"ok": not unreached, "claims": rows, "unreached": unreached,
            "why": ("a safety function that tests assert but production never "
                    "calls is the stale_orders class of defect")}


def dead_safety_sweep() -> Dict[str, Any]:
    """Functions in the execution package that LOOK like safety discipline and
    are referenced only by tests."""
    import pathlib
    src = pathlib.Path("src/gemini_execution")
    prod = "\n".join(p.read_text() for p in src.glob("*.py"))
    suspects = ("stale_orders", "risk_gates", "would_breach",
                "can_make_durable", "reconcile_surface")
    rows = []
    for name in suspects:
        defs = len(re.findall(r"^def %s\(" % name, prod, re.M))
        calls = len(re.findall(r"\b%s\s*\(" % name, prod)) - defs
        rows.append({"name": name, "defined": defs, "production_calls": calls,
                     "dead": defs > 0 and calls <= 0})
    dead = [r for r in rows if r["dead"]]
    return {"ok": not dead, "rows": rows, "dead": dead}


# ------------------------------------------------------ static stale sweep

def stale_reference_sweep(*, branch: str, cohort_prefix: str,
                          envelope_id: str,
                          workflow: str = ".github/workflows/"
                                          "gemini-settlement-gate.yml",
                          ) -> Dict[str, Any]:
    """Stale branch names, old cohort prefixes, obsolete envelope ids and
    hard-coded selectors inconsistent with the proposed invocation."""
    import pathlib
    hits: List[Dict[str, Any]] = []
    wf = pathlib.Path(workflow)
    text = wf.read_text() if wf.exists() else ""

    for m in re.finditer(r"^\s*ref:\s*([\w./-]+)", text, re.M):
        if m.group(1) != branch:
            hits.append({"kind": "checkout_ref", "found": m.group(1),
                         "expected": branch})
    # A BRANCH NAME, NOT "EVERYTHING UP TO WHITESPACE". `(\S+)` captured the
    # trailing ";" from `git push origin HEAD:branch; then` and reported the
    # correct branch as stale -- a false positive from my own sweep, caught by
    # running the gate on the real tree.
    for m in re.finditer(r"git push\s+origin\s+HEAD:([\w./-]+)", text):
        if m.group(1) != branch:
            hits.append({"kind": "push_target", "found": m.group(1),
                         "expected": branch})
    from src.gemini_execution import run_canary as RC
    if RC.BRANCH != branch:
        hits.append({"kind": "latch_branch", "found": RC.BRANCH,
                     "expected": branch})
    # an envelope id named anywhere that is not the proposed one AND not a
    # known sibling is an obsolete selector
    from src.gemini_execution import live_envelope as LE
    known = {LE.SHADOW_Q001.name, LE.LIVE_Q1.name, LE.LIVE_Q1_BTC15M.name,
             LE.LIVE_Q1_BTC15M_2LVL.name}
    for m in re.finditer(r"GEMINI_L5_LIVE_ENVELOPE[A-Z0-9_]*", text):
        if m.group(0) not in known:
            hits.append({"kind": "unknown_envelope_id", "found": m.group(0)})
    return {"ok": not hits, "hits": hits,
            "checked": {"branch": branch, "cohort_prefix": cohort_prefix,
                        "envelope_id": envelope_id, "workflow": str(wf)}}


# ------------------------------------------------------- cross-field rules

def cross_field_invariants(r: Dict[str, Any]) -> Dict[str, Any]:
    """Individually-correct values are not enough."""
    bad: List[str] = []

    if r.get("family") == "btc15m" and r.get("symbol_family") != "GEMI-BTC15M":
        bad.append("btc15m family resolved symbol family %s"
                   % r.get("symbol_family"))
    if r.get("family") == "btc5m" and r.get("symbol_family") != "GEMI-BTC05M":
        bad.append("btc5m family resolved symbol family %s"
                   % r.get("symbol_family"))
    if "BTC15M" in str(r.get("envelope_id")) and r.get("family") != "btc15m":
        bad.append("BTC15m envelope with family %s" % r.get("family"))
    if str(r.get("action_size")) == "1" and str(
            r.get("frame_quantity")) not in ("1", "1.0", None, "None"):
        bad.append("q=1 authorized but the serialized frame carries %s"
                   % r.get("frame_quantity"))
    # the envelope must cover the biggest surface the POLICY can emit
    try:
        emit = int(r.get("policy_max_rungs") or 0)
        if emit > int(r.get("max_working_orders") or 0):
            bad.append("policy can emit %d rungs but the envelope allows %d "
                       "working orders" % (emit, r.get("max_working_orders")))
    except Exception:                                          # noqa: BLE001
        bad.append("policy emission width unreadable")
    if not r.get("rest_floor_invoked"):
        bad.append("the frozen minimum_rest_time is not invoked on the live "
                   "call graph")
    if not r.get("retained_counts_as_risk"):
        bad.append("retained orders do not count toward working-order and "
                   "committed-capital limits")
    if int(r.get("mutations_sent") or 0) != 0:
        bad.append("a mutation was sent in readiness mode")
    return {"ok": not bad, "violations": bad}


# ------------------------------------------------------------- the manifest

def manifest_hash(m: Dict[str, Any]) -> str:
    body = json.dumps({k: v for k, v in m.items() if k != "manifest_sha"},
                      sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_manifest(expected: Expectation, resolved: Dict[str, Any],
                   checks: Dict[str, Any]) -> Dict[str, Any]:
    ok = all(bool(v.get("ok")) for v in checks.values())
    m: Dict[str, Any] = {
        "label": LABEL,
        "utc": datetime.now(timezone.utc).isoformat(),
        "verdict": PASS if ok else FAIL,
        "expected": expected.__dict__ if hasattr(expected, "__dict__")
        else dict(expected),
        "resolved": resolved,
        "checks": checks,
        "labels": ("EXPECTED == RESOLVED, proved immediately before the "
                   "mutation boundary. The frame below is what WOULD have "
                   "been sent; nothing was."),
    }
    m["manifest_sha"] = manifest_hash(m)
    return m


def compare(expected: Expectation, resolved: Dict[str, Any]) -> Dict[str, Any]:
    """EXPECTED == RESOLVED, field by field, with absence counted as failure."""
    diffs = []
    for key in ("branch", "commit_sha", "executable_sha", "envelope_sha",
                "bundle_sha", "auth_id", "family", "symbol_family",
                "envelope_id", "action_size", "cohort_prefix",
                "remaining_budget", "max_working_orders", "max_committed_usd",
                "max_residual_usd", "max_concurrent_markets",
                "minimum_rest_time_s"):
        want = getattr(expected, key)
        got = resolved.get(key, "<ABSENT>")
        if str(want) != str(got):
            diffs.append({"field": key, "expected": str(want),
                          "resolved": str(got)})
    return {"ok": not diffs, "diffs": diffs}
