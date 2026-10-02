#!/usr/bin/env python3
"""Adapt the canonical Gemini 5m tape into the recorder's merged-tape schema,
so scripts/gemini_twosided_sim.py replays it UNCHANGED under the same rules.

Source: analysis/l5/gemini_trajectory/gemini_5m_canonical.jsonl.gz
  181,384 `bbo` rows  -- bid, ask, bid_sz, ask_sz  (touch size, no depth)
    735   `print` rows -- price, qty, trade_id, buyer_is_maker
  756 markets, 2026-08-30T23:18Z -> 2026-09-01T12:16Z

WHY THIS QUALIFIES, and exactly where it does not.

The fill rule is NOT relaxed to make this usable. It qualifies because the
tape independently supplies both things the rule needs:

  queue-ahead   bid_sz / ask_sz is the resting size AT the touch, which is
                precisely the queue a maker quoting AT the touch joins;
  aggressor     `buyer_is_maker` is the venue's own flag, so the consumed
                side is READ, not inferred. buyer_is_maker=True means the
                buyer rested and the SELLER crossed, so the BID was consumed;
                False means the ASK was consumed. No quote-rule guessing.

WHERE IT CANNOT GO, stated as a hard limit rather than papered over:

  * There is NO depth beyond the touch. A quote placed behind the touch sits
    at a price whose resting size is unobserved, so its queue-ahead is
    UNKNOWN. This adapter therefore publishes only the two touch levels, and
    when the simulator's inventory skew steps a quote off the touch it lands
    on an empty level and cannot fill. That under-counts fills; it never
    invents one.
  * 24.3% of bbo rows are missing one or both touch sizes. A side with a
    price but no size is REMOVED from the published book rather than carried
    forward, so the simulator sees no touch there and does not quote. A stale
    size would be exactly the free-queue-priority error the rule exists to
    prevent.
"""
from __future__ import annotations
import gzip, json, os, sys, collections
from datetime import datetime, timezone

SRC = "analysis/l5/gemini_trajectory/gemini_5m_canonical.jsonl.gz"
OUT = "data/gemini_shadow/merged/canonical_5m_tape.jsonl"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def main(min_prints=1):
    rows = []
    for line in gzip.open(SRC, "rt"):
        if line.strip():
            rows.append(json.loads(line))
    rows.sort(key=lambda r: r["t"])

    # keep only markets that actually printed, so the output is replayable
    printed = collections.Counter(r["market"] for r in rows if r["kind"] == "print")
    keep = {m for m, n in printed.items() if n >= min_prints}

    meta = {}
    for r in rows:
        m = r["market"]
        if m in keep and m not in meta and r.get("expiry_t"):
            meta[m] = {"event_expiry": iso(r["expiry_t"]),
                       "ticker": m, "source": "gemini_5m_canonical"}

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    state = {}                       # market -> (bid, ask) currently published
    n_frames = n_trades = n_dropped = 0
    with open(OUT, "w") as fh:
        fh.write(json.dumps({"kind": "header", "started_utc": iso(rows[0]["t"]),
                             "source": SRC, "read_only": True,
                             "limitation": "touch-only book; no depth"}) + "\n")
        fh.write(json.dumps({"kind": "symbols", "t": iso(rows[0]["t"]),
                             "symbols": sorted(keep), "meta": meta}) + "\n")
        for r in rows:
            m = r["market"]
            if m not in keep:
                continue
            if r["kind"] == "bbo":
                pb, pa = state.get(m, (None, None))
                nb = r.get("bid") if r.get("bid_sz") is not None else None
                na = r.get("ask") if r.get("ask_sz") is not None else None
                ch = []
                if pb is not None and pb != nb:
                    ch.append(["buy", pb, 0])
                if pa is not None and pa != na:
                    ch.append(["sell", pa, 0])
                if nb is not None:
                    ch.append(["buy", nb, r["bid_sz"]])
                if na is not None:
                    ch.append(["sell", na, r["ask_sz"]])
                if not ch:
                    continue
                state[m] = (nb, na)
                fh.write(json.dumps({"kind": "frame", "t": iso(r["t"]),
                                     "f": {"type": "l2_updates", "symbol": m,
                                           "changes": ch}}) + "\n")
                n_frames += 1
            elif r["kind"] == "print":
                bim = r.get("buyer_is_maker")
                if bim is None or r.get("price") is None or r.get("qty") is None:
                    n_dropped += 1
                    continue
                # buyer_is_maker True -> seller crossed -> BID consumed
                side = "sell" if bim else "buy"
                fh.write(json.dumps({"kind": "frame", "t": iso(r["t"]),
                                     "f": {"type": "l2_updates", "symbol": m,
                                           "changes": [],
                                           "trades": [{"price": r["price"],
                                                       "quantity": r["qty"],
                                                       "amount": r["qty"],
                                                       "side": side,
                                                       "tid": r.get("trade_id")}]}})
                         + "\n")
                n_trades += 1
    print(f"markets kept (>= {min_prints} print): {len(keep)}")
    print(f"frames {n_frames}  trades {n_trades}  dropped prints {n_dropped}")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
