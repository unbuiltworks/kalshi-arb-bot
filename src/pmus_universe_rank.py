"""READ-ONLY ranked sports universe on the public gateway, scored with the
campaign's own pair_score and frozen L5 model view, and diffed against the
probe's candidate pool.

OWNER (chat, 2026-09-07 ~11:00Z): "keep the full sports universe ranked so
market #2 starts immediately after settlement." Observed 09:46-10:56Z: the
campaign scanned every ~20 s and admitted nothing -- 73 of the 80 rows in
the recorder's status document were yesterday's RESOLVED markets and 7
were thin-flow live ones. Whether that is "no suitable market exists" or
"the pool does not contain the suitable markets" is exactly what this
module answers, without touching the probe or the recorder.

WHAT IT DOES. Keyless GETs only: the venue's live sports events (the same
bounded read pmus_event_progress uses, so game position and feeCoefficient
come with it), the pregame sports slate, and one BBO read per market,
paced. Each market becomes a candidate row in the spring_watch schema the
ranker already consumes, is given the frozen model's view of its OPEN
state (_model_view), and is scored by pair_score with the venue's game
position. Rows are ordered by the campaign's own key.

WHAT IT CANNOT DO, said plainly: the recorder's stream evidence
(two-sided prints in the hour, trades_1h, collector cadence) is not
available on the public gateway, so those components score 0 here and the
strict-evidence admission gate (OWNER 5533476555) is NOT evaluated. This
is a PRE-RANK of where recorder coverage should be pointed, never an
admission list, and it changes nothing live.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, List, Mapping, Optional, Sequence

from src import pmus_event_progress as prog

OUT_PATH = os.path.join("analysis", "l5", "pmus_universe_rank.json")
PREGAME_PATH = "/v1/events?active=true&closed=false&categories=sports"
PAGE = 500
MAX_PAGES = 4
BBO_PAUSE_S = float(os.getenv("PMUS_UNIVERSE_BBO_PAUSE_S", "0.15"))
MAX_BBO = int(os.getenv("PMUS_UNIVERSE_MAX_BBO", "400"))
PREGAME_HORIZON_H = float(os.getenv("PMUS_UNIVERSE_PREGAME_H", "6"))
PREFIX = "PMUS_L5_CAMPAIGN "


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def fetch_pregame(get_json=None, now: Optional[float] = None,
                  horizon_h: float = PREGAME_HORIZON_H) -> tuple:
    """Sports events not yet live whose start is within horizon_h."""
    get_json = prog.http_get_json if get_json is None else get_json
    now = time.time() if now is None else now
    rows: list = []
    pages, err = 0, None
    for page in range(MAX_PAGES):
        try:
            resp = get_json(f"{PREGAME_PATH}&limit={PAGE}&offset={page * PAGE}")
        except Exception as exc:                       # noqa: BLE001
            err = f"{type(exc).__name__}:{str(exc)[:100]}"
            break
        evs = resp if isinstance(resp, list) else (
            resp.get("events") if isinstance(resp, Mapping) else None)
        if not isinstance(evs, list):
            err = "unexpected_response_shape"
            break
        pages += 1
        for e in evs:
            if not isinstance(e, Mapping) or e.get("live"):
                continue
            start = e.get("startTime") or e.get("startDate")
            try:
                from datetime import datetime
                t = datetime.fromisoformat(str(start).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                continue
            if 0 <= (t - now) <= horizon_h * 3600:
                rows.append(e)
        if len(evs) < PAGE:
            break
    return rows, {"pregame_events": len(rows), "pages": pages, "error": err}


def market_rows(events: Sequence[Mapping[str, Any]], live: bool) -> List[dict]:
    out = []
    for e in events:
        for m in (e.get("markets") or []):
            if not isinstance(m, Mapping) or not m.get("slug"):
                continue
            st = str(m.get("status") or "")
            if st and "OPEN" not in st.upper():
                continue
            out.append({"slug": str(m["slug"]), "event_slug": e.get("slug"),
                        "live": live, "status": st or None,
                        "endDate": m.get("endDate"),
                        "game_start": e.get("startTime") or e.get("startDate"),
                        "fee_coefficient": m.get("feeCoefficient"),
                        "sport": (e.get("eventState") or {}).get("type")})
    return out


def read_bbo(slug: str, get_json=None) -> Optional[dict]:
    get_json = prog.http_get_json if get_json is None else get_json
    try:
        md = (get_json(f"/v1/markets/{slug}/bbo") or {}).get("marketData") or {}
    except Exception:                                  # noqa: BLE001
        return None
    bid = (md.get("bestBid") or {}).get("value")
    ask = (md.get("bestAsk") or {}).get("value")
    return {"bid": bid, "ask": ask, "bid_depth": md.get("bidDepth"),
            "ask_depth": md.get("askDepth"), "shares_traded": md.get("sharesTraded"),
            "state": md.get("state")}


def candidate_row(m: Mapping[str, Any], bbo: Mapping[str, Any]) -> dict:
    """The spring_watch-shaped row pair_score / _model_view consume. Recorder
    stream features are UNKNOWN here and score zero: labelled, not faked."""
    return {
        "slug": m["slug"], "bid": bbo.get("bid"), "ask": bbo.get("ask"),
        "bid_touch_qty": bbo.get("bid_depth"), "ask_qty": bbo.get("ask_depth"),
        "trades_1h": None, "two_sided_trades": None, "collector_gaps": None,
        "market_state": {"status": m.get("status"), "endDate": m.get("endDate")},
        "recorder_features": "unavailable on the public gateway (scored 0)",
    }


def rank(now: Optional[float] = None, get_json=None, sleep=time.sleep,
         max_bbo: int = MAX_BBO, model=None) -> dict:
    from src import pmus_l5_continuous_session as cont
    from src import pmus_l5_persistence_v2 as pv2
    now = time.time() if now is None else now
    model = model if model is not None else pv2.load_v2()
    live_events, live_diag = prog.fetch_live_events(get_json)
    pre_events, pre_diag = fetch_pregame(get_json, now)
    progress = prog.progress_table(live_events, now)
    universe = market_rows(live_events, True) + market_rows(pre_events, False)
    seen, ordered = set(), []
    for m in universe:
        if m["slug"] not in seen:
            seen.add(m["slug"])
            ordered.append(m)
    scored, skipped = [], {"no_bbo": 0, "one_sided": 0, "bbo_cap": 0}
    for i, m in enumerate(ordered):
        if i >= max_bbo:
            skipped["bbo_cap"] += 1
            continue
        bbo = read_bbo(m["slug"], get_json)
        if sleep and BBO_PAUSE_S > 0:
            sleep(BBO_PAUSE_S)
        if bbo is None:
            skipped["no_bbo"] += 1
            continue
        if not bbo.get("bid") or not bbo.get("ask"):
            skipped["one_sided"] += 1
            continue
        row = candidate_row(m, bbo)
        mv = cont._model_view(model, row.get("bid"))
        ps = cont.pair_score(row, mv, now, progress.get(m["slug"]))
        scored.append({"slug": m["slug"], "live": m["live"], "sport": m.get("sport"),
                       "bid": bbo["bid"], "ask": bbo["ask"],
                       "bid_touch_qty": bbo.get("bid_depth"), "ask_qty": bbo.get("ask_depth"),
                       "shares_traded": bbo.get("shares_traded"),
                       "fee_coefficient": m.get("fee_coefficient"),
                       "game_start": m.get("game_start"), **mv, **ps})

    def _touch(x):
        return _f(x.get("bid_touch_qty"))
    scored.sort(key=lambda x: (x["off_distribution"], -x["pair_score"], -x["state_n"],
                               -_f(x.get("p_buy")), -_touch(x), x["slug"]))
    for i, x in enumerate(scored, 1):
        x["rank"] = i
    return {
        "t_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "diag": {"live": live_diag, "pregame": pre_diag,
                 "universe_markets": len(ordered), "scored": len(scored),
                 "skipped": skipped, "bbo_pause_s": BBO_PAUSE_S},
        "in_distribution": sum(1 for x in scored if not x["off_distribution"]),
        "two_sided_in_band_0_35_0_65": sum(
            1 for x in scored if 0.35 <= _f(x["bid"]) <= 0.65),
        "ranked": scored,
        "labels": ("public-gateway PRE-RANK with the campaign's pair_score and "
                   "frozen model view; recorder stream features unavailable and "
                   "scored 0; the strict-evidence admission gate is NOT evaluated; "
                   "nothing here is admitted or traded"),
    }


def pool_from_logs(paths: Sequence[str]) -> dict:
    """The probe's most recent candidate pool: every slug in the latest
    selection event (admitted + refused) of the given Railway log dumps."""
    latest = None
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for e in d.get("deploy") or []:
            m = e.get("message") or ""
            if not m.startswith(PREFIX):
                continue
            try:
                j = json.loads(m[len(PREFIX):])
            except json.JSONDecodeError:
                continue
            if j.get("kind") != "selection":
                continue
            ts = e.get("timestamp") or ""
            if latest is None or ts > latest["t"]:
                latest = {"t": ts, "admitted": [a.get("slug") for a in (j.get("admitted") or [])],
                          "refused": {r.get("slug"): r.get("reason")
                                      for r in (j.get("refused") or [])}}
    return latest or {"t": None, "admitted": [], "refused": {}}


def diff(ranked: Mapping[str, Any], pool: Mapping[str, Any], top: int = 25) -> dict:
    pool_slugs = set(pool.get("admitted") or []) | set((pool.get("refused") or {}).keys())
    top_rows = [x for x in ranked["ranked"] if not x["off_distribution"]][:top]
    missing = [x for x in top_rows if x["slug"] not in pool_slugs]
    present = [x for x in top_rows if x["slug"] in pool_slugs]
    from collections import Counter
    reasons = Counter(str(v).split(":")[0] for v in (pool.get("refused") or {}).values())
    return {
        "pool_t": pool.get("t"), "pool_size": len(pool_slugs),
        "pool_refusal_reasons": dict(reasons.most_common()),
        "top_in_distribution_considered": len(top_rows),
        "top_present_in_pool": [{"rank": x["rank"], "slug": x["slug"],
                                 "pool_reason": (pool.get("refused") or {}).get(x["slug"], "admitted")}
                                for x in present],
        "top_missing_from_pool": [{"rank": x["rank"], "slug": x["slug"], "live": x["live"],
                                   "bid": x["bid"], "ask": x["ask"], "pair_score": x["pair_score"],
                                   "state": x.get("state"), "state_n": x.get("state_n"),
                                   "game_period": x.get("game_period")}
                                  for x in missing],
        "reading": ("'missing' = a market the campaign's own scorer ranks near the "
                    "top on public features that the recorder is not serving in its "
                    "status document, so it can never be admitted; a pool made of "
                    "resolved markets is a coverage gap, not an absence of markets"),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--pool-from-logs", nargs="*", default=[])
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--max-bbo", type=int, default=MAX_BBO)
    a = ap.parse_args(argv)
    r = rank(max_bbo=a.max_bbo)
    if a.pool_from_logs:
        paths = []
        for p in a.pool_from_logs:
            paths.extend(os.path.join(p, n) for n in sorted(os.listdir(p))) if os.path.isdir(p) else paths.append(p)
        r["pool_diff"] = diff(r, pool_from_logs(paths), a.top)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(r, f, indent=1, sort_keys=True, default=str)
    head = [{k: x.get(k) for k in ("rank", "slug", "live", "bid", "ask", "pair_score",
                                   "state", "state_n", "game_period", "rebate_eligible")}
            for x in r["ranked"][:15]]
    print(json.dumps({"diag": r["diag"], "in_distribution": r["in_distribution"],
                      "top": head, "pool_diff": {k: v for k, v in (r.get("pool_diff") or {}).items()
                                                 if k != "top_present_in_pool"}},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
