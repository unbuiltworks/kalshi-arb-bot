"""HABITAT EVIDENCE: is this venue/window a place the L5 policy can work?

STRICTLY A MEASUREMENT. Nothing here is reachable from the controller, the
pricing, the sizing or any bound. It exists because CONTINUOUS-4 through -8
produced two fills in five windows and the only explanations available were
reconstructions. The owner's instruction is explicit: collect the evidence
first, do not tune the live controller from it yet.

THE TWO MODELS THIS SERVES, KEPT APART:

  A. L5 FIDELITY     "what would L5 do in this state?"   -- NOT touched here
  B. VENUE/ECONOMIC  "does that behaviour pay here?"     -- this feeds B only

A poor Gemini outcome is evidence about B. It is never a reason to change A.

WHAT IT MEASURES, and where each number comes from:

  spread                  presented book (the state the controller decided on)
  touch depth             contract ladder, our own resting size included and
                          FLAGGED, because "10 resting" means something very
                          different when 0.01 of it is ours
  depth 1-3 ticks away    cumulative ladder size within N ticks of the touch
  trades / flow           GET /v1/trades/{symbol}, PUBLIC -- prints, volume
                          and direction over the window
  quote resting time      how long each of our rungs sat before it moved
  repricing frequency     how often the desired surface moved
  fill conversion         resting-seconds per fill, and fills per rung placed
  adverse movement        the mid after a fill, sampled forward
  TTE                     the venue's own clock
  complement opportunity  what the other leg cost while we held one

NOTHING HERE IS REALIZED. Mid-prices, spreads and unrealized marks are
measurements of a market, not of money.
"""
from __future__ import annotations

from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

TRADES_PATH = "/v1/trades"

#: how far from the touch the cumulative ladder is summarised
TICK_OFFSETS = (1, 2, 3)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def public_flow(symbol: str, *, timeout: float = 2.5,
                limit: int = 200) -> Dict[str, Any]:
    """The venue's PUBLIC print tape for one contract.

    A different question from `/v1/mytrades`: that one is what WE executed,
    this is what ANYONE executed. Flow is the thing a passive quote needs and
    cannot create, so it is the first number to look at when a maker surface
    rests untouched for four minutes.

    Never raises. Unreadable is a result with its reason.
    """
    import time as _t

    from src.l5_shadow import venues as V
    t0 = _t.time()
    out: Dict[str, Any] = {
        "source": "GET %s%s/{symbol} (PUBLIC)" % (V.GEMINI, TRADES_PATH),
        "instrumentSymbol": symbol,
        "labels": ("public prints, not our executions. EVIDENCE ONLY: no "
                   "price, size or decision is taken from this."),
    }
    r = V.get("%s%s/%s?limit_trades=%d" % (V.GEMINI, TRADES_PATH, symbol,
                                           limit), timeout=timeout)
    out["rtt_s"] = r.get("rtt_s")
    if not r.get("ok"):
        out.update({"readable": False, "why": r.get("err")})
        return out
    try:
        import json as _j
        rows = _j.loads(r["body"])
    except ValueError:
        out.update({"readable": False, "why": "unparseable trade tape"})
        return out
    if not isinstance(rows, list):
        out.update({"readable": False, "why": "trade tape was not a list"})
        return out

    prints: List[Dict[str, Any]] = []
    for t in rows:
        if not isinstance(t, Mapping):
            continue
        px, qty = _dec(t.get("price")), _dec(t.get("amount"))
        if px is None or qty is None:
            continue
        prints.append({"tid": t.get("tid"), "utc_ms": t.get("timestampms"),
                       "price": str(px), "qty": str(qty),
                       "side": str(t.get("type") or "").lower()})
    vol = sum((D(p["qty"]) for p in prints), D("0"))
    buys = [p for p in prints if p["side"] == "buy"]
    sells = [p for p in prints if p["side"] == "sell"]
    out.update({
        "readable": True, "n_prints": len(prints),
        "volume": str(vol),
        "buy_prints": len(buys), "sell_prints": len(sells),
        "buy_volume": str(sum((D(p["qty"]) for p in buys), D("0"))),
        "sell_volume": str(sum((D(p["qty"]) for p in sells), D("0"))),
        "newest_utc_ms": (prints[0]["utc_ms"] if prints else None),
        "oldest_utc_ms": (prints[-1]["utc_ms"] if prints else None),
        "prints": prints[:40],
    })
    return out


def ladder_shape(depth: Any, *, our_prices: Sequence[str] = ()
                 ) -> Dict[str, Any]:
    """Touch size and cumulative size 1-3 ticks behind it, per side.

    OUR OWN RESTING SIZE IS FLAGGED, NOT SUBTRACTED. In C8B the bid at our
    price read `0.01` for seven consecutive cycles -- our own order, alone.
    Reporting that as market depth would have been the most misleading number
    in the whole record, and silently removing it would be a different kind
    of lie. It is marked.
    """
    if not isinstance(depth, Mapping) or not depth.get("readable"):
        return {"readable": False,
                "why": ((depth or {}).get("why") if isinstance(depth, Mapping)
                        else "no depth read")}
    ours = {str(p) for p in (our_prices or [])}
    out: Dict[str, Any] = {"readable": True}
    for side in ("bids", "asks"):
        s = depth.get(side) or {}
        levels = s.get("levels") or []
        best = _dec(s.get("best"))
        row: Dict[str, Any] = {
            "best": s.get("best"), "best_qty": s.get("best_qty"),
            "n_levels": s.get("n_levels"),
            "touch_is_ours": bool(s.get("best") in ours),
        }
        if best is not None:
            for n in TICK_OFFSETS:
                lo = best - D("0.01") * n if side == "bids" else None
                hi = best + D("0.01") * n if side == "asks" else None
                tot = D("0")
                for lv in levels:
                    px = _dec(lv.get("price"))
                    if px is None:
                        continue
                    inside = (px >= lo) if side == "bids" else (px <= hi)
                    if inside:
                        tot += _dec(lv.get("qty")) or D("0")
                row["cum_within_%d_ticks" % n] = str(tot)
        out[side] = row
    out["labels"] = ("cumulative size is what a ceiling near the touch would "
                     "actually consume; `touch_is_ours` marks a level our own "
                     "rung is resting at.")
    return out


def cycle_sample(*, utc: str, tte_s: Any, presented: Any, depth: Any,
                 flow: Any, our_prices: Sequence[str] = (),
                 inventory: Any = None) -> Dict[str, Any]:
    """One cycle's habitat row. Pure: it reads what it is given."""
    p = presented if isinstance(presented, Mapping) else {}
    yes, no = (p.get("yes") or {}), (p.get("no") or {})
    f = flow if isinstance(flow, Mapping) else {}
    inv = inventory if isinstance(inventory, Mapping) else {}
    return {
        "utc": utc, "tte_s": tte_s,
        "book_readable": bool(p.get("readable")),
        "book_why": p.get("why"),
        "yes_bid": yes.get("bid"), "yes_ask": yes.get("ask"),
        "yes_spread": yes.get("spread"),
        "no_bid": no.get("bid"), "no_ask": no.get("ask"),
        "no_spread": no.get("spread"),
        "passive_pair_cost": p.get("passive_pair_cost"),
        "crossing_pair_cost": p.get("crossing_pair_cost"),
        "last_trade_price": p.get("last_trade_price"),
        "ladder": ladder_shape(depth, our_prices=our_prices),
        "flow": {"readable": f.get("readable"), "n_prints": f.get("n_prints"),
                 "volume": f.get("volume"), "buy_prints": f.get("buy_prints"),
                 "sell_prints": f.get("sell_prints"),
                 "newest_utc_ms": f.get("newest_utc_ms"),
                 "why": f.get("why")},
        # COMPLEMENT OPPORTUNITY: what the other leg cost while we held one.
        # Recorded whether or not anything was held, so the counterfactual is
        # available either way.
        "complement": {
            "held_leg": ("YES" if _dec(inv.get("yes_qty")) not in (None, D("0"))
                         else ("NO" if _dec(inv.get("no_qty"))
                               not in (None, D("0")) else None)),
            "yes_cost_to_join": yes.get("bid"),
            "no_cost_to_join": no.get("bid"),
            "pair_cost_if_joined": p.get("passive_pair_cost"),
        },
        "labels": ("EVIDENCE ONLY -- model B (venue/economic). Nothing here "
                   "reaches the L5 policy, the pricing or any bound."),
    }


def window_summary(samples: Sequence[Mapping[str, Any]],
                   *, rungs_placed: int = 0, fills: Sequence = (),
                   window_s: Any = None) -> Dict[str, Any]:
    """Per-window habitat aggregates, including fill conversion.

    `resting_seconds` is the sum over rungs of how long each sat, which is the
    denominator conversion actually needs: a rung that rested four minutes and
    one that rested four seconds are not one sample each.
    """
    # A REFUSED CYCLE IS NOT A NARROW SPREAD. Mixing cycles whose book the
    # controller refused into a median spread would report the venue as
    # tighter than it is, using exactly the readings that were unusable.
    readable = [s for s in samples if s.get("book_readable")]
    refused = [s for s in samples if not s.get("book_readable")]
    spreads = [_dec(s.get("yes_spread")) for s in readable]
    spreads = [x for x in spreads if x is not None]
    prints = [s["flow"].get("n_prints") for s in samples
              if isinstance(s.get("flow"), Mapping)
              and isinstance(s["flow"].get("n_prints"), int)]
    # repricing frequency: how often the quoted surface actually moved
    moved = 0
    prev = None
    for s in samples:
        cur = (s.get("yes_bid"), s.get("no_bid"))
        if prev is not None and cur != prev:
            moved += 1
        prev = cur
    n = len(samples)
    return {
        "cycles": n,
        "book_readable_cycles": len(readable),
        "book_refused_cycles": len(refused),
        "book_refused_share": (round(len(refused) / n, 3) if n else None),
        "refusal_reasons": sorted({str(s.get("book_why")) for s in refused
                                   if s.get("book_why")})[:6],
        "spread_yes": {
            "n": len(spreads),
            "min": (str(min(spreads)) if spreads else None),
            "median": (str(sorted(spreads)[len(spreads) // 2])
                       if spreads else None),
            "max": (str(max(spreads)) if spreads else None)},
        "flow": {"samples": len(prints),
                 "max_prints_seen": (max(prints) if prints else None)},
        "surface_moves": moved,
        "reprice_rate_per_cycle": (round(moved / n, 3) if n else None),
        "rungs_placed": rungs_placed,
        "fills": len(fills or []),
        "fill_conversion": {
            "fills_per_rung": (round(len(fills or []) / rungs_placed, 4)
                               if rungs_placed else None),
            "window_s": window_s,
            "why": ("resting seconds per fill is the honest denominator; "
                    "rungs placed is reported beside it because a rung that "
                    "rested four minutes and one that rested four seconds "
                    "are not one sample each")},
        "labels": ("HABITAT EVIDENCE for model B. Not a verdict, not a "
                   "tuning input, and never realized economics."),
    }
