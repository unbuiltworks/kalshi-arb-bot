"""Build the control sample for one contract length."""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Set

from analysis.l5.microstructure.control import build_control
from analysis.l5.microstructure.load import l5_fills, load


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times", required=True)
    ap.add_argument("--contract-length", required=True)
    ap.add_argument("--window-s", type=int, required=True)
    ap.add_argument("--exclude-s", type=int, default=5)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    fills_by_slug, bt, _ = l5_fills(a.backfill, a.block_times)
    traded = {s for s, v in fills_by_slug.items() if v}
    books, wins = load(a.parquet, contract_length=a.contract_length,
                       slugs=traded)
    rows: List[Dict[str, Any]] = []
    for slug in sorted(wins):
        toks = wins[slug]["tokens"]
        up = next((t for t, o in toks.items() if str(o).lower() == "up"), None)
        dn = next((t for t, o in toks.items() if str(o).lower() == "down"),
                  None)
        if up is None or dn is None:
            continue
        start = int(slug.rsplit("-", 1)[1])
        fs: Dict[str, Set[int]] = defaultdict(set)
        for f in fills_by_slug.get(slug, []):
            if f.get("_ts") is not None:
                fs[f["token_id"]].add(int(f["_ts"]))
        got = build_control(books, up, dn, start, start + a.window_s, fs,
                            slug=slug, clen=a.contract_length,
                            exclude_s=a.exclude_s)
        rows.extend(got)
        print("%-34s control obs=%d" % (slug.replace("btc-updown-", ""),
                                        len(got)), flush=True)
    os.makedirs(a.outdir, exist_ok=True)
    p = os.path.join(a.outdir, "CONTROL_%s.jsonl" % a.contract_length)
    with open(p, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    print("\ncontrol observations:", len(rows), "->", p)
    print("exclusion radius around every L5 fill: +/-%ds" % a.exclude_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
