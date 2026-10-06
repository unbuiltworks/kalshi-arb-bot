"""Build the per-fill event table for one contract length."""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List

from analysis.l5.microstructure.events import build
from analysis.l5.microstructure.load import l5_fills, load


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times", required=True)
    ap.add_argument("--contract-length", required=True)
    ap.add_argument("--window-s", type=int, required=True)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    fills_by_slug, bt, bf = l5_fills(a.backfill, a.block_times)
    traded = {s for s, v in fills_by_slug.items() if v}
    books, wins = load(a.parquet, contract_length=a.contract_length,
                       slugs=traded)
    rows: List[Dict[str, Any]] = []
    per_window: List[Dict[str, Any]] = []
    for slug in sorted(wins):
        toks = wins[slug]["tokens"]
        up = next((t for t, o in toks.items() if str(o).lower() == "up"), None)
        dn = next((t for t, o in toks.items() if str(o).lower() == "down"),
                  None)
        if up is None or dn is None:
            per_window.append({"market_slug": slug,
                               "status": "NOT_TWO_OUTCOMES",
                               "tokens": len(toks)})
            continue
        start = int(slug.rsplit("-", 1)[1])
        end = start + a.window_s
        fl = fills_by_slug.get(slug, [])
        got = build(books, up, dn, start, end, fl, slug=slug,
                    clen=a.contract_length, block_times=bt)
        rows.extend(got)
        untimed = sum(1 for f in fl if f.get("_ts") is None)
        per_window.append({
            "market_slug": slug, "contract_length": a.contract_length,
            "window_utc": [start, end], "n_events": len(got),
            "fills_in_backfill": len(fl),
            "fills_without_a_block_timestamp_excluded": untimed,
            "tokens": {"up": up, "down": dn},
            "winner_up": wins[slug]["winner"].get(up)})
        print("%-34s events=%-4d (backfill %d, untimed %d)"
              % (slug.replace("btc-updown-", ""), len(got), len(fl), untimed),
              flush=True)

    os.makedirs(a.outdir, exist_ok=True)
    tag = a.contract_length
    ev_path = os.path.join(a.outdir, "EVENTS_%s.jsonl" % tag)
    with open(ev_path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    mech: Dict[str, int] = {}
    for r in rows:
        mech[r["mechanism"]] = mech.get(r["mechanism"], 0) + 1
    rep = {"contract_length": tag, "window_s": a.window_s,
           "n_events": len(rows), "windows": len(per_window),
           "windows_with_fills": sum(1 for w in per_window
                                     if w.get("n_events")),
           "mechanism_counts": mech,
           "backfill_total_l5_fills": bf.get("total_l5_fills"),
           "backfill_chunk_holes": bf.get("chunk_holes"),
           "resolution": ("whole seconds, event-driven; no interpolation, no "
                          "manufactured sub-second state"),
           "per_window": per_window}
    with open(os.path.join(a.outdir, "EVENTS_%s_SUMMARY.json" % tag),
              "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print("\n" + json.dumps({k: v for k, v in rep.items()
                             if k != "per_window"}, indent=1))
    print("events ->", ev_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
