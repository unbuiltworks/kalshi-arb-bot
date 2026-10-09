"""Chronological reconstruction of L5's executed decision process.

Every fill, every market, in order. State is what was OBSERVABLE at the
moment of each fill; the label is the NEXT observable action in that
market. Nothing about unplaced or unfilled orders is assumed.
"""
from __future__ import annotations
import json, gzip, re, collections, datetime as dt
import numpy as np

KEY = lambda r: (r.get("transactionHash"), r.get("conditionId"),
                 r.get("outcomeIndex"), r.get("timestamp"),
                 r.get("price"), r.get("size"), r.get("type"))

SLUG_RE = re.compile(r"-(\d+)m-(\d+)$")

def load_fills():
    seen, out = set(), []
    for path, op in [("data/l5/raw/activity.jsonl", open),
                     ("data/l5/raw/activity_gap.jsonl.gz", gzip.open)]:
        with op(path, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                for r in (json.loads(line).get("payload") or []):
                    if r.get("type") != "TRADE":
                        continue
                    k = KEY(r)
                    if k in seen:
                        continue
                    seen.add(k)
                    out.append(r)
    return out


def window_of(slug):
    """(duration_s, window_start_unix) from the slug, or (None, None)."""
    m = SLUG_RE.search(str(slug or ""))
    if not m:
        return None, None
    return int(m.group(1)) * 60, int(m.group(2))


BREAK = int(dt.datetime(2026, 6, 23, tzinfo=dt.timezone.utc).timestamp())
# label alphabet
EXTEND, COMPLEMENT, STOP = 0, 1, 2
LABELS = ["EXTEND", "COMPLEMENT", "STOP"]

FEATS = [
    "imbalance",          # (same - opp) / total, signed by last side
    "matched_frac",       # matched / total shares
    "residual_shares",    # |same - opp|
    "pair_cost_sofar",    # vwap_same + vwap_opp  (1.0 when one-sided)
    "price",              # this fill's price
    "price_is_favourite", # price > 0.5
    "n_fills",            # fills so far in this market
    "log_size",           # log1p of this fill's size
    "t_frac",             # elapsed / duration
    "tte_s",              # seconds to window end
    "gap_prev_s",         # seconds since previous fill
    "dur_5m",             # duration indicator
    "same_side_run",      # consecutive fills on this side
    "opp_has_any",        # opposite side already has inventory
]


def build():
    fills = load_fills()
    by_mkt = collections.defaultdict(list)
    for r in fills:
        cid = r.get("conditionId")
        dur, ws = window_of(r.get("slug") or r.get("eventSlug"))
        by_mkt[cid].append((int(r["timestamp"]), int(r["outcomeIndex"]),
                            float(r["price"]), float(r.get("size") or 0),
                            dur, ws))
    X, Y, META = [], [], []
    for cid, rows in by_mkt.items():
        rows.sort(key=lambda t: t[0])
        dur = next((r[4] for r in rows if r[4]), None)
        ws = next((r[5] for r in rows if r[5]), None)
        if not dur or not ws:
            continue
        qty = [0.0, 0.0]
        cost = [0.0, 0.0]
        run = 0
        prev_side = None
        prev_ts = None
        for i, (ts, oi, px, sz, _d, _w) in enumerate(rows):
            qty[oi] += sz
            cost[oi] += px * sz
            run = run + 1 if oi == prev_side else 1
            same, opp = qty[oi], qty[1 - oi]
            tot = same + opp
            matched = min(same, opp)
            vs = cost[oi] / same if same > 0 else 0.0
            vo = cost[1 - oi] / opp if opp > 0 else 0.0
            pair = (vs + vo) if (same > 0 and opp > 0) else 1.0
            gap = 0.0 if prev_ts is None else float(ts - prev_ts)
            elapsed = max(0.0, ts - ws)
            feat = [
                (same - opp) / tot if tot else 0.0,
                (2 * matched) / tot if tot else 0.0,
                abs(same - opp),
                pair,
                px,
                1.0 if px > 0.5 else 0.0,
                float(i + 1),
                float(np.log1p(sz)),
                min(1.0, elapsed / dur),
                float(max(0.0, ws + dur - ts)),
                min(gap, 300.0),
                1.0 if dur == 300 else 0.0,
                float(run),
                1.0 if opp > 0 else 0.0,
            ]
            if i + 1 < len(rows):
                nxt = rows[i + 1]
                lab = EXTEND if nxt[1] == oi else COMPLEMENT
                nxt_gap = float(nxt[0] - ts)
                nxt_px = nxt[2]
                nxt_sz = nxt[3]
            else:
                lab = STOP
                nxt_gap = nxt_px = nxt_sz = float("nan")
            X.append(feat)
            Y.append(lab)
            META.append((ts, cid, oi, px, sz, nxt_gap, nxt_px, nxt_sz,
                         1 if ts < BREAK else 0))
            prev_side, prev_ts = oi, ts
    return (np.asarray(X, dtype=np.float64), np.asarray(Y, dtype=np.int64),
            META)


if __name__ == "__main__":
    X, Y, M = build()
    np.savez_compressed("analysis/l5/clone/decisions.npz", X=X, Y=Y,
                        META=np.asarray([(m[0], m[8]) for m in M],
                                        dtype=np.int64),
                        NXT=np.asarray([(m[5], m[6], m[7]) for m in M],
                                       dtype=np.float64),
                        CID=np.asarray([str(m[1]) for m in M]))
    print("decision points:", len(Y))
    print("label mix:", {LABELS[i]: int((Y == i).sum()) for i in range(3)})
    early = np.asarray([m[8] for m in M], dtype=bool)
    print("early:", int(early.sum()), " late:", int((~early).sum()))
