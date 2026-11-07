"""Repair the L5 activity dataset by UNION, and classify every window.

THE DEFECT. `data/l5/raw/activity.jsonl` was collected with limit/offset
paging and lost rows in the busiest windows. The proof is arithmetic: a
redemption pays $1 per WINNING contract and only one outcome pays, so the
payout cannot exceed the larger side's holding -- and it does in 93 of 315
redeem-mismatch windows. Missing trades mean missing COST, so those windows
overstate profit, and they were carrying most of the edge in my own report.

WHY THE COLLECTOR IS NOT RE-RUN. The upstream endpoint is gone:
`data-api.polymarket.com/activity` and `/trades` both return **HTTP 410**
from this session. No collector, however correct, can fetch what the host
no longer serves. So the repair is a UNION of every capture already in the
repository rather than a refetch, and `l5_activity_collector.py` beside this
file implements the correct resumable collector for whenever an endpoint
exists again.

THE SOURCES, none of which is modified:

  data/l5/raw/activity.jsonl              the capture the analysis used
  data/l5/raw/activity_gap.jsonl.gz       an explicit gap-fill pass, with
                                          start/end params rather than
                                          offset -- so it is NOT subject to
                                          the same drift
  data/l5/sep2026/l5_activity_sep2026_raw.json.gz
  data/l5/sep2026/l5_activity_sep2223_raw.json.gz

INDEPENDENT VALIDATION comes from captures of DIFFERENT endpoints:

  data/l5/raw/trades.jsonl            /trades, a separate endpoint
  data/l5/raw/trades_takeronly.jsonl  /trades?takerOnly -- the maker/taker
                                      composition, which activity does not
                                      carry
  data/l5/raw/positions_all.jsonl     /positions, with avgPrice and cashPnl
                                      per asset: a ground truth for size
                                      and basis that owes nothing to our
                                      own arithmetic

THREE CLASSES PER WINDOW, and the third is not a failure:

  COMPLETE       the redemption identity holds -- payout <= larger side --
                 AND the window has a redemption, so it can be settled.
  INCOMPLETE     payout EXCEEDS the larger side: rows are provably missing.
  INDETERMINATE  no redemption, or one-sided, or no trades. Nothing is
                 claimed either way.

NO UNEXPLAINED REDEMPTION PROCEEDS ARE EVER TREATED AS PROFIT. That is the
rule the INCOMPLETE class exists to enforce.
"""
import gzip
import json
import os
import sys
from collections import defaultdict

ACTIVITY_SOURCES = [
    "data/l5/raw/activity.jsonl",
    "data/l5/raw/activity_gap.jsonl.gz",
    "data/l5/sep2026/l5_activity_sep2026_raw.json.gz",
    "data/l5/sep2026/l5_activity_sep2223_raw.json.gz",
]
FAMILY = "btc-updown-15m"
TOL = 0.02

COMPLETE, INCOMPLETE, INDETERMINATE = "COMPLETE", "INCOMPLETE", \
    "INDETERMINATE"


def _open(path):
    return (gzip.open(path, "rt") if path.endswith(".gz")
            else open(path, "r"))


def _payloads(obj):
    """Every activity row inside one captured response, whatever its shape."""
    if isinstance(obj, list):
        for x in obj:
            if isinstance(x, dict):
                yield x
        return
    if not isinstance(obj, dict):
        return
    for key in ("payload", "data", "rows", "activity", "results"):
        v = obj.get(key)
        if isinstance(v, list):
            for x in v:
                if isinstance(x, dict):
                    yield x
            return
        if isinstance(v, dict):
            for x in _payloads(v):
                yield x
            return
    if obj.get("type") and obj.get("slug"):
        yield obj


def key(row):
    """Stable identity for one activity row, for dedup ACROSS sources."""
    return (row.get("transactionHash"), row.get("asset"),
            row.get("timestamp"), row.get("size"), row.get("side"),
            row.get("type"))


def load_union(sources=None, verbose=True):
    """Union every source, deduped. Returns (rows, per-source stats)."""
    # `None` means "the standard set"; an explicit [] means EMPTY. Letting
    # an empty list fall through to the defaults would make a caller that
    # meant to read nothing silently read everything.
    sources = ACTIVITY_SOURCES if sources is None else list(sources)
    seen = {}
    stats = []
    for path in sources:
        if not os.path.exists(path):
            stats.append((path, 0, 0, "MISSING"))
            continue
        before = len(seen)
        rows = 0
        try:
            with _open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    for r in _payloads(obj):
                        if r.get("type") not in ("TRADE", "REDEEM"):
                            continue
                        rows += 1
                        seen.setdefault(key(r), r)
        except BaseException as exc:                           # noqa: BLE001
            stats.append((path, rows, len(seen) - before,
                          "ERROR:%s" % type(exc).__name__))
            continue
        stats.append((path, rows, len(seen) - before, "ok"))
    if verbose:
        print("  %-52s %9s %9s  %s"
              % ("source", "rows", "new", "status"))
        for p, rows, new, st in stats:
            print("  %-52s %9d %9d  %s" % (p[-52:], rows, new, st))
    return list(seen.values()), stats


def windows(rows, family=FAMILY):
    W = defaultdict(lambda: {"uq": 0.0, "uc": 0.0, "dq": 0.0, "dc": 0.0,
                             "red": 0.0, "n": 0, "assets": set()})
    for t in rows:
        s = str(t.get("slug") or "")
        if not s.startswith(family):
            continue
        e = W[s]
        if t.get("type") == "TRADE":
            e["n"] += 1
            e["assets"].add(t.get("asset"))
            o = t.get("outcome")
            if o == "Up":
                e["uq"] += float(t.get("size") or 0)
                e["uc"] += float(t.get("usdcSize") or 0)
            elif o == "Down":
                e["dq"] += float(t.get("size") or 0)
                e["dc"] += float(t.get("usdcSize") or 0)
        else:
            e["red"] += float(t.get("usdcSize") or 0)
    return W


def classify(e):
    if e["n"] == 0:
        return INDETERMINATE
    if e["red"] <= 0 or e["uq"] <= 0 or e["dq"] <= 0:
        return INDETERMINATE
    if e["red"] > max(e["uq"], e["dq"]) * (1.0 + TOL):
        return INCOMPLETE
    return COMPLETE


def economics(group):
    """Pair/residual split on a group of windows. No attribution needed for
    the matched part: a pair redeems $1 either way."""
    mq = mc = rq = rc = 0.0
    net = 0.0
    trades = 0
    for _s, e in group:
        ub, db = e["uc"] / e["uq"], e["dc"] / e["dq"]
        m = min(e["uq"], e["dq"])
        mq += m
        mc += m * (ub + db)
        rq += abs(e["uq"] - e["dq"])
        rc += abs(e["uq"] - e["dq"]) * (ub if e["uq"] > e["dq"] else db)
        net += e["red"] - (e["uc"] + e["dc"])
        trades += e["n"]
    pair = mq - mc
    return {"windows": len(group), "trades": trades, "net": net,
            "matched_qty": mq, "matched_basis": (mc / mq) if mq else 0,
            "pair_pnl": pair, "pair_per_contract": (pair / mq) if mq else 0,
            "residual_qty": rq, "residual_basis": (rc / rq) if rq else 0,
            "residual_pnl": net - pair,
            "residual_per_contract": ((net - pair) / rq) if rq else 0,
            "turnover": mc + rc}


def main():
    print("=" * 74)
    print("UNION OF EVERY CAPTURE IN THE REPOSITORY")
    print("=" * 74)
    rows, _ = load_union()
    tr = [r for r in rows if r.get("type") == "TRADE"]
    print("\n  deduped TRADE+REDEEM rows %8d   of which TRADE %8d"
          % (len(rows), len(tr)))

    W = windows(rows)
    B = defaultdict(list)
    for s, e in W.items():
        B[classify(e)].append((s, e))
    print("\n" + "=" * 74)
    print("WINDOW CLASSES, %s" % FAMILY)
    print("=" * 74)
    for k in (COMPLETE, INCOMPLETE, INDETERMINATE):
        print("  %-14s %5d" % (k, len(B[k])))

    print("\n" + "=" * 74)
    print("ECONOMICS -- COMPLETE windows only")
    print("=" * 74)
    for k in (COMPLETE, INCOMPLETE):
        if not B[k]:
            continue
        e = economics(B[k])
        print("  %s" % k)
        print("    windows %4d  trades %7d  turnover %11.2f  net %+11.2f"
              "  (%+8.3f/win)"
              % (e["windows"], e["trades"], e["turnover"], e["net"],
                 e["net"] / e["windows"]))
        print("    matched  %9.1f ct  basis %.4f  P&L %+10.2f  %+.4f/ct"
              % (e["matched_qty"], e["matched_basis"], e["pair_pnl"],
                 e["pair_per_contract"]))
        print("    residual %9.1f ct  basis %.4f  P&L %+10.2f  %+.4f/ct"
              % (e["residual_qty"], e["residual_basis"], e["residual_pnl"],
                 e["residual_per_contract"]))
        print("    capital efficiency: net / turnover = %+.4f"
              % (e["net"] / e["turnover"] if e["turnover"] else 0))
    print("\n  INCOMPLETE economics are shown ONLY to size what is being")
    print("  excluded. Their net is inflated by the missing cost and is")
    print("  never treated as profit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
