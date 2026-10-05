"""READ-ONLY settlement/cash audit of BTC5m canary cohort 1. NO ORDER PATH.

Produces, per filled position, the complete lifecycle kept in SEPARATE
labels -- a settled outcome is not a confirmed cashflow:

    DECISION -> SUBMITTED -> ACK -> FILLED -> SETTLED -> CASH_CONFIRMED

SETTLED is the OUTCOME leg, from the authoritative contract object
(status/resolutionSide/resolvedAt/settlementValue). resolution_for takes N
reads because that endpoint flaps, and one `settled` reading wins over any
number of `active` ones; an expired contract that never reads settled is
UNCONFIRMED, which is the absence of evidence and never a claim it did not
settle.

CASH_CONFIRMED is the CASH leg, and ONLY the venue's own
positions/settled?withCashOuts=true cashOuts[] record can supply it. No
arithmetic over an outcome may stand in for it. REALIZED NET P&L IS REPORTED
ONLY where that record exists.

The descriptive reconstruction (reference at entry minus strike, the
trajectory to settlement, the contemporaneous DOWN price, the counterfactual
DOWN result, leader/underdog at entry) is DESCRIPTIVE. It selects nothing: it
is computed after the fact on five probe-contaminated windows and is excluded
from the preregistered strategy dataset.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import gzip
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.gemini_execution import btc5m_canary as C

LABEL = "GEMINI_BTC5M_COHORT1_AUDIT_V1"
TAKER_RATE = D("0.07")
SETTLED_PATH = "/v1/prediction-markets/positions/settled"


def _d(v: Any) -> Optional[D]:
    return C._d(v)


# ------------------------------------------------------------ the local legs
def cohort_chain() -> List[Dict[str, Any]]:
    """DECISION -> SUBMITTED -> ACK -> FILLED, from our own durable ledger."""
    rows = C.ledger_rows()
    out: List[Dict[str, Any]] = []
    for slot in [r for r in rows if r.get("kind") == "SLOT"]:
        sub = next((r for r in rows if r.get("kind") == "SUBMITTED"
                    and r.get("slot") == slot.get("slot")), {})
        body = ((sub.get("venue") or {}).get("body") or {})
        dec = slot.get("decision") or {}
        bk = dec.get("book") or {}
        out.append({
            "slot": slot.get("slot"),
            "market": slot.get("market"),
            "DECISION": {
                "utc": dec.get("utc") or slot.get("utc"),
                "elapsed_since_open_s": dec.get("elapsed_since_open_s"),
                "quote_age_s": dec.get("quote_age_s"),
                "qualifies": dec.get("qualifies"),
                "failed_checks": dec.get("failed_checks"),
                "up_ask": bk.get("ask"), "up_ask_size": bk.get("ask_size"),
                "up_bid": bk.get("bid"), "up_bid_size": bk.get("bid_size"),
                # the venue publishes one ladder; DOWN acquisition is
                # 1 - YES bid, which is what buying NO actually consumes
                "down_acquisition_contemporaneous": (
                    None if bk.get("bid") is None
                    else str(D("1") - D(str(bk["bid"])))),
            },
            "SUBMITTED": {
                "utc": sub.get("utc"),
                "requested_price": slot.get("requested_price"),
                "requested_quantity": slot.get("requested_quantity"),
                "committed_usd": slot.get("committed_usd"),
            },
            "ACK": {
                "order_id": (None if body.get("orderId") is None
                             else str(body["orderId"])),
                "client_order_id": body.get("clientOrderId"),
                "latency_ms": sub.get("latency_ms"),
                "venue_status": body.get("status"),
                "created_at": body.get("createdAt"),
            },
        })
    return out


# ------------------------------------------------------- the recorder legs
def tape_rows(pattern: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for f in sorted(glob.glob(pattern)):
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except Exception:                              # noqa: BLE001
                    continue
    return out


def reference_series(rows: List[Dict[str, Any]]) -> List[tuple]:
    """(epoch_s, value) for the published settlement reference. CAUSAL."""
    ser = []
    for r in rows:
        if r.get("kind") != "index_price":
            continue
        # the recorder's own field names, verified against a tape row:
        # parsed_event_time (epoch seconds, the venue's E) and parsed_value.
        # t_ns is the RECORDER's receive clock and is the fallback only.
        t = r.get("parsed_event_time")
        if t is None:
            t = r.get("t_ns")
            if t is None:
                continue
            t = float(t) / 1e9
        t = float(t)
        if t > 1e12:
            t = t / 1e9
        v = _d(r.get("parsed_value")
               or ((r.get("raw") or {}).get("p")))
        if v is not None:
            ser.append((t, v))
    ser.sort()
    return ser


def reference_at(ser: List[tuple], t: float,
                 stale_s: float = 10.0) -> Dict[str, Any]:
    """The last published value AT OR BEFORE t. Never interpolated forward."""
    import bisect
    if not ser:
        return {"value": None, "why": "no reference rows in the tape"}
    i = bisect.bisect_right([x[0] for x in ser], t) - 1
    if i < 0:
        return {"value": None, "why": "no reference published before t"}
    age = t - ser[i][0]
    return {"value": str(ser[i][1]), "age_s": round(age, 3),
            "stale": age > stale_s,
            "note": ("the last value published AT OR BEFORE the instant; a "
                     "later value would be future information")}


# --------------------------------------------------------- the venue legs
async def settled_outcome(symbol: str) -> Dict[str, Any]:
    from src.gemini_execution import metadata
    # RESOLUTION_FOR ALREADY FLATTENS THIS. The first draft of this function
    # went looking for r["contract"] and for observations[*]["_contract"] --
    # neither survives the call: `_contract` is POPPED off every observation
    # and the fields are published at the top level, with the strike carried
    # in `strike_check`. So the audit printed verdict SETTLED beside a null
    # status, side and settlementValue, which is the unreadable-versus-empty
    # mistake in a new place. Read what the producer actually returns.
    r = metadata.resolution_for(symbol)
    chk = r.get("strike_check") or {}
    k = chk.get("strike") if chk.get("available") else None
    return {
        "verdict": r.get("verdict"),
        "readable": r.get("readable"),
        "status": r.get("status"),
        "resolutionSide": r.get("resolutionSide"),
        "resolvedAt": r.get("resolvedAt"),
        "settlementValue": r.get("settlementValue"),
        "expiryDate": r.get("expiryDate"),
        "reads": r.get("reads"),
        "reads_settled": r.get("reads_settled"),
        "flapped": r.get("flapped"),
        "distinct_readings": r.get("distinct_readings"),
        "missing_fields": r.get("missing_fields"),
        "strike_value": k,
        "strike_source": ("contract.strike.value via strike_check"
                          if k not in (None, "") else "ABSENT"),
        "strike_consistency": chk,
        "label": ("SETTLED is the OUTCOME leg only. It is not cash and not "
                  "realized."),
    }


async def cash_leg(symbols: List[str]) -> Dict[str, Any]:
    """The venue's own cashOuts[] record. The ONLY route to CASH_CONFIRMED."""
    from src.gemini_prediction_auth import private_rest_request
    out: Dict[str, Any] = {"path": SETTLED_PATH, "reads": [],
                           "by_symbol": {s: [] for s in symbols},
                           "readable": False}
    for params in ({"withCashOuts": True, "limit": 100},
                   {"withCashOuts": "true", "limit": 100}):
        try:
            # params is KEYWORD-ONLY and goes into the SIGNED payload;
            # passing it positionally raised TypeError and the first audit
            # recorded the cash leg as unreadable for a reason that had
            # nothing to do with the venue.
            r = await private_rest_request("POST", SETTLED_PATH,
                                           params=params, timeout=25.0)
        except Exception as exc:                               # noqa: BLE001
            out["reads"].append({"params": params,
                                 "why": "raised %s" % type(exc).__name__})
            continue
        rec: Dict[str, Any] = {"params": params, "status": r.status_code}
        if r.status_code != 200:
            out["reads"].append(rec)
            continue
        try:
            body = r.json()
        except ValueError:
            rec["why"] = "body was not JSON"
            out["reads"].append(rec)
            continue
        out["readable"] = True
        rec["positions"] = len((body.get("positions") or [])
                               if isinstance(body, dict) else [])
        rec["cashOuts"] = len((body.get("cashOuts") or [])
                              if isinstance(body, dict) else [])
        out["reads"].append(rec)
        for key in ("cashOuts", "positions"):
            for row in ((body.get(key) or [])
                        if isinstance(body, dict) else []):
                sym = str(row.get("instrumentSymbol")
                          or row.get("symbol") or "")
                for s in symbols:
                    if sym == s and row not in out["by_symbol"][s]:
                        out["by_symbol"][s].append(dict(row, _key=key))
    if not out["readable"]:
        out["why"] = ("the settled/cashOuts surface was not readable; "
                      "UNREADABLE IS NOT ZERO CASHFLOW")
    return out


# ------------------------------------------------------------------- audit
async def main_async(a) -> int:
    chain = cohort_chain()
    symbols = [c["market"] for c in chain]
    rows = tape_rows(a.tapes) if a.tapes else []
    ser = reference_series(rows)

    cash = await cash_leg(symbols) if not a.no_venue else {
        "readable": False, "why": "--no-venue"}

    audit: List[Dict[str, Any]] = []
    for leg in chain:
        sym = leg["market"]
        res = ({"readable": False, "why": "--no-venue"} if a.no_venue
               else await settled_outcome(sym))
        fills = C.attribute_fills(sym, a_fills(sym, a))
        px = _d(leg["SUBMITTED"]["requested_price"])
        qty = _d(leg["SUBMITTED"]["requested_quantity"]) or D("1")
        fill_px = (_d(fills["attributed"][0].get("price"))
                   if fills.get("attributed") else None)
        fee = (_d(fills["attributed"][0].get("fee_amount"))
               if fills.get("attributed") else None)
        maker = (fills["attributed"][0].get("maker")
                 if fills.get("attributed") else None)
        principal = None if fill_px is None else fill_px * qty
        side = res.get("resolutionSide")
        payout = None
        if side in ("yes", "no"):
            payout = (D("1") * qty) if side == "yes" else D("0")
        cash_rows = (cash.get("by_symbol") or {}).get(sym) or []
        # the entry instant, for the descriptive reconstruction
        t_entry = None
        dec_utc = leg["DECISION"]["utc"]
        if dec_utc:
            from datetime import datetime
            try:
                t_entry = datetime.fromisoformat(
                    str(dec_utc).replace("Z", "+00:00")).timestamp()
            except Exception:                                  # noqa: BLE001
                t_entry = None
        k = _d(res.get("strike_value"))
        ref_entry = (reference_at(ser, t_entry) if t_entry and ser
                     else {"value": None, "why": "no tape / no entry time"})
        # A STALE REFERENCE IS NOT A REFERENCE. The only tapes preserved so
        # far end well before these entries, so the nearest prior published
        # value can be an hour old; differencing it against the strike would
        # manufacture a D that was never observed at entry.
        d_entry, d_why = None, None
        if k is None:
            d_why = "no authoritative strike.value"
        elif _d(ref_entry.get("value")) is None:
            d_why = ref_entry.get("why") or "no reference published"
        elif ref_entry.get("stale"):
            d_why = ("the nearest published reference is %.0fs old; a stale "
                     "value is NOT the reference at entry"
                     % ref_entry.get("age_s", 0))
        else:
            d_entry = str(_d(ref_entry["value"]) - k)
        up_ask = _d(leg["DECISION"]["up_ask"])
        down_px = _d(leg["DECISION"]["down_acquisition_contemporaneous"])
        # leader/underdog at entry, from the executable ladder ONLY
        leader = None
        if up_ask is not None and down_px is not None:
            leader = ("UP" if up_ask > down_px
                      else ("DOWN" if down_px > up_ask else "TIED"))
        # counterfactual DOWN, with the SAME published fee model
        cf = None
        if down_px is not None and side in ("yes", "no"):
            dn_fee = (TAKER_RATE * down_px * (D("1") - down_px) * qty
                      ).quantize(D("0.01"))
            dn_payout = (D("1") * qty) if side == "no" else D("0")
            cf = {"down_entry_price": str(down_px),
                  "modelled_fee_usd": str(dn_fee),
                  "payout_usd": str(dn_payout),
                  "net_usd": str(dn_payout - down_px * qty - dn_fee),
                  "label": ("COUNTERFACTUAL. No DOWN order was sent, so this "
                            "is modelled, never realized, and its fee is "
                            "the published schedule rather than a charged "
                            "one.")}
        audit.append({
            "slot": leg["slot"], "market": sym,
            "DECISION": leg["DECISION"],
            "SUBMITTED": leg["SUBMITTED"],
            "ACK": leg["ACK"],
            "FILLED": {
                "attributed": fills.get("ok"),
                "attribution_route": (
                    fills["attributed"][0].get("_attribution_route")
                    if fills.get("attributed") else None),
                "price": None if fill_px is None else str(fill_px),
                "quantity": str(qty),
                "role": maker,
                "venue_fee_usd": None if fee is None else str(fee),
                "acquisition_principal_usd": (
                    None if principal is None else str(principal)),
                "foreign": fills.get("foreign"),
            },
            "SETTLED": dict(res, payout_usd=(None if payout is None
                                             else str(payout))),
            "CASH_CONFIRMED": cash_confirmed(cash_rows, fee),
            "DESCRIPTIVE": {
                "reference_at_entry": ref_entry,
                "strike_value": res.get("strike_value"),
                "strike_source": res.get("strike_source"),
                "D_entry_reference_minus_strike": d_entry,
                "D_entry_unavailable_why": d_why,
                "D_label": ("an observed difference, published reference minus"
                            " strike. NOT Z: the LRRR statistic's mechanics "
                            "are not established."),
                "up_entry_price": None if up_ask is None else str(up_ask),
                "down_price_contemporaneous": (
                    None if down_px is None else str(down_px)),
                "up_was_at_entry": leader,
                "counterfactual_down": cf,
                "settlement_side": side,
                "reference_trajectory": trajectory(ser, t_entry, res),
                "label": ("DESCRIPTIVE ONLY, on five probe-contaminated "
                          "windows. It selects no strategy and no threshold."),
            },
        })
    rep = {
        "label": LABEL,
        "accounting": ("FILLED -> SETTLED -> CASH_CONFIRMED -> REALIZED. "
                       "SETTLED and CASH_CONFIRMED are kept separate and a "
                       "settled outcome is never reported as realized."),
        "read_only": ("this audit imports no order path; it reads the "
                      "contract object, positions/settled and our own "
                      "durable ledger"),
        "cohort_state": C.cohort_state(),
        "cash_leg_reads": cash.get("reads"),
        "cash_leg_readable": cash.get("readable"),
        "rows": audit,
        "excluded_from_strategy": symbols,
    }
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, sort_keys=True, default=str)
    print(json.dumps({"label": LABEL, "rows": len(audit),
                      "cash_leg_readable": cash.get("readable"),
                      "settled": [r["SETTLED"].get("resolutionSide")
                                  for r in audit],
                      "cash_confirmed": [r["CASH_CONFIRMED"]["confirmed"]
                                         for r in audit]},
                     sort_keys=True, default=str))
    return 0


def cash_confirmed(cash_rows: List[Dict[str, Any]],
                   fee: Optional[D]) -> Dict[str, Any]:
    """REALIZED, from the venue's own settled-position cashflow fields.

    WHERE THE CASH ACTUALLY IS. cashOuts[] came back EMPTY on all five of
    cohort 1 while the settled POSITION row carried costBasis, payout,
    netProfit, realizedPnl and settledAt. That is consistent with the SELL
    research: cashOuts is the EARLY-SELL primitive, and a position held to
    settlement reports its cashflow on the position itself, with
    realizedPnl 0 because nothing was cashed out. So an empty cashOuts is
    not an absent cashflow -- but neither is it the cashflow, and only a
    row carrying the venue's own payout/netProfit confirms one.

    OUR ARITHMETIC IS A CROSS-CHECK, NEVER THE SOURCE: netProfit is the
    venue's figure and excludes the trading fee, so payout - costBasis - fee
    is reported beside it and a disagreement is flagged rather than
    averaged.
    """
    pos = [r for r in cash_rows if r.get("_key") == "positions"]
    cos = [r for r in cash_rows if r.get("_key") == "cashOuts"]
    if not pos:
        return {"confirmed": False, "rows": cash_rows,
                "realized_net_usd": None,
                "why": ("no settled-position or cashOut row for this symbol; "
                        "realized net P&L is NOT computed from an outcome")}
    r0 = pos[0]
    payout, cost = _d(r0.get("payout")), _d(r0.get("costBasis"))
    venue_net = _d(r0.get("netProfit"))
    ours = (None if (payout is None or cost is None or fee is None)
            else payout - cost - fee)
    return {
        "confirmed": True,
        "rows": cash_rows,
        "settled_at": r0.get("settledAt"),
        "venue_payout_usd": r0.get("payout"),
        "venue_cost_basis_usd": r0.get("costBasis"),
        "venue_net_profit_usd": r0.get("netProfit"),
        "venue_realized_pnl_usd": r0.get("realizedPnl"),
        "cash_out_rows": len(cos),
        "venue_fee_usd": None if fee is None else str(fee),
        "realized_net_usd": None if ours is None else str(ours),
        "realized_net_basis": ("venue payout minus venue costBasis minus the "
                               "venue's own charged fee"),
        "agrees_with_venue_net_profit_before_fees": (
            None if (venue_net is None or payout is None or cost is None)
            else bool(venue_net == payout - cost)),
        "label": ("REALIZED. The venue's own settled-position cashflow "
                  "record, not a mark and not an outcome."),
    }


def trajectory(ser, t_entry, res) -> Dict[str, Any]:
    """The reference from entry through settlement. Descriptive."""
    if not ser or t_entry is None:
        return {"points": [], "why": "no tape / no entry time"}
    pts = []
    for off in (0, 15, 30, 60, 90, 120, 180, 240, 285):
        got = reference_at(ser, t_entry + off)
        if got.get("value") is not None:
            pts.append({"offset_s_from_entry": off, "value": got["value"],
                        "age_s": got.get("age_s"), "stale": got.get("stale")})
    return {"points": pts,
            "settlementValue": res.get("settlementValue"),
            "label": ("each point is the last value published AT OR BEFORE "
                      "that instant; nothing is read forward")}


def a_fills(symbol: str, a) -> List[Dict[str, Any]]:
    """The venue fill rows already preserved in the cohort run artifact."""
    rows: List[Dict[str, Any]] = []
    for f in sorted(glob.glob(os.path.join(C.LEDGER_DIR,
                                           "canary_run_*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            continue
        for w in (d.get("windows") or []):
            if w.get("symbol") != symbol:
                continue
            fl = w.get("fills") or {}
            for r in (fl.get("rows") or []) + (fl.get("foreign") or []):
                if r not in rows:
                    rows.append(r)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes",
                    default="analysis/gemini/btc5m_rec/btc5m_rec_*.jsonl.gz")
    ap.add_argument("--no-venue", action="store_true",
                    help="skip every account/contract read; local legs only")
    ap.add_argument("--out",
                    default="analysis/gemini/btc5m_canary/cohort1_audit.json")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
