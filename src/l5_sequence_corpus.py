"""L5's per-window action sequences, reconstructed from the raw activity corpus.

SOURCE: `data/l5/raw/activity.jsonl` -- 157 paged responses, 78,000 records,
75,864 of type TRADE. Only `btc-updown-15m` is used here: 67,511 trades, the
SAME market type and horizon as our PMUS BTC15m windows, so the comparison is
like-for-like rather than a cross-horizon extrapolation.

WHAT THIS GIVES THAT THE AGGREGATES DO NOT. `src/l5_complement_response_curve.py`
holds population statistics (the response curve, gap shares, residual bands).
Those cannot answer "at THIS state, what did he do next". This module rebuilds
the per-window chronology so a PMUS state can be matched against comparable L5
states and his SUBSEQUENT action read off.

STATE, as far as public data allows. Per fill, in window order: running Up/Down
inventory, imbalance, held-side average basis, seconds since the previous fill,
TTE from the window's own epoch, and the probability move implied by his own
fill prices. Honest limits, stated because they bound every conclusion drawn
from this file:

  * there is NO book here, so "probability movement" is his own fill price
    sequence, not the market mid. It is a proxy and is named one.
  * maker vs taker is NOT in this feed, so aggressiveness cannot be read per
    fill. The aggregate module has that split; this one must not claim it.
  * he almost never SELLs (2 of 75,864), so exits are REDEEM at settlement.
    "CARRY" therefore means carried to settlement, not closed out.

Read-only. No policy, no threshold, no selection.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Dict, Iterator, List, Optional

RAW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "l5", "raw", "activity.jsonl")

#: the horizon we are translating to. Other families are excluded, not mixed.
SERIES = "btc-updown-15m"
WINDOW_S = 900

#: what the NEXT fill does relative to the state it arrives in
EXTEND, COMPLETE, OPEN, CARRY = "EXTEND", "COMPLETE", "OPEN", "CARRY"


def _records() -> Iterator[dict]:
    with open(RAW, encoding="utf-8") as fh:
        for ln in fh:
            try:
                page = json.loads(ln)
            except ValueError:
                continue
            for r in page.get("payload") or []:
                if r.get("type") != "TRADE":
                    continue
                if not str(r.get("slug", "")).startswith(SERIES):
                    continue
                if r.get("side") != "BUY":
                    # 2 SELLs in 75,864; excluded so "BUY" is exact rather than
                    # nearly-exact, and counted by caller if it ever matters.
                    continue
                yield r


def _window_end(slug: str) -> Optional[int]:
    """`btc-updown-15m-<start epoch>` -> end epoch."""
    tail = slug.rsplit("-", 1)[-1]
    try:
        return int(tail) + WINDOW_S
    except ValueError:
        return None


def windows() -> Dict[str, List[dict]]:
    """slug -> chronological BUY fills, each annotated with the state it met."""
    by: Dict[str, List[dict]] = {}
    for r in _records():
        by.setdefault(str(r["slug"]), []).append(r)
    out: Dict[str, List[dict]] = {}
    for slug, rs in by.items():
        rs.sort(key=lambda x: (int(x["timestamp"]), float(x.get("price") or 0)))
        end = _window_end(slug)
        q = {"up": D(0), "down": D(0)}
        c = {"up": D(0), "down": D(0)}
        seq: List[dict] = []
        prev_t: Optional[int] = None
        prices: List[float] = []
        for r in rs:
            side = "up" if str(r.get("outcome")) == "Up" else "down"
            px = D(str(round(float(r.get("price") or 0), 4)))
            sz = D(str(round(float(r.get("size") or 0), 6)))
            if sz <= 0:
                continue
            held = "up" if q["up"] > q["down"] else (
                "down" if q["down"] > q["up"] else None)
            basis = (c[held] / q[held]) if held and q[held] > 0 else None
            imb = q["up"] - q["down"]
            # the action THIS fill performs, given the state it arrived in
            if held is None:
                kind = OPEN
            elif side == held:
                kind = EXTEND
            else:
                kind = COMPLETE
            # his own-price move over the preceding fills, as an UP-probability
            up_px = [p for p in prices[-4:]]
            move = (round(float(px if side == "up" else D(1) - px)
                          - up_px[0], 4) if up_px else None)
            seq.append({
                "t": int(r["timestamp"]), "side": side, "px": px, "size": sz,
                "kind": kind,
                "inv_before": {"up": q["up"], "down": q["down"]},
                "imbalance_before": imb,
                "held_before": held, "held_basis_before": basis,
                "gap_s": (int(r["timestamp"]) - prev_t) if prev_t else None,
                "tte_s": (end - int(r["timestamp"])) if end else None,
                "own_price_move": move,
            })
            q[side] += sz
            c[side] += sz * px
            prev_t = int(r["timestamp"])
            prices.append(float(px if side == "up" else D(1) - px))
        if seq:
            # terminal state: what he was left holding at the close
            for e in seq:
                e["terminal_imbalance"] = q["up"] - q["down"]
                e["n_fills_in_window"] = len(seq)
            out[slug] = seq
    return out


def states() -> List[dict]:
    """Every fill, flattened, each carrying the action that FOLLOWED it."""
    rows: List[dict] = []
    for slug, seq in windows().items():
        for i, e in enumerate(seq):
            nxt = seq[i + 1] if i + 1 < len(seq) else None
            r = dict(e)
            r["slug"] = slug
            r["next_kind"] = nxt["kind"] if nxt else CARRY
            r["next_side"] = nxt["side"] if nxt else None
            r["next_size"] = nxt["size"] if nxt else None
            r["next_px"] = nxt["px"] if nxt else None
            r["next_delay_s"] = (nxt["t"] - e["t"]) if nxt else None
            rows.append(r)
    return rows
