"""PAPER-ONLY cross-venue signal: Polymarket international (fair value) vs
Polymarket US (where the bot trades). Places and cancels NOTHING.

EVIDENCE (offline, 2026-11-15 21:05-21:35Z, 2,577 samples, 15 matched
markets, intl from the real CLOB book -- the gamma API's bestBid/bestAsk
proved to be a stale cache and is never used here):
  * the venues agree to ~1c at the median; spreads are 1c on both;
  * intl LEADS: corr(intl move, next PMUS move) 0.28 vs 0.02 the other way,
    and a PMUS-intl gap closes by PMUS moving (corr -0.48);
  * 48 distinct moments where PMUS was through intl's FAR side after the
    taker fee; a 1-contract round trip exiting at the PMUS bid ~40s later
    averaged +3.2c (32/48 won), ~150s later +5.6c (25/36) -- exit fee NOT
    included, 6 markets, one 30-minute window: in-sample, not proof.

So this module measures, OUT OF SAMPLE and in realtime, the two uses:
  TAKE  -- PMUS quote through intl's far side by more than the taker fee
           (would need explicit owner taker authorization to ever trade);
  PULL  -- the same moment seen from a resting PMUS maker quote: it is the
           order an informed taker would pick off.
Every paper entry is marked at fixed horizons at the PMUS bid AFTER an exit
taker fee (round trip fully costed) and logged. Nothing here has a handle to
the order path: it receives only read functions.

Intl data: the official public market WebSocket (book / price_change); the
REST CLOB refuses the default Python client and this module never alters
client identity to get around that. If the socket is unreachable the module
logs that and does nothing.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

INTL_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA = "https://gamma-api.polymarket.com"
# PMUS taker coefficient from 2026-11-14: Tier 1 (< $10k 30-day taker
# volume) 0.095 -- was 0.06, which overstated every paper taker edge
TAKER_FEE_RATE = float(os.getenv("PMUS_TWO_SIDED_TAKER_FEE_RATE", "0.095"))


def taker_fee(px: float, rate: float = TAKER_FEE_RATE) -> float:
    """PMUS taker fee for ONE contract, rounded up to the cent."""
    return math.ceil(rate * px * (1 - px) * 100 - 1e-9) / 100


def edge(pb: Optional[float], pa: Optional[float], ib: Optional[float],
         ia: Optional[float]) -> Optional[Dict[str, Any]]:
    """Conservative taker edge on PMUS vs intl: buy YES only below intl's
    BID, buy NO only below 1 - intl ASK, each after the PMUS taker fee."""
    if None in (pb, pa, ib, ia) or not (pa > pb and ia > ib):
        return None
    if not 0.03 < (ib + ia) / 2 < 0.97:
        return None
    ey = ib - pa - taker_fee(pa)
    no_cost = 1 - pb
    en = (1 - ia) - no_cost - taker_fee(no_cost)
    if max(ey, en) <= 0:
        return None
    if ey >= en:
        return {"side": "YES", "cost": pa, "edge": round(ey, 4)}
    return {"side": "NO", "cost": round(no_cost, 4), "edge": round(en, 4)}


class IntlBook:
    """Top of book per intl asset id from the public market socket."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.levels: Dict[str, Dict[str, Dict[float, float]]] = {}
        self.top: Dict[str, Dict[str, Any]] = {}

    def _set_top(self, aid: str, bb=None, ba=None) -> None:
        lv = self.levels.get(aid) or {"bids": {}, "asks": {}}
        bid = float(bb) if bb not in (None, "") else (
            max(lv["bids"]) if lv["bids"] else None)
        ask = float(ba) if ba not in (None, "") else (
            min(lv["asks"]) if lv["asks"] else None)
        self.top[aid] = {"bid": bid, "ask": ask, "t": self.clock()}

    def ingest(self, msg: Any) -> int:
        if isinstance(msg, list):
            return sum(self.ingest(m) for m in msg)
        if not isinstance(msg, Mapping):
            return 0
        et = msg.get("event_type")
        if et == "book":
            aid = str(msg.get("asset_id") or "")
            if not aid:
                return 0
            self.levels[aid] = {
                "bids": {float(x["price"]): float(x["size"])
                         for x in msg.get("bids") or [] if float(x["size"])},
                "asks": {float(x["price"]): float(x["size"])
                         for x in msg.get("asks") or [] if float(x["size"])}}
            self._set_top(aid)
            return 1
        if et == "price_change":
            n = 0
            changes = msg.get("price_changes") or msg.get("changes") or []
            for c in changes:
                aid = str(c.get("asset_id") or msg.get("asset_id") or "")
                if not aid:
                    continue
                lv = self.levels.setdefault(aid, {"bids": {}, "asks": {}})
                book = lv["bids"] if str(c.get("side")).upper() == "BUY" \
                    else lv["asks"]
                try:
                    px, sz = float(c["price"]), float(c["size"])
                except (KeyError, TypeError, ValueError):
                    continue
                if sz:
                    book[px] = sz
                else:
                    book.pop(px, None)
                self._set_top(aid, c.get("best_bid"), c.get("best_ask"))
                n += 1
            return n
        return 0

    def fresh(self, aid: str, max_age_s: float) -> Optional[Dict[str, Any]]:
        t = self.top.get(aid)
        if not t or self.clock() - t["t"] > max_age_s:
            return None
        return t


class PaperBook:
    """One paper position per market; marked at fixed horizons at the PMUS
    exit price AFTER a second taker fee. Pure bookkeeping."""

    def __init__(self, horizons=(40.0, 150.0), cooldown_s: float = 60.0):
        self.horizons = tuple(sorted(horizons))
        self.cooldown = cooldown_s
        self.open: Dict[str, Dict[str, Any]] = {}
        self.last_entry: Dict[str, float] = {}
        self.results: List[Dict[str, Any]] = []

    def maybe_enter(self, slug: str, e: Mapping[str, Any], now: float,
                    meta: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        if slug in self.open or now - self.last_entry.get(slug, -1e18) < \
                self.cooldown:
            return None
        rec = {"slug": slug, "t0": now, "side": e["side"], "cost": e["cost"],
               "edge": e["edge"], "fee_in": taker_fee(e["cost"]),
               "marks": {}, **meta}
        self.open[slug] = rec
        self.last_entry[slug] = now
        return rec

    def mark(self, slug: str, pb: Optional[float], pa: Optional[float],
             now: float) -> List[Dict[str, Any]]:
        rec = self.open.get(slug)
        if not rec or pb is None or pa is None:
            return []
        out = []
        exit_px = pb if rec["side"] == "YES" else 1 - pa
        for h in self.horizons:
            if h in rec["marks"] or now - rec["t0"] < h:
                continue
            pnl = exit_px - rec["cost"] - rec["fee_in"] - taker_fee(exit_px)
            rec["marks"][h] = round(pnl, 4)
            out.append({"slug": slug, "side": rec["side"], "horizon_s": h,
                        "entry": rec["cost"], "exit": round(exit_px, 4),
                        "pnl_after_both_fees": round(pnl, 4),
                        "edge_at_entry": rec["edge"],
                        "late_s": round(now - rec["t0"] - h, 1)})
        if len(rec["marks"]) == len(self.horizons):
            self.results.append(self.open.pop(slug))
        self.results = self.results[-500:]
        return out

    def summary(self) -> Dict[str, Any]:
        s: Dict[str, Any] = {"closed": len(self.results),
                             "open": len(self.open)}
        for h in self.horizons:
            xs = [r["marks"][h] for r in self.results if h in r["marks"]]
            if xs:
                s[f"h{int(h)}"] = {"n": len(xs),
                                   "mean": round(sum(xs) / len(xs), 4),
                                   "win": sum(1 for x in xs if x > 0)}
        return s


def _norm(x: Any) -> str:
    return " ".join(str(x or "").lower().replace(".", " ").split())


def yes_names(pm_market: Mapping[str, Any]) -> List[str]:
    """Every name PMUS gives the YES (long) side: the outcome label plus the
    long side's team name / safeName / alias / abbreviation. LIVE 2026-11-21:
    PMUS labels CFB outcomes by nickname ("Tigers") while intl uses the team
    ("LSU"), so an exact first-outcome match refused every CFB market."""
    names: List[str] = []
    po = pm_market.get("outcomes")
    if isinstance(po, str):
        try:
            po = json.loads(po)
        except ValueError:
            po = None
    if po:
        names.append(str(po[0]))
    for sd in pm_market.get("marketSides") or []:
        if not isinstance(sd, Mapping) or not sd.get("long"):
            continue
        names.append(str(sd.get("description") or ""))
        tm = sd.get("team") or {}
        for k in ("name", "safeName", "alias", "abbreviation",
                  "displayAbbreviation"):
            if tm.get(k):
                names.append(str(tm[k]))
    return [n for n in names if n]


def resolve_token(gamma_get: Callable[[str], Any], slug: str,
                  pm_outcomes: Optional[list],
                  yes_aliases: Optional[Iterable[str]] = None
                  ) -> Optional[str]:
    """PMUS 'aec-X' <-> intl 'X'; the intl token for PMUS YES (outcome[0]),
    refused unless intl's first outcome names the same YES side: the PMUS
    first outcome itself or one of the YES team's published identities
    (yes_aliases). Intl's SECOND outcome matching a YES alias is a
    reversed market and is refused."""
    if not slug.startswith("aec-"):
        return None
    base = slug[4:]
    rows = gamma_get(f"/markets?slug={base}") or []
    if not rows or not isinstance(rows, list):
        # game markets resolve only through their event (live 2026-11-15)
        evs = gamma_get(f"/events?slug={base}") or []
        rows = [x for e in (evs if isinstance(evs, list) else [])
                for x in (e.get("markets") or []) if x.get("slug") == base]
    if not rows:
        return resolve_by_names(gamma_get, slug, pm_outcomes)
    m = rows[0]
    try:
        io = json.loads(m.get("outcomes") or "[]")
        toks = json.loads(m.get("clobTokenIds") or "[]")
    except (TypeError, ValueError):
        return None
    if not io or not toks or not pm_outcomes:
        return None
    if io[0] != pm_outcomes[0]:
        al = {_norm(a) for a in (yes_aliases or []) if _norm(a)}
        if _norm(io[0]) not in al or \
                (len(io) > 1 and _norm(io[1]) in al):
            return None
    if m.get("closed"):
        return None
    return str(toks[0])


#: Leagues whose international slug is NOT 'aec-' stripped. LIVE 2026-11-22
#: 04:16Z: 7 ATP matches were live on PMUS and all 7 read "no international
#: market" -- intl lists them under surname slugs (PMUS
#: aec-atp-yukmoc-kosogu-2026-11-21 = intl atp-mochiz-ogura-2026-11-21), so
#: the exact-slug lookup found nothing and the guard excluded all tennis.
NAME_MATCH_LEAGUES = tuple(
    x.strip().lower() for x in os.getenv(
        "PMUS_TWO_SIDED_INTL_NAME_MATCH", "atp,wta").split(",") if x.strip())


def _slug_date(slug: str):
    from datetime import date
    try:
        y, mo, d = slug.rsplit("-", 3)[-3:]
        return date(int(y), int(mo), int(d))
    except (ValueError, TypeError):
        return None


def resolve_by_names(gamma_get: Callable[[str], Any], slug: str,
                     pm_outcomes: Optional[list]) -> Optional[str]:
    """The intl token for PMUS YES when the two venues name the event
    differently (tennis). Deliberately narrow -- a wrong match would anchor
    our bids to another match's price -- so EVERY one of these must hold, and
    exactly one market must satisfy them, or the answer is None (the guard
    then fails closed for that market, as before):

      * league in NAME_MATCH_LEAGUES, and the intl event slug is that
        league's singles slug (never '<league>-doubles-...');
      * both players' full names equal, IN THE SAME ORDER, after _norm;
      * slug dates within one day of each other;
      * the intl market is the event's own slug and its type is 'moneyline'
        (the same event also lists set winners and handicaps with the same
        two outcome names);
      * event and market are open, with exactly two tokens."""
    parts = slug.split("-")
    if len(parts) < 7 or parts[0] != "aec" or \
            parts[1] not in NAME_MATCH_LEAGUES:
        return None
    league, when = parts[1], _slug_date(slug)
    if when is None or not pm_outcomes or len(pm_outcomes) != 2:
        return None
    want = [_norm(pm_outcomes[0]), _norm(pm_outcomes[1])]
    if not want[0] or not want[1] or want[0] == want[1] or \
            len(want[0].split()) < 2:
        return None
    import urllib.parse
    q = urllib.parse.urlencode({"q": want[0].split()[-1],
                                "limit_per_type": 30})
    res = gamma_get("/public-search?" + q) or {}
    hits = set()
    for ev in (res.get("events") or []) if isinstance(res, Mapping) else []:
        if not isinstance(ev, Mapping) or ev.get("closed"):
            continue
        es = str(ev.get("slug") or "")
        if not es.startswith(league + "-") or \
                es.startswith(league + "-doubles-"):
            continue
        d = _slug_date(es)
        if d is None or abs((d - when).days) > 1:
            continue
        for x in ev.get("markets") or []:
            if not isinstance(x, Mapping) or x.get("slug") != es or \
                    x.get("closed") or \
                    str(x.get("sportsMarketType") or "") != "moneyline":
                continue
            try:
                io = json.loads(x.get("outcomes") or "[]")
                toks = json.loads(x.get("clobTokenIds") or "[]")
            except (TypeError, ValueError):
                continue
            if len(io) == 2 and len(toks) == 2 and \
                    [_norm(io[0]), _norm(io[1])] == want:
                hits.add(str(toks[0]))
    return hits.pop() if len(hits) == 1 else None


#: Soccer leagues whose three-outcome markets may be priced internationally.
#: OWNER 2026-11-22: "Start with Serie A and build an exact three-outcome
#: market resolver ... matching home win, draw, and away win individually
#: across venues ... fail closed on ambiguous or missing mappings." Only
#: leagues that use the SAME league code on both venues belong here (Serie A
#: is `sea` on both; thai1/tha1, tff1/tur2, serca/serc differ and are not
#: mapped).
THREE_WAY_LEAGUES = tuple(
    x.strip().lower() for x in os.getenv(
        "PMUS_TWO_SIDED_INTL_THREE_WAY", "sea").split(",") if x.strip())
PM_THREE_WAY_TYPE = "soccer_team_full_time_winner"


def _words(x: Any) -> str:
    """Lower-case words only: punctuation never joins or hides a word
    ("draw?" is the word "draw"; "vs." is "vs")."""
    return " ".join("".join(ch if ch.isalnum() else " "
                            for ch in str(x or "").lower()).split())


def _has_name(text: Any, name: Any) -> bool:
    """`name` appears in `text` as whole words."""
    t, n = " %s " % _words(text), _words(name)
    return bool(n) and (" %s " % n) in t


def _json_list(v: Any) -> Optional[list]:
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v or "[]")
    except (TypeError, ValueError):
        return None
    return out if isinstance(out, list) else None


def resolve_three_way(gamma_get: Callable[[str], Any], slug: str,
                      pm_market: Optional[Mapping[str, Any]]
                      ) -> Optional[str]:
    """The intl "Yes" token for ONE outcome of a soccer match:
    PMUS atc-<lg>-<home>-<away>-<y>-<m>-<d>-<home|draw|away>  <->
    intl      <lg>-<home>-<away>-<y>-<m>-<d>-<home|draw|away>.

    Each outcome is its own Yes/No market on both venues; this maps one of
    them and never combines outcomes. Returns None (the guard then fails
    closed) unless EVERY check holds:

      PMUS  league in THREE_WAY_LEAGUES; slug has the 8-part shape and its
            outcome suffix is the home code, the away code or 'draw';
            outcomes are exactly ["Yes","No"]; type is the full-time winner.
      intl  the event <lg>-<home>-<away>-<date> exists, is open, and its
            moneyline markets are EXACTLY the three outcomes (home, draw,
            away) -- no more, no fewer; the wanted one is open, has
            outcomes exactly ["Yes","No"] and two tokens.
      same outcome, by name (not only by slug):
            team market: PMUS's long-side team name is one of the two
              teams in the intl event title, the intl question names that
              team and does not say 'draw', and the OTHER team's intl
              market does not name it;
            draw market: both intl team names appear in the PMUS question,
              which says 'draw', and the intl question says 'draw'."""
    parts = str(slug).split("-")
    if len(parts) != 8 or parts[0] != "atc" or \
            parts[1] not in THREE_WAY_LEAGUES or not pm_market:
        return None
    league, home, away, suffix = parts[1], parts[2], parts[3], parts[7]
    if _slug_date("-".join(parts[:7])) is None or home == away or \
            suffix not in (home, away, "draw") or "draw" in (home, away):
        return None
    if _json_list(pm_market.get("outcomes")) != ["Yes", "No"] or \
            str(pm_market.get("sportsMarketType") or "") != PM_THREE_WAY_TYPE:
        return None
    ev_slug = "-".join(parts[1:7])
    evs = gamma_get(f"/events?slug={ev_slug}") or []
    if not isinstance(evs, list) or len(evs) != 1 or \
            not isinstance(evs[0], Mapping):
        return None
    ev = evs[0]
    if ev.get("slug") != ev_slug or ev.get("closed"):
        return None
    ml = [x for x in ev.get("markets") or [] if isinstance(x, Mapping)
          and str(x.get("sportsMarketType") or "") == "moneyline"]
    by = {str(x.get("slug")): x for x in ml}
    want3 = {f"{ev_slug}-{home}", f"{ev_slug}-draw", f"{ev_slug}-{away}"}
    if len(ml) != 3 or set(by) != want3:
        return None
    teams = [t.strip() for t in
             str(ev.get("title") or "").replace(" vs. ", " vs ").split(" vs ")]
    if len(teams) != 2 or not all(_norm(t) for t in teams) or \
            _norm(teams[0]) == _norm(teams[1]):
        return None
    m = by[f"{ev_slug}-{suffix}"]
    toks = _json_list(m.get("clobTokenIds"))
    if m.get("closed") or _json_list(m.get("outcomes")) != ["Yes", "No"] or \
            not toks or len(toks) != 2:
        return None
    iq, pq = m.get("question"), pm_market.get("question")
    if suffix == "draw":
        if not (_has_name(pq, "draw") and _has_name(iq, "draw") and
                all(_has_name(pq, t) for t in teams)):
            return None
        return str(toks[0])
    long_team = None
    for sd in pm_market.get("marketSides") or []:
        if isinstance(sd, Mapping) and sd.get("long"):
            long_team = (sd.get("team") or {}).get("name")
    if not long_team or _norm(long_team) not in {_norm(t) for t in teams}:
        return None
    other = by[f"{ev_slug}-{away if suffix == home else home}"]
    if not _has_name(iq, long_team) or _has_name(iq, "draw") or \
            _has_name(other.get("question"), long_team):
        return None
    return str(toks[0])


def resolve_market(gamma_get: Callable[[str], Any], slug: str,
                   pm_market: Optional[Mapping[str, Any]]) -> Optional[str]:
    """One entry for every structure the guard can price: two-outcome match
    winners (aec-) and, for mapped leagues, single soccer outcomes (atc-)."""
    if not isinstance(pm_market, Mapping):
        return None
    if str(slug).startswith("atc-"):
        return resolve_three_way(gamma_get, slug, pm_market)
    po = _json_list(pm_market.get("outcomes"))
    return resolve_token(gamma_get, slug, po, yes_names(pm_market))


class _IntlSocket:
    """Shared: the intl public market socket feeding an IntlBook for the
    assets in self.tokens (slug -> token). Subclasses set tokens/_lock."""

    def _notify(self, msg: Any) -> None:
        """Hook for subclasses; called after each ingested message."""

    # ---------------------------------------------------------- intl socket
    def _ws_thread(self) -> None:
        import asyncio
        try:
            asyncio.run(self._ws_run())
        except Exception as exc:                          # noqa: BLE001
            self.ws_state["last_err"] = f"{type(exc).__name__}:{exc}"[:160]

    async def _ws_run(self) -> None:
        import asyncio
        import websockets
        backoff = 2.0
        while not self.stop:
            with self._lock:
                want = sorted(set(self.tokens.values()))
            if not want:
                await asyncio.sleep(5)
                continue
            try:
                async with websockets.connect(INTL_WS, open_timeout=15,
                                              ping_interval=None) as ws:
                    await ws.send(json.dumps({"assets_ids": want,
                                              "type": "market"}))
                    self.ws_state["connected"] = True
                    backoff = 2.0
                    last_ping = time.monotonic()
                    while not self.stop:
                        with self._lock:
                            now_want = sorted(set(self.tokens.values()))
                        if now_want != want:
                            break                     # resubscribe
                        try:
                            raw = await asyncio.wait_for(ws.recv(), 10)
                        except asyncio.TimeoutError:
                            raw = None
                        if time.monotonic() - last_ping > 10:
                            await ws.send("PING")
                            last_ping = time.monotonic()
                        if not raw or raw == "PONG":
                            continue
                        try:
                            msg = json.loads(raw)
                            self.intl.ingest(msg)
                            self.ws_state["msgs"] += 1
                            self._notify(msg)
                        except (TypeError, ValueError):
                            continue
            except Exception as exc:                      # noqa: BLE001
                self.ws_state["errors"] += 1
                self.ws_state["last_err"] = \
                    f"{type(exc).__name__}:{str(exc)[:120]}"
            self.ws_state["connected"] = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120.0)


class XvPaper(_IntlSocket):
    """Runner. READ functions only: targets() -> PMUS sports targets,
    pm_get(path) -> PMUS public gateway JSON, gamma_get(path) -> gamma JSON.
    No order-path handle is ever given to this object."""

    def __init__(self, targets: Callable[[], list], pm_get: Callable,
                 gamma_get: Callable, log: Callable, *, max_markets: int = 20,
                 poll_gap_s: float = 0.35, intl_max_age_s: float = 30.0,
                 refresh_s: float = 300.0, summary_s: float = 600.0,
                 tape_event: Optional[Callable] = None,
                 kalshi: Optional[Any] = None,
                 kalshi_max_age_s: float = 10.0):
        self.targets, self.pm_get, self.gamma_get = targets, pm_get, gamma_get
        self.log, self.tape_event = log, tape_event
        self.max_markets, self.gap = max_markets, poll_gap_s
        self.intl_max_age, self.refresh_s = intl_max_age_s, refresh_s
        self.summary_s = summary_s
        self.intl = IntlBook()
        self.paper = PaperBook()
        # second, independent fair-value source (src/pmus_sports_kalshi.py)
        self.kalshi = kalshi
        self.kalshi_max_age = kalshi_max_age_s
        self.paper_k = PaperBook()
        self.kt: Dict[str, str] = {}
        self._kres: Dict[str, Optional[str]] = {}
        self.tokens: Dict[str, str] = {}
        self._lock = threading.Lock()
        self.stop = False
        self.ws_state = {"connected": False, "msgs": 0, "errors": 0,
                         "last_err": None}
        self.stats = {"polls": 0, "edges": 0, "entries": 0, "no_intl": 0,
                      "k_edges": 0, "k_entries": 0, "no_kalshi": 0}
        # intl vs Kalshi on the same market at the same moment
        self.agree = {"both": 0, "abs_gap_sum": 0.0, "gap_ge_2c": 0}

    def _emit(self, kind: str, **kw: Any) -> None:
        self.log(kind, **kw)
        if self.tape_event is not None:
            try:
                self.tape_event(kind, kw)
            except Exception:                             # noqa: BLE001
                pass

    # ---------------------------------------------------------- discovery
    def refresh(self) -> None:
        found: Dict[str, str] = {}
        kfound: Dict[str, str] = {}
        for tg in self.targets() or []:
            slug = str(tg.get("slug") or "")
            if not slug.startswith("aec-") or "cricket" in slug:
                continue
            need_intl = slug not in self.tokens
            need_k = self.kalshi is not None and slug not in self._kres
            m = None
            if need_intl or need_k:
                try:
                    md = self.pm_get(f"/v1/market/slug/{slug}") or {}
                    m = md.get("market") if isinstance(md, Mapping) and \
                        "market" in md else md
                except Exception:                         # noqa: BLE001
                    m = None
            if not need_intl:
                found[slug] = self.tokens[slug]
            elif isinstance(m, Mapping):
                try:
                    po = m.get("outcomes")
                    if isinstance(po, str):
                        po = json.loads(po)
                    tok = resolve_token(self.gamma_get, slug, po)
                except Exception:                         # noqa: BLE001
                    tok = None
                if tok:
                    found[slug] = tok
            if need_k and isinstance(m, Mapping):
                try:
                    self._kres[slug] = self.kalshi.resolve(slug, m)
                except Exception:                         # noqa: BLE001
                    pass                                  # retried next time
            if self._kres.get(slug):
                kfound[slug] = self._kres[slug]
            if len(set(found) | set(kfound)) >= self.max_markets:
                break
        with self._lock:
            self.tokens = found
            self.kt = kfound
        if self.kalshi is not None:
            self.kalshi.set_want(kfound.values())
        self._emit("XV_PAPER_MARKETS", n=len(found), slugs=sorted(found),
                   kalshi_n=len(kfound),
                   kalshi_only=sorted(set(kfound) - set(found)))

    # ---------------------------------------------------------- PMUS poll
    def _score(self, source: str, book: "PaperBook", slug: str,
               pb, pa, fv: Optional[Mapping[str, Any]], now: float) -> None:
        if fv is None:
            self.stats["no_intl" if source == "intl" else "no_kalshi"] += 1
            return
        e = edge(pb, pa, fv["bid"], fv["ask"])
        if e is None:
            return
        self.stats["edges" if source == "intl" else "k_edges"] += 1
        rec = book.maybe_enter(slug, e, now, {
            "source": source, "pm_bid": pb, "pm_ask": pa,
            "fv_bid": fv["bid"], "fv_ask": fv["ask"],
            "fv_age_s": round(now - fv["t"], 1)})
        if rec is None:
            return
        self.stats["entries" if source == "intl" else "k_entries"] += 1
        self._emit("XV_PAPER_ENTRY", slug=slug, source=source,
                   side=e["side"], cost=e["cost"], edge=e["edge"],
                   pm_bid=pb, pm_ask=pa, fv_bid=fv["bid"], fv_ask=fv["ask"],
                   fv_age_s=rec["fv_age_s"],
                   pull_signal=("resting PMUS %s ask at %s would be "
                                "picked off" % (e["side"], e["cost"])),
                   label="PAPER ONLY: no order placed")

    def step(self, slug: str, tok: Optional[str], now: float) -> None:
        md = (self.pm_get(f"/v1/markets/{slug}/bbo") or {}).get(
            "marketData") or {}

        def f(v):
            if isinstance(v, Mapping):
                v = v.get("value")
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        pb, pa = f(md.get("bestBid")), f(md.get("bestAsk"))
        self.stats["polls"] += 1
        for src, book in (("intl", self.paper), ("kalshi", self.paper_k)):
            for r in book.mark(slug, pb, pa, now):
                self._emit("XV_PAPER_RESULT", source=src, **r)
        it = self.intl.fresh(tok, self.intl_max_age) if tok else None
        kq = None
        if self.kalshi is not None and self.kt.get(slug):
            kq = self.kalshi.fresh(self.kt[slug], self.kalshi_max_age)
        if it and kq and it["bid"] is not None and it["ask"] is not None:
            gap = abs((it["bid"] + it["ask"]) / 2 - (kq["bid"] + kq["ask"]) / 2)
            self.agree["both"] += 1
            self.agree["abs_gap_sum"] += gap
            self.agree["gap_ge_2c"] += int(gap >= 0.02 - 1e-9)
        if tok:
            self._score("intl", self.paper, slug, pb, pa, it, now)
        if self.kt.get(slug):
            self._score("kalshi", self.paper_k, slug, pb, pa, kq, now)

    def summary(self) -> Dict[str, Any]:
        a = self.agree
        return {"ws": dict(self.ws_state), "stats": dict(self.stats),
                "paper_intl": self.paper.summary(),
                "paper_kalshi": self.paper_k.summary(),
                "kalshi": (dict(self.kalshi.stats) if self.kalshi is not None
                           else None),
                "intl_vs_kalshi": {
                    "both_fresh": a["both"],
                    "mean_abs_mid_gap": (round(a["abs_gap_sum"] / a["both"], 4)
                                         if a["both"] else None),
                    "gap_ge_2c": a["gap_ge_2c"]},
                "label": "PAPER ONLY; theoretical; never P&L"}

    def run(self) -> None:
        threading.Thread(target=self._ws_thread, daemon=True,
                         name="xv-intl-ws").start()
        if self.kalshi is not None:
            self.kalshi.start()
        last_ref = last_sum = 0.0
        while not self.stop:
            now = time.time()
            if now - last_ref > self.refresh_s:
                try:
                    self.refresh()
                except Exception as exc:                  # noqa: BLE001
                    self._emit("XV_PAPER_ERROR", at="refresh",
                               err=f"{type(exc).__name__}:{str(exc)[:120]}")
                last_ref = now
            if now - last_sum > self.summary_s:
                self._emit("XV_PAPER_SUMMARY", **self.summary())
                last_sum = now
            with self._lock:
                slugs = sorted(set(self.tokens) | set(self.kt))
                toks = dict(self.tokens)
            if not slugs:
                time.sleep(5)
                continue
            for slug in slugs:
                if self.stop:
                    break
                try:
                    self.step(slug, toks.get(slug), time.time())
                except Exception as exc:                  # noqa: BLE001
                    self._emit("XV_PAPER_ERROR", at="step", slug=slug,
                               err=f"{type(exc).__name__}:{str(exc)[:120]}")
                time.sleep(self.gap)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True, name="xv-paper")
        t.start()
        return t


class IntlGuard(_IntlSocket):
    """LIVE-PATH GUARD (default off; PMUS_TWO_SIDED_INTL_GUARD=1).

    The losses were never a lack of fills: 1,004 contracts filled; completed
    pairs made +$8.47, and 37 of 37 markets left with one leg lost ~-$29.
    The lone leg is the fill an informed taker picks off when the price is
    already moving -- and Polymarket intl moves FIRST (offline corr 0.28 vs
    0.02 the other way). So an OPENING bid is posted, and kept, only while
    intl's fair value says it is still cheap:

        YES bid p is unsafe when  p > intl_mid      - margin
        NO  bid q is unsafe when  q > (1 - intl_mid) - margin

    Fails CLOSED for openings: no fresh intl quote -> no opening (require
    mode). Completions are never touched. This object can only answer
    questions and wake the engine; it has no order path."""

    def __init__(self, pm_get: Callable, gamma_get: Callable, *,
                 margin_ticks: int = 1, max_age_s: float = 20.0,
                 require: bool = True, on_change: Optional[Callable] = None,
                 log: Optional[Callable] = None):
        self.pm_get, self.gamma_get = pm_get, gamma_get
        self.margin_ticks, self.max_age = margin_ticks, max_age_s
        self.require = require
        self.on_change = on_change
        self.log = log or (lambda *a, **k: None)
        self.intl = IntlBook()
        self.tokens: Dict[str, str] = {}
        self.resolved: Dict[str, Optional[str]] = {}
        self._pending: List[str] = []
        self._lock = threading.Lock()
        self.stop = False
        self.ws_state = {"connected": False, "msgs": 0, "errors": 0,
                         "last_err": None}
        self._asset_slug: Dict[str, str] = {}
        self.stats = {"blocked": 0, "pulled": 0, "no_intl": 0, "ok": 0}

    # -- matching (background thread: never on the trading loop)
    def prefetch(self, slugs: Iterable[str]) -> None:
        with self._lock:
            for s in slugs:
                if s not in self.resolved and s not in self._pending:
                    self._pending.append(s)

    def token(self, slug: str) -> Optional[str]:
        return self.resolved.get(slug)

    def known(self, slug: str) -> bool:
        return slug in self.resolved

    def track(self, slugs: Iterable[str]) -> None:
        """Subscribe intl for exactly these markets (the engine's active).
        LIVE 2026-11-15 23:43Z: markets selected BEFORE the guard existed
        were never matched (prefetch ran only on new candidates), so the
        guard priced nothing and those markets held every slot. Every
        tracked market is now queued for matching too."""
        slugs = list(slugs)
        self.prefetch(slugs)
        want = {s: self.resolved[s] for s in slugs if self.resolved.get(s)}
        with self._lock:
            self.tokens = want
            self._asset_slug = {t: s for s, t in want.items()}

    def _resolve_one(self, slug: str) -> None:
        tok = None
        try:
            md = self.pm_get(f"/v1/market/slug/{slug}") or {}
            m = md.get("market") if isinstance(md, Mapping) and \
                "market" in md else md
            tok = resolve_market(self.gamma_get, slug, m)
        except Exception:                                 # noqa: BLE001
            tok = None
        self.resolved[slug] = tok

    def _resolver(self) -> None:
        while not self.stop:
            with self._lock:
                slug = self._pending.pop(0) if self._pending else None
            if slug is None:
                time.sleep(1.0)
                continue
            self._resolve_one(slug)
            time.sleep(0.2)

    # -- the decision (pure given the intl book)
    def verdict(self, slug: str, prices: Mapping[str, Any], tick: Any
                ) -> Dict[str, Any]:
        tok = self.resolved.get(slug)
        it = self.intl.fresh(tok, self.max_age) if tok else None
        if it is None or it["bid"] is None or it["ask"] is None or \
                it["ask"] <= it["bid"]:
            self.stats["no_intl"] += 1
            # matched=True while matching is still pending: the engine holds
            # the market; only a market intl CANNOT price is released
            return {"ok": not self.require, "why": "no_fresh_intl",
                    "matched": bool(tok) or slug not in self.resolved}
        mid = (it["bid"] + it["ask"]) / 2
        margin = float(tick) * self.margin_ticks
        fair = {"YES": mid, "NO": 1 - mid}
        bad = {sd: round(fair[sd], 4) for sd, px in prices.items()
               if px is not None and float(px) > fair[sd] - margin + 1e-9}
        if bad:
            return {"ok": False, "why": "intl_says_not_cheap",
                    "intl_mid": round(mid, 4), "unsafe": bad,
                    "intl_age_s": round(self.intl.clock() - it["t"], 1)}
        self.stats["ok"] += 1
        return {"ok": True, "why": "intl_confirms", "intl_mid": round(mid, 4)}

    def _notify(self, msg: Any) -> None:
        if self.on_change is None:
            return
        ids = set()
        for m in (msg if isinstance(msg, list) else [msg]):
            if not isinstance(m, Mapping):
                continue
            if m.get("asset_id"):
                ids.add(str(m["asset_id"]))
            for c in m.get("price_changes") or m.get("changes") or []:
                if isinstance(c, Mapping) and c.get("asset_id"):
                    ids.add(str(c["asset_id"]))
        for a in ids:
            s = self._asset_slug.get(a)
            if s:
                try:
                    self.on_change(s)
                except Exception:                         # noqa: BLE001
                    pass

    def start(self) -> None:
        threading.Thread(target=self._resolver, daemon=True,
                         name="intl-guard-resolve").start()
        threading.Thread(target=self._ws_thread, daemon=True,
                         name="intl-guard-ws").start()

    def status(self) -> Dict[str, Any]:
        return {"ws": dict(self.ws_state), "matched": len(self.tokens),
                "resolved": sum(1 for v in self.resolved.values() if v),
                "unmatched": sum(1 for v in self.resolved.values() if not v),
                **self.stats}


def gamma_get(path: str, timeout: float = 10.0) -> Any:
    import urllib.request
    with urllib.request.urlopen(GAMMA + path, timeout=timeout) as r:  # noqa: S310
        return json.loads(r.read())
