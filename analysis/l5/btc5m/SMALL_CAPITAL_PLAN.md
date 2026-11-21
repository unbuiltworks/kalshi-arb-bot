# Bonereaper-inspired L5 at small capital: what survives, what does not, and how it scales

2026-11-21. Limits are the existing ones and are not changed here: clip
$0.05, side cap $0.30, unmatched cap $0.10, window-loss ceiling $0.20,
envelope $0.60 a window, maker only.

## His behaviour in the units of our limits

1,104 of his windows (2026-11-17..20), and the same scaled to a $0.60
window (`SMALL_CAPITAL_CONSTRAINTS_RESULT.txt`):

| Median per window | His size | At $0.60 | Our limit | Survives? |
|---|---|---|---|---|
| Dollars spent | $2,182 | $0.60 | $0.60 envelope | yes, by construction |
| Dollars on the heavier side | $1,671 (80% of spend) | $0.46 | $0.30 side cap (50%) | NO: the cap stops concentration at 50% |
| Unmatched dollars at the close | $717 (37%) | $0.20 | $0.10 unmatched cap (17%) | NO: half his residual |
| Single fill | $22 | $0.006 | $0.05 clip | NO: our clip is 8x his, scaled; 12 clips fill the window against his 52 fills |
| Dollars paid at 0.90+ | 49% | | maker bids only | not as he does it (he takes) |

Our controller under the live configuration, 1,091 recorded windows
(inferred fills): the unmatched cap first refuses a quote at 16 s
(median) and binds in 97% of windows; the side cap at 55 s, in 95%; no
order is working for 68% of the window. The controller is finished
before most of the window has happened.

What does NOT change with size: the result per share. A fill that earns
or loses 3 cents a share does so at 0.1 shares or 1,000. Fees are
proportional (maker 0; taker 0.095 x p x (1 - p)); the tick is 1 cent at
any size; minimum quantity 0.01 share is not binding at a $0.05 clip.
So small size does not by itself make the economics worse. What it
changes is (a) how early the caps end participation, and (b) that the
window-loss ceiling is two clips' worth of unmatched shares, so one
lopsided window uses the whole ceiling. Whether fee tiers or rebates he
receives are part of his edge is known: they are (his trading alone is
negative after fees); none is available to us at this volume.

## What was built for it (all off unless their token is set)

| Switch | Does | Evidence |
|---|---|---|
| heavy-underdog stop | no new bids on the side that is both heavier and losing | replay only: -20% loss, -77% unmatched; never live |
| side-cap pacing | releases the side cap linearly to the entry cutoff, so quoting lasts the window | replay only: fewer dollars committed early; never live |
| position basis for the entry gate | admits a leg on measured fill edge, no pair credit | refuses everything until an edge is measured |
| own-ledger completeness (always on) | refuses if the account service lists a market of this lane that the lane's ledger lacks | closes the "lost ledger reads as no losses" gap |

## Phases and the evidence that moves between them

PHASE 1, PROVE EXECUTION (the proposed four windows, current limits).
Pass when ALL hold over the four windows:
- every order post-only and acknowledged; every cancel confirmed
  terminal; no uncertain order; no crash while armed;
- every window reconciles to the venue's position row within $0.005;
- no limit exceeded: worst window loss <= $0.20, unmatched <= $0.10;
- the watchdog cancels nothing and reports no read error.
No profitability claim is made from Phase 1.

PHASE 2, PROVE ECONOMICS (still current limits; needs a separate
authorization for more windows).
- Sample: the per-window settled result has a standard deviation of
  $0.096 at this size in replay (1,091 windows). To distinguish a mean
  of +$0.01 a window from zero at two standard errors takes about 370
  settled windows (about 31 hours of consecutive windows); +$0.005 takes
  about 1,500. The number is recomputed from the venue-confirmed spread
  after Phase 1.
- Pass when, on venue-confirmed fills and settlements only:
  settled net P&L per window is positive with a 95% interval above zero;
  the measured fill edge (price 30 s after a fill minus price paid) is
  not negative; unmatched residual P&L is not the source of the gain
  (matched and unmatched lines reported separately); drawdown never
  reaches the $1.00 allowance; every window reconciles.
- Fail (stop and return to design) when cumulative settled loss reaches
  the drawdown allowance, or the interval is entirely below zero at any
  checkpoint of 100 windows.

PHASE 3, INCREASE CAPACITY (owner decision, proposed only after Phase 2
passes). One step at a time: double the clip and the caps together
(clip $0.10, side $0.60, unmatched $0.20, envelope $1.20), repeat the
Phase 2 test at the new size, and confirm the per-share result and the
fill rate did not degrade. Size at the touch is still unmeasured on this
venue; it is the first thing a larger clip will test.

PHASE 4, SCALE. Repeat Phase 3 while per-share net stays positive, the
fill edge holds, and reconciliation stays clean. The first sign of
capacity is the fill rate or fill edge falling as the clip grows.

Fill count, order count and volume are reported at every phase and are
never a pass criterion.
