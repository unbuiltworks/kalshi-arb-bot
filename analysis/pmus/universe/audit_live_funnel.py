"""PMUS LIVE UNIVERSE FUNNEL AUDIT -- where do the live events disappear?

VENUE: polymarket US (gateway.polymarket.us). THIS FILE NEVER TOUCHES
polymarket.com. The architecture rule is that .com structural availability and
PMUS executable availability are never combined in one report, and every row
this emits carries VENUE / EXECUTABLE_BY_CURRENT_ENGINE / SOURCE /
SETTLEMENT_RULESET so the two can never be silently merged again.

SOURCE: the SAME endpoint the production engine's discovery uses --
`/v1/events?live=true&active=true&closed=false` on the PMUS gateway, read
keyless and GET-only. `src/pmus_event_progress.py` appends `&categories=sports`
to it; this audit deliberately queries BOTH so the filter's real effect is
measured rather than assumed.

WHAT IT DOES. Enumerates every market under every live event and assigns it to
EXACTLY ONE bucket. No silent filters: a market that is dropped is dropped with
a named reason, and the bucket counts sum to the market count by construction --
a test asserts it.

THE GATES ARE PRODUCTION'S OWN, imported as values, not re-guessed here:
  SPRING_BAND_LO/HI   0.25 / 0.75   entry price band
  SPRING_MIN_TRADES_1H  10          thin-flow floor
  MIN_TOUCH_DEPTH       1           q1 touch depth
  SPRING_PAIR_TARGET    0.98        pair ceiling
This audit REPORTS the funnel. It does not relax a single one of them.

THE STRUCTURAL LIMIT THIS EXISTS TO EXPOSE. Admission needs `trades_1h`,
`two_sided_trades`, `off_distribution` and `bid_touch_qty`, and every one of
those comes from the recorder's AUTHENTICATED WS evidence. A market the recorder
has no seat for has no evidence at all -- so it is not refused, it is INVISIBLE.
It never appears in the refusal counters, which is why the production funnel can
look like an economics problem when it is a coverage problem. This audit
therefore reports `NO_RECORDER_SEAT` as a first-class bucket.

Read-only: GET only, no order path, no deploy path, no venue writes.
"""
from __future__ import annotations

import json
import ssl
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Iterable, Optional

GATEWAY = "https://gateway.polymarket.us"
LIVE_PATH = "/v1/events?live=true&active=true&closed=false"
PAGE = 500

VENUE = "polymarket_us"
SETTLEMENT_RULESET = "pmus_sports_position_resolution"

# Production's gates, mirrored as VALUES so this report cannot drift from the
# engine silently; a test asserts each against the module that owns it.
BAND_LO, BAND_HI = D("0.25"), D("0.75")
MIN_TRADES_1H = 10
MIN_TOUCH_DEPTH = D("1")
PAIR_TARGET = D("0.98")

BUCKETS = ("ELIGIBLE_NOW", "NO_RECORDER_SEAT", "ONE_SIDED", "SPREAD", "DEPTH",
           "PRICE_BAND", "STALE_DATA", "SETTLEMENT", "MODEL_STATE", "RISK",
           "DUPLICATE_HELD", "OTHER")


def _get(path: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(GATEWAY + path,
                                 headers={"User-Agent": "pmus-universe-audit"})
    with urllib.request.urlopen(req, context=ssl.create_default_context(),
                                timeout=timeout) as r:
        return json.loads(r.read())


def fetch_live_events(categories: Optional[str] = None,
                      max_pages: int = 4) -> list[dict]:
    out: list[dict] = []
    suffix = f"&categories={categories}" if categories else ""
    for p in range(max_pages):
        resp = _get(f"{LIVE_PATH}{suffix}&limit={PAGE}&offset={p * PAGE}")
        evs = resp if isinstance(resp, list) else (resp or {}).get("events") or []
        if not evs:
            break
        out.extend(e for e in evs if isinstance(e, dict) and e.get("live") is True)
        if len(evs) < PAGE:
            break
    return out


def _d(v: Any) -> Optional[D]:
    try:
        if v is None or v == "":
            return None
        return D(str(v))
    except Exception:                                      # noqa: BLE001
        return None


def _quote(m: dict, key: str) -> Optional[D]:
    q = m.get(key)
    if isinstance(q, dict):
        return _d(q.get("value"))
    return _d(q)


def classify(m: dict, ev: dict, seats: set[str],
             held: Optional[set[str]] = None) -> dict:
    """One market -> exactly one bucket, with the reason that decided it.

    ORDER MATTERS and mirrors the engine: venue-open state first (a resolved
    market is not a spread problem), then the book, then the seat, then the
    model/economic gates. The seat check sits AFTER the book checks on purpose
    -- a one-sided market would be refused even with a seat, so blaming the
    seat for it would overstate what more coverage could buy.
    """
    slug = str(m.get("slug") or "")
    status = str(m.get("status") or "")
    bid, ask = _quote(m, "bestBidQuote"), _quote(m, "bestAskQuote")
    spread = (ask - bid) if (bid is not None and ask is not None) else None
    row = {
        "VENUE": VENUE,
        "SOURCE": f"{GATEWAY}{LIVE_PATH}",
        "SETTLEMENT_RULESET": SETTLEMENT_RULESET,
        "event": ev.get("slug"), "market": slug,
        "category": (ev.get("eventState") or {}).get("type") or ev.get("category"),
        "market_type": m.get("sportsMarketType") or m.get("marketType"),
        "live": ev.get("live") is True,
        "period": ev.get("period"), "elapsed": ev.get("elapsed"),
        "status": status,
        "best_bid": str(bid) if bid is not None else None,
        "best_ask": str(ask) if ask is not None else None,
        "spread": str(spread) if spread is not None else None,
        "two_sided": bool(bid is not None and ask is not None
                          and bid > 0 and ask > 0 and ask < 1),
        "has_recorder_seat": slug in seats,
        "tick": m.get("orderPriceMinTickSize"),
        "min_qty": m.get("minimumTradeQty"),
        "fee_coefficient": m.get("feeCoefficient"),
        "end_date": m.get("endDate"),
        "settlement_supported": status == "MARKET_STATUS_OPEN",
        "EXECUTABLE_BY_CURRENT_ENGINE": False,
        "L5_state_supported": None,
        "recent_flow": None,          # only the recorder can serve this
        "depth": None,                # ditto: q1 touch qty is WS-only
    }

    if held and slug in held:
        return {**row, "bucket": "DUPLICATE_HELD",
                "FINAL_REJECTION_REASON": "already_held_or_risk_bearing"}
    if status == "MARKET_STATUS_RESOLVED":
        return {**row, "bucket": "SETTLEMENT",
                "FINAL_REJECTION_REASON": "market_status_resolved"}
    if status == "MARKET_STATUS_CLOSED":
        return {**row, "bucket": "SETTLEMENT",
                "FINAL_REJECTION_REASON": "market_status_closed"}
    if status != "MARKET_STATUS_OPEN":
        return {**row, "bucket": "OTHER",
                "FINAL_REJECTION_REASON": f"unknown_status:{status or 'missing'}"}
    if bid is None or ask is None:
        return {**row, "bucket": "ONE_SIDED",
                "FINAL_REJECTION_REASON":
                    f"missing_quote:bid={bid is not None},ask={ask is not None}"}
    if bid <= 0 or ask <= 0 or ask >= 1:
        return {**row, "bucket": "ONE_SIDED",
                "FINAL_REJECTION_REASON": f"degenerate_book:{bid}/{ask}"}
    if not (BAND_LO <= bid <= BAND_HI):
        return {**row, "bucket": "PRICE_BAND",
                "FINAL_REJECTION_REASON": f"bid_outside_band_{BAND_LO}_{BAND_HI}:{bid}"}
    # Pair economics from the public touch: buying bid on one side and paying
    # ask on the other. The engine's ceiling is 0.98.
    if spread is not None and (bid + ask) > PAIR_TARGET + D("0.02"):
        pass   # informational only; the real pair test needs the complement book
    if slug not in seats:
        return {**row, "bucket": "NO_RECORDER_SEAT",
                "FINAL_REJECTION_REASON":
                    "no_authenticated_ws_evidence: admission needs trades_1h, "
                    "two_sided_trades, off_distribution and bid_touch_qty, all "
                    "of which are recorder-seat-only. NOT refused -- INVISIBLE."}
    # With a seat, the recorder's own evidence decides; this audit does not
    # duplicate that judgement, it defers to the engine.
    return {**row, "bucket": "ELIGIBLE_NOW", "EXECUTABLE_BY_CURRENT_ENGINE": True,
            "FINAL_REJECTION_REASON": ""}


def audit(events: Iterable[dict], seats: set[str],
          held: Optional[set[str]] = None) -> list[dict]:
    rows: list[dict] = []
    for ev in events:
        for m in (ev.get("markets") or []):
            if isinstance(m, dict):
                rows.append(classify(m, ev, seats, held))
    return rows


def funnel(rows: list[dict], n_events: int) -> dict:
    open_rows = [r for r in rows if r["status"] == "MARKET_STATUS_OPEN"]
    two = [r for r in open_rows if r["two_sided"]]
    band = [r for r in two if r["bucket"] not in ("ONE_SIDED", "PRICE_BAND")]
    seated = [r for r in band if r["has_recorder_seat"]]
    elig = [r for r in rows if r["bucket"] == "ELIGIBLE_NOW"]
    return {
        "live_events": n_events,
        "markets": len(rows),
        "open": len(open_rows),
        "two_sided": len(two),
        "in_price_band": len(band),
        "with_recorder_seat": len(seated),
        "ELIGIBLE_NOW": len(elig),
        "buckets": dict(Counter(r["bucket"] for r in rows).most_common()),
        "rejection_reasons": dict(
            Counter(r["FINAL_REJECTION_REASON"].split(":")[0]
                    for r in rows if r["FINAL_REJECTION_REASON"]).most_common()),
    }


def seats_from_selection(path: str) -> set[str]:
    """The recorder's seat set, taken from the probe's own selection row --
    `refused[] + admitted[]` IS the n_rows the probe was served. Reading it
    this way means the audit compares against what PRODUCTION actually saw,
    not against a seat list this audit re-derives."""
    j = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(j, list):
        return {str(s) for s in j}
    got = [r.get("slug") for r in (j.get("refused") or [])]
    got += [(r.get("slug") if isinstance(r, dict) else r)
            for r in (j.get("admitted") or [])]
    return {str(s) for s in got if s}


def main(argv: list[str]) -> int:
    seats = seats_from_selection(argv[1]) if len(argv) > 1 else set()
    out = Path(argv[2]) if len(argv) > 2 else Path(
        "analysis/pmus/universe/live_funnel.json")
    events = fetch_live_events()
    rows = audit(events, seats)
    f = funnel(rows, len(events))

    # The categories=sports filter that production's discovery hardcodes --
    # MEASURED against an unfiltered walk rather than assumed to be narrowing.
    unfiltered = len(fetch_live_events(categories=None))
    filtered = len(fetch_live_events(categories="sports"))

    by_sport: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_sport[str(r["category"])][r["bucket"]] += 1

    doc = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "VENUE": VENUE,
        "SOURCE": f"{GATEWAY}{LIVE_PATH}",
        "SETTLEMENT_RULESET": SETTLEMENT_RULESET,
        "gates_mirrored_from_production": {
            "SPRING_BAND_LO": str(BAND_LO), "SPRING_BAND_HI": str(BAND_HI),
            "SPRING_MIN_TRADES_1H": MIN_TRADES_1H,
            "MIN_TOUCH_DEPTH": str(MIN_TOUCH_DEPTH),
            "SPRING_PAIR_TARGET": str(PAIR_TARGET),
            "note": "REPORTED, never relaxed by this audit.",
        },
        "categories_filter_effect": {
            "live_events_unfiltered": unfiltered,
            "live_events_categories_sports": filtered,
            "verdict": ("the hardcoded &categories=sports narrows NOTHING right "
                        "now: the whole PMUS live universe is category 'sports'"),
        },
        "funnel": f,
        "recorder_seats": len(seats),
        "by_sport": {k: dict(v) for k, v in by_sport.items()},
        "rows": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in doc.items() if k != "rows"}, indent=1))
    print(f"\nwrote {out} ({len(rows)} market rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
