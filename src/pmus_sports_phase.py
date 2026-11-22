"""Phase of a sports market for the one continuous controller (pure).

A market is quoted before its game and during it by the SAME controller.
What changes between the two is decided here, with no I/O:

  pregame  -> the game has not started; resting quotes are allowed
  kickoff  -> from a little before the start until the market has been
              re-checked as live: every resting order is cancelled and
              nothing is placed. Prices jump at the start and a bid left
              over from before it is stale by construction.
  live     -> the game is on; quoting continues with the in-game size

Also here: the price-move pause (pull quotes while the venue's own price is
moving fast, resume after a calm period), the in-game size that shrinks as
the price gets jumpy, and the one ranking number used to choose markets:
expected trading result plus the realistically achievable reward, minus the
inventory-risk charge. Rewards are estimates until the venue credits them.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

PREGAME, KICKOFF, LIVE = "pregame", "kickoff", "live"
ZERO = D("0")


def phase(*, now: float, game_start: Optional[float], pull_s: float,
          resume_min_s: float, max_hold_s: float, resumed: bool,
          confirmed_live: bool, fair_ok: bool, book_ok: bool
          ) -> Dict[str, Any]:
    """{"phase", "why", "resume", "expired"} for one market.

    `game_start` None means the market came from the live slate and has no
    start time on record: it is live. `resumed` is the engine's stored flag
    that this market already passed its kickoff re-check. `resume` True is
    the instruction to store that flag now. `expired` True means the hold
    has lasted `max_hold_s` past the start without the re-check passing:
    the engine treats the market as live but opens nothing new in it."""
    out: Dict[str, Any] = {"phase": LIVE, "why": None, "resume": False,
                           "expired": False}
    if game_start is None:
        out["why"] = "no_start_time"
        return out
    gs = float(game_start)
    if resumed:
        out["why"] = "resumed"
        return out
    if now < gs - max(0.0, pull_s):
        out.update(phase=PREGAME, why="before_pull")
        return out
    why = None
    if now < gs:
        why = "before_start"
    elif now < gs + max(0.0, resume_min_s):
        why = "minimum_hold"
    elif not confirmed_live:
        why = "not_confirmed_live"
    elif not book_ok:
        why = "no_fresh_book"
    elif not fair_ok:
        why = "fair_not_settled"
    if why is None:
        out.update(why="recheck_passed", resume=True)
        return out
    if now >= gs + max(0.0, max_hold_s):
        out.update(why="hold_expired:" + why, expired=True)
        return out
    out.update(phase=KICKOFF, why=why)
    return out


def prune(samples: list, now: float, keep_s: float) -> None:
    """Drop readings older than `keep_s` (in place), keeping one older
    reading so the start of the period stays covered."""
    while len(samples) > 1 and now - samples[1][0] >= keep_s:
        samples.pop(0)


def price_range(samples: Sequence[Tuple[float, Any]], now: float,
                window_s: float) -> D:
    """Highest minus lowest mid over the last `window_s` seconds."""
    vals = [D(str(m)) for t, m in samples if now - t <= window_s]
    if len(vals) < 2:
        return ZERO
    return max(vals) - min(vals)


def pause_until(samples: Sequence[Tuple[float, Any]], now: float, *,
                window_s: float, move_usd: Any, calm_s: float,
                prev_until: float = 0.0) -> float:
    """Epoch until which new exposure in this market is paused. A range of
    `move_usd` or more inside the window starts (or extends) a pause of
    `calm_s`; otherwise the earlier pause stands. `move_usd` <= 0 = off."""
    mv = D(str(move_usd))
    if mv <= ZERO:
        return float(prev_until or 0.0)
    if price_range(samples, now, window_s) >= mv:
        return max(float(prev_until or 0.0), now + max(0.0, calm_s))
    return float(prev_until or 0.0)


def live_qty(base: Any, min_qty: Any, rng: Any, shrink_usd: Any) -> D:
    """In-game order size: `base`, halved (never below the venue minimum,
    never above `base`) while the recent range is at least `shrink_usd`.
    `shrink_usd` <= 0 = never shrink."""
    b, m = D(str(base)), D(str(min_qty))
    s = D(str(shrink_usd))
    if s <= ZERO or D(str(rng)) < s:
        return b
    half = (b / 2).to_integral_value(rounding="ROUND_FLOOR")
    return min(b, max(m, half, D("1")))


def qty_for(ph: str, *, pregame_qty: Any, live_qty_now: Any) -> D:
    return D(str(pregame_qty)) if ph == PREGAME else D(str(live_qty_now))


def opportunity(*, edge_yes: Optional[Any], edge_no: Optional[Any],
                reward_usd_per_contract_hour: Any = 0, horizon_h: Any = 0,
                reward_haircut: Any = 1, reward_pays: bool = False
                ) -> Dict[str, Any]:
    """Ranking number for one candidate market, in dollars per contract.

    `edge_*` is the marginal test's net for a first bid on that side (fair
    value minus price, fee, adverse-selection allowance and inventory-risk
    charge), or None when there is no fair value. The better side counts:
    each side is decided on its own. The reward term is the theoretical
    reward per contract over the time the quote is expected to rest, cut by
    `reward_haircut`, and counted ONLY when the day's projected total would
    clear the venue's minimum payout. A reward never turns a market with no
    fair value into a candidate: `net` is None there."""
    edges = [D(str(e)) for e in (edge_yes, edge_no) if e is not None]
    rw = ZERO
    if reward_pays:
        rw = max(ZERO, D(str(reward_usd_per_contract_hour))
                 * D(str(horizon_h)) * D(str(reward_haircut)))
    if not edges:
        return {"net": None, "trade": None, "reward": rw, "why": "no_fair"}
    trade = max(edges)
    return {"net": trade + rw, "trade": trade, "reward": rw, "why": None}


def rank(rows: Iterable[Tuple[str, Dict[str, Any]]]) -> list:
    """[(slug, opportunity)] best first. Markets with a ranking number come
    before those without one, which keep their incoming order."""
    rows = list(rows)
    scored = [(s, o) for s, o in rows if o.get("net") is not None]
    rest = [(s, o) for s, o in rows if o.get("net") is None]
    scored.sort(key=lambda so: (-so[1]["net"], so[0]))
    return scored + rest


def at_risk_allows(current_usd: Any, order_usd: Any, budget_usd: Any
                   ) -> bool:
    """One portfolio number: unpaired filled cost plus the value of every
    resting order, plus this order, must stay within the budget. Budget
    <= 0 = no budget configured. An unknown current value refuses."""
    b = D(str(budget_usd))
    if b <= ZERO:
        return True
    cur = D(str(current_usd))
    if not cur.is_finite():
        return False
    return cur + D(str(order_usd)) <= b + D("0.000001")
