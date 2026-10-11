"""L5's maker/taker role, established from chain evidence. P0.

NOT INFERRED FROM FEES, and not taken from the venue's own flag either -- that
flag is absent on 38 % of BTC15m complements, which is exactly the population
the resting-order question turns on. This reads the role out of the Polygon
Exchange's `OrderFilled` log:

    OrderFilled(bytes32 indexed orderHash, address indexed maker,
                address indexed taker, ...)
    topic1 = orderHash      topic2 = MAKER      topic3 = TAKER

VALIDATED, AND IT HAD TO BE. An earlier version of the decoder mapped these
backwards from a single transaction. Checked against the venue's own published
maker_taker over 70 BTC15m fills, the corrected mapping agrees 69/70 (98.6 %);
the inverted one agreed 1/60. The single disagreement is a transaction whose
legs share a price and size, where matching a feed row to a leg is ambiguous --
recorded, not hidden.

THE QUESTION THIS ANSWERS. If BTC15m first/complement sequences are
overwhelmingly maker -> maker, then both legs were hit rather than taken, both
orders were resting when their counterparties arrived, and the
continuous-two-sided-maker hypothesis is substantially elevated. If the
complement is routinely a taker fill, he is reaching out for it and a reactive
clone is imitating the right thing.

A REGIME BOUNDARY, NOT A BUG. BTC15m fills before ~2026-08 route through a
different contract (`to` is 0xe3f18acc…, not the CTF Exchange) and emit no
`OrderFilled` at all -- only USDC transfers, ERC1155 TransferSingle and that
contract's own events. Maker/taker is therefore ESTABLISHABLE ONLY FOR THE
OrderFilled-era fills, and the March/April windows are reported as unknown
rather than guessed at. This is the "month/fee regime" split: two different
mechanisms, and only one of them exposes the role.

Every row keeps its transaction hash, block and log index.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.l5_chain_observer import decode as DE
from src.l5_chain_observer import observer as O

PX_TOL = D("0.005")          # the feed rounds; the chain does not
SZ_TOL = D("0.01")
RPC_PACE_S = 0.2
# ARCHIVE FIRST, AND THE ORDER MATTERS. publicnode prunes: it serves receipts
# from ~2026-08-28 and returns NULL for anything older, which would have
# silently capped this study at 59% of BTC15m windows and looked like "no
# decodable leg" rather than "this node does not have it". drpc serves the full
# history. A NULL receipt is never read as an absent fill.
ARCHIVE_RPCS = ("https://polygon.drpc.org",
                "https://polygon-bor-rpc.publicnode.com")


class Cache:
    """Receipts are immutable, so a fetched one is fetched forever."""

    def __init__(self, path: str) -> None:
        self.path, self.d = path, {}
        if path and os.path.exists(path):
            with open(path) as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        self.d[r["tx"]] = r["legs"]
                    except (ValueError, KeyError):
                        continue

    def get(self, tx: str) -> Optional[List[dict]]:
        return self.d.get(tx)

    def put(self, tx: str, legs: List[dict]) -> None:
        self.d[tx] = legs
        if self.path:
            with open(self.path, "a") as fh:
                fh.write(json.dumps({"tx": tx, "legs": legs}) + "\n")


def role_of(fill: dict, cache: Cache, *, pace: float = RPC_PACE_S
            ) -> Dict[str, Any]:
    """One fill -> its chain-attributed role, with provenance.

    UNKNOWN is a real answer here and is never replaced by the feed's flag: the
    point is an INDEPENDENT source, and silently falling back would make the
    two agree by construction.
    """
    tx = fill.get("transaction_hash")
    out = {"tx": tx, "feed_flag": fill.get("maker_taker"),
           "price": fill.get("price"), "qty": fill.get("quantity")}
    if not tx:
        return {**out, "role": None, "why": "the feed published no transaction"}
    legs = cache.get(tx)
    if legs is None:
        rc, served_by, err = None, None, None
        for url in ARCHIVE_RPCS:
            try:
                rc = O.rpc("eth_getTransactionReceipt", [tx], url)
            except O.RpcError as e:
                err = str(e)[:70]
                continue
            if rc:
                served_by = url
                break
        if pace:
            time.sleep(pace)
        if rc is None:
            return {**out, "role": None,
                    "why": (f"no node served this receipt (pruned or "
                            f"unreachable): {err or 'null result'}")}
        legs = DE.decode_receipt(rc, l5_topic=O.PAD, exchanges=O.EXCHANGES)
        for l in legs:
            l["served_by"] = served_by
        cache.put(tx, legs)
    if not legs:
        return {**out, "role": None,
                "why": "no decodable L5 OrderFilled leg in this transaction"}
    m = _match(legs, fill)
    if m is None:
        return {**out, "role": None, "n_legs": len(legs),
                "why": ("no leg matches this fill's price and size: the "
                        "transaction carries other fills and this one cannot "
                        "be attributed without guessing")}
    return {**out, "role": m.get("l5_role"), "topic_pos": m.get("l5_topic_pos"),
            "block": m.get("block"), "log_index": m.get("log_index"),
            "contract": m.get("contract"), "chain_price": m.get("price"),
            "chain_size": m.get("size"), "n_legs": len(legs),
            "served_by": m.get("served_by"),
            "ambiguous": _ambiguous(legs, fill)}


def _match(legs: Sequence[dict], fill: dict) -> Optional[dict]:
    try:
        px, sz = D(str(fill["price"])), D(str(fill["quantity"]))
    except Exception:                                         # noqa: BLE001
        return None
    c = [l for l in legs
         if abs(D(l["price"]) - px) <= PX_TOL and abs(D(l["size"]) - sz) <= SZ_TOL]
    return c[0] if c else None


def _ambiguous(legs: Sequence[dict], fill: dict) -> bool:
    """Several legs fit this price and size, with different roles."""
    try:
        px, sz = D(str(fill["price"])), D(str(fill["quantity"]))
    except Exception:                                         # noqa: BLE001
        return False
    c = [l for l in legs
         if abs(D(l["price"]) - px) <= PX_TOL and abs(D(l["size"]) - sz) <= SZ_TOL]
    return len({l.get("l5_role") for l in c}) > 1


def sequence_roles(windows: Dict[str, List[dict]], cache: Cache, *,
                   max_windows: Optional[int] = None,
                   pace: float = RPC_PACE_S) -> List[Dict[str, Any]]:
    """First fill and first opposite fill of each window, both attributed."""
    out = []
    for wid, fills in list(windows.items())[:max_windows]:
        if not fills:
            continue
        first = fills[0]
        comp = next((f for f in fills[1:]
                     if f["outcome"] != first["outcome"]), None)
        if comp is None:
            continue
        a = role_of(first, cache, pace=pace)
        b = role_of(comp, cache, pace=pace)
        out.append({
            "window_id": wid, "market_slug": first["market_slug"],
            "month": str(first.get("datetime_utc") or "")[:7],
            "cohort": f"{first['asset']}{first['duration_minutes']}m",
            "gap_s": round(float(comp["timestamp"])
                           - float(first["timestamp"]), 1),
            "first": a, "complement": b,
            "sequence": f"{a.get('role') or 'unknown'}"
                        f"->{b.get('role') or 'unknown'}",
        })
    return out


def summarise(rows: Sequence[dict]) -> Dict[str, Any]:
    seq = Counter(r["sequence"] for r in rows)
    known = [r for r in rows
             if r["first"].get("role") and r["complement"].get("role")]
    mm = sum(1 for r in known if r["sequence"] == "maker->maker")
    first_r = Counter(r["first"].get("role") or "unknown" for r in rows)
    comp_r = Counter(r["complement"].get("role") or "unknown" for r in rows)
    agree = [r for r in rows
             for side in ("first", "complement")
             if r[side].get("role") and r[side].get("feed_flag")
             in ("maker", "taker")]
    ok = sum(1 for r in rows for side in ("first", "complement")
             if r[side].get("role")
             and r[side].get("role") == r[side].get("feed_flag"))
    return {
        "sequences": len(rows), "both_roles_known": len(known),
        "sequence_counts": dict(sorted(seq.items())),
        "maker_to_maker": mm,
        "maker_to_maker_rate_of_known": (round(mm / len(known), 4)
                                         if known else None),
        "first_fill_role": dict(sorted(first_r.items())),
        "complement_role": dict(sorted(comp_r.items())),
        "ambiguous_attributions": sum(
            1 for r in rows for s in ("first", "complement")
            if r[s].get("ambiguous")),
        "vs_feed_flag": {
            "comparable": len(agree), "agree": ok,
            "rate": (round(ok / len(agree), 4) if agree else None),
            "why": ("an INDEPENDENT check. The chain role is never replaced by "
                    "the feed's flag, or the two would agree by construction")},
        "by_month": _split(rows, lambda r: r["month"]),
        "regime_note": ("months showing only unknown->unknown predate the CTF "
                        "Exchange OrderFilled era: those fills route through a "
                        "different contract that emits no role information at "
                        "all. Not a decode failure, and not evidence about L5"),
        "by_cohort": _split(rows, lambda r: r["cohort"]),
        "reading": _reading(mm, len(known)),
    }


def _split(rows: Sequence[dict], key) -> Dict[str, Any]:
    g: Dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        g[key(r)][r["sequence"]] += 1
    return {k: dict(sorted(v.items())) for k, v in sorted(g.items())}


def _reading(mm: int, n: int) -> str:
    if n < 20:
        return f"only {n} fully attributed sequences; too few to read"
    rate = mm / n
    if rate >= 0.8:
        return (f"maker->maker on {rate:.1%} of attributed sequences: BOTH legs "
                f"were HIT rather than taken, so both orders were resting when "
                f"their counterparties arrived. This substantially ELEVATES "
                f"the continuous two-sided resting-order hypothesis -- though "
                f"it still does not show WHEN they were placed")
    if rate <= 0.4:
        return (f"maker->maker on only {rate:.1%}: the complement is routinely "
                f"reached rather than rested into, and a reactive clone is "
                f"imitating the right thing")
    return (f"maker->maker on {rate:.1%}: mixed, and it must not be read as "
            f"either")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--dur", default="15")
    ap.add_argument("--max-windows", type=int, default=150)
    ap.add_argument("--cache", default="analysis/l5/pair_formation/receipts.jsonl")
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    from analysis.l5.pair_formation import btc15m as B
    ws = B.load(asset=a.asset, dur=a.dur)
    rows = sequence_roles(ws, Cache(a.cache), max_windows=a.max_windows)
    rep = {"cohort": f"{a.asset}{a.dur}m", "windows_available": len(ws),
           **summarise(rows), "rows": rows,
           "labels": ("chain-attributed from OrderFilled topic position, "
                      "validated against the venue's own flag. Never inferred "
                      "from fees. Every row keeps tx, block and log index.")}
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(rep, fh, indent=1, default=str)
    rep.pop("rows", None)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
