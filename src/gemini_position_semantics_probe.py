"""Read the venue's OWN record of how it represented the CONTINUOUS-4 position.

READ-ONLY, AND STRUCTURALLY SO. This module imports no order transport, holds
no Driver, and POSTs only to Gemini's private READ endpoints -- which take POST
because that is how Gemini authenticates a read, not because anything here
mutates. A test walks the AST and asserts it.

THE QUESTION IT ANSWERS. On 2026-09-18 the account filled BOTH outcomes of one
contract:

    NO  buy 0.01 @ 0.14   orderId 145828836246611835   filled 17:27:59.492Z
    YES buy 0.01 @ 0.92   orderId 145828836246613472   filled 17:28:33.389Z

The YES order was placed 6.4 s AFTER the NO leg had already filled, on the same
instrument, and was accepted and filled. What we have never established is what
the venue then HELD, because `/v1/prediction-markets/positions` has answered 200
with zero rows in every capture and no position read was taken between the
second fill and the 17:30:00Z expiry.

`POST /v1/prediction-markets/positions/settled` is the endpoint that answers it
and the one endpoint in this area the repository has never called. Its published
schema carries a SIGNED `position` -- "Positive values represent a `yes`
position; negative values represent a `no` position" -- so the row shape alone
discriminates:

    two rows for the symbol, +0.01 yes and -0.01 no   -> A, independent legs
    one row, or none plus a realizedPnl/cash-out      -> B or C, netted

`withCashOuts=true` additionally exposes the `CashedOutPosition` rows the venue
uses for "early position exits", which is the API's name for what the consumer
UI calls cashing out.

IT FAILS CLOSED. Without credentials it reports NO_CREDENTIALS and exits
non-zero rather than degrading to a partial answer. Nothing it prints is
REALIZED P&L: it reports the venue's own fields and reconciles them, and where
the venue does not say, it says the venue does not say.

SECRETS NEVER LEAVE. The key and secret are read by the auth module and are
never printed. `accountId` is redacted on the way out.

    python3 -m src.gemini_position_semantics_probe
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from src.gemini_prediction_auth import (
    GEMINI_REST,
    credentials,
    has_credentials,
    private_rest_request,
    rest_auth_headers,
)

LABEL = "GEMINI_POSITION_SEMANTICS_PROBE_V1"

SYMBOL = "GEMI-BTC05M2609181730-UP"
EVENT_TICKER = "BTC05M2609181730"

#: what the venue's own order history recorded, quoted so the reconciliation
#: below is anchored to evidence already in the repository rather than to a
#: memory of it. Source: analysis/gemini/continuous_run_20260918T172750Z.json
KNOWN_FILLS = (
    {"orderId": "145828836246611835", "outcome": "no", "side": "buy",
     "qty": "0.01", "price": "0.14", "filled_utc": "2026-09-18T17:27:59.492Z"},
    {"orderId": "145828836246613472", "outcome": "yes", "side": "buy",
     "qty": "0.01", "price": "0.92", "filled_utc": "2026-09-18T17:28:33.389Z"},
)

#: every path here RETURNS data and changes nothing.
SETTLED_PATH = "/v1/prediction-markets/positions/settled"
OPEN_PATH = "/v1/prediction-markets/positions"
TRADES_PATH = "/v1/mytrades"
TRANSFERS_PATH = "/v1/transfers"

REDACT_KEYS = ("accountId", "account_id", "apiKey", "key", "secret")


def _redact(o: Any) -> Any:
    """Strip account identity out of anything on its way to a log."""
    if isinstance(o, dict):
        return {k: ("REDACTED" if k in REDACT_KEYS else _redact(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [_redact(v) for v in o]
    return o


async def _read_payload(path: str, params: Optional[Dict[str, Any]] = None
                        ) -> Dict[str, Any]:
    """One authenticated read, fields in the signed payload. Never raises."""
    try:
        resp = await private_rest_request("POST", path, timeout=20.0,
                                          params=params)
    except Exception as exc:                                   # noqa: BLE001
        return {"path": path, "how": "payload", "params": params,
                "status": None, "error": type(exc).__name__, "reachable": False}
    out: Dict[str, Any] = {"path": path, "how": "payload", "params": params,
                           "status": resp.status_code, "reachable": True}
    try:
        out["body"] = _redact(resp.json())
    except ValueError:
        out["body"] = None
        out["unparseable"] = resp.text[:300]
    return out


async def _read_query(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Same read with the fields ALSO on the query string.

    The Prediction Markets reference documents these as query parameters while
    Gemini's classic private convention puts them in the signed payload. The
    venue has been measured to read both, but a filter silently ignored would
    look exactly like a filter that matched nothing -- so both shapes are tried
    and reported separately rather than one being assumed.
    """
    key, secret = credentials()
    if not key or not secret:
        return {"path": path, "how": "query", "params": params,
                "status": None, "error": "NO_CREDENTIALS", "reachable": False}
    url = f"{GEMINI_REST}{path}?{urlencode(params)}"
    try:
        headers = rest_auth_headers(key, secret, path, params=params)
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.request("POST", url, headers=headers,
                                        content=b"")
    except Exception as exc:                                   # noqa: BLE001
        return {"path": path, "how": "query", "params": params,
                "status": None, "error": type(exc).__name__, "reachable": False}
    out: Dict[str, Any] = {"path": path, "how": "query", "params": params,
                           "status": resp.status_code, "reachable": True}
    try:
        out["body"] = _redact(resp.json())
    except ValueError:
        out["body"] = None
        out["unparseable"] = resp.text[:300]
    return out


def _rows(read: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    body = read.get("body")
    if isinstance(body, dict):
        v = body.get(key)
        return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    return []


def _for_symbol(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        s = str(r.get("instrumentSymbol") or r.get("symbol") or "")
        if s.upper() == SYMBOL.upper():
            out.append(r)
    return out


def classify(settled_rows: List[Dict[str, Any]],
             cashouts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Name the model the venue's own rows support. No inference from ours.

    This reads the VENUE's rows only. Our Inventory class is not consulted and
    is not importable from here.
    """
    n = len(settled_rows)
    signs = []
    for r in settled_rows:
        raw = r.get("position")
        try:
            signs.append(D(str(raw)))
        except Exception:                                      # noqa: BLE001
            signs.append(None)
    outcomes = [str(r.get("outcome") or "").lower() for r in settled_rows]

    if cashouts:
        return {"model": "C", "why": (
            "the venue recorded a CashedOutPosition for this instrument: the "
            "opposite BUY was represented as an early exit of the existing "
            "position, not as a second inventory"), "n_settled_rows": n}
    if n >= 2 and {"yes", "no"} <= set(outcomes):
        return {"model": "A", "why": (
            "the venue settled TWO rows on ONE instrument, one yes and one no. "
            "Independent inventories coexisted and each redeemed on its own "
            "terms"), "n_settled_rows": n}
    if n == 1:
        p = signs[0]
        return {"model": "B", "why": (
            "the venue settled exactly ONE row for this instrument carrying a "
            "single signed position (%s, outcome %s). The second outcome "
            "reduced or flipped the first rather than opening beside it"
            % (p, outcomes[0] if outcomes else None)), "n_settled_rows": n}
    if n == 0:
        return {"model": "B_OR_C", "why": (
            "the venue settled NO row for this instrument although both legs "
            "filled. A position that does not reach settlement was closed "
            "before it -- netted (B) or cashed out (C) -- but this response "
            "alone does not say which. UNRESOLVED between B and C"),
            "n_settled_rows": 0}
    return {"model": "D", "why": (
        "%d settled rows with outcomes %s -- none of A, B or C describes this"
        % (n, outcomes)), "n_settled_rows": n}


def reconcile(settled_rows: List[Dict[str, Any]],
              cashouts: List[Dict[str, Any]],
              trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Cashflows, each line naming the venue field it came from.

    Where the venue publishes no field, the line says so. Nothing here is
    labelled realized on our own arithmetic.
    """
    legs = []
    gross_debit = D("0")
    fees = D("0")
    fee_source = "venue /v1/mytrades fee_amount"
    for f in KNOWN_FILLS:
        cash = -(D(f["qty"]) * D(f["price"]))
        gross_debit += cash
        legs.append({"orderId": f["orderId"], "outcome": f["outcome"],
                     "side": f["side"], "qty": f["qty"], "price": f["price"],
                     "filled_utc": f["filled_utc"], "cash_usd": str(cash),
                     "source": "venue order history (already captured)"})
    matched_trades = []
    for t in trades:
        if str(t.get("symbol") or "").upper() != SYMBOL.upper():
            continue
        matched_trades.append(t)
        try:
            fees += D(str(t.get("fee_amount") or "0"))
        except Exception:                                      # noqa: BLE001
            fee_source = "UNPARSEABLE fee_amount on at least one row"

    payout = D("0")
    payout_present = False
    cost_basis: Optional[D] = None
    realized: Optional[D] = None
    net_profit: Optional[D] = None
    for r in settled_rows:
        if r.get("payout") is not None:
            payout_present = True
            try:
                payout += D(str(r["payout"]))
            except Exception:                                  # noqa: BLE001
                payout_present = False
        for field, name in ((("costBasis"), "cost_basis"),
                            (("realizedPnl"), "realized"),
                            (("netProfit"), "net_profit")):
            if r.get(field) is None:
                continue
            try:
                v = D(str(r[field]))
            except Exception:                                  # noqa: BLE001
                continue
            if name == "cost_basis":
                cost_basis = (cost_basis or D("0")) + v
            elif name == "realized":
                realized = (realized or D("0")) + v
            else:
                net_profit = (net_profit or D("0")) + v

    proceeds: Optional[D] = None
    for c in cashouts:
        if str(c.get("instrumentSymbol") or "").upper() != SYMBOL.upper():
            continue
        try:
            proceeds = (proceeds or D("0")) + D(str(c.get("proceeds") or "0"))
        except Exception:                                      # noqa: BLE001
            pass

    return {
        "legs": legs,
        "gross_cash_debit_usd": str(gross_debit),
        "fees_usd": (str(fees) if matched_trades else None),
        "fees_source": (fee_source if matched_trades
                        else "NO /v1/mytrades row matched this symbol"),
        "cash_out_proceeds_usd": (str(proceeds) if proceeds is not None
                                  else None),
        "settlement_payout_usd": (str(payout) if payout_present else None),
        "venue_cost_basis_usd": (str(cost_basis) if cost_basis is not None
                                 else None),
        "venue_realized_pnl_usd": (str(realized) if realized is not None
                                   else None),
        "venue_net_profit_usd": (str(net_profit) if net_profit is not None
                                 else None),
        "terminal_signed_position": [
            {"outcome": r.get("outcome"), "position": r.get("position"),
             "positionQuantity": r.get("positionQuantity"),
             "resolutionSide": r.get("resolutionSide")}
            for r in settled_rows],
        "labels": (
            "Every non-null figure above is a VENUE field or a sum of venue "
            "fields. `netProfit` is the venue's own `payout - costBasis + "
            "realizedPnl`. A null is the venue declining to say, never zero, "
            "and nothing here is promoted to REALIZED without an attributable "
            "cash movement."),
    }


async def main_async(limit: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"label": LABEL, "symbol": SYMBOL,
                           "known_fills": list(KNOWN_FILLS)}
    reads: List[Dict[str, Any]] = []

    # 1. THE DECIDING READ, in four shapes so a silently-ignored filter cannot
    #    be mistaken for an empty account.
    reads.append(await _read_payload(
        SETTLED_PATH, {"withCashOuts": True, "limit": limit}))
    reads.append(await _read_query(
        SETTLED_PATH, {"withCashOuts": "true", "limit": limit}))
    reads.append(await _read_payload(
        SETTLED_PATH, {"withCashOuts": True, "eventTicker": EVENT_TICKER}))
    reads.append(await _read_query(
        SETTLED_PATH, {"withCashOuts": "true", "eventTicker": EVENT_TICKER}))
    # 2. the open-position surface, for the record
    reads.append(await _read_payload(OPEN_PATH, None))
    # 3. the venue's own fills, for fees and cash
    reads.append(await _read_payload(TRADES_PATH, {"symbol": SYMBOL,
                                                   "limit_trades": 100}))
    # 4. cash movements, where a settlement credit would land.
    #    ORDER HISTORY IS DELIBERATELY NOT READ HERE. Its path is a prefix of
    #    the venue's order-placement path, and the repo-wide write-path
    #    isolation guard matches on substring -- correctly, since a prefix
    #    match is what stops a placement path being smuggled in beside a
    #    read. Narrowing that guard to admit this convenience would trade a
    #    safety property for a read we do not need: the two orders are
    #    already captured verbatim in
    #    analysis/gemini/continuous_run_20260918T172750Z.json and are
    #    anchored above in KNOWN_FILLS.
    reads.append(await _read_payload(TRANSFERS_PATH, {"limit_transfers": 50}))

    out["reads"] = reads

    settled_rows: List[Dict[str, Any]] = []
    cashouts: List[Dict[str, Any]] = []
    settled_total_seen: Optional[int] = None
    for r in reads:
        if r.get("path") != SETTLED_PATH or r.get("status") != 200:
            continue
        rows = _for_symbol(_rows(r, "positions"))
        for row in rows:
            if row not in settled_rows:
                settled_rows.append(row)
        for c in _for_symbol(_rows(r, "cashOuts")):
            if c not in cashouts:
                cashouts.append(c)
        body = r.get("body")
        if isinstance(body, dict) and isinstance(body.get("total"), int):
            settled_total_seen = body["total"]

    trades = []
    for r in reads:
        if r.get("path") == TRADES_PATH and r.get("status") == 200:
            trades.extend(_rows(r, "trades") or
                          (r.get("body") if isinstance(r.get("body"), list)
                           else []))

    out["settled_rows_for_symbol"] = settled_rows
    out["cash_outs_for_symbol"] = cashouts
    out["settled_total_rows_reported"] = settled_total_seen
    out["verdict"] = classify(settled_rows, cashouts)
    out["reconciliation"] = reconcile(settled_rows, cashouts, trades)
    out["labels"] = (
        "READ-ONLY. No order was placed, cancelled or modified by this module "
        "and it imports no path that could. SHADOW/REALIZED labelling is "
        "unchanged: a venue field is evidence, our arithmetic over it is a "
        "cross-check.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    if not has_credentials():
        print(json.dumps({"label": LABEL, "state": "NO_CREDENTIALS",
                          "why": ("GEMINI_API_KEY/GEMINI_API_SECRET are not "
                                  "present in this environment. Failing "
                                  "closed rather than reporting a partial "
                                  "answer.")}, indent=1))
        return 2
    rep = asyncio.run(main_async(args.limit))
    print(json.dumps(rep, indent=1, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
