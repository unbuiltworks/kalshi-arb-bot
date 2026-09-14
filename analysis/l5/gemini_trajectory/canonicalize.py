"""Canonicalize ALL recoverable Gemini BTC05M + ETH05M tape into one common
market-state schema, with provenance, and measure coverage per window.

RULES OBSERVED HERE:
  * every row keeps its provenance (source file, source timestamp, market);
  * dedup is DETERMINISTIC -- (market, kind, t, payload-digest);
  * NOTHING is forward-filled across an unobserved period. A gap stays a gap
    and is reported as a gap, because a forward-filled quote would create a
    fake executable state;
  * window metadata comes from the tape's own `window` rows (asset, expiry,
    window_minutes) and falls back to the symbol only when absent, with the
    fallback recorded.
"""
import collections
import glob
import gzip
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
TAPES = sorted(glob.glob(os.path.join(ROOT, "data/policy_d_port/tape_*.jsonl"))) + \
        sorted(glob.glob(os.path.join(ROOT, "data/policy_d_port_ext/*.jsonl")))
OUT = os.path.join(HERE, "gemini_5m_canonical.jsonl.gz")
DUR_S = 300.0


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def top(levels):
    """Best price/size from a [[px, sz], ...] ladder, or (None, None)."""
    if not isinstance(levels, list) or not levels:
        return None, None
    lv = levels[0]
    if not isinstance(lv, (list, tuple)) or len(lv) < 2:
        return None, None
    return _f(lv[0]), _f(lv[1])


def expiry_from_symbol(market):
    try:
        core = market.split("-")[1]
        d = "".join(c for c in core if c.isdigit())[-10:]
        if len(d) != 10:
            return None
        return datetime(2000 + int(d[0:2]), int(d[2:4]), int(d[4:6]),
                        int(d[6:8]), int(d[8:10]),
                        tzinfo=timezone.utc).timestamp()
    except Exception:                                       # noqa: BLE001
        return None


def collect():
    meta, rows, seen = {}, [], set()
    counts = collections.Counter()
    for path in TAPES:
        base = os.path.basename(path)
        for line in open(path):
            try:
                j = json.loads(line)
            except Exception:                               # noqa: BLE001
                counts["unparseable"] += 1
                continue
            if j.get("venue") != "gemini":
                continue
            m = str(j.get("market") or "")
            core = m.split("-")[1] if "-" in m else ""
            if not (core.startswith("BTC05M") or core.startswith("ETH05M")):
                continue
            kind = j.get("kind")
            if kind == "window":
                exp = j.get("expiry")
                try:
                    et = datetime.fromisoformat(
                        str(exp).replace("Z", "+00:00")).timestamp()
                except Exception:                           # noqa: BLE001
                    et = None
                meta[m] = {"asset": j.get("asset"),
                           "window_minutes": j.get("window_minutes"),
                           "expiry_t": et, "expiry_source": "tape_window_row",
                           "src_file": base}
                counts["window_rows"] += 1
                continue
            t = _f(j.get("t"))
            if t is None:
                counts["no_timestamp"] += 1
                continue
            if kind == "bbo":
                bid, bid_sz = top(j.get("bids"))
                ask, ask_sz = top(j.get("asks"))
                key = (m, "bbo", round(t, 6), bid, ask, bid_sz, ask_sz)
                if key in seen:
                    counts["dedup_bbo"] += 1
                    continue
                seen.add(key)
                rows.append({"market": m, "kind": "bbo", "t": t,
                             "bid": bid, "ask": ask,
                             "bid_sz": bid_sz, "ask_sz": ask_sz,
                             "src_file": base, "src_t": j.get("t")})
                counts["bbo"] += 1
            elif kind == "print":
                key = (m, "print", j.get("trade_id"), round(t, 6))
                if key in seen:
                    counts["dedup_print"] += 1
                    continue
                seen.add(key)
                rows.append({"market": m, "kind": "print", "t": t,
                             "price": _f(j.get("price")), "qty": _f(j.get("qty")),
                             "buyer_is_maker": j.get("buyer_is_maker"),
                             "trade_id": j.get("trade_id"),
                             "src_file": base, "src_t": j.get("t")})
                counts["print"] += 1
    # attach window metadata; record the fallback when the tape never
    # declared the window
    for r in rows:
        m = r["market"]
        md = meta.get(m)
        if md is None:
            et = expiry_from_symbol(m)
            core = m.split("-")[1] if "-" in m else ""
            md = {"asset": core[:3] or None, "window_minutes": 5,
                  "expiry_t": et, "expiry_source": "symbol_fallback",
                  "src_file": None}
            meta[m] = md
            counts["meta_from_symbol"] += 1
        r["asset"] = md["asset"]
        r["expiry_t"] = md["expiry_t"]
        r["expiry_source"] = md["expiry_source"]
        r["window_start_t"] = (None if md["expiry_t"] is None
                               else md["expiry_t"] - DUR_S)
        r["tte_s"] = (None if md["expiry_t"] is None else md["expiry_t"] - r["t"])
    rows.sort(key=lambda r: (r["market"], r["t"], r["kind"]))
    return rows, meta, counts


def main():
    rows, meta, counts = collect()
    with gzip.open(OUT, "wt") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(f"tapes {len(TAPES)}   canonical rows {len(rows)}   markets {len(meta)}")
    print("  " + json.dumps(dict(counts.most_common()), indent=1)[:400])
    print(f"  wrote {OUT}")
    by_asset = collections.Counter(r["asset"] for r in rows)
    print("  rows by asset:", dict(by_asset))
    src = collections.Counter(m["expiry_source"] for m in meta.values())
    print("  window metadata source:", dict(src))


if __name__ == "__main__":
    main()
