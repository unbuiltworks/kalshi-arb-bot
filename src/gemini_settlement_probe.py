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
                or None,
                order_id=str(row.get("order_id") or "") or None))
        except (TypeError, ValueError):
            continue
    return legs


def expiry_of(symbol: str) -> Dict[str, Any]:
    """Contract -> expiry, parsed from the symbol the venue itself names.

    GEMI-BTC05M2609172155-UP expires 2026-09-17T21:55Z. Derived rather than
    looked up because /v1/symbols/details carries no expiry for these
    instruments -- it reports product_type "Unsupported Instrument".
    """
    import datetime as _dt
    body = symbol.split("-")[1] if symbol.count("-") >= 2 else ""
    stamp = body[-10:]
    if len(stamp) != 10 or not stamp.isdigit():
        return {"symbol": symbol, "expiry_utc": None,
                "why": "the symbol does not carry a parseable expiry stamp"}
    try:
        dt = _dt.datetime.strptime(stamp, "%y%m%d%H%M").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return {"symbol": symbol, "expiry_utc": None,
                "why": "the symbol's expiry stamp is not a valid time"}
    return {"symbol": symbol, "expiry_utc": dt.isoformat(),
            "expiry_epoch": int(dt.timestamp()),
            "source": "parsed from the venue's own symbol"}


def proof_chain(sym: str, st: S.Settlement, candidates: Sequence[dict]
                ) -> Dict[str, Any]:
    """The chain a live gate requires, link by link, each PRESENT or MISSING.

        contract -> expiry -> authoritative outcome -> position
                 -> settlement cashflow -> fees -> ledger reconciliation

    A chain is only as good as its weakest link, so each one reports its own
    status and the chain reports the FIRST break rather than a score.
    """
    sat = st.satisfied()
    found = [c["path"] for c in candidates
             if c.get("reachable") and c.get("status") == 200]
    exp = expiry_of(sym)
    links = [
        {"link": "contract", "present": True, "value": sym,
         "source": "the symbol under account"},
        {"link": "expiry", "present": exp["expiry_utc"] is not None,
         "value": exp.get("expiry_utc"), "source": exp.get("source"),
         "why": exp.get("why")},
        {"link": "authoritative_outcome",
         "present": bool(st.settlement_value and st.settlement_source),
         "value": st.settlement_value, "source": st.settlement_source,
         "why": ("no public source exists and no authenticated settlement "
                 "endpoint answered" if not found else
                 "candidate endpoints answering: %s" % ", ".join(found))},
        {"link": "position", "present": sat[S.POSITION_AT_EXPIRY],
         "value": str(st.position()), "source": "/v1/mytrades",
         "why": ("no authoritative fill for this symbol"
                 if not sat[S.POSITION_AT_EXPIRY] else None)},
        {"link": "settlement_cashflow", "present": sat[S.CASH_CONFIRMED],
         "value": st.cash_delta_observed, "source": st.cash_delta_source,
         "why": ("no attributable cash movement; a balance change without "
                 "source identity is not attribution"
                 if not sat[S.CASH_CONFIRMED] else None)},
        {"link": "fees", "present": sat[S.FEES_ACCOUNTED],
         "value": str(st.fees()), "source": "/v1/mytrades fee fields"},
        {"link": "ledger_reconciliation",
         "present": bool(st.reconstructed() is not None
                         and sat[S.CASH_CONFIRMED]),
         "value": (str(st.reconstructed())
                   if st.reconstructed() is not None else None),
         "source": "venue cash vs arithmetic over our own legs",
         "why": ("nothing to reconcile until both a settlement value and a "
                 "cash movement exist")},
    ]
    broken = [l["link"] for l in links if not l["present"]]
    return {
        "symbol": sym, "links": links,
        "complete": not broken,
        "first_break": (broken[0] if broken else None),
        "missing_links": broken,
        "labels": ("a chain is as good as its weakest link, so this reports "
                   "the FIRST break rather than a count of what works."),
    }


def shape_of(body: Any, limit: int = 40) -> Dict[str, Any]:
    """The STRUCTURE of a response, never its values.

    Endpoint reachability is not the same as an endpoint that CARRIES an
    authoritative outcome, and the difference decides whether a lane can
    settle. This reports field names and row counts so that question can be
    answered without copying account data into an artifact or a log.
    """
    if body is None:
        return {"kind": "none"}
    if isinstance(body, list):
        keys: set = set()
        for row in body[:limit]:
            if isinstance(row, dict):
                keys |= set(row.keys())
        return {"kind": "list", "rows": len(body),
                "row_keys": sorted(keys)}
    if isinstance(body, dict):
        inner = {}
        for k, v in list(body.items())[:limit]:
            if isinstance(v, list):
                ks: set = set()
                for row in v[:limit]:
                    if isinstance(row, dict):
                        ks |= set(row.keys())
                inner[k] = {"kind": "list", "rows": len(v),
                            "row_keys": sorted(ks)}
        return {"kind": "dict", "keys": sorted(body.keys()), "lists": inner}
    return {"kind": type(body).__name__}


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

        # WHICH SIDE EACH FILL IS ON, FROM THE VENUE. /v1/mytrades does NOT
        # say: its rows carry symbol, order_id, client_order_id, price,
        # amount, fee and flags, and no outcome at all (row keys captured
        # 2026-09-18). Order HISTORY does carry the venue's own `outcome`, so
        # the side is joined in by order_id rather than read off our own
        # clientOrderId -- our label is our label, and the venue's is evidence.
        outcome_join: Dict[str, Any] = {"attempted": False}
        try:
            from src.gemini_execution import metadata as MD
            hist = await MD.order_history(symbol=sym,
                                          statuses=("filled", "cancelled"),
                                          limit=100, include_rows=True)
            by_order: Dict[str, str] = {}
            for p in (hist.get("per_status") or []):
                for o in (p.get("orders") or []):
                    oid = o.get("orderId")
                    out = o.get("outcome")
                    if oid is not None and out:
                        by_order[str(oid)] = str(out).lower()
            hit = 0
            for l in legs:
                got = by_order.get(str(l.order_id))
                if got:
                    l.outcome = got
                    hit += 1
            outcome_join = {"attempted": True,
                            "readable": hist.get("readable") is not False,
                            "orders_with_outcome": len(by_order),
                            "legs": len(legs), "legs_labelled": hit,
                            "source": "/v1/orders/history joined on orderId",
                            "why": ("mytrades carries no outcome field; the "
                                    "side comes from the venue's order "
                                    "history, not from our clientOrderId")}
        except Exception as exc:                                # noqa: BLE001
            outcome_join = {"attempted": True, "readable": False,
                            "why": "%s: %s" % (type(exc).__name__, exc)}

        st = S.Settlement(sym, legs=legs)

        # THE OUTCOME LEG, WHICH NOTHING USED TO READ. `resolution_for` is a
        # PUBLIC read of the contract object and carries status,
        # resolutionSide, resolvedAt and settlementValue -- it has carried
        # them all along. Because the probe never called it, settlement_value
        # and settlement_source were never assigned by anything and the
        # authoritative_outcome link was structurally unsatisfiable: a
        # resolved contract stayed labelled unsettled forever. CONTINUOUS-4
        # resolved at 17:30:07.369Z and still read "absent" at 17:55Z.
        resolution: Dict[str, Any] = {}
        applied: Dict[str, Any] = {"applied": False, "why": "not attempted"}
        try:
            from src.gemini_execution import metadata as MD
            resolution = MD.resolution_for(sym)
            applied = S.apply_resolution(st, resolution)
        except Exception as exc:                               # noqa: BLE001
            applied = {"applied": False,
                       "why": "%s: %s" % (type(exc).__name__, exc)}

        settlements.append(st)
        per_symbol.append({
            "symbol": sym, "mytrades_status": r.get("status"),
            "rows": (len(r["body"]) if isinstance(r.get("body"), list)
                     else None),
            "legs_parsed": len(legs),
            "unparseable": r.get("unparseable") is not None,
            "position": str(st.position()),
            # SHAPE ONLY of the trade rows, so the outcome field can be read
            # from what the venue actually sends rather than guessed at.
            "mytrades_row_keys": sorted(
                {k for row in (r.get("body") or [])
                 if isinstance(row, dict) for k in row.keys()}
            ) if isinstance(r.get("body"), list) else None,
            "resolution": {k: resolution.get(k) for k in
                           ("verdict", "resolutionSide", "resolvedAt",
                            "settlementValue", "marketState", "status",
                            "expired", "is_terminal", "flapped",
                            "strike_check", "source")},
            "resolution_applied": applied,
            "outcome_join": outcome_join,
            "leg_outcomes": [l.outcome for l in legs],
            "payout": (str(st.payout()) if st.payout() is not None else None),
            "reconstructed": (str(st.reconstructed())
                              if st.reconstructed() is not None else None),
            "proof_chain": proof_chain(sym, st, candidates),
        })

    found = [c for c in candidates
             if c.get("reachable") and c.get("status") == 200]
    return {
        "label": LABEL, "ok": True,
        "account_reads": [
            {**{k: v for k, v in a.items() if k != "body"},
             "shape": shape_of(a.get("body"))} for a in account],
        "settlement_candidates": [
            {**{k: v for k, v in c.items() if k != "body"},
             # SHAPE ONLY -- field names and row counts, never values. An
             # endpoint that answers 200 is not yet an endpoint that carries
             # an authoritative outcome, and this is what tells them apart.
             "shape": shape_of(c.get("body"))}
            for c in candidates],
        "settlement_endpoint_found": [c["path"] for c in found],
        "settlement_endpoint_exists": bool(found),
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


OUT_DIR = "analysis/gemini"


def persist(out: Mapping[str, Any]) -> Optional[str]:
    """Write the evidence artifact. Failure to write is reported, never fatal:
    the finding is the answer, and losing the file must not lose the run."""
    import datetime
    import pathlib
    try:
        d = pathlib.Path(OUT_DIR)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ")
        f = d / ("settlement_gate_%s.json" % stamp)
        f.write_text(json.dumps(out, indent=2, default=str) + "\n",
                     encoding="utf-8")
        return str(f)
    except OSError:
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only Gemini settlement probe")
    ap.add_argument("--symbols", default="",
                    help="comma-separated GEMI-... symbols to account for")
    ap.add_argument("--no-persist", action="store_true")
    a = ap.parse_args(argv)
    syms = [s.strip() for s in a.symbols.split(",") if s.strip()]
    out = asyncio.run(probe(syms))
    if not a.no_persist and out.get("ok"):
        out["artifact"] = persist(out)
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
