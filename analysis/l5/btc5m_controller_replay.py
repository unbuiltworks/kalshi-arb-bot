#!/usr/bin/env python3
"""Replay of src.l5_btc5m_controller over recorded BTC 5-minute quotes.

Research only: no network, no orders, no credentials. Every number printed
is a REPLAY number with HYPOTHETICAL fills. None of it is realized profit.

INPUT. One or more JSONL paths. A line is either a quote
    {"slug", "t", "bid", "ask", "to_beat", "closed"}    YES best bid/ask,
                                                        t = seconds into window
or a settlement
    {"slug", "settled": "[\\"1\\",\\"0\\"]"}              first "1" = UP won
optionally prefixed by "BTC5M_QUOTE " / "BTC5M_SETTLED " (log lines).

WINDOWS USED. Those with a settlement and samples from t <= 30 to t >= 240.

THE STEP. Samples in time order. On each sample resting buys are first
tested for a fill against that sample's book, then decide() is called if
at least 5 s have passed since the last call: book age 0, p_up=None, so the
probability is the book mid and is NOT independent of the price paid. The
decision's CANCEL and PLACE orders are applied; a new order can first fill
on the NEXT sample.

FILL MODEL. A resting buy fills in full when its side's ask is seen
  through   strictly below the order price, or
  touch     at or below it (reported separately, more generous).
Queue position, partial fills and our own market impact are not modelled.
At window end the inventory settles at $1 per winning share, maker fee 0.

    python3 analysis/l5/btc5m_controller_replay.py quotes.jsonl [more.jsonl]
"""
from __future__ import annotations

import collections
import json
import os
import sys
from decimal import Decimal, InvalidOperation

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src import l5_btc5m_controller as C  # noqa: E402

D = Decimal
LABEL = "REPLAY, hypothetical fills, not realized"
PREFIXES = ("BTC5M_QUOTE ", "BTC5M_SETTLED ")
ADVERSE_GRID = (D("0.06"), D("0.03"), D("0.0"))
MIN_EDGE = D("0.01")
DECIDE_EVERY_S = 5.0
COVER_FROM_S, COVER_TO_S = 30.0, 240.0


def _dec(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        d = D(str(x))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _winner(settled):
    """"UP" / "DOWN" from the settled field, or None when it is not clear."""
    try:
        v = json.loads(settled) if isinstance(settled, str) else settled
        first, second = str(v[0]), str(v[1])
    except (ValueError, TypeError, IndexError, KeyError):
        return None
    if (first, second) == ("1", "0"):
        return C.UP
    if (first, second) == ("0", "1"):
        return C.DOWN
    return None


def load(paths):
    """-> ({slug: [sample]}, {slug: winner}, lines that could not be read)."""
    quotes, winners, bad = collections.defaultdict(list), {}, 0
    for path in paths:
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError as e:
            print("cannot read %s: %s" % (path, e), file=sys.stderr)
            continue
        with fh:
            for line in fh:
                for prefix in PREFIXES:
                    if prefix in line:
                        line = line.split(prefix, 1)[1]
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if not isinstance(rec, dict) or not rec.get("slug"):
                    bad += 1
                    continue
                if "settled" in rec:
                    w = _winner(rec["settled"])
                    if w:
                        winners[rec["slug"]] = w
                    continue
                try:
                    t = float(rec["t"])
                except (KeyError, TypeError, ValueError):
                    bad += 1
                    continue
                if rec.get("closed") or t != t:
                    continue
                quotes[rec["slug"]].append(
                    {"t": t, "bid": _dec(rec.get("bid")), "ask": _dec(rec.get("ask"))})
    return quotes, winners, bad


def usable_windows(quotes, winners):
    out = []
    for slug in sorted(quotes):
        samples = sorted(quotes[slug], key=lambda s: s["t"])
        if slug in winners and samples[0]["t"] <= COVER_FROM_S and samples[-1]["t"] >= COVER_TO_S:
            out.append((slug, samples, winners[slug]))
    return out


def simulate(samples, winner, params, fill_rule):
    """One window under one fill rule. Returns its counters and settled P&L."""
    inv = {C.UP: {"qty": D("0"), "cost": D("0")}, C.DOWN: {"qty": D("0"), "cost": D("0")}}
    resting, n_id, last_t = [], 0, None
    st = {"actions": collections.Counter(), "holds": collections.Counter(),
          "placed": 0, "cancelled": 0, "fills": 0, "fill_qty": D("0"),
          "fill_cost": D("0"), "ignored_non_maker": 0}
    for s in samples:
        t, bid, ask = s["t"], s["bid"], s["ask"]
        # 1. Fills, against orders placed on an earlier sample.
        ask_of = {C.UP: ask, C.DOWN: (D("1") - bid) if bid is not None else None}
        still = []
        for o in resting:
            a = ask_of[o["side"]]
            hit = a is not None and (a < o["price"] if fill_rule == "through" else a <= o["price"])
            if hit:
                inv[o["side"]]["qty"] += o["qty"]
                inv[o["side"]]["cost"] += o["price"] * o["qty"]
                st["fills"] += 1
                st["fill_qty"] += o["qty"]
                st["fill_cost"] += o["price"] * o["qty"]
            else:
                still.append(o)
        resting = still
        # 2. A decision at most every 5 s.
        if last_t is not None and t - last_t < DECIDE_EVERY_S:
            continue
        last_t = t
        d = C.decide(
            book={"bid": bid, "ask": ask, "age_s": 0.0}, t_in_window_s=t,
            inventory={k: dict(v) for k, v in inv.items()},
            resting=[{"id": o["id"], "side": o["side"], "price": o["price"],
                      "qty": o["qty"], "age_s": max(0.0, t - o["placed_t"])} for o in resting],
            params=params, p_up=None)
        st["actions"][d["action"]] += 1
        if d["action"] == C.HOLD_OR_SKIP:
            st["holds"][d["why"]] += 1
        for o in d["orders"]:
            if o["op"] == "CANCEL":
                before = len(resting)
                resting = [r for r in resting if r["id"] != o["id"]]
                st["cancelled"] += before - len(resting)
            elif o["op"] == "PLACE":
                if not o.get("post_only") or o.get("intent", "BUY") != "BUY":
                    st["ignored_non_maker"] += 1      # this replay is maker-only
                    continue
                n_id += 1
                resting.append({"id": "o%d" % n_id, "side": o["side"], "price": o["price"],
                                "qty": o["qty"], "placed_t": t})
                st["placed"] += 1
    # 3. Settle: $1 per winning share, everything paid is gone. Maker fee 0.
    cost = inv[C.UP]["cost"] + inv[C.DOWN]["cost"]
    st["pnl"] = inv[winner]["qty"] - cost
    st["unfilled_at_end"] = len(resting)
    st["unmatched_qty"] = abs(inv[C.UP]["qty"] - inv[C.DOWN]["qty"])
    return st


def _counts(counter):
    return "  ".join("%s=%d" % kv for kv in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))) or "none"


def report(windows, adverse, fill_rule):
    params = C.Params(adverse_selection_per_share=adverse, min_edge_per_share=MIN_EDGE)
    runs = [simulate(samples, winner, params, fill_rule) for _, samples, winner in windows]
    actions, holds = collections.Counter(), collections.Counter()
    for r in runs:
        actions.update(r["actions"])
        holds.update(r["holds"])
    pnls = [r["pnl"] for r in runs]
    n = len(pnls)
    total = sum(pnls, D("0"))
    ahead = sum(1 for p in pnls if p > 0)
    print("--- adverse_selection_per_share=%s  min_edge=%s  fill=%s  [%s] ---"
          % (adverse, MIN_EDGE, fill_rule, LABEL))
    print("  decisions: %d  %s" % (sum(actions.values()), _counts(actions)))
    print("  hold reasons: %s" % _counts(holds))
    print("  orders placed=%d cancelled=%d unfilled_at_window_end=%d  hypothetical fills=%d (qty %s, cost $%s)"
          % (sum(r["placed"] for r in runs), sum(r["cancelled"] for r in runs),
             sum(r["unfilled_at_end"] for r in runs), sum(r["fills"] for r in runs),
             sum((r["fill_qty"] for r in runs), D("0")),
             sum((r["fill_cost"] for r in runs), D("0")).quantize(D("0.0001"))))
    print("  windows with a fill=%d  unmatched qty at settlement=%s"
          % (sum(1 for r in runs if r["fills"]), sum((r["unmatched_qty"] for r in runs), D("0"))))
    if n:
        q = D("0.0001")
        print("  per-window replay P&L (not realized): total $%s  mean $%s  min $%s  max $%s  ahead %d/%d (%.1f%%)"
              % (total.quantize(q), (total / n).quantize(q), min(pnls).quantize(q),
                 max(pnls).quantize(q), ahead, n, 100.0 * ahead / n))
    else:
        print("  per-window replay P&L (not realized): no windows")


def main(argv):
    os.environ[C.CONTROLLER_ENV] = "ON"      # replay only: nothing here can place an order
    quotes, winners, bad = load(argv)
    windows = usable_windows(quotes, winners)
    print("windows used: %d  (slugs with quotes %d, settlements %d, unreadable lines %d)"
          % (len(windows), len(quotes), len(winners), bad))
    print("%s. p_up = book mid (not an independent estimate); decide() at most every %g s; "
          "windows need a settlement and samples from t<=%g to t>=%g."
          % (LABEL, DECIDE_EVERY_S, COVER_FROM_S, COVER_TO_S))
    if not argv:
        print("usage: btc5m_controller_replay.py QUOTES.jsonl [MORE.jsonl ...]")
    for adverse in ADVERSE_GRID:
        for fill_rule in ("through", "touch"):
            report(windows, adverse, fill_rule)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
