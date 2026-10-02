"""Decision state. Built ONLY from data already received at the decision instant.

Two inventories are tracked and never conflated:
  l5_obs_*   L5's OBSERVED inventory this window, from his public fills.
             Used by the side models, because anticipating L5 is the target.
  shadow_*   OUR hypothetical inventory, from defensible shadow fills only.
             Used for pair-cost and economics, never as an anticipation feature.
"""
from __future__ import annotations
import statistics as st
from . import config as C


def touch(bk):
    b = bk["bids"][0] if bk["bids"] else None
    a = bk["asks"][0] if bk["asks"] else None
    return b, a


def depth_at(bk, side, px):
    rows = bk["bids"] if side == "bid" else bk["asks"]
    for r in rows:
        if abs(r["p"] - px) < 1e-9:
            return r["q"]
    return 0.0


def _basis(fills, up):
    f = [x for x in fills if x["up"] == up]
    q = sum(x["q"] for x in f)
    return (sum(x["q"] * x["p"] for x in f) / q) if q else None, q


def build(now, mkt, bk_up, bk_dn, l5_fills, trades, shadow_fills, prev_decision):
    """`l5_fills`, `trades` must already be filtered to t < now by the caller."""
    s = {"t": now, "slug": mkt["slug"], "window_start": mkt["window_start"],
         "secs_from_open": now - mkt["window_start"],
         "secs_to_settle": mkt["window_start"] + C.WINDOW_SECONDS - now}

    for tag, bk in (("up", bk_up), ("dn", bk_dn)):
        b, a = touch(bk)
        s[f"{tag}_bid"] = b["p"] if b else None
        s[f"{tag}_bid_q"] = b["q"] if b else None
        s[f"{tag}_ask"] = a["p"] if a else None
        s[f"{tag}_ask_q"] = a["q"] if a else None
        s[f"{tag}_spread"] = (a["p"] - b["p"]) if (a and b) else None
        s[f"{tag}_bid_levels"] = len(bk["bids"])
        s[f"{tag}_ask_levels"] = len(bk["asks"])
        s[f"{tag}_bid_depth5"] = sum(x["q"] for x in bk["bids"][:5])
        s[f"{tag}_ask_depth5"] = sum(x["q"] for x in bk["asks"][:5])
        s[f"{tag}_book_hash"] = bk.get("hash")

    # ---- recent market flow by taker direction (taker-indexed tape)
    w60 = [x for x in trades if now - x["t"] <= 60]
    s["flow_n_60"] = len(w60)
    s["flow_vol_60"] = sum(x["q"] for x in w60)
    up_take = sum(x["q"] for x in w60 if x["up"] and x["buy"])
    dn_take = sum(x["q"] for x in w60 if (not x["up"]) and x["buy"])
    s["taker_buy_up_vol_60"] = up_take
    s["taker_buy_dn_vol_60"] = dn_take
    tot = up_take + dn_take
    s["taker_up_share_60"] = (up_take / tot) if tot else None
    s["trade_tape_lag_s"] = (now - max(x["t"] for x in trades)) if trades else None
    s["book_changes_since_prev"] = None
    if prev_decision:
        s["book_changes_since_prev"] = sum(
            1 for k in ("up_book_hash", "dn_book_hash")
            if prev_decision.get(k) != s.get(k))

    # ---- D1: unlagged flow from our own book deltas (observed consumption).
    # Measured on the UP book ONLY. Up and Down are complements, so summing both
    # books double-counts the same aggression: a taker buying Up is the same
    # event as a taker selling Down. Kept as a DISTINCT family from the
    # tape-derived flow_* fields; the two are never merged into one number.
    #   depletion at the UP ASK  -> taker BUY of Up        -> up pressure
    #   depletion at the UP BID  -> taker SELL of Up       -> down pressure
    s["bookflow_up_pressure"] = None
    s["bookflow_dn_pressure"] = None
    s["bookflow_up_share"] = None
    if prev_decision:
        def _consumed(px_key, q_key, improving):
            pp, pq = prev_decision.get(px_key), prev_decision.get(q_key)
            cp, cq = s.get(px_key), s.get(q_key)
            if pp is None or pq is None or cp is None:
                return None
            if abs(cp - pp) < 1e-9:
                return max(0.0, pq - cq) if cq is not None else 0.0
            # the level moved. `improving` means the move is AWAY from aggression
            # (new liquidity inside), which is not consumption.
            moved_through = (cp < pp - 1e-9) if improving else (cp > pp + 1e-9)
            return pq if moved_through else 0.0
        upp = _consumed("up_ask", "up_ask_q", improving=False)   # ask rose => lifted
        dnp = _consumed("up_bid", "up_bid_q", improving=True)    # bid fell => hit
        s["bookflow_up_pressure"] = upp
        s["bookflow_dn_pressure"] = dnp
        if upp is not None and dnp is not None:
            tot2 = upp + dnp
            s["bookflow_up_share"] = (upp / tot2) if tot2 else None

    # ---- L5 observed inventory this window
    ub, uq = _basis(l5_fills, True)
    db, dq = _basis(l5_fills, False)
    s["l5_obs_up_qty"], s["l5_obs_dn_qty"] = uq, dq
    s["l5_obs_up_basis"], s["l5_obs_dn_basis"] = ub, db
    s["l5_obs_matched"] = min(uq, dq)
    s["l5_obs_residual"] = uq - dq
    s["l5_obs_imbalance"] = (abs(uq - dq) / (uq + dq)) if (uq + dq) else 0.0
    s["l5_obs_pair_cost"] = (ub + db) if (ub is not None and db is not None) else None
    if l5_fills:
        prev = l5_fills[-1]
        run = 0
        for x in reversed(l5_fills):
            if x["up"] == prev["up"]:
                run += 1
            else:
                break
        s["l5_prev_up"] = 1 if prev["up"] else 0
        s["l5_prev_px"] = prev["p"]
        s["l5_run"] = run
        s["l5_dt_since"] = now - prev["t"]
    else:
        s["l5_prev_up"] = s["l5_prev_px"] = s["l5_run"] = s["l5_dt_since"] = None

    # ---- our own shadow inventory (economics only)
    sub, suq = _basis(shadow_fills, True)
    sdb, sdq = _basis(shadow_fills, False)
    s["shadow_up_qty"], s["shadow_dn_qty"] = suq, sdq
    s["shadow_matched"] = min(suq, sdq)
    s["shadow_residual"] = suq - sdq
    s["shadow_pair_cost"] = (sub + sdb) if (sub is not None and sdb is not None) else None
    s["shadow_up_basis"], s["shadow_dn_basis"] = sub, sdb
    return s
