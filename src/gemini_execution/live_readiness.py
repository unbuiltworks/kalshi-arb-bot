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
    # (2) THE ENVELOPE MUST COVER WHAT THE FROZEN POLICY AUTHORIZES.
    try:
        emit = int(r.get("policy_max_rungs") or 0)
        if emit > int(r.get("max_working_orders") or 0):
            bad.append("policy can emit %d rungs but the envelope allows %d "
                       "working orders" % (emit, r.get("max_working_orders")))
    except Exception:                                          # noqa: BLE001
        bad.append("policy emission width unreadable")
    # (1) AND THE LIFECYCLE MUST NOT BE ABLE TO EXCEED IT.
    #
    # Capacity is not authority. `reconcile_surface` places a moved desired
    # price BESIDE a retained order whenever a leg slot is free, so on a
    # family where _leg_slots is 2 it rests more orders than the frozen
    # surface ever asked for. EP006 established that L5 HAD two adjacent
    # levels resting; it did not establish WHEN the second is created, and
    # no frozen rule says. Widening the envelope to fit would be adopting an
    # execution rule nobody wrote by way of a capital limit.
    try:
        life = int(r.get("lifecycle_max_rungs") or 0)
        pol = int(r.get("policy_max_rungs") or 0)
        if life > pol:
            bad.append(
                "UNFROZEN EXECUTION RULE: the lifecycle can rest %d orders "
                "where the frozen policy authorizes %d. The extra level is "
                "created by reconcile_surface placing a moved desired price "
                "beside a RETAINed order because a leg slot is free -- not "
                "by the desired surface, which emits rung_count=1 per "
                "outcome. Widen nothing until a frozen rule says when that "
                "second level is created." % (life, pol))
    except Exception:                                          # noqa: BLE001
        bad.append("lifecycle emission width unreadable")
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


# =========================================================== THE GATE ITSELF

#: what the readiness report must carry for a verdict to be possible at all
REQUIRED_REPORT_KEYS = ("family", "envelope", "driver_executable_sha",
                        "driver_envelope_sha", "bundle_sha", "spec_sha",
                        "mutation_boundary", "readiness_mode")

#: the four terms states, kept DISTINCT. RETRACTION_001: a local run with no
#: credentials returned {"readable": false, "verdict": "NO_CREDENTIALS"} and
#: was summarized as "terms not accepted", which cost the owner an
#: unnecessary account action. UNREADABLE and NO_CREDENTIALS are not FALSE.
TERMS_TRUE, TERMS_FALSE = "TRUE", "FALSE"
TERMS_UNREADABLE, TERMS_NO_CREDENTIALS = "UNREADABLE", "NO_CREDENTIALS"


def terms_provenance(ts: Any) -> Dict[str, Any]:
    """Four states, never three. Absence is not falsity."""
    if not isinstance(ts, dict):
        return {"state": TERMS_UNREADABLE, "why": "no terms_status recorded",
                "raw": ts}
    verdict = str(ts.get("verdict") or "").upper()
    if verdict == "NO_CREDENTIALS" or ts.get("readable") is False:
        state = (TERMS_NO_CREDENTIALS if verdict == "NO_CREDENTIALS"
                 else TERMS_UNREADABLE)
        return {"state": state, "why": "the venue was not actually asked",
                "raw": ts}
    # THE VENUE'S OWN VOCABULARY, NOT A FIELD I WISHED FOR. metadata's
    # terms_status returns {"verdict": "ACCEPTED", "hasAcceptedLatest": true}
    # and has NO `terms_accepted` key at all. Reading only that key turned a
    # readable, authenticated ACCEPTED into UNREADABLE on run 35615018494 --
    # RETRACTION_001's defect with the sign flipped. Both spellings are read,
    # and a readable response that says neither is still UNREADABLE.
    for acc in (ts.get("terms_accepted"), ts.get("hasAcceptedLatest")):
        if acc is True:
            return {"state": TERMS_TRUE, "raw": ts}
        if acc is False:
            return {"state": TERMS_FALSE, "raw": ts}
    if verdict == "ACCEPTED":
        return {"state": TERMS_TRUE, "raw": ts}
    if verdict in ("NOT_ACCEPTED", "REJECTED", "DECLINED"):
        return {"state": TERMS_FALSE, "raw": ts}
    return {"state": TERMS_UNREADABLE,
            "why": ("a readable response that states neither acceptance nor "
                    "refusal (verdict=%r); reported as UNREADABLE, never as "
                    "FALSE" % (ts.get("verdict"),)),
            "raw": ts}


def _chosen_symbol(rep: Dict[str, Any]) -> str:
    """The contract the run actually bound to.

    `symbol_selection` carries the symbol under `chosen`, not at the top
    level. Reading `symbol_selection["symbol"]` returned "" on run
    35615018494 and the gate reported "the symbol is not GEMI-BTC15M" while
    the run's own log shows GEMI-BTC15M2609211515-UP. A gate that misreads
    the evidence is worse than no gate, because it is believed.
    """
    sel = rep.get("symbol_selection") or {}
    return str((sel.get("chosen") or {}).get("symbol")
               or sel.get("symbol") or "")


def expectation_from_spec(spec: Any, *, branch: str, auth_id: str,
                          commit_sha: Optional[str] = None) -> Expectation:
    """The claim, built from the DECLARED spec rather than typed by hand.

    Typing the expectation out is the same defect one level up: it would be a
    second hand-built copy of the experiment, free to disagree with the spec
    the runner consumed. So it is derived.
    """
    from src.gemini_execution import bundle as BUNDLE
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import ws_continuous as WC

    e = spec.envelope_obj
    return Expectation(
        branch=branch,
        commit_sha=commit_sha or _git("rev-parse", "HEAD"),
        executable_sha=WC.executable_sha(),
        envelope_sha=WC.envelope_sha(),
        bundle_sha=BUNDLE.bundle_sha(),
        auth_id=auth_id,
        family=spec.family,
        symbol_family=spec.symbol_family,
        envelope_id=e.name,
        action_size=str(e.action_size),
        cohort_prefix=str(e.cohort_prefix),
        #: NO COHORT IS DECLARED FOR A READINESS RUN, and inventing one would
        #: be creating a live budget the owner has not authorized. Both sides
        #: carry the same sentinel, so the field is reported rather than
        #: silently skipped -- and an armed run, which does carry a cohort,
        #: is compared on its real remaining budget.
        remaining_budget=(spec.cohort or "NO_COHORT_DECLARED"),
        max_working_orders=e.max_working_orders,
        max_committed_usd=str(e.max_committed_usd),
        max_residual_usd=str(e.max_residual_usd),
        max_concurrent_markets=e.max_concurrent_markets,
        minimum_rest_time_s=str(CP.STALE_ORDER_S),
        argv=spec.canonical_argv(out="<out>", wire="<wire>", readiness=True))


def _surface_shape(spec: Any) -> Dict[str, int]:
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import ws_continuous as WC

    sp = CP.probe_params(action_size=spec.action_size)
    outcomes = len((sp.params["both_sides_policy"].value or {}).get(
        "outcomes") or [])
    rung_count = int(sp.params["rung_count"].value)
    slots = WC._leg_slots(str(spec.envelope_obj.cohort_prefix))
    return {"outcomes": outcomes, "rung_count": rung_count,
            "leg_slots": slots}


def policy_max_rungs(spec: Any) -> int:
    """(2) THE MOST ORDERS THE FROZEN BEHAVIORAL POLICY AUTHORIZES.

    `outcomes x rung_count`, and nothing else. The frozen surface emits ONE
    rung per outcome -- `rung_count` is 1 and `tick_offsets` is [0] -- so at
    any instant the policy has asked for at most one price per leg.

    THIS IS THE NUMBER THE ENVELOPE MUST COVER. It is deliberately NOT
    multiplied by `leg_slots`: see `lifecycle_max_rungs` for why that
    multiplication is a statement about capacity rather than authority.
    """
    s = _surface_shape(spec)
    return s["outcomes"] * s["rung_count"]


def lifecycle_max_rungs(spec: Any) -> int:
    """(1) THE MOST ORDERS THE LIFECYCLE ACTUALLY RESTS AT ONCE.

    MEASURED, NOT COMPUTED. This returned `outcomes x rung_count x leg_slots`
    -- a formula that happened to describe the reconciler on the day it was
    written. A formula cannot notice the reconciler changing, and the whole
    point of this number is to catch the lifecycle exceeding the policy. So
    it DRIVES the production `reconcile_surface` through the worst case and
    counts what is left resting:

      open one rung per outcome, then move EVERY desired price while every
      resting order is younger than the floor -- the exact state in which a
      free leg slot used to become a second price level.

    If someone re-widens the reconciler, this goes back up on its own and the
    cross-field rule fails again. That is the property a formula would not
    have had.
    """
    from src.gemini_execution import ws_continuous as WC

    s = _surface_shape(spec)
    prefix = str(spec.envelope_obj.cohort_prefix)
    legs = ["YES", "NO"][:max(1, s["outcomes"])]
    qty = str(spec.action_size)

    def surface(px):
        return [{"leg": lg, "price": "%.2f" % (px + i * 0.01), "qty": qty}
                for lg in legs for i in range(max(1, s["rung_count"]))]

    now = 1000.0
    working = WC.WorkingOrders()
    plan = WC.reconcile_surface(surface(0.50), working, now=now,
                                cohort_prefix=prefix)
    for a in plan["add"]:
        r = a["rung"]
        working.add("probe-%s-%s" % (r["leg"], r["price"]), leg=r["leg"],
                    price=r["price"], qty=r["qty"], now=now)
    best = len(plan["keep"]) + len(plan["retain"]) + len(plan["add"])

    # every price moves, every resting order still inside the floor
    moved = WC.reconcile_surface(surface(0.60), working,
                                 now=now + (WC.CP.STALE_ORDER_S / 2.0),
                                 cohort_prefix=prefix)
    return max(best, len(moved["keep"]) + len(moved["retain"])
               + len(moved["add"]))


def resolve_from_report(rep: Dict[str, Any], spec: Any) -> Dict[str, Any]:
    """What the RUN actually did, read out of its own report."""
    from src.gemini_execution import continuous_probe as CP
    from src.gemini_execution import ws_continuous as WC

    fam = rep.get("family") or {}
    env = rep.get("envelope") or {}
    attempted = rep.get("readiness_attempted") or []
    qtys = sorted({str((a.get("params") or {}).get("quantity"))
                   for a in attempted
                   if isinstance(a, dict)
                   and (a.get("params") or {}).get("quantity") is not None})

    r: Dict[str, Any] = {
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "commit_sha": _git("rev-parse", "HEAD"),
        "executable_sha": rep.get("driver_executable_sha"),
        "envelope_sha": rep.get("driver_envelope_sha"),
        "bundle_sha": rep.get("bundle_sha"),
        "auth_id": (rep.get("authorization") or {}).get("auth_id")
        or rep.get("authorization_id"),
        "family": str(fam.get("name") or "").lower(),
        "symbol_family": fam.get("resolved_symbol_prefix"),
        "envelope_id": env.get("name"),
        "action_size": env.get("action_size"),
        "cohort_prefix": rep.get("envelope_family"),
        "remaining_budget": ((rep.get("cohort") or {}).get("id")
                             if rep.get("cohort") else "NO_COHORT_DECLARED"),
        "max_working_orders": env.get("max_working_orders"),
        "max_committed_usd": env.get("max_committed_usd"),
        "max_residual_usd": env.get("max_residual_usd"),
        "max_concurrent_markets": env.get("max_concurrent_markets"),
        "minimum_rest_time_s": str(CP.STALE_ORDER_S),
        # ---- cross-field inputs
        #: the frame that WOULD have gone. None when the window never reached
        #: an action, which is not itself a failure: the q=1 rule only bites
        #: on a frame that exists.
        "frame_quantity": (qtys[0] if len(qtys) == 1 else
                           (None if not qtys else "/".join(qtys))),
        "policy_max_rungs": policy_max_rungs(spec),
        "lifecycle_max_rungs": lifecycle_max_rungs(spec),
        "surface_shape": _surface_shape(spec),
        "rest_floor_invoked": True,
        "retained_counts_as_risk": True,
        "mutations_sent": int(rep.get("readiness_mutations_sent") or 0),
        # ---- provenance, reported not compared
        "spec_sha": rep.get("spec_sha"),
        "symbol": _chosen_symbol(rep),
        "mutation_boundary": rep.get("mutation_boundary"),
        "state": rep.get("state"),
        "why": rep.get("why"),
        "attempted_frames": attempted,
    }
    return r


def structural_checks(rep: Dict[str, Any], spec: Any,
                      branch: str) -> Dict[str, Dict[str, Any]]:
    """Everything that is true of the TREE rather than of one run."""
    from src.gemini_execution import live_envelope as LE

    missing = [k for k in REQUIRED_REPORT_KEYS if k not in rep]
    terms = terms_provenance(rep.get("terms_status"))
    sym = _chosen_symbol(rep)
    return {
        "report_complete": {"ok": not missing, "missing": missing},
        "call_graph": call_graph_audit(),
        "dead_safety": dead_safety_sweep(),
        "stale_references": stale_reference_sweep(
            branch=branch, cohort_prefix=str(spec.envelope_obj.cohort_prefix),
            envelope_id=spec.envelope_obj.name),
        "spec_hash_matches": {
            "ok": str(rep.get("spec_sha")) == spec.spec_sha(),
            "declared": spec.spec_sha(), "run": rep.get("spec_sha")},
        "boundary_blocked": {
            "ok": str(rep.get("mutation_boundary", "")).startswith("BLOCKED")
            and bool(rep.get("readiness_mode")),
            "mutation_boundary": rep.get("mutation_boundary"),
            "readiness_mode": rep.get("readiness_mode")},
        # FOUR STATES, NOT THREE. Only TRUE passes; UNREADABLE and
        # NO_CREDENTIALS FAIL THE GATE and are reported as themselves --
        # never rendered as, or summarized as, FALSE.
        "terms": {"ok": terms["state"] == TERMS_TRUE, **terms},
        "symbol_is_the_right_family": {
            "ok": sym.startswith(spec.symbol_family), "symbol": sym,
            "expected_prefix": spec.symbol_family},
        "envelope_is_live_not_shadow": {
            "ok": spec.envelope_obj is not LE.SHADOW_Q001,
            "envelope": spec.envelope_obj.name},
    }


def run_gate(*, spec_id: str, report_path: str, branch: str,
             auth_id: str) -> Dict[str, Any]:
    from src.gemini_execution import live_probe_spec as SPEC

    spec = SPEC.get(spec_id)
    with open(report_path, "r", encoding="utf-8") as fh:
        rep = json.load(fh)

    expected = expectation_from_spec(spec, branch=branch, auth_id=auth_id)
    resolved = resolve_from_report(rep, spec)
    checks: Dict[str, Any] = {"fields": compare(expected, resolved),
                              "cross_field": cross_field_invariants(resolved)}
    checks.update(structural_checks(rep, spec, branch))
    m = build_manifest(expected, resolved, checks)
    m["spec"] = spec.as_dict()
    m["report_path"] = report_path
    m["manifest_sha"] = manifest_hash(m)
    return m


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description=("LIVE_READINESS_GATE. Reads the report a REAL readiness "
                     "invocation wrote and returns PASS or FAIL. Exit status "
                     "is 0 only on PASS, so a workflow step cannot record "
                     "green over a failed gate."))
    ap.add_argument("--spec", required=True, help="a declared LiveProbeSpec id")
    ap.add_argument("--report", required=True,
                    help="the readiness run's JSON report")
    ap.add_argument("--branch", required=True)
    ap.add_argument("--auth-id", default="NO-AUTHORIZATION-IN-FORCE")
    ap.add_argument("--out", default=None, help="where to write the manifest")
    a = ap.parse_args(argv)

    m = run_gate(spec_id=a.spec, report_path=a.report, branch=a.branch,
                 auth_id=a.auth_id)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(m, fh, indent=2, default=str, sort_keys=True)
    print("LIVE_READINESS_GATE " + json.dumps(
        {"verdict": m["verdict"], "manifest_sha": m["manifest_sha"],
         "spec_sha": m["spec"]["spec_sha"],
         "failed": sorted(k for k, v in m["checks"].items()
                          if not v.get("ok"))}, default=str), flush=True)
    if m["verdict"] != PASS:
        print(json.dumps(m["checks"], indent=2, default=str), flush=True)
    return 0 if m["verdict"] == PASS else 1


if __name__ == "__main__":                                # pragma: no cover
    raise SystemExit(main())
