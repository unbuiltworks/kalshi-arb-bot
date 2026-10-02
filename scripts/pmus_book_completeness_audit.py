#!/usr/bin/env python3
"""Audit PMUS_BOOK_ROWS completeness from Railway log dumps. No code in the
recorder is needed for this: completeness is already independently auditable.

WHY THIS EXISTS. The 1300z synchronized window had 87 PMUS book gaps above 2 s,
many near 5 s, in a study whose whole question is what happened to the queue in
the seconds before a fill. The cause was found in the recorder:

    out[slug] = fresh[-cap:]        # src/pmus_ws_evidence_recorder.py

`cap` is PMUS_WS_BOOK_LOG, per market PER EMISSION, and the slice keeps only the
NEWEST `cap` rows -- the older ones in that emission are dropped with no counter.
At cap=50 that silently discarded about half of every emission.

HOW LOSS IS MEASURED WITHOUT TOUCHING THE RECORDER. Each emitted document already
carries `identity.<slug>.book_updates`, the recorder's own CUMULATIVE count of
book updates for that market. The difference between consecutive emissions is
therefore how many states were GENERATED, and the row count is how many were
EMITTED. Loss is the difference. That is the counter the brief asks for, and it
is already in the evidence, so the minimum safe change stays a variable change:
no rebuild of the image that holds the PMUS credentials.

SIZE CEILING, which is why the cap cannot simply be maximised. An over-long log
line is truncated by the platform at 81,920 bytes and every fragment of it is
unparseable, losing the whole emission -- this already happened once and is why
BOOK_LOG_MARKETS_PER_DOC exists. Measured here: 152.6 bytes per row (p50),
154.3 (p90), at most 2 markets per document for BTC15m. So the document size is
checked explicitly and reported as a fraction of the limit.

Usage: python3 scripts/pmus_book_completeness_audit.py <log dump json> [...]
"""
from __future__ import annotations
import datetime as dt
import json, statistics, sys

LOG_LINE_LIMIT = 81920
DEC = json.JSONDecoder()


def docs(paths, tag):
    out = []
    for p in paths:
        d = json.load(open(p))
        for r in d.get("deploy") or []:
            m = r.get("message", "")
            if tag not in m:
                continue
            try:
                o, _ = DEC.raw_decode(m.split(tag, 1)[1].strip())
            except Exception:
                continue
            out.append((r.get("timestamp"), len(m), o))
    out.sort(key=lambda x: x[0] or "")
    return out


def q(v, p):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(p * len(v)))], 3) if v else None


def main(paths):
    books = docs(paths, "PMUS_BOOK_ROWS")
    trades = docs(paths, "PMUS_TRADE_ROWS")
    if not books:
        print(json.dumps({"status": "NO_BOOK_DOCS"})); return

    prev, rows = {}, []
    emitted = generated = cap_hits = 0
    caps, sizes, stamps = set(), [], []
    row_ts = {}
    for ts, nbytes, o in books:
        caps.add(o.get("cap_per_market"))
        sizes.append(nbytes)
        stamps.append(ts)
        for slug, arr in (o.get("markets") or {}).items():
            n = len(arr)
            emitted += n
            row_ts.setdefault(slug, []).extend(
                float(x["t"]) for x in arr if x.get("t") is not None)
            bu = ((o.get("identity") or {}).get(slug) or {}).get("book_updates")
            cap = o.get("cap_per_market") or 0
            if cap and n >= cap:
                cap_hits += 1
            if bu is not None:
                if slug in prev:
                    g = bu - prev[slug]
                    if g >= 0:
                        generated += g
                        rows.append({"slug": slug, "emitted": n, "generated": g,
                                     "lost": max(0, g - n)})
                prev[slug] = bu

    lost = sum(r["lost"] for r in rows)
    gen = [r["generated"] for r in rows]
    cad = []
    try:
        t = [dt.datetime.fromisoformat((s or "").replace("Z", "+00:00"))
             for s in stamps]
        cad = [(b - a).total_seconds() for a, b in zip(t, t[1:])]
    except Exception:
        pass

    gaps = {}
    for slug, ts_ in row_ts.items():
        ts_ = sorted(ts_)
        g = [round(b - a, 3) for a, b in zip(ts_, ts_[1:]) if b - a > 2.0]
        gaps[slug] = {"rows": len(ts_), "span_s": round(ts_[-1] - ts_[0], 1) if ts_ else 0,
                      "gaps_over_2s": len(g), "worst_gap_s": max(g) if g else 0.0}

    res = {
        "caps_seen": sorted(c for c in caps if c is not None),
        "emissions": len(books),
        "rows_emitted": emitted,
        "rows_generated": generated,
        "rows_lost_to_cap": lost,
        "cap_induced_loss": lost > 0,
        "market_emissions_at_cap": cap_hits,
        "generated_per_market_per_emission": {
            "p50": q(gen, .5), "p90": q(gen, .9), "p99": q(gen, .99),
            "max": max(gen) if gen else None},
        "emission_cadence_s": {"p50": q(cad, .5), "p90": q(cad, .9),
                               "max": round(max(cad), 3) if cad else None},
        "log_line_bytes": {"p50": q(sizes, .5), "max": max(sizes),
                           "limit": LOG_LINE_LIMIT,
                           "max_pct_of_limit": round(100 * max(sizes) / LOG_LINE_LIMIT, 1)},
        "book_gaps_by_market": gaps,
        "trade_docs_seen": len(trades),
        "trade_rows_seen": sum(sum(len(v) for v in (o.get("markets") or {}).values())
                               for _, _, o in trades),
        "verdict": ("COMPLETE" if lost == 0 else
                    f"CENSORED -- {lost} of {generated} states dropped by the cap"),
    }
    print(json.dumps(res, indent=2))
    return res


if __name__ == "__main__":
    main(sys.argv[1:])
