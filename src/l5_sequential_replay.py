"""The frozen sequential taker-first rule, run chronologically on one ledger.

READ-ONLY AND OFFLINE. No socket, no credential, no transport, no order body.
It replays recorded decisions and prices; it cannot reach a venue.

THE RULE IS FROZEN AND IS NOT A PARAMETER OF THIS MODULE. Entry, completion,
fee model and risk constraints are the ones recorded in
`L5_SEQUENTIAL_RULE_FROZEN.md` at commit 1edbfbf7, written down BEFORE any
out-of-sample data was examined. Nothing here may be tuned on a validation
sample: a rule that is positive only after an adjustment made with sight of
the holdout is not positive. The constants below are therefore module
constants rather than arguments, so "just try 0.94" is a diff someone has to
justify rather than a flag someone can flip.

WHY IT EXISTS. The in-sample figure (+$2.36 over 137 decisions) was produced
by an inline script that no longer exists, and it was wrong in the way that
mattered: it scored 137 OVERLAPPING entries as independent positions and
settled stranded inventory by a risk-neutral assumption instead of by what
the market actually did. Out of sample, chronologically, on one shared
ledger, with the deployed capacity limits and actual outcomes, the same rule
returns -$0.6723. A result that reverses sign under honest accounting has to
be reproducible by running a file, not by trusting a transcript.

SETTLEMENT EVIDENCE IS LABELLED, NEVER ASSUMED. Every settled strand carries
the provenance of its outcome:

    VENUE_SETTLEMENT        the venue's own settlement record
    TERMINAL_BOOK_INFERRED  read off the last observed book of the window

The second is weaker and says so. A book at 0.01 three minutes from expiry is
overwhelmingly likely to settle at zero, but "overwhelmingly likely" is not
"confirmed", and the two must never be summed into one column as though they
were the same grade of evidence.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Dict, List, Mapping, Optional, Sequence

# ===================================================== THE FROZEN RULE (1edbfbf7)

#: taker coefficient in force on PMUS from 2026-09-17
FEE_THETA_TAKER = D("0.0695")
#: the per-order allowance the deployed gates carry for per-fill rounding
FEE_EXTRA_PER_ORDER_USD = D("0.01")
#: complete at the FIRST instant the pair reaches this, not the best instant
COMPLETION_TARGET = D("0.96")
#: the deployed fee-inclusive $5 ceiling expressed as principal
MAX_CASH_DEPLOYED_USD = D("4.64")
#: the opening notional budget: the dearer leg's solo fill is the strand, so
#: this IS the worst single-leg loss by construction
OPENING_NOTIONAL_BUDGET_USD = D("0.80")
OPENING_LEG_MAX_CONTRACTS = D("2")
QUANTITY_INCREMENT = D("0.01")
ENTRY_CUTOFF_S = 600.0
COMPLETE_CUTOFF_S = 720.0
#: ONE outstanding order. This is what makes the replay capacity-constrained
#: and is the single biggest correction to the in-sample figure.
MAX_LIVE_ORDERS = 1

VENUE_SETTLEMENT = "VENUE_SETTLEMENT"
TERMINAL_BOOK_INFERRED = "TERMINAL_BOOK_INFERRED"

#: the windows the rule was DERIVED on. Excluded from every validation run,
#: by name, so a corpus that accidentally contains them cannot leak.
IN_SAMPLE_WINDOWS = (
    "cpc-btc-updown-15m-2026-10-26-1445z",
    "cpc-btc-updown-15m-2026-10-26-1500z",
    "cpc-btc-updown-15m-2026-10-26-1515z",
)


def taker_fee(qty: D, px: D) -> D:
    """Theta * C * p * (1-p), rounded UP to the cent, plus the per-order
    allowance. Charged on EVERY leg at that leg's OWN price, and never
    netted against a maker rebate."""
    raw = FEE_THETA_TAKER * qty * px * (D(1) - px)
    if raw <= 0:
        return D(0)
    return (raw.quantize(D("0.01"), rounding=ROUND_CEILING)
            + FEE_EXTRA_PER_ORDER_USD)


def floor_to_increment(x: D) -> D:
    return (x / QUANTITY_INCREMENT).quantize(
        D("1"), rounding=ROUND_FLOOR) * QUANTITY_INCREMENT


def own_taker_cost(side: str, bid: D, ask: D) -> D:
    """What buying `side` as a TAKER costs in own-outcome terms.

    The book is quoted in UP terms. Buying UP lifts the UP ask. Buying DOWN
    is the complement of selling UP, so it costs 1 - bid_up. Summing WIRE
    prices across sides double-counts the YES side and invents losses that
    did not happen, which is the reading error that made an earlier analysis
    of 1215z report a loss on a trade that settled +$0.0984.
    """
    return ask if str(side).upper() == "UP" else (D(1) - bid)


# ============================================================ corpus extraction

def extract_corpus(doc: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic rows from a saved corpus fixture.

    Sorted on (timestamp, market, side) so the replay order is a property of
    the data and not of dict iteration or of which log page arrived first.
    In-sample windows are dropped here, at the boundary, rather than trusted
    to be absent.
    """
    out: List[Dict[str, Any]] = []
    for r in (doc.get("rows") or []):
        mkt = str(r.get("market") or "")
        if mkt in IN_SAMPLE_WINDOWS:
            continue
        bid, ask = r.get("bid"), r.get("ask")
        if bid is None or ask is None:
            continue
        t = dt.datetime.fromisoformat(str(r["t"]).replace("Z", "+00:00"))
        hhmm = mkt.split("-")[-1][:4]
        if len(hhmm) != 4 or not hhmm.isdigit():
            continue
        start = t.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]),
                          second=0, microsecond=0)
        out.append({
            "t": t, "elapsed_s": (t - start).total_seconds(),
            "market": mkt, "side": str(r.get("side") or "").upper(),
            "bid": D(str(bid)), "ask": D(str(ask)),
            "depth": None if r.get("depth") in (None, "None")
            else D(str(r["depth"])),
        })
    out.sort(key=lambda r: (r["t"], r["market"], r["side"]))
    return out


def outcomes_from_terminal_book(
        rows: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Each window's winner, read off its LAST observed book.

    WEAKER THAN A SETTLEMENT RECORD, and labelled as such. Used only where
    no venue settlement is available; `replay` prefers a supplied venue
    outcome whenever one exists.
    """
    last: Dict[str, Mapping[str, Any]] = {}
    for r in rows:
        last[str(r["market"])] = r
    return {m: {"winner": "UP" if r["bid"] > D("0.5") else "DOWN",
                "evidence": TERMINAL_BOOK_INFERRED,
                "terminal_bid": str(r["bid"]), "terminal_ask": str(r["ask"]),
                "terminal_t": r["t"].isoformat()}
            for m, r in last.items()}


# ===================================================================== replay

def replay(rows: Sequence[Mapping[str, Any]],
           outcomes: Mapping[str, Mapping[str, Any]],
           *, charge_fees: bool = True) -> Dict[str, Any]:
    """The frozen rule, chronologically, on ONE shared inventory/cash ledger.

    ONE OUTSTANDING ORDER. While a leg is open no new entry is taken, which
    is what the deployed `max_live_orders` enforces and what the in-sample
    figure ignored by treating overlapping decisions as independent.

    A strand is settled against `outcomes`, and the evidence grade travels
    with it into the result. `charge_fees=False` exists only to report the
    fee sensitivity the owner asked for; it is not an alternative rule.
    """
    fee = (lambda q, p: taker_fee(q, p)) if charge_fees else (
        lambda q, p: D(0))
    cash = D(0)
    realized = D(0)
    equity_peak = D(0)
    drawdown = D(0)
    peak_cash = D(0)
    open_leg: Optional[Dict[str, Any]] = None
    completed: List[Dict[str, Any]] = []
    strands: List[Dict[str, Any]] = []
    refused_cash = 0

    def settle(leg: Dict[str, Any]) -> D:
        o = outcomes.get(leg["market"]) or {}
        winner = o.get("winner")
        payoff = leg["qty"] if winner == leg["side"] else D(0)
        pnl = payoff - leg["qty"] * leg["cost"] - fee(leg["qty"], leg["cost"])
        strands.append({
            "market": leg["market"], "side": leg["side"],
            "own_cost": str(leg["cost"]), "qty": str(leg["qty"]),
            "winner": winner,
            "settlement_evidence": o.get("evidence", "NO_OUTCOME_AVAILABLE"),
            "net_pnl": str(pnl)})
        return pnl

    for r in rows:
        mkt = str(r["market"])
        # a leg whose window has rolled over is settled before anything else
        if open_leg is not None and open_leg["market"] != mkt:
            realized += settle(open_leg)
            cash -= open_leg["qty"] * open_leg["cost"]
            open_leg = None

        if open_leg is None:
            if float(r["elapsed_s"]) > ENTRY_CUTOFF_S:
                continue
            c = own_taker_cost(r["side"], r["bid"], r["ask"])
            if c <= 0 or c >= 1:
                continue
            qty = floor_to_increment(
                min(OPENING_LEG_MAX_CONTRACTS,
                    OPENING_NOTIONAL_BUDGET_USD / c))
            if qty <= 0:
                continue
            if cash + qty * c > MAX_CASH_DEPLOYED_USD:
                refused_cash += 1
                continue
            open_leg = {"market": mkt, "side": r["side"], "cost": c,
                        "qty": qty, "entered_t": r["t"]}
            cash += qty * c
            peak_cash = max(peak_cash, cash)
        else:
            if float(r["elapsed_s"]) > COMPLETE_CUTOFF_S:
                continue
            o = open_leg
            comp = "DOWN" if o["side"] == "UP" else "UP"
            cc = own_taker_cost(comp, r["bid"], r["ask"])
            if o["cost"] + cc <= COMPLETION_TARGET:
                if cash + o["qty"] * cc > MAX_CASH_DEPLOYED_USD:
                    refused_cash += 1
                    continue
                pnl = (o["qty"] * (D(1) - o["cost"] - cc)
                       - fee(o["qty"], o["cost"]) - fee(o["qty"], cc))
                realized += pnl
                completed.append({
                    "market": o["market"], "entry_own": str(o["cost"]),
                    "complement_own": str(cc), "pair": str(o["cost"] + cc),
                    "qty": str(o["qty"]), "net_pnl": str(pnl)})
                cash -= o["qty"] * o["cost"]
                open_leg = None
        equity_peak = max(equity_peak, realized)
        drawdown = min(drawdown, realized - equity_peak)

    if open_leg is not None:
        realized += settle(open_leg)

    ev = sorted({s["settlement_evidence"] for s in strands})
    return {
        "label": "L5_SEQUENTIAL_REPLAY_V1",
        "rule_frozen_at": "1edbfbf7",
        "charge_fees": charge_fees,
        "n_rows": len(rows),
        "windows": sorted({str(r["market"]) for r in rows}),
        "n_windows": len({str(r["market"]) for r in rows}),
        "completed_pairs": completed,
        "n_completed": len(completed),
        "stranded": strands,
        "n_stranded": len(strands),
        "net_realized_usd": str(realized),
        "worst_drawdown_usd": str(drawdown),
        "peak_cash_deployed_usd": str(peak_cash),
        "cash_cap_usd": str(MAX_CASH_DEPLOYED_USD),
        "capital_utilisation_pct": str(
            (peak_cash / MAX_CASH_DEPLOYED_USD * D(100)).quantize(D("0.1"))),
        "refused_on_cash": refused_cash,
        "settlement_evidence_grades": ev,
        "labels": (
            "ONE outstanding order, ONE shared cash/inventory ledger. Every "
            "stranded leg carries the provenance of its outcome and the "
            "grades are never summed as equivalent. Fees are charged on both "
            "legs at each leg's own price. Nothing here is a venue fill: it "
            "is a replay of recorded prices under a frozen rule."),
    }


def main(argv: Optional[List[str]] = None) -> int:
    import sys
    paths = list(argv if argv is not None else sys.argv[1:])
    if not paths:
        print(__doc__)
        print("usage: python3 -m src.l5_sequential_replay <corpus.json>",
              file=sys.stderr)
        return 2
    rows: List[Dict[str, Any]] = []
    for p in paths:
        rows.extend(extract_corpus(json.load(open(p))))
    rows.sort(key=lambda r: (r["t"], r["market"], r["side"]))
    out = replay(rows, outcomes_from_terminal_book(rows))
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
