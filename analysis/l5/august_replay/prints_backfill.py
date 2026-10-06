"""Every trader's fill legs in ONE window -- the prints, not just L5's.

WHY THIS IS SEPARATE FROM backfill.py. That module filters exchange logs to
L5's own padded address, which is exactly right for "what did L5 do". The
frozen fill classifier's second standard asks a different question -- did
volume trade AT OR BELOW our price, whoever traded it -- and L5-only logs
cannot answer it. So this scans the SAME block range with NO address-topic
filter and keeps every decodable leg on the window's two tokens.

THE LAYOUT IS NOT RE-DERIVED. `decode.py` is imported, not copied or edited:
its DECODABLE_EVENTS allow-list, its 6-dp SCALE, its MIN_WORDS length gate and
its sanity bounds are reused verbatim. Only the keying changes -- the frozen
decoder requires a named address in the topics, and here no address is named.

EACH ECONOMIC TRADE IS COUNTED ONCE. One matchOrders emits an OrderFilled per
maker order PLUS one for the taker's own order, and in the taker's own log the
counterparty slot holds the exchange contract (decode.py's
EXCHANGE_AS_COUNTERPARTY). Counting both would double every print. This keeps
the MAKER-ORDER legs -- topic3 is a trader, not the exchange -- because those
are the resting orders that were actually consumed at a stated price, which is
what "a print at or below our price" means. The taker aggregate legs are
counted separately and reported, never summed in.

A FAILED CHUNK IS A HOLE. It is recorded and reported, never written down as
an absence of prints.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.block_times import fetch as fetch_block_times
from src.l5_chain_observer import observer as O
from src.l5_chain_observer.decode import (DECODABLE_EVENTS,
                                          EXCHANGE_AS_COUNTERPARTY,
                                          MAX_SANE_SIZE, MIN_WORDS, SCALE)

ARCHIVE = "https://polygon.drpc.org"
#: smaller than backfill.py's 50: with no address filter a 15-minute window
#: returns ~13k logs per 50 blocks, which overruns the node's response cap.
LOG_SPAN = 10
PACE_S = 0.12


def decode_any(log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One exchange log -> one fill leg, with NO address required.

    Same verified word layout as decode.decode_log: w1 is the CTF token id
    received, w2 the collateral paid and w3 the token amount, both 6-dp.
    """
    topics = [str(t).lower() for t in (log.get("topics") or [])]
    if not topics or topics[0][:10] not in DECODABLE_EVENTS:
        return None
    data = str(log.get("data") or "")[2:]
    if len(data) < 64 * MIN_WORDS:
        return None
    words = [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]
    maker_amt, taker_amt = D(words[2]), D(words[3])
    if taker_amt <= 0:
        return None
    price = maker_amt / taker_amt
    if not (D("0") < price <= D("1")) or taker_amt / SCALE > MAX_SANE_SIZE:
        return None
    counterparty = topics[3] if len(topics) > 3 else ""
    # the padded topic holds a 32-byte word; the address is its last 20 bytes
    cp_addr = "0x" + counterparty[-40:] if counterparty else ""
    return {"token_id": str(words[1]),
            "price": str(price.quantize(D("0.000001"))),
            "size": str(taker_amt / SCALE),
            "leg_role": ("taker_aggregate"
                         if cp_addr in EXCHANGE_AS_COUNTERPARTY
                         else "maker_order"),
            "owner_topic": topics[2] if len(topics) > 2 else None,
            "counterparty_topic": counterparty or None,
            "tx": log.get("transactionHash"),
            "block": (int(log["blockNumber"], 16)
                      if log.get("blockNumber") else None),
            "log_index": (int(log["logIndex"], 16)
                          if log.get("logIndex") else None),
            "contract": str(log.get("address") or "").lower(),
            "event": topics[0][:10]}


def scan(lo: int, hi: int, url: str = ARCHIVE
         ) -> Tuple[List[dict], List[Tuple[int, int]]]:
    """(logs, holes). No topic filter at all -- every exchange log."""
    out: List[dict] = []
    holes: List[Tuple[int, int]] = []
    b = lo
    while b <= hi:
        top = min(b + LOG_SPAN - 1, hi)
        try:
            logs = O.rpc("eth_getLogs",
                         [{"fromBlock": hex(b), "toBlock": hex(top),
                           "address": list(O.EXCHANGES)}], url)
            out.extend(logs or [])
        except O.RpcError:
            holes.append((b, top))
        time.sleep(PACE_S)
        b = top + 1
    return out, holes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selection", required=True)
    ap.add_argument("--slug", default=None,
                    help=("scan THIS window instead of the selection's "
                          "chosen one -- used for the generality test over "
                          "the other 11 L5-active windows"))
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--out",
                    default="analysis/l5/august_replay/window_prints.json")
    a = ap.parse_args(argv)

    sel = json.load(open(a.selection))
    slug = a.slug or sel["selected"]
    if not slug:
        print("no window selected -- nothing to scan")
        return 3
    row = next((r for r in sel["rows"] if r["market_slug"] == slug), None)
    if row is None or "tokens" not in row:
        print("window %s is not in the selection report" % slug)
        return 3
    bf = next(w for w in json.load(open(a.backfill))["rows"]
              if w["market_slug"] == slug)
    lo, hi = bf["blocks"]
    tokens = set(row["tokens"])
    print("window %s blocks %d..%d (%d blocks)" % (slug, lo, hi, hi - lo + 1),
          flush=True)

    logs, holes = scan(lo, hi)
    print("%d raw exchange logs, %d holes" % (len(logs), len(holes)),
          flush=True)

    maker_legs: List[dict] = []
    taker_legs: List[dict] = []
    seen = set()
    declined = 0
    off_token = 0
    for lg in logs:
        leg = decode_any(lg)
        if leg is None:
            declined += 1
            continue
        if leg["token_id"] not in tokens:
            off_token += 1
            continue
        k = (leg["tx"], leg["log_index"])
        if k in seen:
            continue
        seen.add(k)
        (taker_legs if leg["leg_role"] == "taker_aggregate"
         else maker_legs).append(leg)

    blocks = sorted({l["block"] for l in maker_legs + taker_legs
                     if l["block"] is not None})
    # REAL block timestamps, never interpolated: a print whose second cannot
    # be established must stay missing rather than land on the wrong tick.
    print("fetching %d block timestamps" % len(blocks), flush=True)
    btimes = fetch_block_times(blocks)
    rep = {
        "market_slug": slug, "blocks": [lo, hi],
        "window_utc": [row["window_start_utc"], row["window_end_utc"]],
        "tokens": row["tokens"],
        "raw_exchange_logs": len(logs),
        "chunk_holes": len(holes), "hole_ranges": holes[:50],
        "coverage": ("COMPLETE" if not holes else "%d HOLE(S)" % len(holes)),
        "declined_not_decodable": declined,
        "legs_on_other_tokens": off_token,
        "n_maker_order_legs": len(maker_legs),
        "n_taker_aggregate_legs": len(taker_legs),
        "distinct_blocks_with_legs": len(blocks),
        "block_times_resolved": len(btimes),
        "block_times": {str(k): v for k, v in btimes.items()},
        "maker_order_legs": maker_legs,
        "taker_aggregate_legs": taker_legs,
        "labels": ("maker-order legs are the resting orders consumed at a "
                   "stated price and are what 'a print at or below our "
                   "price' means; taker aggregate legs are reported beside "
                   "them and never summed in. A failed chunk is a HOLE and "
                   "is never recorded as an absence of prints."),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in rep.items()
                      if k not in ("maker_order_legs", "taker_aggregate_legs",
                                   "block_times",
                                   "hole_ranges")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
