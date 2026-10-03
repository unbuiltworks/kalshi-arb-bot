"""Shadow order lifecycle, lifetime arms R1-R4, ghosts, and fill evidence.

Hard rules, all enforced here:
  * no touch = fill;
  * no invented queue -- queue ahead is initialized from actual L2 and an
    unknown queue can never fill;
  * displaced displayed size is NEVER treated as execution on its own, because
    a cancellation upstream of us is indistinguishable from a trade;
  * repricing is explicit: CANCELLED then a new PLACED_SHADOW, both preserved;
  * a cancelled order is preserved as a GHOST and keeps being observed, solely
    to answer "would this cancelled quote later have received a defensible
    fill?". A ghost never touches inventory or economics;
  * an observation gap censors queue state: it becomes UNKNOWN, never carried
    across as if continuously observed.
"""
from __future__ import annotations
import itertools
from . import config as C

_seq = itertools.count(1)

PLACED, RESTING, PARTIAL, FULL = ("PLACED_SHADOW", "RESTING",
                                  "PARTIAL_SHADOW_FILL", "FULL_SHADOW_FILL")
CANCELLED, EXPIRED, REPRICED = "CANCELLED", "EXPIRED", "REPRICED"
CENSORED = "CENSORED_OBSERVATION_GAP"
UNKNOWN = "UNKNOWN"
TRADE_THROUGH, QUEUE_CONSUMED, DEPLETION = ("TRADE_THROUGH", "QUEUE_CONSUMED",
                                            "DEPLETION_CORROBORATED")


def place(now, slug, side_model, price_arm, lifetime_arm, side, q, state):
    return {
        "oid": f"SH{next(_seq):07d}", "slug": slug,
        "side_model": side_model, "price_arm": price_arm,
        "lifetime_arm": lifetime_arm, "side": side,
        "price": q["price"], "size": q["size"],
        "queue_ahead_at_place": q["queue_ahead"],
        "bid_at_place": q["bid_at_place"], "ask_at_place": q["ask_at_place"],
        "pair_cost_at_place": state.get("l5_obs_pair_cost"),
        "placed_t": now, "state": RESTING, "is_ghost": False,
        "events": [(now, PLACED)],
        "filled_q": 0.0, "vol_at_level": 0.0, "vol_through": 0.0,
        "first_fill_t": None, "full_fill_t": None, "evidence": None,
        "queue_censored": False,
        "bid_at_fill": None, "ask_at_fill": None,
        "cancel": None, "ghost_outcome": None,
    }


def _consumes_us(o, tr):
    """We are always BUYING, so we rest on the bid side of our outcome. A taker
    SELL of that outcome executes against resting bids, i.e. against us."""
    return tr["up"] == (o["side"] == "up") and (not tr["buy"])


def _accrue(o, trades):
    for tr in trades:
        if tr["t"] <= o["placed_t"] or not _consumes_us(o, tr):
            continue
        if tr["p"] < o["price"] - 1e-9:
            o["vol_through"] += tr["q"]
        elif abs(tr["p"] - o["price"]) < 1e-9:
            o["vol_at_level"] += tr["q"]


def _evidence(o):
    """The frozen hierarchy. Returns (evidence, fillable_qty) or (None, 0)."""
    if o["queue_censored"] or o["queue_ahead_at_place"] is None:
        return None, 0.0                      # unknown queue can never fill
    if o["vol_through"] > 0:
        return TRADE_THROUGH, o["size"]
    if o["vol_at_level"] > o["queue_ahead_at_place"] + 1e-9:
        return QUEUE_CONSUMED, o["vol_at_level"] - o["queue_ahead_at_place"]
    return None, 0.0


def observe(o, now, trades, bk):
    """Advance one order (live or ghost) against subsequent evidence."""
    terminal = (FULL, EXPIRED, REPRICED, CANCELLED)
    if o["state"] in terminal and not o["is_ghost"]:
        return o
    _accrue(o, trades)
    ev, avail = _evidence(o)
    if ev:
        got = max(0.0, min(o["size"], avail))
        if got > o["filled_q"]:
            o["filled_q"] = got
            o["evidence"] = ev
            if o["first_fill_t"] is None:
                o["first_fill_t"] = now
                o["bid_at_fill"] = bk["bids"][0]["p"] if bk["bids"] else None
                o["ask_at_fill"] = bk["asks"][0]["p"] if bk["asks"] else None
            if o["is_ghost"]:
                # A ghost is only ever a counterfactual answer. It is NEVER
                # promoted to a fill and never reaches inventory or economics.
                o["ghost_outcome"] = f"WOULD_HAVE_FILLED:{ev}"
            else:
                o["state"] = FULL if got >= o["size"] - 1e-9 else PARTIAL
                o["events"].append((now, o["state"]))
                if o["state"] == FULL:
                    o["full_fill_t"] = now
    elif o["vol_at_level"] > 0 and o["evidence"] is None:
        # same-price prints that do NOT exhaust the observed queue, and pure
        # book depletion, are explicitly NOT a fill
        o["evidence"] = UNKNOWN
    return o


def censor(o, now, reason="observation_gap"):
    """A restart or a read gap destroys queue continuity. Say so; never pretend
    the queue position survived."""
    o["queue_censored"] = True
    o["queue_ahead_at_place"] = None
    o["events"].append((now, CENSORED))
    if o["state"] in (RESTING, PARTIAL):
        o["state"] = CENSORED
    o["cancel"] = (o["cancel"] or {}) | {"censor_reason": reason}
    return o


def should_reprice(o, now, pred_side, s, passive_ok, rule_ok):
    """Lifetime-arm decision. Returns (reprice: bool, reason: str|None)."""
    age = now - o["placed_t"]
    arm = o["lifetime_arm"]
    if pred_side is not None and pred_side != o["side"]:
        return True, "SIDE_REVERSAL"
    if not passive_ok:
        return True, "NO_LONGER_PASSIVE"
    if arm == "R1_STICKY":
        return False, None                       # only fill / invalidation / window end
    if arm == "R2_MATERIAL_CHANGE":
        if not rule_ok:
            return True, "PAIR_COST_RULE_VIOLATION"
        return False, None
    if arm in C.R3_DURATIONS:
        if age >= C.R3_DURATIONS[arm]:
            return True, f"FIXED_REST_ELAPSED_{C.R3_DURATIONS[arm]:g}s"
        return False, None
    if arm == "R4_REPRICE_5S":
        return True, "CONTROL_EVERY_DECISION"
    return False, None


def cancel(o, now, reason, s, bk, make_ghost=True):
    """Explicit cancellation with the full required record, plus a ghost.

    IDEMPOTENT (defect D5): an order that is already terminal is never
    cancelled again, so cancellation and ghost counts cannot be inflated by
    re-visiting it.
    """
    if o["state"] not in (RESTING, PARTIAL):
        return o, None
    rem_q = None
    rows = bk["bids"] if o["side"] in ("up", "dn") else bk["bids"]
    for r in rows:
        if abs(r["p"] - o["price"]) < 1e-9:
            rem_q = r["q"]
            break
    o["cancel"] = {
        "reason": reason, "cancel_t": now,
        "placed_t": o["placed_t"], "order_age_s": now - o["placed_t"],
        "queue_ahead_at_place": o["queue_ahead_at_place"],
        "observable_queue_before_cancel": rem_q,
        "vol_at_level_so_far": o["vol_at_level"],
        "vol_through_so_far": o["vol_through"],
        "bid_at_place": o["bid_at_place"], "ask_at_place": o["ask_at_place"],
        "bid_at_cancel": s.get(f"{o['side']}_bid"),
        "ask_at_cancel": s.get(f"{o['side']}_ask"),
        "price_move_bid": (None if (s.get(f"{o['side']}_bid") is None
                                    or o["bid_at_place"] is None)
                           else round(s[f"{o['side']}_bid"] - o["bid_at_place"], 4)),
        "side_model_state": {"side_model": o["side_model"],
                             "p_up": s.get("_pred_p_up"),
                             "confidence": s.get("_pred_conf")},
        "pair_cost_at_place": o["pair_cost_at_place"],
        "pair_cost_at_cancel": s.get("l5_obs_pair_cost"),
    }
    o["state"] = REPRICED if reason not in ("WINDOW_END",) else CANCELLED
    o["events"].append((now, o["state"]))
    ghost = None
    if make_ghost:
        ghost = dict(o)
        ghost["oid"] = o["oid"] + "G"
        ghost["is_ghost"] = True
        ghost["parent_oid"] = o["oid"]
        ghost["ghost_from_t"] = now
        ghost["ghost_observe_until"] = now + C.GHOST_OBSERVE_S
        ghost["state"] = "GHOST_OBSERVING"
        ghost["events"] = list(o["events"]) + [(now, "GHOST_OBSERVING")]
        ghost["ghost_outcome"] = None
    return o, ghost
