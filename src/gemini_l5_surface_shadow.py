"""Gemini BTC05M passive QUOTE-SURFACE shadow. Read-only, zero orders, no P&L
from a theoretical fill.

OWNER 2026-09-17, after L5_CAUSAL_MECHANISM_V1: start from a SIMULTANEOUS
passive surface, not from sequential action submission. The existing
`src/gemini_l5_shadow_v2.py` is the sequential one and is left untouched; it
also books THEORETICAL fills, which is why it is not the starting point here.

A STRUCTURAL DIFFERENCE, MEASURED NOT ASSUMED. Polymarket BTC windows have TWO
outcome tokens and L5 assembles a pair by BUYING both. Gemini serves ONE symbol
per window (`...-UP`; the `-DOWN` book does not exist -- checked, not inferred).
So the Gemini analogue of a pair is a BUY at b and a SELL at s on the same
instrument, and its economics are

    effective_pair_cost = b + (1 - s)

which is below 1 exactly when s > b. BTC15m parameters are NOT ported onto
this: a 0.98 pair ceiling means something different on a book whose observed
spread is measured in tens of cents, and porting it mechanically is what the
owner ruled out.

THE EVIDENCE LADDER, four rungs, never conflated and only the last one counts:

    RESTABLE                 the quote is non-marketable now -- a legal maker
                             order, nothing more.
    book_reached_quote       the opposite touch moved to or through our price.
                             Executability and staleness. NOT flow.
    trade_flow_through_quote a print actually occurred at or through our price.
                             Real opposing flow. Still not our fill.
    VOLUME_AHEAD_CLEARED     the size that was resting at our price WHEN WE
                             PLACED has since been consumed by prints at or
                             through it. This is the STRICT criterion and the
                             ONLY thing that moves inventory here.

Queue position is not published, so VOLUME_AHEAD_CLEARED is itself a model --
a conservative one that assumes we joined behind everything already there. It
is the strongest evidence available and it is still not a venue confirmation;
every row says so.

NO THEORETICAL FILLS IN P&L. Inventory, matched inventory, residual and
effective pair economics advance on VOLUME_AHEAD_CLEARED alone. The weaker
rungs are counted and reported because the GAP between them is informative,
and never promoted.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.l5_shadow import venues as V
from src import l5_residual_episode_clock as CLK

LABEL = "GEMINI_BTC05M_SURFACE_SHADOW_V1"
STRICT = "VOLUME_AHEAD_CLEARED"
RUNGS = ("RESTABLE", "book_reached_quote", "trade_flow_through_quote", STRICT)

TICK = D("0.01")
UNIT = D("1")                    # one behavioral unit per quote, never scaled here


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def touch(book: Mapping[str, Any]) -> Tuple[Optional[D], Optional[D], Dict[D, D]]:
    """(best_bid, best_ask, size_at_price). Gemini serves both sides best-first,
    but the touch is taken by value anyway so an ordering change cannot invert
    it silently."""
    at: Dict[D, D] = collections.defaultdict(lambda: D("0"))
    bb = aa = None
    for lv in book.get("bids") or []:
        p, s = _f(lv.get("price")), _f(lv.get("amount") or lv.get("size"))
        if p is None:
            continue
        at[D(str(p))] += D(str(s or 0))
        bb = D(str(p)) if bb is None or D(str(p)) > bb else bb
    for lv in book.get("asks") or []:
        p, s = _f(lv.get("price")), _f(lv.get("amount") or lv.get("size"))
        if p is None:
            continue
        at[D(str(p))] += D(str(s or 0))
        aa = D(str(p)) if aa is None or D(str(p)) < aa else aa
    return bb, aa, dict(at)


# ---------------------------------------------------------------------------
# candidate surfaces. Each returns the quotes it wants resting RIGHT NOW, as
# (side, price) pairs -- both sides at once, which is the whole point.

def _skew(residual: D) -> int:
    if residual > D("2"):
        return 1          # long: pull the buy back, push the sell forward
    if residual < D("-2"):
        return -1
    return 0


def surface_quotes(name: str, bb: Optional[D], aa: Optional[D],
                   residual: D) -> List[Tuple[str, D]]:
    if bb is None or aa is None:
        return []
    q: List[Tuple[str, D]] = []
    if name == "single_level":
        q = [("BUY", bb)]
    elif name == "multi_level_ladder":
        q = [("BUY", bb - i * TICK) for i in range(3)] + \
            [("SELL", aa + i * TICK) for i in range(3)]
    elif name == "touch_following":
        q = [("BUY", bb), ("SELL", aa)]
    elif name == "fixed_distance_maker":
        q = [("BUY", bb - 2 * TICK), ("SELL", aa + 2 * TICK)]
    elif name == "inventory_skewed_maker":
        k = _skew(residual)
        q = [("BUY", bb - (2 * TICK if k > 0 else 0)),
             ("SELL", aa + (2 * TICK if k < 0 else 0))]
    elif name == "two_sided_symmetric":
        q = [("BUY", bb), ("SELL", aa)]
    elif name == "two_sided_asymmetric":
        # more presence on the cheaper side, which is where a passive surface
        # historically accumulated -- stated as the hypothesis it is
        q = ([("BUY", bb), ("BUY", bb - TICK), ("SELL", aa)] if bb < D("0.5")
             else [("BUY", bb), ("SELL", aa), ("SELL", aa + TICK)])
    return [(s, p) for s, p in q if D("0.01") <= p <= D("0.99")]


SURFACES = ("single_level", "multi_level_ladder", "touch_following",
            "fixed_distance_maker", "inventory_skewed_maker",
            "two_sided_symmetric", "two_sided_asymmetric")


# ---------------------------------------------------------------------------

class Quote:
    __slots__ = ("side", "px", "placed_t", "volume_ahead", "cleared_volume",
                 "restable", "book_reached", "trade_flow", "filled")

    def __init__(self, side: str, px: D, t: float, volume_ahead: D):
        self.side, self.px, self.placed_t = side, px, t
        self.volume_ahead = volume_ahead
        self.cleared_volume = D("0")
        self.restable = True
        self.book_reached = self.trade_flow = self.filled = False

    def key(self):
        return (self.side, str(self.px))


class SurfaceState:
    def __init__(self, name: str):
        self.name = name
        self.quotes: Dict[tuple, Quote] = {}
        self.long_qty = self.long_usd = D("0")
        self.short_qty = self.short_usd = D("0")
        self.rungs = collections.Counter()
        self.placed = 0
        # SPLIT BY SIDE, because an aggregate placement count cannot tell a
        # sell side that was never placed from one that rested and was never
        # traded through -- opposite conclusions with the same headline.
        self.placed_side = collections.Counter()
        self.rungs_side = collections.Counter()
        self.fills: List[dict] = []
        self.clock = CLK.Clock("GEMINI")
        self.run_side: Optional[str] = None
        self.run_len = 0

    @property
    def residual(self) -> D:
        return self.long_qty - self.short_qty

    @property
    def matched(self) -> D:
        return min(self.long_qty, self.short_qty)

    def queue_ahead(self) -> dict:
        """How much volume sits in front of our LIVE quotes, per side.

        The unfilled side is the one nobody can see: a fill row carries its
        own queue depth, so a side that never fills leaves no record of how
        far it was from filling. Without this, "the sell side never traded"
        and "the sell side was behind a wall" look identical.

        Depth is frozen at PLACEMENT and is a model, not a venue fact: queue
        position is unpublished and this assumes we joined behind everything
        already resting.
        """
        out: Dict[str, Any] = {}
        for side in ("BUY", "SELL"):
            qs = [q for q in self.quotes.values() if q.side == side]
            ahead = sorted(q.volume_ahead for q in qs)
            cleared = sorted(q.cleared_volume for q in qs)
            out[side] = {
                "live_quotes": len(qs),
                "median_volume_ahead_at_placement": (
                    str(ahead[len(ahead) // 2]) if ahead else None),
                "median_volume_cleared_since": (
                    str(cleared[len(cleared) // 2]) if cleared else None),
                "n_quotes_past_their_queue": sum(
                    1 for q in qs if q.cleared_volume > q.volume_ahead),
            }
        out["labels"] = ("depth frozen at placement; queue position is "
                         "unpublished, so this assumes we joined behind "
                         "everything resting. Never a venue fact.")
        return out

    def economics(self) -> dict:
        """The four Gemini quantities kept SEPARATE, per owner 2026-09-17.

        Gemini has one contract, so a sale is a SYNTHETIC complement, not a
        second token owned. Collapsing buy_cost, sell_proceeds and
        synthetic_complement_cost into one "pair" number is what would make an
        open position look like half a completed pair."""
        avg_b = (self.long_usd / self.long_qty) if self.long_qty else None
        avg_s = (self.short_usd / self.short_qty) if self.short_qty else None
        pe = CLK.pair_economics(
            "GEMINI",
            buy_px=(float(avg_b) if avg_b is not None else None),
            sell_px=(float(avg_s) if avg_s is not None else None),
            buy_fee="venue_declared_unknown", sell_fee="venue_declared_unknown")
        return {
            "long_qty": str(self.long_qty), "short_qty": str(self.short_qty),
            "buy_cost": pe["buy_cost"],
            "sell_proceeds": pe["sell_proceeds"],
            "synthetic_complement_cost": pe["synthetic_complement_cost"],
            "effective_pair_cost": pe["effective_pair_cost"],
            "edge_per_unit": pe["edge_per_unit"],
            "both_legs_filled": bool(self.long_qty and self.short_qty),
            "synthetic_pair_realized": False,
            "matched_qty_SYNTHETIC": str(self.matched),
            "residual": str(self.residual),
            "basis": ("evidenced fills only (%s); a SYNTHETIC pair, never "
                      "realized until BOTH orders fill AND authoritative "
                      "settlement/cash accounting confirms it" % STRICT),
            "fees": {"buy": pe["buy_fee"], "sell": pe["sell_fee"],
                     "netted": False},
            "mapping": pe["mapping"], "labels": pe["labels"],
        }


def step(st: SurfaceState, book: dict, prints: List[dict], now: float,
         *, symbol: str = "", tte_s: Optional[float] = None) -> List[dict]:
    """One observation for one surface. Returns evidence rows."""
    bb, aa, at_price = touch(book)
    rows: List[dict] = []
    want = surface_quotes(st.name, bb, aa, st.residual)

    # --- retire quotes the surface no longer wants
    for k in list(st.quotes):
        if (k[0], D(k[1])) not in want:
            st.quotes.pop(k, None)

    # --- place what is missing
    for side, px in want:
        k = (side, str(px))
        if k in st.quotes:
            continue
        st.quotes[k] = Quote(side, px, now, at_price.get(px, D("0")))
        st.placed += 1
        st.placed_side[side] += 1

    # --- advance every live quote against the book and the tape
    for k, q in list(st.quotes.items()):
        # RESTABLE: a bid must sit below the ask, an offer above the bid
        q.restable = bool(
            (q.side == "BUY" and aa is not None and q.px < aa) or
            (q.side == "SELL" and bb is not None and q.px > bb))
        if q.side == "BUY" and bb is not None and bb <= q.px:
            q.book_reached = True
        if q.side == "SELL" and aa is not None and aa >= q.px:
            q.book_reached = True
        for p in prints:
            if p["t"] <= q.placed_t:
                continue
            px, sz = D(str(p["price"])), D(str(p["size"]))
            through = (px <= q.px) if q.side == "BUY" else (px >= q.px)
            if not through:
                continue
            q.trade_flow = True
            q.cleared_volume += sz
        if not q.filled and q.trade_flow and q.cleared_volume > q.volume_ahead:
            q.filled = True
            fill_qty = min(UNIT, q.cleared_volume - q.volume_ahead)
            if q.side == "BUY":
                st.long_qty += fill_qty
                st.long_usd += fill_qty * q.px
            else:
                st.short_qty += fill_qty
                st.short_usd += fill_qty * q.px
            st.fills.append({"side": q.side, "px": str(q.px),
                             "qty": str(fill_qty), "t": now,
                             "evidence": STRICT,
                             "volume_ahead_at_placement": str(q.volume_ahead),
                             "volume_cleared": str(q.cleared_volume)})
            st.run_len = (st.run_len + 1) if st.run_side == q.side else 1
            st.run_side = q.side
            rows.append({"kind": "evidenced_fill", "surface": st.name,
                         "side": q.side, "px": str(q.px), "qty": str(fill_qty),
                         "evidence": STRICT, "t": now,
                         "run_position": st.run_len,
                         "labels": "EVIDENCED, not venue-confirmed: queue "
                                   "position is unpublished and this assumes "
                                   "we joined behind everything resting."})
            # a residual episode opens on the FIRST evidenced fill of a side
            # while the other side is absent -- that is the moment directional
            # exposure exists and the preregistered clock starts
            if (st.long_qty == 0) != (st.short_qty == 0):
                ep = st.clock.open_episode(
                    "%s|%s|%s|%.3f" % (st.name, q.side, q.px, now),
                    symbol=symbol, t=now, side=q.side, px=float(q.px),
                    qty=float(fill_qty), tte_s=tte_s, run_position=st.run_len)
                rows.append({"kind": "residual_episode_opened",
                             "label": CLK.LABEL, "surface": st.name,
                             "episode": ep.key, "marks_s": list(CLK.MARKS_S),
                             "t": now})
            st.quotes.pop(k, None)

    for q in st.quotes.values():
        st.rungs["RESTABLE"] += int(q.restable)
        st.rungs["book_reached_quote"] += int(q.book_reached)
        st.rungs["trade_flow_through_quote"] += int(q.trade_flow)
        for rung, hit in (("RESTABLE", q.restable),
                          ("book_reached_quote", q.book_reached),
                          ("trade_flow_through_quote", q.trade_flow)):
            st.rungs_side["%s|%s" % (q.side, rung)] += int(hit)
    st.rungs[STRICT] = len(st.fills)
    st.rungs_side["BUY|%s" % STRICT] = sum(
        1 for f in st.fills if f.get("side") == "BUY")
    st.rungs_side["SELL|%s" % STRICT] = sum(
        1 for f in st.fills if f.get("side") == "SELL")

    # --- preregistered residual-episode marks that have come due
    econ = st.economics()
    live = {r: any(getattr(q, a) for q in st.quotes.values())
            for r, a in (("quote_restable", "restable"),
                         ("book_reached_quote", "book_reached"),
                         ("trade_flow_through_quote", "trade_flow"))}
    for ep, mark in list(st.clock.due_marks(now)):
        opp = [f for f in st.fills
               if f["side"] != ep.first_side and f["t"] > ep.opened_t]
        rows.append(ep.record(mark, {
            "evidenced_complement": bool(opp),
            "complement_dt_s": (round(opp[0]["t"] - ep.opened_t, 3)
                                if opp else None),
            "quote_restable": live["quote_restable"],
            "book_reached_quote": live["book_reached_quote"],
            "trade_flow_through_quote": live["trade_flow_through_quote"],
            CLK.STRICT: bool(opp),
            "inventory": {"long": str(st.long_qty), "short": str(st.short_qty)},
            "residual": str(st.residual),
            "quote_validity": {"live_quotes": len(st.quotes),
                               "any_restable": live["quote_restable"]},
            "pair_economics": econ,
        }) | {"surface": st.name})
        if mark == CLK.MARKS_S[-1]:
            st.clock.retire(ep.key)
    return rows


def run(out_dir: str, *, minutes: float, interval: float) -> int:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / ("gemini_surface_%s.jsonl"
                % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    fh = open(path, "a", encoding="utf-8")

    def emit(r):
        r.setdefault("label", LABEL)
        fh.write(json.dumps(r, default=str) + "\n")
        fh.flush()

    emit({"kind": "session_start", "surfaces": list(SURFACES),
          "evidence_rungs": list(RUNGS), "strict": STRICT,
          "cohort": "BTC05M",
          "no_theoretical_fills": "inventory advances on %s alone" % STRICT,
          "observed_at": time.time()})

    states: Dict[str, Dict[str, SurfaceState]] = collections.defaultdict(dict)
    last_print_t: Dict[str, float] = {}
    deadline = time.time() + minutes * 60.0
    obs = 0
    while time.time() < deadline:
        t0 = time.time()
        for w in V.live_windows():
            if w["cohort"] != "BTC5m":
                continue
            sym = V.gemini_symbol_for(w["market_key"])
            if not sym:
                continue
            book = V.gemini_book_rest(sym)
            if not book:
                continue
            tr = V.gemini_trades(sym, limit=100) or []
            cut = last_print_t.get(sym, 0.0)
            fresh = [x for x in tr if x["t"] > cut]
            if tr:
                last_print_t[sym] = max(x["t"] for x in tr)
            bb, aa, _ = touch(book)
            obs += 1
            for name in SURFACES:
                st = states[sym].setdefault(name, SurfaceState(name))
                for r in step(st, book, fresh, t0, symbol=sym,
                              tte_s=w["window_end"] - t0):
                    emit({**r, "symbol": sym, "tte_s": w["window_end"] - t0})
            emit({"kind": "observation", "symbol": sym,
                  "market_key": w["market_key"],
                  "tte_s": round(w["window_end"] - t0, 1),
                  "best_bid": str(bb) if bb is not None else None,
                  "best_ask": str(aa) if aa is not None else None,
                  "spread": str(aa - bb) if (bb is not None and aa is not None) else None,
                  "implied_pair_cost_at_touch": (
                      str(bb + (D("1") - aa)) if (bb is not None and aa is not None)
                      else None),
                  "n_fresh_prints": len(fresh),
                  "MARKET_STATE_CLOCK": book.get("source_ts"),
                  "L5_OBSERVATION_CLOCK": book.get("observed_at"),
                  "surfaces": {n: {"rungs": dict(s.rungs),
                                   "quotes_placed": s.placed,
                                   "live_quotes": len(s.quotes),
                                   # per side, so a quiet sell side can be
                                   # told from an absent one
                                   "rungs_by_side": dict(s.rungs_side),
                                   "quotes_placed_by_side": dict(s.placed_side),
                                   "queue_ahead_by_side": s.queue_ahead(),
                                   **s.economics()}
                               for n, s in states[sym].items()}})
        sl = interval - (time.time() - t0)
        if sl > 0:
            time.sleep(sl)

    emit({"kind": "session_end", "observations": obs,
          "final": {sym: {n: {"rungs": dict(s.rungs), **s.economics()}
                          for n, s in v.items()} for sym, v in states.items()},
          "observed_at": time.time()})
    fh.close()
    print("GEMINI_SURFACE wrote %s (observations=%d)" % (path, obs), flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=110.0)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--out-dir", default="data/gemini_surface_shadow")
    a = ap.parse_args()
    return run(a.out_dir, minutes=a.minutes, interval=a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
