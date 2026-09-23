"""P30 -- harvest authenticated PMUS trade rows, prove `dir`, then classify.

Three steps that must happen in this order, because each one is only
meaningful if the previous one succeeded.

  1. HARVEST. Pull `PMUS_TRADE_ROWS` documents out of a saved railway-agent
     tool-result file and persist them with their timestamps. Raw provenance
     is kept; nothing is normalised on the way in.

  2. PROVE THE AGGRESSOR. `dir` comes from the recorder's `_trade_direction`,
     whose vocabulary is buy|sell|unknown -- a TAKER DIRECTION, not an outcome
     name. Its fallback branch resolves `px >= ask -> buy` and `px <= bid ->
     sell` against the YES book, which IMPLIES buy = YES-taker and sell =
     NO-taker. That implication is tested here against real rows and the book
     in force at the time. It is not assumed, and if the rows do not support
     it the mapping stays unproven and every interaction stays
     QUEUE_AMBIGUOUS. Getting a result is not worth more than knowing whether
     the result means anything.

  3. CLASSIFY. Only with a proven mapping do the rows reach the frozen five
     classes and the three bounds.

Read-only throughout.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import pathlib
import re
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from src import pmus_authenticated_evidence as E      # noqa: E402
from src import pmus_interaction_classifier as clf    # noqa: E402

OUT_DIR = "data/pmus_auth_trades"


# ------------------------------------------------------------------ harvest

def harvest(tool_result_path: str, out_dir: str = OUT_DIR) -> Dict[str, Any]:
    """Extract every PMUS_TRADE_ROWS document, persist it, and report."""
    os.makedirs(out_dir, exist_ok=True)
    raw = json.dumps(json.load(open(tool_result_path)).get("toolCalls") or [])
    raw = raw.encode().decode("unicode_escape", errors="ignore")
    docs: List[dict] = []
    for m in re.finditer(r'PMUS_TRADE_ROWS (\{.*?\})(?="|\\n|$)', raw):
        try:
            docs.append(json.loads(m.group(1)))
        except ValueError:
            continue
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(out_dir, "auth_trades_%s.jsonl" % stamp)
    rows = 0
    with open(path, "a") as fh:
        for d in docs:
            fh.write(json.dumps(d) + "\n")
            rows += int(d.get("rows") or 0)
    return {"documents": len(docs), "rows": rows, "file": path,
            "source": tool_result_path}


def load_rows(pattern: str = OUT_DIR + "/*.jsonl") -> Dict[str, List[dict]]:
    """Per-market rows, deduplicated on the row's own identity."""
    by: Dict[str, Dict[str, dict]] = collections.defaultdict(dict)
    for f in sorted(glob.glob(pattern)):
        for line in open(f):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for slug, rows in (d.get("markets") or {}).items():
                for r in rows:
                    k = "|".join(str(r.get(x)) for x in
                                 ("t", "px", "qty", "trade_time", "dir"))
                    by[slug][k] = r
    return {s: sorted(v.values(), key=lambda r: float(r.get("t") or 0))
            for s, v in by.items()}


# -------------------------------------------------------- prove the mapping

def prove_dir_semantics(by_market: Dict[str, List[dict]],
                        books: Dict[str, List[dict]]) -> Dict[str, Any]:
    """Test buy = YES-taker / sell = NO-taker against the observed book.

    A `buy` should sit AT OR ABOVE the YES ask in force, and a `sell` AT OR
    BELOW the YES bid. Rows whose book is unknown are counted as
    unresolvable, not as agreement.
    """
    agree = collections.Counter()
    checked = 0
    for slug, rows in by_market.items():
        snaps = sorted(books.get(slug) or [], key=lambda b: b["t"])
        if not snaps:
            continue
        for r in rows:
            t = float(r.get("t") or 0)
            prior = [b for b in snaps if b["t"] <= t]
            if not prior:
                continue
            b = prior[-1]
            bid, ask = b.get("bid"), b.get("ask")
            if bid is None or ask is None:
                continue
            try:
                px = D(str(r.get("px")))
            except Exception:                                  # noqa: BLE001
                continue
            d = str(r.get("dir"))
            checked += 1
            if d == "buy":
                agree["buy_at_or_above_ask" if px >= D(str(ask))
                       else "buy_BELOW_ask"] += 1
            elif d == "sell":
                agree["sell_at_or_below_bid" if px <= D(str(bid))
                       else "sell_ABOVE_bid"] += 1
            else:
                agree["unknown_dir"] += 1
    ok = agree["buy_at_or_above_ask"] + agree["sell_at_or_below_bid"]
    bad = agree["buy_BELOW_ask"] + agree["sell_ABOVE_bid"]
    frac = (ok / (ok + bad)) if (ok + bad) else None
    return {
        "rows_checked_against_a_book": checked,
        "breakdown": dict(agree),
        "consistent": ok, "inconsistent": bad,
        "consistency": round(frac, 4) if frac is not None else None,
        "hypothesis": "buy = YES-taker (lifts the ask); "
                      "sell = NO-taker (hits the bid)",
        "verdict": ("PROVEN" if frac is not None and frac >= 0.95 and ok >= 20
                    else "UNPROVEN"),
        "consequence": ("mapping may be applied" if frac is not None
                        and frac >= 0.95 and ok >= 20 else
                        "aggressor stays UNKNOWN and every interaction stays "
                        "QUEUE_AMBIGUOUS"),
        "why_this_bar": ("95% consistency on at least 20 resolvable rows. A "
                         "mis-read aggressor turns non-interaction into "
                         "TRADE_AT_PRICE and fabricates the very evidence "
                         "this exercise exists to find."),
    }


def proven_normalizer(proof: Dict[str, Any]):
    """The normalizer to use, given the proof. Unproven means unchanged."""
    if proof.get("verdict") != "PROVEN":
        return E.normalize_print

    def norm(row):
        out = E.normalize_print(row)
        if out is None:
            return None
        d = str(row.get("dir"))
        if d == "buy":
            out["taker_side"] = "yes"
        elif d == "sell":
            out["taker_side"] = "no"
        return out
    return norm


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-tool-result", default=None)
    ap.add_argument("--rows", default=OUT_DIR + "/*.jsonl")
    a = ap.parse_args(argv)
    rep: Dict[str, Any] = {"label": "PMUS_AUTH_TRADE_HARVEST_V1"}
    if a.from_tool_result:
        rep["harvest"] = harvest(a.from_tool_result)
    by = load_rows(a.rows)
    rep["markets"] = {s: len(v) for s, v in by.items()}
    rep["total_rows"] = sum(len(v) for v in by.values())
    flat = [r for v in by.values() for r in v]
    rep["dir_vocabulary_audit"] = E.audit_dir_vocabulary(flat)
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
