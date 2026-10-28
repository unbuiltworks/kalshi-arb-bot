"""Owner campaign approval, read over an owner-controlled object.

ONE OWNER ACTION, MANY WINDOWS, NO RAILWAY CEREMONY. The owner publishes a
small approval document somewhere only they can write. The runner READS it.
Every window inside the approved scope then authorizes itself through the
existing two-level mandate, with its own permanent single-use claim.

WHY THIS IS NOT A WEAKENING. The security property of the Railway dual-key is
not that it lives in Railway; it is that the owner holds a credential the
executor does not. `HttpHaltStore` already establishes exactly that shape for
the halt latch, and says why: "The executor holds this object. It must not be
able to write the latch that governs it." An approval store is the same
object with the same refusal, one key over. Claude cannot write it, so Claude
cannot approve a campaign -- which is the invariant `pmus_q1_executor` states
as "Claude never arms this; the operator does."

WHAT THE DOCUMENT CANNOT DO. It cannot widen anything. Every ceiling below is
a CODE CONSTANT and the effective scope is the INTERSECTION of what was asked
for and what the code permits. A document requesting $50 and forty windows
yields $5 and four. There is deliberately no path by which a number in the
document raises a limit, because then the document, not the code, would be
the risk control.

FAIL-CLOSED ON EVERYTHING, modelled on `HaltGate.state`: absent, unreadable,
malformed, missing a field, a naive timestamp, a future timestamp, a stale
document, an expired campaign, a series outside the allowlist and a spent
nonce all return approved False. Anything but an explicit, in-date, in-scope
approval is a refusal.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Optional

#: the key the approval document lives under. One key, so a store that serves
#: something else cannot have its absence mistaken for "no approval".
APPROVAL_KEY = "campaign_approval.json"

#: THE CEILINGS. Code constants. The document may ask for less and never more.
#: RAISED 5 -> 20 for the eight-window sequential-pairing campaign. This is
#: the INSTANTANEOUS ceiling: one market, one live order, up to $20 at risk
#: at any moment. It is NOT a campaign budget -- eight windows at $20 would
#: be $160 of sequential commitment, which `l5_campaign_budget` now bounds
#: at $20 of worst-case loss independently of this number.
MAX_TOTAL_EXPOSURE_USD = D("20")
#: UNCHANGED at $5. Note the asymmetry this creates and does not resolve: a
#: single market can lose $20, so one settlement can overshoot the daily halt
#: fourfold. The halt stops new commitment; it cannot unwind what is already
#: at risk. The campaign budget is what bounds the overshoot.
MAX_DAILY_LOSS_USD = D("5")
#: EIGHT, raised from four for the two-hour eight-window campaign.
#: WHAT THIS DOES NOT CHANGE, and the distinction is the whole safety
#: argument: exposure is bounded by MAX_TOTAL_EXPOSURE_USD at every
#: instant, not by how many windows may be worked, so eight windows buys
#: more OPPORTUNITIES to deploy the same $5 and never more dollars at
#: risk at once. The duration ceiling, the daily-loss halt, the series
#: allow-list, the class budgets, the completion reserve, the watchdog and
#: the HALT latch are all untouched.
MAX_WINDOWS = 8
#: the longest campaign a single approval may cover, regardless of its own
#: stated expiry. A document asking for a week gets four hours.
MAX_CAMPAIGN_S = 4 * 3600.0
#: instrument families an approval may name. Not a pattern the document
#: supplies -- an allowlist the code holds.
ALLOWED_SERIES = ("cpc-btc-updown-15m-",)
#: a document older than this is stale even if its campaign has not expired,
#: so a forgotten endpoint cannot keep authorizing.
DEFAULT_MAX_AGE_S = 3600.0

REQUIRED_FIELDS = ("approved", "nonce", "series", "issued_utc", "expires_utc",
                   "max_windows", "max_total_exposure_usd",
                   "max_daily_loss_usd")


class ApprovalReadUnavailable(RuntimeError):
    """The answer could not be established. NOT the same as 'no approval'."""


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _dec(v: Any) -> Optional[D]:
    try:
        return None if v is None else D(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _ts(v: Any) -> Optional[dt.datetime]:
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return t if t.tzinfo is not None else None      # naive is a refusal


def digest_of(raw: Optional[bytes]) -> Optional[str]:
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def nonce_sid(nonce: str) -> str:
    """The claim id for ONE campaign approval, for the existing ledger."""
    return "camp1.%s" % str(nonce).strip()


class ApprovalGate:
    """Read-only. Default answer is NO."""

    def __init__(self, store: Any, *,
                 max_age_s: float = DEFAULT_MAX_AGE_S,
                 now_fn: Any = _utc) -> None:
        self.store = store
        self.max_age_s = float(max_age_s)
        self._now = now_fn
        self.last_sha256: Optional[str] = None
        self.last_state: Optional[str] = None

    # ------------------------------------------------------------ telemetry
    def telemetry(self) -> Dict[str, Any]:
        """Safe to log. The digest identifies the document; the document's
        own contents are never emitted."""
        return {"sha256": self.last_sha256, "state": self.last_state,
                "key": APPROVAL_KEY,
                "ceilings": {"max_total_exposure_usd":
                             str(MAX_TOTAL_EXPOSURE_USD),
                             "max_daily_loss_usd": str(MAX_DAILY_LOSS_USD),
                             "max_windows": MAX_WINDOWS,
                             "max_campaign_s": MAX_CAMPAIGN_S,
                             "allowed_series": list(ALLOWED_SERIES)},
                "labels": ("ceilings are code constants; an approval is the "
                           "INTERSECTION of what was asked and what these "
                           "permit, and can never widen one")}

    # ----------------------------------------------------------------- read
    def _raw(self) -> Optional[bytes]:
        try:
            return self.store.get(APPROVAL_KEY)
        except ApprovalReadUnavailable:
            raise
        except BaseException as exc:                          # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ApprovalReadUnavailable(
                "approval GET failed: %s" % type(exc).__name__) from exc

    def state(self) -> Dict[str, Any]:
        """`approved` True only for an explicit, in-date, in-scope document."""
        def no(why: str, **extra: Any) -> Dict[str, Any]:
            self.last_state = why
            return dict({"approved": False, "why": why, "scope": None}, **extra)

        try:
            raw = self._raw()
        except ApprovalReadUnavailable:
            self.last_sha256 = None
            return no("approval_unreadable")
        self.last_sha256 = digest_of(raw)
        if raw is None:
            return no("approval_absent")
        try:
            doc = json.loads(raw.decode("utf-8"))
        except BaseException:                                  # noqa: BLE001
            return no("approval_unparseable")
        if not isinstance(doc, dict):
            return no("approval_not_an_object")
        missing = [f for f in REQUIRED_FIELDS if f not in doc]
        if missing:
            return no("approval_incomplete", missing=missing)
        # anything but an explicit True is a refusal
        if doc.get("approved") is not True:
            return no("approval_not_asserted")
        nonce = str(doc.get("nonce") or "").strip()
        if not nonce or len(nonce) < 8:
            return no("approval_nonce_too_short")
        series = str(doc.get("series") or "").strip()
        if series not in ALLOWED_SERIES:
            return no("approval_series_not_allowed", series=series[:60])

        now = self._now()
        issued = _ts(doc.get("issued_utc"))
        expires = _ts(doc.get("expires_utc"))
        if issued is None:
            return no("approval_issued_time_unusable")
        if expires is None:
            return no("approval_expiry_unusable")
        age = (now - issued).total_seconds()
        if age < 0:
            return no("approval_issued_in_the_future", age_s=round(age, 1))
        if age > self.max_age_s:
            return no("approval_stale", age_s=round(age, 1))
        remaining = (expires - now).total_seconds()
        if remaining <= 0:
            return no("approval_expired", expires_in_s=round(remaining, 1))

        # INTERSECT, NEVER WIDEN.
        want_w = doc.get("max_windows")
        try:
            want_w = int(want_w)
        except (TypeError, ValueError):
            return no("approval_max_windows_unusable")
        if want_w < 1:
            return no("approval_max_windows_below_one")
        want_exp = _dec(doc.get("max_total_exposure_usd"))
        want_loss = _dec(doc.get("max_daily_loss_usd"))
        if want_exp is None or want_loss is None:
            return no("approval_limits_unusable")
        if want_exp <= 0 or want_loss <= 0:
            return no("approval_limits_not_positive")

        eff_w = min(want_w, MAX_WINDOWS)
        eff_exp = min(want_exp, MAX_TOTAL_EXPOSURE_USD)
        eff_loss = min(want_loss, MAX_DAILY_LOSS_USD)
        eff_expires = min(expires, issued + dt.timedelta(seconds=MAX_CAMPAIGN_S))
        if (eff_expires - now).total_seconds() <= 0:
            return no("approval_campaign_ceiling_expired")

        self.last_state = "approved"
        return {"approved": True, "why": None,
                "scope": {"nonce": nonce, "nonce_sid": nonce_sid(nonce),
                          "series": series,
                          "max_windows": eff_w,
                          "max_total_exposure_usd": str(eff_exp),
                          "max_daily_loss_usd": str(eff_loss),
                          # THE SUBMISSION BUDGETS, OPTIONAL AND PASSED
                          # THROUGH. They are the owner's per-campaign
                          # operational numbers and no gate here reads them
                          # -- the durable claim ledger enforces them. They
                          # are carried so the runtime-config verifier can
                          # grade the DEPLOYED variables against the
                          # document that approved them instead of against
                          # a figure hand-edited in code, which is how eight
                          # of its rows came to disagree with the
                          # configuration the owner had actually approved.
                          #
                          # Absent is absent, never a default: the verifier
                          # falls back to an invariant when a field is
                          # missing, and must not be handed a zero it would
                          # read as an approved budget.
                          "submission_budgets": {
                              k: str(doc[k]) for k in
                              ("max_open_submissions",
                               "max_complete_submissions",
                               "max_replace_submissions",
                               "max_total_submissions")
                              if doc.get(k) is not None},
                          "expires_utc": eff_expires.isoformat(),
                          "issued_utc": issued.isoformat(),
                          "clamped": {
                              "max_windows": want_w != eff_w,
                              "max_total_exposure_usd": want_exp != eff_exp,
                              "max_daily_loss_usd": want_loss != eff_loss,
                              "expires_utc": eff_expires != expires},
                          "document_sha256": self.last_sha256},
                "labels": ("the scope is the intersection of the document and "
                           "the code ceilings; `clamped` names every field "
                           "the code reduced. The nonce still has to be "
                           "claimed single-use before anything arms.")}


# ===================================================== the campaign SESSION
#
# `state()` above answers "is there a valid approval, and what does it
# permit?". This layer answers the operational question: "may THIS market, in
# THIS window, be armed under it -- and how many windows are left?"
#
# WHY CONSUMPTION IS DURABLE AND KEYED ON THE NONCE. A window counter held in
# memory is reset by every redeploy, and this service redeploys whenever a
# variable changes. A four-window approval would then authorize four windows
# per restart, without limit. So consumption is written to the volume under
# the nonce's own id: the same approval always sees the same count, and a new
# approval (a new nonce) starts its own.
#
# WHY A WINDOW IS RESERVED BEFORE ARMING, NOT CREDITED AFTER. A crash between
# "we decided to arm" and "we recorded that we did" has to cost a window
# rather than grant one, which is the same conservative direction
# `record_submit` takes before the wire. Reserving is IDEMPOTENT per market
# slug, so a restart inside a window that is already reserved resumes it and
# does not spend a second.

CONSUMED_DIR = "campaign_windows"
ROOT_ENV = "L5_LIVE_ROOT"


def consumed_root(root: Optional[str] = None) -> str:
    base = root or os.getenv(ROOT_ENV, "").strip() or "."
    return os.path.join(base, CONSUMED_DIR)


class WindowLedger:
    """Durable, per-nonce record of which windows an approval has spent."""

    def __init__(self, root: Optional[str] = None) -> None:
        self.dir = consumed_root(root)

    def _path(self, nonce_sid: str) -> str:
        safe = "".join(c for c in str(nonce_sid)
                       if c.isalnum() or c in ".-_")
        if not safe:
            raise ValueError("empty nonce id")
        return os.path.join(self.dir, "%s.json" % safe)

    def read(self, nonce_sid: str) -> Dict[str, Any]:
        """What this approval has spent. A READ FAILURE RAISES -- it is never
        reported as an empty ledger, because "I could not tell" and "nothing
        spent" differ by the whole budget."""
        p = self._path(nonce_sid)
        try:
            with open(p, "r") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            return {"nonce_sid": str(nonce_sid), "windows": []}
        except BaseException as exc:                          # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ApprovalReadUnavailable(
                "window ledger unreadable: %s" % type(exc).__name__) from exc
        if not isinstance(doc, dict) or not isinstance(
                doc.get("windows"), list):
            raise ApprovalReadUnavailable("window ledger malformed")
        return doc

    def would_reserve(self, nonce_sid: str, market: str, *,
                      max_windows: int) -> Dict[str, Any]:
        """Would `market` be granted? READ-ONLY; writes nothing.

        Same verdict shape as `reserve`, so a caller cannot tell them apart
        by accident -- except for `dry_run`, which says plainly that no
        window was spent. A read failure still RAISES: "I could not tell"
        and "nothing spent" differ by the whole budget here too.
        """
        doc = self.read(nonce_sid)
        windows = [str(w) for w in doc.get("windows") or ()]
        if market in windows:
            return {"ok": True, "index": windows.index(market) + 1,
                    "windows_spent": len(windows),
                    "windows_remaining": max(0, int(max_windows)
                                             - len(windows)),
                    "reserved_now": False, "dry_run": True,
                    "market": market}
        if len(windows) >= int(max_windows):
            return {"ok": False, "reason": "CAMPAIGN_WINDOWS_EXHAUSTED",
                    "windows_spent": len(windows),
                    "max_windows": int(max_windows), "dry_run": True,
                    "market": market}
        return {"ok": True, "index": len(windows) + 1,
                "windows_spent": len(windows),
                "windows_remaining": max(0, int(max_windows)
                                         - len(windows)),
                "reserved_now": False, "dry_run": True, "market": market}

    def reserve(self, nonce_sid: str, market: str, *,
                max_windows: int, now_iso: str) -> Dict[str, Any]:
        """Reserve `market` against this approval, durably and idempotently.

        Returns the index (1-based) and what remains. Raises on a read
        failure and refuses once the budget is spent. The write is atomic and
        fsynced before this returns, so the reservation survives the crash it
        exists to survive.
        """
        doc = self.read(nonce_sid)
        windows = [str(w) for w in doc.get("windows") or ()]
        if market in windows:
            return {"ok": True, "index": windows.index(market) + 1,
                    "windows_spent": len(windows),
                    "windows_remaining": max(0, int(max_windows)
                                             - len(windows)),
                    "reserved_now": False, "market": market}
        if len(windows) >= int(max_windows):
            return {"ok": False, "reason": "CAMPAIGN_WINDOWS_EXHAUSTED",
                    "windows_spent": len(windows),
                    "max_windows": int(max_windows), "market": market}
        windows.append(market)
        out = dict(doc, nonce_sid=str(nonce_sid), windows=windows,
                   last_utc=now_iso)
        out.setdefault("first_utc", now_iso)
        os.makedirs(self.dir, exist_ok=True)
        p = self._path(nonce_sid)
        tmp = p + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(out, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
        try:
            dfd = os.open(self.dir, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass            # the file itself is already fsynced
        return {"ok": True, "index": len(windows),
                "windows_spent": len(windows),
                "windows_remaining": max(0, int(max_windows) - len(windows)),
                "reserved_now": True, "market": market}


def authorize_window(gate: "ApprovalGate", market: str, *,
                     ledger: Optional[WindowLedger] = None,
                     now: Optional[dt.datetime] = None,
                     reserve: bool = True) -> Dict[str, Any]:
    """May this market be armed under the current approval? Default NO.

    THREE OUTCOMES, AND THE MIDDLE ONE IS THE IMPORTANT ONE:

      `present: False`  no approval document at all. This layer says nothing
                        and the caller keeps whatever single-window
                        authorization it already had. A campaign system that
                        silently REPLACED the existing handshake would be a
                        widening disguised as a feature.
      `ok: False`       a document exists and does not permit this. The
                        caller must REFUSE -- never fall back to the
                        single-window path, or an expired or exhausted
                        campaign would quietly become a fresh one-window
                        authorization.
      `ok: True`        armed for this window, with the window reserved.
    """
    now = now or _utc()
    try:
        st = gate.state()
    except ApprovalReadUnavailable as exc:
        # UNREADABLE IS PRESENT-AND-REFUSING, never absent. A storage fault
        # must not read as "no campaign, carry on".
        return {"present": True, "ok": False,
                "reason": "APPROVAL_UNREADABLE",
                "detail": {"error": str(exc)[:160]}}
    why = str(st.get("why") or "")
    if not st.get("approved") and why in ("approval_absent", "approval_empty",
                                          "approval_not_set"):
        return {"present": False, "ok": False, "reason": why or "absent"}
    # `state()` CATCHES a read failure and reports it rather than raising, so
    # the precise reason has to be recovered here too -- otherwise a storage
    # fault is reported as a merely invalid document, which reads like the
    # owner's mistake instead of ours.
    if why == "approval_unreadable":
        return {"present": True, "ok": False, "reason": "APPROVAL_UNREADABLE",
                "detail": {"why": why}}
    if not st.get("approved"):
        return {"present": True, "ok": False,
                "reason": "APPROVAL_NOT_VALID", "detail": {"why": why}}
    scope = dict(st.get("scope") or {})
    series = str(scope.get("series") or "")
    if not series or not str(market).startswith(series):
        return {"present": True, "ok": False,
                "reason": "CAMPAIGN_SERIES_MISMATCH",
                "detail": {"series": series, "attempted": str(market)},
                "scope": scope}
    exp = _ts(scope.get("expires_utc"))
    if exp is None or (exp - now).total_seconds() <= 0:
        return {"present": True, "ok": False, "reason": "CAMPAIGN_EXPIRED",
                "detail": {"expires_utc": scope.get("expires_utc")},
                "scope": scope}
    led = ledger or WindowLedger()
    try:
        # RESERVE, OR MERELY ASK.
        #
        # A WINDOW RESERVED WHILE SUBMISSION IS STRUCTURALLY BLOCKED IS A
        # WINDOW SPENT ON NOTHING. The reservation exists so that a crash
        # between "we decided to arm" and "we recorded it" costs a window
        # rather than granting one -- and that risk does not exist when the
        # shadow lock means no order can reach the venue at all. The
        # activation sequence deliberately installs the campaign with the
        # lock ON so it can be verified first, and on 2026-10-20 that
        # verification consumed the 1600z window: four approved windows
        # became three, with the one it spent unusable.
        #
        # So the caller says which it wants. `reserve=False` answers "would
        # this window be granted?" and writes nothing.
        if reserve:
            res = led.reserve(str(scope.get("nonce_sid")), str(market),
                              max_windows=int(scope.get("max_windows") or 0),
                              now_iso=now.isoformat())
        else:
            res = led.would_reserve(
                str(scope.get("nonce_sid")), str(market),
                max_windows=int(scope.get("max_windows") or 0))
    except ApprovalReadUnavailable as exc:
        return {"present": True, "ok": False,
                "reason": "WINDOW_LEDGER_UNREADABLE",
                "detail": {"error": str(exc)[:160]}, "scope": scope}
    except BaseException as exc:                              # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"present": True, "ok": False,
                "reason": "WINDOW_RESERVE_FAILED",
                "detail": {"error": type(exc).__name__}, "scope": scope}
    if not res.get("ok"):
        return {"present": True, "ok": False,
                "reason": str(res.get("reason")), "detail": res,
                "scope": scope}
    return {"present": True, "ok": True, "reason": None,
            "scope": scope, "window": res,
            "labels": ("the window is RESERVED durably before anything arms, "
                       "so a crash costs a window rather than granting one, "
                       "and re-arming the same slug after a restart resumes "
                       "it instead of spending another")}


def reserve_window(market: str, *,
                   root: Optional[str] = None,
                   now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """SPEND the window for `market`, idempotently. Call this LAST.

    THE ORDERING THIS EXISTS TO ENFORCE. `campaign_decision` used to reserve
    while answering, so the window was spent before the arming boundary had
    ruled on anything -- and on 2026-10-20 two windows went that way: 1600z
    to a verification run under the shadow lock, and 1630z to an arm that
    `MANDATE_SCOPE_CHANGED` then refused. Neither could ever have traded.

    So the verdict is now read-only (`reserve=False`) and the spend is this
    separate call, made only once `authorize_arming` has RETURNED a claim --
    which means the shadow lock is off, the owner handshake matched, the halt
    latch is present and clear, preflight passed, the book is reconciled, no
    order is uncertain, the accounting is resolved, the deadline holds, the
    capital scope is known, the market is in the mandate's series AND THE
    MANDATE'S OWN SCOPE DIGEST MATCHES. Every one of those is a reason a
    window must not be spent.

    IDEMPOTENT PER SLUG, so a redeploy or a restart inside a window it has
    already reserved resumes that window instead of spending another:
    `reserved_now` comes back False and the count does not move.
    """
    now = now or _utc()
    gate = approval_gate()
    try:
        st = gate.state()
    except ApprovalReadUnavailable as exc:
        return {"ok": False, "reason": "APPROVAL_UNREADABLE",
                "detail": {"error": str(exc)[:160]}}
    if not st.get("approved"):
        return {"ok": False, "reason": "APPROVAL_NOT_VALID",
                "detail": {"why": st.get("why")}}
    scope = dict(st.get("scope") or {})
    try:
        return dict(WindowLedger(root).reserve(
            str(scope.get("nonce_sid")), str(market),
            max_windows=int(scope.get("max_windows") or 0),
            now_iso=now.isoformat()), spent=True)
    except ApprovalReadUnavailable as exc:
        return {"ok": False, "reason": "WINDOW_LEDGER_UNREADABLE",
                "detail": {"error": str(exc)[:160]}}
    except BaseException as exc:                              # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"ok": False, "reason": "WINDOW_RESERVE_FAILED",
                "detail": {"error": type(exc).__name__}}


# ===================================================== delivering the approval
#
# ONE VARIABLE PER CAMPAIGN, NOT PER WINDOW. The whole point of this layer is
# that the owner stops editing Railway for every fifteen-minute window. The
# approval document is therefore read from a single environment variable, set
# once, covering the bounded run -- and the durable per-nonce window ledger,
# not the variable, is what counts consumption, so a redeploy cannot hand back
# a window.
#
# WHY NOT THE VOLUME. `FileStore` would be the natural home and the halt latch
# uses it, but the owner cannot write to the mounted volume without a deploy,
# which is the ceremony this exists to remove.
#
# THE DOCUMENT IS NOT A CREDENTIAL. It bounds a run; it does not authorize
# one. The owner's `PMUS_Q1_*` handshake is still required, the shadow lock
# still outranks it, and `telemetry()` publishes only the sha256 digest -- the
# contents, nonce included, are never emitted.

APPROVAL_ENV = "L5_CAMPAIGN_APPROVAL"


class EnvApprovalStore:
    """An `ObjectStore` serving the approval document from the environment.

    `get` honours the contract that matters: None ONLY when the variable is
    positively absent or empty, and a raise when the answer cannot be
    established. Those two must not collapse, because `authorize_window`
    treats absent as "no campaign, keep the single-window path" and
    unreadable as "present and refusing".
    """

    def __init__(self, env: str = APPROVAL_ENV) -> None:
        self.env = str(env)

    def get(self, key: str) -> Optional[bytes]:
        if key != APPROVAL_KEY:
            return None
        raw = os.getenv(self.env)
        if raw is None or not str(raw).strip():
            return None
        try:
            return str(raw).encode("utf-8")
        except BaseException as exc:                          # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ApprovalReadUnavailable(
                "approval variable unreadable: %s" % type(exc).__name__
            ) from exc

    def put(self, key: str, body: bytes) -> Any:
        # DELIBERATELY NOT IMPLEMENTED. The executor must never be able to
        # write its own authorization; only the owner sets this variable.
        raise ApprovalReadUnavailable(
            "the executor may not write its own approval")


def approval_gate() -> "ApprovalGate":
    """THE approval gate. Every caller must use this one.

    WHY IT EXISTS. Three call sites built their own gate and two of them
    omitted `max_age_s`, taking the 1-hour DEFAULT_MAX_AGE_S while the
    arming path used the 4-hour MAX_CAMPAIGN_S. The consequences were not
    cosmetic:

      * the heartbeat reported `approval_stale` an hour after issue while
        `try_arm` still accepted the same document for four -- so the status
        an operator reads was not the verdict the money path enforced;
      * `_campaign_nonce_sid` returned None on the same 1-hour clock, and a
        None campaign id made the $29.30 worst-case budget gate SKIP
        ITSELF. A control silently stopped binding one hour after issue
        while trading remained authorized for four.

    A single factory makes that divergence unrepresentable. The staleness
    window is MAX_CAMPAIGN_S, the same bound `state()` already clamps the
    effective expiry to, so the document's own `expires_utc` still decides
    within it.
    """
    return ApprovalGate(EnvApprovalStore(), max_age_s=MAX_CAMPAIGN_S)


def campaign_decision(market: str, *,
                      root: Optional[str] = None,
                      now: Optional[dt.datetime] = None,
                      reserve: bool = True) -> Dict[str, Any]:
    """The per-window campaign verdict for `market`, ready to pass to
    `l5_live_authorization.authorize_arming(campaign=...)`.

    One call, so the runner has one thing to do and cannot half-wire it.
    Absent approval returns `present: False`, which that boundary treats as
    silence.
    """
    # THE STALENESS WINDOW, for an OWNER-SET VARIABLE, IS THE CAMPAIGN
    # CEILING -- not the one-hour default.
    #
    # `DEFAULT_MAX_AGE_S` exists so a forgotten PULL endpoint cannot keep
    # authorizing: nobody has to touch an HTTP server for it to keep serving
    # yesterday's approval, so age is the only thing that stops it. A Railway
    # variable is not that: it is set deliberately, once, and withdrawn by
    # clearing it. Judging it stale an hour after issue would cut the
    # approved FOUR-HOUR campaign to one hour and put the owner back to
    # re-issuing by hand -- the exact ceremony this layer removes.
    #
    # NOTHING IS WIDENED. `state()` still clamps the effective expiry to
    # `issued + MAX_CAMPAIGN_S`, so four hours from issue remains the hard
    # bound; this only stops a SECOND, shorter clock truncating it.
    gate = approval_gate()
    out = authorize_window(gate, market, ledger=WindowLedger(root), now=now,
                           reserve=reserve)
    # the digest identifies the document in the logs; the contents never are
    out["document_sha256"] = gate.last_sha256
    out["approval_state"] = gate.last_state
    return out
