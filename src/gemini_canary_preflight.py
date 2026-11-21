"""The exact preflight for ONE bounded Gemini canary order. It places nothing.

WHAT THIS IS. Every value an owner needs to authorize a single passive limit
order, computed from the live venue rather than described. It has no order
transport, imports none, and a test proves it over the AST. Running it is
always safe; it is a read.

WHAT IT IS NOT. It is not an authorization and it is not a submission. It
prints what WOULD be submitted and stops.

THE ENVELOPE IT ENFORCES, from GEMINI_CANARY_AUTHORIZATION_V1 as tightened by
the owner for the FIRST probe:

  exactly one BTC5m window        exactly one passive limit order
  quantity 0.01 contracts         no automatic second order
  no automatic complement         no automatic repeat
  max residual 0.01               venue limit_only
  and NOTHING intended to improve queue priority

That last one is load-bearing. This preflight deliberately quotes at the touch
with tick offset 0, the same placement the shadow used, because changing
placement to chase priority would make the first live result incomparable with
every observation that justified running it.

BALANCE IS REPORTED AS A SUFFICIENCY BOOLEAN, NEVER AS AN AMOUNT. The account's
cash is not needed to authorize half a cent of exposure, and an artifact that
carries it is an artifact that leaks it. `required` is ours to state; whether
the account covers it is the venue's to answer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_CANARY_PREFLIGHT_V1"

#: the tightened first-probe envelope. These are bounds, not suggestions.
QTY = D("0.01")                 # the venue minimum
MAX_ORDERS = 1
MAX_WINDOWS = 1
MAX_RESIDUAL = D("0.01")
SIDE = "BUY"                    # the first leg; no automatic complement
TICK_OFFSET = 0                 # join the touch, exactly as the shadow did

#: a window with less TTE than this cannot rest long enough to be informative
MIN_TTE_S = 60.0
#: and the lane's own TTE_policy band is [0, 300]
MAX_TTE_S = 300.0


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except (TypeError, ValueError, ArithmeticError):
        return None


def expiry_of(symbol: str) -> Optional[int]:
    body = symbol.split("-")[1] if symbol.count("-") >= 2 else ""
    stamp = body[-10:]
    if len(stamp) != 10 or not stamp.isdigit():
        return None
    try:
        return int(datetime.strptime(stamp, "%y%m%d%H%M").replace(
            tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def candidate_symbols(now: Optional[float] = None, n: int = 4) -> List[str]:
    """Live (not yet expired) BTC5m windows, soonest first."""
    t = int(now if now is not None else time.time())
    base = ((t // 300) + 1) * 300
    out = []
    for k in range(n):
        dt = datetime.fromtimestamp(base + k * 300, timezone.utc)
        out.append("GEMI-BTC05M%s-UP" % dt.strftime("%y%m%d%H%M"))
    return out


def venue_terms(symbol: str) -> Dict[str, Any]:
    """tick, minimum and order-type restriction, read from the venue."""
    from src.l5_shadow import venues as V
    r = V.get("%s/v1/symbols/details/%s" % (V.GEMINI, symbol), timeout=12.0)
    if not r.get("ok"):
        return {"readable": False, "err": r.get("err")}
    try:
        b = json.loads(r["body"])
    except ValueError:
        return {"readable": False, "err": "unparseable"}
    return {"readable": True, "tick_size": b.get("tick_size"),
            "quote_increment": b.get("quote_increment"),
            "min_order_size": b.get("min_order_size"),
            "status": b.get("status")}


def book_of(symbol: str) -> Dict[str, Any]:
    from src.l5_shadow import venues as V
    b = V.gemini_book_rest(symbol)
    if not b:
        return {"readable": False}
    bids, asks = b.get("bids") or [], b.get("asks") or []

    def top(levels):
        if not levels:
            return None, None
        return levels[0].get("price"), levels[0].get("amount")

    bp, bq = top(bids)
    ap, aq = top(asks)
    return {"readable": True, "best_bid": bp, "bid_qty": bq,
            "best_ask": ap, "ask_qty": aq,
            "n_bid_levels": len(bids), "n_ask_levels": len(asks),
            "source_ts": b.get("source_ts")}


def choose_window(now: Optional[float] = None) -> Dict[str, Any]:
    """Exactly ONE window, chosen on INSTRUMENT QUALITY, not on queue position.

    Among the windows that meet the envelope, this prefers the one whose book
    is genuinely two-sided and tightest. That is a judgement about which
    instrument is worth testing -- a 0.60/0.99 book with a single ask level is
    a degenerate quote, not a market -- and it is deliberately NOT a judgement
    about where we would sit in the queue. Depth at the touch is REPORTED so
    the owner can see the fill prospect, and it is not optimised.
    """
    t = now if now is not None else time.time()
    rejected: List[Dict[str, Any]] = []
    eligible: List[Dict[str, Any]] = []
    for sym in candidate_symbols(t):
        exp = expiry_of(sym)
        if exp is None:
            rejected.append({"symbol": sym, "why": "unparseable expiry"})
            continue
        tte = exp - t
        if not (MIN_TTE_S <= tte <= MAX_TTE_S):
            rejected.append({"symbol": sym, "tte_s": round(tte, 1),
                             "why": "TTE outside [%s, %s]"
                                    % (MIN_TTE_S, MAX_TTE_S)})
            continue
        bk = book_of(sym)
        bid, ask = _d(bk.get("best_bid")), _d(bk.get("best_ask"))
        if not bk.get("readable") or bid is None or ask is None:
            rejected.append({"symbol": sym, "tte_s": round(tte, 1),
                             "why": "book not two-sided"})
            continue
        if not (D("0") < bid < ask < D("1")):
            rejected.append({"symbol": sym, "tte_s": round(tte, 1),
                             "why": "bid/ask not a sane two-sided quote"})
            continue
        eligible.append({"symbol": sym, "expiry_epoch": exp,
                         "tte_s": round(tte, 1), "book": bk,
                         "spread": str(ask - bid),
                         "both_sides_deep": bool(bk["n_bid_levels"] >= 2
                                                 and bk["n_ask_levels"] >= 2)})
    if not eligible:
        return {"chosen": None, "rejected": rejected, "eligible": [],
                "why": "no live window met the envelope"}
    # deep-both-sides first, then tightest spread, then soonest
    ranked = sorted(eligible, key=lambda e: (not e["both_sides_deep"],
                                             D(e["spread"]), e["tte_s"]))
    best = ranked[0]
    return {"chosen": best["symbol"], "expiry_epoch": best["expiry_epoch"],
            "tte_s": best["tte_s"], "book": best["book"],
            "selection_basis": ("genuinely two-sided first, then tightest "
                                "spread. Instrument quality, never queue "
                                "position."),
            "eligible": [{k: e[k] for k in
                          ("symbol", "tte_s", "spread", "both_sides_deep")}
                         for e in ranked],
            "rejected": rejected}


async def account_checks(required_usd: D) -> Dict[str, Any]:
    """Authenticated READS only: fee schedule, balance SUFFICIENCY, and that
    every read path the acceptance chain needs still answers."""
    from src.gemini_prediction_auth import has_credentials, private_rest_request
    if not has_credentials():
        return {"ok": False, "verdict": "NO_CREDENTIALS",
                "why": ("run where the Gemini credentials live; this module "
                        "holds none")}

    async def rd(path: str) -> Dict[str, Any]:
        try:
            r = await private_rest_request("POST", path, timeout=15.0)
        except Exception as exc:                              # noqa: BLE001
            return {"path": path, "status": None, "reachable": False,
                    "error": type(exc).__name__}
        try:
            return {"path": path, "status": r.status_code, "reachable": True,
                    "body": r.json()}
        except ValueError:
            return {"path": path, "status": r.status_code, "reachable": True,
                    "body": None, "unparseable": True}

    bal = await rd("/v1/balances")
    vol = await rd("/v1/notionalvolume")
    pos = await rd("/v1/prediction-markets/positions")
    trd = await rd("/v1/mytrades")

    # SUFFICIENCY ONLY. The amount never leaves this function.
    sufficient: Optional[bool] = None
    if isinstance(bal.get("body"), list):
        for row in bal["body"]:
            if not isinstance(row, dict):
                continue
            if str(row.get("currency") or "").upper() != "USD":
                continue
            avail = _d(row.get("available")) or _d(row.get("amount"))
            if avail is not None:
                sufficient = bool(avail >= required_usd)
            break

    fees = {}
    if isinstance(vol.get("body"), dict):
        fees = {k: vol["body"].get(k) for k in
                ("api_maker_fee_bps", "api_taker_fee_bps")}

    return {
        "ok": True,
        "balance_sufficient_for_required": sufficient,
        "balance_required_usd": str(required_usd),
        "balance_amount_reported": "WITHHELD_BY_DESIGN",
        "fee_bps": fees,
        "read_paths": {p["path"]: {"status": p.get("status"),
                                   "reachable": p.get("reachable")}
                       for p in (bal, vol, pos, trd)},
    }


def build(now: Optional[float] = None) -> Dict[str, Any]:
    """The public half of the preflight: everything that needs no credential."""
    w = choose_window(now)
    if not w.get("chosen"):
        return {"label": LABEL, "ok": False, "verdict": "NO_ELIGIBLE_WINDOW",
                "rejected": w.get("rejected"), "why": w.get("why")}
    sym = w["chosen"]
    bk = w["book"]
    terms = venue_terms(sym)
    bid, ask = _d(bk["best_bid"]), _d(bk["best_ask"])
    tick = _d(terms.get("tick_size")) or D("0.01")
    limit = bid - (TICK_OFFSET * tick)         # offset 0 -> join the touch
    exposure = (limit * QTY).quantize(D("0.000001"))
    return {
        "label": LABEL, "ok": True,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "instrument": sym,
        "side": SIDE,
        "limit_price": str(limit),
        "quantity": str(QTY),
        "maximum_cash_exposure_usd": str(exposure),
        "current_bbo": {"bid": bk["best_bid"], "bid_qty": bk["bid_qty"],
                        "ask": bk["best_ask"], "ask_qty": bk["ask_qty"],
                        "levels": "%d bid / %d ask" % (bk["n_bid_levels"],
                                                       bk["n_ask_levels"]),
                        "source_ts": bk.get("source_ts")},
        "restability": {
            "restable": bool(ask is not None and limit < ask),
            "why": ("a BUY below the ask rests; at or above it would cross "
                    "and take"),
            "tick_offset_from_touch": TICK_OFFSET,
            "queue_priority_note": (
                "joins the touch exactly as the shadow did. NOTHING here is "
                "intended to improve queue priority, because a placement "
                "change would make this first live result incomparable with "
                "the observations that justified running it."),
        },
        "post_only": {
            "venue_status": terms.get("status"),
            "enforced_by": ("the VENUE: status limit_only means it rejects "
                            "anything but a limit order on this instrument"),
        },
        "tte_s": w["tte_s"],
        "expiry_utc": datetime.fromtimestamp(
            w["expiry_epoch"], timezone.utc).isoformat(),
        "venue_terms": terms,
        "envelope": {
            "windows": MAX_WINDOWS, "orders": MAX_ORDERS,
            "quantity": str(QTY), "max_residual": str(MAX_RESIDUAL),
            "automatic_second_order": False,
            "automatic_complement": False,
            "automatic_repeat": False,
        },
        "rejected_windows": w.get("rejected"),
        "eligible_windows": w.get("eligible"),
        "selection_basis": w.get("selection_basis"),
        "fill_prospect": _fill_prospect(bk, limit, bid, ask),
        "what_this_proves": {
            "if_it_FILLS": [
                "the durable-intent path wrote before the venue mutation",
                "/v1/mytrades returns OUR fill with the venue's own fee field",
                "/v1/prediction-markets/positions POPULATES -- the one thing "
                "no read can establish today, because it needs a position to "
                "exist through an expiry",
                "settlement cashflow lands and reconciles against our legs",
            ],
            "if_it_does_NOT_fill": [
                "SUBMITTED -> UNFILLED -> EXPIRED is exercised, and the "
                "authoritative order-state read path with it",
                "the settlement and position chain is NOT exercised: with no "
                "position there is nothing for the venue to settle",
            ],
            "stated_plainly": (
                "THE ACCOUNTING HALF OF THIS CANARY NEEDS A FILL. The lane is "
                "queue-bound, so an unfilled first order is the likely "
                "outcome and would leave the settlement question exactly "
                "where it is. That is a cost of the envelope, not a defect "
                "in it, and it is the owner's call to accept: the envelope "
                "forbids anything intended to improve queue priority, and "
                "improving it is the only thing that would raise the fill "
                "probability."),
        },
    }


def _fill_prospect(bk: Mapping[str, Any], limit: D, bid: Optional[D],
                   ask: Optional[D]) -> Dict[str, Any]:
    """What would have to clear for this order to fill. Not a forecast.

    VOLUME_AHEAD_CLEARED assumes we join behind EVERYTHING already resting at
    our price, which is the strictest available model and a LOWER bound on
    fills. Our real queue position is unpublished and may be better.
    """
    ahead = _d(bk.get("bid_qty")) if (bid is not None and limit == bid) else None
    return {
        "volume_ahead_at_our_price": (str(ahead) if ahead is not None
                                      else "UNKNOWN"),
        "our_quantity": str(QTY),
        "must_clear_before_we_fill": (str(ahead) if ahead is not None
                                      else "UNKNOWN"),
        "spread": (str(ask - bid) if (ask is not None and bid is not None)
                   else None),
        "model": ("VOLUME_AHEAD_CLEARED: assumes we joined behind everything "
                  "resting, so it is a LOWER bound. Real priority is "
                  "unpublished and may be better."),
        "caveat": ("an UNKNOWN queue can never be shown to clear, and zero is "
                   "never substituted for it"),
    }


def paths() -> Dict[str, Any]:
    """The four read/write paths the acceptance chain runs on."""
    return {
        "durable_intent": {
            "path": "written BEFORE the venue mutation, never after",
            "module": "src.l5_surface_exec.adapter (record/persist_intent)",
            "why": ("an order that exists at the venue and not in our ledger "
                    "is the one state we cannot reconcile"),
        },
        "authoritative_order_fill": {
            "path": "/v1/mytrades", "status_today": "200, 0 rows",
            "why": ("the venue's own record with its own fee fields. "
                    "VOLUME_AHEAD_CLEARED is OUR model of an unpublished "
                    "queue and is never admissible here"),
        },
        "settlement_position": {
            "path": "/v1/prediction-markets/positions",
            "status_today": "200, {positions[], total}, 0 rows",
            "why": ("the only authoritative outcome source; the PUBLIC "
                    "surface publishes no terminal value at all"),
        },
        "cash_reconciliation": {
            "path": "/v1/balances + /v1/transfers",
            "status_today": "200; transfers carries 9 rows with purpose/status",
            "why": ("REALIZED is the venue's cash movement. Our arithmetic "
                    "over the legs is reported beside it as a cross-check, "
                    "and when they disagree the venue is right"),
        },
    }


def kill_state() -> Dict[str, Any]:
    return {
        "armed": False,
        "submission_reachable": False,
        "why": ("this module has no order transport and imports none; a test "
                "asserts it over the AST"),
        "kill_conditions": [
            "any order that is not a limit_only maker",
            "residual exceeding %s contracts" % MAX_RESIDUAL,
            "more than %d outstanding order or more than %d market"
            % (MAX_ORDERS, MAX_WINDOWS),
            "total notional exceeding the stated maximum cash exposure",
            "venue_vs_ledger disagreement on a resumed session",
            "any settlement endpoint that stops answering mid-run",
            "an unreadable order state that cannot be resolved to terminal",
        ],
        "deadman": ("existing risk/deadman protections remain in force and "
                    "are not modified by this probe"),
        "fail_closed": ("the session does NOT relaunch after a crash or a "
                        "completion"),
    }


def status_ladder() -> List[str]:
    """The only permitted progression. Nothing skips, nothing jumps to the end."""
    return ["SUBMITTED", "FILLED_OR_UNFILLED", "EXPIRED_OR_SETTLED",
            "RECONCILED", "REALIZED"]


async def preflight_async(now: Optional[float] = None) -> Dict[str, Any]:
    pub = build(now)
    if not pub.get("ok"):
        return {**pub, "authorization": "NOT GRANTED. Nothing is submitted."}
    acct = await account_checks(D(pub["maximum_cash_exposure_usd"]))
    return {
        **pub,
        "fee_treatment": {
            "venue_fee_bps": (acct.get("fee_bps") if acct.get("ok")
                              else "UNREAD_WITHOUT_CREDENTIALS"),
            "rule": ("fees are their own line and are NEVER netted into a "
                     "price; rebates and rewards stay in separate ledgers and "
                     "enter nothing until credited and source-accounted"),
        },
        "balance_sufficiency": (
            {k: acct[k] for k in ("balance_sufficient_for_required",
                                  "balance_required_usd",
                                  "balance_amount_reported")}
            if acct.get("ok") else {"verdict": acct.get("verdict")}),
        "read_paths_live": (acct.get("read_paths") if acct.get("ok")
                            else "UNREAD_WITHOUT_CREDENTIALS"),
        "paths": paths(),
        "kill_deadman": kill_state(),
        "status_ladder": status_ladder(),
        "authorization": (
            "NOT GRANTED. This is a preflight, not a submission. No order is "
            "placed by this module and none may be placed without explicit "
            "owner authorization."),
        "labels": [
            "an execution and ACCOUNTING canary, not a profitability test: "
            "the lane is queue-bound and expected P&L at this size is ~zero.",
            "never realized before venue-confirmed cash reconciliation.",
        ],
    }


OUT_DIR = "analysis/gemini"


def persist(out: Mapping[str, Any]) -> Optional[str]:
    """Write the preflight artifact. A write failure is reported, never fatal."""
    import pathlib
    try:
        d = pathlib.Path(OUT_DIR)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        f = d / ("canary_preflight_%s.json" % stamp)
        f.write_text(json.dumps(out, indent=2, default=str) + "\n",
                     encoding="utf-8")
        return str(f)
    except OSError:
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-persist", action="store_true")
    a = ap.parse_args(argv)
    out = asyncio.run(preflight_async())
    if not a.no_persist:
        out["artifact"] = persist(out)
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
