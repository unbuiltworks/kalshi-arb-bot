"""State after fill i, used to predict fill i+1. No lookahead, ever.

THE TRAP THIS FILE IS BUILT AROUND. "Normalized progress through the window"
is the obvious feature and the obvious way to compute it -- fills_so_far /
total_fills_in_window -- IS LOOKAHEAD. The window's eventual fill count is not
knowable at fill i; it is a final-window quantity. Progress is therefore taken
from ELAPSED TIME, (t - window_start) / 900, which is observable from a clock.

Everything else obeys the same rule: every field is computed from fills 1..i
only, and the label is read from fill i+1 and nothing else.

RECENT PRICE MOVEMENT is derived from L5'S OWN FILL PRICES, not from the book.
The 1-second book does not exist for most of this period, so the honest
observable is the sequence of prices he actually paid. It is labelled that way
rather than described as market movement.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

WINDOW_S = 900

# every feature here is a function of fills 1..i. The order is the ablation
# ladder, so a prefix of this list is a nested model.
BLOCKS = {
    "last_side": ["is_up"],
    "market_price": ["price", "price_up_last", "price_dn_last",
                     "x_side_price"],
    "inventory": ["up_qty_log", "dn_qty_log", "signed_imbalance",
                  "abs_imbalance", "run_len", "fills_so_far",
                  "x_side_imbalance", "x_side_runlen"],
    "vwap_economics": ["up_vwap", "dn_vwap", "vwap_diff", "pair_cost",
                       "pair_cost_known", "cheaper_is_up"],
    "tte_progress": ["tte_frac", "progress"],
    "recent_movement": ["dprice_1", "dprice_same_side", "dprice_3"],
}
LADDER = ["last_side", "market_price", "inventory", "vwap_economics",
          "tte_progress", "recent_movement"]


def feature_names(blocks: Sequence[str]) -> List[str]:
    out: List[str] = []
    for b in blocks:
        out.extend(BLOCKS[b])
    return out


def build_window(slug: str, fills: Sequence[dict]) -> List[Dict[str, Any]]:
    """One row per fill i that HAS a successor. Label comes from fill i+1."""
    try:
        start = int(slug.rsplit("-", 1)[1])
    except Exception:                                         # noqa: BLE001
        return []
    up_q = dn_q = 0.0
    up_c = dn_c = 0.0
    run = 0
    prev_side: Optional[str] = None
    last_px: Dict[str, Optional[float]] = {"Up": None, "Down": None}
    px_hist: List[float] = []
    rows: List[Dict[str, Any]] = []
    for i, f in enumerate(fills):
        side = "Up" if str(f.get("outcome")) == "Up" else "Down"
        sz = float(f.get("size") or 0.0)
        usd = float(f.get("usdcSize") or 0.0)
        px = float(f.get("price") or 0.0)
        if side == "Up":
            up_q += sz
            up_c += usd
        else:
            dn_q += sz
            dn_c += usd
        run = run + 1 if side == prev_side else 1
        prev_side = side
        prev_same = last_px[side]
        last_px[side] = px
        px_hist.append(px)

        up_v = (up_c / up_q) if up_q > 0 else None
        dn_v = (dn_c / dn_q) if dn_q > 0 else None
        pair = (up_v + dn_v) if (up_v is not None and dn_v is not None) else None
        tot = up_q + dn_q
        signed = ((up_q - dn_q) / tot) if tot > 0 else 0.0
        t = f.get("timestamp")
        elapsed = ((t - start) / WINDOW_S) if t is not None else None
        tte = ((start + WINDOW_S) - t) if t is not None else None

        nxt = fills[i + 1] if i + 1 < len(fills) else None
        if nxt is None:
            continue
        nside = "Up" if str(nxt.get("outcome")) == "Up" else "Down"
        is_up = 1.0 if side == "Up" else 0.0
        # movement from HIS OWN fill prices, never from a book
        d1 = (px_hist[-1] - px_hist[-2]) if len(px_hist) >= 2 else 0.0
        d3 = (px_hist[-1] - px_hist[-4]) if len(px_hist) >= 4 else 0.0
        rows.append({
            "slug": slug, "i": i, "t": t,
            # --- last_side
            "is_up": is_up,
            # --- market_price
            "price": px,
            "price_up_last": (last_px["Up"] if last_px["Up"] is not None else 0.5),
            "price_dn_last": (last_px["Down"] if last_px["Down"] is not None else 0.5),
            "x_side_price": is_up * px,
            # --- inventory
            "up_qty_log": math.log1p(up_q),
            "dn_qty_log": math.log1p(dn_q),
            "signed_imbalance": signed,
            "abs_imbalance": abs(signed),
            "run_len": float(run),
            "fills_so_far": float(i + 1),
            "x_side_imbalance": is_up * signed,
            "x_side_runlen": is_up * float(run),
            # --- vwap_economics
            "up_vwap": (up_v if up_v is not None else 0.5),
            "dn_vwap": (dn_v if dn_v is not None else 0.5),
            "vwap_diff": ((up_v - dn_v) if (up_v is not None
                                            and dn_v is not None) else 0.0),
            "pair_cost": (pair if pair is not None else 1.0),
            "pair_cost_known": (1.0 if pair is not None else 0.0),
            "cheaper_is_up": (1.0 if (up_v is not None and dn_v is not None
                                      and up_v < dn_v) else 0.0),
            # --- tte_progress   (ELAPSED TIME, never fills/total)
            "tte_frac": ((tte / WINDOW_S) if tte is not None else 0.0),
            "progress": (elapsed if elapsed is not None else 0.0),
            # --- recent_movement (his own fill prices)
            "dprice_1": d1,
            "dprice_same_side": ((px - prev_same) if prev_same is not None
                                 else 0.0),
            "dprice_3": d3,
            # --- labels and slice keys
            "y_up": 1.0 if nside == "Up" else 0.0,
            "y_continue": 1.0 if nside == side else 0.0,
            "last_side": side,
            "_run": run, "_tte": (tte if tte is not None else 0),
            "_price": px, "_imb": signed, "_pair": pair,
        })
    return rows


def build(wins: Dict[str, List[dict]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for slug in sorted(wins):
        out.extend(build_window(slug, wins[slug]))
    out.sort(key=lambda r: (r["t"] or 0, r["slug"], r["i"]))
    return out
