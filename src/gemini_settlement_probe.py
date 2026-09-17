"""Read the account surface that could close the Gemini settlement gate.

READ-ONLY, AND STRUCTURALLY SO. There is no order transport in this module and
no import that reaches one; a test walks the AST and asserts it. It POSTs only
to Gemini's private READ endpoints, which take POST because that is how Gemini
authenticates a read, not because anything here mutates.

WHAT IT IS FOR. `src.gemini_settlement` states what would have to be true
before a Gemini position counts as REALIZED, and reports BLOCKED because every
remaining source is authenticated. This is the thing that goes and looks --
where the credentials already live, which is not this container.

  /v1/mytrades?symbol=   the venue's own record of what we filled, with its
                         own fee fields. This is the ONLY admissible fill.
  /v1/balances           account cash, as an anchor
  /v1/transfers          where a settlement credit is most likely to land

WHERE SETTLEMENT LIVES IS THE FINDING. Gemini does not document which endpoint
carries a prediction-contract settlement, so this probes each and REPORTS
availability per endpoint rather than assuming one. A 404 is a result. That is
the same method `src.gemini_maker.subsidy_verify` used for rebate credits, and
for the same reason: guessing which endpoint pays would produce a confident
number from an endpoint that pays nothing.

IT FAILS CLOSED. Without credentials it reports NO_CREDENTIALS and exits
non-zero rather than degrading to a partial answer, so running it in the wrong
place cannot produce a half-filled gate that looks closed.

NOTHING HERE IS REALIZED P&L. It assembles evidence and hands it to the gate,
which refuses on its own terms. A response this module could not parse is
reported as unparseable, never as an absence.

    python3 -m src.gemini_settlement_probe --symbols GEMI-BTC05M...-UP
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, Dict, List, Optional, Sequence

from src import gemini_settlement as S
from src.gemini_prediction_auth import has_credentials, private_rest_request

LABEL = "GEMINI_SETTLEMENT_PROBE_V1"

#: read endpoints only. Every one of these returns data; none of them changes
#: anything. Settlement-bearing candidates are probed rather than assumed.
READ_PATHS = (
    "/v1/balances",
    "/v1/transfers",
    "/v1/notionalvolume",
)

#: speculative settlement surfaces. A 404 here IS the finding, not a failure.
SETTLEMENT_CANDIDATES = (
    "/v1/prediction-markets/settlements",
    "/v1/prediction-markets/positions",
    "/v1/settlements",
)


async def _read(path: str, params: Optional[Dict[str, Any]] = None
                ) -> Dict[str, Any]:
    """One authenticated read. Never raises; a failure is a reported result."""
    try:
        resp = await private_rest_request("POST", path, timeout=15.0,
                                          params=params)
    except Exception as exc:                                   # noqa: BLE001
        return {"path": path, "status": None, "error": type(exc).__name__,
                "reachable": False}
    out: Dict[str, Any] = {"path": path, "status": resp.status_code,
                           "reachable": True}
    try:
        out["body"] = resp.json()
    except ValueError:
        # an unparseable response is reported as such. Reporting it as empty
        # would read as "the venue has nothing", which is a different claim.
        out["body"] = None
        out["unparseable"] = resp.text[:300]
    return out


def legs_from_mytrades(symbol: str, body: Any) -> List[S.Leg]:
    """Authoritative fills, or nothing. A row we cannot read is skipped and
    counted by the caller rather than guessed at."""
    legs: List[S.Leg] = []
    if not isinstance(body, list):
        return legs
    for row in body:
        if not isinstance(row, dict):
            continue
        side = str(row.get("type") or row.get("side") or "").upper()
        if side not in ("BUY", "SELL"):
            continue
        try:
            legs.append(S.Leg(
                side=side,
                qty=str(row.get("amount")),
                price=str(row.get("price")),
                fee=str(row.get("fee_amount") or "0"),
                source="/v1/mytrades",
                trade_id=str(row.get("tid") or row.get("order_id") or "")
                or None))
        except (TypeError, ValueError):
            continue
    return legs


async def probe(symbols: Sequence[str]) -> Dict[str, Any]:
    if not has_credentials():
        return {"label": LABEL, "ok": False, "verdict": "NO_CREDENTIALS",
                "why": ("this module reads an authenticated account surface "
                        "and holds no credential of its own. Run it where the "
                        "Gemini credentials already live."),
                "gate": "BLOCKED"}

    account = [await _read(p) for p in READ_PATHS]
    candidates = [await _read(p) for p in SETTLEMENT_CANDIDATES]

    settlements: List[S.Settlement] = []
    per_symbol: List[Dict[str, Any]] = []
    for sym in symbols:
        r = await _read("/v1/mytrades", {"symbol": sym, "limit_trades": 500})
        legs = legs_from_mytrades(sym, r.get("body"))
        st = S.Settlement(sym, legs=legs)
        settlements.append(st)
        per_symbol.append({
            "symbol": sym, "mytrades_status": r.get("status"),
            "rows": (len(r["body"]) if isinstance(r.get("body"), list)
                     else None),
            "legs_parsed": len(legs),
            "unparseable": r.get("unparseable") is not None,
            "position": str(st.position()),
        })

    found = [c for c in candidates
             if c.get("reachable") and c.get("status") == 200]
    return {
        "label": LABEL, "ok": True,
        "account_reads": [{k: v for k, v in a.items() if k != "body"}
                          for a in account],
        "settlement_candidates": [{k: v for k, v in c.items() if k != "body"}
                                  for c in candidates],
        "settlement_endpoint_found": [c["path"] for c in found],
        "per_symbol": per_symbol,
        "gate": S.gate(settlements),
        "labels": [
            "READ ONLY. No order transport is imported or reachable here.",
            "a 404 on a settlement candidate is a RESULT: it says the credit "
            "does not live there, which is what nobody had established.",
            "no credential value is read, printed, logged or returned by this "
            "module; it only reports whether one was present.",
        ],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only Gemini settlement probe")
    ap.add_argument("--symbols", default="",
                    help="comma-separated GEMI-... symbols to account for")
    a = ap.parse_args(argv)
    syms = [s.strip() for s in a.symbols.split(",") if s.strip()]
    out = asyncio.run(probe(syms))
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
