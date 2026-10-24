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

import datetime as dt
import json
import os
import tempfile
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: the campaign budget lives beside the window ledger, on the same volume.
BUDGET_DIR = "campaign_budget"
BUDGET_FILE = "active.json"

#: append-only record of every superseded campaign document. The ledger holds
#: ONE document; without this, closing a campaign erased the campaign it
#: closed. See `CampaignBudgetLedger._journal`.
HISTORY_FILE = "history.ndjson"
ROOT_ENV = "L5_LIVE_ROOT"

#: THE OWNER-APPROVED CAMPAIGN WORST-CASE CEILING.
#:
#: RE-SCOPED 20 -> 11.10 and the reason is arithmetic, not caution. The
#: ACCOUNT-WIDE cumulative gate is $20 and `all_time_realized_usd` is
#: -8.8988, so only $11.1012 of that ceiling is unspent. The owner's
#: instruction is to preserve the account-wide limit and never bypass it, so
#: a $20 campaign budget could never bind: the account-wide gate would refuse
#: first, every time, and the campaign number would be decoration. The
#: smaller of the two relevant limits is the real one.
DEFAULT_BUDGET_USD = D("11.10")


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

    def _history_path(self) -> str:
        return os.path.join(self.dir, HISTORY_FILE)

    # ------------------------------------------------------------- journal

    def _journal(self, prev: Mapping[str, Any],
                 nxt: Mapping[str, Any]) -> None:
        """Append the OUTGOING document when a campaign's identity or closed
        state changes. Append-only, one JSON object per line.

        WHY THIS HAD TO EXIST. `_write` is an atomic replace of a single
        document, so `open_or_resume` destroyed the closed campaign's entry in
        place. Production performed exactly that on 2026-10-24T00:31:27Z,
        handing the budget from ...20261023 (final peak_usd 10.35520000 of
        29.30, eight windows exhausted) to ...20261024a, and the only surviving
        record of the campaign it closed was a heartbeat line in a log stream
        with a retention window.

        IT JOURNALS TRANSITIONS, NOT EVERY WRITE. `peak_usd` rises many times
        per campaign and those are not lifecycle events; recording them would
        bury the two that matter. A line is appended only when `campaign_id`
        changes or `closed` flips.

        IT CAN NEVER BREAK THE LEDGER. The journal is written BEFORE the
        document is replaced, and any failure is swallowed: a ledger that
        refuses to record its own budget because an audit file is unwritable
        would be a worse outcome than a missing history line. The ledger write
        is the authority; this is evidence beside it.
        """
        try:
            a, b = prev or {}, nxt or {}
            same_id = str(a.get("campaign_id")) == str(b.get("campaign_id"))
            same_state = bool(a.get("closed")) == bool(b.get("closed"))
            if same_id and same_state:
                return
            if not a.get("campaign_id") and a.get("closed"):
                return                      # the empty placeholder is not news
            rec = dict(a)
            rec["journalled_utc"] = dt.datetime.now(
                dt.timezone.utc).isoformat()
            rec["superseded_by"] = b.get("campaign_id")
            rec["transition"] = ("CLOSED" if bool(b.get("closed"))
                                 and same_id else "REPLACED")
            os.makedirs(self.dir, exist_ok=True)
            with open(self._history_path(), "a") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return

    def history(self) -> List[Dict[str, Any]]:
        """Every superseded campaign document, oldest first. Never raises."""
        out: List[Dict[str, Any]] = []
        try:
            with open(self._history_path(), "r") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return out
        return out

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
        # the outgoing document is journalled FIRST, so a transition cannot be
        # lost even if the process dies between the two writes. A duplicate
        # history line is harmless; a missing one is not recoverable.
        try:
            prev = self.read()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            prev = {}
        self._journal(prev, doc)
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

    def release_stale(self, claimant_id: str, *,
                      legs: Sequence[Mapping[str, Any]] = (),
                      outstanding: Sequence[Mapping[str, Any]] = (),
                      now_iso: str = "") -> Dict[str, Any]:
        """Close a FINISHED campaign's budget so a new one may claim it.

        WHY THIS HAD TO EXIST. `close` is the right gate but nothing ever
        called it, so a campaign's budget stayed OPEN for ever once its
        windows were spent, and every later campaign was refused
        CAMPAIGN_BUDGET_LOCKED. Observed in production on 2026-10-23: the
        0153 campaign could not arm because the 2122 campaign from the
        previous day still owned the ledger, with the venue reporting
        positions {}, openOrders 0 and unsettledFunds 0. The anti-reset
        property was doing its job; the lifecycle around it was missing.

        IT RELEASES ONLY A CAMPAIGN THAT IS PROVABLY FINISHED, on exactly
        the evidence `close` demands -- nothing outstanding, nothing
        unsettled -- and the evidence is supplied by the CALLER from the
        venue-reconciled ledger, never assumed here.

        A CAMPAIGN MAY NOT RELEASE ITSELF. If the open campaign IS the
        claimant, this refuses: closing your own live budget would reset
        `peak_usd` mid-run, which is precisely the reset the lock exists to
        prevent. Use `close` at the end of a campaign for that.

        NO LOSS IS ERASED AND NO HEADROOM IS MINTED. The ledger stores only
        {campaign_id, closed, opened_utc, peak_usd}. Realized losses live in
        the executor's own store and reach the gate through
        `worst_case_usd(realized_usd=...)` on EVERY evaluation, so a release
        cannot touch them: after it, headroom is still
        budget - (realized losses + unsettled cost + outstanding notional).
        What a release clears is the OWNERSHIP claim and the high-water
        mark, neither of which is a spending allowance.
        """
        cid = str(claimant_id or "").strip()
        if not cid:
            return {"ok": False, "reason": "CAMPAIGN_BUDGET_ID_MISSING"}
        doc = self.read()
        open_id = doc.get("campaign_id")
        if not open_id or bool(doc.get("closed")):
            return {"ok": True, "released": False, "reason": "ALREADY_CLOSED"}
        if str(open_id) == cid:
            return {"ok": False, "released": False,
                    "reason": "CANNOT_RELEASE_OWN_OPEN_CAMPAIGN",
                    "campaign_id": str(open_id)}
        if any(True for _ in (outstanding or ())):
            return {"ok": False, "released": False,
                    "reason": "CAMPAIGN_OUTSTANDING_ORDERS",
                    "campaign_id": str(open_id)}
        unsettled = [l for l in (legs or ())
                     if isinstance(l, Mapping) and not bool(l.get("settled"))]
        if unsettled:
            return {"ok": False, "released": False,
                    "reason": "CAMPAIGN_UNSETTLED_EXPOSURE:%d" % len(unsettled),
                    "campaign_id": str(open_id)}
        out = dict(doc)
        out.update({"closed": True, "closed_utc": str(now_iso),
                    "released_for": cid})
        self._write(out)
        return {"ok": True, "released": True,
                "closed_campaign_id": str(open_id), "claimant_id": cid,
                "peak_usd_at_close": str(doc.get("peak_usd") or "0"),
                "labels": ("ownership and high-water mark cleared; realized "
                           "losses are held in the executor's store and are "
                           "recomputed into every gate evaluation")}

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
        return self.refuse_amount(campaign_id=cid, used_usd=used,
                                  new_order_cost_usd=new_order_cost_usd,
                                  record_peak=record_peak, _doc=doc)

    def refuse_amount(self, *,
                      campaign_id: str,
                      used_usd: Any,
                      new_order_cost_usd: Any = 0,
                      record_peak: bool = True,
                      _doc: Optional[Mapping[str, Any]] = None
                      ) -> Optional[str]:
        """The same gate, from a worst case the CALLER already computed.

        WHY THIS ENTRY POINT EXISTS. `ValidationGates` already owns audited
        helpers for exactly these figures -- `cash_committed` skips settled
        legs and honours the cash policy, and `pending_obligation` charges a
        resting BUY_SHORT its full $1-a-share worst case rather than
        qty*price. Recomputing any of that here would be a second opinion
        that could drift from the one the executor actually enforces. So the
        gate takes the number rather than the book, and the book stays the
        responsibility of the layer that already reconciles it.
        """
        cid = str(campaign_id or "").strip()
        if not cid:
            return "CAMPAIGN_BUDGET_ID_MISSING"
        if _doc is None:
            try:
                _doc = self.read()
            except CampaignBudgetUnavailable:
                return "CAMPAIGN_BUDGET_UNREADABLE"
            open_id0 = _doc.get("campaign_id")
            if open_id0 and not bool(_doc.get("closed")) \
                    and str(open_id0) != cid:
                return "CAMPAIGN_BUDGET_LOCKED:%s" % open_id0
        doc = _doc
        open_id = doc.get("campaign_id")
        used = max(D(0), _d(used_usd))
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

    def refuse_arming(self, *, campaign_id: str) -> Optional[str]:
        """The ARMING-time half of the gate. None to allow, else the reason.

        WHY ARMING NEEDS ITS OWN CHECK, and why it is not the per-order one.
        The per-order gate sees the book and refuses the order that would
        breach the budget. But arming happens once per window, before any
        order exists, and an exhausted campaign should not arm a transport at
        all -- a window that can only ever be refused is a window that spent
        a window reservation for nothing, and `WindowLedger.reserve` is
        deliberately not refundable.

        It is also where the LOCK is enforced earliest: a campaign that does
        not own the budget never reaches the order path.
        """
        cid = str(campaign_id or "").strip()
        if not cid:
            return "CAMPAIGN_BUDGET_ID_MISSING"
        try:
            doc = self.read()
        except CampaignBudgetUnavailable:
            return "CAMPAIGN_BUDGET_UNREADABLE"
        open_id = doc.get("campaign_id")
        if open_id and not bool(doc.get("closed")) and str(open_id) != cid:
            return "CAMPAIGN_BUDGET_LOCKED:%s" % open_id
        # Nothing is reserved at arming, so the question is only whether any
        # room remains at all. The peak is the campaign's high-water mark.
        peak = _d(doc.get("peak_usd"))
        if peak >= self.budget_usd:
            return ("CAMPAIGN_WORST_CASE_EXHAUSTED:%s>=%s"
                    % (peak, self.budget_usd))
        return None

    def telemetry(self, *, campaign_id: str = "",
                  realized_usd: Any = None) -> Dict[str, Any]:
        """Non-secret state for the heartbeat. Never raises.

        `realized_usd` is optional and REPORTING ONLY. With it, the block also
        carries `incremental_room_usd` -- the figure the planner actually sizes
        against, which is NOT `budget_usd`.

        WHY IT IS WORTH REPORTING. `open_or_resume` writes peak_usd "0", so a
        new campaign's own usage does start at zero, but the next evaluation
        re-absorbs the account's standing realized loss because
        `worst_case_usd` is account-wide. At the 20261024a handover that read
        peak_usd 10.21 against 8.56 of realized loss plus 1.65 outstanding, so
        the real room was 29.30 - 8.56 = 20.74 and the headline 29.30 invited
        the opposite reading. A fresh nonce buys a fresh WINDOW budget, never a
        fresh loss allowance.

        No gate reads this field.
        """
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
                "incremental_room_usd": (
                    None if realized_usd is None
                    else str(max(D(0), self.budget_usd
                                 - max(D(0), -_d(realized_usd))))),
                "incremental_room_basis": (
                    "budget_usd - account-wide realized LOSS. Reporting only; "
                    "no gate reads it. A fresh campaign nonce resets peak_usd "
                    "but never this."),
                "owns_budget": (bool(doc.get("campaign_id"))
                                and str(doc.get("campaign_id"))
                                == str(campaign_id)),
                "labels": ("worst case = realized losses + unsettled cost + "
                           "outstanding notional. Settlement releases "
                           "exposure; matching does not.")}
