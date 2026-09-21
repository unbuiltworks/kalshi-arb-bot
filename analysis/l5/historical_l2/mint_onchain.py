"""Did L5's maker fills actually settle by minting a complete set? ON CHAIN.

WHY A NEW TEST WAS NEEDED. The feasibility test used until now --
`p + complement_best_bid >= 1.00` -- cannot fail for the fills it was meant to
explain. On a binary book the complement's bid IS one minus this token's ask
(measured: exactly 1.0000 in 97.86% of synchronized snapshot pairs), so

    mint_feasible  <=>  p + (1 - own_ask) >= 1  <=>  p >= own_ask

which is precisely the AT_OR_ABOVE_ASK class being tested. "99.86% of
at-or-above-ask fills are mint feasible" is therefore a restatement of the class
definition, not evidence about mechanism, and it is withdrawn as support.

THE REPLACEMENT IS DIRECT, NOT INFERENTIAL. A complete-set mint is not a theory
about this venue -- it is a state transition the ConditionalTokens contract
records. Minting emits

    PositionSplit  topic0 0x2e6bb91f   at 0x4d97dcd97ec945f40cf65f87097ace5ea0476045

So the question becomes: does the transaction that carried L5's fill contain a
PositionSplit? That is observable, it can come back negative, and nothing about
the book can force its answer.

THE CONFOUND, AND HOW IT IS BOUNDED. The exchange batches: one transaction can
carry several matches, so a PositionSplit in the same transaction is not by
itself attributable to L5's fill. Presence is therefore reported BOTH over all
transactions and over SINGLE-MATCH transactions only (exactly one exchange fill
event pair), where nothing else in the transaction could have caused it. The
single-match figure is the one that carries the argument.

NOTHING HERE IDENTIFIES A COUNTERPARTY. A PositionSplit is a contract event in a
public transaction; no order or address is attributed to any named trader.
"""
from __future__ import annotations

import argparse
import collections
import json
import ssl
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence

CONDITIONAL_TOKENS = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
EXCHANGE = "0xe111180000d2663c0091e4f400237545b87b996b"
POSITION_SPLIT = "0x2e6bb91f"      # PositionSplit(address,address,bytes32,bytes32,uint256[],uint256)
POSITIONS_MERGE = "0x6f13ca62"     # PositionsMerge(...)
EXCHANGE_FILL = "0xd543adfd"       # the exchange's per-fill event
ARCHIVE = "https://polygon.drpc.org"
CTX = ssl.create_default_context(cafile="/root/.ccr/ca-bundle.crt")


def rpc(method: str, params: list, url: str = ARCHIVE, tries: int = 4) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "method": method, "params": params}).encode()
    for i in range(tries):
        try:
            r = urllib.request.Request(url, data=body,
                                       headers={"Content-Type": "application/json",
                                                "User-Agent": "l5-research/1.0"})
            with urllib.request.urlopen(r, timeout=30, context=CTX) as h:
                return (json.loads(h.read()) or {}).get("result")
        except Exception:                                     # noqa: BLE001
            if i == tries - 1:
                return None
            time.sleep(0.6 * (i + 1))
    return None


def receipt_facts(rc: Optional[dict]) -> Optional[Dict[str, Any]]:
    """What the transaction did, from its logs alone."""
    if not rc or "logs" not in rc:
        return None
    n = collections.Counter()
    for lg in rc["logs"]:
        a = (lg.get("address") or "").lower()
        t0 = (lg["topics"][0][:10] if lg.get("topics") else "-")
        n[(a, t0)] += 1
    return {"position_splits": n[(CONDITIONAL_TOKENS, POSITION_SPLIT)],
            "positions_merges": n[(CONDITIONAL_TOKENS, POSITIONS_MERGE)],
            "exchange_fills": n[(EXCHANGE, EXCHANGE_FILL)],
            "n_logs": len(rc["logs"]),
            # Transactions in one block are not independent -- the same batch,
            # the same counterparties. Kept so the rate can be bootstrapped
            # over BLOCKS rather than over transactions.
            "block": int(rc.get("blockNumber", "0x0"), 16)}


def fetch(txs: Sequence[str], workers: int = 6) -> Dict[str, dict]:
    def one(tx):
        return tx, receipt_facts(rpc("eth_getTransactionReceipt", [tx]))
    with ThreadPoolExecutor(workers) as ex:
        return {t: f for t, f in ex.map(one, txs) if f}


def compare(by_class: Dict[str, List[str]], facts: Dict[str, dict],
            single_match_fills: int = 2) -> Dict[str, Any]:
    """PositionSplit presence per class, all transactions and single-match only."""
    out: Dict[str, Any] = {}
    for cls, txs in by_class.items():
        got = [facts[t] for t in txs if t in facts]
        single = [f for f in got if f["exchange_fills"] == single_match_fills]
        def rate(rows):
            if not rows:
                return {"n": 0, "with_split": 0, "share": None}
            k = sum(1 for f in rows if f["position_splits"] > 0)
            return {"n": len(rows), "with_split": k, "share": round(k / len(rows), 4)}
        out[cls] = {"all_transactions": rate(got),
                    "single_match_only": rate(single),
                    "merges_present": sum(1 for f in got if f["positions_merges"] > 0)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", required=True,
                    help="JSON {tx: class} for L5 maker fills")
    ap.add_argument("--per-class", type=int, default=220)
    ap.add_argument("--out",
                    default="analysis/l5/historical_l2/mint_onchain.json")
    a = ap.parse_args(argv)

    cls_of: Dict[str, str] = json.load(open(a.classes))
    by: Dict[str, List[str]] = collections.defaultdict(list)
    seen = set()
    for tx, c in cls_of.items():
        if tx in seen:
            continue
        seen.add(tx)
        by[c].append(tx)
    by = {c: v[:a.per_class] for c, v in by.items() if len(v) >= 10}
    facts = fetch([t for v in by.values() for t in v])
    rep = {"classes": {c: len(v) for c, v in by.items()},
           "receipts_fetched": len(facts),
           "comparison": compare(by, facts),
           "labels": ("PositionSplit is the ConditionalTokens complete-set mint "
                      "event. single_match_only restricts to transactions carrying "
                      "exactly one exchange fill pair, where no other match in the "
                      "same transaction could have produced the split. No order or "
                      "address is attributed to any named trader.")}
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1)
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
