# FAVORITE_DCA — Variant A, maker variant Am, and Variant B on the authenticated PMUS tape

Historical replay. Nothing submitted, filled, settled or realized. Frozen by
`PREREGISTRATION.md` before the first number; no threshold, cadence,
allocation or quote offset was swept. Kept separate from L5 replication: this
is the user's manually tested strategy, not a claim about L5.

## Sample and eligibility

28 of 38 tape windows passed the preregistered gate; the 10 rejections are
all short captures (span 9 s to 538 s), listed in `replay_summary.json`. 387 of 392
evaluation periods produced an allocation; **5 periods were skipped** for
want of a book within ±5 s of the 60 s mark. 45 leadership flips in total.
Settlement outcome is read from the terminal 0.99/0.01 touch at the window
end.

## Aggregate

| metric | A (taker control) | Am (maker variant) | B (opposite resting) |
|---|---|---|---|
| gross settlement P&L | -27.064 | -15.000 | +84.992 |
| fees | -5.916 | -0.000 | -5.916 |
| maker rebate, MODELLED (unverified) | 0.0000 | 0.6338 | 1.3972 |
| maker rebate, VERIFIED | 0.0000 | 0.0000 | 0.0000 |
| net settlement P&L | -32.980 | -15.000 | +79.075 |
| net per window | -1.178 | -0.536 | +2.824 |
| median window net | +0.987 | +0.554 | -2.209 |
| windows with net > 0 (of 28) | 16 | 17 | 8 |
| peak executable P&L, summed | +40.758 | +34.096 | +169.764 |
| max capital, worst window | 14.35 | 12.00 | 25.39 |
| max residual, worst window (shares) | 17.6 | 13.4 | 151.9 |
| max drawdown, worst window | 12.94 | 11.98 | 28.11 |
| matched shares, total | 103.1 | 48.3 | 477.2 |
| shares acquired, total | 519.4 | 281.8 | 2211.8 |
| acquisition cost, total | 386.67 | 201.08 | 534.44 |
| average cost per share | 0.7445 | 0.7137 | 0.2416 |
| maker / opposite fills | 0 | 216 | 252 |
| net excluding the 3 best windows | -44.606 | -25.805 | -59.218 |
| ... per remaining window | -1.784 | -1.032 | -2.369 |

`Am` is the maker variant of A: the same $1 favored allocation **rested at
the favored outcome's own best bid**, replaced each period, unfilled
remainder cancelled at the next evaluation. It is reported because the
resting-fill evidence carries it — 216 of 387 periods filled (55.8%) under
the same discipline as B's — and it pays **no taker fee at all**.

## The three headline readings

**1. Variant A loses money held to settlement: −$1.178 per window.** The
median window is *positive* (+$0.99) and 16 of 28 windows are positive; the
mean is dragged negative by flip-heavy windows (2030z −10.93, 1115z −9.81,
1915z −9.71). Buying the favored side as a taker pays the spread and the
0.0695 fee on every one of 14 periods, and a flip leaves the strategy holding
both sides bought expensive.

**2. The maker variant roughly halves the loss but does not fix it:**
−$0.536 per window, with **zero** taker fees and a 55.8% fill rate. Resting
at the touch recovers about half a cent a share of spread; it does not turn
the strategy positive.

**3. Variant B's +$2.824 per window is one window.** The top window (2015z,
+$102.98) is **130%** of B's entire aggregate; the top three are 175%.
**Excluding the three best windows B is −$2.369 per window, worse than A's
−$1.784.** B's median window is **−$2.21** and only 8 of 28 are positive.

## Why B's distribution looks like that — the frozen rule's degenerate case

The reservation price is `1.00 − avg_cost_favored − fee_paid`. When the
favorite is trading at 0.97, the reservation is ≈0.03, and the frozen **$1
notional buys ≈33 shares** — one fill reached **100 shares** at 0.01. So:

- 38 of 252 opposite fills priced at ≤ $0.05 carry **1,038 of 1,692**
  opposite shares (61%);
- B acquires 2,212 shares against A's 519 for only 38% more cost;
- B's worst-window residual is **151.9 shares** against A's 17.6.

B is therefore not primarily a pair-acquisition machine. **Fixed notional at
an arbitrarily low price is unbounded share count, so B is a longshot
accumulator**, and its aggregate is the payoff of a few longshots that came
in. I did not change the rule — it is frozen and this is its measured
behavior — but the defect is the finding.

## Fill-model audit (the number B depends on)

- queue ahead when our level became the touch: **median 1,363 shares**
  (p25 297, p75 2,680). Nothing filled on a touch alone; every fill was
  earned by trades depleting that queue, median 15.8 s of resting.
- **0 of 252 fills were priced better than the contemporaneous opposite
  ask.** 7 (2.8%) were priced *worse* — our stale resting price stayed put
  while the market's ask fell below it, so we modelled paying more than the
  market then offered. The error is adverse to the strategy, not favorable.
- Depth below the touch is unobservable in this tape, so an order resting
  behind the touch accrues no fill until the book comes to it.

## The four mechanism questions

**(a) Does opposite-side resting lower average total acquisition cost?**
Arithmetically yes — $0.2416 vs $0.7445 per share — and the answer is
**misleading**. The fall is a *composition* effect: B buys thousands of
cheap longshot shares. It is not a better price for the same inventory.

**(b) Does it reduce residual risk after flips?** **No — it multiplies it.**
Absolute residual immediately after a flip allocation: median 2.66 shares (A:
3.63) but p90 **20.65** (A: 11.95) and max **106.3** (A: 14.08). Worst-window
max residual 151.9 against 17.6. The inventory looks more balanced in share
counts only until the longshot leg is priced.

**(c) Capital requirements?** Up. Worst-window max capital $25.39 against
$14.35, +77%; summed max capital $540 against $393.

**(d) Does it improve net P&L rather than merely look balanced?** **Not
demonstrated.** On the mean yes (+2.824 vs −1.178), but the mean is one
window; on the median it is worse (−2.209 vs +0.987), it wins in fewer
windows (8/28 vs 16/28), and with the three best windows removed it is worse
than the control. On 28 windows this is a lottery-shaped return, not an
established edge.

## Capacity implication for $3 and $5 per window — not validated profit

Framed exactly as preregistered, as a linear scaling of the measured
per-window net:

| variant | net/window at $1 | linear multiple for $3/window | for $5/window |
|---|---|---|---|
| A | −$1.178 | no scale reaches $3 (the sign is negative) | — |
| Am | −$0.536 | no scale reaches $3 | — |
| B | +$2.824 | ×1.06 → ≈$27 max capital/window | ×1.77 → ≈$45 |

**The B row must not be read as capacity toward profit.** Linear scaling
assumes the per-window net is a stable expectation; B's is one window in 28,
its median is negative, and scaling a longshot accumulator scales the
residual too — ×1.77 of a 151.9-share worst-case residual is 269 shares.
Capacity is also bounded by something this replay does not model: our own
market impact. At $1 the taker allocations never exhausted the touch, so the
replay's depth cap never bound; at 5× it would begin to.

## Both required replays are in hand

1. **hold to settlement** — the tables above;
2. **the full mark-to-executable trajectory**, one row per book update per
   window per variant in `trajectories.jsonl.gz` (the per-window
   allocation and fill logs stay in `replay_summary.json`), carrying bid, ask,
   mark, P&L, matched, residual and deployed capital. The user's actual
   manual cash-out rule can be applied to those rows without re-running the
   simulation. The exit is **not** invented here.

For reference, peak executable P&L summed over windows is +$40.76 (A), +$34.10
(Am), +$169.76 (B) against terminal nets of −$32.98 / −$15.00 / +$79.08, and
the peak arrives at a median **587 s** into the window for A. A cash-out rule
is where most of the difference between peak and terminal lives — which is
exactly why it should be specified rather than guessed.

## What is NOT claimed

No rebate is claimed: verified rebates are **$0.0000**, the modelled maker
rebate is reported on its own line and excluded from every net figure above.
No `.96` control was imposed or altered. No BTC spot and no trade-imbalance
cancellation were used. Nothing here is submitted, filled, settled or
realized.

## Per window

| window | settle | flips | allocs | A net | Am net | B net | A peak | B peak | A maxDD | B maxDD | A cap | B cap | A \|resid\| | B \|resid\| | B opp fills |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 09 30 1215z | UP | 3 | 10 | -2.74 | -1.10 | -3.74 | +0.09 | +0.09 | 3.12 | 3.81 | 10.15 | 11.15 | 2 | 3 | 1 |
| 09 30 1230z | UP | 0 | 14 | +0.40 | +0.18 | -2.60 | +0.25 | +1.92 | 0.22 | 4.12 | 14.03 | 17.03 | 14 | 45 | 3 |
| 09 30 1245z | UP | 0 | 14 | +2.72 | +1.21 | +0.72 | +2.55 | +1.52 | 1.03 | 1.03 | 14.12 | 16.12 | 17 | 86 | 2 |
| 09 30 1300z | UP | 3 | 14 | -5.65 | -0.96 | +2.15 | +0.36 | +3.67 | 5.99 | 1.64 | 14.24 | 22.24 | 2 | 101 | 16 |
| 09 30 1315z | DOWN | 1 | 14 | -0.71 | +0.06 | -3.57 | +0.08 | +0.03 | 2.31 | 3.54 | 14.29 | 19.48 | 7 | 6 | 6 |
| 09 30 1330z | DOWN | 1 | 14 | -4.65 | -1.92 | -4.65 | +0.15 | +0.15 | 4.82 | 4.82 | 14.12 | 14.12 | 2 | 2 | 0 |
| 10 02 1100z | UP | 1 | 14 | +1.47 | -0.41 | +1.47 | +1.33 | +1.33 | 0.44 | 0.44 | 14.15 | 14.15 | 14 | 14 | 0 |
| 10 02 1115z | DOWN | 3 | 14 | -9.81 | -3.11 | -11.81 | +0.18 | +0.18 | 9.87 | 11.77 | 14.34 | 16.34 | 13 | 23 | 2 |
| 10 02 1130z | UP | 0 | 14 | +2.84 | +1.88 | -2.16 | +2.66 | +7.42 | 4.01 | 8.39 | 14.15 | 19.15 | 17 | 128 | 7 |
| 10 02 1145z | DOWN | 1 | 14 | -7.95 | -4.15 | +4.20 | +2.02 | +4.17 | 9.89 | 1.18 | 14.29 | 21.37 | 8 | 2 | 14 |
| 10 02 1200z | DOWN | 2 | 14 | -1.73 | -2.39 | +3.38 | +0.48 | +3.25 | 4.91 | 5.77 | 14.28 | 16.28 | 5 | 12 | 2 |
| 10 02 1300z | DOWN | 2 | 14 | -2.12 | +0.46 | -2.26 | -0.07 | -0.07 | 2.34 | 1.94 | 14.27 | 20.27 | 4 | 27 | 8 |
| 10 02 1315z | DOWN | 6 | 14 | +1.33 | +3.35 | -0.23 | +1.24 | +0.88 | 1.58 | 1.07 | 14.35 | 20.86 | 8 | 4 | 9 |
| 10 02 1430z | DOWN | 0 | 14 | +3.47 | +2.00 | -0.53 | +3.28 | +1.20 | 1.37 | 1.58 | 14.17 | 18.17 | 18 | 17 | 4 |
| 10 02 1730z | DOWN | 1 | 14 | -7.82 | -6.58 | -1.07 | +0.34 | +0.33 | 8.09 | 1.39 | 14.31 | 21.31 | 8 | 1 | 28 |
| 10 02 1745z | UP | 3 | 14 | +0.72 | +1.11 | -8.28 | +0.60 | -0.02 | 0.82 | 6.86 | 14.20 | 23.20 | 11 | 150 | 18 |
| 10 02 1800z | UP | 2 | 13 | +4.18 | +4.37 | -2.82 | +4.02 | +1.24 | 3.11 | 4.00 | 13.28 | 20.28 | 16 | 6 | 12 |
| 10 02 1815z | DOWN | 0 | 14 | +1.70 | +1.66 | -5.30 | +1.54 | +0.65 | 0.38 | 5.09 | 14.09 | 21.09 | 16 | 92 | 8 |
| 10 02 1830z | DOWN | 0 | 14 | +2.40 | +1.73 | -3.60 | +2.23 | +0.27 | 1.34 | 3.74 | 13.94 | 19.94 | 16 | 14 | 8 |
| 10 02 1845z | UP | 2 | 14 | +2.60 | +1.42 | -2.40 | +2.44 | +0.12 | 1.01 | 1.94 | 14.05 | 19.05 | 15 | 62 | 7 |
| 10 02 1900z | DOWN | 1 | 14 | +1.26 | +0.65 | -3.27 | +1.13 | +0.01 | 1.04 | 2.64 | 14.22 | 21.02 | 12 | 69 | 18 |
| 10 02 1915z | UP | 3 | 14 | -9.71 | -6.38 | +16.95 | +1.52 | +20.45 | 11.13 | 9.57 | 14.26 | 25.39 | 11 | 11 | 17 |
| 10 02 1930z | UP | 2 | 14 | +2.31 | +1.64 | -2.10 | +2.17 | +0.02 | 5.52 | 1.99 | 14.27 | 20.95 | 13 | 13 | 9 |
| 10 02 1945z | UP | 2 | 14 | +3.98 | +3.09 | -2.02 | +3.81 | +0.34 | 1.84 | 1.37 | 14.27 | 20.27 | 16 | 106 | 7 |
| 10 02 2000z | UP | 0 | 14 | +2.31 | +1.46 | -5.69 | +2.14 | +0.38 | 1.45 | 4.90 | 14.13 | 22.13 | 16 | 126 | 15 |
| 10 02 2015z | DOWN | 5 | 14 | -5.19 | -6.15 | +102.98 | -0.07 | +101.84 | 5.12 | 28.11 | 14.30 | 18.30 | 4 | 106 | 5 |
| 10 02 2030z | DOWN | 1 | 14 | -10.93 | -9.94 | +18.37 | +2.12 | +18.31 | 12.94 | 7.38 | 14.19 | 21.19 | 11 | 5 | 17 |
| 10 02 2045z | UP | 0 | 14 | +2.36 | +1.83 | -3.01 | +2.19 | +0.09 | 0.50 | 2.87 | 14.13 | 19.50 | 16 | 26 | 9 |
