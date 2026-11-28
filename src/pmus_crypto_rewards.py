"""Daily-crypto liquidity-reward controller: decisions, limits, accounting.

A lane of its own inside the sports engine (src/pmus_sports_two_sided.py).
The engine keeps every side effect -- placement, exact-id cancel, fills, the
durable book, the watchdog registry, shutdown -- and calls this module for a
crypto market INSTEAD of the RN1 / two-sided sports decisions. This module is
PURE: no network, no credentials, no clock of its own.

DEFAULT OFF AND DISARMED. Three independent keys, all required to place:
  PMUS_CRYPTO_REWARDS=1          controller on (alone: SHADOW -- it selects,
                                 prices, models and logs; it places nothing)
  PMUS_CRYPTO_ARM=YES            owner arms the pilot
  PMUS_CRYPTO_PILOT_APPROVAL     owner approval document (JSON) naming the
                                 exact release SHA, window and limits; every
                                 field must equal the running configuration
The sports lane's own lock, halt and account gates still apply on top.

MARKETS. ETH, SOL and XRP daily up/down only (slug table below). BTC is not
in the table and cannot be configured in.

VENUE RULES (docs.polymarket.us/incentives/liquidity, re-read 2026-11-22;
live programme crypto_alt_updown_1d_20261110: pool $150/day shared by the
three markets, discountFactor 0.3, targetSize 2000, maxSpread 0.05, period
daily_event):
  * score = DF ** ticks_from_best * size, per order, per side, every second;
  * per side the venue walks from the best price outward until cumulative
    RAW size reaches Target Size. Orders inside the walk score, orders
    beyond it score ZERO -- so "one tick behind" earns nothing whenever the
    best level alone already holds Target Size;
  * there is NO minimum qualifying order size (Target Size is a test of the
    whole side, not of one order);
  * Max Spread: each side's size-adjusted price (the price at which its walk
    reaches Target Size) must be within Max Spread of the midpoint OF THOSE
    TWO PRICES, else nobody is paid that second and the second is forfeited;
    a side that never reaches Target Size forfeits the second too;
  * each side is normalised to 1.0 per paying second (two equal halves);
  * rewards under $1.00 are not paid; calculated within 5 business days of
    the period's end and credited within 2 more.
`assess` applies exactly this. (src/pmus_sports_rewards.py measures Max
Spread from the touch mid with an average price -- the older reading; it is
left untouched for sports.)

LABELS. Three figures that are never added together unless stated:
  MODELLED   our share of the displayed book x the published pool. Not money.
  VENUE      reward rows the venue lists for a crypto market; only a row in a
             paid status is money (`credited`), the rest is `pending`.
  REALIZED   venue-reconciled cash result of the crypto markets (pairs paid
             at pairing, settled residual legs, fees charged), plus CREDITED
             rewards.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

ONE, ZERO = D(1), D(0)
EPS = 1e-9

#: the only markets this lane may trade; a slug must start with one of these
ASSET_PREFIX = {"eth": "cpceth-eth-updown-1d-",
                "sol": "cpcsol-sol-updown-1d-",
                "xrp": "cpcxrp-xrp-updown-1d-"}
STAGE_OPEN, STAGE_COMPLETE = "crypto_open", "crypto_complete"
STAGES = (STAGE_OPEN, STAGE_COMPLETE)
LANE = "crypto"
#: owner 2026-11-22: $300 is the MAXIMUM crypto allocation. A constant, not a
#: setting -- configuration can lower the total, never raise it past this.
HARD_TOTAL_USD = D("300")
HARD_MARKET_DAYS = 3


def _d(name: str, default: str) -> D:
    try:
        v = D(str(os.getenv(name, default)).strip())
        return v if v.is_finite() else D(default)
    except Exception:                                    # noqa: BLE001
        return D(default)


def _f(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def _i(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return int(default)


ON_ENV, ARM_ENV = "PMUS_CRYPTO_REWARDS", "PMUS_CRYPTO_ARM"
APPROVAL_ENV = "PMUS_CRYPTO_PILOT_APPROVAL"
COMBINED_CAP_ENV = "PMUS_COMBINED_MAX_EXPOSURE_USD"
SHA_ENV = "RAILWAY_GIT_COMMIT_SHA"

ON = os.getenv(ON_ENV, "0").strip() == "1"
ASSETS = tuple(a for a in (x.strip().lower() for x in os.getenv(
    "PMUS_CRYPTO_ASSETS", "eth,sol,xrp").split(",")) if a in ASSET_PREFIX)
QTY = _d("PMUS_CRYPTO_QTY", "100")                 # contracts per side
MIN_QTY = _d("PMUS_CRYPTO_MIN_QTY", "25")          # smaller is not worth risk
DEPTH_TICKS = _i("PMUS_CRYPTO_DEPTH_TICKS", "1")   # behind the best price
#: join the best price when one tick behind is outside the scoring walk.
#: OFF: at the best price the modelled share is ~3x and so is the pick-off.
ALLOW_TOUCH = os.getenv("PMUS_CRYPTO_ALLOW_TOUCH", "0").strip() == "1"
#: quote only when BOTH sides are reward-eligible (simultaneous YES/NO)
REQUIRE_BOTH = os.getenv("PMUS_CRYPTO_REQUIRE_BOTH", "1").strip() != "0"
PER_MARKET_USD = _d("PMUS_CRYPTO_PER_MARKET_USD", "100")
TOTAL_USD = min(_d("PMUS_CRYPTO_TOTAL_USD", "300"), HARD_TOTAL_USD)
MAX_UNPAIRED_USD = _d("PMUS_CRYPTO_MAX_UNPAIRED_USD", "150")
MAX_OPEN_ORDERS = _i("PMUS_CRYPTO_MAX_OPEN_ORDERS", "6")
MAX_MARKETS = _i("PMUS_CRYPTO_MAX_MARKETS", "3")
LOSS_STOP_USD = _d("PMUS_CRYPTO_LOSS_STOP_USD", "30")
#: opening pair (both bids + maker fees) must cost no more than this
PAIR_CAP = _d("PMUS_CRYPTO_PAIR_CAP", "0.99")
#: a completion never locks a pair above this (1.00 = never a locked loss)
COMPLETE_MAX_PAIR = _d("PMUS_CRYPTO_COMPLETE_MAX_PAIR", "1.00")
PX_MIN = _d("PMUS_CRYPTO_PX_MIN", "0.05")
PX_MAX = _d("PMUS_CRYPTO_PX_MAX", "0.95")
#: maker fee per contract = rate * p * (1 - p). Order previews show 0 bps
#: (2026-11-21); what the venue actually charged is measured, not assumed.
MAKER_FEE_RATE = _d("PMUS_CRYPTO_MAKER_FEE_RATE", "0")
CUTOFF_S = _f("PMUS_CRYPTO_CUTOFF_S", "7200")      # no new quotes this late
BOOK_S = _f("PMUS_CRYPTO_BOOK_S", "10")            # depth re-read interval
#: the feed thread re-reads the crypto event list and programmes this often
DISCOVER_S = _f("PMUS_CRYPTO_DISCOVER_S", "300")
#: a market whose open/tick spec is older than this is not quoted
SPEC_MAX_AGE_S = _f("PMUS_CRYPTO_SPEC_MAX_AGE_S", "300")
BOOK_MAX_AGE_S = _f("PMUS_CRYPTO_BOOK_MAX_AGE_S", "30")
MARKET_DAYS = min(_i("PMUS_CRYPTO_MARKET_DAYS", "3"), HARD_MARKET_DAYS)
#: the venue-reconciled result must be this fresh or new quotes stop
METER_MAX_AGE_S = _f("PMUS_CRYPTO_METER_MAX_AGE_S", str(2 * 3600 + 600))
#: paired YES+NO inventory keeps counting as deployed capital until the
#: market settles. 0 only once the venue is confirmed to net pairs to cash.
COUNT_PAIRED = os.getenv("PMUS_CRYPTO_COUNT_PAIRED", "1").strip() != "0"
#: HARD LOSS BUDGET (owner 2026-11-22: "losses that can exceed the proposed
#: $30 stop" is an unresolved risk). ON (default): quotes are SIZED so that
#: the worst settlement of everything the lane holds or could be left
#: holding -- every unpaired leg lost in full, plus the dearest leg each
#: market's resting quotes could strand -- together with losses already
#: closed, cannot exceed LOSS_STOP_USD. The budget is split evenly over
#: MAX_MARKETS. The stop then is a bound, not only a trigger (maker fees,
#: previewed at 0, are outside it). OFF: sizes follow QTY and a held leg can
#: lose more than the stop; the approval must say so explicitly.
HARD_LOSS_BUDGET = os.getenv("PMUS_CRYPTO_HARD_LOSS_BUDGET",
                             "1").strip() != "0"
MARKOUT_S = (60, 300, 1800)
PAYOUT_FLOOR_USD = D("1.00")

# ----------------------------------------------------------- reward-aware
# REWARD-AWARE MODE (default OFF; needs PMUS_CRYPTO_REWARDS=1 as well, and
# places nothing without the arm key and a signed approval that repeats
# every setting below). Same lane, same controller, same choke point:
#   * both sides quoted INSIDE the scoring walk (so inside Max Spread), the
#     pair of bids stepped back until it fits the pair cap;
#   * controlled asymmetric inventory: the heavier side may keep quoting
#     until the market's unpaired contracts reach RA_MAX_UNPAIRED_QTY;
#   * the lighter side is an economical completion: priced no higher than
#     the pair cap allows, never forced, never a taker;
#   * replenished only while the reward-adjusted net estimate is positive
#     (src/pmus_crypto_reward_economics.py). With the allocation of the
#     pool unknown NO reward is credited and nothing opens, unless the
#     owner names one hypothesis (RA_ALLOCATION) for a bounded probe;
#   * quotes are pulled RA_PULL_S before the window's fixing minute ends,
#     held back RA_OPEN_DELAY_S after it starts, and paused on a jump of the
#     venue's own reference index or of the venue mid.
# The hourly directional model (src/pmus_crypto_hourly_fair.py) is NOT used:
# nothing here reads a fair value, priceToBeat or a forecast.
RA_ENV = "PMUS_CRYPTO_RA"
RA_ON = ON and os.getenv(RA_ENV, "0").strip() == "1"
#: market windows the mode may quote. "1d" (default) or "1d,1h".
RA_SERIES = tuple(x for x in (y.strip().lower() for y in os.getenv(
    "PMUS_CRYPTO_RA_SERIES", "1d").split(",")) if x in ("1d", "1h"))
#: allocation hypothesis credited for a bounded probe ("" = credit nothing)
RA_ALLOCATION = os.getenv("PMUS_CRYPTO_RA_ALLOCATION", "").strip().lower()
#: the hypothesis PAID earnings rows have confirmed ("" = none; 28 Nov)
RA_PAID_VERIFIED = os.getenv("PMUS_CRYPTO_RA_PAID_VERIFIED",
                             "").strip().lower()
#: bounded evidence probe: quote although the net estimate is not positive
#: (its purpose is a PAID row, not profit). Loss stop and every cap apply.
RA_PROBE = os.getenv("PMUS_CRYPTO_RA_PROBE", "0").strip() == "1"
RA_MAX_UNPAIRED_QTY = _d("PMUS_CRYPTO_RA_MAX_UNPAIRED_QTY", "4")
RA_DEPTH_MAX = _i("PMUS_CRYPTO_RA_DEPTH_MAX", "4")
RA_PULL_S = _f("PMUS_CRYPTO_RA_PULL_S", "90")
RA_OPEN_DELAY_S = _f("PMUS_CRYPTO_RA_OPEN_DELAY_S", "60")
#: lower bound on the fraction of the remaining period that will qualify
RA_QUALIFY_FRAC = _d("PMUS_CRYPTO_RA_QUALIFY_FRAC", "0.5")
#: expected trading cost, upper bounds measured from recorded books: loss
#: per filled contract and contracts filled per hour per market. Unset
#: ("") = cost unknown = nothing opens outside a probe.
RA_FILL_COST_USD = os.getenv("PMUS_CRYPTO_RA_FILL_COST_USD", "").strip()
RA_FILLS_PER_HOUR = os.getenv("PMUS_CRYPTO_RA_FILLS_PER_HOUR", "").strip()
RA_MIN_NET_USD = _d("PMUS_CRYPTO_RA_MIN_NET_USD", "0")
#: reference-index jump: a move of RA_JUMP_BPS inside RA_JUMP_WINDOW_S
RA_JUMP_BPS = _f("PMUS_CRYPTO_RA_JUMP_BPS", "10")
RA_JUMP_WINDOW_S = _f("PMUS_CRYPTO_RA_JUMP_WINDOW_S", "10")
RA_JUMP_PAUSE_S = _f("PMUS_CRYPTO_RA_JUMP_PAUSE_S", "30")
RA_INDEX_MAX_AGE_S = _f("PMUS_CRYPTO_RA_INDEX_MAX_AGE_S", "20")
#: 1 = no index, no quote. 0 = fall back to the venue-mid jump pause alone.
RA_REQUIRE_INDEX = os.getenv("PMUS_CRYPTO_RA_REQUIRE_INDEX",
                             "1").strip() != "0"
ASSET_PREFIX_1H = {"eth": "cpceth-eth-updown-1h-",
                   "sol": "cpcsol-sol-updown-1h-",
                   "xrp": "cpcxrp-xrp-updown-1h-"}


def ra_cfg() -> Optional[Dict[str, Any]]:
    """The reward-aware settings the approval must repeat, or None (off)."""
    if not RA_ON:
        return None
    return {"series": list(RA_SERIES), "allocation": RA_ALLOCATION,
            "paid_verified": RA_PAID_VERIFIED, "probe": RA_PROBE,
            "max_unpaired_qty": str(RA_MAX_UNPAIRED_QTY),
            "depth_max": RA_DEPTH_MAX, "pull_s": RA_PULL_S,
            "open_delay_s": RA_OPEN_DELAY_S,
            "qualify_frac": str(RA_QUALIFY_FRAC),
            "fill_cost_usd": RA_FILL_COST_USD,
            "fills_per_hour": RA_FILLS_PER_HOUR,
            "min_net_usd": str(RA_MIN_NET_USD),
            "jump_bps": RA_JUMP_BPS, "jump_window_s": RA_JUMP_WINDOW_S,
            "jump_pause_s": RA_JUMP_PAUSE_S,
            "require_index": RA_REQUIRE_INDEX}


def series_of(slug: str) -> Optional[str]:
    """"1d" / "1h" for a slug this lane may quote, else None. Hourly only
    in reward-aware mode with "1h" in RA_SERIES."""
    s = str(slug)
    for a in ASSETS:
        if s.startswith(ASSET_PREFIX[a]):
            return "1d"
        if RA_ON and "1h" in RA_SERIES and s.startswith(ASSET_PREFIX_1H[a]):
            return "1h"
    return None


def cfg() -> Dict[str, Any]:
    return {"assets": list(ASSETS), "qty": str(QTY), "min_qty": str(MIN_QTY),
            "depth_ticks": DEPTH_TICKS, "allow_touch": ALLOW_TOUCH,
            "require_both": REQUIRE_BOTH,
            "per_market_usd": str(PER_MARKET_USD),
            "total_usd": str(TOTAL_USD), "hard_total_usd": str(HARD_TOTAL_USD),
            "max_unpaired_usd": str(MAX_UNPAIRED_USD),
            "max_open_orders": MAX_OPEN_ORDERS, "max_markets": MAX_MARKETS,
            "loss_stop_usd": str(LOSS_STOP_USD),
            "hard_loss_budget": HARD_LOSS_BUDGET,
            "pair_cap": str(PAIR_CAP),
            "complete_max_pair": str(COMPLETE_MAX_PAIR),
            "px_min": str(PX_MIN), "px_max": str(PX_MAX),
            "maker_fee_rate": str(MAKER_FEE_RATE), "cutoff_s": CUTOFF_S,
            "market_days": MARKET_DAYS, "count_paired": COUNT_PAIRED,
            "taker": "none: this lane has no taker path",
            "reward_aware": ra_cfg()}


# ------------------------------------------------------------- identity

def is_crypto(slug: str) -> bool:
    """Any crypto up/down market, BTC included: none of them may ever be
    handled by the sports / RN1 decisions."""
    s = str(slug)
    return s.startswith("cpc") and "-updown-" in s


def asset_of(slug: str) -> Optional[str]:
    """The pilot asset a slug belongs to, or None (BTC, 5m/15m, anything
    else): only these may be quoted."""
    s = str(slug)
    for a in ASSETS:
        if s.startswith(ASSET_PREFIX[a]):
            return a
        if RA_ON and "1h" in RA_SERIES and s.startswith(ASSET_PREFIX_1H[a]):
            return a
    return None


def tradable(slug: str) -> bool:
    return asset_of(slug) is not None


def is_crypto_market(slug: str, mk: Optional[Mapping[str, Any]] = None) -> bool:
    return is_crypto(slug) or bool(mk and mk.get("lane") == LANE)


def day_key(slug: str) -> str:
    """The market day a slug belongs to (its date token), for the pilot's
    market-day count: ...-updown-1d-2026-11-21-2200z -> 2026-11-21-2200z."""
    s = str(slug)
    return s.split("-updown-1d-", 1)[1] if "-updown-1d-" in s else s


def _ts(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        t = dt.datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def seconds_left(end: Any, now: float) -> Optional[float]:
    e = _ts(end)
    return None if e is None else e - now


def late(end: Any, now: float) -> Optional[str]:
    """Why no NEW quote may open this close to the market's end, or None.
    An unknown end time is late (fail closed)."""
    left = seconds_left(end, now)
    if left is None:
        return "crypto_end_unknown"
    return "crypto_last_%ds" % int(CUTOFF_S) if left <= CUTOFF_S else None


def window_end(tg: Mapping[str, Any]) -> Any:
    """When the market's window ends: the venue's assetPriceTerms.windowEnd
    when the feed carried it. The market's `end` (endDate) is 30 minutes
    LATER than the fixing and is only the fallback of the older mode."""
    return tg.get("window_end") or tg.get("end")


def cutoff_s(slug: str) -> float:
    """Seconds before the window end after which no NEW quote may open.
    Reward-aware: the fixing minute plus a margin (RA_PULL_S, never under
    60). Otherwise the lane's long-standing CUTOFF_S."""
    return max(60.0, RA_PULL_S) if RA_ON else CUTOFF_S


def late_for(slug: str, mk: Mapping[str, Any], now: float) -> Optional[str]:
    """`late` for one market, on its own window end and cutoff. In
    reward-aware mode an unknown venue window end is late (fail closed):
    the endDate fallback would be half an hour wrong."""
    if not RA_ON:
        return late(mk.get("end"), now)
    left = seconds_left(mk.get("window_end"), now)
    if left is None:
        return "crypto_window_end_unknown"
    cut = cutoff_s(slug)
    return "crypto_last_%ds" % int(cut) if left <= cut else None


def pick_targets(targets: Iterable[Mapping[str, Any]], now: float
                 ) -> List[Mapping[str, Any]]:
    """One market per pilot asset and series: the one ending soonest that
    is not yet inside the cutoff (two daily markets of an asset overlap for
    hours)."""
    best: Dict[Tuple[str, str], Tuple[float, Mapping[str, Any]]] = {}
    for tg in targets:
        slug = str(tg.get("slug") or "")
        a, ser = asset_of(slug), series_of(slug)
        if a is None or ser is None:
            continue
        left = seconds_left(window_end(tg) if RA_ON else tg.get("end"), now)
        if left is None or left <= cutoff_s(slug):
            continue
        if (a, ser) not in best or left < best[(a, ser)][0]:
            best[(a, ser)] = (left, tg)
    return [best[(a, ser)][1] for ser in ("1d", "1h") for a in ASSETS
            if (a, ser) in best]


# ------------------------------------------------------------- venue scoring

def _num(v: Any) -> Optional[float]:
    if isinstance(v, Mapping):
        v = v.get("value") if v.get("value") is not None else v.get("px")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def book_levels(book_md: Optional[Mapping[str, Any]], tick: float
                ) -> Tuple[Dict[int, float], Dict[int, float]]:
    """({bid price in ticks: size}, {ask price in ticks: size}) from the
    public book (`offers` is the ask side)."""
    md = book_md or {}

    def side(rows: Any) -> Dict[int, float]:
        out: Dict[int, float] = {}
        for r in rows or ():
            if not isinstance(r, Mapping):
                continue
            px = _num(r.get("px") if "px" in r else r.get("price"))
            q = _num(r.get("qty") if "qty" in r else r.get("size"))
            if px is None or q is None or q <= 0 or not 0.0 < px < 1.0:
                continue
            k = int(round(px / tick))
            out[k] = out.get(k, 0.0) + q
        return out
    return side(md.get("bids")), side(md.get("offers") if "offers" in md
                                      else md.get("asks"))


def _own_ticks(orders: Iterable[Tuple[str, Any, Any]], tick: float
               ) -> Tuple[Dict[int, float], Dict[int, float]]:
    """Our orders as YES-book levels: a YES bid at p is a bid at p; a NO bid
    at n is an offer at 1 - n."""
    bid: Dict[int, float] = {}
    ask: Dict[int, float] = {}
    for side, px, qty in orders:
        q = float(qty)
        if q <= 0:
            continue
        if str(side).upper() == "YES":
            k = int(round(float(px) / tick))
            bid[k] = bid.get(k, 0.0) + q
        else:
            k = int(round((1.0 - float(px)) / tick))
            ask[k] = ask.get(k, 0.0) + q
    return bid, ask


def walk(levels: Mapping[int, float], ours: Mapping[int, float],
         target: float, df: float, is_bid: bool) -> Dict[str, Any]:
    """One side under the venue rule. `levels` INCLUDES our size; `ours` is
    the part of each level that is our order."""
    keys = sorted((k for k, q in levels.items() if q > EPS), reverse=is_bid)
    out = {"qualifies": False, "best": keys[0] if keys else None,
           "adj": None, "total": 0.0, "ours": 0.0, "share": 0.0,
           "inside": False, "ticks_from_best": None, "raw": 0.0}
    if not keys:
        return out
    best = keys[0]
    cum = total = mine = 0.0
    for k in keys:
        t = abs(k - best)
        q = levels[k]
        total += (df ** t) * q
        cum += q
        out["adj"] = k
        o = min(q, ours.get(k, 0.0))
        if o > 0:
            mine += (df ** t) * o
            out["inside"] = True
            if out["ticks_from_best"] is None:
                out["ticks_from_best"] = t
        if cum >= target - EPS:
            break
    out.update(qualifies=cum >= target - EPS, total=total, ours=mine,
               raw=cum)
    if out["qualifies"] and total > 0:
        out["share"] = mine / total
    return out


def assess(book_md: Optional[Mapping[str, Any]], program: Mapping[str, Any],
           tick: Any, quotes: Iterable[Tuple[str, Any, Any]] = (),
           resting: Iterable[Tuple[str, Any, Any]] = ()) -> Dict[str, Any]:
    """MODELLED reward position of `quotes` [(side, px, qty)] against the
    public book. `resting` is our size ALREADY in that book: it is removed
    first, so our orders are counted exactly once.

    pays       this second qualifies under Target Size and Max Spread
    share      our fraction of the market's pool for a paying second
               (0.5 x bid-side share + 0.5 x ask-side share)
    usd_day    share x pool / markets sharing the pool -- a SCENARIO: the
               split of a shared pool between its markets is not published.
    """
    t = float(tick)
    df = float(program.get("df") if program.get("df") is not None
               else program.get("discountFactor"))
    target = float(program.get("targetSize") if program.get("targetSize")
                   is not None else program.get("target"))
    ms = program.get("maxSpread")
    ms = None if ms in (None, "", 0) else float(ms)
    bids, asks = book_levels(book_md, t)
    rb, ra = _own_ticks(resting, t)
    for lv, own in ((bids, rb), (asks, ra)):
        for k, q in own.items():
            if k in lv:
                lv[k] = max(0.0, lv[k] - q)
    ex_bid = max((k for k, q in bids.items() if q > EPS), default=None)
    ex_ask = min((k for k, q in asks.items() if q > EPS), default=None)
    qb, qa = _own_ticks(quotes, t)
    for lv, own in ((bids, qb), (asks, qa)):
        for k, q in own.items():
            lv[k] = lv.get(k, 0.0) + q
    b = walk(bids, qb, target, df, True)
    a = walk(asks, qa, target, df, False)
    if ms is None:
        pays_b, pays_a = b["qualifies"], a["qualifies"]
        why = None
    else:
        both = b["qualifies"] and a["qualifies"]
        ok = both and (a["adj"] - b["adj"]) * t <= 2 * ms + EPS
        pays_b = pays_a = bool(ok)
        why = None if ok else ("target_size_not_met" if not both
                               else "max_spread")
    sb = b["share"] if pays_b else 0.0
    sa = a["share"] if pays_a else 0.0
    share = 0.5 * (sb + sa)
    pool = float(program.get("pool") or program.get("rewardPool") or 0.0)
    period_s = float(program.get("period_s") or 86400.0)
    m = max(1, int(program.get("M_program") or 1))
    rate = pool / period_s / m * share
    return {
        "pays": bool(pays_b or pays_a), "why_not": why,
        "share": share, "bid_share": sb, "ask_share": sa,
        "yes_inside": bool(b["inside"] and pays_b),
        "no_inside": bool(a["inside"] and pays_a),
        "yes_ticks_from_best": b["ticks_from_best"],
        "no_ticks_from_best": a["ticks_from_best"],
        "bid_adj_px": None if b["adj"] is None else round(b["adj"] * t, 6),
        "ask_adj_px": None if a["adj"] is None else round(a["adj"] * t, 6),
        "bid_raw": b["raw"], "ask_raw": a["raw"],
        "best_bid_ex_own": None if ex_bid is None else ex_bid * t,
        "best_ask_ex_own": None if ex_ask is None else ex_ask * t,
        "rate_usd_s": rate, "usd_day": rate * 86400.0,
        "markets_sharing_pool": m,
        "label": "MODELLED share of the displayed book; not earned, not "
                 "credited; pool split between markets is an even-split "
                 "SCENARIO"}


# ------------------------------------------------------------- quoting

def _floor(x: D, tick: D) -> D:
    return (x / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def fee(px: D, rate: D) -> D:
    return rate * px * (ONE - px)


def avg(cost: Any, qty: Any) -> D:
    q = D(str(qty or 0))
    return D(str(cost or 0)) / q if q > 0 else ZERO


def market_capital(mk: Mapping[str, Any], count_paired: Optional[bool] = None
                   ) -> Dict[str, D]:
    """Dollars one unsettled crypto market has deployed: filled cost (paired
    inventory included unless count_paired is off) and resting bids."""
    cp = COUNT_PAIRED if count_paired is None else count_paired
    y, n = D(str(mk["pos"]["YES"])), D(str(mk["pos"]["NO"]))
    cy, cn = D(str(mk["cost"]["YES"])), D(str(mk["cost"]["NO"]))
    if y > n:
        unpaired = cy * (y - n) / y
    elif n > y:
        unpaired = cn * (n - y) / n
    else:
        unpaired = ZERO
    resting = ZERO
    orders = 0
    for o in (mk.get("orders") or {}).values():
        if o.get("open"):
            orders += 1
            resting += max(ZERO, D(str(o["qty"])) -
                           D(str(o.get("filled") or "0"))) * D(str(o["px"]))
    filled = (cy + cn) if cp else unpaired
    return {"filled": filled, "unpaired": unpaired, "resting": resting,
            "capital": filled + resting, "open_orders": D(orders),
            "unpaired_qty": abs(y - n)}


def lane_markets(markets: Mapping[str, Mapping[str, Any]]
                 ) -> Dict[str, Mapping[str, Any]]:
    return {s: mk for s, mk in markets.items() if is_crypto_market(s, mk)}


def exposure(markets: Mapping[str, Mapping[str, Any]],
             exclude: Optional[str] = None) -> Dict[str, Any]:
    """Crypto lane totals over every crypto market that has not settled.
    A closed market still counts until its settlement price is recorded."""
    tot = {"capital": ZERO, "unpaired": ZERO, "resting": ZERO,
           "open_orders": ZERO, "filled": ZERO}
    live_unpaired = ZERO
    per: Dict[str, Any] = {}
    for s, mk in lane_markets(markets).items():
        if s == exclude or mk.get("settle_px") is not None:
            continue
        c = market_capital(mk)
        if c["capital"] == 0 and c["open_orders"] == 0:
            continue
        per[s] = {k: str(v) for k, v in c.items()}
        for k in tot:
            tot[k] += c[k]
        if not mk.get("done"):
            live_unpaired += c["unpaired"]
    # unpaired_open: legs in markets still trading. A closed market's leg
    # is already counted lost in full by closed_pnl / unpaired_cap.
    return {**tot, "unpaired_open": live_unpaired, "by_market": per}


def _at_risk(ex: Mapping[str, Any]) -> D:
    """Unpaired dollars that count against the unpaired / loss budget."""
    return ex["unpaired_open"] if HARD_LOSS_BUDGET else ex["unpaired"]


def quotes(*, book_md: Optional[Mapping[str, Any]],
           program: Optional[Mapping[str, Any]], tick: Any,
           pos: Mapping[str, Any], cost: Mapping[str, Any],
           resting: Iterable[Tuple[str, Any, Any]] = (),
           min_qty: Any = "0.01", capital_used: Any = 0,
           max_leg_usd: Any = None,
           c: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Resting maker bids for one crypto market:
    {"YES": {...}|None, "NO": {...}|None, "why_YES", "why_NO", "model"}.

    Flat or paired -> OPEN both sides DEPTH_TICKS behind the best price that
    is not our own, QTY each, only where the order would actually score
    under the venue rule (inside the Target Size walk, second pays).
    One side heavier -> the lighter side is the COMPLETION (never above a
    COMPLETE_MAX_PAIR pair); the heavier side gets NO new order until the
    market is paired or settled. Never a sell, never a taker."""
    k = dict(c or {})
    qty0 = D(str(k.get("qty", QTY)))
    qmin = D(str(k.get("min_qty", MIN_QTY)))
    depth = int(k.get("depth_ticks", DEPTH_TICKS))
    touch_ok = bool(k.get("allow_touch", ALLOW_TOUCH))
    both = bool(k.get("require_both", REQUIRE_BOTH))
    pcap = D(str(k.get("pair_cap", PAIR_CAP)))
    ccap = D(str(k.get("complete_max_pair", COMPLETE_MAX_PAIR)))
    pmin, pmax = D(str(k.get("px_min", PX_MIN))), D(str(k.get("px_max", PX_MAX)))
    mcap = D(str(k.get("per_market_usd", PER_MARKET_USD)))
    frate = D(str(k.get("maker_fee_rate", MAKER_FEE_RATE)))
    t = D(str(tick))
    resting = list(resting)
    out: Dict[str, Any] = {"YES": None, "NO": None, "model": None}
    if not program:
        out["why"] = "no_paying_program"
    base = assess(book_md, program or {"df": 1, "targetSize": 0},
                  tick, (), resting)
    bb, ba = base["best_bid_ex_own"], base["best_ask_ex_own"]
    if bb is None or ba is None or not 0 < bb < ba < 1:
        out["why"] = "no_two_sided_book"
        return out
    yb = _floor(D(str(round(bb, 6))) + t / 2, t)
    ya = _floor(D(str(round(ba, 6))) + t / 2, t)
    out["best"] = {"bid": str(yb), "ask": str(ya)}
    p = {s: D(str(pos.get(s) or 0)) for s in ("YES", "NO")}
    cs = {s: D(str(cost.get(s) or 0)) for s in ("YES", "NO")}
    touch = {"YES": yb, "NO": ONE - ya}
    opp = {"YES": ya, "NO": ONE - yb}        # price at which a bid would cross
    if p["YES"] != p["NO"]:
        heavy = "YES" if p["YES"] > p["NO"] else "NO"
        light = "NO" if heavy == "YES" else "YES"
        out["why_" + heavy] = "await_pair"
        a_h = cs[heavy] / p[heavy]
        px = _floor(touch[light] - depth * t, t)
        ceiling = _floor(ccap - a_h, t)
        while ceiling > ZERO and ceiling + fee(ceiling, frate) + a_h > ccap:
            ceiling -= t
        px = min(px, ceiling)
        need = p[heavy] - p[light]
        if px < t:
            out["why_" + light] = "no_breakeven_price"
        elif px >= opp[light]:
            out["why_" + light] = "would_cross"
        else:
            out[light] = {"px": px, "qty": max(need, D(str(min_qty))),
                          "role": "complete"}
        return out
    if "why" in out:
        return out
    # flat / paired: both sides, sized to the market's remaining capital
    want: Dict[str, Any] = {}
    for d in ([depth, 0] if (touch_ok and depth > 0) else [depth]):
        want = {s: _floor(touch[s] - d * t, t) for s in ("YES", "NO")}
        m = assess(book_md, program, tick,
                   [(s, want[s], qty0) for s in ("YES", "NO")], resting)
        inside = {"YES": m["yes_inside"], "NO": m["no_inside"]}
        if all(inside.values()) or d == 0 or not touch_ok:
            break
    for s in ("YES", "NO"):
        px = want[s]
        if px < pmin or px > pmax:
            out["why_" + s] = "price_band"
        elif px >= opp[s]:
            out["why_" + s] = "would_cross"
        elif not m["pays"]:
            out["why_" + s] = "second_not_paying:" + str(m["why_not"])
        elif not inside[s]:
            out["why_" + s] = "outside_scoring_walk"
        else:
            out[s] = {"px": px, "qty": qty0, "role": "open"}
    live = [s for s in ("YES", "NO") if out[s]]
    if both and len(live) == 1:
        out[live[0]] = None
        out["why_" + live[0]] = "other_side_not_eligible"
        live = []
    if len(live) == 2:
        pair = sum((out[s]["px"] + fee(out[s]["px"], frate) for s in live),
                   ZERO)
        if pair > pcap:
            for s in live:
                out[s] = None
                out["why_" + s] = "pair_cap"
            live = []
    if live:
        per_contract = sum((out[s]["px"] for s in live), ZERO)
        room = mcap - D(str(capital_used or 0))
        q = min(qty0, _floor(room / per_contract, D("1"))) \
            if per_contract > 0 else ZERO
        short = "per_market_cap"
        if max_leg_usd is not None:
            # hard loss budget: the dearest leg, filled alone and lost in
            # full, must fit what is left of the budget for this market
            dear = max(out[s]["px"] for s in live)
            ql = _floor(max(ZERO, D(str(max_leg_usd))) / dear, D("1"))
            if ql < q:
                q, short = ql, "loss_budget"
        if q < max(qmin, D(str(min_qty))):
            for s in live:
                out[s] = None
                out["why_" + s] = short
        else:
            for s in live:
                out[s]["qty"] = q
    out["model"] = m
    return out


# ------------------------------------------------------------- reward-aware

def ra_identity_block(slug: str, terms: Any) -> Optional[str]:
    """Why a market may not enter the reward-aware lane on identity alone,
    or None: its slug must be a pilot-asset hourly/daily up/down identity
    AND the venue's own assetPriceTerms must confirm asset, index, horizon
    and both window instants (src/pmus_crypto_period_identity.py)."""
    from src import pmus_crypto_period_identity as PI
    ident = PI.identify(slug)
    if ident is None:
        return "identity_unknown"
    if not ident["pilot_asset"] or ident["duration"] != series_of(slug):
        return "identity_not_pilot"
    why = PI.verify(ident, terms if isinstance(terms, Mapping) else None)
    return None if why is None else "identity_unverified:" + why


def ra_extend_ok(pos: Mapping[str, Any], side: str, qty: Any) -> bool:
    """May an OPENING bid of `qty` rest on `side`? Flat or paired: yes. On
    the heavier side: only while a full fill leaves the market's unpaired
    contracts inside RA_MAX_UNPAIRED_QTY. Never on the lighter side (that
    side is a completion)."""
    y, n = D(str(pos.get("YES") or 0)), D(str(pos.get("NO") or 0))
    mine, other = (y, n) if str(side).upper() == "YES" else (n, y)
    if mine < other:
        return False
    return mine + D(str(qty)) - other <= RA_MAX_UNPAIRED_QTY


def index_jump(series: Iterable[Tuple[Any, Any]], now: float,
               window_s: Optional[float] = None,
               bps: Optional[float] = None,
               max_age_s: Optional[float] = None) -> Optional[str]:
    """Why the venue's reference index forbids a new quote right now, or
    None. `series` is [(unix seconds, price)] as the feed last read it from
    the venue's public /v1/asset-prices/history (1-second candles).
    "index_stale" when the newest print is older than max_age_s (or there
    is none); "index_jump:<bps>" when the index moved `bps` or more between
    any print inside the last window_s and the newest one."""
    w = RA_JUMP_WINDOW_S if window_s is None else window_s
    lim = RA_JUMP_BPS if bps is None else bps
    age = RA_INDEX_MAX_AGE_S if max_age_s is None else max_age_s
    pts = []
    for row in series or ():
        try:
            t, px = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if px > 0:
            pts.append((t, px))
    if not pts:
        return "index_stale"
    pts.sort()
    t1, p1 = pts[-1]
    if now - t1 > age:
        return "index_stale"
    worst = 0.0
    for t, px in pts:
        if t1 - t <= w:
            worst = max(worst, abs(p1 / px - 1.0) * 1e4)
    return "index_jump:%.1fbps" % worst if worst >= lim else None


def ra_net(*, slug: str, program: Optional[Mapping[str, Any]], share: Any,
           seconds_left: Optional[float], paid_fraction: Any = None,
           accrued: Any = 0) -> Dict[str, Any]:
    """The reward-adjusted net estimate that decides whether openings are
    (re)placed, from src/pmus_crypto_reward_economics.py. Reports EVERY
    allocation hypothesis; credits only the one the owner named
    (RA_ALLOCATION) -- none named, or cost unknown, is not positive.

    reward   pool x allocation x remaining / period x our modelled share x
             RA_QUALIFY_FRAC x paid fraction, zero under the $1 floor
    cost     RA_FILLS_PER_HOUR x hours left x RA_FILL_COST_USD (measured
             upper bounds; settlement basis, so pair edge is inside it)
    Evidence: PUBLISHED pool, INFERRED allocation, never PAID unless
    RA_PAID_VERIFIED names the same hypothesis."""
    from src import pmus_crypto_reward_economics as RE
    out: Dict[str, Any] = {"ok": False, "why": None, "probe": RA_PROBE,
                           "allocation": RA_ALLOCATION or None,
                           "label": "MODELLED estimate; not earned, not "
                                    "credited", "by_hypothesis": {}}
    if not program or seconds_left is None:
        out["why"] = "no_program_or_window"
        return out
    ser = series_of(slug)
    window = 3600 if ser == "1h" else 86400
    remaining = max(0.0, float(seconds_left) - cutoff_s(slug))
    cost = None
    try:
        if RA_FILL_COST_USD != "" and RA_FILLS_PER_HOUR != "":
            fc, fh = D(RA_FILL_COST_USD), D(RA_FILLS_PER_HOUR)
            if fc.is_finite() and fh.is_finite() and fc >= 0 and fh >= 0:
                cost = fh * D(str(remaining)) / D(3600) * fc
    except Exception:                                    # noqa: BLE001
        cost = None
    pf = D(1) if paid_fraction is None else D(str(paid_fraction))
    common = dict(
        pool=program.get("pool") if program.get("pool") is not None
        else program.get("rewardPool"),
        window_seconds=window,
        sharing_contracts=max(1, int(program.get("M_program") or 1)),
        remaining_seconds=min(remaining, window), share_lower=share,
        qualifying_fraction_lower=RA_QUALIFY_FRAC, accrued_lower=accrued,
        incremental_cost_upper=ZERO if cost is None else cost,
        payout_floor=PAYOUT_FLOOR_USD, paid_fraction_lower=pf,
        paid_verified_hypothesis=RA_PAID_VERIFIED or None)
    for h in RE.HYPOTHESES:
        r = RE.evaluate_row(hypothesis=h, **common)
        out["by_hypothesis"][h] = {
            "reward_usd": str(r["incremental_reward"]),
            "before_floor_usd": None if r.get("projected_before_floor")
            is None else str(r["projected_before_floor"]),
            "net_usd": None if r["net_lower"] is None
            else str(r["net_lower"]),
            "evidence": r["evidence"], "reason": r.get("reason")}
    sel = RE.evaluate_row(hypothesis=RA_ALLOCATION or None, **common)
    out.update(cost_usd=None if cost is None else str(cost),
               reward_usd=str(sel["incremental_reward"]),
               net_usd=None if sel["net_lower"] is None
               else str(sel["net_lower"]),
               evidence=sel["evidence"], remaining_s=remaining,
               paid_fraction=str(pf))
    if sel.get("reason") == "unknown_pool_scope":
        out["why"] = "unknown_pool_scope"
    elif cost is None:
        out["why"] = "cost_unknown"
    elif sel["net_lower"] is None:
        out["why"] = str(sel.get("reason"))
    elif not sel["eligible"] or sel["net_lower"] <= RA_MIN_NET_USD:
        out["why"] = "net_not_positive:" + str(sel.get("reason"))
    else:
        out["ok"] = True
    return out


def ra_quotes(*, book_md: Optional[Mapping[str, Any]],
              program: Optional[Mapping[str, Any]], tick: Any,
              pos: Mapping[str, Any], cost: Mapping[str, Any],
              resting: Iterable[Tuple[str, Any, Any]] = (),
              min_qty: Any = "0.01", capital_used: Any = 0,
              max_leg_usd: Any = None, slug: str = "",
              seconds_left: Optional[float] = None,
              paid_fraction: Any = None,
              c: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Reward-aware resting maker bids for one crypto market; the same
    shape as `quotes` plus "ra" (the net estimate).

    Flat or paired -> OPEN both sides, at the shallowest depth (from
    DEPTH_TICKS out to RA_DEPTH_MAX ticks behind the best price that is not
    ours) at which BOTH bids score under the venue rule -- inside the Target
    Size walk of a second that pays, hence inside Max Spread -- and the pair
    of bids fits the pair cap.
    One side heavier -> the lighter side is a COMPLETION of at most the
    clip, priced no higher than the pair cap leaves (none when that is
    under a tick: never forced). The heavier side may keep an OPENING bid
    while a full fill leaves the unpaired contracts inside
    RA_MAX_UNPAIRED_QTY and its price pairs with the completion.
    Openings are dropped unless the reward-adjusted net estimate is
    positive (or RA_PROBE is on). Never a sell, never a taker."""
    k = dict(c or {})
    clip = D(str(k["clip"])) if k.get("clip") is not None else None
    qty0 = D(str(k.get("qty", QTY)))
    if clip is not None:
        qty0 = min(qty0, clip)
    qmin = D(str(k.get("min_qty", MIN_QTY)))
    depth = int(k.get("depth_ticks", DEPTH_TICKS))
    touch_ok = bool(k.get("allow_touch", ALLOW_TOUCH))
    pcap = D(str(k.get("pair_cap", PAIR_CAP)))
    ccap = D(str(k.get("complete_max_pair", COMPLETE_MAX_PAIR)))
    pmin, pmax = D(str(k.get("px_min", PX_MIN))), D(str(k.get("px_max", PX_MAX)))
    mcap = D(str(k.get("per_market_usd", PER_MARKET_USD)))
    frate = D(str(k.get("maker_fee_rate", MAKER_FEE_RATE)))
    t = D(str(tick))
    resting = list(resting)
    out: Dict[str, Any] = {"YES": None, "NO": None, "model": None,
                           "ra": None}
    base = assess(book_md, program or {"df": 1, "targetSize": 0},
                  tick, (), resting)
    bb, ba = base["best_bid_ex_own"], base["best_ask_ex_own"]
    if bb is None or ba is None or not 0 < bb < ba < 1:
        out["why"] = "no_two_sided_book"
        return out
    yb = _floor(D(str(round(bb, 6))) + t / 2, t)
    ya = _floor(D(str(round(ba, 6))) + t / 2, t)
    out["best"] = {"bid": str(yb), "ask": str(ya)}
    p = {s: D(str(pos.get(s) or 0)) for s in ("YES", "NO")}
    cs = {s: D(str(cost.get(s) or 0)) for s in ("YES", "NO")}
    touch = {"YES": yb, "NO": ONE - ya}
    opp = {"YES": ya, "NO": ONE - yb}        # price at which a bid would cross
    comp: Optional[Tuple[str, D, D]] = None
    open_sides = ["YES", "NO"]
    if p["YES"] != p["NO"]:
        heavy = "YES" if p["YES"] > p["NO"] else "NO"
        light = "NO" if heavy == "YES" else "YES"
        a_h = cs[heavy] / p[heavy]
        px = _floor(touch[light] - depth * t, t)
        ceiling = _floor(ccap - a_h, t)
        while ceiling > ZERO and ceiling + fee(ceiling, frate) + a_h > ccap:
            ceiling -= t
        px = min(px, ceiling)
        need = p[heavy] - p[light]
        if clip is not None:
            need = min(need, clip)
        if px < t:
            out["why_" + light] = "no_breakeven_price"
        elif px >= opp[light]:
            out["why_" + light] = "would_cross"
        else:
            out[light] = {"px": px, "qty": max(need, D(str(min_qty))),
                          "role": "complete"}
            comp = (light, px, out[light]["qty"])
        open_sides = [heavy]
        if not ra_extend_ok(p, heavy, qty0):
            out["why_" + heavy] = "ra_unpaired_cap"
            open_sides = []
    if not program:
        out["why"] = "no_paying_program"
        for s in open_sides:
            out["why_" + s] = "no_paying_program"
        return out
    if not open_sides:
        return out
    order = list(range(depth, max(depth, RA_DEPTH_MAX) + 1))
    if touch_ok:
        order += list(range(depth - 1, -1, -1))
    chosen: Optional[Dict[str, D]] = None
    m: Dict[str, Any] = {}
    first_why: Dict[str, str] = {}
    for d in order:
        want = {s: _floor(touch[s] - d * t, t) for s in open_sides}
        qs = [(s, want[s], qty0) for s in open_sides]
        if comp:
            qs.append(comp)
        m = assess(book_md, program, tick, qs, resting)
        inside = {"YES": m["yes_inside"], "NO": m["no_inside"]}
        why: Dict[str, str] = {}
        for s in open_sides:
            if want[s] < pmin or want[s] > pmax:
                why[s] = "price_band"
            elif want[s] >= opp[s]:
                why[s] = "would_cross"
            elif not m["pays"]:
                why[s] = "second_not_paying:" + str(m["why_not"])
            elif not inside[s]:
                why[s] = "outside_scoring_walk"
        if not why:
            legs = [want[s] + fee(want[s], frate) for s in open_sides]
            if comp:
                legs.append(comp[1] + fee(comp[1], frate))
            if len(legs) == 2 and sum(legs, ZERO) > pcap:
                why = {s: "pair_cap" for s in open_sides}
        if not why:
            chosen = want
            out["depth_ticks"] = d
            break
        for s, w in why.items():
            first_why.setdefault(s, w)
    out["model"] = m
    if chosen is None:
        for s in open_sides:
            out["why_" + s] = first_why.get(s) or "other_side_not_eligible"
        return out
    per_contract = sum(chosen.values(), ZERO)
    room = mcap - D(str(capital_used or 0))
    q = min(qty0, _floor(room / per_contract, D("1"))) \
        if per_contract > 0 else ZERO
    short = "per_market_cap"
    if max_leg_usd is not None:
        dear = max(chosen.values())
        ql = _floor(max(ZERO, D(str(max_leg_usd))) / dear, D("1"))
        if ql < q:
            q, short = ql, "loss_budget"
    if q < max(qmin, D(str(min_qty))):
        for s in open_sides:
            out["why_" + s] = short
        return out
    net = ra_net(slug=slug, program=program, share=m.get("share") or 0,
                 seconds_left=seconds_left, paid_fraction=paid_fraction)
    out["ra"] = net
    if not net["ok"] and not RA_PROBE:
        for s in open_sides:
            out["why_" + s] = "ra_net:" + str(net["why"])
        return out
    for s in open_sides:
        out[s] = {"px": chosen[s], "qty": q, "role": "open"}
    return out


def fit_caps(markets: Mapping[str, Mapping[str, Any]], slug: str,
             want: Dict[str, Any], why: Dict[str, Any]) -> None:
    """Drop this market's OPENING targets unless the whole lane, with them
    resting, stays inside the crypto total, the unpaired bound and the
    outstanding-order bound. Completions are never dropped: they shrink
    what is unpaired."""
    opens = [s for s in ("YES", "NO")
             if want.get(s) and want[s]["role"] == "open"]
    if not opens:
        return
    other = exposure(markets, exclude=slug)
    mk = markets.get(slug) or {"pos": {"YES": "0", "NO": "0"},
                               "cost": {"YES": "0", "NO": "0"}}
    held = market_capital({**mk, "orders": {}})
    new_rest = sum((want[s]["px"] * want[s]["qty"]
                    for s in ("YES", "NO") if want.get(s)), ZERO)
    n_orders = sum(1 for s in ("YES", "NO") if want.get(s))
    worst_leg = max((want[s]["px"] * want[s]["qty"] for s in opens),
                    default=ZERO)
    other_worst = sum((_worst_new_leg(markets[s_])
                       for s_ in other["by_market"]), ZERO)
    over = None
    if other["capital"] + held["filled"] + new_rest > TOTAL_USD:
        over = "crypto_total_cap"
    elif _at_risk(other) + other_worst + held["unpaired"] + worst_leg \
            > unpaired_cap(markets):
        over = "crypto_loss_budget" if HARD_LOSS_BUDGET \
            else "crypto_unpaired_cap"
    elif other["open_orders"] + n_orders > MAX_OPEN_ORDERS:
        over = "crypto_open_order_cap"
    if over:
        for s in opens:
            want[s] = None
            why[s] = over


def closed_pnl(markets: Mapping[str, Mapping[str, Any]]) -> D:
    """Book result of crypto markets that have closed (settled at the
    venue's price; closed but unread = unpaired leg at zero)."""
    per = book_pnl({s: mk for s, mk in lane_markets(markets).items()
                    if mk.get("done") or mk.get("settle_px") is not None})
    return per["usd"]


def unpaired_cap(markets: Mapping[str, Mapping[str, Any]]) -> D:
    """Most the lane may have unpaired or strandable at once. With the hard
    loss budget: what is left of LOSS_STOP_USD after losses already closed
    (gains do not enlarge it)."""
    if not HARD_LOSS_BUDGET:
        return MAX_UNPAIRED_USD
    return max(ZERO, min(MAX_UNPAIRED_USD,
                         LOSS_STOP_USD + min(ZERO, closed_pnl(markets))))


def leg_budget(markets: Mapping[str, Mapping[str, Any]], slug: str
               ) -> Optional[D]:
    """Dollars the dearest opening leg of `slug` may cost, or None when the
    hard loss budget is off: an even share of the lane's budget, never more
    than what the other markets leave."""
    if not HARD_LOSS_BUDGET:
        return None
    cap = unpaired_cap(markets)
    other = exposure(markets, exclude=slug)
    used = _at_risk(other) + sum((_worst_new_leg(markets[s_])
                                  for s_ in other["by_market"]), ZERO)
    return max(ZERO, min(cap / D(max(1, MAX_MARKETS)), cap - used))


def budget_ok(markets: Mapping[str, Mapping[str, Any]], slug: str,
              new_leg_usd: D) -> bool:
    """Would one more opening leg of `new_leg_usd` in `slug` keep the lane's
    worst case (held unpaired + the dearest strandable leg per market)
    inside the cap? The placement choke point's own check."""
    other = exposure(markets, exclude=slug)
    used = _at_risk(other) + sum((_worst_new_leg(markets[s_])
                                  for s_ in other["by_market"]), ZERO)
    mk = markets.get(slug)
    held = market_capital(mk)["unpaired"] if mk else ZERO
    mine = max(D(str(new_leg_usd)), _worst_new_leg(mk) if mk else ZERO)
    return used + held + mine <= unpaired_cap(markets)


def _worst_new_leg(mk: Mapping[str, Any]) -> D:
    """The dearest lone leg a market's resting OPENING bids could create."""
    return max((max(ZERO, D(str(o["qty"])) - D(str(o.get("filled") or "0")))
                * D(str(o["px"])) for o in (mk.get("orders") or {}).values()
                if o.get("open") and o.get("stage") == STAGE_OPEN),
               default=ZERO)


# ------------------------------------------------------------- arming

APPROVAL_FIELDS = ("qty", "per_market_usd", "total_usd", "loss_stop_usd",
                   "market_days", "depth_ticks", "max_markets")


def approval_template(sha: str = "<release sha>") -> Dict[str, Any]:
    """What the owner signs. Values are the RUNNING configuration."""
    return {"approved_by": "", "release_sha": sha, "not_before": "",
            "not_after": "", "assets": list(ASSETS), "qty": str(QTY),
            "per_market_usd": str(PER_MARKET_USD),
            "total_usd": str(TOTAL_USD),
            "loss_stop_usd": str(LOSS_STOP_USD),
            "market_days": MARKET_DAYS, "depth_ticks": DEPTH_TICKS,
            "max_markets": MAX_MARKETS,
            "hard_loss_budget": HARD_LOSS_BUDGET,
            "combined_max_exposure_usd": "", "taker": False,
            "reward_aware": ra_cfg()}


def combined_cap(env: Optional[Mapping[str, str]] = None) -> Optional[D]:
    """The explicit sports + crypto exposure ceiling, or None when unset or
    unreadable (crypto then refuses; sports is as it was)."""
    env = os.environ if env is None else env
    raw = str(env.get(COMBINED_CAP_ENV, "") or "").strip()
    if not raw:
        return None
    try:
        v = D(raw)
    except Exception:                                    # noqa: BLE001
        return None
    return v if v.is_finite() and v > 0 else None


def arm_state(now: float, env: Optional[Mapping[str, str]] = None
              ) -> Dict[str, Any]:
    """{"armed": bool, "why": reason when not}. Fails closed on anything
    missing, unparseable or different from the running configuration."""
    env = os.environ if env is None else env

    def no(why: str) -> Dict[str, Any]:
        return {"armed": False, "why": why}
    if not ON:
        return no("crypto_off")
    if str(env.get(ARM_ENV, "") or "").strip() != "YES":
        return no("not_armed")
    raw = str(env.get(APPROVAL_ENV, "") or "").strip()
    if not raw:
        return no("no_approval")
    try:
        a = json.loads(raw)
    except ValueError:
        return no("approval_unreadable")
    if not isinstance(a, Mapping):
        return no("approval_unreadable")
    if not str(a.get("approved_by") or "").strip():
        return no("approval_unsigned")
    sha = str(env.get(SHA_ENV, "") or "").strip().lower()
    want_sha = str(a.get("release_sha") or "").strip().lower()
    if len(want_sha) < 9 or not sha or not sha.startswith(want_sha):
        return no("approval_release_mismatch")
    if a.get("taker") is not False:
        return no("approval_taker_must_be_false")
    lo, hi = _ts(a.get("not_before")), _ts(a.get("not_after"))
    if lo is None or hi is None or hi <= lo:
        return no("approval_window_unreadable")
    if hi - lo > (HARD_MARKET_DAYS + 1) * 86400:
        return no("approval_window_too_long")
    if now < lo:
        return no("approval_not_started")
    if now >= hi:
        return no("approval_expired")
    if sorted(str(x).lower() for x in (a.get("assets") or [])) != \
            sorted(ASSETS):
        return no("approval_mismatch:assets")
    running = approval_template()
    for f in APPROVAL_FIELDS:
        try:
            if D(str(a.get(f))) != D(str(running[f])):
                return no("approval_mismatch:" + f)
        except Exception:                                # noqa: BLE001
            return no("approval_mismatch:" + f)
    if a.get("hard_loss_budget") is not HARD_LOSS_BUDGET:
        return no("approval_mismatch:hard_loss_budget")
    if a.get("reward_aware") != ra_cfg():
        # reward-aware mode on: the approval must repeat every setting of
        # it. Off: an approval that names the mode does not arm the old one.
        return no("approval_mismatch:reward_aware")
    cap = combined_cap(env)
    try:
        if cap is None or D(str(a.get("combined_max_exposure_usd"))) != cap:
            return no("approval_mismatch:combined_max_exposure_usd")
    except Exception:                                    # noqa: BLE001
        return no("approval_mismatch:combined_max_exposure_usd")
    return {"armed": True, "why": None, "approved_by": a.get("approved_by"),
            "release_sha": want_sha, "not_after": a.get("not_after")}


# ------------------------------------------------------------- loss

def book_pnl(markets: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """The lane's result from the bot's own book, every loop (the venue-
    reconciled meter runs hourly). Per crypto market that this lane traded:
      settled   YES x px + NO x (1 - px) - cost
      open      matched pairs at $1, the unpaired leg at its side's bid
                (the engine's mark) - cost
      closed, settlement price not yet read: the unpaired leg at ZERO.
    ESTIMATE; it never rounds a loss down."""
    total = ZERO
    per: Dict[str, str] = {}
    for s, mk in lane_markets(markets).items():
        y, n = D(str(mk["pos"]["YES"])), D(str(mk["pos"]["NO"]))
        if y == 0 and n == 0:
            continue
        paid = D(str(mk["cost"]["YES"])) + D(str(mk["cost"]["NO"]))
        px = mk.get("settle_px")
        if px is not None:
            v = y * D(str(px)) + n * (ONE - D(str(px))) - paid
        elif mk.get("done") or mk.get("mark") is None:
            v = min(y, n) - paid
        else:
            v = D(str(mk["mark"]))
        per[s] = str(v)
        total += v
    return {"usd": total, "by_market": per}


# ------------------------------------------------------------- markouts

def record_fill(mk: Dict[str, Any], side: str, px: Any, qty: Any, mid: Any,
                now: float) -> None:
    """Remember a fill and the mid at that moment, for adverse selection."""
    mk.setdefault("crypto_fills", []).append({
        "t": now, "side": side, "px": str(px), "qty": str(qty),
        "mid0": None if mid is None else str(mid), "mo": {}})


def update_markouts(mk: Dict[str, Any], mid: Any, now: float) -> bool:
    """Fill each elapsed horizon's markout once: the fair value of the side
    we bought (YES = mid, NO = 1 - mid) minus what we paid, per contract.
    Negative = the price moved against us after we were filled."""
    if mid is None:
        return False
    changed = False
    for f in mk.get("crypto_fills") or ():
        for h in MARKOUT_S:
            if str(h) in f["mo"] or now - float(f["t"]) < h:
                continue
            fair = D(str(mid)) if f["side"] == "YES" else ONE - D(str(mid))
            f["mo"][str(h)] = str(fair - D(f["px"]))
            changed = True
    return changed


def markout_summary(markets: Mapping[str, Mapping[str, Any]]
                    ) -> Dict[str, Any]:
    out: Dict[str, Any] = {"fills": 0, "contracts": "0"}
    usd = {str(h): ZERO for h in MARKOUT_S}
    cnt = {str(h): ZERO for h in MARKOUT_S}
    qty = ZERO
    for mk in lane_markets(markets).values():
        for f in mk.get("crypto_fills") or ():
            out["fills"] += 1
            qty += D(f["qty"])
            for h, v in f["mo"].items():
                usd[h] += D(v) * D(f["qty"])
                cnt[h] += D(f["qty"])
    out["contracts"] = str(qty)
    for h in usd:
        out["markout_%ss_usd" % h] = str(usd[h])
        out["markout_%ss_per_contract" % h] = \
            str(usd[h] / cnt[h]) if cnt[h] else None
        out["markout_%ss_contracts" % h] = str(cnt[h])
    out["label"] = ("adverse selection: fair value after the fill minus the "
                    "price paid; negative = picked off. Mid-based ESTIMATE")
    return out


# ------------------------------------------------------------- rewards

def _amount(row: Mapping[str, Any]) -> D:
    for key in ("reward", "amount", "amountUsd", "usd", "value", "earned",
                "total"):
        v = row.get(key)
        if isinstance(v, Mapping):
            v = v.get("value")
        if v is None or v == "":
            continue
        try:
            return D(str(v))
        except Exception:                                # noqa: BLE001
            continue
    return ZERO


def venue_rewards(rows: Iterable[Mapping[str, Any]],
                  credited_statuses: Iterable[str]) -> Dict[str, Any]:
    """Venue reward rows that NAME a crypto up/down market, split into money
    the venue has paid (`credited`) and everything else (`pending`). A row
    with an unrecognised or missing status is pending, never income."""
    paid = {str(s).upper() for s in credited_statuses}
    per: Dict[str, Dict[str, Any]] = {}
    cred = pend = skip = ZERO
    n = 0
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        slug = str(r.get("marketSlug") or "")
        if not is_crypto(slug):
            continue
        n += 1
        amt = _amount(r)
        st = str(r.get("status") or "STATUS_ABSENT").upper()
        m = per.setdefault(slug, {"credited": ZERO, "pending": ZERO,
                                  "skipped": ZERO, "statuses": {}})
        m["statuses"][st] = m["statuses"].get(st, 0) + 1
        if st == "SKIPPED":
            # resolved and NOT paid: still inside `pending` (never income),
            # reported on its own so paid / awaiting / skipped are separate
            m["skipped"] += amt
            skip += amt
        if st in paid:
            m["credited"] += amt
            cred += amt
        else:
            m["pending"] += amt
            pend += amt
    return {"rows": n, "credited_usd": str(cred), "pending_usd": str(pend),
            "paid_usd": str(cred), "skipped_usd": str(skip),
            "awaiting_usd": str(pend - skip),
            "by_market": {s: {"credited_usd": str(m["credited"]),
                              "pending_usd": str(m["pending"]),
                              "skipped_usd": str(m["skipped"]),
                              "statuses": m["statuses"]}
                          for s, m in sorted(per.items())},
            "label": "venue reward rows naming a crypto market; ONLY "
                     "credited_usd is money"}


def attribution(markets: Mapping[str, Mapping[str, Any]],
                rewards: Optional[Mapping[str, Any]] = None
                ) -> Dict[str, Any]:
    """The crypto lane's own economics, by series ("1d" / "1h"), kept apart
    from sports: fills, pair edge, residual, maker fees, and venue reward
    rows split paid / awaiting / skipped. Book figures are the bot's own
    ESTIMATE (the venue-reconciled cash meter is CRYPTO_PNL); only
    rewards.paid_usd is reward money."""
    def blank() -> Dict[str, Any]:
        return {"markets": 0, "fills": 0, "contracts": ZERO,
                "cost_usd": ZERO, "pairs": ZERO, "pair_edge_usd": ZERO,
                "residual_contracts": ZERO, "residual_cost_usd": ZERO,
                "residual_settled_usd": ZERO, "residual_open_contracts": ZERO,
                "maker_fees_modelled_usd": ZERO,
                "modelled_reward_usd": ZERO,
                "rewards": {"paid_usd": ZERO, "awaiting_usd": ZERO,
                            "skipped_usd": ZERO, "rows": 0}}
    per: Dict[str, Dict[str, Any]] = {}

    def ser_of(slug: str) -> str:
        for tag in ("1d", "1h"):
            if "-updown-%s-" % tag in slug:
                return tag
        return "other"
    for slug, mk in lane_markets(markets).items():
        y, n = D(str(mk["pos"]["YES"])), D(str(mk["pos"]["NO"]))
        cy, cn = D(str(mk["cost"]["YES"])), D(str(mk["cost"]["NO"]))
        cm = D(str((mk.get("crypto_model") or {}).get("usd") or 0))
        fills = mk.get("crypto_fills") or ()
        if y == 0 and n == 0 and not fills and cm == 0:
            continue
        a = per.setdefault(ser_of(str(slug)), blank())
        a["markets"] += 1
        a["fills"] += len(fills)
        a["contracts"] += y + n
        a["cost_usd"] += cy + cn
        a["modelled_reward_usd"] += cm
        for f in fills:
            a["maker_fees_modelled_usd"] += fee(D(f["px"]), MAKER_FEE_RATE) \
                * D(f["qty"])
        pairs = min(y, n)
        heavy = "YES" if y > n else "NO"
        hq, hc = (y, cy) if heavy == "YES" else (n, cn)
        lc = cn if heavy == "YES" else cy
        res = hq - pairs
        res_cost = hc * res / hq if hq > 0 else ZERO
        a["pairs"] += pairs
        a["pair_edge_usd"] += pairs - (hc - res_cost) - lc
        a["residual_contracts"] += res
        a["residual_cost_usd"] += res_cost
        px = mk.get("settle_px")
        if px is None:
            a["residual_open_contracts"] += res
        elif res > 0:
            pay = D(str(px)) if heavy == "YES" else ONE - D(str(px))
            a["residual_settled_usd"] += res * pay - res_cost
    for slug, r in ((rewards or {}).get("by_market") or {}).items():
        a = per.setdefault(ser_of(str(slug)), blank())["rewards"]
        paid = D(str(r.get("credited_usd") or 0))
        skipped = D(str(r.get("skipped_usd") or 0))
        a["paid_usd"] += paid
        a["skipped_usd"] += skipped
        a["awaiting_usd"] += D(str(r.get("pending_usd") or 0)) - skipped
        a["rows"] += sum((r.get("statuses") or {}).values())

    def flat(v: Any) -> Any:
        if isinstance(v, dict):
            return {k_: flat(x) for k_, x in v.items()}
        return str(v) if isinstance(v, D) else v
    return {"lane": LANE, "by_series": flat(per),
            "reward_rows_read": rewards is not None,
            "label": "crypto lane only, never mixed with sports; book "
                     "ESTIMATES except rewards (venue rows); only "
                     "rewards.paid_usd is reward money"}


def net_report(meter: Optional[Mapping[str, Any]],
               rewards: Optional[Mapping[str, Any]],
               modelled_usd: Any, book: Optional[Mapping[str, Any]] = None,
               markouts: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The pilot's economics with each figure under its own label. The test
    passes only on venue figures: a credit row naming a crypto market AND
    settled trading net + credited rewards > 0 (proposal 2026-11-22)."""
    ex = (meter or {}).get("execution") or {}
    m = meter or {}
    trading = None if meter is None else D(str(m["realized_net_usd"]))
    credited = None if rewards is None else D(str(rewards["credited_usd"]))
    realized = None if trading is None or credited is None \
        else trading + credited
    outstanding = None if meter is None else \
        D(str(m.get("outstanding_contracts") or 0))
    traded = D(str(ex.get("contracts") or 0)) > 0
    verdict = "INCOMPLETE"
    if realized is not None and not m.get("unconfirmed") and traded \
            and m.get("positions_checked") and outstanding == 0:
        verdict = "PASS" if (credited > 0 and realized > 0) else \
            "FAIL_OR_AWAITING_CREDIT"
    settled = trading
    return {
        "modelled_reward_usd": str(modelled_usd),
        "venue_pending_reward_usd": None if rewards is None
        else rewards["pending_usd"],
        "venue_credited_reward_usd": None if credited is None
        else str(credited),
        "realized_trading_net_usd": None if settled is None
        else str(settled),
        "locked_pair_edge_usd": ex.get("locked_pair_edge_usd"),
        "settled_residual_usd": m.get("settled_residual_usd"),
        "maker_fees_charged_usd": m.get("fees_charged_usd"),
        "outstanding_contracts": m.get("outstanding_contracts"),
        "outstanding_cost_usd": m.get("outstanding_cost_usd"),
        "open_residual_worst_usd": m.get("open_residual_worst_usd"),
        "positions_checked": m.get("positions_checked"),
        "completed_pairs": ex.get("completed_pairs"),
        "avg_pair_cost": ex.get("avg_pair_cost"),
        "residual_contracts": ex.get("residual_contracts"),
        "residual_cost_usd": ex.get("residual_cost_usd"),
        "fills_unconfirmed": (meter or {}).get("unconfirmed"),
        "book_estimate_usd": None if book is None else str(book["usd"]),
        "adverse_selection": markouts,
        "realized_net_usd": None if realized is None else str(realized),
        "verdict": verdict,
        "labels": {
            "modelled_reward_usd": "MODELLED, never money",
            "venue_pending_reward_usd": "venue-listed, NOT paid",
            "venue_credited_reward_usd": "venue-paid",
            "realized_trading_net_usd": "cash basis: completed pairs "
                                        "(venue nets and pays $1 at "
                                        "pairing) - fees charged + settled "
                                        "residual legs; venue-reconciled "
                                        "fills and positions only",
            "realized_net_usd": "realized_trading_net + venue_credited "
                                "reward; nothing modelled or pending",
            "book_estimate_usd": "bot's own mark; drives the loss stop"}}
