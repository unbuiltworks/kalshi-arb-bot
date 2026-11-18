"""How many maker fills can one BTC5m window actually reach, and what stops it?

THE QUESTION. Production reports `straddle_repeat_enabled: true`,
`straddle_max_side_usd: 0.30`, `straddle_max_unmatched_usd: 0.10`. Those
are the two bounds that REPLACED the one-fill-per-side lockout. Whether
they permit "repeated" fills or merely two is arithmetic, not opinion --
so this drives the real controller instead of reading it.

METHOD. Start flat. Call `C.decide`. Any STRADDLE_LEG it returns is
treated as resting, then as FILLED AT ITS LIMIT PRICE (the optimistic
case for repetition -- a bound that binds here binds harder in reality).
Repeat until the controller stops placing legs, then record the reason.

WHAT THIS IS NOT. Not a profitability claim. Fills are assumed, not
earned; the entry-EV gate's probabilities are inputs here, not
measurements. It answers "how many CAN there be" and nothing about
whether any of them should happen.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

os.environ["L5_BTC5M_CONTROLLER"] = "ON"

from src import l5_btc5m_controller as C      # noqa: E402

BOOK = {"bid": "0.60", "ask": "0.61", "age_s": 0.1}


def params(**kw):
    #: the production-reported effective configuration
    base = dict(entry_mode=C.STRADDLE,
                straddle_repeat_enabled=True,
                straddle_max_side_usd=D("0.30"),
                straddle_max_unmatched_usd=D("0.10"),
                max_exposure_usd=D("5.00"),
                max_resting_entries=4,
                no_new_entry_after_s=270.0,
                straddle_price=D("0.08"),
                clip_usd=D("0.15"),
                hedge_enabled=False, reserve_mode="NONE",
                straddle_p_pair_natural=D("0.070"),
                straddle_p_complete=D("0.131"),
                straddle_p_lone_win=D("0.0981"))
    base.update(kw)
    return C.Params(**base)


def run_window(t=10.0, max_steps=40, **kw):
    """Drive one window to exhaustion. Returns (fills, reason, trace)."""
    inv = {C.UP: {"qty": D("0"), "cost": D("0")},
           C.DOWN: {"qty": D("0"), "cost": D("0")}}
    fills, trace, oid = [], [], 0
    for _ in range(max_steps):
        res = C.decide(book=BOOK, t_in_window_s=t, inventory=inv,
                       resting=[], params=params(**kw))
        legs = [o for o in res["orders"]
                if o.get("reason") == "STRADDLE_LEG"]
        if not legs:
            return fills, res.get("why"), trace
        for o in legs:
            s, p, q = o["side"], D(str(o["price"])), D(str(o["qty"]))
            inv[s] = {"qty": inv[s]["qty"] + q,
                      "cost": inv[s]["cost"] + p * q}
            oid += 1
            fills.append((s, p, q))
            trace.append("fill#%d %s %s x%s" % (oid, s, p, q))
    return fills, "DID_NOT_TERMINATE", trace


def side_counts(fills):
    return {s: sum(1 for f in fills if f[0] == s) for s in (C.UP, C.DOWN)}


if __name__ == "__main__":
    print("=" * 72)
    print("A. production config, clip sweep")
    print("=" * 72)
    print("%-10s %-7s %-22s %s" % ("clip", "fills", "stopped_by", "by side"))
    for clip in ("0.05", "0.10", "0.15", "0.20", "0.30", "0.40"):
        f, why, _ = run_window(clip_usd=D(clip))
        print("%-10s %-7d %-22s %s" % (clip, len(f), why, side_counts(f)))

    print()
    print("=" * 72)
    print("B. which bound binds? raise one at a time, clip 0.15")
    print("=" * 72)
    base_f, base_why, _ = run_window()
    print("baseline                        : %d fills, %s"
          % (len(base_f), base_why))
    for label, kw in (
            ("side_cap 0.30 -> 3.00", {"straddle_max_side_usd": D("3.00")}),
            ("unmatched 0.10 -> 1.00",
             {"straddle_max_unmatched_usd": D("1.00")}),
            ("BOTH raised", {"straddle_max_side_usd": D("3.00"),
                             "straddle_max_unmatched_usd": D("1.00")}),
            ("max_exposure 5.00 -> 50.00",
             {"max_exposure_usd": D("50.00")}),
    ):
        f, why, _ = run_window(**kw)
        print("%-32s: %d fills, %s  %s"
              % (label, len(f), why, side_counts(f)))

    print()
    print("=" * 72)
    print("C. can UP and DOWN rest simultaneously from flat?")
    print("=" * 72)
    res = C.decide(book=BOOK, t_in_window_s=10.0,
                   inventory={C.UP: {"qty": "0", "cost": "0"},
                              C.DOWN: {"qty": "0", "cost": "0"}},
                   resting=[], params=params())
    legs = [o for o in res["orders"] if o.get("reason") == "STRADDLE_LEG"]
    print("legs placed from flat: %d  sides=%s"
          % (len(legs), sorted({o["side"] for o in legs})))
    for o in legs:
        print("   %s @ %s x%s" % (o["side"], o["price"], o["qty"]))
    print("why=%s" % res.get("why"))

    print()
    print("=" * 72)
    print("D. first-step trace at clip 0.15")
    print("=" * 72)
    f, why, tr = run_window()
    for line in tr:
        print("  " + line)
    print("  stopped: %s" % why)


def probe_rebalance():
    """THE SAFETY QUESTION. Holding UP, is the risk-REDUCING DOWN leg
    admitted, or does the same ceiling that blocks opening also block
    closing? A bound that strands inventory is the 1930z failure."""
    print()
    print("=" * 72)
    print("E. lopsided inventory: is the BALANCING leg admitted?")
    print("=" * 72)
    held = D("0.30")          # $0.30 of UP already filled, nothing on DOWN
    inv = {C.UP: {"qty": D("0.5"), "cost": held},
           C.DOWN: {"qty": D("0"), "cost": D("0")}}
    for label, kw in (("production bounds", {}),
                      ("unmatched 1.00", {"straddle_max_unmatched_usd":
                                          D("1.00")})):
        res = C.decide(book=BOOK, t_in_window_s=10.0, inventory=inv,
                       resting=[], params=params(**kw))
        legs = [o for o in res["orders"]
                if o.get("reason") == "STRADDLE_LEG"]
        down = [o for o in legs if o["side"] == C.DOWN]
        up = [o for o in legs if o["side"] == C.UP]
        print("%-20s: legs=%d  DOWN(balancing)=%d  UP(adding)=%d  why=%s"
              % (label, len(legs), len(down), len(up), res.get("why")))
        if isinstance(res.get("side_refusals"), dict):
            print("%-20s  side_refusals=%s" % ("", res["side_refusals"]))

    print()
    print("=" * 72)
    print("F. is the unmatched ceiling even satisfiable by the first leg?")
    print("=" * 72)
    print("A first leg from flat is ENTIRELY unmatched, so its")
    print("prospective unmatched cost IS its notional. Therefore the mode")
    print("can open only when clip_notional <= straddle_max_unmatched_usd.")
    for clip in ("0.05", "0.08", "0.10", "0.12", "0.15", "0.30"):
        f, why, _ = run_window(clip_usd=D(clip))
        opens = "YES" if f else "NO "
        print("  clip %-6s notional~%-6s opens=%s  (%d fills, %s)"
              % (clip, clip, opens, len(f), why))


probe_rebalance()
