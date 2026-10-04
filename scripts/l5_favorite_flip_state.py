"""Pre-action market state for every L5 BTC15m transaction. Read-only.

Builds one row per L5 TRANSACTION (not per leg -- see
`analysis/l5/next_side/features.aggregate_transactions`) carrying the
exogenous market state as of the last counterparty print STRICTLY BEFORE the
action, plus L5's inventory before the action.

The price series is exogenous by construction, and the filter that achieves
that is NOT the wallet. Measured on the first 13 fetched windows, all 133 of
L5's own transactions appear in the tape and ZERO of them carry his
`proxyWallet`: `data-api` attributes each print to the TAKER, and L5 is a
maker in ~88% of his fills, so a wallet filter would have removed almost
nothing while leaving every print he participated in. The series therefore
drops prints by **transaction hash** against his own canonical sequence, and
the wallet filter is kept only as a belt-and-braces second pass.
`p_up_all` keeps the contaminated series beside it so the difference in the
favorite it implies can be measured rather than assumed small.

Same-second prints are EXCLUDED. The tape's timestamps are 1-second, so a
print sharing L5's second cannot be ordered against his action, and including
it risks reading his own market impact back in as his input.

Momentum is emitted as null unless a genuine exogenous print exists inside
the lookback. A carried-forward price is not movement.
"""
from __future__ import annotations
import argparse, bisect, collections, csv, gzip, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.l5.next_side import features as F                   # noqa: E402

L5 = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
SEQ = "analysis/l5/l5_canonical_sequence.csv.gz"
TAPE = "data/l5/market_tape"
HORIZONS = (1, 5, 15, 30)


def load_l5() -> dict:
    """BTC15m legs, grouped by window, chronological."""
    by = collections.defaultdict(list)
    with gzip.open(SEQ, "rt") as f:
        for row in csv.DictReader(f):
            if row["asset"] != "BTC" or row["duration_minutes"] != "15":
                continue
            by[row["market_slug"]].append(row)
    for slug in by:
        by[slug].sort(key=lambda r: (int(r["timestamp"]),
                                     int(r["trade_seq"])))
    return by


def p_up_of(rec: dict) -> float:
    px = float(rec["price"])
    return px if str(rec.get("outcome")) == "Up" else 1.0 - px


def load_tape(slug: str, l5_txs: frozenset = frozenset()):
    """(ts[], p_up[]) for counterparty prints, and the same for all prints."""
    p = os.path.join(TAPE, "%s.jsonl.gz" % slug)
    if not os.path.exists(p):
        return None
    ex_t, ex_p, all_t, all_p = [], [], [], []
    rows = []
    with gzip.open(p, "rt") as f:
        for line in f:
            r = json.loads(line)
            if r.get("kind") == "provenance":
                continue
            rows.append(r)
    rows.sort(key=lambda r: int(r["timestamp"]))
    for r in rows:
        t, pu = int(r["timestamp"]), p_up_of(r)
        all_t.append(t); all_p.append(pu)
        if (r.get("transactionHash") not in l5_txs
                and str(r.get("proxyWallet", "")).lower() != L5):
            ex_t.append(t); ex_p.append(pu)
    return {"ex_t": ex_t, "ex_p": ex_p, "all_t": all_t, "all_p": all_p,
            "n_rows": len(rows), "n_dropped_l5": len(all_t) - len(ex_t)}


def asof(ts, ps, t):
    """Last value strictly before t, and its index. Same-second excluded."""
    i = bisect.bisect_left(ts, t) - 1
    return (None, None) if i < 0 else (ps[i], i)


def favorite(p_up, tau):
    if p_up is None:
        return None
    return "Up" if p_up > tau else ("Down" if p_up < 1.0 - tau else None)


def flip_times(ts, ps, tau):
    """Times at which the exogenous favorite changed, in order."""
    out, cur = [], None
    for t, p in zip(ts, ps):
        f = favorite(p, tau)
        if f is None:
            continue
        if cur is not None and f != cur:
            out.append(t)
        cur = f
    return out


def build(by_l5: dict, tau: float, out_path: str) -> dict:
    stats = collections.Counter()
    rows_out = []
    for slug, legs in sorted(by_l5.items(),
                             key=lambda kv: int(kv[1][0]["timestamp"])):
        tape = load_tape(slug, frozenset(l["transaction_hash"]
                                         for l in legs))
        if tape is None:
            stats["windows_no_tape"] += 1
            continue
        stats["windows_with_tape"] += 1
        stats["prints_total"] += tape["n_rows"]
        stats["prints_dropped_l5"] += tape["n_dropped_l5"]
        # `aggregate_transactions` speaks the raw activity feed's field
        # names; the canonical CSV uses its own. Mapped here rather than
        # forked, so there stays exactly one transaction aggregator.
        txs = F.aggregate_transactions([
            {"transactionHash": l["transaction_hash"], "outcome": l["outcome"],
             "timestamp": int(l["timestamp"]), "size": l["quantity"],
             "usdcSize": l["dollars"]} for l in legs])
        ex_t, ex_p = tape["ex_t"], tape["ex_p"]
        flips = flip_times(ex_t, ex_p, tau)
        up_q = dn_q = 0.0
        prev_side = None
        prev_fav = None
        consec_n = 0
        consec_q = 0.0
        for k, tx in enumerate(txs):
            t = int(tx["timestamp"])
            side = tx["outcome"]                     # "Up" / "Down"
            qty = float(tx["size"])
            pux, _ = asof(ex_t, ex_p, t)
            pall, _ = asof(tape["all_t"], tape["all_p"], t)
            fav = favorite(pux, tau)
            mom = {}
            for h in HORIZONS:
                base, _ = asof(ex_t, ex_p, t - h)
                has = bisect.bisect_left(ex_t, t) - bisect.bisect_left(
                    ex_t, t - h) > 0
                mom["mom_%ds" % h] = (None if (base is None or pux is None
                                               or not has) else pux - base)
            prior = [x for x in flips if x < t]
            last_flip = prior[-1] if prior else None
            signed = up_q - dn_q
            before_abs = abs(signed)
            after = signed + (qty if side == "Up" else -qty)
            rows_out.append({
                "slug": slug, "t": t, "tx_i": k, "side": side, "qty": qty,
                "price": float(tx["price"]),
                "p_up_ex": pux, "p_up_all": pall,
                "favorite": fav,
                "fav_px": (None if pux is None or fav is None
                           else (pux if fav == "Up" else 1.0 - pux)),
                "dist_from_50": (None if pux is None else abs(pux - 0.5)),
                "flip_since_prev_action": (None if (fav is None
                                                    or prev_fav is None)
                                           else fav != prev_fav),
                "last_flip_t": last_flip,
                "flip_latency_s": (None if last_flip is None
                                   else t - last_flip),
                "bought_favorite": (None if fav is None else side == fav),
                "extended_residual": abs(after) > before_abs,
                "consec_fav_buys": consec_n, "consec_fav_qty": consec_q,
                "up_qty": up_q, "dn_qty": dn_q,
                "signed_imbalance": signed,
                "abs_imbalance": before_abs,
                "prev_side": prev_side,
                **mom,
            })
            # consecutive purchases OF THE CURRENT FAVORITE, as of before
            if fav is not None and side == fav:
                consec_n += 1; consec_q += qty
            else:
                consec_n = 0; consec_q = 0.0
            if side == "Up":
                up_q += qty
            else:
                dn_q += qty
            prev_side = side
            if fav is not None:
                prev_fav = fav
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with gzip.open(out_path, "wt") as f:
        f.write(json.dumps({"kind": "provenance", "tau": tau, "l5": L5,
                            "seq": SEQ, "tape": TAPE,
                            "unit": "transaction",
                            "stats": dict(stats),
                            "rows": len(rows_out)}) + "\n")
        for r in rows_out:
            f.write(json.dumps(r) + "\n")
    stats["rows"] = len(rows_out)
    return dict(stats)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tau", type=float, default=0.50)
    ap.add_argument("--out", default="analysis/l5/favorite_flip/state.jsonl.gz")
    a = ap.parse_args()
    s = build(load_l5(), a.tau, a.out)
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
