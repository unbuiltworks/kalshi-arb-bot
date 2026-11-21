"""Which live markets are tradeable RIGHT NOW -- one command, read only.

The collector rotates a bounded budget across thousands of markets, so
"what does the slate look like this minute" is a question its tape can
only answer for what it happened to poll. This asks the venue directly,
for one market per live event, and reports the two things that decide
whether an L5 probe is possible: is the price inside the execution band,
and is there a two-sided book with depth behind it.

    python3 -m src.pmus_live_slate_census
    python3 -m src.pmus_live_slate_census --all-markets   # not just the
                                                          # event centre

READ ONLY: GET requests only, no credentials, no order path, no writes.
Everything it prints is a snapshot -- band membership moves within a
game, so a market listed here is a candidate to OBSERVE, never a
decision to trade. The execution gates still run downstream.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Mapping, Optional

from src import pmus_sports_collector as col
from src import pmus_sports_live_collector as live

BAND_LO, BAND_HI = live.EXEC_BAND_LO, live.EXEC_BAND_HI


def _quote(bbo: Mapping[str, Any], key: str) -> Optional[float]:
    v = bbo.get(key)
    v = v.get("value") if isinstance(v, Mapping) else v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def census(slugs, meta: Mapping[str, Any], get=None) -> list:
    """Snapshot each slug. A market that cannot be read is reported as
    unreadable rather than silently dropped -- a missing row would read
    as 'not a candidate', which is a different claim."""
    get = col.http_get_json if get is None else get
    out = []
    for slug in slugs:
        row: dict = {"slug": slug,
                     "event_slug": (meta.get(slug) or {}).get("event_slug"),
                     "league": (meta.get(slug) or {}).get("league")}
        try:
            bbo = get(f"/v1/markets/{slug}/bbo")["marketData"]
            book = get(f"/v1/markets/{slug}/book")["marketData"]
        except Exception as exc:
            out.append({**row, "readable": False,
                        "error": type(exc).__name__})
            continue
        bid, ask = _quote(bbo, "bestBid"), _quote(bbo, "bestAsk")
        bids, offers = book.get("bids") or [], book.get("offers") or []
        two_sided = bid is not None and ask is not None and ask >= bid
        row.update({
            "readable": True,
            "bid": bid, "ask": ask,
            "spread": None if not two_sided else round(ask - bid, 4),
            "bid_touch_qty": bids[0].get("qty") if bids else None,
            "offer_touch_qty": offers[0].get("qty") if offers else None,
            "shares_traded": bbo.get("sharesTraded"),
            "state": bbo.get("state"),
            "two_sided": two_sided,
            "in_band": bool(bid is not None and BAND_LO <= bid <= BAND_HI),
        })
        # A maker pair costs bid + (1 - ask), which equals 1 - spread.
        # Stated because it is the single number that decides whether a
        # completion can ever clear the >=1c bound on this book.
        row["maker_pair_cost"] = (None if not two_sided
                                  else round(bid + (1.0 - ask), 4))
        row["pair_clears_hard_bound"] = (
            row["maker_pair_cost"] is not None
            and row["maker_pair_cost"] <= 0.99)
        out.append(row)
    return out


def candidates(rows: list) -> list:
    ok = [r for r in rows if r.get("readable") and r.get("in_band")
          and r.get("two_sided")]
    ok.sort(key=lambda r: -(_f(r.get("bid_touch_qty"))))
    return ok


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def run(all_markets: bool = False) -> dict:
    events, diag = col.fetch_live_events(col.http_get_json)
    targets = live.live_targets(events)
    meta = {t["slug"]: t for t in targets}
    if all_markets:
        slugs = sorted(meta)
    else:
        # one market per event: the tightest quoted spread, the only
        # liquidity signal the venue serves at discovery
        by_event: dict = {}
        for t in targets:
            by_event.setdefault(str(t.get("event_slug")), []).append(t)
        slugs = sorted({live.tightest_spread_market(v)["slug"]
                        for v in by_event.values()
                        if live.tightest_spread_market(v)})
    rows = census(slugs, meta)
    cands = candidates(rows)
    return {
        "kind": "PMUS_LIVE_SLATE_CENSUS",
        "live_events": len(events),
        "live_markets": len(targets),
        "markets_polled": len(rows),
        "band": [BAND_LO, BAND_HI],
        "in_band_two_sided": len(cands),
        "candidates": cands,
        "unreadable": [r["slug"] for r in rows if not r.get("readable")],
        "labels": ("READ-ONLY snapshot; band membership moves within a "
                   "game, so these are candidates to OBSERVE, never a "
                   "decision to trade; execution gates run downstream"),
    }


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all-markets", action="store_true")
    args = ap.parse_args(argv)
    print(json.dumps(run(args.all_markets), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
