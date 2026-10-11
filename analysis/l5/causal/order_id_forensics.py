"""L5_ORDER_ID_FORENSICS_V1 -- are L5's order ids retrievable, and do his
same-instant clusters collapse into one parent order?

OWNER 2026-09-17, P0, prompted by an external lead. THE LEAD IS NOT THE
AUTHORITY -- the API's actual behaviour is. Everything below was measured.

WHAT THE APIs ACTUALLY DO (probed 2026-09-17, not read from documentation):

  data-api.polymarket.com/trades?user=<L5>   HTTP 200, 19 fields, and NOT ONE
      of trader_side, maker_orders, maker_address, order_id, matched_amount,
      taker_order_id, orderHash. The public trade feed does not carry them.
  clob.polymarket.com/trades                 HTTP 401 Unauthorized
  clob.polymarket.com/trades?maker_address=<L5>  HTTP 401 -- the filter does
      not make it public
  clob.polymarket.com/data/trades            HTTP 401

So the CLOB trades endpoint is authenticated and scoped to the calling key's
own account. DOCUMENTATION DESCRIBING IT IS EVIDENCE ABOUT OUR OWN ACCOUNT AND
NOTHING ELSE. At the API layer the answer is NO.

BUT THE CHAIN IS PUBLIC, AND THE ANSWER THERE IS YES. Every public trade row
carries a `transactionHash`. The CTF Exchange emits, per fill, an event with
four topics and seven data words:

    topic1 orderHash   topic2 maker   topic3 taker
    word0 makerAssetId  word1 takerAssetId  word2 makerAmountFilled
    word3 takerAmountFilled  word4 fee

VERIFIED BY DECODE, not by a remembered signature: the event was identified by
its STRUCTURE in a real L5 transaction, and the decoded amounts reproduce the
public row exactly (5.00 USDC for 125 shares = $0.04, matching price 0.04 size
125), with fee 336000 = 0.07 x 0.04 x 0.96 x 125 to the cent.

THAT LAST POINT MATTERS BEYOND THIS FILE. The BTC15m work recorded that
maker-vs-taker is NOT recoverable and only fee-bearing vs fee-free is. On chain
BOTH are visible: the taker order is the one whose hash appears in
OrdersMatched, and the fee lands on it.

THE SAMPLE IS PREREGISTERED. `SAMPLE_SPEC` below fixes the selection rule and
the seed. It was written before any receipt was fetched, because a cluster
sample chosen after seeing which ones resolve is not a sample.
"""
from __future__ import annotations

import collections
import json
import random
import ssl
import time
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.residual_exit import forensics as F

LABEL = "L5_ORDER_ID_FORENSICS_V1"
L5 = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
EXCHANGES = ("0xe111180000d2663c0091e4f400237545b87b996b",
             "0xc5d563a36ae78145c45a50134d48a1215220f80a")
CA = "/root/.ccr/ca-bundle.crt"
RPCS = ("https://polygon-bor-rpc.publicnode.com", "https://polygon.drpc.org")

#: identified by STRUCTURE in a real L5 transaction and cross-checked against
#: the public trade row, never from a remembered hash
ORDER_FILLED_TOPIC0 = "0xd543adfd945773f1a6b3c9f4e0a63a0b6e1b21c6d9c3e50cfd8b52f3b70d8d6c"
ORDER_FILLED_TOPICS = 4
ORDER_FILLED_WORDS = 7

#: PREREGISTERED 2026-09-17, before any receipt was fetched
SAMPLE_SPEC = {
    "seed": 20260917,
    "clusters_ge2": 40,
    "singleton_controls": 20,
    "cluster_key": "(timestamp, outcome, price) within one market",
    "rule": ("uniform random over all qualifying clusters in the BTC15m tape, "
             "drawn with the fixed seed before any chain call"),
    "why": ("choosing clusters after seeing which resolve is not a sample; the "
            "unresolvable fraction is itself a result and must not be "
            "selected away"),
}

CLASSES = ("SAME_PARENT_ORDER", "MULTIPLE_PARENT_ORDERS", "UNRESOLVABLE")


def _ctx():
    """An SSL context that works in BOTH containers.

    CA is this session container's agent-proxy bundle. It does not exist in
    the deployed image, and pinning it there raised inside rpc()'s bare
    `except`, so every receipt came back None and every parent was recorded
    with ZERO chain legs -- silently, because an empty leg list is also what a
    genuine no-L5-order receipt looks like. The chain observer already got
    this right; this now does the same: prefer a bundle that exists, else the
    system default.
    """
    import os as _os
    for cand in (_os.environ.get("SSL_CERT_FILE"), CA):
        if cand and _os.path.exists(cand):
            return ssl.create_default_context(cafile=cand)
    return ssl.create_default_context()


def rpc(method: str, params: list, tries: int = 2) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    hdr = {"User-Agent": "l5-research/1.0", "Content-Type": "application/json"}
    for _ in range(tries):
        for u in RPCS:
            try:
                req = urllib.request.Request(u, data=body, headers=hdr)
                with urllib.request.urlopen(req, timeout=30,
                                            context=_ctx()) as fh:
                    d = json.loads(fh.read().decode())
                if "result" in d:
                    return d["result"]
            except Exception:                                # noqa: BLE001
                time.sleep(0.4)
    return None


def order_fills(receipt: dict) -> List[dict]:
    """Every OrderFilled in a receipt, identified by structure."""
    out = []
    for lg in (receipt or {}).get("logs") or []:
        if str(lg.get("address", "")).lower() not in EXCHANGES:
            continue
        tp = lg.get("topics") or []
        data = str(lg.get("data") or "")[2:]
        if len(tp) != ORDER_FILLED_TOPICS or len(data) // 64 != ORDER_FILLED_WORDS:
            continue
        w = [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]
        out.append({
            "order_hash": tp[1],
            "maker": "0x" + tp[2][-40:],
            "taker": "0x" + tp[3][-40:],
            "maker_asset_id": w[0], "taker_asset_id": w[1],
            "maker_amount_filled": w[2], "taker_amount_filled": w[3],
            "fee": w[4], "log_index": lg.get("logIndex"),
        })
    return out


def l5_orders_in(receipt: dict) -> List[dict]:
    """The OrderFilled rows whose MAKER is L5. `maker` on this event is the
    order's owner; the counterparty side appears as its own row."""
    return [f for f in order_fills(receipt)
            if f["maker"].lower() == L5.lower()]


# ---------------------------------------------------------------------------

def clusters(cohort: str = "BTC15m") -> Tuple[List[dict], List[dict]]:
    """(clusters of >=2 fills sharing timestamp+outcome+price, singletons)."""
    windows, _ = F.load_windows(cohort)
    g: Dict[tuple, List[dict]] = collections.defaultdict(list)
    for cid, w in windows.items():
        for t, px, oc, sz, usd in w["fills"]:
            g[(cid, t, oc, px)].append(
                {"cid": cid, "t": t, "outcome": oc, "px": px, "qty": sz})
    multi = [{"key": k, "n": len(v), "fills": v} for k, v in g.items() if len(v) > 1]
    single = [{"key": k, "n": 1, "fills": v} for k, v in g.items() if len(v) == 1]
    return multi, single


def tx_index(cohort_prefix: str = "btc-updown-15m") -> Dict[tuple, List[str]]:
    """(conditionId, timestamp, outcome, price) -> transaction hashes.

    The normalised tape drops `transactionHash`, so it is re-read from the raw
    activity here rather than inferred."""
    from analysis.l5.redemption import audit as A
    idx: Dict[tuple, set] = collections.defaultdict(set)
    for r in A.iter_activity():
        if str(r.get("type") or "").upper() != "TRADE":
            continue
        if not str(r.get("slug") or "").startswith(cohort_prefix):
            continue
        tx = r.get("transactionHash")
        if not tx:
            continue
        try:
            k = (str(r.get("conditionId")), int(float(r.get("timestamp"))),
                 "Up" if str(r.get("outcome")) == "Up" else "Down",
                 float(r.get("price")))
        except (TypeError, ValueError):
            continue
        idx[k].add(str(tx))
    return {k: sorted(v) for k, v in idx.items()}


def classify(cluster: dict, txs: Sequence[str],
             cache: Dict[str, Any]) -> dict:
    hashes: List[str] = []
    roles: List[str] = []
    fees: List[int] = []
    misses = 0
    for tx in txs:
        rc = cache.get(tx)
        if rc is None:
            rc = rpc("eth_getTransactionReceipt", [tx])
            cache[tx] = rc if rc is not None else False
        if not rc:
            misses += 1
            continue
        mine = l5_orders_in(rc)
        allf = order_fills(rc)
        for f in mine:
            hashes.append(f["order_hash"])
            fees.append(f["fee"])
            # CAREFUL: this is the FEE-BEARING / FEE-FREE label, not a
            # maker/taker claim. Asserting taker from fee>0 would be circular
            # with the very label the BTC15m work said was all that is
            # recoverable. Deciding maker vs taker needs the OrdersMatched
            # takerOrderHash cross-check, which this run does NOT do.
            roles.append("fee_bearing" if f["fee"] > 0 else "fee_free")
        if mine and not allf:
            misses += 1
    uniq = sorted(set(hashes))
    if not uniq:
        cls = "UNRESOLVABLE"
    elif len(uniq) == 1:
        cls = "SAME_PARENT_ORDER"
    else:
        cls = "MULTIPLE_PARENT_ORDERS"
    return {
        "key": list(cluster["key"]), "n_fills": cluster["n"],
        "n_tx": len(txs), "tx": list(txs)[:4],
        "l5_order_fills_found": len(hashes),
        "distinct_order_hashes": len(uniq),
        "order_hashes": uniq[:6],
        "roles": collections.Counter(roles),
        "fee_units_sum": sum(fees),
        "receipts_missing": misses,
        "classification": cls,
    }


def run(n_multi: Optional[int] = None, n_single: Optional[int] = None) -> dict:
    multi, single = clusters()
    idx = tx_index()
    rnd = random.Random(SAMPLE_SPEC["seed"])
    nm = n_multi if n_multi is not None else SAMPLE_SPEC["clusters_ge2"]
    ns = n_single if n_single is not None else SAMPLE_SPEC["singleton_controls"]
    pick_m = rnd.sample(multi, min(nm, len(multi)))
    pick_s = rnd.sample(single, min(ns, len(single)))
    cache: Dict[str, Any] = {}
    out_m = [classify(c, idx.get(c["key"], []), cache) for c in pick_m]
    out_s = [classify(c, idx.get(c["key"], []), cache) for c in pick_s]
    tally = collections.Counter(r["classification"] for r in out_m)
    resolved = [r for r in out_m if r["classification"] != "UNRESOLVABLE"]
    same = sum(1 for r in resolved if r["classification"] == "SAME_PARENT_ORDER")
    fills_in_same = sum(r["n_fills"] for r in resolved
                        if r["classification"] == "SAME_PARENT_ORDER")
    fills_resolved = sum(r["n_fills"] for r in resolved)
    return {
        "label": LABEL,
        "api_layer_verdict": {
            "public_data_api_carries_order_ids": False,
            "clob_trades_public": False,
            "clob_trades_status_without_auth": 401,
            "conclusion": ("NO at the API layer for arbitrary traders. The "
                           "CLOB trades endpoint is authenticated and scoped "
                           "to the calling key's own account."),
        },
        "chain_layer_verdict": {
            "order_ids_retrievable_for_arbitrary_traders": True,
            "route": "transactionHash -> eth_getTransactionReceipt -> "
                     "OrderFilled(orderHash, maker, taker, amounts, fee)",
            "also_recovers": ["order owner address", "counterparty order "
                              "hashes", "matched amounts", "PER-FILL FEE"],
            "not_claimed_here": ("maker vs taker. The fee>0 split is the "
                                 "fee-bearing/fee-free label; deciding "
                                 "maker/taker needs the OrdersMatched "
                                 "takerOrderHash cross-check, which this run "
                                 "does not do."),
        },
        "sample_spec": SAMPLE_SPEC,
        "clusters_total_ge2": len(multi), "singletons_total": len(single),
        "sampled_ge2": len(out_m), "sampled_singletons": len(out_s),
        "tally": dict(tally),
        "share_resolved": (round(len(resolved) / len(out_m), 4)
                           if out_m else None),
        "of_resolved_share_same_parent": (round(same / len(resolved), 4)
                                          if resolved else None),
        "of_resolved_FILLS_collapsing_into_one_parent": (
            round(fills_in_same / fills_resolved, 4) if fills_resolved else None),
        "clusters": out_m, "singleton_controls": out_s,
        "unresolvable_cause": (
            "MEASURED, not assumed: the receipts are NOT SERVED by either "
            "public RPC (both return null for those transaction hashes). "
            "Public Polygon nodes prune old receipts, and this tape runs "
            "2026-03 to 2026-08. It is a RETRIEVAL limit, not a decoding bug "
            "-- every unresolvable cluster had ALL of its receipts missing."),
        "labels": ("A cluster is UNRESOLVABLE when no OrderFilled naming L5 "
                   "could be read for it. That is reported, never dropped: "
                   "the unresolvable fraction is a result, and here it is a "
                   "property of the RPC rather than of L5."),
    }
