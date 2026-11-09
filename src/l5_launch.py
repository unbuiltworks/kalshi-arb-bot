"""ONE OWNER APPROVAL, ONE APPLICATION. The launch orchestrator (#214).

WHAT THIS IS. A composition layer. It owns no limit, no latch, no ceiling
and no decision about whether a trade is wise. Every gate it reports is
read from the accessor that already enforces it:

  `l5_live_authorization.authorization_preflight`   the arming gates
  `l5_pmus_live_runner.operator_halted`             the local halt
  `l5_probe_preflight.preflight`                    the runtime readiness
  `l5_probe_preflight.exact_window_consistency`     market identity
  `l5_campaign_approval.campaign_decision`          the campaign verdict
  `l5_campaign_approval.digest_of`                  the document digest
  `l5_pmus_auth_book.slug_start` / `slug_for`        slug <-> UTC

WHAT IT IS NOT. Not a second controller. It cannot price, submit, cancel or
amend anything -- it holds no transport and no credentials. It cannot widen
a limit: the only variables it will ever write are in `WRITABLE`, and that
tuple contains no limit, no taker switch and no capital figure.

THE INCIDENT THAT SHAPED IT, 2026-11-09 08:30Z. An owner-approved window
produced zero orders because `authorization_preflight` reported `ok true`
while `Runner.try_arm` refused 162 times on the local operator halt -- a
prerequisite the readiness artifact did not evaluate. So:

  * a gate this cannot read is BLOCKED, never READY. UNKNOWN is not a pass.
  * `market=null` readiness is NOT readiness. `prepare` refuses to return
    READY_FOR_APPROVAL without a market-specific verdict, because the
    startup preflight evaluates the document alone and says so itself.
  * the local halt and the independent watchdog latch are reported as TWO
    gates. Either alone stops arming, and in that incident the latch was
    CLEAR while the local halt was engaged.

THE APPROVAL BOUNDARY, AND IT IS NOT RELAXED. `EnvApprovalStore.put`
refuses: "the executor may not write its own approval". That invariant is
about the TRADING PROCESS and is untouched -- this module is an
operator-side tool and imports no transport. But it still may not MINT an
authorization: `apply` requires the owner to name the exact digest they
approved, recomputes the document, and refuses unless the two match. The
owner's act is the authorization; this only carries it.

A HALT IS NEVER CLEARED SILENTLY. Resetting the operator halt is a separate
explicit request with its own reason, refused unless every precondition in
`HALT_RESET_PRECONDITIONS` is satisfied from the runtime's own telemetry.
`apply` without that request leaves the halt exactly as it is.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from src import l5_campaign_approval as CA
from src import l5_pmus_auth_book as AB
from src import l5_probe_preflight as PF

#: verdicts. There is no third "probably".
READY = "READY_FOR_APPROVAL"
BLOCKED = "BLOCKED"

#: the ONLY variables this will ever write. No limit, no taker switch, no
#: capital figure, no watchdog setting appears here, and
#: `test_the_writable_set_contains_no_limit` asserts that it cannot.
WRITABLE = (
    "L5_CAMPAIGN_APPROVAL",      # the owner's approved document
    "L5_TS_MARKET",              # the exact approved slug
    "L5_TS_EXPERIMENT_ID",       # the probe label
    "L5_LIVE_AUTH_NOT_AFTER",    # the absolute deadline, = campaign expiry
    "L5_SHADOW_ONLY",            # the structural submission lock
)

#: variables this must refuse to write even if asked, named so a future
#: caller cannot quietly extend the surface.
FORBIDDEN = (
    "L5_MAX_TOTAL_EXPOSURE_USD", "L5_DAILY_REALIZED_LOSS_HALT_USD",
    "L5_MAX_CASH_DEPLOYED_USD", "L5_MAX_WORST_CASE_DEFICIT_USD",
    "L5_TS_COMPLETION_CROSS", "L5_TAKER_COMPLETION_POLICY",
    "L5_TS_MAX_PAIR_COST", "L5_MAX_CUMULATIVE_LOSS_USD",
    "L5_LIVE_HALT", "PMUS_Q1_ARM", "PMUS_Q1_LIVE", "PMUS_Q1_SESSION_ID",
    "L5_HALT_URL", "L5_CAMPAIGN_BUDGET_USD",
)

#: what must hold, from the runtime's OWN telemetry, before an operator
#: halt may be reset. Each is a heartbeat path, so every one is verifiable
#: before anything is changed. See the 08:30Z incident record.
HALT_RESET_PRECONDITIONS = (
    ("watchdog_latch_clear", "submission_gate.halt_latch == CLEAR"),
    ("no_live_orders", "order_progress.live_orders == 0"),
    ("no_parked_orders", "order_progress.parked_orders == 0"),
    ("nothing_pending_terminal", "pending_terminal is empty"),
    ("nothing_quarantined", "quarantined is empty"),
    ("accounting_resolved", "unresolved_accounting.count == 0"),
    ("exposure_zero", "account_exposure.account_total_worst_case_usd == 0"),
    ("recorder_durable", "fill_calibration.readback.readable is true"),
)


def _get(obj: Any, path: str) -> Any:
    """A dotted read that distinguishes absent from false. None is ABSENT."""
    cur = obj
    for part in str(path).split("."):
        if not isinstance(cur, Mapping):
            return None
        if part not in cur:
            return None
        cur = cur[part]
    return cur


def _config_rows(cfg: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """`L5_EFFECTIVE_RUNTIME_CONFIG` flattened to name -> effective.

    Reads both `rows` (graded) and `observed_not_graded`, because the taker
    switches live in the second: the verifier deliberately does not grade
    an activation switch, since grading it would fail at the moment
    activation succeeds. Empty mapping for anything unreadable, which the
    caller must treat as UNKNOWN rather than as absent-and-fine.
    """
    if not isinstance(cfg, Mapping):
        return {}
    out: Dict[str, Any] = {}
    for key in ("rows", "observed_not_graded"):
        for row in (cfg.get(key) or ()):
            if isinstance(row, Mapping) and row.get("name") is not None:
                out[str(row["name"])] = row.get("effective")
    return out


def gate(name: str, ok: Any, **detail: Any) -> Dict[str, Any]:
    """One gate. `ok` is True, False, or PF.UNKNOWN -- never a truthy
    stand-in, because UNKNOWN must not collapse into either answer."""
    return dict({"gate": name, "ok": ok}, **detail)


def _iso(d: dt.datetime) -> str:
    return d.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ===================================================================
# THE CANONICAL DOCUMENT
# ===================================================================
def build_document(*, window_start: dt.datetime, now: dt.datetime,
                   max_windows: int = 1,
                   max_total_exposure_usd: str = "1.00",
                   max_daily_loss_usd: str = "5.00",
                   series: str = "cpc-btc-updown-15m-",
                   nonce: Optional[str] = None,
                   settle_grace_min: int = 30) -> Dict[str, Any]:
    """The nine-field approval, bound to ONE window start.

    DETERMINISTIC given its inputs, so the digest the owner approves is the
    digest that gets published. `window_starts` is an exact-match
    allowlist, which is what makes this a binding: every other window is
    refused WINDOW_NOT_IN_AUTHORIZED_SET by the authorization layer itself.

    It asks for no more than the code permits. The ceilings are code
    constants and the effective scope is the intersection, so a document
    cannot widen anything -- but asking for more produces a `clamped` flag
    and an argument about intent, so this refuses to construct one.
    """
    for field, asked, ceiling in (
            ("max_total_exposure_usd", max_total_exposure_usd,
             CA.MAX_TOTAL_EXPOSURE_USD),
            ("max_daily_loss_usd", max_daily_loss_usd,
             CA.MAX_DAILY_LOSS_USD),
            ("max_windows", max_windows, CA.MAX_WINDOWS)):
        if float(str(asked)) > float(str(ceiling)):
            raise ValueError(
                "%s asks %s above the code ceiling %s" % (field, asked,
                                                          ceiling))
    if series not in CA.ALLOWED_SERIES:
        raise ValueError("series %r is not in ALLOWED_SERIES" % (series,))
    return {
        "approved": True,
        "nonce": nonce or ("probe-%s" % window_start.strftime("%Y%m%d-%H%Mz")),
        "series": series,
        "issued_utc": _iso(now),
        "expires_utc": _iso(window_start
                            + dt.timedelta(minutes=settle_grace_min)),
        "max_windows": int(max_windows),
        "max_total_exposure_usd": str(max_total_exposure_usd),
        "max_daily_loss_usd": str(max_daily_loss_usd),
        "window_starts": [_iso(window_start)],
    }


def document_line(document: Mapping[str, Any]) -> str:
    """The EXACT bytes to publish, one line, compact separators.

    The gate hashes what is STORED, not a canonical form, so the digest is
    a property of this string and not of the mapping. Reformatting changes
    the digest, which is why the line and the digest travel together.
    """
    return json.dumps(dict(document), separators=(",", ":"))


def document_digest(document: Mapping[str, Any]) -> str:
    """The digest of `document_line`, via the campaign module's own hasher
    so this cannot drift from what the gate reports."""
    return CA.digest_of(document_line(document).encode("utf-8")) or ""


def next_full_window(now: dt.datetime, *, lead_min: int) -> dt.datetime:
    """The next 15-minute boundary at least `lead_min` minutes away."""
    start = now.replace(minute=(now.minute // 15) * 15, second=0,
                        microsecond=0)
    while (start - now).total_seconds() < lead_min * 60:
        start += dt.timedelta(minutes=15)
    return start


def slug_for_window(start: dt.datetime) -> Optional[str]:
    """Our slug for a window start, via the authoritative builder."""
    try:
        return AB.slug_for(int(start.timestamp()))
    except BaseException:                                      # noqa: BLE001
        return None


# ===================================================================
# PHASE 1 -- PREPARE. READ-ONLY.
# ===================================================================
def prepare(*, heartbeat: Mapping[str, Any],
            deployment: Mapping[str, Any],
            expected_sha: str,
            window_start: dt.datetime,
            now: dt.datetime,
            auth_preflight: Mapping[str, Any],
            operator_halted: bool,
            calibration: Optional[Mapping[str, Any]] = None,
            plan: Optional[Mapping[str, Any]] = None,
            venue_confirmed_bounds: Optional[Mapping[str, Any]] = None,
            document_kw: Optional[Mapping[str, Any]] = None,
            runtime_config: Optional[Mapping[str, Any]] = None,
            ) -> Dict[str, Any]:
    """ONE verdict: READY_FOR_APPROVAL or BLOCKED, with every gate named.

    WRITES NOTHING. It takes the runtime's own telemetry as arguments
    rather than reaching for it, so it is deterministic, testable, and
    incapable of changing the thing it is judging.

    EVERY gate is reported, pass or fail, because a check that appears only
    when it fails cannot be confirmed as having run -- which is how the
    08:30Z window was lost.
    """
    gates: List[Dict[str, Any]] = []
    slug = slug_for_window(window_start)

    # ---- 1. the exact market and window
    gates.append(gate("window_slug_derived", slug is not None, slug=slug))
    rt = None
    if slug:
        try:
            rt = AB.slug_start(slug)
        except BaseException:                                  # noqa: BLE001
            rt = None
    gates.append(gate(
        "slug_round_trips_to_window_start",
        rt is not None and abs(float(rt) - window_start.timestamp()) < 1.0,
        slug=slug, parsed_epoch=None if rt is None else int(rt),
        window_start_utc=_iso(window_start)))
    lead = (window_start - now).total_seconds()
    gates.append(gate("window_is_in_the_future", lead > 0,
                      lead_s=round(lead, 1)))

    # VENUE CONFIRMATION. The venue's own startTime for this slug, as
    # `venue_window_bounds` obtains it. UNKNOWN before the window exists is
    # honest -- and it is NOT a pass. The arming boundary refuses
    # WINDOW_BOUNDS_UNKNOWN without it, so this cannot be waved through.
    vb = venue_confirmed_bounds or {}
    if not vb:
        gates.append(gate("venue_confirmed_window_bounds", PF.UNKNOWN,
                          why=("the venue confirms a window at the window; "
                               "arming refuses WINDOW_BOUNDS_UNKNOWN "
                               "without it"),
                          blocks="authorize_arming step 7"))
    else:
        gates.append(gate(
            "venue_confirmed_window_bounds",
            str(vb.get("source")) == "VENUE"
            and str(vb.get("start", ""))[:19] == _iso(window_start)[:19],
            source=vb.get("source"), start=vb.get("start"),
            end=vb.get("end"), confirmed_by=vb.get("confirmed_by")))

    # ---- 2. the deployment
    got_sha = str(deployment.get("commitHash") or deployment.get("sha") or "")
    gates.append(gate("deployed_sha_matches", bool(expected_sha)
                      and got_sha == str(expected_sha),
                      expected=str(expected_sha)[:12],
                      deployed=got_sha[:12] or None))
    gates.append(gate("deployment_succeeded",
                      str(deployment.get("status")) == "SUCCESS",
                      status=deployment.get("status"),
                      deployment_id=deployment.get("id")))
    hb_utc = _get(heartbeat, "utc")
    age = None
    if hb_utc:
        try:
            age = (now - dt.datetime.fromisoformat(
                str(hb_utc).replace("Z", "+00:00"))).total_seconds()
        except BaseException:                                  # noqa: BLE001
            age = None
    gates.append(gate("heartbeat_is_fresh",
                      age is not None and 0 <= age <= 180,
                      heartbeat_utc=hb_utc, age_s=None if age is None
                      else round(age, 1)))

    # ---- 3. BOTH halts, as two gates. The 08:30Z lesson.
    gates.append(gate("local_operator_halt_clear", operator_halted is False,
                      engaged=bool(operator_halted), env="L5_LIVE_HALT",
                      blocks="Runner.try_arm returns before step 1",
                      note=("distinct from the independent watchdog latch; "
                            "either alone stops arming")))
    latch = _get(heartbeat, "submission_gate.halt_latch")
    gates.append(gate("watchdog_latch_clear", latch == "CLEAR",
                      halt_latch=latch,
                      digest=_get(heartbeat,
                                  "submission_gate.halt_latch_telemetry"
                                  ".sha256"),
                      note="the INDEPENDENT latch, read over HTTP"))

    # ---- 4. the rest of the runtime
    gates.append(gate("q1_handshake_present",
                      _get(heartbeat, "submission_gate.owner_arm_token")
                      is True,
                      owner_arm_token=_get(heartbeat,
                                           "submission_gate.owner_arm_token")))
    gates.append(gate("no_live_orders",
                      _get(heartbeat, "order_progress.live_orders") == 0,
                      live_orders=_get(heartbeat,
                                       "order_progress.live_orders"),
                      parked=_get(heartbeat,
                                  "order_progress.parked_orders")))
    gates.append(gate("accounting_resolved",
                      _get(heartbeat, "unresolved_accounting.count") == 0,
                      count=_get(heartbeat, "unresolved_accounting.count")))
    gates.append(gate("exposure_within_cap",
                      _get(heartbeat, "account_exposure.ok") is True,
                      total_worst_case_usd=_get(
                          heartbeat,
                          "account_exposure.account_total_worst_case_usd"),
                      cap=_get(heartbeat,
                               "account_exposure.max_total_exposure_usd")))
    gates.append(gate("daily_loss_room",
                      _get(heartbeat, "day_realized.would_halt") is False,
                      room_usd=_get(heartbeat, "day_realized.room_usd"),
                      today_usd=_get(heartbeat,
                                     "day_realized.utc_day_realized_usd")))
    # TAKER, GRADED FROM THE RUNTIME CONFIG AND NOT INVENTED HERE.
    #
    # A CORRECTION OF MY OWN FIRST DRAFT, which graded this on
    # `submission_gate.blocked is not None` -- a condition that is true
    # whenever the heartbeat is readable at all and says nothing whatever
    # about taker execution. A gate that passes for the wrong reason is
    # worse than an absent one, and it is the same defect class that lost
    # the 08:30Z window.
    #
    # The four taker facts live on `L5_EFFECTIVE_RUNTIME_CONFIG`, in
    # `observed_not_graded`, each the value the money path's own accessor
    # returns. Absent config is UNKNOWN, which is not a pass.
    _tk = _config_rows(runtime_config)
    if not _tk:
        gates.append(gate("taker_unreachable", PF.UNKNOWN,
                          why="no L5_EFFECTIVE_RUNTIME_CONFIG supplied"))
    else:
        _names = ("taker_policy_enabled",
                  "taker_policy_owner_cross_authorized",
                  "taker_crossing_reachable",
                  "defensive_execution_enabled")
        _vals = {n: _tk.get(n) for n in _names}
        _missing = [n for n, v in _vals.items() if v is None]
        if _missing:
            gates.append(gate("taker_unreachable", PF.UNKNOWN,
                              missing=_missing, observed=_vals))
        else:
            gates.append(gate("taker_unreachable",
                              all(v is False for v in _vals.values()),
                              observed=_vals,
                              note=("every taker path must read false; "
                                    "any one true is BLOCKED")))
    # submission budget headroom, from the campaign's own counters
    spent = _get(heartbeat, "campaign.submissions_spent")
    cap = _get(heartbeat, "campaign.max_submissions")
    gates.append(gate("submission_budget_remaining",
                      isinstance(spent, int) and isinstance(cap, int)
                      and spent < cap,
                      spent=spent, max=cap))

    # ---- 5. the document
    doc = None
    line = None
    digest = None
    try:
        doc = build_document(window_start=window_start, now=now,
                             **dict(document_kw or {}))
        line = document_line(doc)
        digest = document_digest(doc)
        gates.append(gate("document_constructed", True, digest=digest,
                          bytes=len(line)))
    except BaseException as exc:                               # noqa: BLE001
        gates.append(gate("document_constructed", False,
                          error="%s: %s" % (type(exc).__name__,
                                            str(exc)[:120])))

    # ---- 6. the runtime readiness preflight (#212's, with the new rows)
    if calibration is not None or plan is not None:
        try:
            sub = PF.preflight(heartbeat=heartbeat, plan=plan,
                               markets=[slug] if slug else [],
                               calibration=calibration)
            gates.append(gate("runtime_preflight", bool(sub.get("ok")),
                              failed=sub.get("failed"),
                              unknown=sub.get("unknown")))
        except BaseException as exc:                           # noqa: BLE001
            gates.append(gate("runtime_preflight", PF.UNKNOWN,
                              error=type(exc).__name__))
    else:
        gates.append(gate("runtime_preflight", PF.UNKNOWN,
                          why="no calibration or plan supplied"))

    # ---- 7. the arming preflight, MARKET-SPECIFIC
    #
    # A STARTUP VERDICT IS NOT READINESS. `authorization_preflight` with no
    # slug evaluates the campaign DOCUMENT alone and reports coverage as
    # NOT_EVALUATED -- its own labels say so. Treating that as ready is how
    # a window gets approved against a verdict that never saw it.
    evaluated = _get(auth_preflight, "evaluated_market")
    gates.append(gate("auth_preflight_is_market_specific",
                      bool(evaluated) and evaluated == slug,
                      evaluated_market=evaluated, expected=slug,
                      note=("a market=null startup verdict is NEVER "
                            "readiness")))
    gates.append(gate("auth_preflight_ok",
                      auth_preflight.get("ok") is True,
                      fail=auth_preflight.get("fail"),
                      unknown=auth_preflight.get("unknown"),
                      fail_reasons=auth_preflight.get("fail_reasons")))

    verdict = READY if all(g["ok"] is True for g in gates) else BLOCKED
    blocking = [g["gate"] for g in gates if g["ok"] is not True]
    return {
        "verdict": verdict,
        "blocking": blocking,
        "gates": gates,
        "market": slug,
        "window_start_utc": _iso(window_start),
        "lead_s": round(lead, 1),
        "document": doc,
        "document_line": line,
        "document_digest": digest,
        "variables": ({} if not (doc and slug) else {
            "L5_CAMPAIGN_APPROVAL": line,
            "L5_TS_MARKET": slug,
            "L5_TS_EXPERIMENT_ID": str(doc["nonce"]),
            "L5_LIVE_AUTH_NOT_AFTER": str(doc["expires_utc"]).replace(
                "Z", "+00:00"),
        }),
        "labels": ("READ-ONLY. Writes nothing, arms nothing, reserves "
                   "nothing. UNKNOWN is never READY. A market=null "
                   "authorization verdict is never READY."),
    }


# ===================================================================
# PHASE 2 -- APPLY. ONE OWNER APPROVAL, CARRIED NOT MINTED.
# ===================================================================
class ApplyRefused(RuntimeError):
    """Nothing was applied. Carries the reason and what was expected."""

    def __init__(self, reason: str, detail: Any = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def apply_plan(*, prepared: Mapping[str, Any],
               approved_digest: str,
               set_variables: Callable[[Mapping[str, str]], Any],
               read_back: Callable[[], Mapping[str, Any]],
               unlock_shadow: bool = False,
               reset_operator_halt: Optional[str] = None,
               halt_reset_evidence: Optional[Mapping[str, Any]] = None,
               ) -> Dict[str, Any]:
    """Publish the approved document and apply the bound configuration.

    THE APPROVAL IS THE OWNER'S ACT, AND THIS ONLY CARRIES IT. The caller
    must name the digest the owner approved. The document is rebuilt from
    `prepared` and refused unless the digests match exactly, so this cannot
    publish anything the owner did not see. It MINTS NOTHING.

    IT REFUSES A BLOCKED PLAN. A `prepare` verdict other than
    READY_FOR_APPROVAL means a gate did not pass, and applying over it
    would be the 08:30Z failure with extra steps.

    IDEMPOTENT. Re-applying the same approved digest re-writes the same
    values and reports `changed: false`, so a retry after a timeout is
    safe and a double approval cannot spend anything twice. The window
    ledger, not this, counts windows.

    TWO THINGS IT WILL NOT DO WITHOUT BEING ASKED, SEPARATELY, EACH TIME:

      * `unlock_shadow` -- clearing the structural submission lock. Absent,
        `L5_SHADOW_ONLY` is not written at all and the lock stands.
      * `reset_operator_halt` -- a reason string, plus
        `halt_reset_evidence` satisfying every HALT_RESET_PRECONDITION from
        the runtime's own telemetry. A halt is never cleared silently and
        never as a side effect of launching.

    IT CANNOT WIDEN ANYTHING. Only `WRITABLE` names are ever sent, every
    name is checked against `FORBIDDEN`, and no limit, taker switch or
    capital figure is in either list's writable half.
    """
    if str(prepared.get("verdict")) != READY:
        raise ApplyRefused("PLAN_NOT_READY",
                           {"verdict": prepared.get("verdict"),
                            "blocking": prepared.get("blocking")})
    doc = prepared.get("document")
    if not isinstance(doc, Mapping):
        raise ApplyRefused("NO_DOCUMENT_IN_PLAN")
    recomputed = document_digest(doc)
    if not approved_digest or recomputed != str(approved_digest).strip():
        raise ApplyRefused("APPROVED_DIGEST_MISMATCH",
                           {"approved": str(approved_digest)[:64],
                            "recomputed": recomputed,
                            "note": ("the owner approves a digest; this "
                                     "publishes only that document")})

    variables = dict(prepared.get("variables") or {})
    if not variables:
        raise ApplyRefused("NO_VARIABLES_IN_PLAN")

    steps: List[Dict[str, Any]] = []

    # ---- the halt reset, if and only if explicitly requested
    halt_reset = None
    if reset_operator_halt is not None:
        if not str(reset_operator_halt).strip():
            raise ApplyRefused("HALT_RESET_NEEDS_A_REASON")
        ev = halt_reset_evidence or {}
        unmet = [name for name, _ in HALT_RESET_PRECONDITIONS
                 if ev.get(name) is not True]
        if unmet:
            raise ApplyRefused("HALT_RESET_PRECONDITIONS_UNMET",
                               {"unmet": unmet,
                                "required": [n for n, _ in
                                             HALT_RESET_PRECONDITIONS]})
        halt_reset = {"requested": True,
                      "reason": str(reset_operator_halt)[:200],
                      "preconditions_met": [n for n, _ in
                                            HALT_RESET_PRECONDITIONS]}
        steps.append({"step": "operator_halt_reset_authorized",
                      "detail": halt_reset,
                      "note": ("the variable itself is cleared by the "
                               "owner-facing apply below; this records "
                               "that it was explicitly requested and "
                               "every precondition held")})
        variables["L5_LIVE_HALT"] = ""

    if unlock_shadow:
        variables["L5_SHADOW_ONLY"] = "0"
        steps.append({"step": "shadow_unlock_requested",
                      "note": ("the structural submission lock is cleared "
                               "ONLY within the approved scope; absent "
                               "this flag it is not written at all")})

    # ---- the writable-surface check, before anything leaves
    for name in variables:
        if name in FORBIDDEN and name != "L5_LIVE_HALT":
            raise ApplyRefused("FORBIDDEN_VARIABLE", {"name": name})
        if name not in WRITABLE and name != "L5_LIVE_HALT":
            raise ApplyRefused("VARIABLE_NOT_IN_WRITABLE_SET",
                               {"name": name, "writable": list(WRITABLE)})

    steps.append({"step": "set_variables",
                  "names": sorted(variables),
                  "digest_published": recomputed})
    set_variables(dict(variables))

    # ---- read back and verify the digest the runtime actually holds
    rb = dict(read_back() or {})
    seen = str(_get(rb, "campaign.document_sha256") or "")
    ok = seen == recomputed
    steps.append({"step": "verify_durable_read_back",
                  "expected_digest": recomputed,
                  "observed_digest": seen or None,
                  "approval_state": _get(rb, "campaign.approval_state"),
                  "ok": ok})
    if not ok:
        raise ApplyRefused("READ_BACK_DIGEST_MISMATCH",
                           {"expected": recomputed, "observed": seen or None,
                            "note": ("the gate hashes what is STORED; a "
                                     "reformatted document is a different "
                                     "document")})
    return {"applied": True,
            "digest": recomputed,
            "market": prepared.get("market"),
            "window_start_utc": prepared.get("window_start_utc"),
            "variables_written": sorted(variables),
            "shadow_unlocked": bool(unlock_shadow),
            "operator_halt_reset": halt_reset,
            "steps": steps,
            "labels": ("the owner's approval was carried, not minted. No "
                       "limit, taker switch or capital figure was written.")}


# ===================================================================
# PHASE 3 -- THE WINDOW, AND SHUTTING IT DOWN
# ===================================================================
def execution_report(*, heartbeat: Mapping[str, Any],
                     market: str,
                     orders: Sequence[Mapping[str, Any]] = (),
                     ) -> Dict[str, Any]:
    """What actually happened, with the five states kept apart.

    WOULD_SUBMIT, submitted, acknowledged, filled and settled are counted
    separately and never summed. A decision is not an order, an
    acknowledgement is not a fill, and a fill is not settled money -- this
    programme has mislabelled each of those at least once, so the report
    refuses to collapse them.
    """
    decided = sum(1 for o in orders if o.get("decision"))
    would = sum(1 for o in orders
                if str(o.get("state")) == "WOULD_SUBMIT")
    submitted = [o for o in orders if o.get("submitted") is True]
    acked = [o for o in submitted if o.get("venue_order_id")]
    filled = [o for o in acked if _num(o.get("filled_qty")) > 0]
    cancelled = [o for o in submitted if o.get("cancelled") is True]
    up = sum(_num(o.get("filled_qty")) for o in filled
             if str(o.get("side")) == "UP")
    dn = sum(_num(o.get("filled_qty")) for o in filled
             if str(o.get("side")) == "DOWN")
    matched = min(up, dn)
    return {
        "market": market,
        "decisions": decided,
        "would_submit": would,
        "orders_submitted": len(submitted),
        "venue_acknowledged": len(acked),
        "orders_with_fills": len(filled),
        "cancellations": len(cancelled),
        "filled_up": str(up), "filled_down": str(dn),
        "matched_pairs": str(matched),
        "unmatched_residual": str(abs(up - dn)),
        "settled_realized_net_usd": _get(heartbeat,
                                         "realized.realized_total"),
        "settled_today_usd": _get(heartbeat,
                                  "day_realized.utc_day_realized_usd"),
        "verified_l5_status": _get(heartbeat, "realized.verified_l5_status"),
        "labels": ("five states, never collapsed. A WOULD_SUBMIT is not an "
                   "order, an acknowledgement is not a fill, and a fill is "
                   "not settled money. settled_realized_net_usd is the "
                   "ACCOUNT figure and is not this window's attribution."),
    }


def _num(v: Any) -> float:
    try:
        return float(str(v))
    except BaseException:                                      # noqa: BLE001
        return 0.0


def shutdown_plan(*, heartbeat: Mapping[str, Any],
                  now: dt.datetime,
                  window_start: dt.datetime,
                  expires_utc: Optional[str] = None,
                  window_s: float = 900.0) -> Dict[str, Any]:
    """What must happen at the end of authorization, and whether it is due.

    AUTHORIZATION ENDS AT THE EARLIER OF the window's end and the
    campaign's expiry. Rolling into the next window is not an extension
    this may grant: a new window needs a new approval, and the exact-match
    `window_starts` allowlist refuses it anyway.

    RESTORING THE LOCK IS THE DEFAULT, not a courtesy. The structural
    submission lock goes back on; if anything is still outstanding the plan
    says so and requires cancellation and reconciliation first, because a
    lock does not flatten a resting order.
    """
    end = window_start + dt.timedelta(seconds=float(window_s))
    exp = None
    if expires_utc:
        try:
            exp = dt.datetime.fromisoformat(
                str(expires_utc).replace("Z", "+00:00"))
        except BaseException:                                  # noqa: BLE001
            exp = None
    deadline = min([t for t in (end, exp) if t is not None])
    live = _get(heartbeat, "order_progress.live_orders")
    parked = _get(heartbeat, "order_progress.parked_orders")
    uncertain = _get(heartbeat, "unresolved_accounting.count")
    actions: List[Dict[str, Any]] = []
    if live not in (0, None) or parked not in (0, None):
        actions.append({"action": "cancel_outstanding_by_exact_id",
                        "live_orders": live, "parked": parked,
                        "note": ("a lock blocks submission and does NOT "
                                 "flatten a resting order")})
    actions.append({"action": "verify_terminal_state",
                    "note": "an unverifiable cancellation marks UNCERTAIN"})
    if uncertain not in (0, None):
        actions.append({"action": "halt_reentry_pending_reconciliation",
                        "unresolved": uncertain})
    actions.append({"action": "restore_structural_submission_lock",
                    "variable": "L5_SHADOW_ONLY", "value": "1"})
    actions.append({"action": "emit_final_execution_report"})
    return {
        "due": now >= deadline,
        "deadline_utc": _iso(deadline),
        "deadline_is": ("campaign_expiry" if exp is not None
                        and deadline == exp else "window_end"),
        "window_end_utc": _iso(end),
        "campaign_expiry_utc": None if exp is None else _iso(exp),
        "outstanding_clear": (live in (0, None) and parked in (0, None)),
        "actions": actions,
        "labels": ("authorization ends at the EARLIER of the window end and "
                   "the campaign expiry. No rollover, no extension, no "
                   "capital increase without a fresh approval."),
    }
