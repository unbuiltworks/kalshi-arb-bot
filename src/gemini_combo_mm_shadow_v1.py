"""Next-generation Gemini sports combo market-maker SHADOW supervisor.

This service implements the full production decision stack without order entry:
- scans short-horizon Gemini sports for cheap exact complements;
- manufactures 2-6 leg candidate covers with a bounded beam search;
- computes q1/q2/q3 true-depth hedge costs and quantity-rounded fees;
- requires every possible partial fill size to retain >=3c before q>1 is eligible;
- ranks by fillability and expected dollars/hour rather than raw theoretical edge;
- reserves capital for up to five simultaneous resting quotes;
- subscribes to Gemini public bookTicker updates for selected hedge symbols and
  recalculates dynamic safe maker prices on market events;
- persists candidate/reprice observations for later fill-rate calibration;
- reports the fail-closed Gemini/Kalshi/Polymarket rule-certification gate.

NO authenticated order endpoint is imported or called. combo_creation=false,
order_entry=false.  Live promotion belongs in a separate reviewed executor.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
import json
import logging
import os
import re
import time
from typing import Any, Iterable, Mapping, Sequence
import uuid

import httpx
import websockets

from src.gemini_combo_cross_venue_router import current_mlb_external_gate
from src.gemini_combo_mm_core import (
    ComboLeg,
    ComboPlan,
    HedgeDepth,
    MAKER,
    TAKER,
    TARGET,
    dec,
    fee_total,
    make_plan,
    physical_event_key,
    reserve_candidates,
)
from src.gemini_combo_mm_telemetry import emit as telemetry_emit
from src.gemini_combo_mm_telemetry import ensure_schema, historical_fill_rate, signature
from src.gemini_sports_inventory import fetch_active_sports_events
from src.gemini_prediction_auth import private_rest_request
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker

D = Decimal
ONE = D("1")
BASE = "https://api.gemini.com"
logger = logging.getLogger("kalshi_arb.gemini_combo_mm_shadow_v1")

HORIZON_HOURS = int(os.getenv("GEMINI_COMBO_MM_HORIZON_HOURS", "36"))
MAX_DISCOVERY_LEGS = int(os.getenv("GEMINI_COMBO_MM_MAX_LEGS", "60"))
BEAM_WIDTH = int(os.getenv("GEMINI_COMBO_MM_BEAM_WIDTH", "160"))
MAX_RESTING = max(1, min(5, int(os.getenv("GEMINI_COMBO_MM_MAX_RESTING", "5"))))
MAX_QTY = max(1, min(3, int(os.getenv("GEMINI_COMBO_MM_MAX_QTY", "3"))))
WS_SECONDS = max(10, min(180, int(os.getenv("GEMINI_COMBO_MM_WS_SECONDS", "60"))))
CYCLE_SECONDS = max(60, min(900, int(os.getenv("GEMINI_COMBO_MM_CYCLE_SECONDS", "300"))))
SHADOW_CAPITAL = D(os.getenv("GEMINI_COMBO_MM_SHADOW_CAPITAL", "50"))
MAX_COMPLEMENT_ASK = D(os.getenv("GEMINI_COMBO_MM_MAX_COMPLEMENT_ASK", "0.08"))


def _parse_dt(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _numeric_contract_id(contract: Mapping[str, Any]) -> str | None:
    raw = str(contract.get("id") or "")
    if raw.isdigit():
        return raw
    m = re.fullmatch(r"\d+-(\d+)", raw)
    return m.group(1) if m else None


def _book_levels(body: Any, outcome: str) -> list[tuple[D, D]]:
    """Executable ask levels in requested outcome space.

    YES asks are direct asks. Buying NO consumes YES bids at price 1-bid.
    """
    if not isinstance(body, Mapping):
        return []
    source = body.get("asks") if outcome == "YES" else body.get("bids")
    out: list[tuple[D, D]] = []
    for row in source or []:
        if not isinstance(row, Mapping):
            continue
        p = dec(row.get("price"))
        q = dec(row.get("amount") if row.get("amount") is not None else row.get("quantity"))
        if p is None or q is None or q <= 0 or not D("0") < p < ONE:
            continue
        ask = p if outcome == "YES" else ONE - p
        if D("0") < ask < ONE:
            out.append((ask, q))
    out.sort(key=lambda x: x[0])
    return out


def _executable(levels: Sequence[tuple[D, D]], qty: int) -> HedgeDepth | None:
    remaining = D(qty)
    raw = D("0")
    fees = D("0")
    worst = D("0")
    total_units = D("0")
    for price, available in levels:
        total_units += available
        take = min(remaining, available)
        if take <= 0:
            continue
        raw += price * take
        fees += fee_total(price, take, TAKER)
        worst = max(worst, price)
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0:
        return None
    return HedgeDepth(qty=qty, raw_total=raw, fee_total=fees, all_in_total=raw + fees, worst_price=worst, units_available=total_units)


def _event_meta(event: Mapping[str, Any]) -> tuple[str, str]:
    ticker = str(event.get("ticker") or "").upper().removeprefix("GEMI-")
    sport = ticker.split("-", 1)[0].lower() if ticker else "sports"
    title = str(event.get("title") or "").lower()
    family = "unknown"
    if "total" in title: family = "total"
    elif "spread" in title or "point spread" in title: family = "spread"
    elif "winner" in title or "moneyline" in title: family = "winner"
    elif "top " in title or "finish" in title: family = "placement"
    elif "touchdown" in title or "yards" in title or "hits" in title or "runs" in title: family = "prop"
    return sport, family


def _leg_to_dict(leg: ComboLeg) -> dict[str, Any]:
    return {
        "event_ticker": leg.event_ticker,
        "physical_event": leg.physical_event,
        "title": leg.title,
        "symbol": leg.symbol,
        "contract_id": leg.contract_id,
        "label": leg.label,
        "required_outcome": leg.required_outcome,
        "complement_outcome": leg.complement_outcome,
        "selected_mid": str(leg.selected_mid),
        "expiry_ts": leg.expiry_ts,
        "terms_url": leg.terms_url,
        "hedge_depth": {
            str(q): {
                "raw_total": str(row.raw_total), "fee_total": str(row.fee_total),
                "all_in_total": str(row.all_in_total), "worst_price": str(row.worst_price),
                "units_available": str(row.units_available),
            }
            for q, row in leg.hedge_depth.items()
        },
    }


def _plan_to_dict(plan: ComboPlan) -> dict[str, Any]:
    return {
        "combo_key": plan.combo_key,
        "leg_count": plan.leg_count,
        "max_qty": plan.max_qty,
        "fair_proxy": str(plan.fair_proxy),
        "exact_qty_safe_price": str(plan.exact_qty_safe_price) if plan.exact_qty_safe_price is not None else None,
        "partial_fill_safe_price": str(plan.partial_fill_safe_price) if plan.partial_fill_safe_price is not None else None,
        "safe_price_by_fill_qty": {str(k): str(v) if v is not None else None for k, v in plan.safe_price_by_fill_qty.items()},
        "hedge_total_exact_qty": str(plan.hedge_total_exact_qty),
        "locked_per_contract": str(plan.locked_per_contract) if plan.locked_per_contract is not None else None,
        "fair_gap": str(plan.fair_gap) if plan.fair_gap is not None else None,
        "fillability_score": plan.fillability_score,
        "fill_probability_1h": plan.fill_probability_1h,
        "expected_dollars_per_hour": str(plan.expected_dollars_per_hour),
        "capital_reserve": str(plan.capital_reserve),
        "legs": [_leg_to_dict(x) for x in plan.legs],
    }


async def _available_usd() -> tuple[D, str]:
    """Read-only account balance if credentials exist; otherwise use shadow capital."""
    try:
        r = await private_rest_request("POST", "/v1/balances", timeout=5.0)
        if r.status_code == 200:
            rows = r.json()
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, Mapping) and str(row.get("currency") or "").upper() == "USD":
                        amount = dec(row.get("available"))
                        if amount is not None and amount >= 0:
                            return amount, "GEMINI_READ_ONLY_BALANCE"
    except Exception:
        pass
    return SHADOW_CAPITAL, "CONFIGURED_SHADOW_CAPITAL"


async def _discover_legs(http: httpx.AsyncClient) -> tuple[list[ComboLeg], dict[str, Any]]:
    now = time.time()
    horizon = now + HORIZON_HOURS * 3600
    events = await fetch_active_sports_events(http)
    raw: list[dict[str, Any]] = []
    for event in events:
        event_expiry = _parse_dt(event.get("expiryDate"))
        if event_expiry is None or event_expiry <= now or event_expiry > horizon:
            continue
        ticker = str(event.get("ticker") or "")
        title = str(event.get("title") or "")
        for contract in event.get("contracts") or []:
            if not isinstance(contract, Mapping):
                continue
            if str(contract.get("status") or "").lower() != "active" or str(contract.get("marketState") or "").lower() != "open":
                continue
            prices = contract.get("prices") if isinstance(contract.get("prices"), Mapping) else {}
            ask = dec(prices.get("bestAsk")); bid = dec(prices.get("bestBid"))
            cid = _numeric_contract_id(contract); symbol = str(contract.get("instrumentSymbol") or "")
            if ask is None or bid is None or cid is None or not symbol or not (D("0") <= bid < ask <= ONE):
                continue
            mid = (ask + bid) / D("2")
            expiry = _parse_dt(contract.get("expiryDate")) or event_expiry
            common = {
                "event_ticker": ticker, "title": title, "symbol": symbol, "contract_id": cid,
                "label": str(contract.get("label") or ""), "expiry": expiry,
                "terms": str(contract.get("termsAndConditionsUrl") or ""),
                "physical_event": physical_event_key(ticker, title),
            }
            # Selected NO, hedge exact YES.
            if ask <= MAX_COMPLEMENT_ASK:
                raw.append({**common, "required": "No", "complement": "YES", "selected_mid": ONE - mid, "top_complement": ask})
            # Selected YES, hedge exact NO.
            no_ask = ONE - bid
            if no_ask <= MAX_COMPLEMENT_ASK:
                raw.append({**common, "required": "Yes", "complement": "NO", "selected_mid": mid, "top_complement": no_ask})

    # Strongest cheap complements first, while retaining event diversity.
    raw.sort(key=lambda x: (x["top_complement"], -x["selected_mid"], x["expiry"]))
    kept: list[dict[str, Any]] = []
    per_event: defaultdict[str, int] = defaultdict(int)
    for row in raw:
        if per_event[row["physical_event"]] >= 3:
            continue
        kept.append(row); per_event[row["physical_event"]] += 1
        if len(kept) >= MAX_DISCOVERY_LEGS:
            break

    books: dict[str, Any] = {}
    sem = asyncio.Semaphore(15)
    async def fetch_book(symbol: str) -> None:
        async with sem:
            try:
                r = await http.get(f"{BASE}/v1/book/{symbol}", params={"limit_bids": 50, "limit_asks": 50})
                books[symbol] = r.json() if r.status_code == 200 else {}
            except Exception:
                books[symbol] = {}
    await asyncio.gather(*(fetch_book(x["symbol"]) for x in kept))

    legs: list[ComboLeg] = []
    for row in kept:
        lv = _book_levels(books.get(row["symbol"]), row["complement"])
        depth = {q: d for q in range(1, MAX_QTY + 1) if (d := _executable(lv, q)) is not None}
        if 1 not in depth:
            continue
        legs.append(ComboLeg(
            event_ticker=row["event_ticker"], physical_event=row["physical_event"], title=row["title"],
            symbol=row["symbol"], contract_id=row["contract_id"], label=row["label"],
            required_outcome=row["required"], complement_outcome=row["complement"], selected_mid=row["selected_mid"],
            expiry_ts=row["expiry"], terms_url=row["terms"], hedge_depth=depth,
        ))
    return legs, {"sports_events": len(events), "raw_extreme_legs": len(raw), "depth_checked": len(kept), "usable_legs": len(legs)}


def _rough_score(legs: Sequence[ComboLeg]) -> tuple[D, D]:
    fair = ONE
    hedge = D("0")
    for leg in legs:
        fair *= leg.selected_mid
        hedge += leg.hedge_depth[1].all_in_total
    # q1 maker price approximation using exact safety function through make_plan later.
    safe = ONE - hedge - TARGET - D("0.01")
    return fair - max(D("0"), safe), -fair


def _beam_combos(legs: Sequence[ComboLeg]) -> list[tuple[ComboLeg, ...]]:
    """Bounded 2-6-leg manufacturer; never expands same physical event twice."""
    indexed = list(enumerate(legs))
    beam: list[tuple[tuple[int, ...], tuple[ComboLeg, ...]]] = []
    out: list[tuple[ComboLeg, ...]] = []
    # Seed every valid pair; cap to beam width by fillability proxy.
    seeds: list[tuple[tuple[int, ...], tuple[ComboLeg, ...]]] = []
    for i in range(len(indexed)):
        for j in range(i + 1, len(indexed)):
            a, b = legs[i], legs[j]
            if a.physical_event == b.physical_event:
                continue
            seeds.append(((i, j), (a, b)))
    seeds.sort(key=lambda x: _rough_score(x[1]))
    beam = seeds[:BEAM_WIDTH]
    out.extend(x[1] for x in beam)
    for target_n in range(3, 7):
        expanded: list[tuple[tuple[int, ...], tuple[ComboLeg, ...]]] = []
        seen: set[tuple[int, ...]] = set()
        for ids, combo in beam:
            used_events = {x.physical_event for x in combo}
            start = ids[-1] + 1
            for k in range(start, len(legs)):
                leg = legs[k]
                if leg.physical_event in used_events:
                    continue
                new_ids = ids + (k,)
                if new_ids in seen:
                    continue
                seen.add(new_ids)
                expanded.append((new_ids, combo + (leg,)))
        expanded.sort(key=lambda x: _rough_score(x[1]))
        beam = expanded[:BEAM_WIDTH]
        out.extend(x[1] for x in beam)
        if not beam:
            break
    return out


def _signature_for_plan(plan: ComboPlan) -> str:
    sports, families = [], []
    for leg in plan.legs:
        s, f = _event_meta({"ticker": leg.event_ticker, "title": leg.title})
        sports.append(s); families.append(f)
    return signature(leg_count=plan.leg_count, sports=sports, families=families)


def _score_plans(combos: Iterable[Sequence[ComboLeg]], *, now: float) -> list[ComboPlan]:
    plans: list[ComboPlan] = []
    for combo in combos:
        sig = signature(leg_count=len(combo), sports=[_event_meta({"ticker": x.event_ticker, "title": x.title})[0] for x in combo], families=[_event_meta({"ticker": x.event_ticker, "title": x.title})[1] for x in combo])
        hist, _, _ = historical_fill_rate(sig)
        for q in range(1, MAX_QTY + 1):
            plan = make_plan(legs=combo, max_qty=q, now_ts=now, historical_fill_rate=hist)
            if plan is not None and plan.partial_fill_safe_price is not None and plan.locked_per_contract is not None and plan.locked_per_contract >= TARGET:
                plans.append(plan)
    plans.sort(key=lambda p: (p.expected_dollars_per_hour, D(str(p.fillability_score)), -(p.fair_gap or D("99"))), reverse=True)
    return plans


async def _ws_reprice(selected: Sequence[ComboPlan], run_id: str, seconds: int) -> dict[str, Any]:
    """Event-driven proof: recalc plan safe price when any selected hedge bookTicker changes."""
    # q1 event-driven repricing is the live-safe baseline; q2/q3 still get full REST snapshots.
    symbols = sorted({leg.symbol for plan in selected for leg in plan.legs})
    if not symbols or seconds <= 0:
        return {"symbols": 0, "updates": 0, "reprices": 0, "ws_ok": True}
    plan_by_symbol: defaultdict[str, list[ComboPlan]] = defaultdict(list)
    for plan in selected:
        for leg in plan.legs:
            plan_by_symbol[leg.symbol].append(plan)
    latest: dict[str, tuple[D, D, D]] = {}  # symbol -> yes ask, no ask, q1 depth placeholder
    last_safe: dict[str, D | None] = {p.combo_key: p.partial_fill_safe_price for p in selected}
    updates = reprices = 0
    deadline = time.monotonic() + seconds
    try:
        async with websockets.connect(GEMINI_WS, open_timeout=5.0, ping_interval=20) as ws:
            await ws.send(json.dumps({"id": f"combo-mm-{run_id[:8]}", "method": "SUBSCRIBE", "params": [f"{s}@bookTicker" for s in symbols]}, separators=(",", ":")))
            while time.monotonic() < deadline:
                timeout = max(0.1, min(2.0, deadline - time.monotonic()))
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                payload = json.loads(raw)
                if payload.get("status") is not None:
                    if int(payload.get("status")) >= 400:
                        raise RuntimeError(f"Gemini WS subscription error {payload}")
                    continue
                symbol = str(payload.get("s") or "")
                if symbol not in plan_by_symbol:
                    continue
                top = parse_gemini_book_ticker(payload, received_ms=int(time.time() * 1000))
                yes_ask = top.yes_ask if top.yes_ask_size > 0 else ONE
                no_ask = ONE - top.yes_bid if top.yes_bid_size > 0 else ONE
                latest[symbol] = (yes_ask, no_ask, max(top.yes_ask_size, top.yes_bid_size))
                updates += 1
                for plan in plan_by_symbol[symbol]:
                    # Only reprice once every leg has a WS snapshot; q1 safe envelope.
                    hedge_total = D("0"); complete = True
                    for leg in plan.legs:
                        row = latest.get(leg.symbol)
                        if row is None:
                            complete = False; break
                        price = row[0] if leg.complement_outcome == "YES" else row[1]
                        depth = row[2]
                        if price >= ONE or depth < 1:
                            complete = False; break
                        hedge_total += price + fee_total(price, D("1"), TAKER)
                    if not complete:
                        continue
                    from src.gemini_combo_mm_core import max_safe_combo_price
                    safe = max_safe_combo_price(qty=1, hedge_all_in_total=hedge_total)
                    old = last_safe.get(plan.combo_key)
                    if safe != old:
                        reprices += 1; last_safe[plan.combo_key] = safe
                        sig = _signature_for_plan(plan)
                        telemetry_emit(run_id=run_id,event_type="shadow_reprice",combo_key=plan.combo_key,signature_value=sig,leg_count=plan.leg_count,quantity=1,safe_price=safe,fair_proxy=plan.fair_proxy,fair_gap=(plan.fair_proxy-safe) if safe is not None else None,locked_per_contract=TARGET if safe is not None else None,fillability=plan.fillability_score,expected_dollars_per_hour=plan.expected_dollars_per_hour,capital_reserve=plan.capital_reserve,details={"previous_safe_price":str(old) if old is not None else None,"hedge_total":str(hedge_total),"event_symbol":symbol,"order_entry":False})
                        print("GEMINI_COMBO_MM_SHADOW_REPRICE "+json.dumps({"run_id":run_id,"combo_key":plan.combo_key,"old_safe_price":str(old) if old is not None else None,"new_safe_price":str(safe) if safe is not None else None,"fair_proxy":str(plan.fair_proxy),"hedge_total":str(hedge_total),"event_symbol":symbol,"order_entry":False},separators=(",",":")),flush=True)
    except Exception as exc:
        return {"symbols":len(symbols),"updates":updates,"reprices":reprices,"ws_ok":False,"error":f"{type(exc).__name__}:{str(exc)[:180]}"}
    return {"symbols":len(symbols),"updates":updates,"reprices":reprices,"ws_ok":True}


async def run_cycle() -> dict[str, Any]:
    run_id = f"gmm-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    schema_ok = ensure_schema()
    now = time.time()
    async with httpx.AsyncClient(timeout=30.0) as http:
        legs, discovery = await _discover_legs(http)
    combos = _beam_combos(legs)
    plans = _score_plans(combos, now=now)
    capital, capital_source = await _available_usd()
    selected, remaining = reserve_candidates(plans, available_capital=capital, max_resting=MAX_RESTING)

    for plan in selected:
        sig = _signature_for_plan(plan)
        telemetry_emit(run_id=run_id,event_type="shadow_reserved",combo_key=plan.combo_key,signature_value=sig,leg_count=plan.leg_count,quantity=plan.max_qty,safe_price=plan.partial_fill_safe_price,fair_proxy=plan.fair_proxy,fair_gap=plan.fair_gap,locked_per_contract=plan.locked_per_contract,fillability=plan.fillability_score,expected_dollars_per_hour=plan.expected_dollars_per_hour,capital_reserve=plan.capital_reserve,details={"safe_price_by_fill_qty":{str(k):str(v) if v is not None else None for k,v in plan.safe_price_by_fill_qty.items()},"legs":[_leg_to_dict(x) for x in plan.legs],"order_entry":False,"combo_creation":False})

    external_gate = current_mlb_external_gate()
    ws = await _ws_reprice(selected, run_id, WS_SECONDS)
    output = {
        "status": "GEMINI_COMBO_MM_SHADOW_V1",
        "run_id": run_id,
        "target_locked_per_contract": str(TARGET),
        "discovery": discovery,
        "manufactured_combo_structures": len(combos),
        "eligible_partial_fill_safe_plans": len(plans),
        "available_capital": str(capital),
        "capital_source": capital_source,
        "max_simultaneous_resting": MAX_RESTING,
        "reserved_count": len(selected),
        "reserved_capital": str(capital - remaining),
        "capital_remaining": str(remaining),
        "selected": [_plan_to_dict(x) for x in selected],
        "top_plans": [_plan_to_dict(x) for x in plans[:20]],
        "event_driven_repricing": ws,
        "external_hedge_gate": external_gate,
        "partial_fill_rule": "EVERY_FILL_SIZE_1_TO_Q_MUST_PRESERVE_3C",
        "correlation_rule": "UNIQUE_PHYSICAL_EVENT_PRECREATE;_GEMINI_CREATE_CAN_STILL_REJECT",
        "combo_leg_range": [2, 6],
        "quantity_range": [1, MAX_QTY],
        "rank_objective": "EXPECTED_DOLLARS_PER_HOUR",
        "telemetry_schema_ok": schema_ok,
        "combo_creation": False,
        "order_entry": False,
        "read_only": True,
    }
    print("GEMINI_COMBO_MM_SHADOW_V1 "+json.dumps(output,separators=(",",":"),default=str),flush=True)
    return output


async def supervisor() -> None:
    while True:
        started = time.monotonic()
        try:
            await run_cycle()
        except Exception as exc:
            print("GEMINI_COMBO_MM_SHADOW_ERROR "+json.dumps({"error":f"{type(exc).__name__}:{str(exc)[:240]}","order_entry":False},separators=(",",":")),flush=True)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(5.0, CYCLE_SECONDS - elapsed))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(supervisor())
