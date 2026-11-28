"""Directional inventory allocation: market-agnostic, pure, no venue imports.

WHAT IT DECIDES. Given an outside probability for the YES outcome (a band
lo <= p <= hi, e.g. the international mid and its recent range), the
current two-sided inventory, and the bids the quoting engine already wants
to rest, it decides for every OPENING or SAME-SIDE ADDITION bid:

  * whether it is still worth adding (marginal expected value per contract
    after adverse selection, fees and an inventory-concentration charge);
  * how many contracts (the largest size whose average marginal EV stays
    above `min_ev`; never more than the engine asked for);
  * where it rests (the side the probability does not favour can be moved
    further back; the favoured side is never moved closer);
  * the order in which sides are replenished (highest marginal EV first).

COMPLETIONS (the lighter side, which reduce the unpaired position) are never
refused, resized or re-priced here: pair-cost protections stay where they
are, in the engine. They get a risk-reduction credit for ordering only.

WHAT IT DOES NOT DO.
  * No probability, no tilt: with `prob=None` every bid passes unchanged
    (`why="no_probability"`). The favourite is never inferred from the
    traded venue's own price -- buying the price-favourite was tested and is
    negative after weighting (bonereaper_recent/FINDINGS.md, 2026-11-20).
  * No fixed dominance ratio, no buying because a side is cheap, no taker
    orders, no selling, no size above what the engine and its caps allowed.
  * No rewards inside unless the caller passes a per-contract reward it has
    already calibrated; rewards never rescue a negative trading edge
    (`trade_edge_negative` is checked before the reward is added).

THE ECONOMICS, per contract of side s at price px with probability band
(lo, hi), YES-probability mid p:
    fair_s       = lo        (s = YES)  |  1 - hi   (s = NO)   [conservative]
    edge         = fair_s - px - fill_loss - fee
    N            = pos[s] - pos[other]     (signed: > 0 means s is heavier)
    var          = p_s (1 - p_s),  p_s = p (YES) or 1 - p (NO)
    risk(q)      = risk_aversion * var * ((N + q)^2 - N^2) / q
    ev(q)        = edge + reward - risk(q)
Adding to the heavier side has risk(q) > 0 and growing with N; adding to the
lighter side has risk(q) < 0 (it reduces the position's variance), so cheaper
opposite-side inventory is bought when its edge plus that reduction pays.
The favoured side is the one with the larger edge; it changes only when the
other side's edge exceeds it by `hysteresis` (so a fair value oscillating
around a boundary does not flip allocation every pass).

Reuse: the crypto controller can call `allocate` with its own probability
band (e.g. a spot-derived model) and its own `fill_loss`; nothing here knows
what market it is pricing.
"""
from __future__ import annotations

from decimal import Decimal as D, ROUND_FLOOR
from typing import Any, Dict, List, Mapping, Optional

ONE, ZERO = D(1), D(0)

DEFAULTS: Dict[str, Any] = {
    "risk_aversion": "0.005",     # $ per contract^2 of YES-variance
    "min_ev": "0",                # marginal EV per contract must exceed this
    "hysteresis": "0.01",         # favoured side flips only past this edge gap
    "depth_unfavoured_ticks": 1,  # extra ticks back for the other side
    "touch_edge": "0.02",         # unfavoured side keeps its place above this
    "fee": "0",                   # per contract (maker on PMUS: 0)
    # False: an OPENING bid on a flat side (both sides empty, or this side
    # empty) passes unchanged, so both sides still open and pair; only
    # same-side additions and bids behind them are judged.
    "judge_openings": True,
    # FAVOURED-SIDE SIZING (size_hint): the favoured side may ask for up to
    # `up_mult` x the engine's clip when its conservative edge is at least
    # `upsize_edge`. The engine's own caps still bound the result; 1 = off.
    "up_mult": "1",
    "upsize_edge": "0.03",
    # FAVOURED-SIDE DEPTH (ladder_hint): extra ladder levels for the
    # favoured side under the same condition; the engine's worst-case caps
    # still decide whether each level fits. 0 = off.
    "extra_levels": 0,
}


def _d(x: Any) -> D:
    return x if isinstance(x, D) else D(str(x))


def _cfg(cfg: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg or {}).items() if v is not None})
    return out


def probability(p: Any, lo: Any = None, hi: Any = None,
                source: str = "", age_s: Any = None,
                max_age_s: Any = None,
                drift_per_s: Any = 0) -> Optional[Dict[str, Any]]:
    """A probability band, or None when it cannot be used. lo/hi default to
    p; anything outside (0, 1) or out of order is refused (None).

    REFERENCE FRESHNESS: with `age_s`, a reference older than `max_age_s`
    is refused (None), and a younger one has its band widened by
    `drift_per_s` x age on each side (how far the probability can have moved
    since it was read), clipped inside (0, 1). A stale fair value can
    therefore only make a bid harder to justify, never easier."""
    try:
        p_, lo_, hi_ = _d(p), _d(p if lo is None else lo), \
            _d(p if hi is None else hi)
    except Exception:                                     # noqa: BLE001
        return None
    if not (ZERO < lo_ <= p_ <= hi_ < ONE):
        return None
    age = None
    if age_s is not None:
        try:
            age = max(ZERO, _d(age_s))
        except Exception:                                 # noqa: BLE001
            return None
        if max_age_s is not None and age > _d(max_age_s):
            return None
        w = _d(drift_per_s or 0) * age
        eps = D("0.0001")
        lo_ = max(eps, lo_ - w)
        hi_ = min(ONE - eps, hi_ + w)
    return {"p": p_, "lo": lo_, "hi": hi_, "source": source,
            "age_s": None if age is None else str(age)}


def fair_for(prob: Mapping[str, Any], side: str, conservative: bool = True
             ) -> D:
    if side == "YES":
        return prob["lo"] if conservative else prob["p"]
    return ONE - (prob["hi"] if conservative else prob["p"])


def assess(*, prob: Mapping[str, Any], side: str, px: Any, qty: Any,
           pos: Mapping[str, Any], fill_loss: Any = 0, reward: Any = 0,
           min_qty: Any = 1, step: Any = None,
           cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Marginal economics of buying up to `qty` of `side` at `px` on top of
    `pos`. Returns the size it supports (0 = refuse) and its terms."""
    c = _cfg(cfg)
    other = "NO" if side == "YES" else "YES"
    p_, q0, mq = _d(px), _d(qty), _d(min_qty)
    st = _d(step) if step is not None else mq
    n = _d(pos.get(side) or 0) - _d(pos.get(other) or 0)
    p_s = prob["p"] if side == "YES" else ONE - prob["p"]
    var = p_s * (ONE - p_s)
    lam = _d(c["risk_aversion"])
    edge = fair_for(prob, side) - p_ - _d(fill_loss) - _d(c["fee"])
    rw = max(ZERO, _d(reward))
    min_ev = _d(c["min_ev"])

    def risk(q: D) -> D:
        return lam * var * ((n + q) ** 2 - n ** 2) / q if q > 0 else ZERO

    def ev(q: D) -> D:
        return edge + rw - risk(q)

    out = {"side": side, "px": str(p_), "asked_qty": str(q0),
           "fair": str(fair_for(prob, side)), "edge": str(edge),
           "reward": str(rw), "net_before": str(n), "var": str(var)}
    if edge < ZERO and n >= ZERO:
        # adding to the flat/heavier side with a negative trading edge:
        # no reward or anything else may carry it
        out.update(qty="0", why="trade_edge_negative",
                   ev_per_contract=str(ev(max(q0, mq))),
                   risk_per_contract=str(risk(max(q0, mq))))
        return out
    q = q0
    while q > mq and ev(q) <= min_ev:
        q = max(mq, (q - st).quantize(st, rounding=ROUND_FLOOR)
                if st > 0 else mq)
    if ev(q) <= min_ev:
        out.update(qty="0", why="marginal_ev_not_positive",
                   ev_per_contract=str(ev(q)), risk_per_contract=str(risk(q)))
        return out
    out.update(qty=str(q), why=None if q == q0 else "resized",
               ev_per_contract=str(ev(q)), risk_per_contract=str(risk(q)))
    return out


def favoured(prob: Mapping[str, Any], px: Mapping[str, Any],
             prev: Optional[str] = None,
             cfg: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """The side whose conservative fair exceeds its own price by more.
    `px` = {"YES": price, "NO": price} (each side's best bid, or the bid it
    would rest at). Keeps `prev` unless the other side leads by
    `hysteresis`. None when neither price is known."""
    c = _cfg(cfg)
    e = {s: fair_for(prob, s) - _d(px[s]) for s in ("YES", "NO")
         if px.get(s) is not None}
    if not e:
        return prev
    lead = max(e, key=lambda s: e[s])
    if prev in e and lead != prev and \
            e[lead] - e[prev] < _d(c["hysteresis"]):
        return prev
    return lead


def allocate(*, prob: Optional[Mapping[str, Any]],
             want: Mapping[str, Any], pos: Mapping[str, Any],
             tick: Any, min_qty: Any = 1,
             touch: Optional[Mapping[str, Any]] = None,
             fill_loss: Optional[Mapping[str, Any]] = None,
             reward: Optional[Mapping[str, Any]] = None,
             prev_favoured: Optional[str] = None,
             cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Apply the allocation to the engine's wanted bids.

    `want`: the quoting engine's output, {"YES": {"px","qty","role",...} |
    None, "NO": ..., "ladder": [{"side","px","qty","level"}, ...] (optional),
    "why_<SIDE>": reason (optional)}. Returned as a NEW dict of the same
    shape plus "directional": the decision record. Nothing is ever added:
    a side the engine did not want stays absent.

    `touch`: each side's best bid (for the favoured-side test); defaults to
    the wanted prices. `fill_loss` / `reward`: per side, per contract."""
    c = _cfg(cfg)
    out: Dict[str, Any] = {k: (dict(v) if isinstance(v, Mapping) else v)
                           for k, v in want.items() if k != "ladder"}
    ladder = [dict(x) for x in (want.get("ladder") or [])]
    rec: Dict[str, Any] = {"active": prob is not None, "sides": {}}
    if prob is None:
        rec["why"] = "no_probability"
        if ladder:
            out["ladder"] = ladder
        out["directional"] = rec
        return out
    t = _d(tick)
    fl = {s: _d((fill_loss or {}).get(s) or 0) for s in ("YES", "NO")}
    rw = {s: _d((reward or {}).get(s) or 0) for s in ("YES", "NO")}
    tp = dict(touch or {})
    for s in ("YES", "NO"):
        if tp.get(s) is None and isinstance(want.get(s), Mapping):
            tp[s] = want[s]["px"]
    fav = favoured(prob, tp, prev_favoured, c)
    rec.update(favoured=fav, prob={k: str(v) for k, v in prob.items()
                                   if k != "source"},
               source=prob.get("source"))
    held = {s: _d(pos.get(s) or 0) for s in ("YES", "NO")}
    priority: List[tuple] = []
    for s in ("YES", "NO"):
        w = out.get(s)
        if not isinstance(w, Mapping):
            continue
        if w.get("role") == "complete":
            a = assess(prob=prob, side=s, px=w["px"], qty=w["qty"],
                       pos=held, fill_loss=fl[s], reward=rw[s],
                       min_qty=min_qty, cfg=c)
            rec["sides"][s] = dict(a, role="complete", qty=str(w["qty"]),
                                   why="completion_unchanged")
            priority.append((_d(a["ev_per_contract"]), s))
            continue
        if not c.get("judge_openings", True) and held[s] == ZERO:
            rec["sides"][s] = {"role": w.get("role"),
                               "why": "opening_not_judged"}
            priority.append((ZERO, s))
            continue
        px = _d(w["px"])
        moved = 0
        if s != fav and int(c["depth_unfavoured_ticks"]) > 0:
            e = fair_for(prob, s) - px - fl[s]
            if e < _d(c["touch_edge"]):
                moved = int(c["depth_unfavoured_ticks"])
                px = px - t * moved
        if px <= ZERO:
            out[s] = None
            out["why_" + s] = "directional_no_price"
            rec["sides"][s] = {"why": "directional_no_price"}
            continue
        a = assess(prob=prob, side=s, px=px, qty=w["qty"], pos=held,
                   fill_loss=fl[s], reward=rw[s], min_qty=min_qty, cfg=c)
        a.update(role=w.get("role"), depth_moved_ticks=moved,
                 favoured=(s == fav))
        rec["sides"][s] = a
        if _d(a["qty"]) <= ZERO:
            out[s] = None
            out["why_" + s] = "directional:" + str(a["why"])
            continue
        out[s] = dict(w, px=px if moved else w["px"], qty=D(a["qty"]))
        priority.append((_d(a["ev_per_contract"]), s))
    if ladder:
        kept, refused = allocate_ladder(prob=prob, ladder=ladder, front=out,
                                        pos=held, min_qty=min_qty,
                                        fill_loss=fl, reward=rw, cfg=c)
        out["ladder"] = kept
        if refused:
            rec["ladder_refused"] = refused
    rec["order"] = [s for _e, s in sorted(priority, key=lambda x: -x[0])]
    out["directional"] = rec
    return out


def allocate_ladder(*, prob: Optional[Mapping[str, Any]], ladder: Any,
                    front: Mapping[str, Any], pos: Mapping[str, Any],
                    min_qty: Any = 1,
                    fill_loss: Optional[Mapping[str, Any]] = None,
                    reward: Optional[Mapping[str, Any]] = None,
                    cfg: Optional[Mapping[str, Any]] = None) -> tuple:
    """(kept, refused) ladder levels behind level 0. Each level is judged on
    the inventory it would hold if every bid in front of it on its side
    (level 0 and shallower levels) filled. A level behind a refused or
    absent front bid is dropped, except behind a COMPLETION, where deeper
    levels are ordinary openings and are judged on their own. With no
    probability the ladder is returned unchanged."""
    lv_all = [dict(x) for x in (ladder or [])]
    if prob is None:
        return lv_all, []
    c = _cfg(cfg)
    fl = {s: _d((fill_loss or {}).get(s) or 0) for s in ("YES", "NO")}
    rw = {s: _d((reward or {}).get(s) or 0) for s in ("YES", "NO")}
    held = {s: _d(pos.get(s) or 0) for s in ("YES", "NO")}
    ahead = {}
    for s in ("YES", "NO"):
        f = front.get(s)
        add = _d(f["qty"]) if isinstance(f, Mapping) and \
            f.get("role") != "complete" else ZERO
        ahead[s] = held[s] + add
    kept, refused = [], []
    for lv in sorted(lv_all, key=lambda x: (x["side"],
                                            int(x.get("level", 1)))):
        s = lv["side"]
        f = front.get(s)
        if not isinstance(f, Mapping):
            refused.append({"side": s, "px": str(lv["px"]),
                            "why": "front_bid_refused"})
            continue
        pos_now = dict(held)
        pos_now[s] = ahead[s]
        a = assess(prob=prob, side=s, px=lv["px"], qty=lv["qty"],
                   pos=pos_now, fill_loss=fl[s], reward=rw[s],
                   min_qty=min_qty, cfg=c)
        if _d(a["qty"]) <= ZERO:
            refused.append({"side": s, "px": str(lv["px"]),
                            "why": a["why"]})
            continue
        kept.append(dict(lv, qty=D(a["qty"])))
        ahead[s] += D(a["qty"])
    return kept, refused


def _favoured_with_edge(prob, touch, fill_loss, prev_favoured, c):
    fav = favoured(prob, touch, prev_favoured, c)
    if fav is None or touch.get(fav) is None:
        return None
    fl = _d((fill_loss or {}).get(fav) or 0)
    e = fair_for(prob, fav) - _d(touch[fav]) - fl
    return fav if e >= _d(c["upsize_edge"]) else None


def ladder_hint(*, prob: Optional[Mapping[str, Any]],
                touch: Mapping[str, Any],
                fill_loss: Optional[Mapping[str, Any]] = None,
                prev_favoured: Optional[str] = None,
                cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, int]:
    """Extra ladder levels per side: `extra_levels` for the favoured side
    when its conservative edge at the touch is at least `upsize_edge`, 0
    otherwise (and always 0 with no probability)."""
    c = _cfg(cfg)
    out = {"YES": 0, "NO": 0}
    n = int(c.get("extra_levels") or 0)
    if prob is None or n <= 0:
        return out
    fav = _favoured_with_edge(prob, touch, fill_loss, prev_favoured, c)
    if fav:
        out[fav] = n
    return out


def size_hint(*, prob: Optional[Mapping[str, Any]], touch: Mapping[str, Any],
              clip: Any, fill_loss: Optional[Mapping[str, Any]] = None,
              prev_favoured: Optional[str] = None,
              cfg: Optional[Mapping[str, Any]] = None,
              max_qty: Any = None) -> Dict[str, Any]:
    """Per-side clip the engine may ASK the quoting rule for, before its
    caps: the favoured side gets `up_mult` x clip when its conservative edge
    at the touch is at least `upsize_edge`; every other case is the clip
    unchanged. With no probability, or up_mult 1, nothing changes. The
    quoting rule's residual, per-market and campaign caps still apply to the
    result, and `allocate` still judges every contract of it."""
    c = _cfg(cfg)
    q = _d(clip)
    out = {"YES": q, "NO": q}
    if prob is None or _d(c["up_mult"]) <= ONE:
        return out
    fav = _favoured_with_edge(prob, touch, fill_loss, prev_favoured, c)
    if fav:
        up = (q * _d(c["up_mult"])).to_integral_value(rounding=ROUND_FLOOR)
        if max_qty is not None:
            up = min(up, _d(max_qty))   # the per-order maximum is a limit
        out[fav] = max(q, up)
    return out
