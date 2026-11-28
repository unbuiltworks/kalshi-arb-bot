"""READ-ONLY residual-completion shadow — the L5-policy evidence tool.

Issue #167 comment 5519680543: the conservative pair shell can fill a
first leg and still strand a q=1 residual at the 300s campaign deadline
(SF/PIT +1 YES @ 0.71, DET/MIN +1 @ 0.42). Before anyone considers
L5-style PERSISTENT incremental inventory management above the safety
shell, this tool measures what that persistence would actually have been
worth: it replays the recorder's persisted authenticated tape AFTER a
given first-leg fill and reports every moment a fee-safe complement
would THEORETICALLY have completed the pair, had the residual been
managed as standing inventory instead of expiring with the campaign.

Strictly evidence, strictly labeled:
* PURE TAPE ANALYSIS — no network access of any kind exists in this
  module (structurally tested: no network-capable import, no transport,
  no order capability), nothing armed. It changes no live behavior.
* All completion verdicts are THEORETICAL and use the exact economics of
  the live shell (complement_bound / taker_completion_ok / MIN_EDGE from
  src.pmus_q1_executor) — the bound printed for first_cost 0.42 is the
  same 0.570 the live campaign used. Nothing here loosens residual
  controls, auto-repeats, or implies realized P&L.
* Maker-completion semantics match ws_gate/slot_replay: holding YES at
  first_cost, the complement is BUY NO at p_no <= bound, economically a
  resting YES ask at (1 - p_no); the most generous legal join is
  1 - bound, so a BUY-direction print at px >= (1 - bound) is a
  theoretical maker completion. Taker windows use the live BBO: a moment
  when the NO ask (= 1 - yes_bid) satisfies taker_completion_ok.

Run it ON THE HOST THAT HOLDS THE TAPE (AWS /state/pmus-ws or the
Railway recorder volume):

    python3 -m src.pmus_residual_completion_shadow \
        --slug aec-mlb-det-min-2026-09-02 --first-cost 0.42 \
        --entry-time 2026-09-03T02:48:00Z [--root /state/pmus-ws]
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping, Optional

from src import pmus_q1_executor as econ
from src import pmus_ws_evidence_recorder as rec


def _parse_iso(value: str) -> float:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def _events_for_slug(msg: Any, slug: str) -> list[dict]:
    events: list = []
    rec._walk_market_events(msg, events)
    return [e for e in events if e.get("slug") == slug]


def analyze_residual(rows, slug: str, first_cost: D, entry_t: float,
                     until_t: Optional[float] = None,
                     deadline_s: float = 300.0) -> dict:
    """Fold tape rows (epoch_t, msg) chronologically and report every
    THEORETICAL completion opportunity for the held residual."""
    bound = econ.complement_bound(first_cost)
    report: dict[str, Any] = {
        "labels": "THEORETICAL replay of persisted tape; no order, no "
                  "realized P&L; economics identical to the live shell",
        "slug": slug, "first_cost": str(first_cost),
        "complement_bound": str(bound) if bound is not None else None,
        "entry_time": _iso(entry_t),
        "campaign_deadline": _iso(entry_t + deadline_s),
    }
    if bound is None:
        return {**report, "verdict": "NO_FEE_SAFE_COMPLEMENT_EXISTS"}
    maker_join = econ.ONE - bound            # BUY print >= this completes

    state = {"bid": None, "ask": None}
    maker_hits: list[dict] = []
    taker_windows: list[dict] = []
    taker_open: Optional[dict] = None
    trades_seen = books_seen = 0
    last_t = entry_t
    for t, msg in rows:
        if t < entry_t or (until_t is not None and t > until_t):
            continue
        last_t = max(last_t, t)
        for ev in _events_for_slug(msg, slug):
            if ev["kind"] == "book":
                books_seen += 1
                bid, _ = rec._level(ev.get("bids"))
                ask, _ = rec._level(ev.get("asks"))
                state["bid"] = rec._px(ev.get("best_bid")) or bid \
                    or state["bid"]
                state["ask"] = rec._px(ev.get("best_ask")) or ask \
                    or state["ask"]
                if state["bid"] is not None:
                    try:
                        no_ask = econ.ONE - D(str(state["bid"]))
                        ok = econ.taker_completion_ok(first_cost, no_ask)
                    except Exception:
                        ok = False
                    if ok and taker_open is None:
                        taker_open = {"from": _iso(t), "from_t": t,
                                      "no_ask": str(no_ask)}
                    elif not ok and taker_open is not None:
                        taker_windows.append({**taker_open, "to": _iso(t)})
                        taker_open = None
                continue
            trades_seen += 1
            try:
                px = D(str(ev.get("price")))
            except Exception:
                continue
            direction = rec._trade_direction(
                {**ev, "price": str(px)}, state)
            if direction == "buy" and px >= maker_join:
                maker_hits.append({
                    "t": _iso(t), "px": str(px),
                    "seconds_after_entry": round(t - entry_t, 1),
                    "within_campaign_deadline": t <= entry_t + deadline_s,
                })
    if taker_open is not None:
        taker_windows.append({**taker_open, "to": _iso(last_t),
                              "open_at_end": True})

    post = [h for h in maker_hits if not h["within_campaign_deadline"]]
    first_post = post[0] if post else None
    deadline_t = entry_t + deadline_s
    post_taker = [w for w in taker_windows
                  if w.get("from_t", 0) > deadline_t]
    for w in taker_windows:
        w.pop("from_t", None)
    return {
        **report,
        "maker_join_yes_equivalent": str(maker_join),
        "tape_coverage": {"books": books_seen, "trades": trades_seen,
                          "through": _iso(last_t)},
        "theoretical_maker_completions": maker_hits[:50],
        "completions_total": len(maker_hits),
        "completions_after_deadline": len(post),
        "first_completion_after_deadline": first_post,
        "taker_fee_safe_windows": taker_windows[:50],
        "taker_windows_after_deadline": len(post_taker),
        "verdict": (
            "PERSISTENT_INVENTORY_WOULD_HAVE_COMPLETED"
            if post or post_taker
            else "NO_COMPLETION_AVAILABLE_ON_TAPE"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--first-cost", required=True)
    ap.add_argument("--entry-time", required=True,
                    help="ISO8601 UTC of the first-leg fill")
    ap.add_argument("--until", default=None)
    ap.add_argument("--root", default=os.getenv(rec.ROOT_ENV,
                                                rec.DEFAULT_ROOT))
    ap.add_argument("--max-bytes", type=int, default=0,
                    help="tail bound per daily tape file; 0 = whole file")
    args = ap.parse_args()
    entry_t = _parse_iso(args.entry_time)
    rows = rec.iter_recent_tape_rows(Path(args.root), now=None,
                                     max_bytes=args.max_bytes)
    report = analyze_residual(
        rows, args.slug, D(str(args.first_cost)), entry_t,
        _parse_iso(args.until) if args.until else None)
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
