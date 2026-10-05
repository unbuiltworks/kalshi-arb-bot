"""Causal BRTI settlement-state arithmetic for PMUS BTC15m.

SEPARATE STUDY. This does not touch, re-tune or re-read the frozen underdog
matrix (`src/pmus/btc15m_spec.py`, `PMUS_BTC15M_ECONOMICS.md`). It is a new
descriptive layer.

THE SETTLEMENT STATISTIC, from the venue's own rules via `src/pmus_brti.py`:
the window's close statistic is the simple average of the SIXTY one-second BRTI
closes on bucket starts [T-60, T-1], rounded half-even to the cent. The strike
(`priceToBeat`) is the same statistic taken at the window OPEN. Ties settle UP:
the outcome is UP iff close >= open.

PUBLICATION DISCIPLINE, AND WHY IT DIFFERS FROM THE BUCKET STAMP.
A 1-second bucket stamped `s` covers [s, s+1) and is not complete until the
second ends, so its close cannot be known before `s + 1`. Every count and every
level here is therefore taken from availability `s + 1`, never from the stamp.
This is rule 1 of the programme's state-conditional preregistration and it is
what separates an information lead from a timestamp illusion.

The canonical `pmus_brti.settlement_minute_published` counts by bucket STAMP and
so reaches 60 at `t = T - 1`. Counting by availability, 60 observations are not
all published until `t = T`. This module counts by availability, which is the
stricter and the only actionable reading, and the difference is exactly one
observation.

A CONSEQUENCE WORTH STATING PLAINLY: inside the final 60 seconds the number of
remaining unpublished settlement observations is always at least one. No outcome
is ever arithmetically determined before T, however improbable a reversal is.
This module therefore reports the REQUIRED remaining average and the size of the
move it needs -- in dollars, in basis points and in units of the volatility
available at that instant -- and never emits a "locked" flag.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, asdict
from decimal import Decimal as D, ROUND_HALF_EVEN
from typing import Dict, List, Optional, Sequence, Tuple

#: the settlement statistic's parameters, from the venue's market rules
STAT_N = 60
STAT_LO, STAT_HI = -60, -1
CENT = D("0.01")

#: Below this dollar standard deviation the sigma normalisation is meaningless
#: and explodes: a near-constant index makes any required move look like
#: millions of sigmas. Such states report ``required_move_sigmas = None`` and
#: are counted as "unknown" rather than as an astronomically safe state.
MIN_SIGMA_DOLLARS = 0.01

__all__ = [
    "settlement_buckets", "n_published", "known_contribution",
    "required_remaining_average", "projected_settlement",
    "realized_vol_1s", "brti_change", "causal_level", "SecondState",
    "second_state", "executable_from_book",
]


def settlement_buckets(window_end_unix: int) -> range:
    """The bucket starts the close statistic averages: [T-60, T-1]."""
    return range(window_end_unix + STAT_LO, window_end_unix + STAT_HI + 1)


def n_published(window_end_unix: int, t: float) -> int:
    """How many of the 60 settlement observations are AVAILABLE at instant t.

    Bucket `s` becomes available at `s + 1`, so the count is
    ``floor(t) - (T - 60)`` clamped to [0, 60]. At t = T - 1 this is 59, not 60.
    """
    return max(0, min(STAT_N, int(math.floor(t)) - (window_end_unix + STAT_LO)))


def known_contribution(candles: Dict[int, dict], window_end_unix: int,
                       t: float) -> Tuple[Optional[D], int, int]:
    """(sum of the available settlement closes, n_available, n_zero_tick).

    Returns None for the sum if any available bucket is missing from the
    candles, because inventing one would fabricate index data.
    """
    n = n_published(window_end_unix, t)
    if n <= 0:
        return D(0), 0, 0
    lo = window_end_unix + STAT_LO
    total, zero = D(0), 0
    for s in range(lo, lo + n):
        c = candles.get(s)
        if c is None:
            return None, n, zero
        total += c["close"]
        if c.get("ticks", 0) == 0:
            zero += 1
    return total, n, zero


def required_remaining_average(known_sum: D, n_known: int,
                               strike: D) -> Optional[D]:
    """The remaining observations' average that lands settlement exactly on the
    strike.

    settlement = (known_sum + n_rem * avg_rem) / 60, and the outcome flips at
    settlement == strike (ties settle UP), so

        avg_rem* = (60 * strike - known_sum) / n_rem

    Above avg_rem* the window settles UP, below it DOWN. Returns None when no
    observations remain, which inside the final 60 seconds cannot happen.
    """
    n_rem = STAT_N - n_known
    if n_rem <= 0:
        return None
    return (D(STAT_N) * strike - known_sum) / D(n_rem)


def projected_settlement(known_sum: D, n_known: int, level: D) -> D:
    """Settlement if every remaining observation printed at `level`.

    This is the running statistic a trader can actually compute: it is NOT a
    forecast, it is the arithmetic consequence of the index standing still.
    """
    n_rem = STAT_N - n_known
    return ((known_sum + D(n_rem) * level) / D(STAT_N)).quantize(
        CENT, rounding=ROUND_HALF_EVEN)


def causal_level(candles: Dict[int, dict], t: float,
                 lookback: int = 120) -> Optional[D]:
    """The newest BRTI close AVAILABLE at t (bucket s counts from s + 1)."""
    s = int(math.floor(t)) - 1
    for k in range(s, s - lookback, -1):
        c = candles.get(k)
        if c is not None:
            return c["close"]
    return None


def brti_change(candles: Dict[int, dict], t: float,
                seconds: int) -> Optional[D]:
    """Change in the available BRTI level over the prior `seconds`."""
    now = causal_level(candles, t)
    then = causal_level(candles, t - seconds)
    if now is None or then is None:
        return None
    return now - then


def realized_vol_1s(candles: Dict[int, dict], t: float,
                    seconds: int = 60) -> Optional[Dict[str, float]]:
    """Trailing realized volatility from AVAILABLE one-second closes.

    Returns the per-second standard deviation of log returns, the same figure in
    basis points, and the dollar standard deviation scaled to the window of
    `seconds` (sigma_1s * sqrt(seconds) * level) -- the natural yardstick for
    "how big is the move this state would need".
    """
    end = int(math.floor(t)) - 1
    closes: List[D] = []
    for s in range(end - seconds, end + 1):
        c = candles.get(s)
        if c is not None:
            closes.append(c["close"])
    if len(closes) < 10:
        return None
    rets = [math.log(float(b) / float(a))
            for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 9:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    level = float(closes[-1])
    return {
        "sigma_1s": sd,
        "sigma_1s_bp": sd * 1e4,
        "n_returns": len(rets),
        "sigma_dollars_over_window": sd * math.sqrt(seconds) * level,
    }


def executable_from_book(row: dict) -> Optional[Dict[str, object]]:
    """Both executable prices and capacities from one Yes/UP-side snapshot.

    A matched UP+DOWN pair pays $1.00, so buying DOWN at the touch is taking the
    other side of the displayed UP bid:

        UP   ask = ask          capacity = ask_qty
        DOWN ask = 1 - bid      capacity = bid_qty

    Both sides therefore come from the SAME snapshot. A one-sided book has no
    executable price for the missing side and is refused rather than guessed.
    """
    bid, ask = row.get("bid"), row.get("ask")
    if bid is None or ask is None:
        return None
    return {
        "up_ask": ask, "up_capacity": row.get("ask_qty"),
        "down_ask": D(1) - bid, "down_capacity": row.get("bid_qty"),
        "displayed_bid": bid, "displayed_ask": ask,
        "spread": ask - bid, "t": row["t"],
    }


def book_at(times: Sequence[float], rows: Sequence[dict],
            t: float) -> Optional[dict]:
    """The newest book snapshot stamped at or before t. Strictly causal."""
    i = bisect.bisect_right(times, t) - 1
    return rows[i] if i >= 0 else None


@dataclass(frozen=True)
class SecondState:
    """One second of the final minute, as a trader could have seen it."""

    slug: str
    window_end_unix: int
    t: int
    seconds_remaining: int
    n_known: int
    n_remaining: int
    known_sum: Optional[str]
    known_mean: Optional[str]
    zero_tick_known: int
    strike: Optional[str]
    brti: Optional[str]
    required_remaining_avg: Optional[str]
    required_move_dollars: Optional[str]
    required_move_bp: Optional[float]
    required_move_sigmas: Optional[float]
    projected_settlement: Optional[str]
    projected_side: Optional[str]
    up_ask: Optional[str]
    down_ask: Optional[str]
    up_capacity: Optional[str]
    down_capacity: Optional[str]
    displayed_bid: Optional[str]
    displayed_ask: Optional[str]
    quote_age_s: Optional[float]
    sigma_1s_bp: Optional[float]
    settled_outcome: Optional[str]

    def as_dict(self) -> dict:
        return asdict(self)


def second_state(slug: str, window_end_unix: int, t: int,
                 candles: Dict[int, dict], strike: Optional[D],
                 times: Sequence[float], rows: Sequence[dict],
                 settled_outcome: Optional[str] = None) -> SecondState:
    """Assemble one second of settlement state plus the executable book.

    `settled_outcome` is carried for LABELLING ONLY and enters no computed
    field here.
    """
    ks, n_known, zero = known_contribution(candles, window_end_unix, t)
    n_rem = STAT_N - n_known
    brti = causal_level(candles, t)
    vol = realized_vol_1s(candles, t)

    req = move_d = proj = proj_side = None
    move_bp = move_sig = None
    if ks is not None and strike is not None:
        req = required_remaining_average(ks, n_known, strike)
        if req is not None and brti is not None:
            move_d = req - brti
            if brti > 0:
                move_bp = float(move_d / brti) * 1e4
            if vol and vol["sigma_dollars_over_window"] >= MIN_SIGMA_DOLLARS:
                move_sig = float(move_d) / vol["sigma_dollars_over_window"]
        if brti is not None:
            proj = projected_settlement(ks, n_known, brti)
            proj_side = "UP" if proj >= strike else "DOWN"

    b = book_at(times, rows, float(t))
    exe = executable_from_book(b) if b is not None else None

    def s(v):
        return None if v is None else str(v)

    return SecondState(
        slug=slug, window_end_unix=window_end_unix, t=t,
        seconds_remaining=window_end_unix - t,
        n_known=n_known, n_remaining=n_rem,
        known_sum=s(ks),
        known_mean=s(None if (ks is None or not n_known) else ks / D(n_known)),
        zero_tick_known=zero,
        strike=s(strike), brti=s(brti),
        required_remaining_avg=s(req),
        required_move_dollars=s(move_d),
        required_move_bp=(None if move_bp is None else round(move_bp, 4)),
        required_move_sigmas=(None if move_sig is None else round(move_sig, 4)),
        projected_settlement=s(proj), projected_side=proj_side,
        up_ask=s(exe and exe["up_ask"]), down_ask=s(exe and exe["down_ask"]),
        up_capacity=s(exe and exe["up_capacity"]),
        down_capacity=s(exe and exe["down_capacity"]),
        displayed_bid=s(exe and exe["displayed_bid"]),
        displayed_ask=s(exe and exe["displayed_ask"]),
        quote_age_s=(None if exe is None else round(float(t) - exe["t"], 3)),
        sigma_1s_bp=(None if vol is None else round(vol["sigma_1s_bp"], 4)),
        settled_outcome=settled_outcome,
    )
