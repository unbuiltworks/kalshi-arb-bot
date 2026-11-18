"""Five stranded-leg actions scored against HOLD, on executable prices and
actual settlement. READ-ONLY. Nothing here is deployed or armed.

GROUND TRUTH, all of it the venue's:
  - 290 `L5_RESIDUAL_BOOK_SAMPLE` frames with FULL recorded ladders, emitted
    by the live engine while inventory was unmatched (data/l5_strand_replay).
  - the eight realized residual episodes, their own cost and realized maker
    completion (src/l5_strand_policy_replay.EPISODES).
  - the venue's settlement rows, including `yes_value`
    (data/l5/ops/btc15m_settled_20261012_20261105.jsonl).

`yes_value` is what makes HOLD measurable rather than modelled: holding a
residual to settlement pays exactly q*yes_value on UP and q*(1-yes_value)
on DOWN. No distribution is assumed and no probability is fitted.

WHAT IS MODELLED, and the direction of each assumption:
  - REDUCE and TAKER_COMPLETE walk the RECORDED ladder for the whole
    residual and are refused if displayed depth is short. Displayed is
    displayed, never guaranteed, so both are OPTIMISTIC about fills -- which
    biases AGAINST hold, and is stated rather than hidden.
  - the published taker fee theta*C*p*(1-p) is charged on every crossing.
  - MAKER_COMPLETE is not simulated: where a window actually completed as a
    maker, its REALIZED fill and realized pair cost are used as-is.
  - PARTIAL completes only what the top rung holds, then settles the rest.
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D

sys.path.insert(0, os.getcwd())
from src import l5_strand_policy_replay as R          # noqa: E402

SETTLED = os.path.join("data", "l5", "ops",
                       "btc15m_settled_20261012_20261105.jsonl")
PREFIX = "cpc-btc-updown-15m-2026-10-25-"
MAX_ROW_AGE_S = 0.5


def yes_values():
    out = {}
    with open(SETTLED) as fh:
        for line in fh:
            r = json.loads(line)
            m = str(r.get("market") or "")
            if m.startswith(PREFIX) and r.get("yes_value") is not None:
                out[m.rsplit("-", 1)[-1]] = D(str(r["yes_value"]))
    return out


def hold_pnl(ep, yv):
    """Settled P&L of holding the residual. The venue's own number."""
    per = yv if ep["held"] == "UP" else (D(1) - yv)
    return ep["q"] * (per - ep["own"])


def sell_walk(frame, held, qty):
    """(vwap_own_proceeds, worst) selling the HELD leg into the ladder.

    Holding UP we sell UP and hit the BID, so proceeds are the wire bid.
    Holding DOWN we sell DOWN, whose bid is 1 - the UP ask. This is the
    mirror of `R.walk_for_qty`, which BUYS the complement.
    """
    rungs = []
    if held == "UP":
        for p, q in frame.get("bids") or []:
            rungs.append((D(str(p)), D(str(q))))
    else:
        for p, q in frame.get("asks") or []:
            rungs.append((D(1) - D(str(p)), D(str(q))))
    need, got, worst = qty, D(0), None
    for px, avail in rungs:
        if need <= 0:
            break
        take = min(need, avail)
        got += px * take
        need -= take
        worst = px
    if need > 0 or worst is None:
        return None
    return (got / qty, worst)


def first_fresh(fs):
    for f in fs:
        age = f.get("row_age_s")
        if age is not None and abs(float(age)) <= MAX_ROW_AGE_S:
            return f
    return None


def reduce_pnl(ep, fs):
    """Sell the residual at the first fresh frame. Risk-aware reduction."""
    f = first_fresh(fs)
    if f is None:
        return None, "NO_FRESH_FRAME"
    w = sell_walk(f, ep["held"], ep["q"])
    if w is None:
        return None, "DEPTH_SHORT"
    vwap, _ = w
    pnl = ep["q"] * (vwap - ep["own"]) - R.taker_fee(ep["q"], vwap)
    return pnl, "sold %s @ %s" % (ep["q"], round(vwap, 4))


def taker_complete_pnl(ep, fs):
    """Cross for the whole residual at the first fresh frame with depth."""
    for f in fs:
        age = f.get("row_age_s")
        if age is None or abs(float(age)) > MAX_ROW_AGE_S:
            continue
        if f.get("walk_short") is True:
            continue
        w = R.walk_for_qty(f, ep["held"], ep["q"])
        if w is None:
            continue
        vwap, _ = w
        pair = ep["own"] + vwap
        pnl = ep["q"] * (D(1) - pair) - R.taker_fee(ep["q"], vwap)
        return pnl, "pair %s" % round(pair, 4)
    return None, "NO_EXECUTABLE_FRAME"


def partial_pnl(ep, fs, yv):
    """Complete only what the TOP RUNG holds; settle the remainder."""
    f = first_fresh(fs)
    if f is None:
        return None, "NO_FRESH_FRAME"
    rung = ((f.get("bids") or [None])[0] if ep["held"] == "UP"
            else (f.get("asks") or [None])[0])
    if not rung:
        return None, "NO_TOUCH"
    own = (D(1) - D(str(rung[0])) if ep["held"] == "UP"
           else D(str(rung[0])))
    avail = D(str(rung[1]))
    q = min(ep["q"], avail)
    if q <= 0:
        return None, "NO_SIZE"
    pair = ep["own"] + own
    pnl = q * (D(1) - pair) - R.taker_fee(q, own)
    rest = ep["q"] - q
    if rest > 0:
        per = yv if ep["held"] == "UP" else (D(1) - yv)
        pnl += rest * (per - ep["own"])
    return pnl, "completed %s of %s at pair %s" % (q, ep["q"], round(pair, 4))


def main():
    yv = yes_values()
    frames = R.load_frames()
    eps = [e for e in R.EPISODES if e["held"] is not None]
    cols = ["HOLD", "MAKER_COMPLETE", "TAKER_COMPLETE", "REDUCE", "PARTIAL"]
    tot = {c: D(0) for c in cols}
    miss = {c: 0 for c in cols}
    print("%-7s %-5s %-6s %-6s %-6s | %9s %9s %9s %9s %9s"
          % ("window", "held", "q", "own", "yes", *cols))
    rows = []
    for ep in eps:
        v = yv[ep["w"]]
        fs = R.episode_frames(frames, ep)
        h = hold_pnl(ep, v)
        m = R.baseline_pnl(ep)
        tk, tk_why = taker_complete_pnl(ep, fs)
        rd, rd_why = reduce_pnl(ep, fs)
        pt, pt_why = partial_pnl(ep, fs, v)
        got = {"HOLD": h, "MAKER_COMPLETE": m, "TAKER_COMPLETE": tk,
               "REDUCE": rd, "PARTIAL": pt}
        for c in cols:
            if got[c] is None:
                miss[c] += 1
            else:
                tot[c] += got[c]
        rows.append((ep, v, got, {"TAKER_COMPLETE": tk_why,
                                  "REDUCE": rd_why, "PARTIAL": pt_why}))
        print("%-7s %-5s %-6s %-6s %-6s | %9s %9s %9s %9s %9s"
              % (ep["w"], ep["held"], ep["q"], ep["own"], v,
                 *[("--" if got[c] is None else str(round(got[c], 4)))
                   for c in cols]))
    print("-" * 94)
    print("%-33s | %9s %9s %9s %9s %9s"
          % ("TOTAL (7 episodes)", *[str(round(tot[c], 4)) for c in cols]))
    print("%-33s | %9s %9s %9s %9s %9s"
          % ("episodes not executable", *[str(miss[c]) for c in cols]))
    print()
    best = max(cols, key=lambda c: tot[c])
    print("BEST BY TOTAL: %s (%s). HOLD = %s, so the margin is %s."
          % (best, round(tot[best], 4), round(tot["HOLD"], 4),
             round(tot[best] - tot["HOLD"], 4)))
    print()
    print("PER-EPISODE: where does HOLD win?")
    for ep, v, got, _ in rows:
        others = {c: got[c] for c in cols if c != "HOLD"
                  and got[c] is not None}
        bc = max(others, key=lambda c: others[c])
        verdict = "HOLD" if got["HOLD"] >= others[bc] else bc
        print("  %-7s %-5s won by %-15s (hold %8s vs best-action %8s)"
              % (ep["w"], ep["held"], verdict, round(got["HOLD"], 4),
                 round(others[bc], 4)))
    print()
    print("WHY EACH ACTION WAS OR WAS NOT EXECUTABLE:")
    for ep, _, _, why in rows:
        print("  %-7s %-5s %s" % (ep["w"], ep["held"], why))


if __name__ == "__main__":
    main()


def stats():
    """Is the MAKER_COMPLETE margin over HOLD distinguishable from noise?

    n=7 paired episodes, one day, one asset. A total is not a frequency, so
    both are reported: a sign test on who wins each episode, and a paired
    bootstrap on the total.
    """
    import random
    yv, frames = yes_values(), R.load_frames()
    eps = [e for e in R.EPISODES if e["held"] is not None]
    diffs = [float(R.baseline_pnl(e) - hold_pnl(e, yv[e["w"]])) for e in eps]
    wins = sum(1 for d in diffs if d > 1e-9)
    loss = sum(1 for d in diffs if d < -1e-9)
    ties = len(diffs) - wins - loss
    print()
    print("=== IS THE MARGIN REAL? MAKER_COMPLETE - HOLD, per episode ===")
    for e, d in zip(eps, diffs):
        print("  %-7s %-5s %+8.4f" % (e["w"], e["held"], d))
    print("  sign test: maker wins %d, holds wins %d, ties %d  -> %s"
          % (wins, loss, ties,
             "INCONCLUSIVE" if min(wins, loss) >= 2 else "suggestive"))
    random.seed(20261118)
    tots = []
    for _ in range(20000):
        s = sum(random.choice(diffs) for _ in diffs)
        tots.append(s)
    tots.sort()
    lo, hi = tots[int(0.025 * len(tots))], tots[int(0.975 * len(tots))]
    print("  observed total margin %+.4f" % sum(diffs))
    print("  paired bootstrap 95%% CI [%+.4f, %+.4f] -> %s"
          % (lo, hi, "EXCLUDES zero" if lo > 0 or hi < 0
             else "INCLUDES zero, so NOT significant"))
    print()
    print("=== THE IDENTITY: why REDUCE == TAKER_COMPLETE to the cent ===")
    print("  holding UP at own cost c, at a bid b:")
    print("    complete: buy DOWN at own (1-b); pair redeems $1")
    print("              -> 1 - c - (1-b)  =  b - c")
    print("    reduce:   sell UP at b      ->  b - c")
    print("  and the fee theta*C*p*(1-p) is symmetric about p=0.5, so")
    print("  p=(1-b) and p=b carry the SAME fee. Identical, not a bug.")
