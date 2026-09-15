"""Observe L5's fills from Polygon, and cross-validate before believing it.

WHY. Every Polymarket-published surface carries L5 a median 15 s late, because
publication waits ~12 s after the block already confirmed. The chain itself is
~3 s. That is a 5x improvement in when we can know, and it is the difference
between the 5 s / 15 s horizons being observable and not.

WHY IT IS NOT PROMOTED. Speed is the easy half. A faster feed that disagrees
about WHAT happened is worse than a slow one that does not, and the failure
would be silent: a missing fill looks exactly like L5 not trading. So this
module's job is not to be fast, it is to be CHECKABLE. `cross_validate` joins
chain-derived fills to activity-derived fills on transactionHash -- an exact
key both carry -- and reports agreement, chain-only and feed-only sets
separately. Feed-only rows are the dangerous ones: they are fills the chain
MISSED.

READ ONLY, STRUCTURALLY. Public RPC, no key, no wallet, no signing, no order
verb anywhere in this package. It never writes to the executor's state dir and
never touches FIDELITY.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

ADDR = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
PAD = "0x" + ADDR[2:].lower().rjust(64, "0")
UA = {"User-Agent": "l5-chain-observer/1.0 (read-only research)",
      "Content-Type": "application/json"}
# verified keyless from this environment 2026-09-15; polygon-rpc.com 401,
# ankr key-required, 1rpc quota, blockpi 521, blast 403.
RPCS = ("https://polygon-bor-rpc.publicnode.com", "https://polygon.drpc.org")
DATA = "https://data-api.polymarket.com"
# L5's address appears in an INDEXED topic of the exchange fill event, so it is
# filterable rather than scrapeable. Position varies by event, hence the sweep.
TOPIC_POSITIONS = (1, 2, 3)
# Public RPCs refuse an unfiltered eth_getLogs ("Please specify an address"),
# and my first version swallowed that error into an EMPTY RESULT -- 0 fills
# against 480 the feed had, reported as ok. Exactly the silent zero this module
# exists to prevent, in the module itself. Addresses are now always supplied
# and transport/JSON-RPC errors are propagated rather than flattened.
#   0xe111... CTF Exchange  -- L5 in topic2 of the fill event
#   0x4d97... CTF ERC1155   -- L5 in topic3
EXCHANGES = ("0xe111180000d2663c0091e4f400237545b87b996b",
             "0x4d97dcd97ec945f40cf65f87097ace5ea0476045")


class RpcError(RuntimeError):
    """The question could not be put. NEVER interchangeable with "no fills"."""


def _ca() -> ssl.SSLContext:
    for p in (os.environ.get("SSL_CERT_FILE"), "/root/.ccr/ca-bundle.crt"):
        if p and os.path.exists(p):
            return ssl.create_default_context(cafile=p)
    return ssl.create_default_context()


def rpc(method: str, params: List[Any], url: str = RPCS[0],
        timeout: float = 15.0) -> Any:
    """Raises RpcError when the question could not be put. Returning None on
    failure is what turned a refused query into "L5 did nothing"."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    try:
        req = urllib.request.Request(url, data=body, headers=UA, method="POST")
        with urllib.request.urlopen(req, timeout=timeout, context=_ca()) as fh:
            d = json.loads(fh.read().decode())
    except Exception as e:
        raise RpcError(f"{type(e).__name__}: {e}") from e
    if isinstance(d, dict) and d.get("error"):
        raise RpcError(json.dumps(d["error"])[:200])
    return (d or {}).get("result")


def chain_fills(from_block: int, to_block: int,
                url: str = RPCS[0]) -> Dict[str, dict]:
    """L5-bearing transactions in a block range, keyed by transactionHash.

    Raises RpcError if any query fails. An empty dict therefore means the
    chain was asked successfully and had nothing, which is a measurement;
    a failure raises instead of returning the same value."""
    out: Dict[str, dict] = {}
    for pos in TOPIC_POSITIONS:
        topics: List[Any] = [None] * (pos + 1)
        topics[pos] = PAD
        logs = rpc("eth_getLogs", [{"fromBlock": hex(from_block),
                                    "toBlock": hex(to_block),
                                    "address": list(EXCHANGES),
                                    "topics": topics}], url)
        for lg in (logs or []):
            tx = lg.get("transactionHash")
            if tx:
                out.setdefault(tx, {"tx": tx,
                                    "block": int(lg["blockNumber"], 16),
                                    "contract": lg.get("address"),
                                    "topic_pos": pos})
    return out


def chain_fills_checked(from_block: int, to_block: int,
                        url: str = RPCS[0]) -> Tuple[bool, Dict[str, dict]]:
    """(asked_successfully, fills). ok=False means the question could not be
    put; it is never interchangeable with an answer of nothing. Tries the
    fallback RPC before giving up."""
    for u in (url,) + tuple(r for r in RPCS if r != url):
        try:
            return True, chain_fills(from_block, to_block, u)
        except RpcError:
            continue
    return False, {}


def activity_fills(limit: int = 500) -> Optional[Dict[str, dict]]:
    """L5's recent fills as the venue publishes them, keyed by the SAME
    transactionHash the chain uses. None means the feed could not be read.

    A TRANSACTION IS NOT A FILL. Measured 2026-09-15 over 500 feed rows: 22
    transactions carried TWO fills each, so keying by hash with `setdefault`
    silently discarded 22 rows -- 4.4% of the sample. It also made the chain
    look wrong: a transaction holding 80 @ 0.44 and 80 @ 0.43 was compared
    against whichever single row arrived first, producing a phantom one-cent
    disagreement that was entirely on this side.

    Every fill is therefore retained under `fills`, with the aggregate the
    chain can actually be compared against beside it. The top-level keys stay
    transaction hashes so the miss test (feed_only) is unchanged."""
    u = f"{DATA}/activity?user={ADDR}&limit={limit}&type=TRADE"
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA),
                                    timeout=20, context=_ca()) as fh:
            rows = json.loads(fh.read().decode())
    except Exception:
        return None
    out: Dict[str, dict] = {}
    for x in rows if isinstance(rows, list) else []:
        tx = x.get("transactionHash")
        if not tx:
            continue
        e = out.setdefault(tx, {"tx": tx, "ts": x.get("timestamp"),
                                "slug": x.get("slug"), "size": x.get("size"),
                                "price": x.get("price"), "side": x.get("side"),
                                "fills": []})
        e["fills"].append({"price": x.get("price"), "size": x.get("size"),
                           "outcome": x.get("outcome"), "slug": x.get("slug"),
                           "side": x.get("side"), "ts": x.get("timestamp")})
        # earliest timestamp wins, so the window filter sees when the
        # transaction's first fill was published rather than its last
        if x.get("timestamp") is not None and (
                e["ts"] is None or float(x["timestamp"]) < float(e["ts"])):
            e["ts"] = x.get("timestamp")
    for e in out.values():
        e["n_fills"] = len(e["fills"])
        agg = _agg_fills(e["fills"])
        e["agg_size"], e["agg_price"] = agg["size"], agg["price"]
    return out


def _agg_fills(fills: List[dict]) -> Dict[str, Optional[str]]:
    """Size-weighted aggregate over a transaction's fills.

    This is the only price comparable against a multi-fill transaction's
    chain-side aggregate; a single fill's price is a different number.
    """
    from decimal import Decimal as _D
    size = _D("0")
    notional = _D("0")
    for f in fills:
        try:
            sz, px = _D(str(f["size"])), _D(str(f["price"]))
        except Exception:                                     # noqa: BLE001
            continue
        size += sz
        notional += sz * px
    if size <= 0:
        return {"size": None, "price": None}
    return {"size": str(size),
            "price": str((notional / size).quantize(_D("0.000001")))}


def cross_validate(chain: Dict[str, dict], feed: Dict[str, dict],
                   *, window: Optional[Tuple[float, float]] = None
                   ) -> Dict[str, Any]:
    """Do the two sources agree about WHAT happened?

    Joined on transactionHash, an exact key both carry, so disagreement is a
    fact rather than a matching artifact.

    THE ASYMMETRY MATTERS. `chain_only` is expected and benign: the chain sees
    a fill before the feed publishes it, which is the entire point.
    `feed_only` is the DANGEROUS set -- fills the chain missed -- and it is the
    one that must be empty before promotion, because a missed fill is
    indistinguishable from L5 not trading.

    A feed-only row inside the publication lag is not yet evidence of a miss,
    so `window` restricts the comparison to a block range both sources had
    time to cover. Without it the result is reported as INDICATIVE only."""
    ck, fk = set(chain), set(feed)
    if window is not None:
        lo, hi = window
        fk = {t for t in fk
              if feed[t].get("ts") is not None and lo <= float(feed[t]["ts"]) <= hi}
    both, conly, fonly = ck & fk, ck - fk, fk - ck
    n = len(ck | fk)
    return {
        "n_union": n,
        "agree": len(both),
        "chain_only": sorted(conly),
        "feed_only": sorted(fonly),
        "agreement_rate": (round(len(both) / n, 4) if n else None),
        "windowed": window is not None,
        "promotion_blocker": (
            None if (window is not None and not fonly)
            else ("feed_only is non-empty: the chain MISSED %d fill(s) the "
                  "venue published" % len(fonly)) if fonly
            else "unwindowed comparison is INDICATIVE only, not a promotion test"),
        "labels": ("chain_only is expected (the chain is ~12s ahead of "
                   "publication). feed_only is the dangerous set and must be "
                   "empty over a settled window before promotion."),
    }


PROMOTED = False   # flipped only by a human, after cross-validation passes.
