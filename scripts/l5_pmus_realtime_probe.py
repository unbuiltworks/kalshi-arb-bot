"""The DISARMED production path, run CONTINUOUSLY across BTC15m windows.

    new L5 action observed -> contemporaneous authenticated PMUS book ->
    frozen L5_NATIVE -> PMUS_TRANSLATION -> SAFETY_OVERLAY -> would-order

Each action is handled WHEN IT IS OBSERVED, from the recorder's live state.
Nothing is batched and nothing is replayed: the close-of-window run that
preceded this refused all four real L5 actions with STALE_ACTION at 229-348s
against the executor's 30s bound, because every action was minutes old by the
time the executor saw it. The executor was right; the input was stale. No
bound was widened to fix that.

This rolls through windows on its own. A window with no L5 actions, a refused
translation, a quiet market -- all ordinary, none of them stop the run. It
stops only on a genuine integrity problem (see `l5_session_integrity`), and
above all if anything is ever submitted while DISARMED.

Every action and every window is written append-only to the session journal
with full provenance, so the session can be replayed exactly.

SHADOW. Nothing here is filled, settled or realized. A would-order is an
intent the transport declined to send. The economics carried on the window
summary are labelled SHADOW and are modelled from would-orders, never from
fills that did not happen.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from src import l5_authenticated_observation as OBS
from src import l5_live_execution as EX
from src import l5_pmus_live_wiring as W
from src import l5_probe_manifest as PM
from src import l5_session_integrity as IG
from src import l5_session_journal as JN

WINDOW_S = 900.0
SLUG_PREFIX = "cpc-btc-updown-15m-"

#: The executor refuses anything older than 30s, so the poll must be a small
#: fraction of that or the probe manufactures the staleness it exists to
#: avoid. Pinned by test against EX.STALE_ACTION_MAX_S.
POLL_S = 5.0

#: run the preflight this far into a window: late enough that the venue has
#: opened the market, early enough that it is nowhere near closing. The last
#: run executed at +899s and every market field came back empty because the
#: window had effectively closed.
PREFLIGHT_AT_S = 45.0

#: retry cadence and cutoff for the preflight within a window
PREFLIGHT_RETRY_S = 60.0
PREFLIGHT_UNTIL_S = 780.0


def current_slug(now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    t = dt.datetime.fromtimestamp(int(now // WINDOW_S) * WINDOW_S,
                                  dt.timezone.utc)
    return "%s%s-%02d%02dz" % (SLUG_PREFIX, t.strftime("%Y-%m-%d"),
                               t.hour, t.minute)


def _state(slug: str) -> Dict[str, Any]:
    from src import pmus_ws_evidence_recorder as R
    return (R.LIVE_MSTATE or {}).get(slug) or {}


def live_books(slug: str) -> List[dict]:
    out = []
    for h in (_state(slug).get("bbo_history") or ()):
        bid, ask = h.get("bid"), h.get("ask")
        out.append({"t": h.get("t"), "bid": bid, "ask": ask,
                    "bid_qty": h.get("bid_qty"), "ask_qty": h.get("ask_qty"),
                    "status": ("OK" if bid is not None and ask is not None
                               else "NO_BOOK" if bid is None and ask is None
                               else "ONE_SIDED")})
    out.sort(key=lambda r: (r["t"] is None, r["t"]))
    return out


def live_trades(slug: str) -> List[float]:
    ts = []
    for tr in (_state(slug).get("trades") or ()):
        t = OBS._epoch(tr.get("trade_time")) or OBS._epoch(tr.get("t"))
        if t is not None:
            ts.append(t)
    return sorted(ts)


def new_l5_actions(slug: str, seen: Set[str]) -> Tuple[List[dict], bool]:
    """L5's trades in THIS window that we have not handled yet.

    Returns (actions, feed_ok). `l5_activity` returns None when the FEED
    FAILED, and an empty list when L5 simply has not traded. Collapsing those
    with `or []` makes a broken feed report as a quiet market -- a window
    saying l5_actions 0 would then be evidence of nothing at all while
    looking like evidence of inactivity. They are kept apart.
    """
    from src import l5_pmus_realtime_observer as O
    ws = OBS.window_start_epoch(slug)
    rows = O.l5_activity(limit=100)
    if rows is None:
        return [], False
    out = []
    for r in rows:
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
        now = time.time()
        out.append({"action_key": key,
                    "receipt_utc": dt.datetime.fromtimestamp(
                        now, dt.timezone.utc).isoformat(),
                    "l5_event_utc": dt.datetime.fromtimestamp(
                        t, dt.timezone.utc).isoformat(),
                    "observation_latency_s": round(now - t, 2),
                    "l5": act, "pmus": {"slug": slug}})
    return out, True


class Window:
    """One window, accumulating as it happens."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.start = OBS.window_start_epoch(slug) or time.time()
        self.records: List[dict] = []
        self.latencies: List[float] = []
        self.preflight: Optional[dict] = None
        self.preflight_at: Optional[float] = None
        self.preflight_attempts = 0
        self.last_preflight_t: Optional[float] = None
        # Continuously SAMPLED observation. The recorder's live state is a
        # ring buffer -- bbo_history keeps 2000 entries and trades 500 -- and
        # an active BTC15m window overruns both, so scanning it at the window
        # close sees only the tail. Measured that way, three consecutive
        # windows reported first-book latency of 645.4s, 672.2s and 644.2s
        # with coverage stuck near 25% and max_gap exactly equal to the
        # latency: the signature of a rolled buffer, not of a late
        # subscription. These are accumulated as the window runs instead.
        self.spans: List[List[float]] = []          # merged [t, t+ATTACH]
        self.first_book_t: Optional[float] = None
        self.first_trade_t: Optional[float] = None
        self.book_states_seen = 0
        self.trades_seen = 0
        self.feed_polls = 0
        self.feed_failures = 0
        self._book_seen: Set[float] = set()
        self._trade_seen: Set[float] = set()

    ATTACH_S = 15.0

    def observe(self, books: Sequence[Mapping[str, Any]],
                trades: Sequence[float]) -> None:
        """Fold this poll's view into the window's accumulated observation."""
        for b in books:
            if b.get("status") != "OK":
                continue
            t = OBS._epoch(b.get("t"))
            if t is None or t in self._book_seen:
                continue
            self._book_seen.add(t)
            self.book_states_seen += 1
            if self.first_book_t is None or t < self.first_book_t:
                self.first_book_t = t
            self._merge(t, t + self.ATTACH_S)
        if len(self._book_seen) > 60000:            # bounded, never unbounded
            self._book_seen = set(sorted(self._book_seen)[-30000:])
        for t in trades:
            if t in self._trade_seen:
                continue
            self._trade_seen.add(t)
            self.trades_seen += 1
            if self.first_trade_t is None or t < self.first_trade_t:
                self.first_trade_t = t
        if len(self._trade_seen) > 60000:
            self._trade_seen = set(sorted(self._trade_seen)[-30000:])

    def _merge(self, a: float, z: float) -> None:
        self.spans.append([a, z])
        self.spans.sort()
        out: List[List[float]] = []
        for lo, hi in self.spans:
            if out and lo <= out[-1][1]:
                out[-1][1] = max(out[-1][1], hi)
            else:
                out.append([lo, hi])
        self.spans = out

    def coverage(self) -> Dict[str, Any]:
        lo, hi = self.start, self.start + WINDOW_S
        covered = 0.0
        gaps: List[float] = []
        cursor = lo
        for a, z in self.spans:
            a, z = max(lo, a), min(hi, z)
            if z <= a:
                continue
            if a > cursor:
                gaps.append(a - cursor)
            covered += z - a
            cursor = max(cursor, z)
        if cursor < hi:
            gaps.append(hi - cursor)
        return {"pct": round(100.0 * covered / WINDOW_S, 1),
                "max_gap_s": round(max(gaps), 1) if gaps else 0.0}

    # ------------------------------------------------------------ economics
    def shadow_economics(self) -> Dict[str, Any]:
        """Modelled from WOULD-ORDERS. Never a fill, never realized."""
        up = dn = D(0)
        cost_up = cost_dn = D(0)
        fee = D(0)
        for r in self.records:
            o = r.get("ORDER") or {}
            i = o.get("intent")
            if not i or o.get("reason") != "NOT_ARMED":
                continue
            qty, px = D(str(i["qty"])), D(str(i["price"]))
            if i["side"] == "UP":
                up += qty
                cost_up += px * qty
            else:
                dn += qty
                cost_dn += px * qty
            t = (r.get("PMUS_TRANSLATION") or {})
            if t.get("mode") == "TAKER":
                from src import pmus_fees as fees
                fee += fees.taker_fee(px, qty)
        matched = min(up, dn)
        resid = up - dn
        pair = None
        if up > 0 and dn > 0:
            pair = str((cost_up / up) + (cost_dn / dn))
        return {"LABEL": "SHADOW_NOT_REALIZED",
                "qty_up": str(up), "qty_dn": str(dn),
                "matched": str(matched), "residual": str(resid),
                "cost_up_usd": str(cost_up), "cost_dn_usd": str(cost_dn),
                "modelled_taker_fee_usd": str(fee),
                "pair_cost_per_share": pair,
                "opposite_side_acquired": bool(up > 0 and dn > 0),
                "note": ("modelled from would-orders the transport declined "
                         "to send; no fill, settlement or realization "
                         "occurred or is implied")}

    def summary(self, journal: JN.Journal) -> Dict[str, Any]:
        cov = self.coverage()
        sub_dt = (round(self.first_book_t - self.start, 1)
                  if self.first_book_t is not None else None)
        first_trade = (round(self.first_trade_t - self.start, 1)
                       if self.first_trade_t is not None else None)
        s = W.summarize(self.records)
        stale = sum(1 for r in self.records
                    if str(r.get("refused_reason") or "").startswith(
                        "STALE_ACTION"))
        lat = sorted(self.latencies)

        def pct(q):
            if not lat:
                return None
            i = min(len(lat) - 1, int(round(q * (len(lat) - 1))))
            return lat[i]
        return {
            "market": self.slug,
            "window_start_utc": dt.datetime.fromtimestamp(
                self.start, dt.timezone.utc).isoformat(),
            "subscription_vs_open_s": sub_dt,
            "subscribed_before_or_at_open": bool(
                sub_dt is not None and sub_dt <= 5.0),
            "first_book_latency_s": sub_dt,
            "first_trade_latency_s": first_trade,
            "book_observed_before_first_trade": (
                None if (sub_dt is None or first_trade is None)
                else sub_dt <= first_trade),
            "book_coverage_pct": cov["pct"],
            "max_book_gap_s": cov["max_gap_s"],
            "usable_book_states": self.book_states_seen,
            "authenticated_trades": self.trades_seen,
            "measurement": ("accumulated as the window ran; the recorder's "
                            "live state is a ring buffer (2000 book states, "
                            "500 trades) and an active window overruns both, "
                            "so a close-of-window scan sees only the tail"),
            "l5_actions": len(self.records),
            # a window reporting 0 actions means L5 was quiet ONLY if the feed
            # was healthy; otherwise it means we could not see
            "l5_feed": {"polls": self.feed_polls,
                        "failures": self.feed_failures,
                        "healthy": self.feed_failures == 0,
                        "zero_actions_is_trustworthy": (
                            self.feed_failures == 0 and self.feed_polls > 0)},
            # AGE OF L5'S ACTION when we decided on it: venue event time ->
            # our observation. This is NOT what the executor's 30s
            # STALE_ACTION gate measures; that gate measures our own
            # receipt -> decision handoff, which is sub-second because the
            # action is handled in the same poll iteration. Both are reported
            # so neither is mistaken for the other.
            "action_age_at_decision_s": {
                "p50": pct(0.50), "p90": pct(0.90),
                "max": (lat[-1] if lat else None),
                "n": len(lat),
                "measures": "L5 venue event timestamp -> our observation",
            },
            "stale_action_gate_measures": (
                "our receipt_utc -> decision, bound at %.0fs; it does NOT "
                "bound the venue-event age above"
                % EX.STALE_ACTION_MAX_S),
            "median_observation_latency_s": (lat[len(lat) // 2] if lat
                                             else None),
            "would_orders": s["would_submit"],
            "would_orders_by_mode": s["would_submit_by_mode"],
            "stale_action_refusals": stale,
            "refused_by_layer": s["refused_by_layer"],
            "refused_reason": s["refused_reason"],
            "submitted_live": s["submitted_live"],
            "shadow_economics": self.shadow_economics(),
            "preflight": ((self.preflight or {}).get("preflight")
                          if self.preflight else "NOT_RUN"),
            "preflight_at_s": self.preflight_at,
            "preflight_attempts": self.preflight_attempts,
            "preflight_blockers": [
                c["check"] for c in ((self.preflight or {}).get("checks") or [])
                if not c.get("passed") and c.get("blocking", True)],
            "journal_writes_failed": journal.writes_failed,
            "live_limits": PM.probe_limits().as_dict(),
            "worst_case": PM.worst_case(),
            "armed": False,
        }


def run_forever(state_path: str, journal_path: str,
                transport_factory=None, max_windows: Optional[int] = None,
                sleep_fn=time.sleep) -> int:
    limits = PM.probe_limits()
    store = EX.InventoryStore(state_path)
    journal = JN.Journal(journal_path)
    seen: Set[str] = set()
    win: Optional[Window] = None
    done = 0

    journal.write("session_start", {
        **journal.resume_note(),
        "frozen": IG.frozen_ok()["hashes"],
        "limits": limits.as_dict(),
        "worst_case": PM.worst_case(),
        "armed": False,
        "poll_s": POLL_S,
        "labels": "DISARMED continuous session; no order can be transmitted",
    })

    while True:
        try:
            slug = current_slug()
            if win is None or win.slug != slug:
                if win is not None:
                    rep = win.summary(journal)
                    journal.write("window_summary", rep)
                    print("L5_WINDOW " + json.dumps(
                        rep, separators=(",", ":"), default=str), flush=True)
                    done += 1
                    if max_windows is not None and done >= max_windows:
                        return done
                win = Window(slug)
                seen.clear()
                journal.write("window_open", {"market": slug,
                                              "start": win.start})

            now = time.time()

            # Preflight WHILE the market is live, retried until it passes.
            # The market metadata read goes to the PUBLIC gateway, which does
            # not expose a BTC15m window in its first minutes -- the same
            # gateway behaviour already established for the book. One early
            # shot therefore fails on market_readable for a reason that has
            # nothing to do with the account. Retrying costs nothing and
            # records the earliest attempt that actually succeeded.
            due = (win.preflight is None
                   or (win.preflight.get("preflight") != "PASSED"
                       and now - (win.last_preflight_t or 0) >= PREFLIGHT_RETRY_S
                       and now - win.start <= PREFLIGHT_UNTIL_S))
            if due and now - win.start >= PREFLIGHT_AT_S:
                win.last_preflight_t = now
                win.preflight_attempts += 1
                tp = (transport_factory or _default_transport)(slug)
                try:
                    win.preflight = tp.preflight()
                except BaseException as exc:                   # noqa: BLE001
                    try:
                        win.preflight = json.loads(str(exc))
                    except Exception:                          # noqa: BLE001
                        win.preflight = {"preflight": "FAILED",
                                         "fatal": type(exc).__name__}
                win.preflight_at = round(now - win.start, 1)
                journal.write("preflight", {"market": slug,
                                            "at_s": win.preflight_at,
                                            "report": win.preflight})

            books_poll = live_books(slug)
            win.observe(books_poll, live_trades(slug))

            actions, feed_ok = new_l5_actions(slug, seen)
            win.feed_polls += 1
            if not feed_ok:
                win.feed_failures += 1
            if actions:
                books = books_poll
                tp = (transport_factory or _default_transport)(slug)
                out = W.run_window(actions, books, market=slug,
                                   window=slug[-5:], store=store,
                                   limits=limits, transport=tp,
                                   lam=PM.LAMBDA)
                for a, r in zip(actions, out["records"]):
                    win.latencies.append(a["observation_latency_s"])
                    journal.write("action", {
                        "market": slug,
                        "observation_latency_s": a["observation_latency_s"],
                        "l5_event_utc": a["l5_event_utc"],
                        "receipt_utc": a["receipt_utc"],
                        "provenance": r,
                    })
                win.records.extend(out["records"])

            books_now = live_books(slug)
            last_t = (OBS._epoch(books_now[-1]["t"]) if books_now else None)
            IG.check(window_market=slug, last_book_t=last_t,
                     submitted_live=W.summarize(win.records)["submitted_live"],
                     records=win.records,
                     has_credentials=_creds_present(),
                     store_readable=True, now=now)

        except IG.Halt as h:
            journal.write("halt", {"reason": h.reason, "detail": h.detail})
            print("L5_SESSION_HALT " + json.dumps(
                {"reason": h.reason, "detail": h.detail},
                separators=(",", ":"), default=str), flush=True)
            return done
        except BaseException as exc:                           # noqa: BLE001
            journal.write("error", {"err": type(exc).__name__,
                                    "detail": str(exc)[:300]})
            print("L5_REALTIME_ERR " + json.dumps(
                {"err": type(exc).__name__, "detail": str(exc)[:200]}),
                flush=True)
        try:
            sleep_fn(POLL_S)
        except BaseException:                                  # noqa: BLE001
            return done


def _creds_present() -> bool:
    from src import pmus_auth
    return pmus_auth.has_credentials()


def _default_transport(slug: str):
    from src import l5_pmus_transport as TP
    return TP.PmusTransport(market_slug=slug)


def main(argv: Optional[List[str]] = None) -> int:
    run_forever(
        os.getenv("L5_PROBE_STATE", "/state/pmus-ws/l5_probe_inventory.json"),
        os.getenv("L5_SESSION_JOURNAL",
                  "/state/pmus-ws/l5_session_journal.jsonl"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
