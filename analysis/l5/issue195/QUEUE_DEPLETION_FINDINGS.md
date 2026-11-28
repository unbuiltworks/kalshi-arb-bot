# Queue depletion, fill bounds, and the unit economics of the pair

## 1. What data exists, verified before anything was built on it

The durable calibration ledger holds **17,600 records** and reports
**`usable_for_calibration: 0`**. Two separate reasons, and the second is the
one that matters:

- the gate demands all five capture categories, and `fee_accounting` can
  only be supplied by a real venue fill;
- the per-order **observation** loop — the one that records
  `prints_at_our_price` — appended **nothing** (`events_appended: 0`). It
  runs over *live* orders; the 17,600 are `coid: "shadow|ARGMAX|…"`
  pre-submit records with `venue_actual_status: PENDING`.

Verified, not assumed: a log query for `prints_at_our_price` across the
running deployment returns **zero records**.

**So there is no trade-print series, and a fill probability is not in this
data.** My previous report said the estimator could be built from
`queue_ahead` + `prints_at_our_price` on all 17,600 records. That was wrong:
the prints field exists in the schema and is wired from the runner, but no
observation record was ever written. I'm correcting that here.

What *does* exist is dense 8-level ladder snapshots **with sizes**, from
three independent recordings on three dates — 15 windows in total.

## 2. The estimator: three verdicts, no invented probabilities

`src/l5_queue_depletion.py`. From ladders alone, exactly three verdicts are
available:

| verdict | logic | what it bounds |
|---|---|---|
| **PROVEN** | the opposite touch reached our price. A resting buy cannot sit above the best ask, so it executed. | **Sufficient** → lower bound on the fill rate |
| **POSSIBLE** | cumulative *decrease* in at-or-better displayed size reached our queue-ahead | **Necessary only** → upper bound |
| **IMPOSSIBLE** | the necessary condition never held under the assumption most favourable to filling | a hard negative, whatever the trade/cancel mix |

Handled explicitly rather than averaged away:

- **cancellations** — counted in the decrease, which is precisely why
  POSSIBLE is only an upper bound: a cancel advances our position but never
  fills us.
- **additions** — ignored in both directions. Size added at a better price
  joins *ahead* of us; size added at our price joins *behind*. The ladder
  cannot say which, so an increase is not allowed to reduce or raise the
  requirement.
- **partial fills** — the requirement is in contracts of queue, so partial
  clearance reduces it proportionally; our own order is treated as
  indivisible and unfilled until the whole queue ahead has cleared.
- **priority** — strict price priority then FIFO: everything at a strictly
  better price plus everything already resting at our level. Our own size is
  never counted as ahead of itself.
- **uncertainty** — an unreadable ladder returns `None`, never zero. Zero is
  the most favourable answer there is and an absent ladder has not earned it.

Reproduces the venue-recorded figure: at own 0.4600 against the 0100z
ladder, `queue_ahead` = **2923.0200**, and at the 0.5100 the requote wanted
to chase, **561.0200** — both exactly as `L5_TS_REQUOTE` recorded them.

## 3. The identity that makes depth an economic choice

For a binary pair `own_UP = best_bid` and `own_DOWN = 1 − best_ask`, so the
pair available **at the touch** is exactly `1 − spread`, and resting each leg
`d` ticks behind its own touch gives

    pair = 1 − spread − 2·d·tick

The spread is **one tick in 217 of 220** samples. So buying both sides at the
market cannot beat $1.00 by more than $0.01, and the deployed `depth_ticks=2`
yields a **0.95 pair** — exactly the pair 0100z had resting. The design is
self-consistent; the edge is structural, not discretionary.

## 4. Unit economics by depth, both samples

`EV per attempt = P(pair)·pair_edge + P(single)·single_outcome`

**IN SAMPLE** — 2026-10-28 (×3 windows), 2026-11-06 0300z

| d | P(pair) | P(single) | pair $ | hold $ | taker $ | wait $ | **EV today** | **EV w/ taker** |
|---|---|---|---|---|---|---|---|---|
| 0 | 71.7% | 25.5% | 0.0108 | −0.1891 | −0.0370 | −0.1891 | **−0.0405** | −0.0017 |
| 1 | 65.2% | 31.5% | 0.0308 | −0.1914 | −0.0333 | −0.1895 | **−0.0396** | +0.0096 |
| 2 ⬅ deployed | 63.0% | 35.2% | 0.0508 | −0.2011 | −0.0337 | −0.1752 | **−0.0297** | +0.0201 |
| 3 | 57.4% | 39.8% | 0.0708 | −0.2002 | −0.0350 | −0.1631 | **−0.0244** | +0.0267 |
| 4 | 53.0% | 44.4% | 0.0908 | −0.1938 | −0.0341 | −0.1562 | **−0.0212** | +0.0330 |
| 5 | 47.0% | 51.4% | 0.1108 | −0.2007 | −0.0338 | −0.1369 | **−0.0182** | +0.0347 |
| 6 | 45.5% | 50.3% | 0.1308 | −0.2103 | −0.0353 | −0.1145 | **+0.0020** | +0.0418 |

**OUT OF SAMPLE** — 2026-10-25 (×12 windows), 2026-11-06 1130z

| d | P(pair) | P(single) | pair $ | hold $ | taker $ | wait $ | **EV today** | **EV w/ taker** |
|---|---|---|---|---|---|---|---|---|
| 0 | 65.3% | 34.2% | 0.0101 | −0.2218 | −0.0410 | −0.2218 | **−0.0692** | −0.0074 |
| 1 | 56.7% | 41.9% | 0.0301 | −0.2221 | −0.0419 | −0.2193 | **−0.0749** | −0.0005 |
| 2 ⬅ deployed | 52.5% | 42.3% | 0.0502 | −0.2440 | −0.0542 | −0.2313 | **−0.0714** | +0.0034 |
| 3 | 47.8% | 48.2% | 0.0702 | −0.2454 | −0.0535 | −0.2124 | **−0.0687** | +0.0078 |
| 4 | 44.3% | 52.8% | 0.0902 | −0.2542 | −0.0562 | −0.2108 | **−0.0714** | **+0.0103** |
| 5 | 38.1% | 59.3% | 0.1101 | −0.2451 | −0.0593 | −0.1837 | **−0.0669** | +0.0068 |
| 6 | 34.0% | 61.6% | 0.1301 | −0.2430 | −0.0597 | −0.1774 | **−0.0650** | +0.0075 |

`today` = maker-only, hold out for a ≤0.96 pair, taker disabled.
`taker` = complete the unmatched leg at the opposite touch, paying the
published `theta·q·p·(1−p)` at `theta = 0.0695`.

## 5. Conclusions

**(a) Today's policy has negative measured expectancy at every depth, in
both samples.** −$0.018 to −$0.041 per attempt in sample; −$0.065 to −$0.075
out of sample. The deployed `d=2` is **−$0.0297 / −$0.0714**.

**(b) The unmatched leg, not the quoting, sets the sign.** The pair earns
the identity's edge almost exactly (+$0.0502 at d=2 against a nominal
+$0.05). A leg that fills alone realizes **−$0.19 to −$0.25**, nearly
independent of depth — that figure is the adverse-selection constant. It is
4–5× the pair edge and occurs about as often.

**(c) A ≤0.96 completion is rarely available once the first leg fills** —
offered in **1% to 20%** of single-leg cases. So "wait for 0.96" is, in
practice, "carry the residual": `wait $` tracks `hold $` closely.

**(d) Completing the unmatched leg at the touch is worth ~$0.16–0.19 per
leg** against holding, and ~$0.13–0.18 against waiting. It caps the single's
cost at −$0.034 to −$0.060. **This flips the strategy's sign** — to
+$0.0034 out of sample at the deployed depth, +$0.0103 at `d=4`.

Ex ante this is not free money: at the moment of the fill, holding is
EV-neutral against the market's own prices, which is already pinned by
`test_declining_a_zero_edge_completion_is_ev_neutral`. These are **ex post**
figures. What they measure is that the market keeps moving after it fills a
maker quote, so the realized cost of carrying is far worse than its price
implied.

**(e) The optimum depth is deeper than deployed.** Out of sample the EV
peaks near `d=4` (+$0.0103); in sample it is still rising at `d=6`. More
depth buys more edge per pair faster than it loses pair rate.

**(f) Against the $480/day target.** Best out-of-sample figure is
**+$0.0103 per contract-attempt**. At 96 BTC15m windows a day and one
attempt per window, that is **$0.99/day per contract of leg size** — so the
target implies roughly **485 contracts per leg in every window, all day**,
about **$1,000 of gross cash per window** against the currently authorized
$1.00. Displayed depth is not the constraint (0100z showed ~2,900 contracts
at one level); authorization and risk budget are. **We are not approaching
the target, and nothing here is evidence that we are.**

## 6. What is still missing

1. **No trade prints, so no fill probability.** PROVEN/POSSIBLE bracket the
   fill rate; nothing in this data closes the gap. Fixing it means running
   the observation loop against **real resting orders**.
2. **The sample is biased against us, which is the honest direction.** A leg
   is PROVEN filled only when the opposite touch reached it — the market
   moved *through* our price — so proven fills over-sample exactly the
   adverse case. Real fills also happen inside the queue without the touch
   crossing, and those are less adverse. **Single-leg losses here are likely
   overstated and the pair rate understated.**
3. **Overlapping entries.** Every frame is a candidate entry and they share
   futures, so these are correlated observations, not independent trades. No
   confidence intervals are claimed and the totals are not a P&L.
4. **Settlement is proxied by the final mid**, not by actual settlement,
   except where a window's outcome is separately known.
5. **15 windows across 3 dates.** Thin.
6. **The taker path has never been exercised live**, and the $0.0521
   completion-budget discrepancy remains open.

## 7. The next highest-impact code change

**Make the unmatched-leg disposition an explicit, priced decision rather
than an implicit carry.** Today the engine quotes maker-only, holds out for
≤0.96, and — when that never arrives, which is 80–99% of the time — carries
the residual to settlement with no record stating the position, its worst
case and that nothing was available. The measurement says this is the worst
of the three dispositions by $0.13–0.19 per leg.

The code to do it already exists and is default-off: `completion_pair_rescue`
at the submission boundary already bounds a completion at
`completion_pair_ceiling()` = 1.00 and requires it to reduce an unmatched
position without crossing zero. What is missing is a controller that, on a
confirmed first-leg fill with no complement available inside the cap,
**prices hold-versus-complete explicitly and records the verdict** — and,
where completing wins by more than a stated margin, routes it.

That last step needs taker execution, which is disabled by your standing
order. **I am not proposing to enable it.** This is the evidence for that
decision: the measured cost of keeping it off is ~$0.16–0.19 per unmatched
leg, and with it off the strategy's measured expectancy is negative at every
depth in both samples. The decision is yours.

The step that needs no such authorization: emit the explicit
hold-versus-complete verdict in the tape, disarmed, so the next live window
produces venue-confirmed evidence on the decision rather than a silent carry.
