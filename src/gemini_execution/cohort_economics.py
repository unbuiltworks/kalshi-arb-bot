"""GEMINI_COHORT_ECONOMICS_V1 -- seven lines that are never one number.

WHY THIS IS ITS OWN MODULE. COHORT-1's aggregate stop fired at 4 of its 8
remaining windows on $10.63 of "reconstructed loss" while the four windows it
had records for were **+$1.14** settlement-implied. The measure was
`total_spend_usd` -- cash OUT of the account from confirmed fills, with no
settlement credit, no fee, no rebate and no payout in it. I wrote that
deliberately and called it conservative.

It is not conservative. It is a DIFFERENT QUANTITY. For a strategy that buys
both legs of a binary and is paid on one, gross buy outflow accumulates at
roughly half of turnover no matter whether the cohort is making money or
losing it, so a cap on it is a TURNOVER cap wearing a loss cap's name. Naming
it `reconstructed_loss_usd` is what let it be compared against a dollar loss
limit without anyone noticing the units were wrong.

So the lines are kept apart here, each with its own provenance, and the only
function that produces a single number says in its name which one it is.

    gross_fill_spend_usd     cash OUT, venue-confirmed BUY fills
    committed_open_usd       cash reserved against orders still live
    settlement_payout_usd    cash IN from resolution, $1 per winning contract
    fees_usd                 venue-confirmed, from the venue's own `n` field
    rebates_usd              venue-CREDITED only; never assumed, never modelled
    settlement_implied_net   payout + rebates - spend - fees, SETTLED only
    realized_net             cash-confirmed. Absent until a cashflow is read.

AN UNRESOLVED MARKET CONTRIBUTES NOTHING to the cohort's economic loss. Its
risk is bounded where it has always been bounded: the per-market committed,
residual and exposure gates inside the driver. Guessing its payout would be
inventing the very number this module exists to stop inventing.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_COHORT_ECONOMICS_V1"

#: a winning prediction-market contract pays this. It is the venue's own
#: contract definition, not an assumption about price.
CONTRACT_PAYOUT = D("1")

PENDING = "PENDING_SETTLEMENT"
SETTLED = "SETTLED"
UNRECOVERABLE = "UNRECOVERABLE"


def _d(v: Any, default: str = "0") -> D:
    try:
        return D(str(v))
    except Exception:                                         # noqa: BLE001
        return D(default)


def _fmt(v: Any) -> str:
    """One money string, with trailing zeros stripped and no exponent.

    Not cosmetic: `2.52 + 0.0000` is Decimal 2.5200, and a report that prints
    the same quantity as "1.14" in one place and "1.1400" in another invites
    exactly the kind of eyeball comparison that misses a real difference.
    `format(..., "f")` also keeps 1E+2 from ever reaching a report as "1E+2".
    """
    return format(_d(v).normalize(), "f")


def fees_from_events(events: Optional[Sequence[Mapping[str, Any]]]) -> D:
    """Venue-CONFIRMED fees, summed from the venue's own orderUpdate rows.

    `n` is the fee the venue itself reported on that execution. Nothing is
    modelled: an event without an `n` contributes nothing, because an absent
    fee is unknown, not zero -- and the caller is told how many were missing
    so "no fees" and "no fee data" can never read the same.
    """
    total, seen, missing = D("0"), 0, 0
    for e in events or []:
        if str(e.get("X") or "").upper() not in ("FILLED", "PARTIALLY_FILLED"):
            continue
        seen += 1
        if e.get("n") is None:
            missing += 1
            continue
        total += _d(e.get("n"))
    return total if not missing else total  # value; the count is reported below


def fee_provenance(events: Optional[Sequence[Mapping[str, Any]]]
                   ) -> Dict[str, Any]:
    fills = [e for e in (events or [])
             if str(e.get("X") or "").upper() in ("FILLED",
                                                  "PARTIALLY_FILLED")]
    missing = [e for e in fills if e.get("n") is None]
    return {"fills_seen": len(fills), "fees_missing": len(missing),
            "fees_usd": _fmt(fees_from_events(events)),
            "source": "the venue's own orderUpdate `n` field",
            "note": ("an execution with no `n` contributes nothing and is "
                     "COUNTED here: absent is unknown, never zero")}


def fill_provenance(events: Optional[Sequence[Mapping[str, Any]]]
                    ) -> Dict[str, Any]:
    """MAKER-VS-TAKER, VERIFIED PER FILL from the venue's own `m` flag.

    Not inferred from post-only having been requested: a request is what we
    sent, and `m` is what the venue says happened. The distinction is the
    whole bound -- a taker fill under a maker-only envelope is a violation,
    not a rounding difference -- so an execution whose `m` is ABSENT is
    counted as UNKNOWN and never quietly folded into the maker count.
    """
    fills = [e for e in (events or [])
             if str(e.get("X") or "").upper() in ("FILLED",
                                                  "PARTIALLY_FILLED")]
    maker = [e for e in fills if e.get("m") is True]
    taker = [e for e in fills if e.get("m") is False]
    unknown = [e for e in fills if e.get("m") is None]
    return {
        "fills": len(fills), "maker": len(maker), "taker": len(taker),
        "unknown": len(unknown),
        "all_maker": bool(fills) and len(maker) == len(fills),
        "taker_client_order_ids": [str(e.get("c")) for e in taker],
        "source": "the venue's own orderUpdate `m` flag",
        "note": ("VERIFIED, not assumed from post-only. An execution with no "
                 "`m` is UNKNOWN and is never counted as maker."),
    }


def window_economics(rep: Mapping[str, Any],
                     resolution: Optional[Mapping[str, Any]] = None,
                     rebates_usd: Any = None,
                     fees_usd: Any = None,
                     fee_provenance_in: Optional[Mapping[str, Any]] = None
                     ) -> Dict[str, Any]:
    """The seven lines for ONE window, and which of them are established.

    `resolution` is the venue's own contract object. Without one, or with one
    that is not settled, the window is PENDING: its settlement payout is
    UNKNOWN and is not guessed, and it contributes nothing to cohort loss.

    `rebates_usd` is CREDITED rebate only. The default is None, which means
    "none has been read back", and it is reported as such rather than as zero.

    `fees_usd` / `fee_provenance_in` exist for ONE caller: the settle pass,
    which re-prices a window hours after its order events have been dropped
    from the cohort state file. Recomputing from an empty event list would
    silently report the venue-confirmed fee as 0.00, so the already-confirmed
    figure and its provenance are carried forward instead of re-derived.
    """
    win = (rep.get("window") or {})
    inv = win.get("inventory") or rep.get("inventory") or {}
    events = win.get("order_events") or []

    yes_qty, no_qty = _d(inv.get("yes_qty")), _d(inv.get("no_qty"))
    spend = _d(inv.get("total_spend_usd"))
    fees = fees_from_events(events) if fees_usd is None else _d(fees_usd)
    prov = (fee_provenance(events) if fee_provenance_in is None
            else dict(fee_provenance_in, carried_forward=True))

    out: Dict[str, Any] = {
        "label": LABEL,
        "symbol": win.get("symbol") or (rep.get("cohort_runner") or {}
                                        ).get("symbol"),
        "gross_fill_spend_usd": _fmt(spend),
        "committed_open_usd": _fmt(_d(
            ((win.get("refreshes") or [{}])[-1].get("outstanding") or {}
             ).get("committed_usd"))) if win.get("refreshes") else "0",
        "fees_usd": _fmt(fees),
        "fee_provenance": prov,
        "fill_provenance": fill_provenance(events),
        "rebates_usd": (None if rebates_usd is None else _fmt(rebates_usd)),
        "rebates_note": ("CREDITED rebates only. None read back means None, "
                         "not zero: an assumed rebate is exactly the kind of "
                         "number that makes a bad trade look profitable."),
        "yes_qty": _fmt(yes_qty), "no_qty": _fmt(no_qty),
        "n_fills": inv.get("n_fills"),
        "matched_qty": inv.get("matched_qty"),
        "directional_residual": inv.get("directional_residual"),
        "realized_net_usd": None,
        "realized_note": ("REALIZED requires an attributable cash movement "
                          "read back from the account. Nothing here is it."),
    }

    side = str((resolution or {}).get("resolutionSide") or "").lower()
    settled = (str((resolution or {}).get("status") or "").lower() == "settled"
               and side in ("yes", "no"))
    if not settled:
        out.update(
            state=PENDING,
            resolution_side=None,
            settlement_payout_usd=None,
            settlement_implied_net_usd=None,
            economic_loss_usd=None,
            why=("the contract has not settled, so its payout is UNKNOWN and "
                 "is not guessed. It contributes NOTHING to cohort economic "
                 "loss; its risk is bounded by the per-market committed, "
                 "residual and exposure gates inside the driver."))
        return out

    winners = yes_qty if side == "yes" else no_qty
    payout = winners * CONTRACT_PAYOUT
    credited = D("0") if rebates_usd is None else _d(rebates_usd)
    net = payout + credited - spend - fees
    out.update(
        state=SETTLED,
        resolution_side=side,
        winning_qty=_fmt(winners),
        settlement_payout_usd=_fmt(payout),
        settlement_implied_net_usd=_fmt(net),
        # LOSS IS THE NEGATIVE OF NET, AND A PROFITABLE WINDOW HAS A NEGATIVE
        # LOSS. That is the whole correction: a window that made money REDUCES
        # cumulative economic loss instead of adding its turnover to it.
        economic_loss_usd=_fmt(-net),
        why=("settlement-inclusive: payout + credited rebates - fill spend - "
             "confirmed fees. Settlement-IMPLIED, never REALIZED."))
    return out


def cohort_economics(windows: Sequence[Mapping[str, Any]]
                     ) -> Dict[str, Any]:
    """Roll the per-window lines up WITHOUT collapsing them into one number.

    `cumulative_economic_loss_usd` is the ONLY field the cohort's aggregate
    stop may read, and it sums SETTLED windows only.
    """
    settled = [w for w in windows if w.get("state") == SETTLED]
    # PEAK-TO-TROUGH, IN SETTLEMENT ORDER. Cumulative loss alone cannot see a
    # cohort that made 4.00 and then gave back 4.00: it reads zero throughout
    # while the strategy's recent behaviour is as bad as it has ever been.
    # The equity curve here is settlement-implied and SETTLED-only, exactly
    # like the cumulative figure, so neither measure can be moved by turnover
    # or by an unresolved market.
    equity, peak, dd = D("0"), D("0"), D("0")
    curve = []
    for w in settled:
        equity += _d(w.get("settlement_implied_net_usd"))
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
        curve.append({"symbol": w.get("symbol"),
                      "net_usd": _fmt(w.get("settlement_implied_net_usd")),
                      "equity_usd": _fmt(equity),
                      "drawdown_usd": _fmt(peak - equity)})
    pending = [w for w in windows if w.get("state") == PENDING]
    unrec = [w for w in windows if w.get("state") == UNRECOVERABLE]

    loss = sum((_d(w.get("economic_loss_usd")) for w in settled), D("0"))
    return {
        "label": LABEL,
        "windows_settled": len(settled),
        "windows_pending_settlement": len(pending),
        "windows_unrecoverable": len(unrec),
        "gross_fill_spend_usd": _fmt(sum(
            (_d(w.get("gross_fill_spend_usd")) for w in windows), D("0"))),
        "settlement_payout_usd": _fmt(sum(
            (_d(w.get("settlement_payout_usd")) for w in settled), D("0"))),
        "fees_usd": _fmt(sum((_d(w.get("fees_usd")) for w in windows), D("0"))),
        "rebates_credited_usd": _fmt(sum(
            (_d(w.get("rebates_usd")) for w in windows
             if w.get("rebates_usd") is not None), D("0"))),
        "settlement_implied_net_usd": _fmt(sum(
            (_d(w.get("settlement_implied_net_usd")) for w in settled),
            D("0"))),
        "cumulative_economic_loss_usd": _fmt(loss),
        # THE SECOND BOUND. Peak-to-trough of the settlement-implied equity
        # curve, in settlement order. Always >= 0: a cohort at a new high has
        # no drawdown.
        "max_drawdown_usd": _fmt(dd),
        "equity_peak_usd": _fmt(peak),
        "equity_curve": curve,
        "realized_net_usd": None,
        "labels": (
            "cumulative_economic_loss_usd and max_drawdown_usd are the "
            "ONLY two fields the cohort stops read, and both are computed "
            "over SETTLED windows only. A profitable "
            "window carries a NEGATIVE loss and reduces it. Pending windows "
            "contribute nothing -- their risk lives in the per-market gates. "
            "Unrecoverable windows contribute nothing and are never estimated "
            "from turnover. REALIZED stays null until attributable cash is "
            "read back."),
    }


def unrecoverable_window(symbol: Optional[str], *, gross_spend_usd: Any,
                         why: str, source_run: Optional[str] = None
                         ) -> Dict[str, Any]:
    """A window whose record was lost. It carries what is known and NO MORE.

    Its gross outflow is recorded because it happened; its economic loss is
    NOT, because settlement was never observed and turnover is not loss. It
    contributes nothing to `cumulative_economic_loss_usd`.
    """
    return {
        "label": LABEL, "state": UNRECOVERABLE, "symbol": symbol,
        "gross_fill_spend_usd": _fmt(gross_spend_usd),
        "settlement_payout_usd": None,
        "settlement_implied_net_usd": None,
        "economic_loss_usd": None,
        "fees_usd": "0",
        "rebates_usd": None,
        "source_run": source_run,
        "why": why,
        "labels": (
            "gross outflow is recorded because it is known. ECONOMIC LOSS IS "
            "NOT ESTABLISHED: settlement was never observed, and gross buy "
            "outflow is turnover, not loss. It is excluded from the cohort's "
            "loss counter rather than carried forward as though it were a "
            "loss."),
    }
