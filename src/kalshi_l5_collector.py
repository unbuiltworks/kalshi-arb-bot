"""Continuous keyless collector for Kalshi 15-minute crypto windows, and
the loop that runs the existing L5 shadow over its tape.
(OWNER #167 comment 5584095017 workstream B.)

The readiness survey found that `src/kalshi_l5_shadow.py` is a one-shot
tape ANALYSER -- no loop, no live call -- and that the tape it reads does
not exist, because `src/policy_d_port/collector.py` is a Gemini WebSocket
collector, not a Kalshi one. So the Kalshi lane could not run continuously
for want of a producer. This module is that producer, plus the loop that
consumes it; the shadow itself is reused unchanged.

WHY THIS LANE KEEPS LITERAL TIME. A Kalshi 15M window serves its own
`open_time` and `close_time` and settles on a 60-second CF Benchmarks RTI
average. Seconds-to-settlement is a fact here, not a translation, so the
sports event-progress mapping is deliberately NOT applied: this lane keys
the frozen model on real TTE, exactly as L5's native 5m/15m crypto
windows do.

WHAT IT WILL NOT DO. Read-only and keyless: GET requests to the public
API, no credentials, no order endpoint reachable from this module, no
write of any kind to the venue. `shadow_only` and `order_entry: False`
travel on every emission, as they do in the analyser.

Operational lessons this file already carries, from incidents earlier the
same day:
  * the tape is BOUNDED and swept oldest-first -- an unbounded tape filled
    the recorder's 50 GB volume at 00:40Z and crash-looped it;
  * writes are fail-soft with an alarm rather than an exception that ends
    the process;
  * the heartbeat reports CURRENT resident memory from /proc alongside the
    peak, because reporting `ru_maxrss` as if it were current overstated
    live memory fourfold in the probe's first heartbeat.

    python3 -m src.kalshi_l5_collector --once     # one cycle, then exit
    python3 -m src.kalshi_l5_collector            # 24/7
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from src.kalshi_l5_shadow import CERTIFIED_SERIES, shadow_status

# OWNER GO 2026-09-08: observe the series that Kalshi is ACTUALLY paying for.
# CERTIFIED_SERIES is deliberately NOT extended. It gates the economic
# analyser because the quadratic maker schedule is established only for those
# two; adding a series there would silently imply a fee model we have not
# verified, and the analyser would then emit net economics built on it. These
# series are COLLECTED and described with FEE-INDEPENDENT structure only
# (spread, pair cost, depth, occupancy). Net-of-fee economics for them stay
# unavailable until the schedule is established, exactly as for any
# uncertified series.
OBSERVATION_SERIES = ("KXCRYPTOLEAD15M", "KXGOLD15M", "KXWTI15M",
                      "KXSILVER15M", "KXNATGAS15M", "KXCOPPER15M")
COLLECT_SERIES = tuple(CERTIFIED_SERIES) + OBSERVATION_SERIES

API = "https://api.elections.kalshi.com/trade-api/v2"
UA = "l5-transfer-research/1.0 (read-only; public endpoints only)"

STATE_DIR_ENV = "KALSHI_L5_STATE_DIR"
DEFAULT_STATE_DIR = "/data/kalshi_l5"
INTERVAL_ENV = "KALSHI_L5_INTERVAL_S"
DEFAULT_INTERVAL_S = 20.0
SHADOW_EVERY_ENV = "KALSHI_L5_SHADOW_EVERY"
DEFAULT_SHADOW_EVERY = 15                  # cycles between shadow runs
TAPE_MAX_BYTES_ENV = "KALSHI_L5_TAPE_MAX_BYTES"
DEFAULT_TAPE_MAX_BYTES = 512 * 1024 ** 2   # bounded from the first commit
HEARTBEAT_S = 300.0
# A collector that is running but seeing nothing is indistinguishable from
# a healthy quiet market unless it says so. These bound "nothing".
STALE_MARKETS_S = 1800.0      # no open certified market discovered at all
STALE_PRINTS_S = 2700.0       # no new public print on any market

_HTTP_TIMEOUT_S = 20.0


def _get(path: str, params: Optional[Mapping[str, Any]] = None) -> dict:
    """One public GET. No credentials are attached, here or anywhere in
    this module."""
    url = API + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode({k: v for k, v in params.items()
                                if v is not None})
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _iso_epoch(s: Any) -> Optional[float]:
    if not s:
        return None
    from datetime import datetime, timezone
    txt = str(s).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# --------------------------------------------------------------------------
# discovery and event shaping -- exactly the shape kalshi_l5_shadow reads
# --------------------------------------------------------------------------

def open_markets(series: Sequence[str] = COLLECT_SERIES) -> List[dict]:
    """Open markets for the certified series (control) and the
    incentivized observation series. Collecting an uncertified series
    implies NOTHING about its fee schedule: the analyser still refuses it,
    and only fee-independent structure is reported for it."""
    out: List[dict] = []
    for ser in series:
        try:
            resp = _get("/markets", {"series_ticker": ser, "status": "open",
                                     "limit": 20})
        except (urllib.error.URLError, OSError, ValueError):
            continue
        for m in (resp.get("markets") or []):
            if m.get("ticker"):
                out.append(m)
    return out


def window_event(m: Mapping[str, Any]) -> dict:
    return {"venue": "kalshi", "market": m.get("ticker"), "kind": "window",
            "series": str(m.get("event_ticker") or "").split("-")[0],
            "open_time": m.get("open_time"), "close_time": m.get("close_time"),
            "expected_expiration_time": m.get("expected_expiration_time"),
            "title": m.get("title"), "collected_t": time.time()}


def bbo_event(m: Mapping[str, Any], t: Optional[float] = None) -> Optional[dict]:
    """Top of book in the analyser's own shape. The NO bid is the venue's
    own `no_bid_dollars`; its size is the YES ask size, because a resting
    NO bid IS the YES ask. A missing side is left out rather than
    invented."""
    yes_bid, no_bid = _f(m.get("yes_bid_dollars")), _f(m.get("no_bid_dollars"))
    if yes_bid is None and no_bid is None:
        return None
    ev: Dict[str, Any] = {"venue": "kalshi", "market": m.get("ticker"),
                          "kind": "bbo", "t": t if t is not None else time.time()}
    if yes_bid is not None:
        ev["yes_bids"] = [[yes_bid, _f(m.get("yes_bid_size_fp")) or 0.0]]
    if no_bid is not None:
        ev["no_bids"] = [[no_bid, _f(m.get("yes_ask_size_fp")) or 0.0]]
    return ev


def print_events(ticker: str, since_t: Optional[float] = None,
                 limit: int = 100) -> List[dict]:
    """Public trades for one market, newest first from the venue, emitted
    oldest first. `since_t` keeps the tape from repeating prints it already
    holds; a trade without a usable time or price is dropped, never
    guessed."""
    try:
        resp = _get("/markets/trades", {"ticker": ticker, "limit": limit})
    except (urllib.error.URLError, OSError, ValueError):
        return []
    out: List[dict] = []
    for tr in (resp.get("trades") or []):
        t = _iso_epoch(tr.get("created_time"))
        px = _f(tr.get("yes_price_dollars"))
        if t is None or px is None:
            continue
        if since_t is not None and t <= since_t:
            continue
        out.append({"venue": "kalshi", "market": ticker, "kind": "print",
                    "t": t, "yes_price": px,
                    "qty": _f(tr.get("count_fp")),
                    "taker_side": tr.get("taker_side"),
                    "trade_id": tr.get("trade_id")})
    out.sort(key=lambda e: e["t"])
    return out


# --------------------------------------------------------------------------
# the tape: bounded from the first line, swept oldest-first
# --------------------------------------------------------------------------

def state_dir() -> Path:
    return Path(os.getenv(STATE_DIR_ENV, DEFAULT_STATE_DIR))


def tape_dir() -> Path:
    return state_dir() / "tape"


def tape_path(t: Optional[float] = None) -> Path:
    """Hourly parts. The recorder's 00:40Z ENOSPC came from one unbounded
    daily file; parts are what make a sweep possible at all."""
    stamp = time.strftime("%Y%m%d_%H", time.gmtime(t if t is not None
                                                   else time.time()))
    return tape_dir() / f"kalshi_l5_{stamp}.jsonl"


def tape_parts() -> List[Path]:
    d = tape_dir()
    return sorted(d.glob("kalshi_l5_*.jsonl")) if d.is_dir() else []


def tape_budget() -> int:
    try:
        return max(1, int(os.getenv(TAPE_MAX_BYTES_ENV,
                                    str(DEFAULT_TAPE_MAX_BYTES))))
    except ValueError:
        return DEFAULT_TAPE_MAX_BYTES


def sweep_tape(budget: Optional[int] = None) -> dict:
    """Delete oldest parts until the tape fits its budget. The CURRENT
    part is never deleted."""
    budget = tape_budget() if budget is None else budget
    parts = tape_parts()
    current = tape_path()
    sizes = {p: (p.stat().st_size if p.exists() else 0) for p in parts}
    total = sum(sizes.values())
    deleted: List[str] = []
    for p in parts:
        if total <= budget:
            break
        if p == current:
            continue
        try:
            p.unlink()
        except OSError:
            continue
        total -= sizes.get(p, 0)
        deleted.append(p.name)
    return {"kind": "tape_retention", "parts": len(parts),
            "bytes": total, "budget": budget, "deleted": deleted}


def append(events: Iterable[Mapping[str, Any]]) -> dict:
    """Fail-soft append: a full or unwritable volume must alarm, not end
    the process."""
    rows = [json.dumps(e, separators=(",", ":"), default=str) for e in events]
    if not rows:
        return {"written": 0}
    path = tape_path()
    for attempt in (1, 2):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(rows) + "\n")
            return {"written": len(rows)}
        except OSError as exc:
            if attempt == 1:
                sweep_tape()
                continue
            print("KALSHI_L5_ALARM " + json.dumps(
                {"alarm": "tape_write_failed",
                 "err": f"{type(exc).__name__}"}), flush=True)
            return {"written": 0, "error": type(exc).__name__}
    return {"written": 0}


# --------------------------------------------------------------------------
# liveness
# --------------------------------------------------------------------------

def _rss_now_mb() -> Optional[float]:
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as fh:
            pages = int(fh.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / (1024.0 ** 2), 1)
    except Exception:                                   # noqa: BLE001
        return None


def _rss_peak_mb() -> Optional[float]:
    try:
        import resource
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                     / 1024.0, 1)
    except Exception:                                   # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# one cycle, and the loop
# --------------------------------------------------------------------------

def seed_seen_from_tape(parts: Optional[Sequence[Path]] = None
                        ) -> Dict[str, float]:
    """Newest print time per market already on the tape.

    Without this a restart re-appends prints the tape already holds, and
    the analyser would count the same fill evidence twice. Collection has
    to be idempotent across restarts, not only within one process."""
    seen: Dict[str, float] = {}
    for path in (tape_parts() if parts is None else parts):
        try:
            with open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if '"print"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("kind") != "print" or e.get("venue") != "kalshi":
                        continue
                    m, t = e.get("market"), _f(e.get("t"))
                    if m and t is not None and t > seen.get(str(m), 0.0):
                        seen[str(m)] = t
        except OSError:
            continue
    return seen


def collect_once(seen: Optional[Dict[str, float]] = None,
                 known_windows: Optional[set] = None) -> dict:
    """Discover, snapshot and record one pass. Returns an audit row; never
    raises for a venue hiccup."""
    seen = {} if seen is None else seen
    known_windows = set() if known_windows is None else known_windows
    markets = open_markets()
    now = time.time()
    events: List[dict] = []
    for m in markets:
        ticker = str(m.get("ticker"))
        if ticker not in known_windows:
            events.append(window_event(m))
            known_windows.add(ticker)
        bbo = bbo_event(m, now)
        if bbo:
            events.append(bbo)
        prints = print_events(ticker, since_t=seen.get(ticker))
        if prints:
            seen[ticker] = prints[-1]["t"]
            events.extend(prints)
    wrote = append(events)
    return {"kind": "kalshi_collect", "markets": len(markets),
            "events": len(events), **wrote,
            "series": sorted({str(m.get("event_ticker") or "").split("-")[0]
                              for m in markets})}


# --------------------------------------------------------- reward units
# period_reward arrives as a bare integer. Its denomination is CENTI-CENTS
# (1/10,000 of a dollar), from Kalshi's current Liquidity Incentive Program
# documentation, supplied by the owner on 2026-09-08 with the worked
# examples below. We keep the raw integer next to the conversion for as
# long as this field exists, so a wrong unit is a one-line correction and
# not an archaeology exercise across a month of tape.
REWARD_UNIT = {
    "units": "centi-cents",
    "divisor": 10_000,
    "source": ("Kalshi Liquidity Incentive Program documentation, relayed "
               "by the owner 2026-09-08; worked examples 1,000,000=$100, "
               "2,850,000=$285, 10,000,000=$1,000 all satisfy /10,000, and "
               "the documented $1-$1,000 per market/day advertised range "
               "brackets every value we have observed"),
    "status": ("HEADLINE POOL ONLY. This is what the venue distributes "
               "among ALL qualifying liquidity in the market, before any "
               "share-of-score. It is not estimated obtainable reward and "
               "it is not credited reward. Those are separate statuses and "
               "this collector produces neither."),
    "examples": {"1000000": 100.0, "2850000": 285.0, "10000000": 1000.0},
}

def _incentive_bucket() -> Dict[str, Any]:
    """A FRESH bucket per series. This is a factory and not a module-level
    template on purpose: a shallow copy of a template would share the four
    nested histograms across every series, which silently reports one
    venue-wide reward distribution as if it were each series' own."""
    return {"active": 0, "upcoming": 0, "past": 0,
            "period_reward_raw": {}, "headline_reward_dollars": {},
            "target_size_raw": {}, "types": {}}


def reward_dollars(period_reward: Any) -> Optional[float]:
    """Headline pool in dollars for one reward period. None when the field
    is absent or unparseable -- never 0.0, because a missing program and a
    zero-funded one are different facts."""
    if period_reward is None:
        return None
    try:
        return int(period_reward) / REWARD_UNIT["divisor"]
    except (TypeError, ValueError):
        return None


def incentive_state(series: Sequence[str] = OBSERVATION_SERIES,
                   now: Optional[float] = None, pages: int = 25) -> dict:
    """Current incentive-program state per series, from the public
    endpoint. Read-only.

    period_reward is denominated in CENTI-CENTS, per Kalshi's current
    Liquidity Incentive Program documentation (see REWARD_UNIT below).
    The raw integer is retained verbatim under period_reward_raw; the
    dollar reading is reported ALONGSIDE it, never instead of it, so the
    conversion stays auditable and reversible if the source is ever
    contradicted.

    A dollar figure here is the HEADLINE POOL for a market-day: what the
    venue advertises it will distribute among all qualifying liquidity.
    It is NOT our revenue and must never be read as one."""
    now = time.time() if now is None else now
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    want = {s.upper() for s in series} | {s.upper() for s in CERTIFIED_SERIES}
    out: Dict[str, Dict[str, Any]] = {}
    cursor = None
    for _ in range(max(1, pages)):
        try:
            resp = _get("/incentive_programs",
                        {"limit": 200, "cursor": cursor})
        except (urllib.error.URLError, OSError, ValueError):
            break
        rows = resp.get("incentive_programs") or []
        for r in rows:
            ser = str(r.get("market_ticker") or "").split("-", 1)[0].upper()
            if ser not in want:
                continue
            b = out.setdefault(ser, _incentive_bucket())
            st, en = str(r.get("start_date") or ""), str(r.get("end_date") or "")
            if st <= iso <= en:
                b["active"] += 1
            elif st > iso:
                b["upcoming"] += 1
            else:
                b["past"] += 1
            pr = str(r.get("period_reward"))
            b["period_reward_raw"][pr] = b["period_reward_raw"].get(pr, 0) + 1
            usd = reward_dollars(r.get("period_reward"))
            if usd is not None:
                k = f"{usd:.4f}"
                b["headline_reward_dollars"][k] = \
                    b["headline_reward_dollars"].get(k, 0) + 1
            ts = str(r.get("target_size_fp") if r.get("target_size_fp")
                     is not None else r.get("target_size"))
            b["target_size_raw"][ts] = b["target_size_raw"].get(ts, 0) + 1
            ty = str(r.get("incentive_type"))
            b["types"][ty] = b["types"].get(ty, 0) + 1
        cursor = resp.get("next_cursor")
        if not cursor or not rows:
            break
    for ser in want:
        out.setdefault(ser, _incentive_bucket())
    return {"kind": "KALSHI_INCENTIVE_STATE", "observed_utc": iso,
            "series": out,
            "period_reward_units": REWARD_UNIT["units"],
            "period_reward_divisor": REWARD_UNIT["divisor"],
            "period_reward_source": REWARD_UNIT["source"],
            "dollar_status": REWARD_UNIT["status"],
            "withdrawn_note": ("KXBTC15M/KXETH15M carried volume programs that "
                               "ENDED 2026-05-12; a zero here is a withdrawn "
                               "subsidy, not an absent one")}


# ------------------------------------------------------------ leg identity
# KXCRYPTOLEAD15M is ONE series carrying five assets whose books differ by an
# order of magnitude in cost. A series median hides that BTC and HYPE are not
# the same business, so the reporting unit is the LEG.
ASSET_LEG_SERIES = ("KXCRYPTOLEAD15M",)


def leg_key(ticker: str) -> str:
    """The decision unit. Asset for a multi-asset series, series otherwise --
    a commodity ticker's trailing segment is the 15-minute window, which is
    not an identity and would shatter one leg into hundreds."""
    ser = str(ticker).split("-", 1)[0].upper()
    if ser in ASSET_LEG_SERIES:
        return "{}:{}".format(ser, str(ticker).rsplit("-", 1)[-1].upper())
    return ser


def _quotes(events: Iterable[Mapping[str, Any]]):
    """(yes_bid, no_bid, yes_size, no_size) from each bbo event that has
    BOTH sides. One-sided books cannot price a pair and are counted
    separately rather than imputed."""
    for e in events:
        if e.get("kind") != "bbo":
            continue
        yb = (e.get("yes_bids") or [[None, None]])[0]
        nb = (e.get("no_bids") or [[None, None]])[0]
        if yb[0] is None or nb[0] is None:
            yield None
            continue
        yield (float(yb[0]), float(nb[0]), float(yb[1] or 0.0),
               float(nb[1] or 0.0))


def structure_report(by_market: Mapping[str, list]) -> dict:
    """FEE-INDEPENDENT execution structure per series, from the tape.

    pair cost to BUY both sides = yes_ask + no_ask = 2 - yes_bid - no_bid,
    because a resting NO bid IS the YES ask on this venue. The same
    identity makes spread = pair_cost - 1, so both are read off the two
    bids with no fee model and no assumed fill."""
    ser: Dict[str, Dict[str, Any]] = {}
    leg: Dict[str, Dict[str, Any]] = {}
    for market, events in by_market.items():
        s = str(market).split("-", 1)[0].upper()
        k = leg_key(market)
        buckets = [ser.setdefault(s, {"markets": set(), "two_sided": 0,
                                      "one_sided": 0, "pair_costs": [],
                                      "depths": []}),
                   leg.setdefault(k, {"markets": set(), "two_sided": 0,
                                      "one_sided": 0, "pair_costs": [],
                                      "depths": []})]
        for b in buckets:
            b["markets"].add(market)
        for q in _quotes(events):
            if q is None:
                for b in buckets:
                    b["one_sided"] += 1
                continue
            yb, nb, ysz, nsz = q
            for b in buckets:
                b["two_sided"] += 1
                b["pair_costs"].append(round(2.0 - yb - nb, 4))
                b["depths"].append(min(ysz, nsz))
    out = {}
    for s, b in ser.items():
        pc = sorted(b["pair_costs"])
        n = len(pc)
        dep = sorted(b["depths"])
        out[s] = {
            "markets": len(b["markets"]),
            "two_sided_quotes": b["two_sided"],
            "one_sided_quotes": b["one_sided"],
            "pair_cost_median": pc[n // 2] if n else None,
            "pair_cost_p10": pc[n // 10] if n >= 10 else None,
            "pair_cost_min": pc[0] if n else None,
            "frac_le_1_00": (round(sum(1 for x in pc if x <= 1.0) / n, 4)
                             if n else None),
            "frac_lt_1_00": (round(sum(1 for x in pc if x < 1.0) / n, 4)
                             if n else None),
            "spread_median": (round(pc[n // 2] - 1.0, 4) if n else None),
            "two_sided_depth_median": (dep[len(dep) // 2] if dep else None),
            "certified_for_net_economics": s in CERTIFIED_SERIES,
        }
    legs = {}
    for k, b in leg.items():
        pc = sorted(b["pair_costs"])
        n = len(pc)
        dep = sorted(b["depths"])
        legs[k] = {
            "markets": len(b["markets"]),
            "two_sided_quotes": b["two_sided"],
            "one_sided_quotes": b["one_sided"],
            "pair_cost_median": pc[n // 2] if n else None,
            "pair_cost_p10": pc[n // 10] if n >= 10 else None,
            "pair_cost_p90": pc[min(n - 1, (9 * n) // 10)] if n else None,
            "spread_median": (round(pc[n // 2] - 1.0, 4) if n else None),
            "two_sided_depth_median": (dep[len(dep) // 2] if dep else None),
        }
    return {"kind": "KALSHI_L5_STRUCTURE", "series": out, "legs": legs,
            "leg_note": ("legs are the decision unit; the series roll-up is "
                         "kept only for continuity with the earlier tape and "
                         "MUST NOT be used to compare KXCRYPTOLEAD15M assets, "
                         "whose books differ by an order of magnitude"),
            "labels": ("OBSERVED book structure, fee-independent. NOT a "
                       "shadow P&L and NOT realized: no order was placed, "
                       "no fill assumed, no fee model applied. Series with "
                       "certified_for_net_economics false have NO "
                       "established maker schedule here."),
            "shadow_only": True, "order_entry": False, "realized_pnl": None}


SHADOW_MAX_PARTS = 4
SHADOW_MAX_EVENTS = int(os.environ.get("KALSHI_L5_SHADOW_MAX_EVENTS", "60000"))
INCENTIVE_EVERY_N = 12   # incentive-program state is slow-moving; poll rarely


def bounded_tape(parts: Sequence[Path],
                 max_events: int = SHADOW_MAX_EVENTS) -> Dict[str, list]:
    """load_tape's dict, but hard-capped in EVENTS rather than in parts.

    "The newest four hourly parts" is not a memory bound: a part's size
    scales with how many markets are live, and only 6 of the 14 certified
    fifteen-minute series were open when this lane started. Measured RSS
    climbed 28.2 -> 39.0 MB over the first 61 cycles on 4-6 markets, which
    extrapolates badly. The cap is on events because that is what the
    analyser actually holds.

    Newest part first and, within a part, the TAIL -- an analyser looking
    for completion paths inside 15-minute windows needs the most recent
    tape, so what gets dropped under pressure must be the oldest evidence,
    never the newest. Chronological order is restored before returning."""
    chunks: List[list] = []
    budget = max(0, int(max_events))
    for path in reversed(list(parts)[-SHADOW_MAX_PARTS:]):
        if budget <= 0:
            break
        d: collections.deque = collections.deque(maxlen=budget)
        try:
            with open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("venue") != "kalshi" or not e.get("market"):
                        continue
                    d.append(e)
        except OSError:
            continue
        chunks.append(list(d))
        budget -= len(d)
    by_market: Dict[str, list] = {}
    for chunk in reversed(chunks):
        for e in chunk:
            by_market.setdefault(str(e["market"]), []).append(e)
    return by_market


def run_shadow() -> Optional[dict]:
    """The EXISTING analyser over the rolling tape. Nothing about the L5
    policy is reimplemented here."""
    parts = tape_parts()
    if not parts:
        return None
    try:
        by_market = bounded_tape(parts)
        out = shadow_status(by_market)
        if isinstance(out, dict):
            # whether the cap is actually binding has to be readable, not
            # inferred from an RSS trend: input_events at the cap means the
            # bound engaged, below it means the tape is still filling
            out["input_events"] = sum(len(v) for v in by_market.values())
            out["input_events_cap"] = SHADOW_MAX_EVENTS
        return out
    except Exception as exc:                            # noqa: BLE001
        print("KALSHI_L5_ALARM " + json.dumps(
            {"alarm": "shadow_failed",
             "err": f"{type(exc).__name__}:{str(exc)[:120]}"}), flush=True)
        return None


def _compact_shadow(status: Mapping[str, Any]) -> dict:
    """One line. The probe's 06:30Z exit printed a whole document and
    dropped 117,262 log messages including its own terminal status; the
    full row set stays on the tape."""
    return {"kind": "KALSHI_L5_SHADOW_STATUS",
            "model_sha": status.get("model_sha"),
            "markets": status.get("markets"),
            "markets_with_possible_fills": status.get("markets_with_possible_fills"),
            "executable_paths": status.get("executable_paths"),
            "repeated_executable_path": status.get("repeated_executable_path"),
            "pair_costs_theoretical": (status.get("pair_costs_theoretical")
                                       or [])[:8],
            "blockers": sorted({b for r in (status.get("rows") or [])
                                for b in (r.get("blockers") or [])})[:8],
            "input_events": status.get("input_events"),
            "input_events_cap": status.get("input_events_cap"),
            "shadow_only": True, "order_entry": False, "realized_pnl": None}


def main(argv: Optional[Sequence[str]] = None, *, sleep=time.sleep,
         clock=time.time) -> int:
    ap = argparse.ArgumentParser(
        prog="kalshi_l5_collector",
        description=("Keyless read-only collector for Kalshi 15M crypto "
                     "windows plus the L5 shadow loop over its tape. "
                     "No credentials, no orders."))
    ap.add_argument("--once", action="store_true", help="one cycle, then exit")
    ap.add_argument("--interval", type=float,
                    default=float(os.getenv(INTERVAL_ENV,
                                            str(DEFAULT_INTERVAL_S))))
    a = ap.parse_args(argv)

    print("KALSHI_L5_COLLECTOR " + json.dumps(
        {"kind": "boot", "series": list(COLLECT_SERIES),
         "certified_for_net_economics": list(CERTIFIED_SERIES),
         "observation_only": list(OBSERVATION_SERIES),
         "state_dir": str(state_dir()), "interval_s": a.interval,
         "tape_budget_bytes": tape_budget(),
         "clock": ("literal TTE: these windows serve open_time/close_time "
                   "and settle deterministically, so no sports-style "
                   "translation is applied"),
         "shadow_only": True, "order_entry": False}), flush=True)
    print("KALSHI_L5_COLLECTOR " + json.dumps(sweep_tape()), flush=True)

    # idempotent across restarts: never re-append a print the tape holds
    seen: Dict[str, float] = seed_seen_from_tape()
    windows: set = set()
    print("KALSHI_L5_COLLECTOR " + json.dumps(
        {"kind": "resume", "markets_with_prior_prints": len(seen),
         "note": ("newest print time per market restored from the tape, so "
                  "a restart cannot double-count fill evidence")}), flush=True)
    last_market = last_print = clock()
    every = max(1, int(os.getenv(SHADOW_EVERY_ENV, str(DEFAULT_SHADOW_EVERY))))
    hb = 0.0
    cycle = 0
    while True:
        cycle += 1
        row = collect_once(seen, windows)
        if row.get("error") or cycle % every == 1:
            print("KALSHI_L5_COLLECTOR " + json.dumps(row), flush=True)
        if cycle % every == 0:
            status = run_shadow()
            if status:
                print("KALSHI_L5_SHADOW " + json.dumps(_compact_shadow(status)),
                      flush=True)
            try:
                parts = tape_parts()
                if parts:
                    print("KALSHI_L5_STRUCTURE " + json.dumps(
                        structure_report(bounded_tape(parts))), flush=True)
            except Exception as exc:                     # noqa: BLE001
                print("KALSHI_L5_ALARM " + json.dumps(
                    {"alarm": "structure_failed",
                     "err": f"{type(exc).__name__}:{str(exc)[:120]}"}),
                    flush=True)
            if cycle % (every * INCENTIVE_EVERY_N) == 0:
                try:
                    print("KALSHI_INCENTIVE " + json.dumps(incentive_state()),
                          flush=True)
                except Exception as exc:                 # noqa: BLE001
                    print("KALSHI_L5_ALARM " + json.dumps(
                        {"alarm": "incentive_state_failed",
                         "err": f"{type(exc).__name__}:{str(exc)[:120]}"}),
                        flush=True)
            swept = sweep_tape()
            if swept["deleted"]:
                print("KALSHI_L5_COLLECTOR " + json.dumps(swept), flush=True)
        now = clock()
        if row.get("markets"):
            last_market = now
        if row.get("events", 0) > (1 if row.get("markets") else 0):
            last_print = now
        for label, since, limit in (("no_open_markets", last_market,
                                     STALE_MARKETS_S),
                                    ("no_new_prints", last_print,
                                     STALE_PRINTS_S)):
            if now - since >= limit:
                print("KALSHI_L5_ALARM " + json.dumps(
                    {"alarm": f"stale_data:{label}",
                     "seconds": round(now - since, 1), "limit_s": limit,
                     "note": ("a running collector that sees nothing looks "
                              "exactly like a quiet market unless it says "
                              "so")}), flush=True)
                if label == "no_open_markets":
                    last_market = now       # alarm once per stale period
                else:
                    last_print = now
        if now - hb >= HEARTBEAT_S:
            hb = now
            print("KALSHI_L5_COLLECTOR " + json.dumps(
                {"kind": "heartbeat", "cycle": cycle,
                 "markets_seen": len(windows),
                 "since_open_market_s": round(now - last_market, 1),
                 "since_new_print_s": round(now - last_print, 1),
                 "tape_bytes": sum((p.stat().st_size for p in tape_parts()
                                    if p.exists()), 0),
                 "rss_mb": _rss_now_mb(), "rss_peak_mb": _rss_peak_mb(),
                 "evidence": ("positive liveness: a missing heartbeat means "
                              "the process is gone, which quiet logs alone "
                              "can never tell you")}), flush=True)
        if a.once:
            return 0
        sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
