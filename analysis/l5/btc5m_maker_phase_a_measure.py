"""PHASE A measurement. Reads the read-only tape; places nothing.

THE SIX QUANTITIES, and the observable each rests on. Everything here is
derived from the venue's own book/quote/settlement fields; where a number
cannot be observed it is reported as unavailable rather than modelled.

SIDE CONVENTION. The venue quotes ONE side (call it UP) and the
complement is 1 - p. So:
    an UP  bid at X  is reached when the UP best bid falls to <= X
    a DOWN bid at X  IS an UP offer at 1-X, and is reached when the UP
                     best ask rises to >= 1-X
Both follow from observables only; neither needs a model.

(1) REACH, per side: did the price path bring our rung to the top of
    book at all. This is the loosest possible test and is reported
    separately precisely BECAUSE it is not a fill.

(2) BOTH-REACH: both rungs reached in the same window. For a symmetric
    pair at X this needs the UP price to visit <= X and >= 1-X in one
    300-second window.

(3) QUEUE AHEAD AT INSERTION: the resting quantity at our exact price in
    the FIRST BOOK THE VENUE WOULD SERVE US. Measured there and not
    later, because that is the queue our order would actually have
    joined. It is NOT t=0: the venue does not create a BTC5m market
    until at or after its nominal open, so the insertion moment is a
    measured quantity and is reported as one.

(4) QUEUE-ADJUSTED FILL, conservative. A touch is NOT a fill. Our order
    fills only once the queue ahead of it has been consumed by observed
    trading. The consuming volume is `sharesTraded` differenced between
    samples, counted ONLY while the last trade price is at or beyond our
    rung -- trades away from our level cannot consume it. FIFO: the
    queue ahead must clear before any of our clip fills, and our clip
    fills in full only after `queue_ahead + our_qty` has traded.
    CANCELLATIONS ARE NOT CREDITED in the conservative figure. An
    optimistic variant that credits observed shrinkage of the resting
    quantity at our level is reported alongside and labelled, because
    queue relief we cannot attribute to a cancellation is not evidence.

(5) P(filled unmatched leg wins): of the windows where the model fills
    exactly one side, how often did that side settle as the winner.
    This is the adverse-selection term the economics turn on, and it is
    the whole reason a touch may not be credited as a fill.

(6) SETTLEMENT-ADJUSTED NET: pair cost where both legs fill, the whole
    outlay lost where a lone leg loses, $1.00 per winning contract.

SETTLEMENT IS A TERMINAL MARK, NOT A BINARY PAYOUT. Observed values are
0.0, 0.01 and 0.99 -- a binary payout would be exactly 0 or 1, so the
field is the UP side's terminal value and it tracks the final quote. The
win test is therefore a threshold, and any settlement strictly inside
0.1..0.9 is AMBIGUOUS: it is counted, reported, and EXCLUDED from (5)
and (6) rather than silently resolved by rounding.

OUT-OF-SAMPLE. Windows are ordered chronologically and split in half.
The first half is reported as in-sample and the second as out-of-sample,
and both are printed. No window is excluded for being unfavourable.
"""
import collections, json, os, statistics, sys

D = os.environ.get("PHASE_A_DIR") or "/home/user/kalshi-arb-bot/data/l5/phase_a"
TAPE = os.path.join(D, "btc5m_books.jsonl")
WINS = os.path.join(D, "btc5m_books_windows.jsonl")
RUNGS = [float(x) for x in (os.environ.get("PHASE_A_RUNGS") or "0.40,0.42,0.47").split(",")]
CLIP = 2.00
#: The insertion moment is whenever the venue first served the market, so
#: there is no cutoff of our choosing WITHIN a window. But a window the
#: collector only picked up late is a different and systematically worse
#: sample -- joining after the price has moved fills only the side the
#: move is running through -- so such windows are excluded wholesale.
#: Measured on five of them: insertion at a median of t+136s, and the
#: lone filled leg won 0 of 4 times.
INSERT_BY_T = None
MAX_JOIN_T = 30


def load():
    byw = collections.defaultdict(list)
    for line in open(TAPE):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        byw[r["slug"]].append(r)
    for s in byw:
        byw[s].sort(key=lambda r: r["t"])
    settle = {}
    if os.path.exists(WINS):
        for line in open(WINS):
            try:
                w = json.loads(line)
            except Exception:
                continue
            if w.get("settlement_px") is not None:
                settle[w["slug"]] = float(w["settlement_px"])
    return byw, settle


def qty_at(levels, price, eps=1e-9):
    for p, q in levels:
        if abs(p - price) < eps:
            return q
    return 0.0


def evaluate(rs, X, side):
    """One rung, one side, one window. Returns observables only."""
    ins = rs[0] if rs else None
    if ins is None:
        return None
    if INSERT_BY_T is not None and ins["t"] > INSERT_BY_T:
        return None
    if side == "UP":
        our_px = X
        q0 = qty_at(ins["bids"], round(X, 2))
        reached = any(r["best_bid"] is not None and r["best_bid"] <= X + 1e-9
                      for r in rs)
        #: trades at or below our bid are the only ones that consume it
        def consumes(r):
            return r["last"] is not None and r["last"] <= X + 1e-9
        lvl = lambda r: qty_at(r["bids"], round(X, 2))
    else:
        comp = round(1.0 - X, 2)
        our_px = X
        q0 = qty_at(ins["offers"], comp)
        reached = any(r["best_ask"] is not None and r["best_ask"] >= comp - 1e-9
                      for r in rs)
        def consumes(r):
            return r["last"] is not None and r["last"] >= comp - 1e-9
        lvl = lambda r: qty_at(r["offers"], comp)

    our_qty = CLIP / our_px
    consumed = 0.0
    relief = 0.0
    prev = ins
    fill_t = None
    fill_t_opt = None
    for r in rs:
        if r["t"] <= ins["t"]:
            continue
        dv = 0.0
        if (r["shares_traded"] is not None
                and prev["shares_traded"] is not None):
            dv = max(0.0, r["shares_traded"] - prev["shares_traded"])
        if dv > 0 and consumes(r):
            consumed += dv
        #: queue relief we can SEE: the resting quantity at our own level
        #: fell by more than the trading that could explain it. Reported
        #: separately and never folded into the conservative figure.
        drop = lvl(prev) - lvl(r)
        if drop > 0:
            relief += max(0.0, drop - (dv if consumes(r) else 0.0))
        if fill_t is None and consumed >= q0 + our_qty:
            fill_t = r["t"]
        if fill_t_opt is None and (consumed + relief) >= q0 + our_qty:
            fill_t_opt = r["t"]
        prev = r
    return {"reached": reached, "queue_ahead": q0, "our_qty": our_qty,
            "consumed": consumed, "relief": relief,
            "fill_t": fill_t, "fill_t_opt": fill_t_opt,
            "filled": fill_t is not None,
            "filled_opt": fill_t_opt is not None}


def report(byw, settle, slugs, label):
    print("=" * 74)
    print("%s -- %d windows" % (label, len(slugs)))
    if not slugs:
        return
    print()
    for X in RUNGS:
        rows = []
        for s in slugs:
            up = evaluate(byw[s], X, "UP")
            dn = evaluate(byw[s], X, "DOWN")
            if up and dn:
                rows.append((s, up, dn))
        if not rows:
            print("  X=%.2f: no window had an observed insertion moment" % X)
            continue
        n = len(rows)
        ru = sum(1 for _s, u, _d in rows if u["reached"])
        rd = sum(1 for _s, _u, d in rows if d["reached"])
        rb = sum(1 for _s, u, d in rows if u["reached"] and d["reached"])
        fu = sum(1 for _s, u, _d in rows if u["filled"])
        fd = sum(1 for _s, _u, d in rows if d["filled"])
        fb = sum(1 for _s, u, d in rows if u["filled"] and d["filled"])
        fb_o = sum(1 for _s, u, d in rows if u["filled_opt"] and d["filled_opt"])
        qu = [u["queue_ahead"] for _s, u, _d in rows]
        qd = [d["queue_ahead"] for _s, _u, d in rows]
        oq = rows[0][1]["our_qty"]
        print("  X=%.2f  (pair cost %.2f, our clip %.2f contracts/side)"
              % (X, 2 * X, oq))
        print("    (1) REACH          UP %5.1f%%   DOWN %5.1f%%" %
              (100.0 * ru / n, 100.0 * rd / n))
        print("    (2) BOTH REACH     %5.1f%%   <- a touch, NOT a fill" %
              (100.0 * rb / n))
        print("    (3) QUEUE AHEAD at insertion, contracts:")
        print("          UP   median %8.1f  p90 %8.1f  = %6.1fx our clip"
              % (statistics.median(qu), sorted(qu)[int(.9 * n) - 1] if n > 1 else qu[0],
                 statistics.median(qu) / oq if oq else 0))
        print("          DOWN median %8.1f  p90 %8.1f  = %6.1fx our clip"
              % (statistics.median(qd), sorted(qd)[int(.9 * n) - 1] if n > 1 else qd[0],
                 statistics.median(qd) / oq if oq else 0))
        print("    (4) QUEUE-ADJUSTED FILL (conservative, no cancel credit):")
        print("          UP %5.1f%%   DOWN %5.1f%%   BOTH %5.1f%%"
              % (100.0 * fu / n, 100.0 * fd / n, 100.0 * fb / n))
        print("          BOTH, crediting observed queue relief: %5.1f%%"
              % (100.0 * fb_o / n))
        # (5) and (6) need settlement
        lone = []
        pairs = []
        net = 0.0
        acc = 0
        ambiguous = 0
        for s, u, d in rows:
            sp = settle.get(s)
            if sp is None:
                continue
            if 0.1 < sp < 0.9:
                #: neither side clearly settled; resolving it by rounding
                #: would invent the very quantity (5) measures
                ambiguous += 1
                continue
            acc += 1
            up_won = sp >= 0.5
            if u["filled"] and d["filled"]:
                pairs.append(2 * X)
                net += 1.00 * oq - 2 * X * oq
            elif u["filled"]:
                lone.append(up_won)
                net += (1.00 * oq if up_won else 0.0) - X * oq
            elif d["filled"]:
                lone.append(not up_won)
                net += (1.00 * oq if not up_won else 0.0) - X * oq
        if ambiguous:
            print("    settlement AMBIGUOUS (0.1<px<0.9) in %d window(s):"
                  " excluded from (5) and (6)" % ambiguous)
        if acc == 0:
            print("    (5)(6) no unambiguously settled window yet")
        else:
            if lone:
                print("    (5) P(lone filled leg WINS) = %5.1f%%  (n=%d)"
                      " vs its price %.2f" % (100.0 * sum(lone) / len(lone),
                                              len(lone), X))
            else:
                print("    (5) no lone-leg fill observed yet")
            print("    (6) settled net over %d settled windows: $%+.4f"
                  " (%.4f/window)" % (acc, net, net / acc))
            print("        complete pairs %d at cost %.2f; lone legs %d"
                  % (len(pairs), 2 * X, len(lone)))
        print()


def main():
    byw, settle = load()
    #: only windows we watched from the start are usable: a window whose
    #: insertion moment we missed cannot have a queue-at-insertion.
    #: JOIN TIME, not first-available time. A window the venue served late
    #: is still an unbiased sample if we were polling from its open; a
    #: window WE picked up late is not. Rows written before `join_t`
    #: existed fall back to the first sample's t.
    def jt(s):
        v = byw[s][0].get("join_t")
        return byw[s][0]["t"] if v is None else v
    late = sorted(s for s in byw if byw[s] and jt(s) > MAX_JOIN_T)
    usable = sorted((s for s in byw if byw[s] and jt(s) <= MAX_JOIN_T),
                    key=lambda s: byw[s][0]["epoch"])
    if late:
        print("EXCLUDED %d window(s) WE joined later than t+%ds (biased"
              " sample, not a weaker one): %s"
              % (len(late), MAX_JOIN_T, ", ".join(x[-6:] for x in late[:8])))
    firsts = [byw[s][0]["t"] for s in usable]
    print("tape: %d windows sampled, %d settled" % (len(byw), len(settle)))
    if firsts:
        print("INSERTION MOMENT (first instant the venue served the market):")
        print("  median t+%ds   min t+%ds   max t+%ds   n=%d"
              % (statistics.median(firsts), min(firsts), max(firsts),
                 len(firsts)))
        print("  => an order cannot be resting before this; the queue we")
        print("     join is whatever already rests at that instant.")
    if not usable:
        print("no window yet watched from its open; nothing to measure.")
        return
    half = len(usable) // 2
    report(byw, settle, usable, "ALL USABLE WINDOWS (chronological)")
    if half >= 1:
        report(byw, settle, usable[:half], "IN-SAMPLE (first half)")
        report(byw, settle, usable[half:], "OUT-OF-SAMPLE (second half)")


if __name__ == "__main__":
    main()
