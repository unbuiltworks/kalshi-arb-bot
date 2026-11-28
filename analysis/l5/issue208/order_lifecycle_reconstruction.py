"""Every own order's full event chain, DERIVED from the venue tapes.

WHY THIS EXISTS RATHER THAN THE TABLE WE ALREADY HAD. `analysis/l5/issue206/
own_order_lifecycles.py` carries 34 orders as a hand-transcribed tuple list
copied out of markdown reports. The statistics computed from it are sound,
but a transcription cannot be re-derived, cannot be checked, and cannot
answer a question nobody asked when it was typed. This module reads the
tapes and rebuilds each order's chain, so every field has a file and a line
behind it.

THE CHAIN, and exactly what the tape does and does not contain:

  L5_FILL_CALIB_PRESUBMIT   our maker QUOTE. side, own_px, wire_px, qty,
                            which side of the book it rests on, the book and
                            the queue-ahead reading at that instant, and the
                            fee/rebate estimate. This is an intent record.
  L5_LIVE_LIFECYCLE         the venue talking back. The FIRST event carrying
                            a `venue_order_id` is the ACKNOWLEDGEMENT; later
                            ones carry the `cum_qty` trajectory, so partial
                            fills are visible as deltas rather than inferred.
  (terminal lifecycle)      `ORDER_STATE_FILLED` / `ORDER_STATE_CANCELED`,
                            with avg_px, commission and final cum_qty.
  L5_LIVE_SETTLED           the window's settlement: per-side qty and cost,
                            matched and residual kept apart, and the venue's
                            own resolution source.

THERE IS NO DISTINCT SUBMIT-REQUEST EVENT. The tape records our intent
(presubmit) and then the venue's acknowledgement; the wire call between them
is not logged. So `ack_latency_s` is presubmit -> first acknowledged event,
which is an UPPER bound on venue latency because it includes whatever our
own process did in between. It is reported as that and not as round-trip
time.

`L5_FILL_CALIB_TERMINAL` is NOT a source here. Every one of its records in
these tapes is `SHADOW_QUOTE_WITHDRAWN` with `venue_confirmed: false` -- it
is the shadow quotes' terminal, not ours. Our terminal state comes from the
lifecycle events, which are the venue's.

PROVENANCE. `l5f-` client ids only. `shadow|ARGMAX|` and dotted coids are
modelled quotes and are excluded everywhere. Nothing here is a modelled
fill, and no fill rate computed from it is a profit.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

OWN_PREFIX = "l5f-"

PRESUBMIT = "L5_FILL_CALIB_PRESUBMIT"
LIFECYCLE = "L5_LIVE_LIFECYCLE"
SETTLED = "L5_LIVE_SETTLED"

FILLED = "ORDER_STATE_FILLED"
CANCELED = "ORDER_STATE_CANCELED"
NEW = "ORDER_STATE_NEW"

DEFAULT_TAPES = "analysis/l5/residual_exit/preserved/*.ndjson"


def _ts(s: str) -> float:
    """Tape timestamps are RFC3339 with nanoseconds; keep them as epoch."""
    from datetime import datetime, timezone
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1]
    if "." in s:
        head, frac = s.split(".", 1)
        frac = frac[:6].ljust(6, "0")
        s = "%s.%s" % (head, frac)
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


def read_events(patterns: List[str]) -> List[Dict[str, Any]]:
    """Every tape line, with its file and line number kept."""
    out: List[Dict[str, Any]] = []
    for pat in patterns:
        for path in sorted(glob.glob(pat)):
            with open(path) as fh:
                for n, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        body = rec.get("body")
                        if isinstance(body, str):
                            body = json.loads(body)
                    except BaseException:                      # noqa: BLE001
                        out.append({"file": path, "line": n,
                                    "label": "UNPARSEABLE", "body": None,
                                    "ts": None})
                        continue
                    if not isinstance(body, dict):
                        continue
                    out.append({"file": path, "line": n,
                                "label": rec.get("label"), "body": body,
                                "ts": rec.get("ts"),
                                "t": _ts(rec["ts"]) if rec.get("ts") else None})
    return out


def _window_of(slug: str) -> str:
    """`cpc-btc-updown-15m-2026-10-24-0115z` -> `2026-10-24 0115z`."""
    parts = (slug or "").split("-")
    if len(parts) < 8:
        return slug or ""
    return "%s-%s-%s %s" % (parts[4], parts[5], parts[6], parts[7])


class Order:
    """One own order, assembled from the events that mention its coid."""

    def __init__(self, coid: str) -> None:
        self.coid = coid
        self.quote: Optional[Dict[str, Any]] = None
        self.quote_at: Optional[float] = None
        self.quote_line_at: Optional[float] = None
        self.quote_src = ""
        self.ack_at: Optional[float] = None
        self.ack_src = ""
        self.venue_order_id = ""
        self.progress: List[Dict[str, Any]] = []
        self.terminal_state = ""
        self.terminal_at: Optional[float] = None
        self.terminal_src = ""
        self.market = ""
        self.avg_px = ""
        self.commission = ""
        self.final_qty = ""

    # ------------------------------------------------------------ assembly
    def add_quote(self, ev: Dict[str, Any]) -> None:
        b = ev["body"]
        if self.quote is not None:
            return                      # the first quote is the submission
        self.quote = b
        self.quote_at = b.get("t")          # the PROCESS clock
        self.quote_line_at = ev["t"]        # the COLLECTOR clock
        self.quote_src = "%s:%d" % (ev["file"], ev["line"])
        self.market = self.market or str(b.get("slug") or "")

    def add_lifecycle(self, ev: Dict[str, Any]) -> None:
        b = ev["body"]
        self.market = self.market or str(b.get("market") or "")
        voi = str(b.get("venue_order_id") or "")
        if voi and not self.venue_order_id:
            self.venue_order_id = voi
            self.ack_at = ev["t"]
            self.ack_src = "%s:%d" % (ev["file"], ev["line"])
        self.progress.append({"t": ev["t"], "state": b.get("state"),
                              "cum_qty": str(b.get("cum_qty") or "0"),
                              "delta": str(b.get("delta") or "0"),
                              "avg_px": str(b.get("avg_px") or ""),
                              "terminal": bool(b.get("terminal")),
                              "src": "%s:%d" % (ev["file"], ev["line"])})
        state = str(b.get("state") or "")
        if state in (FILLED, CANCELED):
            self.terminal_state = state
            self.terminal_at = ev["t"]
            self.terminal_src = "%s:%d" % (ev["file"], ev["line"])
            self.avg_px = str(b.get("avg_px") or "")
            self.commission = str(b.get("commission") or "")
            self.final_qty = str(b.get("cum_qty") or "0")

    # ------------------------------------------------------------ readings
    @property
    def side(self) -> str:
        return str((self.quote or {}).get("side") or "")

    @property
    def own_px(self) -> str:
        return str((self.quote or {}).get("own_px") or "")

    @property
    def qty(self) -> str:
        return str((self.quote or {}).get("qty") or "")

    @property
    def window(self) -> str:
        return _window_of(self.market)

    @property
    def log_lag_s(self) -> Optional[float]:
        """How late the QUOTE's own log line was, measured against the clock
        inside its body. A property of the log pipeline, not of the venue.

        It is reported because it is large and variable -- 0.03 s to 9.6 s in
        these tapes -- and because every duration that mixes the two clocks
        is inflated by it.
        """
        if self.quote_at is None or self.quote_line_at is None:
            return None
        return round(self.quote_line_at - self.quote_at, 3)

    @property
    def quote_to_ack_s(self) -> Optional[float]:
        """Quote line -> acknowledgement line. SAME CLOCK, both collector.

        This is NOT venue latency and must not be read as it. The collector
        lag on the quote line alone reaches 9.6 s here, so the difference of
        two lagged timestamps carries that jitter. The tape has no
        submit-request event and the lifecycle events carry no clock of their
        own, so submit -> ACK latency is NOT MEASURABLE from this source.
        """
        if self.quote_line_at is None or self.ack_at is None:
            return None
        return round(self.ack_at - self.quote_line_at, 3)

    @property
    def dur_from_quote_mixed_s(self) -> Optional[float]:
        """Quote body clock -> terminal line clock: MIXED, and inflated by
        `log_lag_s`. Reported only so the bias in the published durations is
        visible beside the same-clock figure."""
        if self.quote_at is None or self.terminal_at is None:
            return None
        return round(self.terminal_at - self.quote_at, 3)

    @property
    def rest_s(self) -> Optional[float]:
        """Acknowledgement -> terminal. What the order actually rested."""
        if self.ack_at is None or self.terminal_at is None:
            return None
        return round(self.terminal_at - self.ack_at, 3)

    @property
    def partial(self) -> bool:
        """A fill for less than the quoted quantity."""
        if self.terminal_state != FILLED or not self.qty:
            return False
        try:
            return D(self.final_qty) < D(self.qty)
        except BaseException:                                  # noqa: BLE001
            return False

    @property
    def fill_steps(self) -> int:
        """How many separate progress events moved cum_qty."""
        n = 0
        for p in self.progress:
            try:
                if D(p["delta"]) > 0:
                    n += 1
            except BaseException:                              # noqa: BLE001
                continue
        return n

    @property
    def dist_ticks(self) -> Optional[int]:
        """Ticks behind our OWN-side touch at the quote: `bid` for UP,
        `1 - ask` for DOWN, which is the planner's own convention."""
        q = self.quote or {}
        book = q.get("book") or {}
        try:
            own = D(str(q["own_px"]))
            if self.side == "UP":
                touch = D(str(book["bid"]))
            else:
                touch = D(1) - D(str(book["ask"]))
            return int((touch - own) / D("0.01"))
        except BaseException:                                  # noqa: BLE001
            return None

    @property
    def queue_ahead(self) -> Optional[str]:
        q = ((self.quote or {}).get("queue") or {}).get("queue_ahead")
        return None if q is None else str(q)

    @property
    def maker_rebate_estimate(self) -> Optional[str]:
        f = ((self.quote or {}).get("fees") or {}).get("schedule_estimate") or {}
        v = f.get("maker_rebate_usd")
        return None if v is None else str(v)

    def row(self) -> Dict[str, Any]:
        return {"coid": self.coid, "window": self.window, "side": self.side,
                "own_px": self.own_px, "qty": self.qty,
                "filled_qty": self.final_qty,
                "partial": self.partial, "fill_steps": self.fill_steps,
                "dist_ticks": self.dist_ticks, "queue_ahead": self.queue_ahead,
                "state": self.terminal_state or "NO_TERMINAL_IN_TAPE",
                "log_lag_s": self.log_lag_s,
                "quote_to_ack_s": self.quote_to_ack_s,
                "rest_s": self.rest_s,
                "dur_from_quote_mixed_s": self.dur_from_quote_mixed_s,
                "avg_px": self.avg_px, "commission": self.commission,
                "maker_rebate_estimate": self.maker_rebate_estimate,
                "venue_order_id": self.venue_order_id,
                "quote_src": self.quote_src, "ack_src": self.ack_src,
                "terminal_src": self.terminal_src}


def reconstruct(patterns: List[str]) -> Dict[str, Any]:
    events = read_events(patterns)
    orders: Dict[str, Order] = {}
    settlements: Dict[str, Dict[str, Any]] = {}
    excluded = {"shadow_coids": set(), "unparseable": 0}

    for ev in events:
        if ev["label"] == "UNPARSEABLE":
            excluded["unparseable"] += 1
            continue
        b, label = ev["body"], ev["label"]
        if label == SETTLED:
            settlements[_window_of(str(b.get("market") or ""))] = b
            continue
        coid = str(b.get("coid") or "")
        if not coid:
            continue
        if not coid.startswith(OWN_PREFIX):
            excluded["shadow_coids"].add(coid)
            continue
        o = orders.setdefault(coid, Order(coid))
        if label == PRESUBMIT:
            o.add_quote(ev)
        elif label == LIFECYCLE:
            o.add_lifecycle(ev)

    return {"orders": orders, "settlements": settlements,
            "excluded": {"shadow_coids": len(excluded["shadow_coids"]),
                         "unparseable": excluded["unparseable"]},
            "events_read": len(events)}


def reconcile(orders: Dict[str, Order],
              settlements: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per window: our filled orders against the venue's settlement.

    The settlement line is the authority. If the orders we can see do not add
    up to it, orders are MISSING from the tape -- which is a statement about
    the tape, not about the venue.
    """
    out = []
    by_window: Dict[str, List[Order]] = {}
    for o in orders.values():
        by_window.setdefault(o.window, []).append(o)
    for window in sorted(set(by_window) | set(settlements)):
        rows = by_window.get(window, [])
        cost = {"UP": D(0), "DOWN": D(0)}
        qty = {"UP": D(0), "DOWN": D(0)}
        for o in rows:
            if o.terminal_state != FILLED:
                continue
            try:
                q, px = D(o.final_qty), D(o.avg_px)
            except BaseException:                              # noqa: BLE001
                continue
            side = o.side if o.side in cost else "UP"
            qty[side] += q
            # OWN price, not wire. A DOWN leg is a SELL of YES resting on the
            # ask, so its own cost is `1 - avg_px`; settlement reports own
            # cost, and comparing a wire price against it is the complement
            # error. `up_cost` reconciled and `dn_cost` did not, which is
            # exactly the signature of getting this backwards.
            cost[side] += q * (px if side == "UP" else (D(1) - px))
        s = settlements.get(window) or {}
        rec = {"window": window, "orders": len(rows),
               "filled": sum(1 for o in rows if o.terminal_state == FILLED),
               "cancelled": sum(1 for o in rows
                                if o.terminal_state == CANCELED),
               "our_up_qty": str(qty["UP"]), "our_dn_qty": str(qty["DOWN"]),
               "our_up_cost": str(cost["UP"].quantize(D("0.000001"))),
               "our_dn_cost": str(cost["DOWN"].quantize(D("0.000001"))),
               "settled_up_qty": str(s.get("up_qty") or ""),
               "settled_dn_qty": str(s.get("dn_qty") or ""),
               "settled_up_cost": str(s.get("up_cost") or ""),
               "settled_dn_cost": str(s.get("dn_cost") or ""),
               "settled_matched_pnl": str(s.get("matched_pnl") or ""),
               "settled_residual_pnl": str(s.get("residual_pnl") or ""),
               "resolution_source": str(s.get("resolution_source") or "")}
        rec["reconciles"] = bool(s) and all(
            _eq(rec["our_%s" % k], rec["settled_%s" % k])
            for k in ("up_qty", "dn_qty", "up_cost", "dn_cost"))
        out.append(rec)
    return out


def pairs(orders: Dict[str, Order],
          settlements: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """First leg, second leg, and what happened between them.

    The OPENING leg is the first order in the window to fill. A COMPLETION
    leg is a later order on the opposite side. What matters for pair
    throughput is the gap between the opening FILL and the completion QUOTE
    -- the time we spent holding one-sided exposure before even asking for
    the other side -- and then the completion's own rest.

    Realized pair cost is taken from the SETTLEMENT, in own-price terms:
    `(up_cost + dn_cost) / matched_qty`. That is the venue's number, not a
    modelled one, and it is what the pair ceiling should be judged against.
    """
    out = []
    by_window: Dict[str, List[Order]] = {}
    for o in orders.values():
        by_window.setdefault(o.window, []).append(o)
    for window in sorted(by_window):
        rows = sorted(by_window[window], key=lambda o: o.quote_line_at or 0)
        fills = [o for o in rows if o.terminal_state == FILLED]
        opening = fills[0] if fills else None
        completion = None
        if opening is not None:
            for o in rows:
                if o is opening or o.side == opening.side:
                    continue
                if (o.quote_line_at or 0) >= (opening.quote_line_at or 0):
                    completion = o
                    break
        s = settlements.get(window) or {}
        pair_cost = ""
        try:
            mq = D(str(s.get("matched_qty") or 0))
            if mq > 0:
                pair_cost = str(((D(str(s["up_cost"])) + D(str(s["dn_cost"])))
                                 / mq).quantize(D("0.0001")))
        except BaseException:                                  # noqa: BLE001
            pair_cost = ""
        rec = {"window": window,
               "open_side": opening.side if opening else "",
               "open_px": opening.own_px if opening else "",
               "open_rest_s": opening.rest_s if opening else None,
               "completion_side": completion.side if completion else "",
               "completion_px": completion.own_px if completion else "",
               "completion_state": (completion.terminal_state
                                    if completion else "NO_COMPLETION_ORDER"),
               "completion_rest_s": completion.rest_s if completion else None,
               "hold_before_completion_quote_s": None,
               "realized_pair_cost": pair_cost,
               "matched_qty": str(s.get("matched_qty") or ""),
               "residual_qty": str(s.get("residual_qty") or ""),
               "residual_side": str(s.get("residual_side") or "")}
        if (opening is not None and completion is not None
                and opening.terminal_at and completion.quote_line_at):
            rec["hold_before_completion_quote_s"] = round(
                completion.quote_line_at - opening.terminal_at, 3)
        out.append(rec)
    return out


def _eq(a: str, b: str) -> bool:
    try:
        return D(a or 0) == D(b or 0)
    except BaseException:                                      # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tapes", default=DEFAULT_TAPES,
                    help="glob(s), comma separated")
    ap.add_argument("--json", default="", help="write the rows here")
    a = ap.parse_args()
    root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    pats = [p if os.path.isabs(p) else os.path.join(root, p)
            for p in a.tapes.split(",")]
    got = reconstruct(pats)
    orders: Dict[str, Order] = got["orders"]
    rows = [o.row() for o in sorted(orders.values(),
                                    key=lambda o: (o.window,
                                                   o.quote_at or 0))]
    print("events read: %d   own orders: %d   shadow coids excluded: %d   "
          "unparseable lines: %d"
          % (got["events_read"], len(orders),
             got["excluded"]["shadow_coids"], got["excluded"]["unparseable"]))
    hdr = ("window          side own_px  qty  fill  dist q_ahead  log_lag "
           "rest_s  mixed  state      steps")
    print()
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print("%-15s %-4s %-7s %-4s %-5s %-4s %-8s %-7s %-6s %-6s %-10s %d%s"
              % (r["window"], r["side"], r["own_px"], r["qty"],
                 r["filled_qty"], r["dist_ticks"],
                 r["queue_ahead"] if r["queue_ahead"] is not None else "-",
                 r["log_lag_s"] if r["log_lag_s"] is not None else "-",
                 r["rest_s"] if r["rest_s"] is not None else "-",
                 r["dur_from_quote_mixed_s"]
                 if r["dur_from_quote_mixed_s"] is not None else "-",
                 r["state"].replace("ORDER_STATE_", ""), r["fill_steps"],
                 "  PARTIAL" if r["partial"] else ""))
    print()
    print("per-window reconciliation against L5_LIVE_SETTLED")
    hdr2 = ("window          ord fill canc  our_up/dn qty    settled up/dn   "
            "our cost up/dn        settled cost up/dn     ok")
    print(hdr2)
    print("-" * len(hdr2))
    for rc in reconcile(orders, got["settlements"]):
        print("%-15s %3d %4d %4d  %-7s %-7s %-7s %-7s %-10s %-10s %-10s %-10s %s"
              % (rc["window"], rc["orders"], rc["filled"], rc["cancelled"],
                 rc["our_up_qty"], rc["our_dn_qty"],
                 rc["settled_up_qty"] or "-", rc["settled_dn_qty"] or "-",
                 rc["our_up_cost"], rc["our_dn_cost"],
                 rc["settled_up_cost"] or "-", rc["settled_dn_cost"] or "-",
                 "YES" if rc["reconciles"] else "NO"))
    print()
    print("first leg / second leg, and the pair the venue actually settled")
    hdr3 = ("window          open      rest_s  hold_s  completion      rest_s "
            "state      pair_cost matched resid")
    print(hdr3)
    print("-" * len(hdr3))
    partials = sum(1 for r in rows if r["partial"])
    steps = sorted({r["fill_steps"] for r in rows
                    if r["state"] == "ORDER_STATE_FILLED"})
    for pr in pairs(orders, got["settlements"]):
        print("%-15s %-4s %-5s %-7s %-7s %-4s %-5s %-6s %-10s %-9s %-7s %s"
              % (pr["window"], pr["open_side"], pr["open_px"],
                 pr["open_rest_s"] if pr["open_rest_s"] is not None else "-",
                 pr["hold_before_completion_quote_s"]
                 if pr["hold_before_completion_quote_s"] is not None else "-",
                 pr["completion_side"] or "-", pr["completion_px"] or "-",
                 pr["completion_rest_s"]
                 if pr["completion_rest_s"] is not None else "-",
                 pr["completion_state"].replace("ORDER_STATE_", ""),
                 pr["realized_pair_cost"] or "-",
                 pr["matched_qty"] or "-",
                 "%s %s" % (pr["residual_qty"], pr["residual_side"] or "")))
    print()
    print("PARTIAL FILLS: %d of %d fills. cum_qty step counts seen: %s"
          % (partials, sum(1 for r in rows
                           if r["state"] == "ORDER_STATE_FILLED"), steps))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"orders": rows,
                       "reconciliation": reconcile(orders,
                                                   got["settlements"]),
                       "pairs": pairs(orders, got["settlements"])},
                      fh, indent=1)
        print("\nwrote", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
