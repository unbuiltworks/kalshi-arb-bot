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
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Optional

#: the key the approval document lives under. One key, so a store that serves
#: something else cannot have its absence mistaken for "no approval".
APPROVAL_KEY = "campaign_approval.json"

#: THE CEILINGS. Code constants. The document may ask for less and never more.
MAX_TOTAL_EXPOSURE_USD = D("5")
MAX_DAILY_LOSS_USD = D("5")
MAX_WINDOWS = 4
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
