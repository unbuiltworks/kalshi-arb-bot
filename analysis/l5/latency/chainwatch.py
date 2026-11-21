"""Is reading Polygon DIRECTLY faster than any Polymarket feed for L5's fills?

WHY THIS IS THE RIGHT EXPERIMENT. Measured on identical events:

  * the CLOB websocket's `last_trade_price` timestamp is the MATCH time;
  * the block containing that trade lands ~2.9 s later (p50, measured);
  * but the websocket does not PUBLISH the print until ~15 s after the match.

So roughly twelve of those fifteen seconds are publication delay, not
consensus. If that is right, the chain itself carries L5's fill about five
times sooner than any Polymarket surface does -- and since his address sits in
an INDEXED topic of the exchange's fill event, it can be filtered for directly
rather than scraped.

That is a hypothesis, and it is the kind that is easy to believe and wrong.
This measures it: watch for new blocks, pull L5-topic logs, and record when we
could first have known, against the same fill's arrival on /activity.

READ ONLY. Public RPC, no key, no wallet, no transaction is ever sent.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import time
import urllib.request
from typing import Any, Dict, List, Optional

ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
PAD = "0x" + ADDR[2:].lower().rjust(64, "0")
UA = {"User-Agent": "l5-chainwatch/1.0 (read-only research)",
      "Content-Type": "application/json"}
RPC = "https://polygon-bor-rpc.publicnode.com"
RPC_ALT = "https://polygon.drpc.org"
DATA = "https://data-api.polymarket.com"


def _ca() -> ssl.SSLContext:
    for p in (os.environ.get("SSL_CERT_FILE"), "/root/.ccr/ca-bundle.crt"):
        if p and os.path.exists(p):
            return ssl.create_default_context(cafile=p)
    return ssl.create_default_context()


def rpc(method: str, params: List[Any], url: str = RPC) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    try:
        req = urllib.request.Request(url, data=body, headers=UA, method="POST")
        with urllib.request.urlopen(req, timeout=15, context=_ca()) as fh:
            return json.loads(fh.read().decode()).get("result")
    except Exception:
        return None


def activity() -> List[dict]:
    u = f"{DATA}/activity?user={ADDR}&limit=500&type=TRADE"
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA),
                                    timeout=20, context=_ca()) as fh:
            return json.loads(fh.read().decode())
    except Exception:
        return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=15.0)
    ap.add_argument("--out", default="analysis/l5/latency/chainwatch.jsonl")
    a = ap.parse_args(argv)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)

    seen_block = 0
    chain_first: Dict[str, Dict[str, Any]] = {}
    act_first: Dict[str, float] = {}
    rows: List[Dict[str, Any]] = []
    deadline = time.time() + a.minutes * 60
    last_act = 0.0

    def flush() -> None:
        with open(a.out, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    while time.time() < deadline:
        # --- chain: any log with L5 in an indexed topic, positions 1..3
        bn = rpc("eth_blockNumber", [])
        if bn:
            n = int(bn, 16)
            if n > seen_block:
                lo = max(seen_block + 1, n - 4) if seen_block else n
                for pos in (1, 2, 3):
                    topics: List[Any] = [None, None, None, None]
                    topics[pos] = PAD
                    logs = rpc("eth_getLogs", [{
                        "fromBlock": hex(lo), "toBlock": hex(n),
                        "topics": topics[:pos + 1]}])
                    t_det = time.time()
                    for lg in (logs or []):
                        tx = lg.get("transactionHash")
                        if not tx or tx in chain_first:
                            continue
                        blk = rpc("eth_getBlockByNumber",
                                  [lg["blockNumber"], False])
                        bts = int(blk["timestamp"], 16) if blk else None
                        chain_first[tx] = {
                            "detected_at": t_det, "block_ts": bts,
                            "block": int(lg["blockNumber"], 16),
                            "contract": lg.get("address"),
                            "topic_pos": pos,
                        }
                seen_block = n

        # --- the same fills as /activity reports them
        if time.time() - last_act >= 1.0:
            last_act = time.time()
            for x in activity():
                tx = x.get("transactionHash")
                if tx and tx not in act_first:
                    act_first[tx] = last_act

        # --- join on transactionHash and emit whatever is complete
        rows = []
        for tx, c in chain_first.items():
            src = c["block_ts"]
            av = act_first.get(tx)
            rows.append({
                "tx": tx, "block": c["block"], "block_ts": src,
                "chain_detected_at": c["detected_at"],
                "chain_lag_from_block_s": (round(c["detected_at"] - src, 3)
                                           if src else None),
                "activity_first_seen_at": av,
                "activity_lag_from_block_s": (round(av - src, 3)
                                              if (av and src) else None),
                "chain_earlier_by_s": (round(av - c["detected_at"], 3)
                                       if av else None),
                "contract": c["contract"], "topic_pos": c["topic_pos"],
            })
        flush()
        time.sleep(0.4)

    flush()
    print(f"wrote {a.out}: {len(rows)} L5 chain events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
