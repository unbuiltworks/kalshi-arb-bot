"""PRE-GAME, SHARP-ODDS-ANCHORED quoting for the sports lane (owner
2026-11-18: "build the pre-game sharp odds mode").

WHY. Live two-sided quoting has no edge: a resting bid fills when the price
is moving through it (measured 10.6c completion drift vs ~7c quoted margin),
and the legs left unpaired lost 37 of 37 against ~18 implied. This mode
quotes only BEFORE a game, around the de-vigged price of a sharp sportsbook
(Pinnacle first), so the bot rests where the sharp market says the price is,
not where the thin Polymarket book happens to sit, and leaves before the
in-play adverse selection starts.

THIS MODULE IS PURE apart from `OddsClient.fetch`, which GETs The Odds API
(https://api.the-odds-api.com/v4) with the key read from ODDS_API_KEY. The
key is never logged, never placed in a returned value, and errors are
reported by type/status only (an HTTP error string can carry the URL).
Nothing here places, cancels or authorizes an order.

THE RULES (defaults are the owner-agreed spec; every one is an env knob):
  fair        Pinnacle h2h, de-vigged multiplicatively; else the median of
              the de-vigged probabilities across the books returned. Odds
              older than SHARP_MAX_ODDS_AGE_S -> no fair -> no quote.
  window      game starts between now + SHARP_CUTOFF_S (5 min) and
              now + SHARP_MAX_LEAD_S (6 h).
  match       YES = the market's long side team; the sportsbook event must
              have exactly the same two team names and a commence time within
              SHARP_MATCH_TOL_S of gameStartTime. Anything else: no quote.
  quote       YES bid = fair - margin, NO bid = (1 - fair) - margin
              (pair <= 1 - 2*margin, ~$0.94). Post-only: never at/through
              the opposite touch.
  underpriced if a side's ASK is >= SHARP_EDGE below its fair, quote that
              side alone (a one-sided, +EV-vs-sharp entry), at no more than
              fair - SHARP_UNDERPRICED_MARGIN, and do NOT quote the other side
              (it is overpriced vs sharp).
  residual    a leg left unpaired is HELD only while its cost is at least
              SHARP_HOLD_EDGE below the side's current fair; otherwise it may
              be completed only at a pair cost <= SHARP_COMPLETE_MAX.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal as D
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

ONE = D(1)
ODDS_BASE = "https://api.the-odds-api.com/v4"
KEY_ENV = "ODDS_API_KEY"

#: Polymarket slug sport code -> The Odds API sport key (moneyline / h2h).
#: Tennis keys are per-tournament and change weekly, so they are not in v1.
SPORT_KEYS: Dict[str, str] = {
    "nba": "basketball_nba",
    "nhl": "icehockey_nhl",
    "nfl": "americanfootball_nfl",
    "cbb": "basketball_ncaab",
    "wcbb": "basketball_wncaab",
    "cfb": "americanfootball_ncaaf",
}


def _env_d(name: str, default: str) -> D:
    try:
        return D(str(os.getenv(name, default)).strip())
    except Exception:                                    # noqa: BLE001
        return D(default)


MARGIN = _env_d("PMUS_SHARP_MARGIN", "0.03")
EDGE = _env_d("PMUS_SHARP_EDGE", "0.025")
UNDERPRICED_MARGIN = _env_d("PMUS_SHARP_UNDERPRICED_MARGIN", "0.02")
HOLD_EDGE = _env_d("PMUS_SHARP_HOLD_EDGE", "0.01")
COMPLETE_MAX = _env_d("PMUS_SHARP_COMPLETE_MAX", "0.98")
CUTOFF_S = float(os.getenv("PMUS_SHARP_CUTOFF_S", "300"))
MAX_LEAD_S = float(os.getenv("PMUS_SHARP_MAX_LEAD_S", "21600"))
MATCH_TOL_S = float(os.getenv("PMUS_SHARP_MATCH_TOL_S", "1800"))
MAX_ODDS_AGE_S = float(os.getenv("PMUS_SHARP_MAX_ODDS_AGE_S", "900"))


# ------------------------------------------------------------ fair value
def devig(decimal_odds: Iterable[Any]) -> Optional[List[D]]:
    """Multiplicative de-vig: implied 1/o, normalised to sum 1. None on any
    unusable price (<= 1.0, missing, unparsable)."""
    try:
        inv = [ONE / D(str(o)) for o in decimal_odds]
    except Exception:                                    # noqa: BLE001
        return None
    if len(inv) < 2 or any(x <= 0 or x >= 1 for x in inv):
        return None
    s = sum(inv)
    return [x / s for x in inv]


def _ts(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")
                                      ).timestamp()
    except ValueError:
        return None


def _h2h(book: Mapping[str, Any]) -> Optional[Tuple[Dict[str, D], float]]:
    for mk in book.get("markets") or []:
        if not isinstance(mk, Mapping) or mk.get("key") != "h2h":
            continue
        outs = [o for o in mk.get("outcomes") or [] if isinstance(o, Mapping)]
        if len(outs) != 2:                     # draws (3-way) are not ours
            return None
        probs = devig(o.get("price") for o in outs)
        if probs is None:
            return None
        upd = _ts(mk.get("last_update") or book.get("last_update"))
        return ({norm(o.get("name")): p for o, p in zip(outs, probs)},
                upd or 0.0)
    return None


def fair_from_event(event: Mapping[str, Any], *, now: float,
                    prefer: str = "pinnacle") -> Optional[Dict[str, Any]]:
    """{team(normalised): prob, ...} plus its source and age, or None."""
    books = [b for b in event.get("bookmakers") or [] if isinstance(b, Mapping)]
    pref = [b for b in books if b.get("key") == prefer]
    for b in pref:
        got = _h2h(b)
        if got and 0 <= now - got[1] <= MAX_ODDS_AGE_S:
            return {"probs": got[0], "source": prefer,
                    "age_s": round(now - got[1], 1)}
    fresh = []
    for b in books:
        got = _h2h(b)
        if got and 0 <= now - got[1] <= MAX_ODDS_AGE_S:
            fresh.append(got)
    if not fresh:
        return None
    teams = set(fresh[0][0])
    if any(set(f[0]) != teams for f in fresh):
        return None
    probs = {t: D(str(median(f[0][t] for f in fresh))) for t in teams}
    s = sum(probs.values())
    return {"probs": {t: p / s for t, p in probs.items()},
            "source": "median_of_%d" % len(fresh),
            "age_s": round(now - min(f[1] for f in fresh), 1)}


# --------------------------------------------------------------- matching
def norm(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def sport_code(slug: str) -> str:
    parts = str(slug).split("-")
    return parts[1] if len(parts) > 1 else ""


def market_teams(market: Mapping[str, Any]) -> Optional[Tuple[str, str]]:
    """(YES team, NO team) by full name from the market's sides, or None."""
    yes = no = None
    for s in market.get("marketSides") or []:
        if not isinstance(s, Mapping):
            continue
        name = (s.get("team") or {}).get("name") if isinstance(
            s.get("team"), Mapping) else None
        if not name:
            return None
        if s.get("long") is True:
            yes = name
        elif s.get("long") is False:
            no = name
    if not yes or not no or norm(yes) == norm(no):
        return None
    return yes, no


def match_event(market: Mapping[str, Any], events: Iterable[Mapping[str, Any]]
                ) -> Optional[Mapping[str, Any]]:
    """The ONE sportsbook event with exactly these two teams and a start
    within MATCH_TOL_S. Zero or several candidates -> None."""
    teams = market_teams(market)
    start = _ts(market.get("gameStartTime"))
    if teams is None or start is None:
        return None
    want = {norm(teams[0]), norm(teams[1])}
    hits = []
    for ev in events:
        if not isinstance(ev, Mapping):
            continue
        got = {norm(ev.get("home_team")), norm(ev.get("away_team"))}
        t = _ts(ev.get("commence_time"))
        if got == want and t is not None and abs(t - start) <= MATCH_TOL_S:
            hits.append(ev)
    return hits[0] if len(hits) == 1 else None


def fair_yes(market: Mapping[str, Any], events: Iterable[Mapping[str, Any]],
             *, now: float) -> Optional[Dict[str, Any]]:
    ev = match_event(market, events)
    if ev is None:
        return None
    f = fair_from_event(ev, now=now)
    if f is None:
        return None
    yes, _no = market_teams(market)
    p = f["probs"].get(norm(yes))
    if p is None:
        return None
    return {"fair_yes": p, "source": f["source"], "age_s": f["age_s"],
            "event_id": ev.get("id")}


# ----------------------------------------------------------------- window
def in_window(game_start: Any, now: float) -> bool:
    t = _ts(game_start)
    return t is not None and now + CUTOFF_S <= t <= now + MAX_LEAD_S


# ------------------------------------------------------------------ quote
def _floor(x: D, tick: D) -> D:
    return (x / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def quote(fair: Any, yes_bid: Any, yes_ask: Any, tick: Any) -> Dict[str, Any]:
    """What to rest, given the sharp fair YES probability and the book.

    Returns {"mode": two_sided|yes_only|no_only|none, "YES": px|None,
    "NO": px|None, "pair": px|None, "why": ...}. All prices are maker bids
    strictly under the opposite touch (post-only)."""
    f, yb, ya, t = (D(str(fair)), D(str(yes_bid)), D(str(yes_ask)),
                    D(str(tick)))
    out: Dict[str, Any] = {"mode": "none", "YES": None, "NO": None,
                           "pair": None, "why": None}
    if not (D(0) < f < ONE) or ya <= yb:
        out["why"] = "bad_inputs"
        return out
    no_fair, no_ask = ONE - f, ONE - yb           # NO is bought at 1 - yes_bid
    yes_cheap = ya <= f - EDGE                    # YES underpriced vs sharp
    no_cheap = no_ask <= no_fair - EDGE           # NO underpriced vs sharp
    yes_px = _floor(f - MARGIN, t)
    no_px = _floor(no_fair - MARGIN, t)
    if yes_cheap and not no_cheap:
        yes_px = min(_floor(f - UNDERPRICED_MARGIN, t), ya - t)
        no_px = None
        out["mode"], out["why"] = "yes_only", "yes_underpriced_vs_sharp"
    elif no_cheap and not yes_cheap:
        no_px = min(_floor(no_fair - UNDERPRICED_MARGIN, t), no_ask - t)
        yes_px = None
        out["mode"], out["why"] = "no_only", "no_underpriced_vs_sharp"
    else:
        yes_px = min(yes_px, ya - t)
        no_px = min(no_px, no_ask - t)
        out["mode"] = "two_sided"
    if yes_px is not None and yes_px < t:
        yes_px = None
    if no_px is not None and no_px < t:
        no_px = None
    if yes_px is None and no_px is None:
        out["mode"], out["why"] = "none", out["why"] or "no_legal_price"
    elif out["mode"] == "two_sided" and (yes_px is None or no_px is None):
        out["mode"] = "yes_only" if yes_px is not None else "no_only"
    out["YES"], out["NO"] = yes_px, no_px
    if yes_px is not None and no_px is not None:
        out["pair"] = yes_px + no_px
    return out


# --------------------------------------------------------------- residual
def residual(lead_px: Any, lead_fair: Any, comp_px: Any) -> Dict[str, Any]:
    """HOLD a stranded leg only while it is still +EV against the sharp fair
    by HOLD_EDGE; otherwise COMPLETE only if the pair <= COMPLETE_MAX; else
    WAIT (rest the completion no higher than COMPLETE_MAX - lead)."""
    lp, lf, cp = D(str(lead_px)), D(str(lead_fair)), D(str(comp_px))
    if lp <= lf - HOLD_EDGE:
        return {"action": "HOLD", "why": "leg_still_plus_ev_vs_sharp",
                "edge": str(lf - lp)}
    if lp + cp <= COMPLETE_MAX:
        return {"action": "COMPLETE", "pair": str(lp + cp)}
    return {"action": "WAIT", "max_comp_px": str(COMPLETE_MAX - lp),
            "why": "pair_would_exceed_cap"}


# ------------------------------------------------------------ odds client
class OddsClient:
    """The Odds API, budget-aware for the free tier. One call per sport per
    poll interval (h2h, eu region, decimal). Stops calling when the remaining
    request count reported by the API falls to `reserve`."""

    def __init__(self, *, key: Optional[str] = None,
                 poll_s: Optional[float] = None, reserve: int = 25,
                 opener=None, clock=time.time):
        self._key = key if key is not None else os.getenv(KEY_ENV, "")
        self.poll_s = float(poll_s if poll_s is not None else
                            os.getenv("PMUS_SHARP_POLL_S", "3600"))
        self.reserve = int(os.getenv("PMUS_SHARP_RESERVE", str(reserve)))
        self.opener = opener or urllib.request.urlopen
        self.clock = clock
        self.cache: Dict[str, Tuple[float, List[Mapping[str, Any]]]] = {}
        self.remaining: Optional[int] = None
        self.used: Optional[int] = None
        self.last_error: Optional[str] = None
        self.calls = 0

    @property
    def configured(self) -> bool:
        return bool((self._key or "").strip())

    def fetch(self, sport_key: str) -> List[Mapping[str, Any]]:
        now = self.clock()
        hit = self.cache.get(sport_key)
        # polling is unchanged until provider coverage and quota are
        # reported (issue #251); stale odds are still refused downstream
        if hit and now - hit[0] < self.poll_s:
            return hit[1]
        if not self.configured:
            self.last_error = "NO_KEY"
            return hit[1] if hit else []
        if self.remaining is not None and self.remaining <= self.reserve:
            self.last_error = "BUDGET_RESERVE"
            return hit[1] if hit else []
        q = urllib.parse.urlencode({
            "apiKey": self._key.strip(), "regions": "eu", "markets": "h2h",
            "oddsFormat": "decimal", "dateFormat": "iso"})
        url = "%s/sports/%s/odds?%s" % (ODDS_BASE, sport_key, q)
        try:
            with self.opener(url, timeout=10) as r:            # noqa: S310
                body = json.loads(r.read().decode("utf-8"))
                hdr = getattr(r, "headers", {}) or {}
                rem, used = (hdr.get("x-requests-remaining"),
                             hdr.get("x-requests-used"))
            self.calls += 1
            self.remaining = int(rem) if rem not in (None, "") else None
            self.used = int(used) if used not in (None, "") else None
            self.last_error = None
            evs = body if isinstance(body, list) else []
            self.cache[sport_key] = (now, evs)
            return evs
        except urllib.error.HTTPError as exc:
            # NEVER str(exc): an HTTPError can carry the URL, i.e. the key
            self.last_error = "HTTP_%s" % exc.code
        except Exception as exc:                         # noqa: BLE001
            self.last_error = type(exc).__name__
        self.cache[sport_key] = (now, hit[1] if hit else [])
        return hit[1] if hit else []

    def coverage(self) -> Dict[str, Any]:
        """READ-ONLY provider coverage (28 Nov, issue #251): the provider's
        own sport list (GET /sports, the endpoint that lists keys; its quota
        cost is MEASURED here from the usage headers before and after, not
        assumed), with every key that could serve our allowlist (tennis,
        table tennis) and the configured SPORT_KEYS that are and are not
        offered. Never raises; the key is never logged."""
        out: Dict[str, Any] = {"ok": False, "label": "provider coverage; "
                               "read-only; no odds fetched"}
        if not self.configured:
            out["why"] = "NO_KEY"
            return out
        used_before = self.used
        q = urllib.parse.urlencode({"apiKey": self._key.strip()})
        try:
            with self.opener("%s/sports?%s" % (ODDS_BASE, q),
                             timeout=10) as r:                # noqa: S310
                body = json.loads(r.read().decode("utf-8"))
                hdr = getattr(r, "headers", {}) or {}
                rem, used = (hdr.get("x-requests-remaining"),
                             hdr.get("x-requests-used"))
        except urllib.error.HTTPError as exc:
            out["why"] = "HTTP_%s" % exc.code
            return out
        except Exception as exc:                         # noqa: BLE001
            out["why"] = type(exc).__name__
            return out
        rows = [b for b in (body if isinstance(body, list) else [])
                if isinstance(b, Mapping)]
        keys = {str(b.get("key") or ""): b for b in rows}

        def pick(word: str) -> list:
            return sorted(k for k, b in keys.items()
                          if word in k.lower() or word in str(
                              b.get("group") or "").lower())
        used_after = int(used) if used not in (None, "") else None
        out.update(ok=True, sports=len(keys),
                   tennis=pick("tennis"),
                   table_tennis=pick("table") + [k for k in pick("tt_")
                                                 if k not in pick("table")],
                   configured_offered=sorted(v for v in SPORT_KEYS.values()
                                             if v in keys),
                   configured_missing=sorted(v for v in SPORT_KEYS.values()
                                             if v not in keys),
                   requests_remaining=int(rem) if rem not in (None, "")
                   else None,
                   requests_used=used_after,
                   list_cost=(used_after - used_before) if (
                       used_after is not None and used_before is not None)
                   else None)
        return out

    def status(self) -> Dict[str, Any]:
        return {"configured": self.configured, "calls": self.calls,
                "remaining": self.remaining, "used": self.used,
                "last_error": self.last_error, "poll_s": self.poll_s,
                "reserve": self.reserve}
