"""Is there a positive edge? Four ways of quoting, one ruler.

OFFLINE. Reads L5's public Polymarket BTC15m history and the Coinbase minute
tape; places nothing, changes no configuration, and is imported by nothing
under ``src/``.

THE QUESTION (owner, 2026-11-10). Find a demonstrable positive trading edge.
Compare the candidate ways of quoting on IDENTICAL windows with realistic
maker fills, adverse selection, fees, residual exposure and confirmed
settlement, decompose where the money goes, validate out of sample, and
report the uncertainty.

THE WINDOWS. The 312 resolved BTC15m windows for which the Coinbase minute
tape gives a strike -- the only ones on which the spot-gated candidate can
be evaluated at all, so every candidate is scored on exactly those. They are
split chronologically 60/40; anything tuned is tuned on the first part only.
THEY SPAN FOUR DAYS (2026-08-28 to 08-31): one regime. The candidates that
need no spot model are therefore also scored on all 526 resolved windows
(2026-03-19 to 08-31), split the same way.

THE RULER, identical for every candidate.

  * path: L5's prints as an UP price, in time order; the book is one tick
    wide around the print (PMUS BTC15m's median spread);
  * every order is a post-only maker BUY of one contract. It fills ONLY when
    a LATER print trades ``through`` ticks beyond it. Never at placement,
    never on a touch;
  * maker commission 0.0000 (every live fill so far); no rebate counted;
  * a contract matched against the other side redeems $1.00; one left
    unmatched settles on the venue's outcome.

THE DECOMPOSITION. Every filled contract's result is split into five parts
that add up to it exactly (marks are the print mid for that side):

  entry       mark at placement - price      what the quote asked for
  fill        mark at the fill - mark at placement, on a leg that OPENS or
              adds to an imbalance            adverse selection
  completion  mark at its own fill - price, on a leg that REDUCES an
              imbalance                       what matching cost or earned
  unmatched   mark when matched (or at the last decision time) - mark at
              the fill                        carrying the leg alone
  settlement  outcome - mark at the last decision time, for what was never
              matched                         the final coin flip

  A pair: (m1(t1)-q1) + (m1(t2)-m1(t1)) + (m2(t2)-q2) = 1 - q1 - q2, because
  the two marks at t2 sum to one. An unmatched leg: entry + fill + unmatched
  + settlement = outcome - price.

THE CANDIDATES.

  PAIR        the deployed engine: one pair two ticks back, complement
              follows its touch up to a 0.99 pair, 600 s cutoff.
  LADDER      that pair at two, three and four ticks back at once.
  GRIND       L5's shape: both sides AT the touch all window (a 0.99 pair),
              re-quoted after every fill, residual capped at three
              contracts, a reducing quote never paying past a 0.99 pair.
              (The unbounded chase is shown too: it completes above $1.)
  SPOT        quote a side only while a spot-and-strike probability model
              puts fair value at least ``margin`` above the quote; cancel
              when it stops; hold to settlement. Tuned on the first part.
  VETO        the pair, with a leg pulled while the model says it is
              overpriced.

AND ONE BENCHMARK THAT NEEDS NO FILL MODEL: what L5's own executed fills
earned on the same windows. Real fills, real adverse selection -- but his
queue, his speed and his incentives, not ours.

WHAT THIS CANNOT SAY. It is Polymarket's path, sampled where L5 traded. The
spot model sees a ONE-MINUTE tape, so it cannot react inside the minute in
which most adverse fills happen: SPOT and VETO here are what that tape can
do, not what a live feed could. Queue position is unmodelled beyond the
trade-through rule. It ranks candidates on identical paths; it is not a
forecast of our fills.
"""
from __future__ import annotations

import math
import random
import statistics as st
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from analysis.l5 import favorite_accumulation as FA
from analysis.l5 import pair_completion_replay as PR

TICK = PR.TICK
HALF = PR.HALF_SPREAD
EPS = PR.EPS
FLOOR = 0.30
OPEN_AFTER_S = 5.0
ENTRY_CUTOFF_S = 480.0
COMPLETE_CUTOFF_S = 600.0
ORDER_TTL_S = 120.0
BUCKETS = ("entry", "fill", "completion", "unmatched", "settlement")
UP, DOWN = "UP", "DOWN"


def _down(x: float) -> float:
    return math.floor(x / TICK + EPS) * TICK


def _mark(side: str, m: float) -> float:
    return m if side == UP else 1.0 - m


def _other(side: str) -> str:
    return DOWN if side == UP else UP


class Book:
    """One window's resting orders and inventory, with exact accounting."""

    def __init__(self, winner: str, through: int = 1) -> None:
        self.winner = winner
        self.thr = through * TICK
        self.orders: Dict[int, Dict[str, Any]] = {}
        self._id = 0
        self.open: Dict[str, List[Dict[str, Any]]] = {UP: [], DOWN: []}
        self.b = {k: 0.0 for k in BUCKETS}
        self.fills = 0
        self.pairs = 0
        self.max_unmatched = 0.0
        self.unmatched_results: List[float] = []
        self.pair_costs: List[float] = []
        self.log: List[Dict[str, Any]] = []

    # ---------------------------------------------------------- orders
    def place(self, side: str, px: float, t: float, m: float,
              tag: str = "") -> Optional[int]:
        px = _down(px + EPS)
        if px < TICK - EPS or px > 1.0 - TICK + EPS:
            return None
        self._id += 1
        self.orders[self._id] = {"side": side, "px": px, "t": t,
                                 "m_place": _mark(side, m), "tag": tag}
        return self._id

    def cancel(self, oid: int, at: Optional[float] = None) -> None:
        """Cancel now, or -- with `at` -- only from that time on: until then
        the order is still at the venue and a print can still fill it."""
        if at is None:
            self.orders.pop(oid, None)
        elif oid in self.orders:
            cur = self.orders[oid].get("cancel_at")
            self.orders[oid]["cancel_at"] = at if cur is None else min(cur, at)

    def resting(self, side: Optional[str] = None, tag: Optional[str] = None,
                cancelling: bool = False
                ) -> List[Tuple[int, Dict[str, Any]]]:
        """Orders still wanted. One whose cancel is in flight is left out
        unless `cancelling` asks for it."""
        return [(i, o) for i, o in self.orders.items()
                if (side is None or o["side"] == side)
                and (tag is None or o["tag"] == tag)
                and (cancelling or o.get("cancel_at") is None)]

    def imbalance(self) -> float:
        """Unmatched contracts: + is UP-heavy, - is DOWN-heavy."""
        return float(len(self.open[UP]) - len(self.open[DOWN]))

    # ----------------------------------------------------------- fills
    def match(self, t: float, m: float) -> List[Dict[str, Any]]:
        """Fill every order this print trades through. Returns the fills."""
        done = []
        for oid, o in sorted(self.orders.items()):
            if o.get("cancel_at") is not None and t >= o["cancel_at"]:
                self.orders.pop(oid, None)       # the cancel had landed
                continue
            if t <= o["t"]:
                continue
            if _mark(o["side"], m) <= o["px"] - self.thr + EPS:
                done.append((oid, o))
        out = []
        for oid, o in done:
            self.orders.pop(oid, None)
            out.append(self._book_fill(o, t, m))
        return out

    def _book_fill(self, o: Dict[str, Any], t: float, m: float
                   ) -> Dict[str, Any]:
        side, q = o["side"], o["px"]
        mf = _mark(side, m)
        self.fills += 1
        opp = self.open[_other(side)]
        if opp:
            # REDUCES the imbalance: this leg completes the oldest open one
            first = opp.pop(0)
            self.b["completion"] += mf - q
            self.b["unmatched"] += _mark(first["side"], m) - first["m_fill"]
            self.pairs += 1
            self.pair_costs.append(first["q"] + q)
            role = "COMPLETE"
        else:
            self.b["entry"] += o["m_place"] - q
            self.b["fill"] += mf - o["m_place"]
            self.open[side].append({"side": side, "q": q, "m_fill": mf,
                                    "t": t})
            role = "OPEN"
        self.max_unmatched = max(self.max_unmatched, abs(self.imbalance()))
        rec = {"side": side, "px": q, "t": t, "role": role, "tag": o["tag"]}
        self.log.append(rec)
        return rec

    # ---------------------------------------------------------- settle
    def settle(self, m_last: float) -> None:
        for side in (UP, DOWN):
            won = (self.winner == "Up") == (side == UP)
            for leg in self.open[side]:
                ml = _mark(side, m_last)
                self.b["unmatched"] += ml - leg["m_fill"]
                self.b["settlement"] += (1.0 if won else 0.0) - ml
                self.unmatched_results.append(
                    (1.0 if won else 0.0) - leg["q"])
            self.open[side] = []

    def result(self) -> Dict[str, Any]:
        return {"net": sum(self.b.values()), "fills": self.fills,
                "pairs": self.pairs, "buckets": dict(self.b),
                "max_unmatched": self.max_unmatched,
                "unmatched_results": list(self.unmatched_results),
                "pair_costs": list(self.pair_costs),
                "log": list(self.log)}


Strategy = Callable[[Book, float, float, Optional[float], Dict[str, Any]],
                    None]


def run_window(path: Sequence[Tuple[float, float]], winner: str,
               strategy: Strategy, *, model: Optional[Callable[[float],
                                                              Optional[float]]]
               = None, through: int = 1) -> Dict[str, Any]:
    book = Book(winner, through)
    state: Dict[str, Any] = {}
    m_last = None
    for t, m in path:
        if t <= COMPLETE_CUTOFF_S + 240.0:
            state["new_fills"] = book.match(t, m)
        for oid, o in list(book.orders.items()):
            if t - o["t"] >= ORDER_TTL_S and not o["tag"].startswith("keep"):
                book.cancel(oid)
        p = model(t) if model is not None else None
        strategy(book, t, m, p, state)
        if t <= COMPLETE_CUTOFF_S:
            m_last = m
    book.orders.clear()
    book.settle(m_last if m_last is not None else (path[-1][1] if path
                                                   else 0.5))
    return book.result()


# ================================================================ strategies
def make_pair(depth: int = 2, ceiling: float = 0.99) -> Strategy:
    """The deployed pair: both legs `depth` ticks back, one round."""
    def strat(book, t, m, p, s):
        if not s.get("opened"):
            if not (OPEN_AFTER_S <= t <= ENTRY_CUTOFF_S):
                return
            u = _down(m - HALF - depth * TICK)
            d = _down(1.0 - m - HALF - depth * TICK)
            if u < FLOOR - EPS or d < FLOOR - EPS:
                return
            book.place(UP, u, t, m, "leg")
            book.place(DOWN, d, t, m, "leg")
            s["opened"], s["t_open"] = True, t
            return
        imb = book.imbalance()
        if imb == 0:
            if not book.resting() and not book.fills and \
                    t <= ENTRY_CUTOFF_S:
                s["opened"] = False          # both expired unfilled: requote
            return
        # one leg is held: work the other up to the pair ceiling
        held = UP if imb > 0 else DOWN
        comp = _other(held)
        if t > COMPLETE_CUTOFF_S:
            for oid, _o in book.resting(comp):
                book.cancel(oid)
            return
        cost = book.open[held][0]["q"]
        want = min(_down(_mark(comp, m) - HALF), _down(ceiling - cost + EPS))
        rest = book.resting(comp)
        if rest and rest[0][1]["px"] >= want - EPS:
            return
        for oid, _o in rest:
            book.cancel(oid)
        if want >= TICK:
            book.place(comp, want, t, m, "keep-comp")
    return strat


def make_ladder(depths: Sequence[int] = (2, 3, 4)) -> List[Strategy]:
    """Independent pairs at several depths; results are summed."""
    return [make_pair(d) for d in depths]


def make_grind(depth: int = 0, cap: int = 3,
               ceiling: Optional[float] = 0.99) -> Strategy:
    """Both sides at the touch all window, re-quoted, residual capped.

    ``ceiling`` bounds what a REDUCING quote may pay: never more than
    ``ceiling`` less the price of the oldest unmatched contract it would
    complete. ``None`` is the unbounded chase (completes at any price), kept
    only to show what it costs."""
    def strat(book, t, m, p, s):
        imb = book.imbalance()
        for side in (UP, DOWN):
            heavy = (imb >= cap and side == UP) or \
                    (imb <= -cap and side == DOWN)
            reducing = (imb > 0 and side == DOWN) or (imb < 0 and side == UP)
            live = (OPEN_AFTER_S <= t <= ENTRY_CUTOFF_S) or \
                   (reducing and t <= COMPLETE_CUTOFF_S)
            want = _down(_mark(side, m) - HALF - depth * TICK)
            if reducing and ceiling is not None:
                first = book.open[_other(side)][0]["q"]
                want = min(want, _down(ceiling - first + EPS))
            rest = book.resting(side)
            if not live or heavy or want < TICK:
                for oid, _o in rest:
                    book.cancel(oid)
                continue
            if rest and abs(rest[0][1]["px"] - want) < EPS:
                continue
            for oid, _o in rest:
                book.cancel(oid)
            book.place(side, want, t, m, "keep-grind")
    return strat


def make_spot(margin: float, depth: int = 1, cap: int = 3) -> Strategy:
    """Quote a side only while the model says it is `margin` underpriced."""
    def strat(book, t, m, p, s):
        for side in (UP, DOWN):
            rest = book.resting(side)
            fair = None if p is None else (p if side == UP else 1.0 - p)
            want = _down(_mark(side, m) - HALF - depth * TICK)
            held = len(book.open[side])
            ok = (fair is not None and OPEN_AFTER_S <= t <= COMPLETE_CUTOFF_S
                  and want >= TICK and held < cap
                  and fair - want >= margin - EPS)
            if not ok:
                for oid, _o in rest:
                    book.cancel(oid)
                continue
            if rest and abs(rest[0][1]["px"] - want) < EPS:
                continue
            for oid, _o in rest:
                book.cancel(oid)
            book.place(side, want, t, m, "keep-spot")
    return strat


def make_veto(veto: float, depth: int = 2, ceiling: float = 0.99) -> Strategy:
    """The pair, with an OPENING leg pulled while the model says it is more
    than `veto` overpriced, and put back when it no longer is."""
    base = make_pair(depth, ceiling)

    def strat(book, t, m, p, s):
        base(book, t, m, p, s)
        if p is None or book.imbalance() != 0:
            return
        for oid, o in book.resting(tag="leg"):
            fair = p if o["side"] == UP else 1.0 - p
            if fair - o["px"] < -veto + EPS:
                book.cancel(oid)
    return strat


# =================================================================== scoring
def _boot_ratio(nets: Sequence[float], dens: Sequence[float], n: int = 2000,
                seed: int = 7) -> Tuple[float, float]:
    """Window-clustered bootstrap interval for sum(net)/sum(den)."""
    rng = random.Random(seed)
    k = len(nets)
    if k < 2 or sum(dens) <= 0:
        return float("nan"), float("nan")
    out = []
    for _ in range(n):
        tn = td = 0.0
        for _i in range(k):
            j = rng.randrange(k)
            tn += nets[j]
            td += dens[j]
        if td > 0:
            out.append(tn / td)
    out.sort()
    return out[int(0.025 * len(out))], out[int(0.975 * len(out)) - 1]


def summarise(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    nets = [r["net"] for r in results]
    fills = [float(r["fills"]) for r in results]
    tot_f = sum(fills)
    lo, hi = _boot_ratio(nets, fills)
    n = len(nets)
    mean = st.mean(nets) if nets else 0.0
    half = (1.96 * st.stdev(nets) / math.sqrt(n)) if n > 1 else float("nan")
    unm = [x for r in results for x in r["unmatched_results"]]
    pcs = sorted(x for r in results for x in r["pair_costs"])
    return {
        "windows": n, "fills": int(tot_f),
        "pairs": sum(r["pairs"] for r in results),
        "unmatched_contracts": len(unm),
        "net_total": sum(nets),
        "net_per_contract": (sum(nets) / tot_f) if tot_f else None,
        "net_per_contract_ci95": [lo, hi],
        "net_per_window": mean, "net_per_window_ci95": [mean - half,
                                                        mean + half],
        "buckets_per_contract": {
            k: (sum(r["buckets"][k] for r in results) / tot_f)
            if tot_f else None for k in BUCKETS},
        "pair_cost_p50": (pcs[len(pcs) // 2] if pcs else None),
        "worst_window": (min(nets) if nets else None),
        "p05_window": (sorted(nets)[int(0.05 * n)] if n else None),
        "worst_unmatched_contract": (min(unm) if unm else None),
        "unmatched_lost_frac": ((sum(1 for x in unm if x < 0) / len(unm))
                                if unm else None),
        "unmatched_mean": (st.mean(unm) if unm else None),
        "max_unmatched_held": max((r["max_unmatched"] for r in results),
                                  default=0.0),
    }


def paired_difference(a: Sequence[float], b: Sequence[float], n: int = 2000,
                      seed: int = 11) -> Dict[str, Any]:
    """Mean of (a - b) per window, with a window bootstrap interval."""
    d = [x - y for x, y in zip(a, b)]
    if len(d) < 2:
        return {"windows": len(d), "mean": None, "ci95": [None, None]}
    rng = random.Random(seed)
    k = len(d)
    means = sorted(sum(d[rng.randrange(k)] for _ in range(k)) / k
                   for _ in range(n))
    return {"windows": k, "mean": sum(d) / k,
            "ci95": [means[int(0.025 * n)], means[int(0.975 * n) - 1]]}


def combine(parts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Sum several independent books run on the same window."""
    return {"net": sum(p["net"] for p in parts),
            "fills": sum(p["fills"] for p in parts),
            "pairs": sum(p["pairs"] for p in parts),
            "buckets": {k: sum(p["buckets"][k] for p in parts)
                        for k in BUCKETS},
            "max_unmatched": sum(p["max_unmatched"] for p in parts),
            "unmatched_results": [x for p in parts
                                  for x in p["unmatched_results"]],
            "pair_costs": [x for p in parts for x in p["pair_costs"]]}


def l5_actual(trades: Sequence[Dict[str, Any]], slugs: Sequence[str]
              ) -> Dict[str, Any]:
    """What L5's own executed fills earned on these windows. No fill model."""
    want = set(slugs)
    by: Dict[str, List[float]] = {}
    for t in trades:
        if t["slug"] not in want or t["side"] != "BUY":
            continue
        w = by.setdefault(t["slug"], [0.0, 0.0])
        w[0] += t["size"] * ((1.0 if t["outcome"] == t["winner"] else 0.0)
                             - t["px"])
        w[1] += t["size"]
    nets = [by[s][0] for s in slugs if s in by]
    dens = [by[s][1] for s in slugs if s in by]
    lo, hi = _boot_ratio(nets, dens)
    return {"windows": len(nets), "contracts": sum(dens),
            "net_total": sum(nets),
            "net_per_contract": (sum(nets) / sum(dens)) if dens else None,
            "net_per_contract_ci95": [lo, hi]}


# ===================================================================== model
def load_model():
    """{slug: fn(t_off) -> P(up) or None}, from the one-minute tape."""
    from analysis.l5 import btc15m_calibration_report as CR
    from analysis.l5.btc15m_asof import asof_snapshot
    from analysis.l5.btc15m_probability import estimate_up
    series = CR.load_candles()
    sources = CR.load_sources()

    def for_window(slug: str, ws: float):
        src = sources.get(slug, "")
        cache: Dict[int, Optional[float]] = {}

        def p_at(t_off: float) -> Optional[float]:
            # the tape is one-minute, so the model only changes on the minute
            key = int(t_off // 60)
            if key not in cache:
                try:
                    cache[key] = float(estimate_up(asof_snapshot(
                        series, ws, ws + 60.0 * key,
                        resolution_source=src)))
                except ValueError:
                    cache[key] = None
            return cache[key]
        try:
            from analysis.l5.btc15m_asof import strike
            strike(series, ws, src)
        except ValueError:
            return None
        return p_at
    return for_window


SPOT_GRID = [(mg, d) for mg in (0.02, 0.04, 0.06, 0.08, 0.10)
             for d in (0, 1, 2)]
VETO_GRID = (0.00, 0.02, 0.04, 0.06)


def run() -> Dict[str, Any]:
    trades = FA.load_trades(FA.load_outcomes())
    ps = PR.paths(trades)
    model_for = load_model()
    models = {}
    for s, w in ps.items():
        fn = model_for(s, w["ws"])
        if fn is not None:
            models[s] = fn
    order = sorted(models, key=lambda s: ps[s]["ws"])
    cut = int(len(order) * 0.6)
    halves = {"ALL": order, "IS": order[:cut], "OOS": order[cut:]}

    def score(slugs, strategies, through=1):
        strategies = strategies if isinstance(strategies, list) \
            else [strategies]
        out = []
        for s in slugs:
            # a strategy closure keeps no cross-window state: state is passed
            parts = [run_window(ps[s]["path"], ps[s]["winner"], strat,
                                model=models[s], through=through)
                     for strat in strategies]
            out.append(combine(parts))
        summ = summarise(out)
        summ["_nets"] = [o["net"] for o in out]
        return summ

    # tuned on the first part ONLY
    best_spot = max(SPOT_GRID, key=lambda g: (score(
        halves["IS"], make_spot(g[0], g[1]))["net_per_contract"] or -9))
    best_veto = max(VETO_GRID, key=lambda v: (score(
        halves["IS"], make_veto(v))["net_per_contract"] or -9))

    cands = {
        "PAIR (deployed)": make_pair(2),
        "LADDER 2/3/4": make_ladder((2, 3, 4)),
        "GRIND at touch": make_grind(0),
        "GRIND 1 back": make_grind(1),
        "GRIND chase (no ceiling)": make_grind(0, ceiling=None),
        "SPOT margin %.2f depth %d" % best_spot: make_spot(*best_spot),
        "VETO %.2f" % best_veto: make_veto(best_veto),
    }
    res: Dict[str, Any] = {
        "evidence_class": ("L5 POLYMARKET PATHS with PMUS ticks and fees, "
                           "one-minute Coinbase reference -- not PMUS "
                           "fills, not our own"),
        "windows": {k: len(v) for k, v in halves.items()},
        "tuned_on_IS": {"spot": best_spot, "veto": best_veto},
        "candidates": {}, "through0": {}, "spot_grid_IS": {}, "l5_actual": {}}
    for name, strat in cands.items():
        res["candidates"][name] = {h: score(sl, strat)
                                   for h, sl in halves.items()}
        res["through0"][name] = score(halves["OOS"], strat, through=0)
    # the same windows, so differences are paired window by window
    base = res["candidates"]["PAIR (deployed)"]
    res["paired_vs_pair"] = {}
    for name, by in res["candidates"].items():
        if name == "PAIR (deployed)":
            continue
        res["paired_vs_pair"][name] = {
            h: paired_difference(by[h]["_nets"], base[h]["_nets"])
            for h in halves}
    for group in (res["candidates"], ):
        for by in group.values():
            for s_ in by.values():
                s_.pop("_nets", None)
    for s_ in res["through0"].values():
        s_.pop("_nets", None)
    # the candidates that need no spot model, on EVERY resolved window
    wide = sorted(ps, key=lambda s_: ps[s_]["ws"])
    wcut = int(len(wide) * 0.6)
    whalves = {"ALL": wide, "IS": wide[:wcut], "OOS": wide[wcut:]}
    res["wide"] = {"windows": {k: len(v) for k, v in whalves.items()},
                   "period": {h: [ps[sl[0]]["ws"], ps[sl[-1]]["ws"]]
                              for h, sl in whalves.items()},
                   "candidates": {}, "l5_actual": {}}
    for name, strat in cands.items():
        if name.startswith(("SPOT", "VETO")):
            continue
        res["wide"]["candidates"][name] = {}
        for h, sl in whalves.items():
            strategies = strat if isinstance(strat, list) else [strat]
            res["wide"]["candidates"][name][h] = summarise([combine([
                run_window(ps[s_]["path"], ps[s_]["winner"], st_)
                for st_ in strategies]) for s_ in sl])
    for h, sl in whalves.items():
        res["wide"]["l5_actual"][h] = l5_actual(trades, sl)
    for g in SPOT_GRID:
        res["spot_grid_IS"]["%.2f/%d" % g] = score(
            halves["IS"], make_spot(*g))["net_per_contract"]
    res["veto_grid_IS"] = {"%.2f" % v: score(
        halves["IS"], make_veto(v))["net_per_contract"] for v in VETO_GRID}
    res["period"] = {h: [ps[sl[0]]["ws"], ps[sl[-1]]["ws"]]
                     for h, sl in halves.items()}
    for h, sl in halves.items():
        res["l5_actual"][h] = l5_actual(trades, sl)
    return res


def _f(x, w=8, p=4):
    return (" " * (w - 3) + "n/a") if x is None else ("%+*.*f" % (w, p, x))


def main() -> None:
    import json
    import os
    r = run()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "edge_comparison.json"), "w") as fh:
        json.dump(r, fh, indent=1, sort_keys=True)
    print("evidence:", r["evidence_class"])
    print("windows:", r["windows"], "| tuned on IS:", r["tuned_on_IS"])
    for half in ("OOS", "IS", "ALL"):
        print("\n== %s ==" % half)
        print("%-26s %6s %6s %5s %9s  %-20s %9s  %s" % (
            "candidate", "fills", "pairs", "unm", "net/ctr", "95% CI",
            "net/win", "worst win / p05 / worst unmatched"))
        for name, by in r["candidates"].items():
            s = by[half]
            print("%-26s %6d %6d %5d %9s  [%+.4f, %+.4f]  %9s  %s / %s / %s"
                  % (name, s["fills"], s["pairs"], s["unmatched_contracts"],
                     _f(s["net_per_contract"], 9),
                     s["net_per_contract_ci95"][0],
                     s["net_per_contract_ci95"][1],
                     _f(s["net_per_window"], 9), _f(s["worst_window"], 7, 2),
                     _f(s["p05_window"], 7, 2),
                     _f(s["worst_unmatched_contract"], 6, 2)))
        a = r["l5_actual"][half]
        print("%-26s %6d contracts %12.0f %9s  [%+.4f, %+.4f]" % (
            "L5 HIMSELF (real fills)", a["windows"], a["contracts"],
            _f(a["net_per_contract"], 9), a["net_per_contract_ci95"][0],
            a["net_per_contract_ci95"][1]))
    w = r["wide"]
    print("\n== every resolved window (no spot model needed): %s ==" %
          w["windows"])
    for half in ("OOS", "IS", "ALL"):
        for name, by in w["candidates"].items():
            s = by[half]
            print("%-4s %-26s %6d fills %9s  [%+.4f, %+.4f]  /win %s  "
                  "worst %s" % (half, name, s["fills"],
                                _f(s["net_per_contract"], 9),
                                s["net_per_contract_ci95"][0],
                                s["net_per_contract_ci95"][1],
                                _f(s["net_per_window"], 8),
                                _f(s["worst_window"], 7, 2)))
        a = w["l5_actual"][half]
        print("%-4s %-26s %6d windows %7s  [%+.4f, %+.4f]" % (
            half, "L5 HIMSELF (real fills)", a["windows"],
            _f(a["net_per_contract"], 9), a["net_per_contract_ci95"][0],
            a["net_per_contract_ci95"][1]))
    print("\n== where the money goes, per filled contract, OOS ==")
    print("%-26s %8s %8s %10s %9s %10s %9s" % (
        ("candidate",) + BUCKETS + ("= net",)))
    for name, by in r["candidates"].items():
        b = by["OOS"]["buckets_per_contract"]
        print("%-26s %s %s %s %s %s %s" % (
            name, _f(b["entry"]), _f(b["fill"]), _f(b["completion"], 10),
            _f(b["unmatched"], 9), _f(b["settlement"], 10),
            _f(by["OOS"]["net_per_contract"], 9)))
    print("\n== optimistic fill model (a touch fills), OOS net/contract ==")
    for name, s in r["through0"].items():
        print("%-26s %s  [%+.4f, %+.4f]" % (
            name, _f(s["net_per_contract"], 9),
            s["net_per_contract_ci95"][0], s["net_per_contract_ci95"][1]))
    print("\n== versus the deployed pair, same windows, $ per window ==")
    for name, by in r["paired_vs_pair"].items():
        print("%-26s %s" % (name, "  ".join(
            "%s %+.4f [%+.4f, %+.4f]" % (h, by[h]["mean"], by[h]["ci95"][0],
                                         by[h]["ci95"][1])
            for h in ("IS", "OOS"))))
    print("\n== SPOT grid on IS (margin/depth -> net/contract) ==")
    print("  ".join("%s:%s" % (k, _f(v, 7)) for k, v in
                    r["spot_grid_IS"].items()))


if __name__ == "__main__":
    main()
