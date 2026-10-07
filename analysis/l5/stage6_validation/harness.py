"""STAGE-6 HISTORICAL VALIDATION HARNESS -- prerecorded L2 only, zero capital.

HISTORICAL REPLAY. Nothing here is shadow, submitted, filled, settled or
realized. No order, no signing, no credential, no network call. Read-only
over `data/zenodo_22084977/updown_{15m,5m}.parquet`.

WHAT THIS ANSWERS. Had the Stage-6 matched-pair protocol been applied to every
evaluable historical state we possess, how often would a first leg have become
MATCHED at a fee-inclusive combined pair cost at or below the ceiling, how
often would it have REMAINED RESIDUAL, how long would residuals have
persisted, and what is the worst exposure?

THE DEFINING RULE: WHERE QUEUE STATE IS UNKNOWABLE, IT IS LABELLED UNKNOWABLE.
A passive maker order's fill depends on queue position. The Zenodo archive is
a BOOK archive: aligned bid/ask ladders written on change, with NO trade
prints and NO sequence numbers. A resting bid at price p placed when size Q
already sat at p joins BEHIND Q, and nothing in a book-only archive reveals
whether our own quantity traded. So every passive leg lands in exactly one of
three buckets, and they are never merged:

  WOULD_FILL      provable from the book: some later snapshot has
                  best_bid < p, i.e. the touch moved STRICTLY THROUGH our
                  price, so the whole level at p -- us included -- left the
                  book.
  WOULD_NOT_FILL  provable from the book: best_bid > p at EVERY later
                  snapshot in the horizon. The market never traded down to
                  our price at any observed instant, so our order was never
                  at the touch and never reachable.
  UNKNOWABLE      everything else: best_bid reaches p and stays at or above
                  p. Size at our level may fall, rise, or hold. Whether our
                  own 1.00 shares traded is a function of queue position,
                  which this data cannot determine.

THE LEVEL_CONSUMED SELECTION BIAS, honored explicitly.
`analysis/l5/august_replay/S2_FINDINGS.md` section 2 establishes that
`EVIDENCED_LEVEL_CONSUMED` fires IFF the best bid has fallen strictly below
our own price -- only when the market has crossed our bid. It therefore
mechanically selects the subset of hypothetical maker fills whose
contemporaneous book movement went AGAINST us, and systematically excludes
benign fills. WOULD_FILL here is EXACTLY that biased subset. It is reported as
such everywhere and is never presented as a representative sample of what a
real resting order would have experienced. Two further caveats travel with it:
a level vanishing is also consistent with MASS CANCELLATION rather than
trading, and the archive's own granularity (row on change, 60 s REST resync,
no sequence numbers) means a sweep through p that recovered between snapshots
is undetectable -- which loosens WOULD_NOT_FILL in the optimistic direction
and is stated rather than assumed away.

WHAT *IS* PROVABLE WITHOUT QUEUE STATE: TAKING DISPLAYED DEPTH. If the
complement token's best ask is already at or below the price the pair ceiling
allows, and the displayed size at that ask is at least our quantity, then a
crossing order fills deterministically against visible liquidity. Queue
position is irrelevant to a taker. That is the one completion channel this
archive can prove, and it carries the taker fee from the frozen schedule.

NO PARAMETER FITTING. Every policy number is INJECTED (see `Params`), bound
either to Lane 4's frozen `analysis/l5/stage6/bounds.py` when present or to
the standing spring-discipline fallback. Nothing in this module searches,
grids or optimizes a threshold. The decision grid is a fixed cadence over
every market's whole life; no state is selected on its outcome.

NO HINDSIGHT. Every fill decision reads snapshots with `captured_at` strictly
greater than the placement instant and strictly less than or equal to the
horizon, and reads nothing else. The settled `winner` column is read ONLY to
value an unmatched residual at settlement for the worst-exposure accounting,
after every fill verdict is already fixed; a test-grade assertion keeps it out
of the classifier by construction (it is never passed to it).
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import hashlib
import importlib
import json
import math
import os
import random
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

import pmus_fee_schedule as FEES  # noqa: E402

FEES.assert_frozen()
FEE_FINGERPRINT = FEES.FINGERPRINT

BOOK_FILES = (
    os.path.join(ROOT, "data", "zenodo_22084977", "updown_15m.parquet"),
    os.path.join(ROOT, "data", "zenodo_22084977", "updown_5m.parquet"),
)

#: the archive's own REST resync interval. A snapshot older than this is a
#: REFUSED read, exactly as `analysis/l5/hturn/hturn.py` refuses it.
MAX_CARRY_S = 60.0
EPS = 1e-9

#: canonical regime change points (mandate + analysis/l5/CANON_REGIMES.md)
REGIME_POINTS_UTC = ("2026-04-25", "2026-06-23", "2026-08-19")

L5_COINS = ("btc", "eth")


# ------------------------------------------------------------------ params

class Params:
    """Policy parameters, INJECTED. Nothing here is chosen to improve a
    result; each value carries where it came from."""

    def __init__(self, **kw: Any) -> None:
        self.tick: float = kw["tick"]
        self.qty: float = kw["qty"]
        self.min_price: float = kw["min_price"]
        self.max_price: float = kw["max_price"]
        self.max_first_leg_price: float = kw["max_first_leg_price"]
        self.pair_cost_ceiling: float = kw["pair_cost_ceiling"]
        self.tte_cutoff_s: float = kw["tte_cutoff_s"]
        self.rebate_assumed_zero: bool = kw["rebate_assumed_zero"]
        self.first_leg_offset_ticks: int = kw["first_leg_offset_ticks"]
        self.complement_requotes: int = kw["complement_requotes"]
        self.binding: str = kw["binding"]
        self.source: Dict[str, str] = kw["source"]

    def as_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in sorted(self.__dict__.items())}

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True,
                       default=str).encode()).hexdigest()


#: THE FALLBACK. Standing spring discipline, used ONLY if Lane 4's frozen
#: module is absent. CLAUDE.md pins the pair ceiling and maker-first; the
#: venue grid and minimum come from the proven order spec.
FALLBACK = dict(
    tick=0.01, qty=0.01, min_price=0.01, max_price=0.99,
    max_first_leg_price=0.99, pair_cost_ceiling=0.96,
    tte_cutoff_s=0.0, rebate_assumed_zero=True,
    first_leg_offset_ticks=0, complement_requotes=0,
    binding="STANDING_SPRING_DISCIPLINE_FALLBACK_NOT_LANE4_FROZEN",
    source={
        "pair_cost_ceiling": "CLAUDE.md spring discipline <= 0.96",
        "qty": "venue minimum 0.01, per mandate",
        "tick": "venue grid 0.01",
        "max_first_leg_price": "no ceiling in the standing discipline",
        "rebate_assumed_zero": "PMUS maker rebate cent floor 1.62 shares",
        "first_leg_offset_ticks": "maker-first: join the bid (S1 offset 0)",
    },
)


def bind_params() -> Params:
    """Bind to Lane 4's frozen Stage-6 spec if it is on disk, else fall back.

    Lane 4's files are READ ONLY here and never written or imported for
    mutation."""
    mod_path = os.path.join(ROOT, "analysis", "l5", "stage6", "bounds.py")
    proto = os.path.join(ROOT, "analysis", "l5", "stage6",
                         "STAGE6_PROTOCOL.md")
    if not os.path.exists(mod_path):
        return Params(**FALLBACK)
    sys.path.insert(0, ROOT)
    b = importlib.import_module("analysis.l5.stage6.bounds")
    importlib.reload(b)
    src = os.path.join("analysis", "l5", "stage6", "bounds.py")
    fp = hashlib.sha256(open(mod_path, "rb").read()).hexdigest()
    return Params(
        tick=float(b.VENUE_TICK),
        qty=float(b.FIRST_LEG_QTY),
        min_price=float(b.VENUE_MIN_PRICE),
        max_price=float(b.VENUE_MAX_PRICE),
        max_first_leg_price=float(b.MAX_FIRST_LEG_PRICE),
        pair_cost_ceiling=float(b.PAIR_COST_CEILING),
        tte_cutoff_s=float(b.TTE_CUTOFF_S),
        rebate_assumed_zero=bool(b.REBATE_IS_ASSUMED_ZERO),
        first_leg_offset_ticks=0,
        complement_requotes=int(b.MAX_COMPLEMENT_SUBMISSIONS) - 1,
        binding=("LANE4_FROZEN_bounds.py"
                 + ("+STAGE6_PROTOCOL.md" if os.path.exists(proto)
                    else " (PROTOCOL.md ABSENT)")),
        source={"all": src, "bounds_py_sha256": fp,
                "protocol_md_present": str(os.path.exists(proto)),
                "first_leg_offset_ticks":
                    "not in bounds.py; maker-first join-the-bid, S1 offset 0",
                },
    )


# -------------------------------------------------------------------- books

class TokenBook:
    """On-change snapshots for ONE outcome token of ONE market, with the
    ladder kept so size-at-price is readable."""

    __slots__ = ("token_id", "outcome", "t", "bid", "ask", "bids", "asks",
                 "crossed", "stc", "winner")

    def __init__(self, token_id: str, outcome: str) -> None:
        self.token_id = token_id
        self.outcome = outcome
        self.t: List[float] = []
        self.bid: List[Optional[float]] = []
        self.ask: List[Optional[float]] = []
        self.bids: List[List[Tuple[float, float]]] = []
        self.asks: List[List[Tuple[float, float]]] = []
        self.crossed: List[bool] = []
        self.stc: List[Optional[float]] = []
        self.winner: Optional[str] = None

    def add(self, ts, bb, ba, bl, al, cr, stc) -> None:
        self.t.append(ts)
        self.bid.append(bb)
        self.ask.append(ba)
        self.bids.append(bl)
        self.asks.append(al)
        self.crossed.append(bool(cr))
        self.stc.append(stc)

    def sort(self) -> None:
        o = sorted(range(len(self.t)), key=lambda i: self.t[i])
        for name in ("t", "bid", "ask", "bids", "asks", "crossed", "stc"):
            v = getattr(self, name)
            setattr(self, name, [v[i] for i in o])

    def idx_at_or_before(self, ts: float) -> Optional[int]:
        i = bisect.bisect_right(self.t, ts) - 1
        if i < 0 or ts - self.t[i] > MAX_CARRY_S:
            return None
        return i

    def size_at(self, i: int, px: float) -> float:
        return sum(s for p, s in self.bids[i] if abs(p - px) < 1e-7)


def _ladder(prices, sizes) -> List[Tuple[float, float]]:
    prices = prices or []
    sizes = sizes or []
    return [(float(p), float(s)) for p, s in zip(prices, sizes)
            if p is not None and s is not None]


def load_books(paths: Sequence[str] = BOOK_FILES,
               coins: Optional[Sequence[str]] = None
               ) -> Dict[str, Dict[str, Any]]:
    """market_slug -> {'contract','coin','tokens': {outcome: TokenBook}}."""
    import pyarrow.parquet as pq

    cols = ["market_slug", "coin", "outcome", "token_id", "captured_at",
            "best_bid", "best_ask", "bid_prices", "bid_sizes",
            "ask_prices", "ask_sizes", "crossed", "seconds_to_close",
            "winner"]
    out: Dict[str, Dict[str, Any]] = {}
    keep = set(coins) if coins else None
    for p in paths:
        contract = "15m" if "15m" in os.path.basename(p) else "5m"
        tbl = pq.read_table(p, columns=cols)
        d = tbl.to_pydict()
        for i in range(len(d["market_slug"])):
            coin = d["coin"][i]
            if keep is not None and coin not in keep:
                continue
            slug, oc = d["market_slug"][i], d["outcome"][i]
            m = out.setdefault(slug, {"contract": contract, "coin": coin,
                                      "tokens": {}})
            tb = m["tokens"].get(oc)
            if tb is None:
                tb = m["tokens"][oc] = TokenBook(str(d["token_id"][i]), oc)
            tb.add(d["captured_at"][i].timestamp(), d["best_bid"][i],
                   d["best_ask"][i],
                   _ladder(d["bid_prices"][i], d["bid_sizes"][i]),
                   _ladder(d["ask_prices"][i], d["ask_sizes"][i]),
                   d["crossed"][i], d["seconds_to_close"][i])
            if tb.winner is None and d["winner"][i] not in (None, ""):
                tb.winner = str(d["winner"][i])
    for m in out.values():
        for tb in m["tokens"].values():
            tb.sort()
    return out


# ------------------------------------------------------------------- fees

def fee_inclusive_pair_cost(p1: float, p2: float, qty: float,
                            leg2_is_taker: bool, when: dt.datetime,
                            params: Params) -> Tuple[float, float]:
    """(fee-inclusive combined pair cost per contract, taker fee usd).

    Leg 1 is always a maker in this protocol, so it carries no venue charge.
    The maker REBATE is counted as EXACTLY ZERO -- the PMUS schedule's cent
    floor makes it structurally $0.00 below 1.62 shares, and a rebate is
    never allowed to rescue the economics here regardless.
    """
    fee = 0.0
    if leg2_is_taker:
        fee = float(FEES.taker_fee(D(str(p2)), D(str(qty)), when=when,
                                  cents=True))
    return (p1 + p2 + fee / qty), fee


# --------------------------------------------------- the three-bucket verdict

PASSIVE_BUCKETS = ("WOULD_FILL", "WOULD_NOT_FILL", "UNKNOWABLE")


def passive_bid_verdict(tb: TokenBook, px: float, t0: float,
                        horizon: float) -> Dict[str, Any]:
    """Classify a hypothetical resting BID at `px` placed at `t0`, using ONLY
    snapshots in (t0, horizon].

    The three buckets are defined in the module docstring and are never
    merged. WOULD_FILL reproduces the frozen `EVIDENCED_LEVEL_CONSUMED`
    condition of `src/polymarket_l5/owned_order_shadow.py` -- best bid
    strictly below our price -- and inherits its documented selection bias.
    """
    i0 = bisect.bisect_right(tb.t, t0)
    touched = False
    min_bid = None
    n = 0
    for i in range(i0, len(tb.t)):
        t = tb.t[i]
        if t > horizon:
            break
        n += 1
        bb = tb.bid[i]
        if bb is None:
            # an empty bid side means nothing rests anywhere, our price
            # included: the level is gone and the touch is below it.
            return {"bucket": "WOULD_FILL", "at": t, "wait_s": t - t0,
                    "evidence": "BID_SIDE_EMPTIED", "snapshots": n}
        min_bid = bb if min_bid is None else min(min_bid, bb)
        if bb < px - EPS:
            return {"bucket": "WOULD_FILL", "at": t, "wait_s": t - t0,
                    "evidence": "LEVEL_CONSUMED_TOUCH_THROUGH_PRICE",
                    "snapshots": n}
        if bb <= px + EPS:
            touched = True
    if n == 0:
        return {"bucket": "UNKNOWABLE", "at": None, "wait_s": None,
                "evidence": "NO_SNAPSHOTS_IN_HORIZON", "snapshots": 0}
    if not touched:
        return {"bucket": "WOULD_NOT_FILL", "at": None, "wait_s": None,
                "evidence": "TOUCH_NEVER_REACHED_OUR_PRICE",
                "min_best_bid": min_bid, "snapshots": n}
    return {"bucket": "UNKNOWABLE", "at": None, "wait_s": None,
            "evidence": "TOUCH_SAT_AT_OUR_PRICE_QUEUE_DECIDES",
            "snapshots": n}


def taker_cross_verdict(tb: TokenBook, limit_px: float, t0: float,
                        horizon: float, qty: float) -> Dict[str, Any]:
    """Classify an immediate-or-later CROSS that buys at or below `limit_px`.

    A taker consumes DISPLAYED depth, so queue position is irrelevant: if a
    snapshot in (t0, horizon] shows cumulative ask size at or below
    `limit_px` of at least `qty`, the fill is provable from the book. This is
    the only completion channel a book-only archive can prove.
    """
    i0 = bisect.bisect_right(tb.t, t0)
    n = 0
    for i in range(i0, len(tb.t)):
        t = tb.t[i]
        if t > horizon:
            break
        n += 1
        if tb.crossed[i]:
            continue  # a crossed book is a known archive defect; skip it
        cum = 0.0
        vwap_num = 0.0
        for p, s in sorted(tb.asks[i]):
            if p > limit_px + EPS:
                break
            take = min(s, qty - cum)
            vwap_num += take * p
            cum += take
            if cum >= qty - EPS:
                break
        if cum >= qty - EPS:
            return {"bucket": "WOULD_FILL", "at": t, "wait_s": t - t0,
                    "px": vwap_num / qty, "evidence":
                    "DISPLAYED_DEPTH_CONSUMABLE_QUEUE_IRRELEVANT",
                    "snapshots": n}
    if n == 0:
        return {"bucket": "UNKNOWABLE", "at": None, "wait_s": None,
                "evidence": "NO_SNAPSHOTS_IN_HORIZON", "snapshots": 0}
    return {"bucket": "WOULD_NOT_FILL", "at": None, "wait_s": None,
            "evidence": "DISPLAYED_DEPTH_NEVER_REACHED_LIMIT",
            "snapshots": n}


# ------------------------------------------------------------- the evaluation

def floor_tick(x: float, tick: float) -> float:
    return math.floor(x / tick + 1e-9) * tick


def market_close_t(m: Dict[str, Any]) -> Optional[float]:
    """Close instant from the archive's own `seconds_to_close`, taken at the
    LAST snapshot of each token and reconciled; None if unavailable."""
    cands = []
    for tb in m["tokens"].values():
        for i in range(len(tb.t) - 1, -1, -1):
            if tb.stc[i] is not None:
                cands.append(tb.t[i] + float(tb.stc[i]))
                break
    if not cands:
        return None
    return min(cands)


def evaluate_market(slug: str, m: Dict[str, Any], params: Params,
                    grid_s: float) -> List[Dict[str, Any]]:
    """Every evaluable state in one market, both tokens as candidate first
    legs. No side selection rule is applied: a selection rule is a parameter
    and this lane does not choose one."""
    toks = m["tokens"]
    if len(toks) != 2:
        return []
    names = sorted(toks)
    close = market_close_t(m)
    rows: List[Dict[str, Any]] = []
    for a, b in ((names[0], names[1]), (names[1], names[0])):
        ta, tbk = toks[a], toks[b]
        if not ta.t or not tbk.t:
            continue
        lo = max(ta.t[0], tbk.t[0])
        hi = min(ta.t[-1], tbk.t[-1])
        horizon_cap = hi if close is None else min(hi, close
                                                   - params.tte_cutoff_s)
        t = lo
        while t <= hi:
            t_dec = t
            t += grid_s
            if close is not None and (close - t_dec) <= params.tte_cutoff_s:
                continue  # TTE cutoff: no placement inside it
            ia = ta.idx_at_or_before(t_dec)
            ib = tbk.idx_at_or_before(t_dec)
            if ia is None or ib is None:
                rows.append({"slug": slug, "coin": m["coin"],
                             "contract": m["contract"], "side": a,
                             "t_dec": t_dec, "state": "REFUSED_STALE_BOOK"})
                continue
            if ta.crossed[ia] or tbk.crossed[ib]:
                rows.append({"slug": slug, "coin": m["coin"],
                             "contract": m["contract"], "side": a,
                             "t_dec": t_dec, "state": "REFUSED_CROSSED_BOOK"})
                continue
            bb = ta.bid[ia]
            if bb is None:
                rows.append({"slug": slug, "coin": m["coin"],
                             "contract": m["contract"], "side": a,
                             "t_dec": t_dec, "state": "REFUSED_NO_BID"})
                continue
            p1 = floor_tick(bb - params.first_leg_offset_ticks * params.tick,
                            params.tick)
            if p1 < params.min_price - EPS:
                rows.append({"slug": slug, "coin": m["coin"],
                             "contract": m["contract"], "side": a,
                             "t_dec": t_dec,
                             "state": "REFUSED_BELOW_MIN_PRICE"})
                continue
            if p1 > params.max_first_leg_price + EPS:
                rows.append({"slug": slug, "coin": m["coin"],
                             "contract": m["contract"], "side": a,
                             "t_dec": t_dec, "p1": p1,
                             "state": "REFUSED_FIRST_LEG_PRICE_CEILING"})
                continue
            row: Dict[str, Any] = {
                "slug": slug, "coin": m["coin"], "contract": m["contract"],
                "side": a, "t_dec": t_dec, "p1": round(p1, 4),
                "queue_ahead": round(ta.size_at(ia, p1), 4),
                "state": "EVALUATED",
            }
            v1 = passive_bid_verdict(ta, p1, t_dec, horizon_cap)
            row["leg1_bucket"] = v1["bucket"]
            row["leg1_evidence"] = v1["evidence"]
            row["leg1_wait_s"] = v1["wait_s"]
            # ---- leg 2, evaluated from the instant leg 1 could have filled.
            # For UNKNOWABLE/NOT_FILL leg 1 we still evaluate the complement
            # CONDITIONALLY from t_dec, labelled as conditional and never
            # counted as evidence of a matched pair.
            t1 = v1["at"] if v1["at"] is not None else t_dec
            budget = params.pair_cost_ceiling - p1
            row["complement_budget"] = round(budget, 4)
            if budget < params.min_price - EPS:
                row["leg2_maker_bucket"] = "STRUCTURALLY_INFEASIBLE"
                row["leg2_taker_bucket"] = "STRUCTURALLY_INFEASIBLE"
                row["pair_verdict"] = "RESIDUAL_PROVABLE_NO_FEASIBLE_PRICE"
                rows.append(row)
                continue
            when = dt.datetime.fromtimestamp(t1, dt.timezone.utc)
            # maker complement at the richest tick inside the budget
            p2m = floor_tick(budget, params.tick)
            p2m = min(p2m, params.max_price)
            row["p2_maker"] = round(p2m, 4)
            v2m = passive_bid_verdict(tbk, p2m, t1, horizon_cap)
            row["leg2_maker_bucket"] = v2m["bucket"]
            row["leg2_maker_evidence"] = v2m["evidence"]
            row["leg2_maker_wait_s"] = v2m["wait_s"]
            row["pair_cost_maker"] = round(p1 + p2m, 4)
            # taker complement: the limit is the richest price whose
            # FEE-INCLUSIVE pair cost still clears the ceiling.
            lim = None
            k = int(round(budget / params.tick))
            while k >= 1:
                cand = round(k * params.tick, 4)
                c, _f = fee_inclusive_pair_cost(p1, cand, params.qty, True,
                                                when, params)
                if c <= params.pair_cost_ceiling + 1e-9:
                    lim = cand
                    break
                k -= 1
            if lim is None:
                row["leg2_taker_bucket"] = "STRUCTURALLY_INFEASIBLE_AFTER_FEE"
                row["p2_taker_limit"] = None
            else:
                row["p2_taker_limit"] = lim
                v2t = taker_cross_verdict(tbk, lim, t1, horizon_cap,
                                          params.qty)
                row["leg2_taker_bucket"] = v2t["bucket"]
                row["leg2_taker_evidence"] = v2t["evidence"]
                row["leg2_taker_wait_s"] = v2t["wait_s"]
                if v2t["at"] is not None:
                    c, f = fee_inclusive_pair_cost(p1, v2t["px"], params.qty,
                                                   True, when, params)
                    row["pair_cost_taker_fee_incl"] = round(c, 5)
                    row["taker_fee_usd"] = round(f, 4)
            # ---- the pair verdict, composed WITHOUT merging buckets
            row["pair_verdict"] = compose(row)
            # ---- residual valuation uses the settled winner, read only
            # AFTER every fill verdict above is fixed.
            row["winner"] = ta.winner
            row["residual_notional_usd"] = round(p1 * params.qty, 6)
            if ta.winner is not None:
                won = (str(ta.winner).lower() == str(a).lower())
                row["residual_settle_pnl_usd"] = round(
                    (1.0 - p1) * params.qty if won else -p1 * params.qty, 6)
            rows.append(row)
    return rows


def compose(row: Dict[str, Any]) -> str:
    """The pair verdict. Leg-1 and leg-2 provability are composed
    conservatively: a pair is MATCHED_PROVABLE only when BOTH legs are
    provable from the book. Anything resting on a queue-dependent leg is
    UNKNOWABLE, and UNKNOWABLE is never collapsed into either answer."""
    l1 = row["leg1_bucket"]
    l2m = row.get("leg2_maker_bucket")
    l2t = row.get("leg2_taker_bucket")
    l2 = "WOULD_FILL" if (l2m == "WOULD_FILL" or l2t == "WOULD_FILL") else (
        "WOULD_NOT_FILL" if (l2m in ("WOULD_NOT_FILL",
                                     "STRUCTURALLY_INFEASIBLE")
                             and l2t in ("WOULD_NOT_FILL",
                                         "STRUCTURALLY_INFEASIBLE",
                                         "STRUCTURALLY_INFEASIBLE_AFTER_FEE"))
        else "UNKNOWABLE")
    if l1 == "WOULD_NOT_FILL":
        return "NO_FIRST_LEG_PROVABLE"          # no position, no exposure
    if l1 == "UNKNOWABLE":
        return "UNKNOWABLE_FIRST_LEG"           # dominant by construction
    # leg 1 provable (and therefore adverse-selected -- see S2_FINDINGS 2)
    if l2 == "WOULD_FILL":
        return "MATCHED_PROVABLE"
    if l2 == "WOULD_NOT_FILL":
        return "RESIDUAL_PROVABLE"
    return "UNKNOWABLE_COMPLEMENT"


# ------------------------------------------------------------------ summarize

def q(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return round(s[i], 2)


def cluster_bootstrap(by_cluster: Dict[str, Tuple[int, int]], iters: int,
                      seed: int) -> Optional[List[float]]:
    """Market-clustered bootstrap CI for a proportion. Clustered because
    overlapping decision states inside one market are NOT independent."""
    ks = list(by_cluster)
    if len(ks) < 2:
        return None
    rng = random.Random(seed)
    out = []
    for _ in range(iters):
        num = den = 0
        for _ in ks:
            n, d = by_cluster[rng.choice(ks)]
            num += n
            den += d
        if den:
            out.append(num / den)
    if not out:
        return None
    out.sort()
    return [round(out[int(0.025 * (len(out) - 1))], 4),
            round(out[int(0.975 * (len(out) - 1))], 4)]


def summarize(rows: List[Dict[str, Any]], params: Params,
              label: str) -> Dict[str, Any]:
    ev = [r for r in rows if r["state"] == "EVALUATED"]
    refused = collections.Counter(r["state"] for r in rows
                                  if r["state"] != "EVALUATED")
    leg1 = collections.Counter(r["leg1_bucket"] for r in ev)
    pair = collections.Counter(r["pair_verdict"] for r in ev)
    filled1 = [r for r in ev if r["leg1_bucket"] == "WOULD_FILL"]
    c1 = collections.Counter(r["pair_verdict"] for r in filled1)

    def cl(pred_num, pred_den, rs):
        d: Dict[str, Tuple[int, int]] = {}
        for r in rs:
            n, dd = d.get(r["slug"], (0, 0))
            d[r["slug"]] = (n + (1 if pred_num(r) else 0),
                            dd + (1 if pred_den(r) else 0))
        return cluster_bootstrap({k: v for k, v in d.items() if v[1]},
                                 2000, 20260821)

    l1w = [r["leg1_wait_s"] for r in filled1
           if r["leg1_wait_s"] is not None]
    matched = [r for r in filled1 if r["pair_verdict"] == "MATCHED_PROVABLE"]
    mcost = [r["pair_cost_maker"] for r in matched
             if r.get("leg2_maker_bucket") == "WOULD_FILL"]
    # Historical-replay expectancy per provable-first-leg state. A matched
    # pair locks (1 - pair cost) per contract; an unmatched first leg is
    # valued at settlement. THIS IS REPLAY, NOT REALIZED, NOT SHADOW.
    exp = []
    for r in filled1:
        if r["pair_verdict"] == "MATCHED_PROVABLE":
            c = r.get("pair_cost_taker_fee_incl")
            if r.get("leg2_maker_bucket") == "WOULD_FILL":
                c = r["pair_cost_maker"]
            if c is None:
                continue
            exp.append((1.0 - c) * params.qty)
        elif r.get("residual_settle_pnl_usd") is not None:
            exp.append(r["residual_settle_pnl_usd"])
    res_wait = [r["leg2_maker_wait_s"] for r in filled1
                if r.get("leg2_maker_bucket") == "WOULD_FILL"
                and r.get("leg2_maker_wait_s") is not None]
    tak_wait = [r["leg2_taker_wait_s"] for r in filled1
                if r.get("leg2_taker_bucket") == "WOULD_FILL"
                and r.get("leg2_taker_wait_s") is not None]
    # residual persistence = time an unmatched first leg stays unmatched.
    # Right-CENSORED at the horizon for every pair that never provably
    # matched; censoring is reported, never imputed.
    censored = sum(1 for r in filled1
                   if r["pair_verdict"] != "MATCHED_PROVABLE")
    worst = max((r.get("residual_notional_usd") or 0.0) for r in ev) if ev \
        else 0.0
    worst_path = sorted(
        (r for r in filled1 if r["pair_verdict"] != "MATCHED_PROVABLE"),
        key=lambda r: (r.get("residual_settle_pnl_usd") or 0.0))[:5]
    return {
        "label": label,
        "markets": len(set(r["slug"] for r in rows)),
        "candidate_states": len(rows),
        "evaluated_states": len(ev),
        "refused": dict(refused),
        "leg1_three_buckets": dict(leg1),
        "leg1_fractions": {k: round(v / len(ev), 4) for k, v in leg1.items()}
        if ev else {},
        "leg1_unknowable_fraction": round(
            leg1.get("UNKNOWABLE", 0) / len(ev), 4) if ev else None,
        "leg1_unknowable_ci95_market_clustered": cl(
            lambda r: r["leg1_bucket"] == "UNKNOWABLE", lambda r: True, ev),
        "pair_verdicts_all_states": dict(pair),
        "conditional_on_provable_first_leg": {
            "n": len(filled1),
            "counts": dict(c1),
            "fractions": {k: round(v / len(filled1), 4)
                          for k, v in c1.items()} if filled1 else {},
            "matched_provable_ci95_market_clustered": cl(
                lambda r: r["pair_verdict"] == "MATCHED_PROVABLE",
                lambda r: True, filled1),
            "unknowable_fraction": round(
                c1.get("UNKNOWABLE_COMPLEMENT", 0) / len(filled1), 4)
            if filled1 else None,
            "NOTE": ("this subset is the LEVEL_CONSUMED adverse-selected "
                     "subset documented in analysis/l5/august_replay/"
                     "S2_FINDINGS.md section 2, NOT a representative sample"),
        },
        "leg1_time_to_level_consumed_s": {
            "n": len(l1w), "p50": q(l1w, .5), "p75": q(l1w, .75),
            "p90": q(l1w, .9), "p99": q(l1w, .99),
            "max": round(max(l1w), 2) if l1w else None,
            "NOTE": ("how long the horizon had to run before the touch fell "
                     "through our price. A long tail here means WOULD_FILL "
                     "is being earned by HORIZON LENGTH, i.e. by the market "
                     "moving against us, which is the LEVEL_CONSUMED bias "
                     "itself and not a benign maker fill."),
        },
        "matched_pair_cost_maker_leg2": {
            "n": len(mcost), "p50": q(mcost, .5), "p05": q(mcost, .05),
            "p95": q(mcost, .95),
            "max": round(max(mcost), 4) if mcost else None,
            "all_at_or_below_ceiling": (max(mcost) <= params.pair_cost_ceiling
                                        + 1e-9) if mcost else None,
        },
        "replay_expectancy_per_provable_first_leg_usd": {
            "n": len(exp),
            "mean": round(sum(exp) / len(exp), 6) if exp else None,
            "total": round(sum(exp), 4) if exp else None,
            "LABEL": "HISTORICAL_REPLAY_ONLY_NOT_REALIZED_NOT_SHADOW",
            "NOTE": ("computed over the adverse-selected provable subset "
                     "only; the UNKNOWABLE states carry no expectancy "
                     "estimate and are NOT imputed."),
        },
        "residual_persistence_s_maker_complement": {
            "n": len(res_wait), "p50": q(res_wait, .5), "p75": q(res_wait, .75),
            "p90": q(res_wait, .9), "p95": q(res_wait, .95),
            "p99": q(res_wait, .99),
            "max": round(max(res_wait), 2) if res_wait else None,
            "right_censored_at_horizon": censored,
            "censored_fraction": round(censored / len(filled1), 4)
            if filled1 else None,
        },
        "residual_persistence_s_taker_complement": {
            "n": len(tak_wait), "p50": q(tak_wait, .5), "p75": q(tak_wait, .75),
            "p90": q(tak_wait, .9), "p95": q(tak_wait, .95),
            "p99": q(tak_wait, .99),
            "max": round(max(tak_wait), 2) if tak_wait else None,
        },
        "worst_exposure": {
            "qty_shares": params.qty,
            "worst_single_residual_notional_usd": round(worst, 6),
            "worst_single_residual_loss_usd": round(
                min((r.get("residual_settle_pnl_usd") or 0.0)
                    for r in filled1), 6) if filled1 else None,
            "worst_drawdown_path_one_market_at_a_time_usd": round(
                sum(min(0.0, r.get("residual_settle_pnl_usd") or 0.0)
                    for r in worst_path), 6),
            "worst_path_rows": [
                {k: r.get(k) for k in ("slug", "side", "t_dec", "p1",
                                       "pair_verdict",
                                       "residual_settle_pnl_usd")}
                for r in worst_path],
            "NOTE": ("settlement is read only to VALUE a residual after every "
                     "fill verdict is fixed; it never enters a fill decision"),
        },
    }


# ---------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid-s", type=float, default=15.0,
                    help="fixed decision cadence in seconds; a cadence, not "
                         "a tuned parameter")
    ap.add_argument("--out", default=os.path.join(
        HERE, "STAGE6_HISTORICAL_VALIDATION.json"))
    ap.add_argument("--rows-out", default=None)
    a = ap.parse_args()

    params = bind_params()
    books = load_books()
    two = {k: v for k, v in books.items() if len(v["tokens"]) == 2}

    inventory = {
        "sources": [{"path": os.path.relpath(p, ROOT),
                     "sha256_present": os.path.exists(p)}
                    for p in BOOK_FILES],
        "markets_total": len(books),
        "markets_two_token": len(two),
        "markets_one_token_excluded": len(books) - len(two),
        "coins": sorted(set(v["coin"] for v in two.values())),
        "by_contract": dict(collections.Counter(
            v["contract"] for v in two.values())),
        "snapshot_span_utc": None,
        "per_market_snapshot_cadence_s": None,
    }
    gaps: List[float] = []
    tmin = tmax = None
    per_market_rows = 0
    for v in two.values():
        for tb in v["tokens"].values():
            per_market_rows += len(tb.t)
            if tb.t:
                tmin = tb.t[0] if tmin is None else min(tmin, tb.t[0])
                tmax = tb.t[-1] if tmax is None else max(tmax, tb.t[-1])
            for i in range(1, len(tb.t)):
                gaps.append(tb.t[i] - tb.t[i - 1])
    inventory["snapshot_rows_two_token"] = per_market_rows
    inventory["snapshot_span_utc"] = [
        dt.datetime.fromtimestamp(tmin, dt.timezone.utc).isoformat(),
        dt.datetime.fromtimestamp(tmax, dt.timezone.utc).isoformat()]
    inventory["inter_snapshot_gap_s"] = {
        "n": len(gaps), "p50": q(gaps, .5), "p90": q(gaps, .9),
        "p99": q(gaps, .99), "max": round(max(gaps), 2) if gaps else None,
        "gaps_over_max_carry_60s": sum(1 for g in gaps if g > MAX_CARRY_S),
    }

    rows: List[Dict[str, Any]] = []
    for slug, m in sorted(two.items()):
        rows.extend(evaluate_market(slug, m, params, a.grid_s))

    l5rows = [r for r in rows if r["coin"] in L5_COINS]
    # Regime split. Every evaluable state in this archive falls on
    # 2026-08-21..24, i.e. entirely inside REGIME_2026-08-19_ON. The split is
    # computed rather than asserted so the emptiness of the earlier regimes
    # is a measured fact.
    def regime_of(ts: float) -> str:
        d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
        if d >= "2026-08-19":
            return "REGIME_2026-08-19_ON"
        if d >= "2026-06-23":
            return "REGIME_2026-06-23"
        if d >= "2026-04-25":
            return "REGIME_2026-04-25"
        return "REGIME_PRE_2026-04-25"

    by_regime: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        by_regime[regime_of(r["t_dec"])].append(r)

    out = {
        "classification": ("HISTORICAL_REPLAY_THREE_BUCKET_NOT_SHADOW_"
                           "NOT_SUBMITTED_NOT_FILLED_NOT_SETTLED_"
                           "NOT_REALIZED"),
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "decision_grid_s": a.grid_s,
        "fee_schedule_fingerprint": FEE_FINGERPRINT,
        "fee_schedule_frozen": True,
        "taker_theta_at_evaluable_states": str(FEES.taker_theta(
            dt.datetime(2026, 8, 22, tzinfo=dt.timezone.utc))),
        "taker_theta_today": str(FEES.taker_theta(
            dt.datetime(2026, 10, 7, tzinfo=dt.timezone.utc))),
        "maker_rebate_counted": "EXACTLY_ZERO_CENT_FLOOR_1.62_SHARES",
        "params_bound": params.as_dict(),
        "params_fingerprint": params.fingerprint(),
        "inventory": inventory,
        "pooled_all_coins": summarize(rows, params, "POOLED_ALL_COINS"),
        "l5_coins_only": summarize(l5rows, params, "L5_COINS_btc_eth"),
        "by_regime": {k: summarize(v, params, k)
                      for k, v in sorted(by_regime.items())},
    }
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    if a.rows_out:
        with open(a.rows_out, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
    print(json.dumps({k: out[k] for k in (
        "params_bound", "params_fingerprint", "inventory",
        "pooled_all_coins")}, indent=2, sort_keys=True)[:12000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
