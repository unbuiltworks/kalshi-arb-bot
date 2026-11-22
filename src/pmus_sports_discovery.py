"""Sport-agnostic live discovery funnel for the sports bot. READ-ONLY.

Owner 2026-11-22: "Discover every live sporting event on Polymarket US,
regardless of sport. Classify exclusions ... Expand support selectively ...
Keep the economic safeguards ... Rank eligible markets by expected net
economics." And: "No cricket!"

This module places, cancels and authorizes nothing and holds no credential.
It reads the venue's public live-events list, each match-winner book, and
the international book through the SAME resolver the live guard uses
(src/pmus_sports_xv.resolve_token), then says, for every market, the first
reason the trading engine would not open there -- using the engine's own
thresholds (price band, spread in ticks, pair cap, game-phase rule) so the
funnel cannot drift from the bot.

It does NOT change what the bot trades: the production allowlist
(PMUS_TWO_SIDED_OPEN_GATE_ALLOW_SPORTS) is only read here.

Run:  python3 -m src.pmus_sports_discovery [out.json]
"""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal as D
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from src import pmus_rn1_replica as RN1
from src import pmus_sports_two_sided as M
from src import pmus_sports_xv as XV

LABEL = "READ-ONLY DISCOVERY; modelled economics, never P&L"

#: slug prefix -> market structure. Only the two-outcome match winner is a
#: structure the RN1 controller and the international guard both handle.
STRUCTURES = {"aec": "match_winner", "asc": "spread", "tsc": "total",
              "atc": "three_way", "astatc": "stat"}
#: eventState.type values the engine has a late-phase rule for (is_late).
#: A sport outside this set would be quoted to the final whistle.
PHASE_RULE_TYPES = frozenset({"football", "basketball", "college-basketball",
                              "hockey", "soccer", "tennis", "table-tennis",
                              "ufc"})
#: PROPOSED, not an engine rule: fewer contracts than this at either touch
#: means a 2-contract clip is a visible share of the book.
MIN_TOP_QTY = D(os.getenv("PMUS_DISCOVERY_MIN_TOP_QTY", "10"))

REASONS = ("cricket_excluded", "incompatible_market_structure",
           "no_two_sided_book", "lopsided_price", "wide_spread",
           "insufficient_depth", "final_phase", "no_phase_rule",
           "no_international_match", "international_book_one_sided",
           "pair_cost_above_cap", "sport_not_on_allowlist")


def structure(slug: str) -> str:
    return STRUCTURES.get(str(slug).split("-", 1)[0], "other")


def sport_of(slug: str) -> str:
    return M.sport_of(slug)


def classify(mk: Mapping[str, Any], allow: Iterable[str]) -> Dict[str, Any]:
    """Pure. `mk`: slug, tick, bid/ask (px, qty) or None, state (eventState),
    intl_token (bool), ibid/iask (px, qty) or None. Returns every failing
    reason in engine order, the first one as `primary`, and -- when the only
    thing between the market and trading is the allowlist or nothing --
    the modelled opening economics."""
    slug = str(mk["slug"])
    sport = sport_of(slug)
    why: List[str] = []
    st = dict(mk.get("state") or {})
    typ = str(st.get("type") or "").lower()
    if M.is_cricket(slug) or typ == "cricket":
        why.append("cricket_excluded")
    if structure(slug) != "match_winner":
        why.append("incompatible_market_structure")
    tick = D(str(mk.get("tick") or "0.01"))
    bid, ask = mk.get("bid"), mk.get("ask")
    two = bool(bid and ask and D(str(ask[0])) > D(str(bid[0])) > 0)
    out: Dict[str, Any] = {"slug": slug, "sport": sport, "type": typ or None,
                           "period": st.get("period"),
                           "structure": structure(slug)}
    if not two:
        why.append("no_two_sided_book")
    else:
        yb, ya = D(str(bid[0])), D(str(ask[0]))
        out.update(bid=str(yb), ask=str(ya), spread=str(ya - yb),
                   top_qty=[str(bid[1]), str(ask[1])], tick=str(tick))
        if not (M.BAND_LO <= yb <= M.BAND_HI):
            why.append("lopsided_price")
        if (ya - yb) > M.MAX_SPREAD_TICKS * tick:
            why.append("wide_spread")
        if min(D(str(bid[1])), D(str(ask[1]))) < MIN_TOP_QTY:
            why.append("insufficient_depth")
    try:
        late = M.is_late(st)
    except Exception:                                     # noqa: BLE001
        late = "unreadable_state"
    if late:
        why.append("final_phase")
        out["late"] = late
    if typ not in PHASE_RULE_TYPES:
        why.append("no_phase_rule")
    ib, ia = mk.get("ibid"), mk.get("iask")
    if not mk.get("intl_token"):
        why.append("no_international_match")
    elif not (ib and ia and D(str(ia[0])) > D(str(ib[0])) > 0):
        why.append("international_book_one_sided")
    else:
        imid = (D(str(ib[0])) + D(str(ia[0]))) / 2
        out.update(intl_bid=str(ib[0]), intl_ask=str(ia[0]),
                   intl_mid=str(imid))
    if two:
        q = RN1.quotes(yes_bid=bid[0], yes_ask=ask[0], tick=tick,
                       pos={"YES": 0, "NO": 0}, cost={"YES": 0, "NO": 0})
        y, n = q.get("YES"), q.get("NO")
        if y and n:
            pair = D(str(y["px"])) + D(str(n["px"]))
            out.update(open_yes=str(y["px"]), open_no=str(n["px"]),
                       modelled_pair_cost=str(pair),
                       modelled_edge_per_pair=str(1 - pair))
            if pair > RN1.PAIR_CAP:
                why.append("pair_cost_above_cap")
            if "intl_mid" in out:
                # the guard's own test: a bid is unsafe above fair - margin
                imid = D(out["intl_mid"])
                out["guard_ok"] = {
                    "YES": D(str(y["px"])) <= imid - tick,
                    "NO": D(str(n["px"])) <= (1 - imid) - tick}
                out["us_minus_intl_mid"] = str((D(str(bid[0])) +
                                                D(str(ask[0]))) / 2 - imid)
        else:
            why.append("pair_cost_above_cap")
            out["rn1_why"] = {s: q.get("why_" + s) for s in ("YES", "NO")}
    if sport not in set(allow):
        why.append("sport_not_on_allowlist")
    why = [r for r in REASONS if r in why]               # engine order
    out["reasons"] = why
    out["primary"] = why[0] if why else None
    out["eligible"] = not why
    out["eligible_but_for_allowlist"] = why == ["sport_not_on_allowlist"]
    return out


def score(c: Mapping[str, Any]) -> Optional[D]:
    """MODELLED net economics of opening here, per completed pair, for
    ranking only: the edge if both resting bids fill (maker fee 0), minus
    how far the US mid sits from the international mid beyond one tick
    (a stale US quote is the pick-off the guard exists for). Markets where
    the guard would refuse a side today score that side's edge as zero."""
    if "modelled_edge_per_pair" not in c:
        return None
    edge = D(c["modelled_edge_per_pair"])
    gap = abs(D(c.get("us_minus_intl_mid") or "0"))
    pen = max(D(0), gap - D(c.get("tick") or "0.01"))
    g = c.get("guard_ok") or {}
    if g and not (g.get("YES") and g.get("NO")):
        edge = edge / 2
    return edge - pen


def summarise(rows: List[Mapping[str, Any]], allow: Iterable[str]
              ) -> Dict[str, Any]:
    allow = set(allow)
    prim: Dict[str, int] = {}
    sports: Dict[str, Dict[str, Any]] = {}
    for c in rows:
        prim[c["primary"] or "eligible"] = prim.get(
            c["primary"] or "eligible", 0) + 1
        if c["structure"] != "match_winner":
            continue
        s = sports.setdefault(c["sport"], {
            "markets": 0, "type": c.get("type"), "intl_matched": 0,
            "pass_all_but_allowlist": 0, "eligible": 0,
            "on_allowlist": c["sport"] in allow,
            "phase_rule": (c.get("type") or "") in PHASE_RULE_TYPES,
            "reasons": {}})
        s["markets"] += 1
        r = c["reasons"]
        if "no_international_match" not in r and \
                "international_book_one_sided" not in r:
            s["intl_matched"] += 1
        s["pass_all_but_allowlist"] += int(
            c["eligible"] or c["eligible_but_for_allowlist"])
        s["eligible"] += int(c["eligible"])
        for x in r:
            s["reasons"][x] = s["reasons"].get(x, 0) + 1
    for name, s in sports.items():
        if s["reasons"].get("cricket_excluded"):
            v = "excluded_by_owner"
        elif s["on_allowlist"]:
            v = "supported"
        elif not s["phase_rule"]:
            v = "blocked_no_phase_rule"
        elif not s["intl_matched"]:
            v = "blocked_no_international_listing"
        elif s["pass_all_but_allowlist"]:
            v = "candidate"
        else:
            v = "mapped_but_nothing_tradable_now"
        s["verdict"] = v
    ranked = sorted((c for c in rows
                     if c["eligible"] or c["eligible_but_for_allowlist"]),
                    key=lambda c: (score(c) is None, -(score(c) or D(0))))
    return {"markets": len(rows), "primary_reason": prim, "sports": sports,
            "ranked": [{"slug": c["slug"], "sport": c["sport"],
                        "on_allowlist": c["eligible"],
                        "score": str(score(c)),
                        "modelled_edge_per_pair":
                            c.get("modelled_edge_per_pair"),
                        "bid": c.get("bid"), "ask": c.get("ask"),
                        "intl_mid": c.get("intl_mid"),
                        "top_qty": c.get("top_qty"),
                        "period": c.get("period")} for c in ranked],
            "label": LABEL}


# ------------------------------------------------------------------ I/O
def _levels(rows: Any) -> list:
    out = []
    for r in rows or []:
        try:
            out.append((D(str(r["px"]["value"])), D(str(r["qty"]))))
        except Exception:                                 # noqa: BLE001
            continue
    return out


def snapshot_market(pm_get: Callable, gamma_get: Callable, clob_get: Callable,
                    slug: str, state: Mapping[str, Any]) -> Dict[str, Any]:
    mk: Dict[str, Any] = {"slug": slug, "state": dict(state or {})}
    if structure(slug) != "match_winner":
        return mk
    try:
        md = (pm_get(f"/v1/markets/{slug}/book") or {}).get("marketData") or {}
        b = sorted(_levels(md.get("bids")), reverse=True)
        a = sorted(_levels(md.get("offers")))
        mk["bid"], mk["ask"] = (b[0] if b else None), (a[0] if a else None)
    except Exception as exc:                              # noqa: BLE001
        mk["book_error"] = type(exc).__name__
    try:
        m = (pm_get(f"/v1/market/slug/{slug}") or {}).get("market") or {}
        mk["tick"] = str(m.get("orderPriceMinTickSize") or "0.01")
        po = m.get("outcomes")
        if isinstance(po, str):
            po = json.loads(po)
        mk["outcomes"] = po
        tok = XV.resolve_token(gamma_get, slug, po, XV.yes_names(m))
        mk["intl_token"] = bool(tok)
        if tok:
            bk = clob_get(f"/book?token_id={tok}") or {}
            ib = max(((D(str(x["price"])), D(str(x["size"])))
                      for x in bk.get("bids") or []), default=None)
            ia = min(((D(str(x["price"])), D(str(x["size"])))
                      for x in bk.get("asks") or []), default=None)
            mk["ibid"], mk["iask"] = ib, ia
    except Exception as exc:                              # noqa: BLE001
        mk["intl_error"] = type(exc).__name__
    return mk


def discover(pm_get: Callable, gamma_get: Callable, clob_get: Callable,
             allow: Iterable[str], workers: int = 5) -> Dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor
    from src import pmus_spring_gateway_candidates as GW
    resp = pm_get(GW.LIVE_EVENTS_PATH + "&limit=500&offset=0")
    evs = resp if isinstance(resp, list) else (resp or {}).get("events") or []
    live = [e for e in evs if isinstance(e, Mapping)
            and e.get("live") is True and not e.get("ended")]
    items, no_winner = [], []
    for e in live:
        slugs = [str(m.get("slug") or "") for m in e.get("markets") or []
                 if isinstance(m, Mapping) and m.get("slug")]
        if not any(structure(s) == "match_winner" for s in slugs):
            no_winner.append(str(e.get("slug")))
        for s in slugs:
            items.append((s, e.get("eventState") or {}))
    with ThreadPoolExecutor(workers) as ex:
        snaps = list(ex.map(lambda it: snapshot_market(
            pm_get, gamma_get, clob_get, it[0], it[1]), items))
    rows = [classify(mk, allow) for mk in snaps]
    return {"t": time.time(), "live_events": len(live),
            "events_without_match_winner": no_winner,
            "allowlist": sorted(set(allow)),
            "thresholds": {"band": [str(M.BAND_LO), str(M.BAND_HI)],
                           "max_spread_ticks": M.MAX_SPREAD_TICKS,
                           "pair_cap": str(RN1.PAIR_CAP),
                           "min_top_qty_proposed": str(MIN_TOP_QTY)},
            "summary": summarise(rows, allow), "rows": rows, "label": LABEL}


def _http(base: str) -> Callable:
    import urllib.request

    def get(path: str, timeout: float = 15.0) -> Any:
        req = urllib.request.Request(base + path, headers={
            "User-Agent": "research-client", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return json.loads(r.read())
    return get


def main(argv: List[str]) -> int:
    allow = sorted(M.OPEN_GATE_ALLOW_SPORTS) or \
        ["cfb", "cs2", "nba", "nfl", "wta"]
    rep = discover(_http("https://gateway.polymarket.us"), XV.gamma_get,
                   _http("https://clob.polymarket.com"), allow)
    if len(argv) > 1:
        with open(argv[1], "w") as f:
            json.dump(rep, f, default=str)
    s = rep["summary"]
    print(json.dumps({"t": rep["t"], "live_events": rep["live_events"],
                      "markets": s["markets"],
                      "primary_reason": s["primary_reason"],
                      "ranked": s["ranked"][:12]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
