#!/usr/bin/env python3
"""Convert a saved Railway log dump from `pmus-ws-evidence-recorder` into a
PMUS trade/book tape.

The recorder is the EXISTING authenticated service in project
arb-ev-wallet-shadow / production (service pmus-ws-evidence-recorder). Nothing
was built, deployed or restarted to obtain this: the service was already
running and already emitting PMUS_TRADE_ROWS and PMUS_BOOK_ROWS.

It emits two row types, and the trade row is the reason the acceptance chain
closes here where the public REST surface failed:

  trade: t, px, qty, trade_time, bid_at_trade, ask_at_trade, dir
         -> `dir` is the TAKER direction, so the consumed side is READ.
         -> bid_at_trade/ask_at_trade is the authenticated top of book AT THE
            INSTANT OF THE TRADE, bound by the recorder itself, so the
            trade->preceding-book synchronization needs no merge and carries no
            clock-skew risk.
  book:  t, bid, ask, bid_qty, ask_qty, status
         -> bid_qty/ask_qty is displayed depth AT THAT STATE: the queue-ahead
            for a maker resting AT the touch. There is NO depth beyond the
            touch, exactly as on the Gemini canonical tape, so a quote behind
            the touch has UNKNOWN queue and must not fill.

Dedupe is by (slug, t, px/bid, qty/ask) so repeated log pulls cannot double
count. Nothing is interpolated or backfilled.
"""
from __future__ import annotations
import json, os, sys, glob

OUT_DIR = "data/pmus_ws_tape"


def parse(paths):
    trades, books = {}, {}
    for p in paths:
        d = json.load(open(p))
        for r in d.get("deploy") or []:
            m = r.get("message", "")
            for kind, tag, sink in (("authenticated_trade_rows", "PMUS_TRADE_ROWS", trades),
                                    ("authenticated_book_rows", "PMUS_BOOK_ROWS", books)):
                if f'"kind":"{kind}"' not in m:
                    continue
                try:
                    o = json.loads(m.split(tag, 1)[1].strip())
                except Exception:
                    continue
                for slug, arr in (o.get("markets") or {}).items():
                    for x in arr:
                        k = (slug, x["t"], str(x.get("px") or x.get("bid")),
                             str(x.get("qty") or x.get("ask")))
                        x = dict(x); x["slug"] = slug
                        sink[k] = x
    return trades, books


def main(paths):
    tr, bk = parse(paths)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "pmus_btc15m_auth_tape.jsonl")
    seen = set()
    if os.path.exists(out):
        for line in open(out):
            r = json.loads(line)
            seen.add((r["kind"], r["slug"], r["t"],
                      str(r.get("px") or r.get("bid")), str(r.get("qty") or r.get("ask"))))
    n = 0
    with open(out, "a") as fh:
        for kind, sink in (("trade", tr), ("book", bk)):
            for k, x in sorted(sink.items(), key=lambda kv: kv[1]["t"]):
                key = (kind, x["slug"], x["t"], str(x.get("px") or x.get("bid")),
                       str(x.get("qty") or x.get("ask")))
                if key in seen:
                    continue
                seen.add(key)
                x["kind"] = kind
                fh.write(json.dumps(x) + "\n")
                n += 1
    slugs = sorted({x["slug"] for x in list(tr.values()) + list(bk.values())})
    print(f"trades {len(tr)}  books {len(bk)}  new rows appended {n}")
    print(f"slugs: {slugs}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    ps = sys.argv[1:] or glob.glob(
        "/root/.claude/projects/*/*/tool-results/mcp-Railway-get-logs-*.txt")
    sys.exit(main(ps))
