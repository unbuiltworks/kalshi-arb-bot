"""Read-only diagnostic for Gemini short-crypto native UP/DOWN books.

This probe exists to answer a narrow data-quality question before changing the l5
model: are BTC/ETH 5m and 15m markets actually missing two-sided liquidity, or is
our UP-only adapter discarding usable native DOWN-book information?

Safety: public REST only, no credentials, no order entry, no fill assumptions,
no P&L claims. UP and DOWN contracts are reported separately; this module does
not synthesize complements or claim complete-set economics.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping

import httpx

REST = "https://api.gemini.com"
EVENT_RE = re.compile(r"^(BTC|ETH)(05M|15M)(\d{10})$")
TARGETS = ("BTC05M", "BTC15M", "ETH05M", "ETH15M")


@dataclass(frozen=True)
class Contract:
    symbol: str
    side: str
    price_increment: D


@dataclass(frozen=True)
class Event:
    ticker: str
    asset: str
    horizon: str
    expiry_ms: int
    up: Contract | None
    down: Contract | None

    @property
    def key(self) -> str:
        return f"{self.asset}-{self.horizon}"


def _iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _contract(row: Mapping[str, Any]) -> Contract | None:
    symbol = str(row.get("instrumentSymbol") or "").strip()
    upper = symbol.upper()
    if upper.endswith("-UP"):
        side = "UP"
    elif upper.endswith("-DOWN"):
        side = "DOWN"
    else:
        return None
    state = str(row.get("marketState") or row.get("status") or "").lower()
    if state not in {"open", "active"}:
        return None
    return Contract(symbol=symbol, side=side, price_increment=D(str(row.get("priceIncrement") or "0.01")))


async def discover(http: httpx.AsyncClient) -> dict[str, Event]:
    found: dict[str, list[Event]] = {target: [] for target in TARGETS}
    now_ms = int(time.time() * 1000)
    offset = 0
    while offset < 5000:
        response = await http.get(
            f"{REST}/v1/prediction-markets/events",
            params=[("status", "active"), ("category", "Crypto"), ("limit", "500"), ("offset", str(offset))],
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or [] if isinstance(payload, dict) else []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            ticker = str(row.get("ticker") or "").upper().strip()
            match = EVENT_RE.match(ticker)
            if not match:
                continue
            asset, duration, _ = match.groups()
            expiry = _iso_ms(row.get("expiryDate"))
            if expiry is None:
                for c in row.get("contracts") or []:
                    if isinstance(c, Mapping):
                        expiry = _iso_ms(c.get("expiryDate"))
                        if expiry is not None:
                            break
            if expiry is None or expiry <= now_ms:
                continue
            contracts = [_contract(c) for c in row.get("contracts") or [] if isinstance(c, Mapping)]
            up = next((c for c in contracts if c and c.side == "UP"), None)
            down = next((c for c in contracts if c and c.side == "DOWN"), None)
            horizon = "5m" if duration == "05M" else "15m"
            found[f"{asset}{duration}"].append(Event(ticker, asset, horizon, expiry, up, down))
        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        total = int((pagination or {}).get("total") or len(rows))
        offset += len(rows)
        if not rows or offset >= total:
            break
    return {
        target: min(events, key=lambda e: e.expiry_ms)
        for target, events in found.items() if events
    }


def _top(payload: Any) -> dict[str, str | None]:
    if not isinstance(payload, Mapping):
        return {"bid": None, "bid_qty": None, "ask": None, "ask_qty": None}
    bids = payload.get("bids") or []
    asks = payload.get("asks") or []
    def pxq(rows: Any, best: str) -> tuple[str | None, str | None]:
        parsed: list[tuple[D, D]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            try:
                p = D(str(row.get("price")))
                q = D(str(row.get("amount") or row.get("quantity") or row.get("remaining") or 0))
            except Exception:
                continue
            if p > 0 and q > 0:
                parsed.append((p, q))
        if not parsed:
            return None, None
        p, q = (max(parsed) if best == "bid" else min(parsed))
        return str(p), str(q)
    bid, bid_qty = pxq(bids, "bid")
    ask, ask_qty = pxq(asks, "ask")
    return {"bid": bid, "bid_qty": bid_qty, "ask": ask, "ask_qty": ask_qty}


async def native_book(http: httpx.AsyncClient, contract: Contract | None) -> dict[str, Any]:
    if contract is None:
        return {"symbol": None, "contract_present": False, "bid": None, "bid_qty": None, "ask": None, "ask_qty": None, "two_sided": False}
    response = await http.get(
        f"{REST}/v1/book/{contract.symbol}",
        params={"limit_bids": 20, "limit_asks": 20},
        headers={"Cache-Control": "no-cache"},
    )
    response.raise_for_status()
    top = _top(response.json())
    return {
        "symbol": contract.symbol,
        "contract_present": True,
        "price_increment": str(contract.price_increment),
        **top,
        "two_sided": top["bid"] is not None and top["ask"] is not None,
    }


async def capture(seconds: float, poll_s: float, output: Path) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, seconds)
    output.parent.mkdir(parents=True, exist_ok=True)
    stats = {key: {"samples": 0, "up_two_sided": 0, "down_two_sided": 0, "both_native_two_sided": 0, "errors": 0} for key in ("BTC-5m", "BTC-15m", "ETH-5m", "ETH-15m")}
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-native-book-probe/1.0"}) as http:
        with output.open("w", encoding="utf-8") as fh:
            while time.monotonic() < deadline:
                started = time.monotonic()
                try:
                    events = await discover(http)
                except Exception as exc:
                    print(json.dumps({"type":"GEMINI_NATIVE_BOOK_DISCOVERY_ERROR","error_type":type(exc).__name__,"error":str(exc)[:240],"read_only":True}), flush=True)
                    await asyncio.sleep(max(0.1, poll_s))
                    continue
                for event in events.values():
                    key = event.key
                    try:
                        up, down = await asyncio.gather(native_book(http, event.up), native_book(http, event.down))
                        row = {
                            "type": "GEMINI_NATIVE_SHORT_CRYPTO_BOOK",
                            "observed_at": datetime.now(timezone.utc).isoformat(),
                            "ticker": event.ticker,
                            "asset": event.asset,
                            "horizon": event.horizon,
                            "seconds_to_expiry": round((event.expiry_ms - int(time.time()*1000))/1000, 3),
                            "up": up,
                            "down": down,
                            "both_native_two_sided": bool(up.get("two_sided") and down.get("two_sided")),
                            "synthetic_complement_used": False,
                            "fill_status": "NO_FILL_ASSUMED",
                            "pnl_status": "NOT_COMPUTED",
                            "read_only": True,
                            "order_entry": False,
                        }
                        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                        fh.flush()
                        stats[key]["samples"] += 1
                        stats[key]["up_two_sided"] += int(bool(up.get("two_sided")))
                        stats[key]["down_two_sided"] += int(bool(down.get("two_sided")))
                        stats[key]["both_native_two_sided"] += int(row["both_native_two_sided"])
                    except Exception as exc:
                        stats[key]["errors"] += 1
                        print(json.dumps({"type":"GEMINI_NATIVE_BOOK_ERROR","ticker":event.ticker,"asset":event.asset,"horizon":event.horizon,"error_type":type(exc).__name__,"error":str(exc)[:240],"read_only":True}), flush=True)
                await asyncio.sleep(max(0.05, poll_s - (time.monotonic() - started)))
    summary = {"type":"GEMINI_NATIVE_SHORT_CRYPTO_BOOK_SUMMARY","seconds_requested":seconds,"poll_s":poll_s,"series":stats,"read_only":True,"order_entry":False,"realized_pnl":None}
    print(json.dumps(summary, separators=(",", ":")), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=360.0)
    parser.add_argument("--poll-s", type=float, default=2.0)
    parser.add_argument("--output", type=Path, default=Path("artifacts/gemini-native-short-crypto-book.jsonl"))
    args = parser.parse_args()
    asyncio.run(capture(args.seconds, args.poll_s, args.output))


if __name__ == "__main__":
    main()
