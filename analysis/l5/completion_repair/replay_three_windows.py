"""COUNTERFACTUAL replay of the three settled campaign windows.

WHAT THIS IS. Every state below is the RECORDED one: the books are the
`L5_FILL_CALIB_PRESUBMIT` snapshots the live engine took at submission time
(authenticated websocket, sub-10ms age), the bases are venue-confirmed fills,
and the settled outcomes are the `L5_WINDOW_ACCOUNTING` CONFIRMED rows. The
decisions are produced by calling the REAL `l5_book_completion.decide`.

WHAT THIS IS NOT. Nothing here is realized. A decision to submit is not a
fill; a price at the touch is not a fill either. Every P&L figure produced by
this script is labelled COUNTERFACTUAL and is conditional on a maker fill
that did not happen. The only realized numbers in this file are the three
settled window results, which are labelled as such.

Run: PYTHONPATH=. python3 analysis/l5/completion_repair/replay_three_windows.py
"""
from decimal import Decimal as D

from src import l5_book_completion as BC
from src import l5_two_sided_probe as TS

TICK = D("0.01")

# ---------------------------------------------------------------- the record
#
# Every live order the campaign submitted, from L5_FILL_CALIB_PRESUBMIT.
# (utc, window, side, own_px quoted, qty, book bid, book ask)
SUBMITS = [
    ("14:15:11", "1415z", "UP",   "0.4200", "2.11", "0.4400", "0.4500"),
    ("14:15:57", "1415z", "DOWN", "0.5600", "2.11", "0.4100", "0.4200"),
    ("14:16:13", "1415z", "UP",   "0.4500", "2.06", "0.4700", "0.4800"),
    ("14:16:39", "1415z", "DOWN", "0.5200", "2.06", "0.4500", "0.4600"),
    ("14:17:03", "1415z", "DOWN", "0.5600", "2.06", "0.4100", "0.4200"),
    ("14:18:29", "1415z", "UP",   "0.4500", "2.06", "0.4900", "0.5000"),
    ("14:18:48", "1415z", "DOWN", "0.5600", "2.06", "0.4100", "0.4400"),
    ("14:19:00", "1415z", "UP",   "0.4100", "2.12", "0.4300", "0.4400"),
    ("14:21:21", "1415z", "DOWN", "0.2100", "1.72", "0.7600", "0.7700"),
    ("14:21:39", "1415z", "UP",   "0.6700", "1.72", "0.6900", "0.7000"),
    ("14:22:17", "1415z", "UP",   "0.7100", "1.72", "0.7300", "0.7400"),
    ("14:22:37", "1415z", "DOWN", "0.3000", "1.81", "0.6700", "0.6800"),
    ("14:23:10", "1415z", "UP",   "0.7200", "1.81", "0.7400", "0.7500"),
    ("14:30:14", "1430z", "UP",   "0.4300", "1.51", "0.4500", "0.4600"),
    ("14:30:52", "1430z", "UP",   "0.4500", "2.06", "0.4700", "0.4800"),
    ("14:38:32", "1430z", "DOWN", "0.5200", "2.06", "0.4500", "0.4600"),
    ("14:38:45", "1430z", "DOWN", "0.3800", "1.91", "0.5900", "0.6000"),
    ("14:39:32", "1430z", "UP",   "0.6200", "1.91", "0.6400", "0.6600"),
    ("14:45:15", "1445z", "UP",   "0.4200", "2.11", "0.4400", "0.4500"),
    ("14:47:40", "1445z", "DOWN", "0.3700", "1.91", "0.6000", "0.6100"),
    ("14:47:52", "1445z", "UP",   "0.6000", "1.91", "0.6200", "0.6300"),
]

#: settled, venue-confirmed, REALIZED
SETTLED = {"1415z": D("0.102400"), "1430z": D("0.061800"),
           "1445z": D("-0.706700")}


def touch_own(side, bid, ask):
    """The best own-terms price at which this side can REST (post-only)."""
    return D(bid) if side == "UP" else (D(1) - D(ask))


def ticks_behind(side, own, bid, ask):
    return int((touch_own(side, bid, ask) - D(own)) / TICK)


def banner(s):
    print("\n" + "=" * 74 + "\n" + s + "\n" + "=" * 74)


# ============================================================ 1. the handicap
banner("1. EVERY LIVE ORDER RESTED BEHIND THE TOUCH  (recorded, not modelled)")
print("%-9s %-6s %-5s %-8s %-9s %-7s" % (
    "utc", "win", "side", "quoted", "touch", "behind"))
behind_counts = {}
for utc, win, side, own, qty, bid, ask in SUBMITS:
    t = touch_own(side, bid, ask)
    n = ticks_behind(side, own, bid, ask)
    behind_counts[n] = behind_counts.get(n, 0) + 1
    print("%-9s %-6s %-5s %-8s %-9s %d ticks" % (utc, win, side, own, t, n))
print("\ndistribution of ticks behind the touch: %s" % (
    dict(sorted(behind_counts.items())),))
print("L5_TS_DEPTH_TICKS on the deployed service was 2, and it priced the")
print("OPENINGS and the COMPLETIONS alike.")

# ====================================================== 2. why that is wrong
banner("2. THE ASYMMETRY THAT MAKES ONE DEPTH WRONG FOR BOTH JOBS")
print("An OPENING that does not fill costs nothing: no trade, no exposure.")
print("So a cheaper price is worth queue position, and 2 ticks back is right.")
print()
print("A COMPLETION that does not fill strands the leg to settlement. On the")
print("1445z basis that is the whole position:")
resid_per_unit = D("0.37")
concession = 2 * TICK
print("   not filling      -%s / unit   (the 1.91 DOWN basis, realized)"
      % resid_per_unit)
print("   2-tick concession -%s / unit   (a dearer pair, if it fills)"
      % concession)
print("   ratio             %.0fx in favour of fill probability"
      % (resid_per_unit / concession))
print()
print("So the completion wants the touch and the opening wants the discount.")
print("One parameter cannot serve both, which is why they are now separate.")

# ================================================ 3. the 1445z counterfactual
banner("3. 1445z COUNTERFACTUAL  --  the window that stranded its leg")
WS, NOW = 1793371500.0, 1793371500.0 + 170.0
BOOK = {"bid": "0.6200", "ask": "0.6300", "stream_age_s": "0.007",
        "bid_shares": "50", "ask_shares": "50"}
HELD_QTY, HELD_COST = D("1.91"), D("0.7067")
print("recorded state: held %s DOWN, basis %s own (cost $%s), book %s/%s"
      % (HELD_QTY, HELD_COST / HELD_QTY, HELD_COST,
         BOOK["bid"], BOOK["ask"]))
print("spread was 1 tick for 205 of 210 two-sided ticks in this window.\n")

for depth, label in ((2, "as deployed (opening depth)"),
                     (0, "repaired (completion depth)")):
    rec = BC.decide(
        market="cpc-btc-updown-15m-2026-10-30-1445z", held_side="DOWN",
        held_qty=HELD_QTY, held_cost_usd=HELD_COST,
        deficient_qty=D("0"), deficient_cost_usd=D("0"),
        book=dict(BOOK), now_t=NOW, window_start=WS, resting=(),
        tick="0.01", min_qty="0.01", depth_ticks=depth,
        residual_budget_usd=D("3"), submissions_left=5, last_decide_t=None)
    leg = rec.get("leg") or {}
    print("depth %d  %-28s -> %-9s %s" % (
        depth, label, rec.get("action"), rec.get("reason")))
    if leg:
        pair = D(rec["pair_own_cost"])
        print("          quote own %s   pair %s   COUNTERFACTUAL locked "
              "$%+.4f on %s if it fills"
              % (leg["price"], pair, D(rec["locked_pnl_usd"]), HELD_QTY))
    print("          within preferred target (0.9532): %s"
          % rec.get("within_preferred_target"))

print("\nWhat the OLD gate did instead, at the same state:")
print("   pair 0.9700 and 0.9900 both exceed BREAKEVEN_PAIR 0.9532")
print("   -> PAIR_ABOVE_BREAKEVEN on every 5s tick for the final 12 minutes")
print("   -> residual sampler at 14:59:52 and 14:59:59: order_resting false")
print("   -> REALIZED settled result: -$0.706700")

# ============================================ 4. additional orders submitted
banner("4. WHICH ADDITIONAL ORDERS THE REPAIRED ENGINE WOULD SUBMIT")
print("1445z, after the complement was cancelled unfilled at 14:47:58, the")
print("window still had 12 minutes and 1.91 DOWN unmatched. The repaired")
print("engine re-decides every DECIDE_EVERY_S = %ss, and the attempt axis"
      % BC.DECIDE_EVERY_S)
print("now reaches the wire, bounded by L5_ACC_MAX_ATTEMPTS.")
span_s = 12 * 60
print("   evaluation opportunities in that span: %d" % (span_s / BC.DECIDE_EVERY_S))
print("   placements permitted per leg per round: 4 (default)")
print("   orders actually submitted by the deployed engine: 0")
print()
print("Windows 1415z and 1430z both ended with residual 0, so the repaired")
print("gate adds no order there -- it only ever acts on UNMATCHED inventory.")
print("Their three defensive completions at pairs 1.01/1.01/1.02 were placed")
print("by the DEFENSIVE route, whose ceiling is 1.20, and this change")
print("neither removes nor widens that route.")

# ===================================================== 5. settled vs counter
banner("5. SETTLED (REALIZED) vs COUNTERFACTUAL")
realized = sum(SETTLED.values())
print("REALIZED, venue-confirmed, settlement-only:")
for w in ("1415z", "1430z", "1445z"):
    print("   %-6s %+0.6f" % (w, SETTLED[w]))
print("   total  %+0.6f" % realized)
print()
cf_1445 = D("0.0191")
print("COUNTERFACTUAL, conditional on a maker fill at the touch that did")
print("NOT occur -- not realized, not a fill, not evidence of one:")
print("   1445z completing at pair 0.9900 -> %+0.6f  (vs %+0.6f realized)"
      % (cf_1445, SETTLED["1445z"]))
print("   three-window total would be      %+0.6f  (vs %+0.6f realized)"
      % (SETTLED["1415z"] + SETTLED["1430z"] + cf_1445, realized))
print("   swing on the one window          %+0.6f"
      % (cf_1445 - SETTLED["1445z"]))
print()
print("THE CLAIM THIS SUPPORTS, AND ITS LIMIT. The repaired engine would have")
print("been ALLOWED to submit a complement at a fillable price and would have")
print("kept re-evaluating for 12 minutes instead of stopping. Whether it")
print("would have FILLED is not established by this replay and cannot be:")
print("PMUS publishes no print endpoint, so joint maker-fill probability is")
print("not measurable from the record. That is what the live validation is")
print("for, and it is why the objective there is a FILL, not a submission.")
