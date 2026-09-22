"""Did any maker fee ever leave the account? A CASH question, not a field read.

    python -m src.gemini_execution.maker_fee_cash_probe \
        --from 2026-09-20 --to 2026-09-23 --out <path>.json

THE CONFLICT THIS EXISTS TO SETTLE. The published schedule
(https://www.gemini.com/fees/predictions, effective 2026-03-18) says

    Maker Fee = 0.0175 x C x P x (1 - P), rounded UP to the next cent

which on the eighteen EP016 fills is $0.18 -- 2.6% of their $6.86 notional, and
more than twice the same day's $0.08 rebate. Every one of those fills reported
the venue's own `n` as 0.0000, and the venue's WebSocket reference defines `n`
as "Fee amount (only present in 'FILLED' events)" while also saying zero-valued
fields MAY be omitted, so a PRESENT zero is an assertion rather than a silence.

A field can only tell us what the venue said at fill time. Whether money later
moved is a different question with a different source, and this probe asks it
of the account's own records:

    POST /v1/mytrades                      our executions, with fee_amount
    POST /v1/transfers                     every cash movement, both directions
    POST /v1/prediction-markets/positions/settled?withCashOuts=true
                                           settlement cashflows

NOTHING IS INFERRED. The probe reports what each endpoint returned, the sum of
anything fee-shaped, and whether a charge near $0.18 appears anywhere. It does
not attribute a debit to a fill -- `/v1/transfers` carries no order id, which
this programme has already established -- and it does not change the fee line.

READ-ONLY: three enumerated POST reads, no order, cancel or transfer-creating
route.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

LABEL = "GEMINI_MAKER_FEE_CASH_PROBE_V1"

MYTRADES = "/v1/mytrades"
TRANSFERS = "/v1/transfers"
SETTLED = "/v1/prediction-markets/positions/settled?withCashOuts=true"

#: what the published schedule implies for the eighteen EP016 fills
IMPLIED_USD = D("0.18")

#: the four EP016 contracts, so their own rows can be pulled out by name
#: rather than by a time filter that could drift with a timezone
EP016_SYMBOLS = (
    "GEMI-BTC15M2609220230-UP", "GEMI-BTC15M2609220245-UP",
    "GEMI-BTC15M2609220300-UP", "GEMI-BTC15M2609220315-UP",
)
FEE_WORDS = ("fee", "commission", "charge")


def _d(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


MAKER_RATE = D("0.0175")
TAKER_RATE = D("0.07")


def _ceil_cent(v: D) -> D:
    from decimal import ROUND_CEILING
    return v.quantize(D("0.01"), rounding=ROUND_CEILING)


def fee_shape(row: Any) -> Optional[Dict[str, Any]]:
    """Which published formula, if either, produced this row's fee?

    The schedule gives Fee = rate x C x P x (1 - P) rounded UP to the next
    cent, with rate 0.0175 for a maker and 0.07 for a taker. Both are computed
    and the row is labelled by which one it matches -- because "fees exist" and
    "MAKER fees exist" are different claims, and only the second one bears on a
    maker strategy.
    """
    c = _d(row.get("amount"))
    p = _d(row.get("price"))
    f = _d(row.get("fee_amount"))
    if c is None or p is None or f is None or not (0 < p < 1):
        return None
    base = c * p * (D(1) - p)
    mk, tk = _ceil_cent(MAKER_RATE * base), _ceil_cent(TAKER_RATE * base)
    if f == 0:
        lab = "ZERO"
    elif f == mk and f != tk:
        lab = "MAKER_FORMULA"
    elif f == tk and f != mk:
        lab = "TAKER_FORMULA"
    elif f == mk and f == tk:
        lab = "EITHER (the two agree here)"
    else:
        lab = "NEITHER"
    return {"label": lab, "fee": str(f), "maker_expected": str(mk),
            "taker_expected": str(tk), "price": str(p), "amount": str(c),
            "symbol": row.get("symbol"), "timestampms": row.get("timestampms")}


def fee_like(row: Any) -> List[Dict[str, Any]]:
    """Any field whose NAME says fee and whose value is non-zero."""
    out = []
    if not isinstance(row, dict):
        return out
    for k, v in row.items():
        if not any(w in str(k).lower() for w in FEE_WORDS):
            continue
        d = _d(v)
        if d is not None and d != 0:
            out.append({"field": k, "value": str(d)})
    return out


async def run(date_from: str, date_to: str) -> Dict[str, Any]:
    from src.gemini_prediction_auth import has_credentials, private_rest_request

    rep: Dict[str, Any] = {
        "label": LABEL, "utc": datetime.now(timezone.utc).isoformat(),
        "window": {"from": date_from, "to": date_to},
        "implied_by_schedule_usd": str(IMPLIED_USD),
        "question": ("the venue reported n=0.0000 on every EP016 fill; did any "
                     "maker fee leave the account later?"),
    }
    if not has_credentials():
        rep.update({"readable": False, "verdict": "NO_CREDENTIALS"})
        return rep

    async def _post(path: str, params: Optional[Dict[str, Any]] = None):
        try:
            r = await private_rest_request("POST", path, timeout=20.0,
                                           params=params)
            body = None
            try:
                body = r.json()
            except Exception:                                  # noqa: BLE001
                body = None
            return {"status": r.status_code, "readable": r.status_code == 200,
                    "body": body}
        except Exception as exc:                               # noqa: BLE001
            return {"status": None, "readable": False, "body": None,
                    "error": "%s: %s" % (type(exc).__name__, exc)}

    mt = await _post(MYTRADES, {"limit_trades": 500})
    tr = await _post(TRANSFERS, {"limit_transfers": 200})
    st = await _post(SETTLED)

    # ---- our own executions: is there a fee on any of them?
    rows = mt.get("body") if isinstance(mt.get("body"), list) else []
    fees, nonzero = D("0"), []
    for t in rows:
        f = _d(t.get("fee_amount"))
        if f is not None:
            fees += f
            if f != 0:
                nonzero.append({"tid": t.get("tid"), "symbol": t.get("symbol"),
                                "timestampms": t.get("timestampms"),
                                "fee_amount": str(f),
                                "fee_currency": t.get("fee_currency"),
                                "price": t.get("price"),
                                "amount": t.get("amount")})
    #: THE ROWS THAT MATTER, pulled out by symbol. Every EP016 fill reported
    #: the WebSocket `n` as 0.0000; whether the account's own execution record
    #: agrees is the whole question, and it is answered per row rather than in
    #: aggregate.
    ep = [t for t in rows if str(t.get("symbol")) in EP016_SYMBOLS]
    ep_fee = D("0")
    ep_rows = []
    for t in ep:
        f = _d(t.get("fee_amount"))
        if f is not None:
            ep_fee += f
        ep_rows.append({"tid": t.get("tid"), "symbol": t.get("symbol"),
                        "timestampms": t.get("timestampms"),
                        "price": t.get("price"), "amount": t.get("amount"),
                        "fee_amount": t.get("fee_amount"),
                        "fee_currency": t.get("fee_currency"),
                        "type": t.get("type"),
                        "aggressor": t.get("aggressor"),
                        "maker_side": t.get("maker_side")})
    rep["ep016"] = {
        "symbols": list(EP016_SYMBOLS),
        "rows_found": len(ep),
        "fee_amount_total": str(ep_fee),
        "implied_by_schedule_usd": str(IMPLIED_USD),
        "delta_vs_schedule": str(ep_fee - IMPLIED_USD),
        "rows": ep_rows,
        "note": ("18 fills are expected. Fewer means /v1/mytrades did not "
                 "return them all in this page, which is a coverage fact and "
                 "not a statement about fees."),
    }

    shapes = [fee_shape(t) for t in rows]
    shapes = [x for x in shapes if x]
    tally: Dict[str, int] = {}
    for x in shapes:
        tally[x["label"]] = tally.get(x["label"], 0) + 1
    maker_rows = [x for x in shapes if x["label"] == "MAKER_FORMULA"]
    rep["fee_formula"] = {
        "tally": tally,
        "maker_formula_rows": maker_rows[:20],
        "n_maker_formula": len(maker_rows),
        "neither": [x for x in shapes if x["label"] == "NEITHER"][:10],
        "note": ("published: Fee = rate x C x P x (1-P), rounded UP to the "
                 "next cent; maker rate 0.0175, taker rate 0.07. 'Fees exist' "
                 "and 'MAKER fees exist' are different claims and only the "
                 "second bears on a maker strategy."),
    }

    rep["mytrades"] = {
        "status": mt.get("status"), "readable": mt.get("readable"),
        "n_rows": len(rows), "fee_amount_total": str(fees),
        "rows_with_a_nonzero_fee": len(nonzero), "examples": nonzero[:10],
        "note": ("fee_amount is the account's own record of what it paid. A "
                 "total of 0 across every row is a cash statement, not a "
                 "field default -- but only for the rows this endpoint "
                 "returns, which is why n_rows is reported beside it."),
    }

    # ---- every cash movement, both directions
    trows = tr.get("body") if isinstance(tr.get("body"), list) else []
    debits, fee_named = [], []
    for r in trows:
        amt = _d(r.get("amount"))
        kind = str(r.get("type") or "")
        purpose = str(r.get("purpose") or "")
        hit = fee_like(r)
        if hit:
            fee_named.append({"type": kind, "purpose": purpose, "hits": hit,
                              "eid": r.get("eid"),
                              "timestampms": r.get("timestampms")})
        if amt is not None and (amt < 0 or "debit" in kind.lower()
                                or any(w in purpose.lower() for w in FEE_WORDS)):
            debits.append({"type": kind, "purpose": purpose, "amount": str(amt),
                           "currency": r.get("currency"), "eid": r.get("eid"),
                           "status": r.get("status"),
                           "timestampms": r.get("timestampms")})
    rep["transfers"] = {
        "status": tr.get("status"), "readable": tr.get("readable"),
        "n_rows": len(trows), "debit_like_rows": debits[:25],
        "n_debit_like": len(debits), "fee_named_fields": fee_named[:25],
        "note": ("/v1/transfers carries no order id, so a debit here can never "
                 "be attributed to a fill. Its value is existential: a maker "
                 "fee that was charged has to appear as SOMETHING."),
    }

    rep["settled_cashouts"] = {
        "status": st.get("status"), "readable": st.get("readable"),
        "n_rows": (len(st["body"]) if isinstance(st.get("body"), list) else None),
        "fee_named_fields": [fee_like(r) for r in (st.get("body") or [])
                             if isinstance(r, dict) and fee_like(r)][:25],
    }

    any_fee = bool(nonzero) or bool(fee_named) or bool(debits)
    rep["verdict"] = ("FEE_EVIDENCE_FOUND" if any_fee else
                      "NO_FEE_CASH_FOUND" if mt.get("readable") else
                      "UNREADABLE")
    rep["labels"] = ("UNRESOLVED either way until a cash movement is matched "
                     "to a window. The fee line is not changed by this probe.")
    return rep


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default="2026-09-20")
    ap.add_argument("--to", dest="date_to", default="2026-09-23")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = asyncio.run(run(a.date_from, a.date_to))
    text = json.dumps(rep, indent=2, sort_keys=True, default=str)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text + "\n")
    print(text)
    print("VERDICT %s" % rep.get("verdict"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
