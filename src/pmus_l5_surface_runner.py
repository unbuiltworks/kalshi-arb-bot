"""Run the PMUS sports surface against live markets.

TWO TAPE SOURCES, AND THE DIFFERENCE IS THE WHOLE POINT.

  authenticated   the WS recorder's per-market state, which carries a REAL
                  trade tape. This is the only source that can reach
                  TRADE_FLOW and therefore EVIDENCED_FILL.
  public          the gateway book only. Reaches BOOK_REACHED and stops, and
                  SAYS it stops -- `trade_flow_source: "UNAVAILABLE"` on every
                  row, so a public run can never be mistaken for evidence of
                  no interaction.

The public mode is not a degraded substitute; it is how the surface is
validated against real books without credentials, and how the missing input is
isolated rather than hidden. `sharesTraded` is deliberately NOT used as a flow
proxy: it is cumulative per market and cannot say a trade happened at our
price, which is exactly the claim VOLUME_AHEAD_CLEARED depends on.

Read-only in both modes. No order transport, no credential of its own.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_l5_surface_shadow as SS

LABEL = "PMUS_L5_SURFACE_RUNNER_V1"

AUTHENTICATED = "AUTHENTICATED_WS"
PUBLIC = "PUBLIC_BOOK_ONLY"


def _f(v: Any) -> Optional[str]:
    return None if v is None else str(v)


def market_from_gateway(slug: str, get_json) -> Optional[Dict[str, Any]]:
    """Public book -> the recorder's state shape, with NO trade tape.

    `trades` is None, not [], because None means "unreadable" to the ladder
    and [] would assert nobody traded -- the claim that manufactures no-fills.
    """
    try:
        bbo = (get_json("/v1/markets/%s/bbo" % slug) or {}).get("marketData") or {}
        md = (get_json("/v1/markets/%s/book" % slug) or {}).get("marketData") or {}
    except Exception:                                          # noqa: BLE001
        return None
    bids, offers = md.get("bids") or [], md.get("offers") or []

    def top(levels):
        if not levels:
            return None, None
        lv = levels[0]
        return (lv.get("px") or {}).get("value"), lv.get("qty")

    b_px, b_qty = top(bids)
    a_px, a_qty = top(offers)
    return {
        "bid": _f((bbo.get("bestBid") or {}).get("value") or b_px),
        "ask": _f((bbo.get("bestAsk") or {}).get("value") or a_px),
        "bid_qty": _f(b_qty), "ask_qty": _f(a_qty),
        "trades": None,                       # UNREADABLE, never empty
        "state": bbo.get("state"),
        "n_bid_levels": len(bids), "n_ask_levels": len(offers),
    }


def market_from_recorder(state: Mapping[str, Any]) -> Dict[str, Any]:
    """The WS recorder's per-market state, used as-is.

    Its trade tape is the authenticated one; `pmus_prints` applies the
    coverage rules (a wrapped 500-trade ring vouches for nothing)."""
    return {
        "bid": _f(state.get("bid")), "ask": _f(state.get("ask")),
        "bid_qty": _f(state.get("bid_qty")),
        "ask_qty": _f(state.get("ask_qty")),
        "trades": state.get("trades"),
        "both_sides_served": state.get("both_sides_served"),
    }


def discover(cap: int = 64) -> List[str]:
    """Live sports slugs, unsigned. Two-sided is decided per observation by
    the surface itself, not pre-filtered here, so a market that becomes
    two-sided mid-run is not excluded by a stale screen."""
    from src.pmus_sports_collector import (http_get_json, fetch_live_events,
                                           live_event_targets)
    events, _ = fetch_live_events(http_get_json)
    return [t["slug"] for t in live_event_targets(events, prefixes=())][:cap]


def run(out_dir: str, *, minutes: float = 30.0, interval: float = 5.0,
        cap: int = 40, source: str = PUBLIC,
        states: Optional[Mapping[str, Mapping[str, Any]]] = None) -> int:
    from src.pmus_sports_collector import http_get_json

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "pmus_surface_%s.jsonl"
                        % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    fh = open(path, "a", encoding="utf-8")

    def emit(row: Mapping[str, Any]) -> None:
        fh.write(json.dumps(row, default=str) + "\n")
        fh.flush()

    spec = SS.spec()
    slugs = list(states) if states is not None else discover(cap)
    surfaces: Dict[str, SS.MarketSurface] = {
        s: SS.MarketSurface(s, spec) for s in slugs}

    emit({"kind": "session_start", "label": LABEL, "t": time.time(),
          "spec_label": spec.label, "spec_sha256": spec.spec_sha256(),
          "trade_flow_source": (AUTHENTICATED if source == AUTHENTICATED
                                else "UNAVAILABLE"),
          "source": source, "slugs": slugs,
          "labels": ("a PUBLIC run cannot reach TRADE_FLOW: the gateway "
                     "serves no print tape and sharesTraded is cumulative per "
                     "market. It is not used as a flow proxy.")})

    deadline = time.time() + minutes * 60.0
    obs = 0
    while time.time() < deadline:
        t0 = time.time()
        for slug, surf in surfaces.items():
            if source == AUTHENTICATED:
                st = (states or {}).get(slug)
                mkt = market_from_recorder(st) if st else None
            else:
                mkt = market_from_gateway(slug, http_get_json)
            if mkt is None:
                surf.empty_reason["unreadable"] += 1
                continue
            for row in SS.step(surf, mkt, time.time(), tte_s=3600.0):
                emit(row)
        obs += 1
        if obs % 6 == 0:
            emit({"kind": "progress", "t": time.time(), "cycles": obs,
                  **SS.report(surfaces)})
        sleep = interval - (time.time() - t0)
        if sleep > 0:
            time.sleep(sleep)

    final = SS.report(surfaces)
    emit({"kind": "session_end", "t": time.time(), "cycles": obs, **final})
    fh.close()
    print("PMUS_SURFACE " + json.dumps({"ok": True, "tape": path,
                                        "cycles": obs, **final},
                                       default=str), flush=True)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default="data/pmus_l5_surface")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--cap", type=int, default=40)
    ap.add_argument("--source", choices=(PUBLIC, AUTHENTICATED), default=PUBLIC)
    a = ap.parse_args(argv)
    return run(a.out_dir, minutes=a.minutes, interval=a.interval, cap=a.cap,
               source=a.source)


if __name__ == "__main__":
    raise SystemExit(main())
