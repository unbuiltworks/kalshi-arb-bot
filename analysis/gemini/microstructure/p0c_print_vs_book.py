"""P0c: where do executions land relative to the DISPLAYED top of book?

This is the diagnostic that decides whether "the displayed book understates
the market". For every in-window print we take the last BBO strictly before
it and classify the print price:

  AT_BID / AT_ASK        -- print matched a displayed touch price
  INSIDE_SPREAD          -- print happened at a price BETTER than both
                            displayed touches: liquidity existed that the
                            displayed book never showed
  OUTSIDE (through)      -- print beyond a touch: a sweep into depth
  NO_BOOK                -- no two-sided quote on record before the print

If INSIDE_SPREAD dominates, a maker resting at the displayed touch is
structurally not the counterparty, and the displayed book is not the market.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

MARKET_RE = re.compile(r"^GEMI-((?:BTC|ETH)(?:05|15)M\d{10})-(UP|DOWN)$")
BARE_RE = re.compile(r"^((?:BTC|ETH)(?:05|15)M\d{10})$")


def key_of(row):
    m = row.get("market") or ""
    h = MARKET_RE.match(m) or BARE_RE.match(m)
    return h.group(1) if h else None


def main(tape_dir="data/policy_d_port", asset="BTC", dur="05M", min_cov=240.0):
    dur_s = 300.0 if dur == "05M" else 900.0
    expiry, prints, bbo = {}, defaultdict(list), defaultdict(list)
    for p in sorted(Path(tape_dir).glob("tape_*.jsonl")):
        for line in open(p):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("venue") != "gemini":
                continue
            k = key_of(r)
            if not k or not k.startswith(asset) or k[3:6] != dur:
                continue
            kind = r.get("kind")
            if kind == "vol":
                e = r.get("expiry")
                if e and k not in expiry:
                    expiry[k] = datetime.fromisoformat(
                        e.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()
            elif kind == "print":
                try:
                    prints[k].append((r["t"], float(r["qty"]), float(r["price"]),
                                      r.get("buyer_is_maker"), r.get("trade_id")))
                except Exception:
                    pass
            elif kind == "bbo":
                b, a = r.get("bids") or [], r.get("asks") or []
                bbo[k].append((r["t"],
                               float(b[0][0]) if b else None, float(b[0][1]) if b else None,
                               float(a[0][0]) if a else None, float(a[0][1]) if a else None))

    cls = Counter()
    qty_cls = Counter()
    passive_at_touch = Counter()
    staleness = []
    rows = []
    for k, exp in expiry.items():
        start = exp - dur_s
        ob = sorted([o for o in bbo.get(k, []) if start <= o[0] <= exp])
        if len(ob) < 2 or (ob[-1][0] - ob[0][0]) < min_cov:
            continue
        seen = set()
        pr = []
        for t, qty, px, bim, tid in prints.get(k, []):
            if not (start <= t <= exp):
                continue
            if tid is not None and tid in seen:
                continue
            if tid is not None:
                seen.add(tid)
            pr.append((t, qty, px, bim))
        pr.sort()
        for t, qty, px, bim in pr:
            prev = None
            for o in ob:
                if o[0] < t:
                    prev = o
                else:
                    break
            if prev is None:
                cls["NO_PRIOR_BBO"] += 1
                qty_cls["NO_PRIOR_BBO"] += qty
                continue
            _, bid, bsz, ask, asz = prev
            staleness.append(t - prev[0])
            if bid is None or ask is None:
                c = "NO_TWO_SIDED_BOOK"
            elif px == bid:
                c = "AT_BID"
            elif px == ask:
                c = "AT_ASK"
            elif bid < px < ask:
                c = "INSIDE_SPREAD"
            elif px < bid:
                c = "BELOW_BID"
            else:
                c = "ABOVE_ASK"
            cls[c] += 1
            qty_cls[c] += qty
            # did the print land on the touch of the side that was passive?
            if bim is True:
                passive_at_touch["buy_passive_at_bid" if px == bid else "buy_passive_off_bid"] += 1
            elif bim is False:
                passive_at_touch["sell_passive_at_ask" if px == ask else "sell_passive_off_ask"] += 1
            rows.append({"window": k, "t": t, "qty": qty, "price": px,
                         "buyer_is_maker": bim, "bid": bid, "ask": ask,
                         "bid_sz": bsz, "ask_sz": asz, "class": c,
                         "book_age_s": round(t - prev[0], 2)})

    out = Path(__file__).with_name(f"p0c_print_vs_book_{asset}{dur}.json")
    out.write_text(json.dumps(rows, indent=1))

    tot = sum(cls.values())
    totq = sum(qty_cls.values())
    print(f"in-window prints classified: {tot}  ({round(totq,2)} contracts)")
    print()
    print(f"{'class':20s} {'prints':>7s} {'share':>8s} {'contracts':>11s} {'qty_share':>10s}")
    print("-" * 60)
    for c, n in cls.most_common():
        print(f"{c:20s} {n:7d} {n/tot*100:7.1f}% {qty_cls[c]:11.2f} "
              f"{qty_cls[c]/totq*100:9.1f}%")
    print()
    print("PASSIVE-SIDE TOUCH TEST (could a maker at the displayed touch have been us?)")
    for k2, v in passive_at_touch.most_common():
        print(f"  {k2:28s} {v}")
    print()
    if staleness:
        staleness.sort()
        print(f"book_age before print: p50={staleness[len(staleness)//2]:.2f}s "
              f"max={staleness[-1]:.2f}s  (collector sampled at ~5 s)")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*(sys.argv[1:4] or ["data/policy_d_port", "BTC", "05M"])))
