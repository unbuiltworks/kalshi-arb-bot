"""CHANNEL_A forensics: every passive action in the four NEVER_LOCKED windows.

Read-only. Classifies each passive acquisition by PRE-TRADE state only -- no
settlement outcome, no future price, no future fill.

The two pre-trade quantities that turn out to matter, both computable before the
order is placed:

  MARGINAL FLOOR EFFECT. The settlement-independent floor is
  min(q_up,q_down) - cost - fees. A clip of n at price p on the DEFICIENT side
  raises min by n and costs n*p, so it moves the floor by +n*(1-p) > 0 ALWAYS.
  The same clip on the SURPLUS or FLAT side leaves min alone and moves the floor
  by -n*p < 0 ALWAYS. So "floor-increasing" and "floor-decreasing" is not a
  judgement call, it is an identity of the book state.

  BASIS DISPLACEMENT. For a floor-decreasing add, whether the fill price is below
  the held side's average basis (averaging DOWN, which lowers basis and makes a
  later theta-clearing completion cheaper) or above it (averaging UP, which
  raises basis and can put completion out of reach at any theta).
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_completion_shadow as S                       # noqa: E402
from scripts.audit_economic_complete import load, USABLE          # noqa: E402

THETA, VARIANT, FILL = D("0.96"), "B", "queue_aware"
NEVER_LOCKED = ("1915z", "1930z", "2045z", "2245z")
OUT = "analysis/l5/p1/p1_channel_a_forensics.json"


def forensics(rows, clip, winner):
    """Replay the acquisition path recording pre-trade state at each action."""
    bk = S.Book()
    qstate, ladder, out = {}, {}, []
    blocked_until, need_flat = 0.0, False
    max_clip_seen = D(0)
    for row in rows:
        if row.get("kind") == "book":
            ladder = row
            continue
        if row.get("kind") != "bbo":
            continue
        if str(row.get("state") or "") != "MARKET_STATE_OPEN":
            continue
        now = S._epoch(row.get("t"))
        # ---- completion, exactly as the frozen arm does it
        r = bk.residual()
        ec_available = False
        if r != 0:
            held = "up" if r > 0 else "down"
            comp = S.complement(held)
            lv = S.buy_levels(ladder, comp) if ladder else []
            basis = bk.avg(held)
            n, cost, fee, _dl = (S.marginal_complete(lv, abs(r), basis, THETA)
                                 if lv else (D(0), D(0), D(0), False))
            ec_available = n > 0
            if n > 0:
                bk.add(comp, n, cost, maker=False, fee=fee)
                bk.locked += n * D(1) - n * basis - cost
                bk.gross_margin += n * D(1) - n * basis - cost
                bk.q[held] -= n
                bk.c[held] -= n * basis
                bk.q[comp] -= n
                bk.c[comp] -= cost
                bk.completions += 1
                bk.completed_qty += n
                if VARIANT == "B":
                    blocked_until = now + 60.0
        if need_flat and bk.residual() != 0:
            continue
        need_flat = False
        for side in ("up", "down"):
            if VARIANT == "B" and now < blocked_until:
                rr = bk.residual()
                if rr != 0 and ((rr > 0) == (side == "up")):
                    continue
            qty, px = S.maker_fill(row, side, clip, fill_model=FILL,
                                   ladder=ladder or None, qstate=qstate)
            if qty <= 0 or px is None:
                continue
            rr = bk.residual()
            signed = qty if side == "up" else -qty
            after = rr + signed
            if rr != 0 and abs(after) > abs(rr):
                mc = max_clip_seen if max_clip_seen > qty else qty
                if abs(after) > D(2) * mc:
                    continue
            # ---------- PRE-TRADE STATE, recorded before the add ----------
            floor_before = S.settlement_independent_floor(bk)
            held_b = "up" if rr > 0 else ("down" if rr < 0 else None)
            deficient = (held_b is not None and side != held_b)
            basis_b = bk.avg(side)
            # the identity: deficient side raises the floor, surplus side lowers it
            d_floor = (qty * (D(1) - px)) if deficient else (-qty * px)
            own_now = S.live_touch_own(row, side)
            bk.add(side, qty, qty * px, maker=True)
            if qty > max_clip_seen:
                max_clip_seen = qty
            out.append({
                "t": row.get("t"), "side": side, "px": px, "qty": qty,
                "inv_before": {"up": bk.q["up"] - (qty if side == "up" else D(0)),
                               "down": bk.q["down"] - (qty if side == "down" else D(0))},
                "inv_after": {"up": bk.q["up"], "down": bk.q["down"]},
                "imbalance_before": rr,
                "held_before": held_b,
                "on_deficient_side": deficient,
                "floor_before": floor_before,
                "marginal_floor_effect": d_floor,
                "floor_after": S.settlement_independent_floor(bk),
                "basis_before_this_side": basis_b,
                "basis_displacement": (px - basis_b) if basis_b > 0 else None,
                "averaging": ("DOWN" if basis_b > 0 and px < basis_b
                              else ("UP" if basis_b > 0 and px > basis_b
                                    else "NEW")),
                "held_side_move_since_basis": (
                    (own_now - bk.avg(held_b)) if (held_b and own_now is not None
                                                   and bk.avg(held_b) > 0) else None),
                "ec_opportunity_available_this_row": ec_available,
            })
    return out, bk
