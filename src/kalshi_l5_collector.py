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
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from src.kalshi_l5_shadow import CERTIFIED_SERIES, load_tape, shadow_status

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

def open_markets(series: Sequence[str] = CERTIFIED_SERIES) -> List[dict]:
    """Open markets for the certified 15M series only. An uncertified
    series is not collected: the analyser would refuse it anyway, and
    collecting it would imply a fee schedule we have not established."""
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


def run_shadow() -> Optional[dict]:
    """The EXISTING analyser over the rolling tape. Nothing about the L5
    policy is reimplemented here."""
    parts = tape_parts()
    if not parts:
        return None
    try:
        return shadow_status(load_tape(parts[-4:]))
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
        {"kind": "boot", "series": list(CERTIFIED_SERIES),
         "state_dir": str(state_dir()), "interval_s": a.interval,
         "tape_budget_bytes": tape_budget(),
         "clock": ("literal TTE: these windows serve open_time/close_time "
                   "and settle deterministically, so no sports-style "
                   "translation is applied"),
         "shadow_only": True, "order_entry": False}), flush=True)
    print("KALSHI_L5_COLLECTOR " + json.dumps(sweep_tape()), flush=True)

    seen: Dict[str, float] = {}
    windows: set = set()
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
            swept = sweep_tape()
            if swept["deleted"]:
                print("KALSHI_L5_COLLECTOR " + json.dumps(swept), flush=True)
        now = clock()
        if now - hb >= HEARTBEAT_S:
            hb = now
            print("KALSHI_L5_COLLECTOR " + json.dumps(
                {"kind": "heartbeat", "cycle": cycle,
                 "markets_seen": len(windows),
                 "rss_mb": _rss_now_mb(), "rss_peak_mb": _rss_peak_mb(),
                 "evidence": ("positive liveness: a missing heartbeat means "
                              "the process is gone, which quiet logs alone "
                              "can never tell you")}), flush=True)
        if a.once:
            return 0
        sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
