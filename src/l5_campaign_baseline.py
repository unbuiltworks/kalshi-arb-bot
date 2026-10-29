"""The campaign's own starting point, persisted, and the $5 it may lose from it.

WHY THIS HAD TO EXIST, AND WHY THE EXISTING GATES DO NOT SUPPLY IT.

The account-wide lifetime ceiling measures `max(0, -account_wide_realized) +
unrealized` against $20 from account inception. On 2026-10-24 that figure was
already -$11.6213, so the ceiling permitted $8.3787 of further loss -- a number
that is an artefact of history, not a decision anybody made about the next
campaign. It also MOVES every time the account settles anything, in either
direction, so "how much may this campaign lose" had no stable answer.

The campaign worst-case budget does not supply it either: its input is the same
all-time realized figure, which is why `L5_CAMPAIGN_BUDGET_USD` has to sit at
$29.30 to clear history at all. A budget whose first $11.62 is spent before the
campaign opens cannot express a $5 campaign limit.

So this is a THIRD, INDEPENDENT bound, measured from a point in time that is
written down: at most $5 of additional loss from the campaign's own start,
counting both what has settled since and the worst the open book can still do.
OWNER 2026-10-24: "Ensure the campaign has an independently enforced maximum $5
additional loss, including outstanding worst-case obligations. Do not assume
the existing $20 lifetime gate supplies this."

IT IS ADDITIVE AND REFUSE-ONLY. Every existing ceiling still runs and still
refuses first if breached. This one can only ever add a refusal; there is no
path by which it permits something another gate denied, and it does not relax,
rescope or replace the account-wide check. Unconfigured, it is inert.

THE BASELINE IS A MEASUREMENT, NOT AN ALLOWANCE -- and that distinction is the
whole security argument. Re-capturing a baseline moves the zero point, which
would silently hand the campaign a fresh $5. So:

  * a campaign may NOT re-capture its own baseline. `capture` is idempotent:
    once a campaign has a baseline, it reads back the stored one unchanged.
  * a DIFFERENT campaign id captures a fresh baseline, and that is acceptable
    precisely because a new campaign id requires a new owner-set approval
    document -- the nonce comes from `L5_CAMPAIGN_APPROVAL`, which the
    executor cannot write (`EnvApprovalStore.put` is deliberately
    unimplemented). Rotating the zero point therefore costs an owner action,
    which is the same boundary `CampaignBudgetLedger` already relies on.
  * the superseded baseline is journalled before it is replaced, so a
    campaign's starting point survives the campaign.

NOTHING HERE RESETS A REALIZED LOSS. The baseline records what realized was;
it does not change it. The account-wide gate still reads the full lifetime
figure on every evaluation and still refuses at $20. A campaign starting at
-$11.6213 with a $5 campaign bound is held to BOTH: it stops at $5 of new loss,
or at $8.3787, whichever comes first, and the campaign bound is the tighter of
the two by construction.

FAIL CLOSED ON EVERY UNKNOWN. An absent baseline, a malformed one, one
belonging to another campaign, an unreadable stored figure, a missing current
realized figure, a missing inventory, or a book whose worst case cannot be
computed are all REFUSALS. The bound exists for exactly the states we cannot
see, so "could not establish" must never read as "nothing lost".
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence

#: beside the campaign budget and the window ledger, on the same volume.
BASELINE_DIR = "campaign_baseline"
BASELINE_FILE = "active.json"
#: append-only record of every superseded baseline. Same reasoning as
#: `l5_campaign_budget.HISTORY_FILE`: this holds ONE document, so replacing it
#: would otherwise erase the starting point of the campaign it replaced.
HISTORY_FILE = "history.ndjson"
ROOT_ENV = "L5_LIVE_ROOT"

#: THE OWNER-APPROVED CAMPAIGN ADDITIONAL-LOSS CEILING (OWNER 2026-10-24).
#:
#: It is a CODE CONSTANT and not a variable, for the same reason the approval
#: ceilings are: a limit that a deployed variable can widen is not a limit. The
#: environment may configure the gate ON, and may configure it TIGHTER, and can
#: never configure it looser -- see `limit_from_env`.
MAX_ADDITIONAL_LOSS_USD = D("5.00")

LIMIT_ENV = "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD"


class CampaignBaselineUnavailable(RuntimeError):
    """The starting point could not be established. NOT 'nothing lost'."""


def baseline_root(root: Optional[str] = None) -> str:
    base = root or os.getenv(ROOT_ENV, "").strip() or "."
    return os.path.join(base, BASELINE_DIR)


def _dec(v: Any) -> Optional[D]:
    """A finite Decimal, or None. None is never silently a zero here."""
    if v is None:
        return None
    try:
        out = D(str(v))
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return None
    return out if out.is_finite() else None


def limit_from_env() -> Optional[D]:
    """The configured ceiling, clamped to the code constant. None = inert.

    UNSET IS OFF, so no existing deployment changes behaviour by upgrading.
    SET BUT UNREADABLE IS NOT OFF and is not the default either -- it is the
    tightest possible bound, $0, which refuses everything. An owner who typed a
    ceiling and fat-fingered it gets a live refusal rather than a silent $5,
    which is the same rule `max_total_exposure_usd` already follows and for the
    same reason.
    """
    raw = (os.getenv(LIMIT_ENV) or "").strip()
    if not raw:
        return None
    v = _dec(raw)
    if v is None or v < 0:
        return D(0)
    return min(v, MAX_ADDITIONAL_LOSS_USD)


class CampaignBaseline:
    """Durable, single-document, campaign-owned starting point."""

    def __init__(self, root: Optional[str] = None) -> None:
        self.dir = baseline_root(root)

    def _path(self) -> str:
        return os.path.join(self.dir, BASELINE_FILE)

    def _history_path(self) -> str:
        return os.path.join(self.dir, HISTORY_FILE)

    # ------------------------------------------------------------- storage

    def read(self) -> Dict[str, Any]:
        """The stored baseline, or an empty placeholder. Raises on failure.

        A MISSING FILE IS NOT A FAILURE -- no campaign has started yet, and
        `capture` is what resolves that. An unreadable or malformed file IS a
        failure, because something is there and we cannot tell what.
        """
        try:
            with open(self._path(), "r") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            return {"campaign_id": None}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise CampaignBaselineUnavailable(
                "campaign baseline unreadable: %s" % type(exc).__name__
            ) from exc
        if not isinstance(doc, dict) or "campaign_id" not in doc:
            raise CampaignBaselineUnavailable("campaign baseline malformed")
        return doc

    def _journal(self, prev: Mapping[str, Any]) -> None:
        """Append the outgoing baseline. Never breaks the write.

        Same trade as the budget ledger's journal: a baseline that refused to
        be written because an audit file was unwritable would be worse than a
        missing history line, so a failure here is swallowed. Unlike the
        budget's, this one has no counter to surface -- a baseline is written
        once per campaign, so a lost line is visible as a gap in the sequence
        of campaign ids rather than needing to be counted.
        """
        try:
            if not (prev or {}).get("campaign_id"):
                return
            os.makedirs(self.dir, exist_ok=True)
            with open(self._history_path(), "a") as fh:
                fh.write(json.dumps(dict(prev), sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise

    def _write(self, doc: Mapping[str, Any]) -> None:
        try:
            prev = self.read()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            prev = {}
        self._journal(prev)
        os.makedirs(self.dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(dict(doc), fh, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path())
        except BaseException:                                  # noqa: BLE001
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------- capture

    def capture(self, campaign_id: str, *,
                account_wide_realized: Any,
                cash_usd: Any = None,
                inventory: Any = None,
                open_orders: Any = None,
                now_iso: str = "") -> Dict[str, Any]:
        """Record this campaign's starting point, ONCE.

        IDEMPOTENT FOR THE SAME CAMPAIGN, and that is the anti-reset property:
        a second call returns the stored document untouched, so neither a
        restart, a re-arm, nor a later window can move the zero point the $5 is
        measured from.

        A DIFFERENT CAMPAIGN ID captures a fresh baseline. That is not a
        loophole: the campaign id is the nonce from the owner's approval
        document, which the executor cannot write, so a new zero point costs an
        owner action.

        THE REALIZED FIGURE IS REQUIRED. Without it there is nothing to measure
        from, and a baseline that recorded "unknown" would be a baseline that
        could never refuse. Cash, inventory and open orders are recorded as
        evidence of the starting state the owner asked to be persisted; they
        are not gate inputs, so an absent one is stored as null rather than
        refusing.
        """
        cid = str(campaign_id or "").strip()
        if not cid:
            return {"ok": False, "reason": "CAMPAIGN_BASELINE_ID_MISSING"}
        realized = _dec(account_wide_realized)
        if realized is None:
            return {"ok": False,
                    "reason": "CAMPAIGN_BASELINE_REALIZED_UNAVAILABLE",
                    "note": "a starting point we cannot measure is not a "
                            "starting point; the campaign bound would be "
                            "unenforceable and this refuses instead"}
        try:
            doc = self.read()
        except CampaignBaselineUnavailable as exc:
            return {"ok": False, "reason": "CAMPAIGN_BASELINE_UNREADABLE",
                    "error": str(exc)}
        if str(doc.get("campaign_id") or "") == cid:
            # ALREADY CAPTURED. Return it unchanged -- never re-measure.
            return {"ok": True, "captured": False, "baseline": dict(doc),
                    "labels": ("the baseline is already set for this "
                               "campaign and is never re-measured, because "
                               "moving the zero point would hand the campaign "
                               "a fresh additional-loss allowance")}
        out = {
            "campaign_id": cid,
            "captured_utc": str(now_iso or
                                dt.datetime.now(dt.timezone.utc).isoformat()),
            "account_wide_realized": str(realized),
            "cash_usd": None if _dec(cash_usd) is None else str(_dec(cash_usd)),
            "inventory": _plain(inventory),
            "open_orders": _plain(open_orders),
            "max_additional_loss_usd": str(MAX_ADDITIONAL_LOSS_USD),
        }
        self._write(out)
        return {"ok": True, "captured": True, "baseline": out,
                "labels": ("the campaign's own zero point. Realized losses "
                           "are NOT reset by this: the account-wide gate "
                           "still reads the full lifetime figure on every "
                           "evaluation")}

    # ---------------------------------------------------------- the figure

    def additional_loss_usd(self, campaign_id: str, *,
                            account_wide_realized: Any,
                            open_worst_case_usd: Any) -> Dict[str, Any]:
        """How much this campaign is down from its own starting point.

        `realized_delta_loss + open_worst_case`, where the first term is how
        much worse realized has become since the baseline and the second is the
        most the CURRENT open book can still lose -- including every
        still-fillable outstanding order and the order being evaluated.

        A REALIZED GAIN CREATES NO ROOM. `max(0, ...)` on the delta means a
        campaign that made money does not thereby earn a larger loss budget,
        which is the same rule `l5_campaign_budget.worst_case_usd` states: "a
        realized gain that has not been withdrawn is not a loss that did not
        happen."
        """
        cid = str(campaign_id or "").strip()
        if not cid:
            return {"ok": False, "reason": "CAMPAIGN_BASELINE_NO_CAMPAIGN_ID"}
        try:
            doc = self.read()
        except CampaignBaselineUnavailable as exc:
            return {"ok": False, "reason": "CAMPAIGN_BASELINE_UNREADABLE",
                    "error": str(exc)}
        stored_id = str(doc.get("campaign_id") or "")
        if not stored_id:
            return {"ok": False, "reason": "CAMPAIGN_BASELINE_ABSENT",
                    "note": "no starting point has been captured, so the "
                            "campaign's additional loss cannot be measured"}
        if stored_id != cid:
            return {"ok": False,
                    "reason": "CAMPAIGN_BASELINE_FOREIGN:%s" % stored_id,
                    "note": "the stored baseline belongs to another campaign; "
                            "measuring this one against it would be measuring "
                            "from a point this campaign never occupied"}
        base = _dec(doc.get("account_wide_realized"))
        now = _dec(account_wide_realized)
        open_wc = _dec(open_worst_case_usd)
        if base is None:
            return {"ok": False,
                    "reason": "CAMPAIGN_BASELINE_STORED_FIGURE_UNUSABLE"}
        if now is None:
            return {"ok": False,
                    "reason": "CAMPAIGN_REALIZED_UNAVAILABLE"}
        if open_wc is None:
            return {"ok": False,
                    "reason": "CAMPAIGN_OPEN_WORST_CASE_UNAVAILABLE",
                    "note": "the owner's bound includes outstanding "
                            "obligations, so a book whose worst case cannot "
                            "be computed refuses rather than counting zero"}
        realized_delta_loss = max(D(0), base - now)
        open_risk = max(D(0), open_wc)
        return {"ok": True,
                "additional_loss_usd": str(realized_delta_loss + open_risk),
                "realized_delta_loss_usd": str(realized_delta_loss),
                "open_worst_case_usd": str(open_risk),
                "baseline_realized_usd": str(base),
                "current_realized_usd": str(now),
                "campaign_id": cid,
                "basis": ("max(0, baseline_realized - current_realized) + "
                          "max(0, worst case of the open book including this "
                          "order and the worst subset of still-fillable "
                          "outstanding orders)")}

    def refuse(self, campaign_id: str, *,
               account_wide_realized: Any,
               open_worst_case_usd: Any,
               limit_usd: Any) -> Optional[str]:
        """None to allow, else the reason. Refuse-only, never raises.

        `>=` and not `>`, matching the account-wide ceiling: a campaign that
        has reached its limit has spent it.
        """
        lim = _dec(limit_usd)
        if lim is None:
            return "CAMPAIGN_ADDITIONAL_LOSS_LIMIT_UNREADABLE"
        got = self.additional_loss_usd(
            campaign_id,
            account_wide_realized=account_wide_realized,
            open_worst_case_usd=open_worst_case_usd)
        if not got.get("ok"):
            return str(got.get("reason") or "CAMPAIGN_ADDITIONAL_LOSS_UNKNOWN")
        add = _dec(got.get("additional_loss_usd"))
        if add is None:
            return "CAMPAIGN_ADDITIONAL_LOSS_UNKNOWN"
        if add >= lim:
            return "CAMPAIGN_ADDITIONAL_LOSS_LIMIT:%s>=%s" % (add, lim)
        return None

    # ----------------------------------------------------------- telemetry

    def telemetry(self) -> Dict[str, Any]:
        """Safe to log: a starting point is not a secret and the owner has to
        be able to see which one is in force."""
        try:
            doc = self.read()
        except CampaignBaselineUnavailable as exc:
            return {"readable": False, "error": str(exc)}
        return {"readable": True,
                "campaign_id": doc.get("campaign_id"),
                "captured_utc": doc.get("captured_utc"),
                "account_wide_realized": doc.get("account_wide_realized"),
                "cash_usd": doc.get("cash_usd"),
                "max_additional_loss_usd": str(MAX_ADDITIONAL_LOSS_USD),
                "limit_in_force_usd": (
                    None if limit_from_env() is None
                    else str(limit_from_env())),
                "labels": ("the campaign's own zero point. It records what "
                           "realized WAS and changes nothing: the lifetime "
                           "account-wide ceiling still reads the full figure "
                           "on every evaluation. Unset limit = gate inert.")}


def _plain(v: Any) -> Any:
    """JSON-safe copy, with Decimals as strings. Never raises."""
    try:
        if v is None or isinstance(v, (bool, int, float, str)):
            return v
        if isinstance(v, D):
            return str(v)
        if isinstance(v, Mapping):
            return {str(k): _plain(x) for k, x in v.items()}
        if isinstance(v, Sequence):
            return [_plain(x) for x in v]
        return str(v)
    except BaseException:                                      # noqa: BLE001
        return None
