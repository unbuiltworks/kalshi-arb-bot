"""P2 -- WHERE DOES L5 ACTUALLY MAKE HIS MONEY?

Reconstructs L5's per-window INVENTORY TRAJECTORY from public activity, so the
question can be answered as a sequential process rather than a trade count.

TWO SOURCES, deliberately kept separable so a claim can be attributed:
  canonical  data/l5/normalized/fills_in_scope.jsonl   2026-03-19 -> 2026-08-31
  fresh      data/l5/sep2026/l5_activity_sep2026_raw.json.gz  2026-09-01 -> 09-10

THE UNIT IS THE WINDOW, NOT THE TRADE PAIR. L5 runs continuous inventory inside
one market window: he buys a side, the side moves, he buys it again, he buys the
complement, and the matched cost compresses as he goes. An OPEN->COMPLETE cycle
is OUR abstraction, not necessarily his, so imposing it on his tape would answer
a question nobody asked. Every metric here is therefore computed over the whole
window: qty_up(t), qty_dn(t), matched(t), residual(t), avg cost per side(t),
effective pair cost(t), max imbalance, side switches, terminal residual.

WHAT THE PUBLIC TAPE CANNOT TELL US, stated once:
  * MAKER vs TAKER -- the activity feed has no liquidity flag. Not inferred.
  * FEES -- not itemised per trade anywhere in the feed.
  * INCENTIVES ARE NOT ATTRIBUTABLE TO A MARKET. MAKER_REBATE / TAKER_REBATE /
    REWARD rows carry an EMPTY conditionId and an empty slug. They are
    account-level credits. Per the standing rule they are reported as their own
    line and NEVER folded into a market's trading P&L or spread across windows.

Read-only. No venue writes, no order path, no deploy path.
"""
from __future__ import annotations

import gzip
import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[3]
CANONICAL = ROOT / "data/l5/normalized/fills_in_scope.jsonl"
FRESH = ROOT / "data/l5/sep2026/l5_activity_sep2026_raw.json.gz"

UNKNOWN = "unknown_from_public_tape"

# `btc-updown-15m-1789320900` -- the numeric suffix IS the window start epoch,
# verified against the canonical rows' own window_start_ts field.
SLUG_RE = re.compile(r"^(?P<asset>[a-z0-9]+)-updown-(?P<mins>\d+)m-(?P<start>\d{6,})$")


def parse_slug(slug: str) -> Optional[dict]:
    m = SLUG_RE.match(str(slug or "").strip().lower())
    if not m:
        return None
    mins = int(m.group("mins"))
    start = int(m.group("start"))
    return {"asset": m.group("asset").upper(), "window_minutes": mins,
            "window_start_ts": start, "window_end_ts": start + mins * 60}


@dataclass
class Fill:
    ts: int
    outcome: str                 # "Up" / "Down"
    side: str                    # "BUY" / "SELL"
    price: float
    shares: float
    dollars: float


@dataclass
class Window:
    window_id: str
    slug: str
    asset: str
    window_minutes: int
    window_start_ts: int
    window_end_ts: int
    fills: list[Fill] = field(default_factory=list)
    winning_outcome: Optional[str] = None
    redeem_usdc: Optional[float] = None
    source: str = ""


def _f(v: Any, d: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:                                      # noqa: BLE001
        return d


def load_canonical(path: Path = CANONICAL) -> dict[str, Window]:
    """The Mar-Aug harvest, already normalised to windows upstream."""
    wins: dict[str, Window] = {}
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            wid = r["window_id"]
            w = wins.get(wid)
            if w is None:
                w = wins[wid] = Window(
                    window_id=wid, slug=r.get("market_slug") or "",
                    asset=r.get("asset") or "", window_minutes=int(r["window_minutes"]),
                    window_start_ts=int(r["window_start_ts"]),
                    window_end_ts=int(r["window_end_ts"]),
                    winning_outcome=r.get("winning_outcome") or None,
                    source="canonical")
            w.fills.append(Fill(ts=int(r["timestamp"]), outcome=r["outcome"],
                                side=r["side"], price=_f(r["price"]),
                                shares=_f(r["shares"]), dollars=_f(r["dollars"])))
    for w in wins.values():
        w.fills.sort(key=lambda f: f.ts)
    return wins


def load_fresh(path: Path = FRESH) -> tuple[dict[str, Window], dict[str, float]]:
    """The Sep 1-10 raw activity. Returns (windows, account_level_incentives).

    Incentives come back SEPARATELY and unattributed, because the venue does not
    attribute them -- their conditionId is the empty string."""
    doc = json.loads(gzip.open(path, "rt", encoding="utf-8").read())
    wins: dict[str, Window] = {}
    incentives: dict[str, float] = defaultdict(float)
    redeems: list[dict] = []
    for r in doc["rows"]:
        t = r.get("type")
        if t in ("MAKER_REBATE", "TAKER_REBATE", "REWARD"):
            incentives[t] += _f(r.get("usdcSize"))
            continue
        if t == "REDEEM":
            redeems.append(r)
            continue
        if t != "TRADE":
            continue
        meta = parse_slug(r.get("slug") or r.get("eventSlug") or "")
        if not meta:
            continue
        wid = r.get("conditionId") or r.get("slug")
        w = wins.get(wid)
        if w is None:
            w = wins[wid] = Window(window_id=wid, slug=r.get("slug") or "",
                                   asset=meta["asset"],
                                   window_minutes=meta["window_minutes"],
                                   window_start_ts=meta["window_start_ts"],
                                   window_end_ts=meta["window_end_ts"],
                                   source="fresh")
        w.fills.append(Fill(ts=int(r["timestamp"]), outcome=r.get("outcome") or "",
                            side=r.get("side") or "", price=_f(r.get("price")),
                            shares=_f(r.get("size")), dollars=_f(r.get("usdcSize"))))
    # SETTLEMENT, and the label is NOT the authority -- the MONEY is.
    #
    # 18 of 3,526 windows emit TWO REDEEM rows, one per outcome, and the LOSING
    # side's row has usdcSize == 0 while the winner's carries the payout. Taking
    # `outcome` off whichever row arrived last (and summing both usdc figures)
    # named the loser as the winner in 5 windows and would have OVERSTATED L5's
    # P&L on exactly the windows where he was wrong. A single row with
    # usdcSize == 0 is likewise a LOSS, not a win with no payout.
    #
    # So: the winner is the outcome that was actually PAID. If nothing was paid,
    # every side he held lost, and the winner is the complement.
    by_win: dict[str, list[dict]] = defaultdict(list)
    for r in redeems:
        if r.get("conditionId") in wins:
            by_win[r["conditionId"]].append(r)
    for wid, rs in by_win.items():
        w = wins[wid]
        paid = [r for r in rs if _f(r.get("usdcSize")) > 0]
        if paid:
            w.winning_outcome = paid[0].get("outcome") or None
            w.redeem_usdc = sum(_f(r.get("usdcSize")) for r in paid)
        else:
            held = {r.get("outcome") for r in rs} - {None, ""}
            w.redeem_usdc = 0.0
            w.winning_outcome = ("Down" if held == {"Up"} else
                                 "Up" if held == {"Down"} else None)
    for w in wins.values():
        w.fills.sort(key=lambda f: f.ts)
    return wins, dict(incentives)


# ---------------------------------------------------------------------------
# INVENTORY TRAJECTORY -- the whole point of P2
# ---------------------------------------------------------------------------

@dataclass
class Step:
    """One fill, and the inventory state it produced."""
    i: int
    ts: int
    t_rel: float                 # seconds from window start
    tte: float                   # seconds to window end
    outcome: str
    price: float
    shares: float
    qty_up: float
    qty_dn: float
    cost_up: float
    cost_dn: float
    matched: float
    residual: float              # +ve = long Up, -ve = long Down
    avg_up: Optional[float]
    avg_dn: Optional[float]
    pair_cost: Optional[float]   # avg_up + avg_dn, once both sides exist
    is_switch: bool              # side differs from the previous fill
    is_extend: bool              # same side as the previous fill


def trajectory(w: Window) -> list[Step]:
    qty = {"Up": 0.0, "Down": 0.0}
    cost = {"Up": 0.0, "Down": 0.0}
    out: list[Step] = []
    prev: Optional[str] = None
    for i, f in enumerate(w.fills):
        sign = -1.0 if f.side == "SELL" else 1.0
        o = f.outcome if f.outcome in qty else ("Up" if f.outcome == "Yes" else "Down")
        qty[o] += sign * f.shares
        cost[o] += sign * (f.dollars if f.dollars else f.price * f.shares)
        au = (cost["Up"] / qty["Up"]) if qty["Up"] > 1e-9 else None
        ad = (cost["Down"] / qty["Down"]) if qty["Down"] > 1e-9 else None
        out.append(Step(
            i=i, ts=f.ts, t_rel=f.ts - w.window_start_ts,
            tte=w.window_end_ts - f.ts, outcome=o, price=f.price, shares=f.shares,
            qty_up=qty["Up"], qty_dn=qty["Down"],
            cost_up=cost["Up"], cost_dn=cost["Down"],
            matched=min(qty["Up"], qty["Down"]),
            residual=qty["Up"] - qty["Down"],
            avg_up=au, avg_dn=ad,
            pair_cost=(au + ad) if (au is not None and ad is not None) else None,
            is_switch=prev is not None and o != prev,
            is_extend=prev is not None and o == prev))
        prev = o
    return out


@dataclass
class Economics:
    """Settlement decomposition. matched + residual == total, by construction,
    and `reconciles` checks that total against the venue's own REDEEM payout
    where one exists -- the only external check available on a public tape."""
    matched_qty: float
    pair_cost: Optional[float]
    matched_pnl: Optional[float]
    residual_qty: float          # signed: +Up / -Down
    residual_side: Optional[str]
    residual_avg_cost: Optional[float]
    residual_pnl: Optional[float]
    total_spend: float
    payout_modelled: Optional[float]
    total_pnl: Optional[float]
    redeem_usdc: Optional[float]
    reconciles: Optional[bool]


def economics(w: Window, steps: list[Step], tol: float = 0.02) -> Economics:
    if not steps:
        return Economics(0, None, None, 0, None, None, None, 0, None, None,
                         w.redeem_usdc, None)
    s = steps[-1]
    matched = max(0.0, min(s.qty_up, s.qty_dn))
    au, ad = s.avg_up, s.avg_dn
    pair = (au + ad) if (au is not None and ad is not None) else None
    # A matched pair pays exactly $1 at settlement regardless of the winner.
    matched_pnl = matched * (1.0 - pair) if pair is not None else None
    resid = s.qty_up - s.qty_dn
    rside = "Up" if resid > 1e-9 else ("Down" if resid < -1e-9 else None)
    ravg = au if rside == "Up" else (ad if rside == "Down" else None)
    rpnl = None
    if rside and ravg is not None and w.winning_outcome:
        won = 1.0 if w.winning_outcome == rside else 0.0
        rpnl = abs(resid) * (won - ravg)
    spend = s.cost_up + s.cost_dn
    payout = total = None
    if w.winning_outcome:
        win_qty = s.qty_up if w.winning_outcome == "Up" else s.qty_dn
        payout = max(0.0, win_qty)          # $1 per winning share
        total = payout - spend
    rec = None
    if w.redeem_usdc is not None and payout is not None:
        rec = abs(w.redeem_usdc - payout) <= max(tol, 0.001 * max(payout, 1.0))
    return Economics(matched, pair, matched_pnl, resid, rside, ravg, rpnl,
                     spend, payout, total, w.redeem_usdc, rec)
