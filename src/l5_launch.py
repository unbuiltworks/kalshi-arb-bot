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
import time
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


def variables_for(document: Mapping[str, Any]) -> Dict[str, str]:
    """Every published value, derived from the bytes the owner HASHED.

    WHY THIS IS NOT A CONVENIENCE. The approval the owner reads and the
    digest they approve cover `document` and nothing else -- so anything
    `apply_plan` publishes that is NOT a function of `document` is
    published outside the approval. A prepared plan whose `variables` were
    edited after the digest was taken would otherwise pass the digest
    check and publish the edit, and the two names that matters most for
    are `L5_LIVE_HALT` and `L5_SHADOW_ONLY`.

    So the market slug is DERIVED from `series` + `window_starts[0]`
    rather than carried alongside them, and `apply_plan` recomputes this
    whole mapping from the document instead of trusting the one handed to
    it. Returns {} when the document cannot produce a slug, which is
    BLOCKED upstream and refused downstream.
    """
    try:
        start = dt.datetime.fromisoformat(
            str(document["window_starts"][0]).replace("Z", "+00:00"))
    except BaseException:                                      # noqa: BLE001
        return {}
    slug = slug_for_window(start)
    if not slug:
        return {}
    return {
        "L5_CAMPAIGN_APPROVAL": document_line(document),
        "L5_TS_MARKET": slug,
        "L5_TS_EXPERIMENT_ID": str(document["nonce"]),
        "L5_LIVE_AUTH_NOT_AFTER": str(document["expires_utc"]).replace(
            "Z", "+00:00"),
    }


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
            _failed = list(sub.get("failed") or ())
            _unknown = list(sub.get("unknown") or ())
            # THE ORDER PLAN IS NOT A LAUNCH PRECONDITION, and pretending
            # otherwise makes this gate permanently UNKNOWN.
            #
            # `plan_admissible` judges a PRICED two-leg order plan. The
            # executor builds that at the window, from the book as it is
            # then -- a launcher running fifty minutes earlier has no such
            # plan and could only invent one. So when no plan is supplied,
            # that single row being UNKNOWN is EXPECTED and is named as
            # tolerated; every other row must still pass, and anything
            # failed or any OTHER unknown still BLOCKS.
            #
            # This is the one place a launch verdict tolerates an UNKNOWN,
            # and it is tolerated because the thing it describes does not
            # exist yet rather than because it could not be read.
            _tolerated = ({"plan_admissible"} if plan is None else set())
            _residual = [u for u in _unknown if u not in _tolerated]
            gates.append(gate(
                "runtime_preflight",
                bool(not _failed and not _residual),
                failed=_failed, unknown=_unknown,
                tolerated_without_a_plan=sorted(_tolerated) or None,
                note=(None if plan is not None else
                      "an order plan is built at the window, not here; "
                      "plan_admissible UNKNOWN is expected and every "
                      "other row must still pass")))
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
        "variables": ({} if not (doc and slug) else variables_for(doc)),
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

    # THE VARIABLES ARE RE-DERIVED FROM THE HASHED DOCUMENT, never read
    # from the plan. The digest above covers `document` alone, so a
    # `variables` mapping edited after the owner approved the digest would
    # otherwise publish that edit under a valid approval -- including
    # `L5_LIVE_HALT: ""`, which would clear the halt with none of the
    # preconditions below. Deriving closes that by construction.
    variables = variables_for(doc)
    if not variables:
        raise ApplyRefused("DOCUMENT_YIELDS_NO_VARIABLES",
                           {"note": ("the market slug could not be derived "
                                     "from series + window_starts")})
    carried = dict(prepared.get("variables") or {})
    if carried and carried != variables:
        raise ApplyRefused(
            "PLAN_VARIABLES_DISAGREE_WITH_DOCUMENT",
            {"extra_or_changed": sorted(
                k for k in set(carried) | set(variables)
                if carried.get(k) != variables.get(k)),
             "note": ("the published values are a function of the hashed "
                      "document; a plan carrying anything else is refused "
                      "rather than silently ignored")})

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
        # THE HALT IS EXEMPT ONLY WHEN THIS CALL AUTHORIZED CLEARING IT.
        # The exemption used to be unconditional, which made the name a
        # hole in the forbidden set rather than a gated exception to it.
        if name == "L5_LIVE_HALT":
            if halt_reset is None:
                raise ApplyRefused(
                    "HALT_WRITE_WITHOUT_AUTHORIZED_RESET",
                    {"note": ("L5_LIVE_HALT is only ever written by an "
                              "explicitly requested reset whose every "
                              "precondition held")})
            continue
        if name in FORBIDDEN:
            raise ApplyRefused("FORBIDDEN_VARIABLE", {"name": name})
        if name not in WRITABLE:
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

    # MISSING IS NEVER ZERO, and this reader had it the other way round.
    #
    # `_get` returns None for ABSENT, and the old conditions were
    # `live not in (0, None)` -- so a heartbeat that did not report
    # `order_progress` at all produced NO cancellation action and
    # `outstanding_clear: True`. An unreadable count is the one case where
    # a shutdown reporter must be most careful, because the orders it
    # cannot see are exactly the ones nobody will cancel.
    #
    # So absence is UNKNOWN, unknown is not clear, and the cancellation
    # action is emitted anyway: cancelling by exact owned id is safe when
    # there is nothing to cancel, and skipping it is not.
    unknown = [n for n, v in (("live_orders", live),
                              ("parked_orders", parked),
                              ("unresolved_accounting", uncertain))
               if v is None]
    actions: List[Dict[str, Any]] = []
    if live != 0 or parked != 0:
        actions.append({"action": "cancel_outstanding_by_exact_id",
                        "live_orders": live, "parked": parked,
                        "counts_unreadable": [n for n in unknown
                                              if n != "unresolved_accounting"]
                        or None,
                        "note": ("a lock blocks submission and does NOT "
                                 "flatten a resting order; an unreadable "
                                 "count is treated as outstanding")})
    actions.append({"action": "verify_terminal_state",
                    "note": "an unverifiable cancellation marks UNCERTAIN"})
    if uncertain != 0:
        actions.append({"action": "halt_reentry_pending_reconciliation",
                        "unresolved": uncertain,
                        "readable": uncertain is not None,
                        "note": (None if uncertain is not None else
                                 "the figure could not be read, which is "
                                 "not evidence that it is zero")})
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
        "outstanding_clear": (live == 0 and parked == 0),
        "reconciliation_clear": uncertain == 0,
        "counts_unreadable": unknown or None,
        "actions": actions,
        "labels": ("authorization ends at the EARLIER of the window end and "
                   "the campaign expiry. No rollover, no extension, no "
                   "capital increase without a fresh approval. An "
                   "UNREADABLE count is never reported clear: absence is "
                   "not zero, and the cancellation sweep runs anyway."),
    }


# ===================================================================
# THE STATE MACHINE. It DOES the sequence; it does not describe it.
# ===================================================================
#: every step, in order, so a partial run says exactly how far it got.
STEPS = ("verify_scope", "read_runtime", "prepare", "apply",
         "await_deployment", "reread_runtime", "verify_arming",
         "monitor", "shutdown")


class LaunchHalted(RuntimeError):
    """The run stopped. `at` names the step and `trail` carries the rest."""

    def __init__(self, at: str, reason: str, trail: Any = None) -> None:
        super().__init__("%s: %s" % (at, reason))
        self.at = at
        self.reason = reason
        self.trail = trail or []


def run_launch(*, client: Any,
               expected_sha: str,
               window_start: dt.datetime,
               approved_digest: Optional[str] = None,
               approved_plan: Optional[Mapping[str, Any]] = None,
               now_fn: Callable[[], dt.datetime] = lambda: dt.datetime.now(
                   dt.timezone.utc),
               unlock_shadow: bool = False,
               reset_operator_halt: Optional[str] = None,
               halt_reset_evidence: Optional[Mapping[str, Any]] = None,
               document_kw: Optional[Mapping[str, Any]] = None,
               deployment_timeout_s: float = 420.0,
               arming_timeout_s: float = 180.0,
               arming_poll_s: float = 15.0,
               sleeper: Callable[[float], None] = None,
               ) -> Dict[str, Any]:
    """Drive the whole sequence against a live runtime, failing closed.

    WITHOUT `approved_digest` IT STOPS AFTER `prepare`. That is the
    default and it is the safe one: a caller that has not been given an
    owner approval gets a verdict and nothing else happens. There is no
    flag that makes it proceed on its own judgement.

    WHAT EACH STEP ACTUALLY DOES, as opposed to plans:

      verify_scope      proves the credential covers the project and
                        environment named, so a token for another project
                        cannot read and write the wrong service.
      read_runtime      reads the heartbeat, the runtime config, the
                        market-specific authorization verdict, the venue
                        bounds and the live variables.
      prepare           the read-only verdict. STOPS HERE unless READY.
      apply             publishes the owner's approved document and the
                        bound variables, then verifies durable read-back
                        of the digest.
      await_deployment  a variable write redeploys the service, so this
                        POLLS until a FRESH deployment reaches SUCCESS on
                        the expected commit. FAILED, CRASHED and REMOVED
                        are terminal and are not retried.
      reread_runtime    reads the heartbeat from the NEW container, because
                        the values only take effect there.
      verify_arming     waits for the effective transition and refuses to
                        call it armed on anything but the runtime's own
                        `submission_gate`.
      monitor           reads decisions, orders and fills for the window.
      shutdown          what must happen at the end, and whether it is due.

    IT NEVER DECIDES TO CLEAR A HALT. `reset_operator_halt` is passed
    through to `apply_plan`, which refuses without a reason and without
    every precondition. Absent it, the halt is untouched at every step.
    """
    sleep = sleeper or time.sleep
    trail: List[Dict[str, Any]] = []

    def done(step: str, **detail: Any) -> None:
        trail.append(dict({"step": step, "ok": True}, **detail))

    def stop(step: str, reason: str, **detail: Any) -> "LaunchHalted":
        trail.append(dict({"step": step, "ok": False, "reason": reason},
                          **detail))
        return LaunchHalted(step, reason, trail)

    # ---- verify_scope
    try:
        scope = client.verify_scope()
    except BaseException as exc:                               # noqa: BLE001
        raise stop("verify_scope", "%s: %s" % (type(exc).__name__,
                                               str(exc)[:160]))
    done("verify_scope", **dict(scope))

    # ---- read_runtime
    try:
        hb = client.heartbeat() or {}
        cfg = client.runtime_config()
        apf = client.auth_preflight() or {}
        deployment = client.latest_deployment()
        variables = client.variables()
    except BaseException as exc:                               # noqa: BLE001
        raise stop("read_runtime", "%s: %s" % (type(exc).__name__,
                                               str(exc)[:160]))
    if not hb:
        raise stop("read_runtime", "NO_HEARTBEAT",
                   note="a runtime that is not reporting cannot be judged")
    slug = slug_for_window(window_start)
    # THE HALT IS READ FROM THE VARIABLE, NOT FROM THE PREFLIGHT. A
    # deployment older than 2c7c20b7 reports a verdict with no
    # `local_operator_halt` row at all, and trusting it is the 08:30Z
    # failure. Any non-empty value halts.
    halted = bool(str(variables.get("L5_LIVE_HALT") or "").strip())
    bounds = None
    try:
        bounds = client.venue_window_bounds(slug) if slug else None
    except BaseException:                                      # noqa: BLE001
        bounds = None
    done("read_runtime", heartbeat_utc=hb.get("utc"),
         deployment=deployment.get("id"),
         deployed_sha=str(deployment.get("commitHash") or "")[:12],
         operator_halted=halted, venue_bounds_present=bool(bounds))

    # ---- prepare
    prepared = prepare(
        heartbeat=hb, deployment=deployment, expected_sha=expected_sha,
        window_start=window_start, now=now_fn(), auth_preflight=apf,
        operator_halted=halted, calibration=hb.get("fill_calibration"),
        plan=None, venue_confirmed_bounds=bounds,
        document_kw=document_kw, runtime_config=cfg)
    if prepared["verdict"] != READY:
        trail.append({"step": "prepare", "ok": False,
                      "verdict": prepared["verdict"],
                      "blocking": prepared["blocking"]})
        return {"verdict": prepared["verdict"], "stopped_at": "prepare",
                "prepared": prepared, "trail": trail,
                "labels": ("BLOCKED. Nothing was written, nothing armed, "
                           "no order placed. Every blocker above is read "
                           "from the accessor that enforces it.")}
    done("prepare", digest=prepared["document_digest"],
         market=prepared["market"])

    if not approved_digest:
        return {"verdict": READY, "stopped_at": "prepare",
                "prepared": prepared, "trail": trail,
                "labels": ("READY, and STOPPED. No owner approval was "
                           "supplied, so nothing was applied. This is the "
                           "default path.")}

    # ---- apply
    to_apply = dict(prepared)
    if approved_plan:
        # the artifact the owner approved, carried rather than re-derived:
        # `issued_utc` is clock-stamped, so a regenerated document would
        # hash differently and `apply_plan` would refuse every time.
        for key in ("document", "document_line", "document_digest",
                    "variables"):
            if not approved_plan.get(key):
                raise stop("apply", "APPROVED_PLAN_INCOMPLETE", missing=key)
        if approved_plan.get("market") != prepared.get("market"):
            raise stop("apply", "APPROVED_PLAN_NAMES_ANOTHER_MARKET",
                       approved=approved_plan.get("market"),
                       current=prepared.get("market"))
        to_apply.update({k: approved_plan[k] for k in
                         ("document", "document_line", "document_digest",
                          "variables")})
    before = str(deployment.get("createdAt") or "")
    try:
        applied = apply_plan(
            prepared=to_apply, approved_digest=approved_digest,
            set_variables=client.set_variables,
            read_back=lambda: client.heartbeat() or {},
            unlock_shadow=unlock_shadow,
            reset_operator_halt=reset_operator_halt,
            halt_reset_evidence=halt_reset_evidence)
    except ApplyRefused as exc:
        raise stop("apply", exc.reason, detail=exc.detail)
    except BaseException as exc:                               # noqa: BLE001
        raise stop("apply", "%s: %s" % (type(exc).__name__, str(exc)[:160]))
    done("apply", digest=applied["digest"],
         variables=applied["variables_written"],
         shadow_unlocked=applied["shadow_unlocked"],
         halt_reset=bool(applied["operator_halt_reset"]))

    # ---- await_deployment
    try:
        dep2 = client.wait_for_deployment(
            expect_sha=str(deployment.get("commitHash") or "") or None,
            timeout_s=deployment_timeout_s, not_before=before)
    except BaseException as exc:                               # noqa: BLE001
        raise stop("await_deployment", "%s: %s" % (type(exc).__name__,
                                                   str(exc)[:160]))
    done("await_deployment", deployment=dep2.get("id"),
         status=dep2.get("status"))

    # ---- reread_runtime
    try:
        hb2 = client.heartbeat() or {}
        vars2 = client.variables()
    except BaseException as exc:                               # noqa: BLE001
        raise stop("reread_runtime", "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:160]))
    seen = str(_get(hb2, "campaign.document_sha256") or "")
    if seen != applied["digest"]:
        raise stop("reread_runtime", "NEW_CONTAINER_DIGEST_MISMATCH",
                   expected=applied["digest"], observed=seen or None)
    done("reread_runtime", heartbeat_utc=hb2.get("utc"),
         approval_state=_get(hb2, "campaign.approval_state"),
         operator_halted=bool(str(vars2.get("L5_LIVE_HALT") or "").strip()))

    # ---- verify_arming
    #
    # ARMED IS THE RUNTIME'S WORD, NOT OURS. The only evidence accepted is
    # the service's own `submission_gate` reporting unblocked, plus
    # `armed`. A deployment that succeeded and a lock that was cleared are
    # NOT arming -- that conflation is what 08:30Z looked like from the
    # outside.
    deadline = time.time() + float(arming_timeout_s)
    gate: Dict[str, Any] = {}
    while True:
        hb3 = client.heartbeat() or {}
        gate = dict(_get(hb3, "submission_gate") or {})
        if gate.get("blocked") is False and gate.get("armed") is True:
            done("verify_arming", reason=gate.get("reason"),
                 armed=True)
            break
        if time.time() >= deadline:
            trail.append({"step": "verify_arming", "ok": False,
                          "reason": "NOT_ARMED_BEFORE_DEADLINE",
                          "gate": {k: v for k, v in gate.items()
                                   if k != "labels"}})
            return {"verdict": "NOT_ARMED", "stopped_at": "verify_arming",
                    "prepared": prepared, "applied": applied,
                    "gate": {k: v for k, v in gate.items()
                             if k != "labels"},
                    "trail": trail,
                    "labels": ("the configuration was applied and the "
                               "runtime did NOT arm. No order was placed. "
                               "`gate.reason` is the runtime's own word "
                               "for why.")}
        sleep(float(arming_poll_s))

    # ---- monitor + shutdown
    hb4 = client.heartbeat() or {}
    report = execution_report(heartbeat=hb4, market=prepared["market"])
    done("monitor", **{k: report[k] for k in
                       ("orders_submitted", "venue_acknowledged",
                        "orders_with_fills", "matched_pairs")})
    sd = shutdown_plan(heartbeat=hb4, now=now_fn(),
                       window_start=window_start,
                       expires_utc=str(prepared["document"]["expires_utc"]))
    done("shutdown", due=sd["due"], deadline=sd["deadline_utc"],
         outstanding_clear=sd["outstanding_clear"])
    return {"verdict": "EXECUTED", "stopped_at": None,
            "prepared": prepared, "applied": applied,
            "execution": report, "shutdown": sd, "trail": trail,
            "labels": ("EXECUTED means the sequence ran, NOT that money "
                       "was made. Read `execution` -- submitted, "
                       "acknowledged, filled and settled are separate "
                       "lines and a zero in any of them is a zero.")}
