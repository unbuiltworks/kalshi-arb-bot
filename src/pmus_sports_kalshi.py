"""Kalshi as a free, independent fair-value source for PMUS sports markets.

OWNER 2026-11-16: "build the Kalshi one" -- a second fair price beside
Polymarket intl, from Kalshi's FREE public market-data API (no key, no
account, read-only GETs). Kalshi lists NBA, NHL, NFL, college football and
basketball, and ATP tennis game-winner markets -- including tennis, which
intl mostly does not, and where many of our stranded legs came from.

MATCHING (from data, never from position in a list):
  PMUS  aec-<league>-<a>-<b>-<YYYY-MM-DD>, marketSides[] each with
        long (True = the PMUS YES side) and a team/player object
        (name "New York Knicks", abbreviation "ny", logo ".../NYK_light...").
  Kalshi <SERIES>-<YYMONDD><CODES>-<CODE>, one market per side, with
        yes_sub_title "New York" / "Carlos Alcaraz".
A Kalshi event matches a PMUS market only when BOTH PMUS sides map to its
two markets one-to-one (by team code, logo code or name) and exactly one
Kalshi event on that date does. PMUS YES = the Kalshi market of the PMUS
long side. Anything ambiguous is left unmatched.

PRICES: one batched GET /markets?tickers=... per poll; a quote is used only
while fresh. Nothing here can place or cancel anything.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = {"nba": "KXNBAGAME", "nhl": "KXNHLGAME", "nfl": "KXNFLGAME",
          "cfb": "KXNCAAFGAME", "cbb": "KXNCAAMBGAME", "atp": "KXATPMATCH"}
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
          "OCT", "NOV", "DEC")


def kalshi_get(path: str, timeout: float = 10.0) -> Any:
    with urllib.request.urlopen(KALSHI + path, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read())


def kalshi_date(iso: str) -> Optional[str]:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y[2:]}{MONTHS[int(mo) - 1]}{d}"


def slug_parts(slug: str) -> Optional[Dict[str, str]]:
    m = re.fullmatch(r"aec-([a-z0-9]+)-(.+)-(\d{4}-\d{2}-\d{2})", slug or "")
    if not m:
        return None
    return {"league": m.group(1), "date": m.group(3)}


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(s or "").lower()).strip()


def pm_sides(market: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Each PMUS side: long flag, names and codes it can be matched by."""
    out = []
    sides = market.get("marketSides")
    if isinstance(sides, str):
        try:
            sides = json.loads(sides)
        except ValueError:
            sides = []
    for s in sides or []:
        if not isinstance(s, Mapping):
            continue
        t = s.get("team") if isinstance(s.get("team"), Mapping) else {}
        names = {_norm(x) for x in (t.get("name"), t.get("alias"),
                                    t.get("safeName"), s.get("description"))
                 if x}
        codes = {str(x).upper() for x in (t.get("abbreviation"),
                                          t.get("displayAbbreviation")) if x}
        logo = str(t.get("logo") or "")
        lm = re.search(r"/([A-Z0-9]{2,5})_(?:light|dark)", logo)
        if lm:
            codes.add(lm.group(1))
        out.append({"long": bool(s.get("long")), "names": names,
                    "codes": codes})
    return out


def side_matches(side: Mapping[str, Any], km: Mapping[str, Any]) -> bool:
    code = str(km.get("ticker") or "").rsplit("-", 1)[-1].upper()
    if code and (code in side["codes"] or any(
            len(c) >= 2 and code.startswith(c) for c in side["codes"])):
        return True
    kn = _norm(km.get("yes_sub_title"))
    if not kn:
        return False
    for n in side["names"]:
        if n == kn or n.startswith(kn + " "):
            return True                       # "new york knicks" / "new york"
        kt, nt = kn.split(), n.split()
        if len(kt) >= 2 and len(nt) >= 2 and kt[-1] == nt[-1] and \
                kt[0][0] == nt[0][0]:
            return True                       # player: same surname+initial
    return False


def match(slug: str, pm_market: Mapping[str, Any],
          kalshi_markets: Iterable[Mapping[str, Any]]) -> Optional[str]:
    """Kalshi ticker that prices PMUS YES for `slug`, or None."""
    p = slug_parts(slug)
    if not p or p["league"] not in SERIES:
        return None
    # the slug carries the LISTING date; the game can be the next day (live
    # 2026-11-16: aec-atp-caralc-fratia-2026-11-15 starts 11-16T13:00Z and
    # Kalshi files it under 26NOV16). Try slug date and the start date in
    # UTC and US Eastern; a match must still be unique across all of them.
    dates = {p["date"]}
    st = str(pm_market.get("gameStartTime") or "")
    try:
        t0 = datetime.fromisoformat(st.replace("Z", "+00:00"))
        dates.add(t0.strftime("%Y-%m-%d"))
        dates.add((t0 - timedelta(hours=5)).strftime("%Y-%m-%d"))
    except ValueError:
        pass
    prefixes = tuple(f"{SERIES[p['league']]}-{kalshi_date(d)}" for d in dates)
    sides = pm_sides(pm_market)
    if len(sides) != 2 or sum(s["long"] for s in sides) != 1:
        return None
    events: Dict[str, List[Mapping[str, Any]]] = {}
    for km in kalshi_markets:
        ev = str(km.get("event_ticker") or "")
        if ev.startswith(prefixes):
            events.setdefault(ev, []).append(km)
    hits = []
    for ev, kms in events.items():
        if len(kms) != 2:
            continue
        for a, b in ((kms[0], kms[1]), (kms[1], kms[0])):
            if side_matches(sides[0], a) and side_matches(sides[1], b) \
                    and not side_matches(sides[0], b):
                long_km = a if sides[0]["long"] else b
                hits.append(str(long_km["ticker"]))
                break
    return hits[0] if len(set(hits)) == 1 else None


class KalshiFair:
    """Background reader: series listings (slow) and batched prices (fast)
    for the tickers in `self.want`. Read functions only."""

    def __init__(self, get_json: Callable[[str], Any] = kalshi_get, *,
                 clock: Callable[[], float] = time.time,
                 list_ttl_s: float = 600.0, poll_s: float = 2.0,
                 batch: int = 50):
        self.get, self.clock = get_json, clock
        self.list_ttl, self.poll_s, self.batch = list_ttl_s, poll_s, batch
        self._lists: Dict[str, Any] = {}
        self.top: Dict[str, Dict[str, Any]] = {}
        self.want: set = set()
        self.stop = False
        self.stats = {"polls": 0, "errors": 0, "last_err": None}
        self._lock = threading.Lock()

    def listing(self, series: str) -> List[Mapping[str, Any]]:
        hit = self._lists.get(series)
        if hit and self.clock() - hit[0] < self.list_ttl:
            return hit[1]
        rows: List[Mapping[str, Any]] = []
        cursor = ""
        for _ in range(10):
            d = self.get(f"/markets?series_ticker={series}&status=open"
                         f"&limit=1000" + (f"&cursor={cursor}" if cursor
                                           else "")) or {}
            rows.extend(d.get("markets") or [])
            cursor = d.get("cursor") or ""
            if not cursor:
                break
        self._lists[series] = (self.clock(), rows)
        return rows

    def resolve(self, slug: str, pm_market: Mapping[str, Any]
                ) -> Optional[str]:
        p = slug_parts(slug)
        if not p or p["league"] not in SERIES:
            return None
        return match(slug, pm_market, self.listing(SERIES[p["league"]]))

    def ingest(self, markets: Iterable[Mapping[str, Any]],
               now: Optional[float] = None) -> None:
        now = self.clock() if now is None else now
        for km in markets:
            try:
                b = float(km.get("yes_bid_dollars"))
                a = float(km.get("yes_ask_dollars"))
            except (TypeError, ValueError):
                continue
            if km.get("status") not in (None, "active", "open"):
                continue
            self.top[str(km["ticker"])] = {"bid": b, "ask": a, "t": now}

    def poll_once(self) -> None:
        with self._lock:
            want = sorted(self.want)
        for i in range(0, len(want), self.batch):
            chunk = want[i:i + self.batch]
            try:
                d = self.get("/markets?tickers=" + ",".join(chunk)) or {}
                self.ingest(d.get("markets") or [])
                self.stats["polls"] += 1
            except Exception as exc:                      # noqa: BLE001
                self.stats["errors"] += 1
                self.stats["last_err"] = f"{type(exc).__name__}:{exc}"[:120]

    def fresh(self, ticker: Optional[str], max_age_s: float
              ) -> Optional[Dict[str, Any]]:
        t = self.top.get(ticker or "")
        if not t or self.clock() - t["t"] > max_age_s or t["ask"] <= t["bid"]:
            return None
        return t

    def set_want(self, tickers: Iterable[str]) -> None:
        with self._lock:
            self.want = {t for t in tickers if t}

    def run(self) -> None:
        while not self.stop:
            self.poll_once()
            time.sleep(self.poll_s)

    def start(self) -> threading.Thread:
        th = threading.Thread(target=self.run, daemon=True, name="kalshi")
        th.start()
        return th
