# TWO_SIDED_FAVORITE_TRAIL (Variant C) vs FAVORITE_DCA and OPPOSITE_REST

Historical replay on the same **28 untouched windows**. A, Am and B were
re-run unchanged by the same code path and reproduce their earlier numbers
exactly. No threshold sweep, no retrospective dip selection, no claim of
realized profit. Frozen by `PREREGISTRATION_C.md`, which was committed
before any Variant C number existed.

## Four variants, identical windows

| metric | A taker | Am maker | B opposite-rest | **C trail** |
|---|---|---|---|---|
| net settlement P&L | -32.980 | -15.000 | +79.075 | -4.388 |
| **net per window** | -1.178 | -0.536 | +2.824 | -0.157 |
| median window net | +0.987 | +0.554 | -2.209 | -0.145 |
| windows net > 0 (of 28) | 16 | 17 | 8 | 5 |
| net excl. 3 best windows, per window | -1.784 | -1.032 | -2.369 | -0.200 |
| fees | -5.916 | -0.000 | -5.916 | -6.124 |
| maker compensation, VERIFIED | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| maker compensation, modelled only | 0.0000 | 0.6338 | 1.3972 | 0.0000 |
| peak executable P&L, summed | +40.758 | +34.096 | +169.764 | -0.990 |
| max capital, worst window | 14.35 | 12.00 | 25.39 | 6.00 |
| max residual, worst window (shares) | 17.6 | 13.4 | 151.9 | 6.6 |
| max drawdown, worst window | 12.94 | 11.98 | 28.11 | 0.58 |
| matched shares, terminal total | 103.1 | 48.3 | 477.2 | 50.5 |
| gross cash out | 392.59 | 201.08 | 540.36 | 252.66 |

## C's economic decomposition

| line | total | per window |
|---|---|---|
| matched-pair economics | **−$19.837** | −$0.709 |
| directional trading P&L | **+$15.449** | +$0.552 |
| fees | −$6.124 | −$0.219 |
| maker compensation | $0.0000 (structurally — every C fill is a taker fill) | — |
| cleanup (shares depth would not let us exit) | **0.0000 shares** | — |
| **net** | **−$4.388** | **−$0.157** |

Reconciliation: `matched + directional − net = 0.000000` in **every one of
the 28 windows**. Net is the cash identity `exit proceeds + settlement −
cash out` = `$160.927 + $87.345 − $252.660`.

The sign pattern is the interesting part. **C's matched inventory loses
money** (−$19.84): the seed buys both sides as a taker, so every window
starts by paying two half-spreads plus two fees for a pair that can only ever
return $1.00. **C's directional trading makes money** (+$15.45) — the
extend-while-strengthening, exit-on-reversal loop is the part that works —
but not enough to pay for the seed and the fees.

## Did the reversal exit actually capture anything?

Two different questions, and only the second one is meaningful.

**As asked — executable P&L immediately before vs after each cash-out:**
161 exits, **median delta $0.0000**, mean −$0.0001, range −$0.006 to
+$0.000, and **all 161 within $0.01**.

That result is near-tautological and should be read as a consistency check,
not a finding: `mark-to-executable` already values the residual at the price
the exit transacts at, so exiting at that price moves value from the mark
line to the cash line and cannot create any. The exits pass the check — they
are not quietly booking a gain at a price the market was not showing.

**The question with content — proceeds vs holding those same shares to
settlement:**

| | |
|---|---|
| exit proceeds, net of fee | $160.927 |
| value of the same shares had they been HELD | $149.109 |
| **the exits saved** | **+$11.818** |
| per-window saving, median | **−$0.472** |
| windows where exiting beat holding | **12 of 28** |
| best / worst window | 2030z +$7.97 / 1800z −$2.21 |

So the trailing exit is **not demonstrated to add value**: positive in
aggregate, negative in the median window, better than holding in fewer than
half the windows, and the aggregate is tail-driven again. What it
unambiguously does is **compress risk** — max capital $6.00 against $14.35
(A) and $25.39 (B), worst-window residual 6.6 shares against 17.6 and 151.9,
and worst-window drawdown **$0.58** against $12.94 and $28.11.

## What the frozen reversal rule actually did

The derived trail is `spread_at_peak + 2 × taker_fee(peak)`, which on a
1-tick book is about **$0.012**. That is tight, and the consequence shows:
**161 exits against 195 extensions** across 28 windows, with 22 extensions
withheld for want of strengthening. C spends most of each window flat or
near-flat, which is why its peak executable P&L summed is **−$0.990** — C is
essentially never in profit; it bleeds slowly instead of swinging.

I did not widen the trail. Searching for a better distance is the exact
thing the preregistration forbade, and the tightness is a property of the
derivation (round-trip cost on a penny-tick market), not an accident.

## Ranking on these 28 windows

1. **C is the best-behaved loser**: −$0.157/window, and −$0.200/window with
   its three best windows removed — the only variant whose result barely
   moves when the tail is deleted.
2. **Am** −$0.536, **A** −$1.178.
3. **B's** +$2.824 remains one window in 28 (−$2.369/window without its top
   three), with 4× C's capital and 23× C's worst-case residual.

**No variant is profitable on this sample.** C's merit is that it is nearly
flat with small, bounded risk, which is a materially different failure mode
from A's steady bleed and B's lottery.

## Mechanics audit — no semantic or accounting defects found

- reconciliation ties to **0.000000** in all 28 windows;
- **no touch = fill** anywhere: C transacts only as a taker at the book's own
  displayed prices, capped by displayed depth, and B's resting fills were
  separately audited (median 1,363 shares of queue ahead at arming);
- no exit priced better than the contemporaneous executable price;
- no negative share count, no negative cost basis, no price outside
  $0.01–$0.99;
- cleanup **0.0000 shares**: displayed depth was sufficient for every exit;
- 5 periods skipped for want of a book within ±5 s of the mark, unchanged
  from the earlier run.

Because the mechanics survived, the bounded live-probe plan required by the
preregistration is prepared in `Q1_BOUNDED_PROBE_PLAN.md`. **It is not
authorized and not executed**, and preparing it is not authorization.

## Per window (C)


| window | settle | flips | C extends | C withheld | C exits | C net | C matched econ | C directional | C maxcap | C \|resid\| | C maxDD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 09-30-1215z | UP | 3 | 4 | 0 | 3 | -0.057 | +0.000 | -0.057 | 1.07 | 1.0 | 0.10 |
| 09-30-1230z | UP | 0 | 5 | 5 | 2 | -0.687 | -0.211 | -0.475 | 4.88 | 2.0 | 0.56 |
| 09-30-1245z | UP | 0 | 6 | 1 | 5 | +0.041 | -0.467 | +0.508 | 3.62 | 0.0 | 0.07 |
| 09-30-1300z | UP | 3 | 5 | 0 | 4 | -0.251 | -1.050 | +0.800 | 3.59 | 1.0 | 0.17 |
| 09-30-1315z | DOWN | 1 | 7 | 0 | 4 | -0.241 | -0.930 | +0.688 | 4.49 | 2.1 | 0.25 |
| 09-30-1330z | DOWN | 1 | 5 | 4 | 2 | -0.338 | -1.376 | +1.037 | 5.86 | 3.1 | 0.38 |
| 10-02-1100z | UP | 1 | 8 | 3 | 6 | -0.069 | -0.881 | +0.813 | 4.83 | 3.0 | 0.08 |
| 10-02-1115z | DOWN | 3 | 5 | 0 | 5 | -0.022 | -0.366 | +0.344 | 2.66 | 1.2 | 0.18 |
| 10-02-1130z | UP | 0 | 8 | 0 | 6 | +0.002 | -0.817 | +0.820 | 5.10 | 0.0 | 0.11 |
| 10-02-1145z | DOWN | 1 | 7 | 0 | 8 | +0.118 | -0.628 | +0.745 | 2.65 | 0.0 | 0.14 |
| 10-02-1200z | DOWN | 2 | 6 | 0 | 6 | -0.263 | -0.887 | +0.625 | 3.04 | 1.0 | 0.25 |
| 10-02-1300z | DOWN | 2 | 7 | 1 | 6 | -0.182 | -0.885 | +0.703 | 4.02 | 1.0 | 0.16 |
| 10-02-1315z | DOWN | 6 | 5 | 0 | 5 | -0.238 | -0.565 | +0.327 | 3.06 | 1.0 | 0.23 |
| 10-02-1430z | DOWN | 0 | 8 | 0 | 7 | +0.240 | -0.622 | +0.862 | 3.48 | 2.0 | 0.12 |
| 10-02-1730z | DOWN | 1 | 6 | 0 | 7 | -0.115 | -0.835 | +0.719 | 3.19 | 0.0 | 0.36 |
| 10-02-1745z | UP | 3 | 7 | 1 | 5 | -0.113 | -0.879 | +0.766 | 3.83 | 2.0 | 0.12 |
| 10-02-1800z | UP | 2 | 7 | 0 | 7 | -0.282 | -0.377 | +0.095 | 3.08 | 1.0 | 0.39 |
| 10-02-1815z | DOWN | 0 | 9 | 2 | 7 | -0.151 | -0.672 | +0.521 | 4.90 | 0.0 | 0.12 |
| 10-02-1830z | DOWN | 0 | 7 | 0 | 7 | -0.290 | -0.434 | +0.144 | 3.80 | 0.0 | 0.24 |
| 10-02-1845z | UP | 2 | 8 | 2 | 6 | +0.243 | -0.556 | +0.799 | 4.46 | 3.0 | 0.21 |
| 10-02-1900z | DOWN | 1 | 11 | 1 | 8 | -0.525 | -1.053 | +0.528 | 5.69 | 3.0 | 0.58 |
| 10-02-1915z | UP | 3 | 6 | 0 | 7 | -0.350 | -0.748 | +0.398 | 3.02 | 0.0 | 0.31 |
| 10-02-1930z | UP | 2 | 10 | 0 | 11 | -0.079 | -0.733 | +0.654 | 3.00 | 0.0 | 0.23 |
| 10-02-1945z | UP | 2 | 6 | 0 | 4 | -0.084 | -0.678 | +0.594 | 4.43 | 2.0 | 0.25 |
| 10-02-2000z | UP | 0 | 7 | 1 | 3 | -0.150 | -0.630 | +0.480 | 6.00 | 3.1 | 0.22 |
| 10-02-2015z | DOWN | 5 | 6 | 0 | 6 | -0.336 | -0.770 | +0.434 | 4.26 | 0.0 | 0.29 |
| 10-02-2030z | DOWN | 1 | 10 | 0 | 7 | -0.070 | -1.159 | +1.089 | 5.14 | 1.0 | 0.21 |
| 10-02-2045z | UP | 0 | 9 | 1 | 7 | -0.141 | -0.629 | +0.488 | 4.98 | 3.1 | 0.21 |
