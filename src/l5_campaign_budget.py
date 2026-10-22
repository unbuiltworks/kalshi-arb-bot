"""The campaign-wide WORST-CASE loss budget. Independent of every other gate.

WHAT IT BOUNDS, and why it is not any of the ceilings that already exist.
`max_total_exposure_usd` bounds what is at risk at one INSTANT.
`daily_realized_loss_halt_usd` bounds one UTC DAY and, by its own admission,
"stops new commitment but cannot unwind what is already at risk".
`max_cumulative_loss_usd` is the ACCOUNT-WIDE lifetime ceiling and is
untouched by this module -- it still runs, still refuses first, and this can
only ADD a refusal.

None of them bounds one CAMPAIGN across its windows. Eight windows at $20 an
instant is up to $160 of sequential commitment, and nothing above would stop
it. This does: one budget, spanning the campaign, counting the worst case
rather than the realized outcome.

THE WORST CASE, not the mark. Every share bought can settle worthless, so
cash committed to an unsettled position counts in full, and an order still
resting counts in full as well because it can still fill. The budget is
therefore:

    realized losses + cash in unsettled positions + outstanding order notional

A number that can only be revised DOWN by settlement, never by optimism.

WHY A PAIR DOES NOT RELEASE EXPOSURE WHEN IT IS "MATCHED". A matched UP/DOWN
pair is worth $1.00 at settlement and so looks riskless, but it is only worth
that AFTER the venue settles it. Until then the position is unsettled cash and
the pairing is an assumption about economics that have not happened. This
module therefore releases exposure on SETTLEMENT evidence alone -- `settled`
true with its realized figure -- and never on a `matched` flag. The previous
campaign's own ledger is the argument: 416 of 427 markets carried residual
units, and pair_net +15.41 was swamped by residual_net -47.24.

WHY THE LEDGER IS NOT KEYED ON THE NONCE, which is the whole point of this
file. `WindowLedger` is keyed per nonce on purpose: a new approval is a new
grant of windows. A LOSS budget keyed the same way would be reset by issuing a
new approval, which is a variable change -- so the budget that exists to bound
a campaign could be erased mid-campaign by the same operator action that
starts one. It is instead ONE file, not per-nonce, recording which campaign
owns it. A different campaign id arriving while the ledger is open is REFUSED,
not granted a fresh budget. The experiment id is never read here at all, so
rotating `L5_EXPERIMENT_ID` cannot affect it by construction.

CLOSING IS EARNED, NOT ASSERTED. A campaign closes only when it has nothing
outstanding and nothing unsettled. Until then the budget stays open and keeps
binding, which is what makes "during an active campaign" enforceable rather
than advisory.

A READ FAILURE RAISES. "I could not tell" and "nothing spent" differ by the
whole budget, exactly as `WindowLedger.read` already argues.
"""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, Mapping, Optional, Sequence

#: the campaign budget lives beside the window ledger, on the same volume.
BUDGET_DIR = "campaign_budget"
BUDGET_FILE = "active.json"
ROOT_ENV = "L5_LIVE_ROOT"

#: the owner-approved campaign worst-case ceiling.
DEFAULT_BUDGET_USD = D("20")


class CampaignBudgetUnavailable(RuntimeError):
    """The budget could not be established. NOT the same as 'nothing spent'."""


def budget_root(root: Optional[str] = None) -> str:
    base = root or os.getenv(ROOT_ENV, "").strip() or "."
    return os.path.join(base, BUDGET_DIR)


def _d(v: Any) -> D:
    """Decimal or a refusal-shaped zero. Never raises on ordinary input."""
    try:
        out = D(str(v))
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return D(0)
    return out if out.is_finite() else D(0)


def worst_case_usd(*,
                   realized_usd: Any = 0,
                   legs: Sequence[Mapping[str, Any]] = (),
                   outstanding: Sequence[Mapping[str, Any]] = ()) -> D:
    """The most this campaign can still be down, counted conservatively.

    `realized_usd` is signed: gains do NOT create budget headroom, because a
    realized gain that has not been withdrawn is not a loss that did not
    happen. Only the loss side is counted.

    A leg counts its full `cost_usd` until `settled` is true. Once settled it
    contributes its realized loss instead, which is what the venue actually
    did. `matched` is deliberately ignored.
    """
    used = max(D(0), -_d(realized_usd))
    for leg in legs or ():
        if not isinstance(leg, Mapping):
            continue
        if bool(leg.get("settled")):
            # Settled: the outcome is known. Count the loss it produced, and
            # nothing for a leg that paid out.
            used += max(D(0), -_d(leg.get("realized_usd")))
        else:
            # Unsettled: the whole committed cost can be lost. `matched` is
            # not consulted -- see the module docstring.
            used += max(D(0), _d(leg.get("cost_usd")))
    for o in outstanding or ():
        if not isinstance(o, Mapping):
            continue
        # A resting order can still fill, and what it buys can settle
        # worthless. Remaining quantity only -- the filled part is already a
        # leg above and must not be counted twice.
        qty = max(D(0), _d(o.get("leaves_qty", o.get("qty"))))
        used += qty * max(D(0), _d(o.get("price")))
    return used


class CampaignBudgetLedger:
    """Durable, single-file, campaign-owned worst-case loss budget."""

    def __init__(self, root: Optional[str] = None,
                 budget_usd: Any = DEFAULT_BUDGET_USD) -> None:
        self.dir = budget_root(root)
        self.budget_usd = _d(budget_usd)

    def _path(self) -> str:
        return os.path.join(self.dir, BUDGET_FILE)

    # ---------------------------------------------------------------- read

    def read(self) -> Dict[str, Any]:
        """The open campaign, or a closed placeholder. Raises on failure."""
        p = self._path()
        try:
            with open(p, "r") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            return {"campaign_id": None, "closed": True, "peak_usd": "0"}
        except BaseException as exc:                          # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise CampaignBudgetUnavailable(
                "campaign budget unreadable: %s" % type(exc).__name__) from exc
        if not isinstance(doc, dict) or "campaign_id" not in doc:
            raise CampaignBudgetUnavailable("campaign budget malformed")
        return doc

    def _write(self, doc: Mapping[str, Any]) -> None:
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

    def is_active(self) -> bool:
        doc = self.read()
        return bool(doc.get("campaign_id")) and not bool(doc.get("closed"))

    # ------------------------------------------------------------ lifecycle

    def open_or_resume(self, campaign_id: str, *,
                       now_iso: str = "") -> Dict[str, Any]:
        """Claim the budget for `campaign_id`, or resume its own claim.

        A DIFFERENT campaign id arriving while one is open is refused. That
        refusal is the entire anti-reset property: rotating the nonce does
        not mint a second budget, it is simply denied.
        """
        cid = str(campaign_id or "").strip()
        if not cid:
            return {"ok": False, "reason": "CAMPAIGN_BUDGET_ID_MISSING"}
        doc = self.read()
        open_id = doc.get("campaign_id")
        if open_id and not bool(doc.get("closed")):
            if str(open_id) != cid:
                return {"ok": False,
                        "reason": "CAMPAIGN_BUDGET_LOCKED:%s" % open_id,
                        "campaign_id": str(open_id),
                        "peak_usd": str(doc.get("peak_usd") or "0")}
            return {"ok": True, "campaign_id": cid, "resumed": True,
                    "peak_usd": str(doc.get("peak_usd") or "0"),
                    "budget_usd": str(self.budget_usd)}
        self._write({"campaign_id": cid, "closed": False,
                     "opened_utc": str(now_iso), "peak_usd": "0"})
        return {"ok": True, "campaign_id": cid, "resumed": False,
                "peak_usd": "0", "budget_usd": str(self.budget_usd)}

    def close(self, campaign_id: str, *,
              legs: Sequence[Mapping[str, Any]] = (),
              outstanding: Sequence[Mapping[str, Any]] = (),
              now_iso: str = "") -> Dict[str, Any]:
        """Close the campaign. Refused while anything is live or unsettled.

        Closing is what frees the budget for a NEXT campaign, so it is the
        one place that could be abused to reset it. It therefore requires
        evidence rather than assertion.
        """
        cid = str(campaign_id or "").strip()
        doc = self.read()
        if not doc.get("campaign_id") or bool(doc.get("closed")):
            return {"ok": True, "already_closed": True}
        if str(doc.get("campaign_id")) != cid:
            return {"ok": False,
                    "reason": "CAMPAIGN_BUDGET_LOCKED:%s"
                              % doc.get("campaign_id")}
        if any(True for _ in (outstanding or ())):
            return {"ok": False, "reason": "CAMPAIGN_OUTSTANDING_ORDERS"}
        unsettled = [l for l in (legs or ())
                     if isinstance(l, Mapping) and not bool(l.get("settled"))]
        if unsettled:
            return {"ok": False, "reason": "CAMPAIGN_UNSETTLED_EXPOSURE:%d"
                                           % len(unsettled)}
        out = dict(doc)
        out.update({"closed": True, "closed_utc": str(now_iso)})
        self._write(out)
        return {"ok": True, "already_closed": False, "campaign_id": cid}

    # -------------------------------------------------------------- the gate

    def refuse(self, *,
               campaign_id: str,
               realized_usd: Any = 0,
               legs: Sequence[Mapping[str, Any]] = (),
               outstanding: Sequence[Mapping[str, Any]] = (),
               new_order_cost_usd: Any = 0,
               record_peak: bool = True) -> Optional[str]:
        """None to allow, else the reason. Never raises on ordinary input.

        The projected figure includes the order being considered, so the
        budget binds BEFORE the commitment rather than after it -- the same
        direction `ValidationGates.refuse` takes on potential exposure.
        """
        cid = str(campaign_id or "").strip()
        if not cid:
            return "CAMPAIGN_BUDGET_ID_MISSING"
        try:
            doc = self.read()
        except CampaignBudgetUnavailable:
            # Unreadable is a refusal. It is never read as an empty budget.
            return "CAMPAIGN_BUDGET_UNREADABLE"
        open_id = doc.get("campaign_id")
        if open_id and not bool(doc.get("closed")) and str(open_id) != cid:
            return "CAMPAIGN_BUDGET_LOCKED:%s" % open_id

        used = worst_case_usd(realized_usd=realized_usd, legs=legs,
                              outstanding=outstanding)
        projected = used + max(D(0), _d(new_order_cost_usd))

        # WHAT IS GATED IS THE CURRENT WORST CASE, NOT THE HIGH-WATER MARK,
        # and the distinction decides whether an eight-window campaign is
        # possible at all. A window that commits $2 and SETTLES FLAT has lost
        # nothing, so it must hand its $2 back -- otherwise eight sequential
        # $2 windows would consume $16 of a $20 loss budget while losing $0,
        # and the budget would be a turnover cap wearing a loss budget's
        # name. Losses are what persist: `worst_case_usd` keeps every settled
        # leg's realized loss forever, so a campaign that loses cannot cycle
        # its way back to a clean slate.
        if projected > self.budget_usd:
            return ("CAMPAIGN_WORST_CASE_BUDGET:%s>%s"
                    % (projected, self.budget_usd))

        # The high-water mark is recorded for the heartbeat only. It is
        # deliberately NOT a gate -- see above.
        peak = max(_d(doc.get("peak_usd")), projected)
        if record_peak and open_id and not bool(doc.get("closed")) \
                and peak > _d(doc.get("peak_usd")):
            out = dict(doc)
            out["peak_usd"] = str(peak)
            try:
                self._write(out)
            except BaseException:                              # noqa: BLE001
                return "CAMPAIGN_BUDGET_UNWRITABLE"
        return None

    def telemetry(self, *, campaign_id: str = "") -> Dict[str, Any]:
        """Non-secret state for the heartbeat. Never raises."""
        try:
            doc = self.read()
        except CampaignBudgetUnavailable as exc:
            return {"readable": False, "why": str(exc),
                    "budget_usd": str(self.budget_usd)}
        peak = _d(doc.get("peak_usd"))
        return {"readable": True,
                "campaign_id": doc.get("campaign_id"),
                "closed": bool(doc.get("closed")),
                "peak_usd": str(peak),
                "budget_usd": str(self.budget_usd),
                "remaining_usd": str(max(D(0), self.budget_usd - peak)),
                "owns_budget": (bool(doc.get("campaign_id"))
                                and str(doc.get("campaign_id"))
                                == str(campaign_id)),
                "labels": ("worst case = realized losses + unsettled cost + "
                           "outstanding notional. Settlement releases "
                           "exposure; matching does not.")}
