"""P9-F: can this venue support the strategy at all -- quote both sides, get hit?

    python3 analysis/gemini/p9f_two_sided_quote.py analysis/gemini/p7_bulk_tape.json

WHY THIS MEASUREMENT AND NOT ANOTHER. P9 showed that a pair assembled from two
prints at different times earns the PRICE CHANGE between them, not a spread,
and that a completion ceiling selects only the favourable half of that change.
The repeatable version of the same trade is to rest a bid and an ask AT THE
SAME TIME and be hit on both: then the margin is the spread we quoted, by
construction, with no view on direction.

So this measures exactly that, and nothing else:

    quote a YES bid at m - s/2 and a YES ask at m + s/2 around the last traded
    price m, leave both resting for the rest of the contract, and see what the
    tape does.

      both legs hit   -> a matched pair at cost 1 - s, margin s, outcome-free
      one leg hit     -> an unpaired leg carried to settlement, paid in full
      neither         -> nothing

A MAKER FILL PRINTS AT THE RESTING PRICE, so a sell aggressor printing at or
below our bid fills the bid, and a buy aggressor printing at or above our ask
fills the ask. That is the AT_LEVEL evidence tier calibrated earlier against
56 confirmed maker fills; `capture` thins it for queue position.

Settlement is the venue's own `resolutionSide`. No rebate term, no fee
assumption beyond `fee_per_fill`, and no probability model anywhere.
"""
import os
import sys
from collections import defaultdict
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import p9a_targets as A                                        # noqa: E402

SPREADS = (F(2, 100), F(4, 100), F(6, 100), F(10, 100), F(20, 100))


def quote_once(contract, i, s, *, capture=F(1)):
    """Quote around print `i` and report what the rest of the tape does to it."""
    prints = sorted(contract.get("prints") or [],
                    key=lambda r: (int(r[0]), str(r[1])))
    if i >= len(prints) - 1:
        return None
    m = F(str(prints[i][2]))
    half = s / 2
    bid = m - half
    ask = m + half
    if bid <= 0 or ask >= 1:
        return None
    need = int(1 / float(capture)) if float(capture) > 0 else 1
    seen_b = seen_a = 0
    t_bid = t_ask = None
    for t, side, px_s, _q in prints[i + 1:]:
        px = F(px_s)
        if side == "sell" and px <= bid and t_bid is None:
            seen_b += 1
            if seen_b % need == 0:
                t_bid = int(t)
        elif side == "buy" and px >= ask and t_ask is None:
            seen_a += 1
            if seen_a % need == 0:
                t_ask = int(t)
        if t_bid is not None and t_ask is not None:
            break
    return {"bid": bid, "ask": ask, "yes_filled": t_bid is not None,
            "no_filled": t_ask is not None,
            "t_bid": t_bid, "t_ask": t_ask}


def settle(contract, q, s, *, fee=F(0)):
    res = str(contract.get("resolution") or "").strip().lower()
    won = "YES" if res in ("yes", "up", "true") else (
        "NO" if res in ("no", "down", "false") else None)
    if won is None:
        return None
    y, n = q["yes_filled"], q["no_filled"]
    if y and n:
        # cost bid + (1 - ask) = 1 - s; pays exactly 1
        return {"kind": "pair", "pnl": s - 2 * fee, "spend": F(1) - s}
    if y:
        cost = q["bid"]
        return {"kind": "yes_only",
                "pnl": (F(1) if won == "YES" else F(0)) - cost - fee,
                "spend": cost}
    if n:
        cost = F(1) - q["ask"]
        return {"kind": "no_only",
                "pnl": (F(1) if won == "NO" else F(0)) - cost - fee,
                "spend": cost}
    return {"kind": "none", "pnl": F(0), "spend": F(0)}


def main(path):
    rows, rep = A.DS.load([path])
    rep.pop("quarantined_detail", None)
    print(rep, flush=True)
    by = defaultdict(list)
    for c in rows:
        if c["duration"] in ("5m", "15m") and c.get("prints"):
            by[c["duration"]].append(c)

    print(f"\n{'='*126}\nP9-F  ONE two-sided quote per contract, placed at its "
          f"FIRST print and left resting\n{'='*126}")
    print(f"{'lane':>5} {'split':>5} {'cap':>5} {'spread':>7} {'quotes':>7} "
          f"{'both':>7} {'yes only':>9} {'no only':>9} {'neither':>8} "
          f"{'pair $':>8} {'orphan $':>9} {'NET $':>8} {'per quote':>10}")
    for lane in ("5m", "15m"):
        for split in ("dev", "val"):
            for cap in (F(1), F(1, 3)):
                for s in SPREADS:
                    agg = defaultdict(int)
                    pair_pnl = orphan_pnl = F(0)
                    n = 0
                    for c in by[lane]:
                        if A.split_of(c["day"]) != split:
                            continue
                        q = quote_once(c, 0, s, capture=cap)
                        if q is None:
                            continue
                        r = settle(c, q, s)
                        if r is None:
                            continue
                        n += 1
                        agg[r["kind"]] += 1
                        if r["kind"] == "pair":
                            pair_pnl += r["pnl"]
                        else:
                            orphan_pnl += r["pnl"]
                    if not n:
                        continue
                    net = pair_pnl + orphan_pnl
                    print(f"{lane:>5} {split:>5} {float(cap):>5.2f} "
                          f"{float(s):>7.2f} {n:>7} {agg['pair']:>7} "
                          f"{agg['yes_only']:>9} {agg['no_only']:>9} "
                          f"{agg['none']:>8} {float(pair_pnl):>8.2f} "
                          f"{float(orphan_pnl):>9.2f} {float(net):>8.2f} "
                          f"{float(net) / n:>10.4f}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
