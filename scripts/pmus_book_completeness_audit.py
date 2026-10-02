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
    dup_rows = 0
    seen_ids: dict = {}
    first_emit_rows: dict = {}
    caps, sizes, stamps = set(), [], []
    row_ts = {}
    for ts, nbytes, o in books:
        caps.add(o.get("cap_per_market"))
        sizes.append(nbytes)
        stamps.append(ts)
        for slug, arr in (o.get("markets") or {}).items():
            n = len(arr)
            emitted += n
            # duplicate_previously_emitted_rows: the diagnostic for the
            # _BOOK_LOG_SEEN retention defect. Before the fix a 400-row
            # document carried 349-365 rows already emitted earlier.
            ids = seen_ids.setdefault(slug, set())
            for x in arr:
                k = str(x.get("t"))
                if k in ids:
                    dup_rows += 1
                else:
                    ids.add(k)
            # a row can appear in two documents; count DISTINCT receipt
            # timestamps. An earlier revision extended a list and reported
            # 9,814 rows for a window that actually held 7,914.
            row_ts.setdefault(slug, set()).update(
                float(x["t"]) for x in arr if x.get("t") is not None)
            if slug not in first_emit_rows:
                # `generated` sums CONSECUTIVE book_updates deltas, so it cannot
                # account for a slug's FIRST emission -- there is no prior value
                # to difference against. Those rows are counted here instead, so
                # the invariant below is not failed by a boundary artifact.
                first_emit_rows[slug] = n
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
        ts_ = sorted(ts_)   # set -> ordered
        g = [round(b - a, 3) for a, b in zip(ts_, ts_[1:]) if b - a > 2.0]
        gaps[slug] = {"rows": len(ts_), "span_s": round(ts_[-1] - ts_[0], 1) if ts_ else 0,
                      "gaps_over_2s": len(g), "worst_gap_s": max(g) if g else 0.0}

    res = {
        "caps_seen": sorted(c for c in caps if c is not None),
        "emissions": len(books),
        "rows_emitted": emitted,
        "rows_generated": generated,
        "rows_lost_to_cap": lost,
        "duplicate_previously_emitted_rows": dup_rows,
        "rows_emitted_unique": sum(len(v) for v in seen_ids.values()),
        "rows_generated_unique": generated,
        "first_emission_rows_not_in_generated": sum(first_emit_rows.values()),
        "invariant_emitted_unique_le_generated_unique":
            sum(len(v) for v in seen_ids.values())
            <= generated + sum(first_emit_rows.values()),
        "invariant_note": ("rows_emitted_unique <= rows_generated_unique + "
                           "first_emission_rows_not_in_generated. The allowance "
                           "is a WINDOW-BOUNDARY artifact: `generated` sums "
                           "consecutive book_updates deltas and so omits each "
                           "slug's first emission. It is not a duplicate "
                           "allowance -- duplicates are counted separately as "
                           "duplicate_previously_emitted_rows, which must be 0."),
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
