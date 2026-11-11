"""The gates a live economic-measurement probe must clear before it may run.

FAILS CLOSED, ALWAYS. A check that cannot be established is a BLOCKER, never a
pass: "we could not reach the venue" and "the venue is fine" must never produce
the same verdict. Every refusal names the bound it hit.

WHAT THIS DOES NOT DO. It contains no order verb and cannot submit, cancel or
amend anything; tests/test_l5_live_probe_closure.py asserts that over the
source in a subprocess. Passing this preflight is PERMISSION TO MEASURE, not an
instruction to trade, and it does not select a strategy.

REUSE, NOT A SECOND OPINION. Risk arithmetic comes from l5_risk.shell -- the
single definition already equivalence-tested against the live PMUS
implementation -- and the settlement/accounting assertions come from
l5_shadow.preflight (A1-A4). Writing a second copy of either is how two
components end up disagreeing about the one number that must mean the same
thing everywhere.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.l5_live_probe import envelope as ENV
from src.l5_risk import shell as RISK

LABEL = "LIVE_PROBE_PREFLIGHT/economic_measurement"

# Credential NAMES only. This module never reads, prints, copies or logs a
# value -- presence is the whole check. These are the names
# src/polymarket_probe.py actually passes to the PolymarketUS client; the
# earlier PMUS_API_* guesses were names nothing reads, so the check would have
# been asking about variables that do not exist.
CREDENTIAL_ENV = ("POLYMARKET_KEY_ID", "POLYMARKET_SECRET_KEY")

# Order verbs that must not appear anywhere in this package.
ORDER_VERBS = ("place_order", "submit_order", "cancel_order", "amend_order",
               "create_order", "post_order")


def gate(name: str, ok: Optional[bool], blocker: Optional[str],
         detail: Any = None) -> Dict[str, Any]:
    """ok=None means UNDETERMINED, which is a blocker. Never a pass."""
    return {"check": name,
            "state": ("PASS" if ok is True else
                      "FAIL" if ok is False else "UNDETERMINED"),
            "blocker": blocker, "detail": detail}


# --------------------------------------------------------------- the checks

def serving_sha(expected: Optional[str] = None) -> Dict[str, Any]:
    """The artifact that would run must be the artifact that was reviewed."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception as exc:                                  # noqa: BLE001
        return gate("SERVING_SHA", None, f"cannot read git HEAD: {exc!r}"[:120])
    if not head:
        return gate("SERVING_SHA", None, "git HEAD empty")
    if expected is None:
        return gate("SERVING_SHA", None,
                    "no expected serving SHA supplied; an unpinned artifact is "
                    "not a verified one", {"head": head})
    ok = head.startswith(expected) or expected.startswith(head)
    return gate("SERVING_SHA", ok,
                None if ok else f"HEAD {head[:12]} != serving {expected[:12]}",
                {"head": head, "expected": expected})


def credentials_present(names: Sequence[str] = CREDENTIAL_ENV) -> Dict[str, Any]:
    """Presence only. Values are never read into a variable, printed or stored."""
    absent = [n for n in names if not os.environ.get(n)]
    return gate("CREDENTIALS_PRESENT", not absent,
                None if not absent else
                f"{len(absent)} credential env name(s) unset",
                {"required": list(names), "absent": absent,
                 "note": "presence only; no value is read, logged or copied"})


def venue_connectivity(probe: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """Read-only reachability. An exception is UNDETERMINED, not a failure to
    trade against -- but it still blocks."""
    if probe is None:
        return gate("VENUE_CONNECTIVITY", None,
                    "no connectivity probe supplied")
    try:
        r = probe()
    except Exception as exc:                                  # noqa: BLE001
        return gate("VENUE_CONNECTIVITY", None,
                    f"venue unreachable: {exc!r}"[:140])
    return gate("VENUE_CONNECTIVITY", bool(r),
                None if r else "venue reachable but returned nothing",
                {"reachable": bool(r)})


def market_eligibility(mkt: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Open, two-sided, and resolvable. A market we cannot settle is a market we
    cannot measure."""
    if not mkt:
        return gate("MARKET_ELIGIBILITY", None, "no market supplied")
    missing = [k for k in ("market_key", "open", "best_bid", "best_ask",
                           "resolvable") if mkt.get(k) in (None, "")]
    if missing:
        return gate("MARKET_ELIGIBILITY", None,
                    f"market state incomplete: {', '.join(missing)}", mkt)
    if not mkt.get("open"):
        return gate("MARKET_ELIGIBILITY", False, "market is not open", mkt)
    if not mkt.get("resolvable"):
        return gate("MARKET_ELIGIBILITY", False,
                    "market has no confirmable resolution path", mkt)
    return gate("MARKET_ELIGIBILITY", True, None, mkt)


def order_size_constraints(size: Optional[Dict[str, Any]],
                           minimums: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The configured size must satisfy every venue minimum, at the venue's own
    numbers rather than ours."""
    if not minimums:
        return gate("ORDER_SIZE_CONSTRAINTS", None,
                    "venue minimums unknown; see analysis/l5/live_probe")
    if not size:
        return gate("ORDER_SIZE_CONSTRAINTS", None,
                    "no order size chosen (envelope not selected)",
                    {"minimums": minimums})
    bad = []
    for k, lo in minimums.items():
        v = size.get(k)
        if v is None:
            bad.append(f"{k} unset")
        elif float(v) < float(lo):
            bad.append(f"{k}={v} below venue minimum {lo}")
    return gate("ORDER_SIZE_CONSTRAINTS", not bad,
                None if not bad else "; ".join(bad),
                {"size": size, "minimums": minimums})


def risk_bounds(*, realized: Any = "0", floors: Sequence[Any] = (),
                current_inventory: Any = "0", market_spent: Any = "0",
                open_markets: int = 0,
                last_heartbeat_age_s: Optional[float] = None,
                intent: Any = None) -> List[Dict[str, Any]]:
    """Residual exposure, concurrency, daily-loss halt and deadman -- all four
    from the ONE risk shell, so they cannot drift apart."""
    try:
        sh = ENV.shell()
    except ENV.EnvelopeNotChosen as exc:
        b = str(exc)
        return [gate(n, None, b) for n in
                ("RESIDUAL_EXPOSURE_LIMIT", "CONCURRENCY_LIMIT",
                 "DAILY_LOSS_HALT", "DEADMAN_CANCEL")]
    combined = RISK.combined_loss(realized, floors)
    tripped = RISK.budget_tripped(combined, sh.daily_loss_stop)
    stale = (last_heartbeat_age_s is not None
             and last_heartbeat_age_s > sh.deadman_s)
    out = [
        gate("DAILY_LOSS_HALT", not tripped,
             None if not tripped else
             f"combined loss {combined} has reached {sh.daily_loss_stop} "
             f"(inclusive)",
             {"combined_loss": str(combined), "stop": sh.daily_loss_stop}),
        gate("CONCURRENCY_LIMIT", open_markets < sh.max_concurrent_markets,
             None if open_markets < sh.max_concurrent_markets else
             f"{open_markets} markets open, limit {sh.max_concurrent_markets}",
             {"open_markets": open_markets,
              "limit": sh.max_concurrent_markets}),
        gate("DEADMAN_CANCEL",
             None if last_heartbeat_age_s is None else not stale,
             "no heartbeat age supplied; staleness cannot be established"
             if last_heartbeat_age_s is None else
             (f"last heartbeat {last_heartbeat_age_s:.0f}s ago exceeds "
              f"{sh.deadman_s:.0f}s" if stale else None),
             {"deadman_s": sh.deadman_s, "age_s": last_heartbeat_age_s}),
    ]
    if intent is None:
        out.append(gate("RESIDUAL_EXPOSURE_LIMIT", None,
                        "no intent supplied; the shell bounds an ACTION, so "
                        "there is nothing to bound",
                        {"max_inventory_shares": sh.max_inventory_shares,
                         "max_market_spend": sh.max_market_spend}))
    else:
        # the shell's method is check(); allows() does not exist. This branch
        # only runs when an intent is supplied, so the wrong name sat latent
        # through every envelope-unset run.
        v = sh.check(intent=intent, realized=realized, floors=floors,
                     current_inventory=current_inventory,
                     market_spent=market_spent, open_markets=open_markets,
                     last_heartbeat_age_s=last_heartbeat_age_s)
        out.append(gate("RESIDUAL_EXPOSURE_LIMIT", v.allowed,
                        None if v.allowed else v.reason, v.detail))
    return out


def venue_access(market: Optional[Dict[str, Any]],
                 access: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """THE MARKET MUST BE ON THE VENUE THE CREDENTIALS SIGN FOR.

    Without this, MARKET_ELIGIBILITY and CREDENTIALS_PRESENT can both pass while
    being true about different venues -- which is exactly what happened: the
    BTC15m rows come from Polymarket international and carry `venue: "PMUS"`,
    while the credentials authenticate to Polymarket US.
    """
    from src.l5_live_probe import venue_access as VA
    c = VA.consistency(market, access)
    return gate("VENUE_ACCESS", c.get("ok"),
                None if c.get("ok") else c.get("why"), c)


def settlement_accounting_path(src_dir: str = "src/l5_shadow") -> Dict[str, Any]:
    """filled -> settled -> realized must survive a change of operating state.
    This is the assertion already built for that; it is not re-derived here."""
    try:
        from src.l5_shadow import preflight as ACC
        v = ACC.verdict(src_dir=src_dir)
    except Exception as exc:                                  # noqa: BLE001
        return gate("SETTLEMENT_ACCOUNTING_PATH", None,
                    f"accounting preflight did not run: {exc!r}"[:140])
    # READ THAT MODULE'S ACTUAL CONTRACT. It reports {"verdict": "PASS",
    # "failed": [...]} and carries no "ok" key; probing for one returned
    # falsey and reported a PASSING accounting path as FAILED.
    passed = v.get("verdict") == "PASS" and not v.get("failed")
    return gate("SETTLEMENT_ACCOUNTING_PATH", passed,
                None if passed else
                f"accounting-path assertions did not pass: "
                f"{v.get('verdict')} failed={v.get('failed')}",
                {"gate": v.get("gate"), "verdict": v.get("verdict"),
                 "failed": v.get("failed"),
                 "assertions": [c.get("assertion") for c in v.get("checks", [])]})


def pmus_separation(pkg_dir: str = "src/l5_live_probe") -> Dict[str, Any]:
    """The probe must not be able to touch the HALTED legacy executor.

    Checked three ways, because any one alone is weak: a distinct mutable state
    root, no import of a PMUS module, and no order verb in the package at all.
    """
    problems: List[str] = []
    if ENV.STATE_ROOT in ENV.PMUS_STATE_ROOTS:
        problems.append(f"state root {ENV.STATE_ROOT} collides with PMUS")
    srcs: Dict[str, str] = {}
    if not os.path.isdir(pkg_dir):
        return gate("PMUS_SEPARATION", None, f"{pkg_dir} not found")
    for fn in sorted(os.listdir(pkg_dir)):
        if fn.endswith(".py"):
            srcs[fn] = open(os.path.join(pkg_dir, fn)).read()
    for fn, s in srcs.items():
        for line in s.splitlines():
            t = line.strip()
            if (t.startswith("import ") or t.startswith("from ")) and "pmus" in t.lower():
                problems.append(f"{fn} imports a PMUS module: {t[:60]}")
        for verb in ORDER_VERBS:
            # the constant tuple that DEFINES the ban is not a violation of it
            if f"def {verb}" in s or f".{verb}(" in s:
                problems.append(f"{fn} contains order verb {verb}")
    return gate("PMUS_SEPARATION", not problems,
                None if not problems else "; ".join(problems),
                {"state_root": ENV.STATE_ROOT, "files": sorted(srcs),
                 "pmus_roots_avoided": list(ENV.PMUS_STATE_ROOTS)})


# --------------------------------------------------------------- the verdict

def verdict(*, expected_sha: Optional[str] = None,
            connectivity: Optional[Callable[[], Any]] = None,
            market: Optional[Dict[str, Any]] = None,
            size: Optional[Dict[str, Any]] = None,
            minimums: Optional[Dict[str, Any]] = None,
            access: Optional[Dict[str, Any]] = None,
            risk: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every gate, then one answer. ANY non-PASS blocks."""
    checks: List[Dict[str, Any]] = [
        serving_sha(expected_sha),
        credentials_present(),
        venue_connectivity(connectivity),
        market_eligibility(market),
        venue_access(market, access),
        order_size_constraints(size, minimums),
        settlement_accounting_path(),
        pmus_separation(),
    ]
    checks.extend(risk_bounds(**(risk or {})))
    blockers = [c for c in checks if c["state"] != "PASS"]
    return {
        "label": LABEL,
        "ok": not blockers,
        "verdict": "CLEAR_TO_MEASURE" if not blockers else "BLOCKED",
        "n_checks": len(checks), "n_blockers": len(blockers),
        "blockers": [{"check": c["check"], "state": c["state"],
                      "blocker": c["blocker"]} for c in blockers],
        "checks": checks,
        "labels": ("passing is PERMISSION TO MEASURE, not an instruction to "
                   "trade, and selects no strategy. UNDETERMINED is a blocker: "
                   "a check that could not be established never passes."),
    }
