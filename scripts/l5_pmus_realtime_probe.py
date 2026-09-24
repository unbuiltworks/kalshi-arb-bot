"""The DISARMED production path, run CONTINUOUSLY during a BTC15m window.

    new L5 action observed -> current authenticated book -> frozen L5_NATIVE
    -> PMUS_TRANSLATION -> SAFETY_OVERLAY -> would-submit order

WHY THIS REPLACES THE CLOSE-OF-WINDOW DRY RUN. That run replayed the whole
window at its close, so every L5 action was minutes old by the time the
executor saw it and all four were refused STALE_ACTION at 229-348s against
the executor's 30s bound. It was not a test of the live path; it was
guaranteed to fail one. Each action is now handled WHEN IT IS OBSERVED, from
the recorder's live in-memory state, with no replay and no batching.

The 30s stale-action bound, the 2.0s book-staleness bound inside the frozen
rule, and every safety limit are UNCHANGED. The fix is to stop feeding the
executor stale input, not to widen what it accepts.

Same path that transmits once armed. DISARMED, the transport holds
ReadOnlyTransport, which raises on any method but GET -- so this process does
not merely decline to send an order, it holds no object able to send one.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Set

from src import l5_authenticated_observation as OBS
from src import l5_live_execution as EX
from src import l5_pmus_live_wiring as W
from src import l5_probe_manifest as PM

WINDOW_S = 900.0
SLUG_PREFIX = "cpc-btc-updown-15m-"

#: how often to ask the public activity feed for L5's newest trades. The
#: executor refuses anything older than 30s, so the poll has to be a small
#: fraction of that or we would manufacture our own staleness.
POLL_S = 5.0


def current_slug(now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    t = dt.datetime.fromtimestamp(int(now // WINDOW_S) * WINDOW_S,
                                  dt.timezone.utc)
    return "%s%s-%02d%02dz" % (SLUG_PREFIX, t.strftime("%Y-%m-%d"),
                               t.hour, t.minute)


def live_books(slug: str) -> List[dict]:
    """Authenticated book states for this market, from the LIVE state."""
    from src import pmus_ws_evidence_recorder as R
    st = (R.LIVE_MSTATE or {}).get(slug) or {}
    out = []
    for h in (st.get("bbo_history") or ()):
        bid, ask = h.get("bid"), h.get("ask")
        out.append({"t": h.get("t"), "bid": bid, "ask": ask,
                    "bid_qty": h.get("bid_qty"), "ask_qty": h.get("ask_qty"),
                    "status": ("OK" if bid is not None and ask is not None
                               else "NO_BOOK" if bid is None and ask is None
                               else "ONE_SIDED")})
    out.sort(key=lambda r: (r["t"] is None, r["t"]))
    return out


def live_trades(slug: str) -> List[float]:
    from src import pmus_ws_evidence_recorder as R
    st = (R.LIVE_MSTATE or {}).get(slug) or {}
    ts = []
    for tr in (st.get("trades") or ()):
        t = OBS._epoch(tr.get("trade_time")) or OBS._epoch(tr.get("t"))
        if t is not None:
            ts.append(t)
    return sorted(ts)


def new_l5_actions(slug: str, seen: Set[str]) -> List[dict]:
    """L5's trades in THIS window that we have not handled yet."""
    from src import l5_pmus_realtime_observer as O
    ws = OBS.window_start_epoch(slug)
    out = []
    for r in (O.l5_activity(limit=100) or []):
        try:
            t = float(r.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if ws is None or not (ws <= t <= ws + WINDOW_S):
            continue
        key = O.action_key(r)
        if key in seen:
            continue
        act = O.l5_to_pmus_action(r)
        if not act.get("pmus_side"):
            continue
        seen.add(key)
        out.append({"action_key": key,
                    "receipt_utc": dt.datetime.now(
                        dt.timezone.utc).isoformat(),
                    "l5_event_utc": dt.datetime.fromtimestamp(
                        t, dt.timezone.utc).isoformat(),
                    "l5": act, "pmus": {"slug": slug}})
    return out


class WindowRun:
    """One window's accumulating result, reported when the window closes."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.records: List[dict] = []
        self.actions = 0
        self.started = time.time()

    def report(self) -> Dict[str, Any]:
        books = live_books(self.slug)
        trades = live_trades(self.slug)
        ev = OBS.evaluate(self.slug, {self.slug: books},
                          {self.slug: trades}, [])
        ws = OBS.window_start_epoch(self.slug) or 0
        first_ok = next((b for b in books if b["status"] == "OK"), None)
        summary = W.summarize(self.records)
        stale = sum(1 for r in self.records
                    if str(r.get("refused_reason") or "").startswith(
                        "STALE_ACTION"))
        return {
            "market": self.slug,
            "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "subscribed_before_or_at_open": bool(
                first_ok and (OBS._epoch(first_ok["t"]) or 0) - ws <= 5.0),
            "first_book_latency_s": ev.get("first_book_latency_s"),
            "first_trade_latency_s": ev.get("first_trade_latency_s"),
            "coverage_pct_of_window": ev.get("coverage_pct_of_window"),
            "max_book_gap_s": ev.get("max_book_gap_s"),
            "usable_book_states": ev.get("usable_book_states"),
            "authenticated_trades": ev.get("authenticated_trades"),
            "realtime_l5_actions": self.actions,
            "would_submit": summary["would_submit"],
            "would_submit_by_mode": summary["would_submit_by_mode"],
            "stale_action_refusals": stale,
            "refused_by_layer": summary["refused_by_layer"],
            "refused_reason": summary["refused_reason"],
            "submitted_live": summary["submitted_live"],
            "live_limits": PM.probe_limits().as_dict(),
            "worst_case": PM.worst_case(),
            "labels": ("DISARMED realtime run. Each action was handled when "
                       "observed, never replayed. A would-submit order is an "
                       "intent the transport declined to send."),
        }


def run_forever(state_path: str, transport_factory=None) -> None:
    limits = PM.probe_limits()
    store = EX.InventoryStore(state_path)
    seen: Set[str] = set()
    run: Optional[WindowRun] = None

    while True:
        try:
            slug = current_slug()
            if run is None or run.slug != slug:
                if run is not None:
                    print("L5_REALTIME_WINDOW " + json.dumps(
                        run.report(), separators=(",", ":"), default=str),
                        flush=True)
                run = WindowRun(slug)
                seen.clear()

            actions = new_l5_actions(slug, seen)
            if actions:
                tp = (transport_factory or _default_transport)(slug)
                out = W.run_window(actions, live_books(slug), market=slug,
                                   window=slug[-5:], store=store,
                                   limits=limits, transport=tp,
                                   lam=PM.LAMBDA)
                run.records.extend(out["records"])
                run.actions += len(actions)
        except BaseException as exc:                           # noqa: BLE001
            print("L5_REALTIME_ERR " + json.dumps(
                {"err": type(exc).__name__, "detail": str(exc)[:200]}),
                flush=True)
        try:
            time.sleep(POLL_S)
        except BaseException:                                  # noqa: BLE001
            return


def _default_transport(slug: str):
    from src import l5_pmus_transport as TP
    return TP.PmusTransport(market_slug=slug)


def main(argv: Optional[List[str]] = None) -> int:
    run_forever(os.getenv("L5_PROBE_STATE",
                          "/state/pmus-ws/l5_probe_inventory.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
