"""Can L5's fills be attributed in NEAR-REAL TIME from public sources?

THE CHAIN UNDER TEST, end to end, on identical events:

  1. CLOB websocket `last_trade_price`  -> carries `transaction_hash`,
     price, size, side, ms timestamp. ANONYMOUS, arrives in ~50 ms.
  2. eth_getTransactionReceipt(hash) on a KEYLESS public Polygon RPC -> the
     logs name the wallets party to the trade. This is where identity enters.
  3. data-api /activity?user=L5 -> the same transactionHash, named, but tens
     of seconds later.

If (1)+(2) attributes a fill to L5 materially sooner than (3), then the
~20 s figure is a property of ONE endpoint, not a limit on observing L5, and
estimand B is blocked by the wrong constraint.

WHAT MUST NOT BE ASSUMED. A websocket print is not proof the transaction is
already mined. The receipt only exists once the block lands, so the honest
measurement is time-until-the-receipt-is-actually-fetchable, polled, not the
single lucky lookup of an old hash. That is what this measures.

READ ONLY. Public endpoints, no credentials, no order path, no transaction is
ever sent.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import time
import urllib.request
from typing import Any, Dict, List, Optional

ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
UA = {"User-Agent": "l5-reconstruct/1.0 (read-only research)",
      "Content-Type": "application/json"}
GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
# verified keyless from this environment; tried and rejected: polygon-rpc.com
# (401), ankr (key required), 1rpc (quota), blockpi (521), blast (403).
RPCS = ["https://polygon-bor-rpc.publicnode.com", "https://polygon.drpc.org"]


def _ca() -> ssl.SSLContext:
    for p in (os.environ.get("SSL_CERT_FILE"), "/root/.ccr/ca-bundle.crt"):
        if p and os.path.exists(p):
            return ssl.create_default_context(cafile=p)
    return ssl.create_default_context()


def _get(url: str, timeout: float = 20.0) -> Optional[Any]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                    timeout=timeout, context=_ca()) as fh:
            return json.loads(fh.read().decode())
    except Exception:
        return None


def receipt(tx: str, rpc: str, timeout: float = 8.0) -> Optional[Dict[str, Any]]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "method": "eth_getTransactionReceipt",
                       "params": [tx]}).encode()
    try:
        req = urllib.request.Request(rpc, data=body, headers=UA, method="POST")
        with urllib.request.urlopen(req, timeout=timeout, context=_ca()) as fh:
            return json.loads(fh.read().decode()).get("result")
    except Exception:
        return None


def live_tokens() -> List[str]:
    out: List[str] = []
    now = int(time.time())
    for horizon, dur in (("15m", 900), ("5m", 300)):
        slug = f"btc-updown-{horizon}-{(now // dur) * dur}"
        ms = _get(f"{GAMMA}/markets?slug={slug}") or []
        for m in ms if isinstance(ms, list) else []:
            t = m.get("clobTokenIds")
            t = json.loads(t) if isinstance(t, str) else t
            out.extend(t or [])
    return out


async def main_async(minutes: float, out_path: str) -> None:
    import websockets
    rows: List[Dict[str, Any]] = []
    deadline = time.time() + minutes * 60
    rpc_i = 0

    def write() -> None:
        with open(out_path, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    while time.time() < deadline:
        toks = live_tokens()
        if not toks:
            await asyncio.sleep(5)
            continue
        try:
            async with websockets.connect(WS_URL, open_timeout=20,
                                          ssl=_ca()) as ws:
                await ws.send(json.dumps({"assets_ids": toks, "type": "market"}))
                opened = time.time()
                while time.time() < deadline and time.time() - opened < 240:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=15)
                    except asyncio.TimeoutError:
                        continue
                    t_ws = time.time()
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    for ev in (d if isinstance(d, list) else [d]):
                        if (not isinstance(ev, dict)
                                or ev.get("event_type") != "last_trade_price"):
                            continue
                        tx = ev.get("transaction_hash")
                        if not tx:
                            continue
                        src = float(ev.get("timestamp") or 0) / 1000.0
                        # poll until the receipt actually exists; a single
                        # lookup would silently measure "was it already mined"
                        rpc_i += 1
                        rpc = RPCS[rpc_i % len(RPCS)]
                        t_first = None
                        rec = None
                        t_try = time.time()
                        while time.time() - t_try < 20:
                            rec = await asyncio.to_thread(receipt, tx, rpc)
                            if rec:
                                t_first = time.time()
                                break
                            await asyncio.sleep(0.25)
                        is_l5 = None
                        if rec:
                            is_l5 = ADDR[2:].lower() in json.dumps(
                                rec.get("logs", [])).lower()
                        rows.append({
                            "tx": tx, "ws_source_ts": src, "ws_observed_at": t_ws,
                            "ws_lag_s": round(t_ws - src, 3),
                            "price": ev.get("price"), "size": ev.get("size"),
                            "side": ev.get("side"), "market": ev.get("market"),
                            "receipt_available_at": t_first,
                            "receipt_wait_s": (round(t_first - t_ws, 3)
                                               if t_first else None),
                            "attributable_at_s": (round(t_first - src, 3)
                                                  if t_first else None),
                            "is_l5": is_l5, "rpc": rpc,
                        })
                        write()
        except Exception:
            await asyncio.sleep(3)
    write()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=12.0)
    ap.add_argument("--out", default="analysis/l5/latency/reconstruct.jsonl")
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    asyncio.run(main_async(a.minutes, a.out))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
