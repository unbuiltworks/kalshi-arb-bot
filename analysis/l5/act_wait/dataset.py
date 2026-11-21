"""ACT/WAIT: turn L5's fill tape into a classification dataset with real negatives.

THE MODELLING MISMATCH THIS EXISTS TO FIX. The deployed v2 artifact is
`P(next action | L5 just acted, state, previous action)` -- a TRANSITION model
over 69,646 fills, with a relation vocabulary of {open, complete, extend} and no
wait/hold/none token anywhere. V1 queries it as though it were
`P(L5 acts | observable market state)`, so `P(act) = 1` by construction and the
controller proposes on ~79.5% of rows, every WAIT coming from a risk cap rather
than from the model. Adding WAIT as a fourth relation to a transition model
would not fix that: the corpus contains no example of L5 declining, because it
is built from fills.

So the negatives have to be CONSTRUCTED, and they have to be real.

## What is actually observable at time T, and what is not

Source: `analysis/l5/l5_canonical_sequence.csv.gz` -- 71,984 L5 fills
reconstructed into complete up/down windows, carrying `seconds_to_settlement`,
`price`, cumulative up/down quantity and spend, matched, signed residual, and
window identity.

**Knowable at T** (and therefore allowed as features):

- the last price L5 traded in this market, carried forward. This is exactly the
  `last_price` the deployed `state_key` already consumes, so we are not
  inventing a feature the model never had;
- seconds to settlement at T, from the window's settlement time, which is known
  ex ante rather than in hindsight;
- L5's inventory carried forward: cumulative up/down quantity, matched, signed
  residual;
- `last_rel` and `last_size` -- L5's own previous action;
- elapsed since L5's last action, and the action index within the window.

**NOT available, and deliberately not manufactured:**

- the order book at T. No bid, no ask, no spread, no depth. We hold L5's own
  fill tape, not a market tape;
- any other trader's activity at T;
- whether a quote L5 had resting was filled, cancelled or repriced. We see
  fills, never orders;
- maker/taker on 5,269 of the 71,984 rows, which the corpus marks `unknown`.

## The scope limit this imposes, stated rather than buried

Because every negative is sampled from INSIDE a window L5 is already trading,
this dataset estimates

    P(L5 acts within H | L5 is active in this market, state carried forward)

and **not** `P(L5 enters a market it is not in)`. We have no negatives for
markets L5 never touched -- we cannot know which markets it looked at and
declined, because we only see where it traded.

That is a real limit and it bounds what the model may be used for. It is also
where the decision volume is: V1's proposals are ~65% extend and ~33% complete
against ~2% open, so this covers ~98% of the decisions V1 actually makes. The
OPEN/entry decision stays out of scope until a collector records the candidate
universe L5 could have entered, which is noted as the smallest honest addition.

## Opportunity sampling

Labelling every second of inactivity would produce hundreds of thousands of
near-identical negatives that differ only in a clock reading, and any classifier
trained on that measures the sampling grid rather than L5. Opportunities are
therefore drawn on a fixed grid of `interval_s`, and `SAMPLING_INTERVALS`
exists so the sensitivity of every headline number to that choice is reported
rather than assumed away.

There is no special-casing of pre-fill instants. A positive is simply a grid
point from which L5 did act within the horizon, so the base rate falls out of
the data instead of being set by how many positives we chose to add.

Chronological order is preserved throughout; `split()` cuts by time, never at
random, so no window contributes to both sides of a split.
"""
from __future__ import annotations

import csv
import gzip
import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

CANONICAL = "analysis/l5/l5_canonical_sequence.csv.gz"

#: Forward horizons, in seconds. Chosen to match the cadence buckets the
#: artifact already carries (`gap<2s`, `gap2-10`, `gap10-30`, `gap>30`) so the
#: ACT-within-H formulation and the four-way timing formulation are measured on
#: the same boundaries rather than on a threshold invented here.
HORIZONS_S = (2, 10, 30, 120)

#: Reported for every headline number. A result that only survives at one
#: spacing is a result about the spacing.
SAMPLING_INTERVALS_S = (5, 15, 30, 60)

SIZE_BUCKETS = (("q<5", 5.0), ("q5-15", 15.0), ("q15-40", 40.0), ("q>40", float("inf")))


def size_bucket(qty: float) -> str:
    for name, hi in SIZE_BUCKETS:
        if qty < hi:
            return name
    return "q>40"


def relation_of(row: Mapping_, prev_up: float, prev_dn: float) -> str:
    """OPEN / EXTEND / COMPLETE from inventory movement, L5's own convention.

    OPEN when there was no position, COMPLETE when this fill adds to the side
    that is short of the other, EXTEND otherwise. Derived from the cumulative
    columns rather than guessed from the side.
    """
    up, dn = _f(row["cum_up_qty"]), _f(row["cum_down_qty"])
    if prev_up <= 0 and prev_dn <= 0:
        return "open"
    grew_up = up > prev_up
    # the side that was BEHIND before this fill is the one a complete fills
    behind_up = prev_up < prev_dn
    if (grew_up and behind_up) or ((not grew_up) and (not behind_up) and prev_dn < prev_up):
        return "complete"
    return "extend"


try:                                   # typing convenience only
    from typing import Mapping as Mapping_
except ImportError:                    # pragma: no cover
    Mapping_ = dict                    # type: ignore


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Fill:
    ts: int
    window: str
    slug: str
    price: float
    qty: float
    tte_s: float
    up: float
    dn: float
    matched: float
    residual: float
    relation: str
    seq: int


def load_windows(path: str = CANONICAL) -> Dict[str, List[Fill]]:
    """Every window's fills, in chronological order, with relations derived."""
    by: Dict[str, List[dict]] = {}
    with gzip.open(path, "rt") as fh:
        for row in csv.DictReader(fh):
            by.setdefault(row["window_id"], []).append(row)
    out: Dict[str, List[Fill]] = {}
    for win, rows in by.items():
        rows.sort(key=lambda r: (int(r["timestamp"]), int(r["trade_seq"])))
        fills, pu, pd = [], 0.0, 0.0
        for r in rows:
            rel = relation_of(r, pu, pd)
            fills.append(Fill(
                ts=int(r["timestamp"]), window=win, slug=r["market_slug"],
                price=_f(r["price"]), qty=_f(r["quantity"]),
                tte_s=_f(r["seconds_to_settlement"]),
                up=_f(r["cum_up_qty"]), dn=_f(r["cum_down_qty"]),
                matched=_f(r["matched_qty"]),
                residual=_f(r["residual_qty_signed"]),
                relation=rel, seq=int(r["trade_seq"])))
            pu, pd = _f(r["cum_up_qty"]), _f(r["cum_down_qty"])
        out[win] = fills
    return out


@dataclass(frozen=True)
class Opportunity:
    """One decision point. Every field is knowable at `ts`; nothing looks ahead
    except `acted_within` and `next_gap_s`, which are the LABELS."""
    ts: int
    window: str
    slug: str
    # --- features, all carried forward from the last fill at or before ts
    last_price: float
    tte_s: float
    up: float
    dn: float
    matched: float
    residual: float
    last_rel: str
    last_size: str
    since_last_s: float
    actions_so_far: int
    # --- labels
    next_gap_s: Optional[float]
    acted_within: Dict[int, int]


def opportunities(fills: Sequence[Fill], interval_s: int = 15,
                  horizons: Sequence[int] = HORIZONS_S) -> Iterator[Opportunity]:
    """Grid opportunities inside one window, from the first fill onward.

    Sampling starts AFTER the first fill because before it there is no carried
    state to describe -- and, more honestly, because before the first fill we
    have no evidence L5 was watching this market at all.
    """
    if len(fills) < 2:
        return
    end = fills[-1].ts
    i = 0
    t = fills[0].ts + interval_s
    while t <= end:
        while i + 1 < len(fills) and fills[i + 1].ts <= t:
            i += 1
        cur = fills[i]
        nxt = next((f for f in fills[i + 1:] if f.ts > t), None)
        gap = (nxt.ts - t) if nxt else None
        yield Opportunity(
            ts=t, window=cur.window, slug=cur.slug,
            last_price=cur.price,
            tte_s=cur.tte_s - (t - cur.ts),
            up=cur.up, dn=cur.dn, matched=cur.matched, residual=cur.residual,
            last_rel=cur.relation, last_size=size_bucket(cur.qty),
            since_last_s=float(t - cur.ts), actions_so_far=i + 1,
            next_gap_s=float(gap) if gap is not None else None,
            acted_within={h: int(gap is not None and gap <= h) for h in horizons})
        t += interval_s


def build(path: str = CANONICAL, interval_s: int = 15,
          horizons: Sequence[int] = HORIZONS_S) -> List[Opportunity]:
    """Every opportunity across every window, in chronological order."""
    wins = load_windows(path)
    out: List[Opportunity] = []
    for fills in wins.values():
        out.extend(opportunities(fills, interval_s, horizons))
    out.sort(key=lambda o: (o.ts, o.window))
    return out


def split(rows: Sequence[Opportunity], train_frac: float = 0.7
          ) -> Tuple[List[Opportunity], List[Opportunity]]:
    """CHRONOLOGICAL split, and no window straddles the cut.

    A random split would leak: opportunities inside one window are minutes apart
    and share carried-forward state, so a randomly held-out point sits between
    two training points from the same window and is nearly free to predict. The
    cut is made in time and then pushed forward past any window that spans it.
    """
    if not rows:
        return [], []
    ordered = sorted(rows, key=lambda o: (o.ts, o.window))
    cut_i = int(len(ordered) * train_frac)
    cut_t = ordered[cut_i].ts
    train_windows = {o.window for o in ordered if o.ts < cut_t}
    train = [o for o in ordered if o.window in train_windows]
    test = [o for o in ordered if o.window not in train_windows]
    return train, test


def counts(rows: Sequence[Opportunity], horizons: Sequence[int] = HORIZONS_S) -> dict:
    n = len(rows)
    out: Dict[str, Any] = {
        "opportunities": n,
        "windows": len({o.window for o in rows}),
        "markets": len({o.slug for o in rows}),
        "span_utc": ([_iso(min(o.ts for o in rows)), _iso(max(o.ts for o in rows))]
                     if rows else []),
        "base_rate": {}, "positives": {},
    }
    for h in horizons:
        pos = sum(o.acted_within[h] for o in rows)
        out["positives"][h] = pos
        out["base_rate"][h] = round(pos / n, 6) if n else 0.0
    gaps = sorted(o.next_gap_s for o in rows if o.next_gap_s is not None)
    if gaps:
        out["next_gap_s"] = {
            "p10": gaps[int(.10 * len(gaps))], "p50": gaps[len(gaps) // 2],
            "p90": gaps[int(.90 * len(gaps))], "max": gaps[-1]}
    return out


def _iso(ts: int) -> str:
    import datetime as _dt
    return _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def as_dict(o: Opportunity) -> dict:
    d = asdict(o)
    d["acted_within"] = {str(k): v for k, v in o.acted_within.items()}
    return d
