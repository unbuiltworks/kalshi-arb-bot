# The portfolio controller: what was built and what it is worth

**Built:** one portfolio-level controller (`src/l5_portfolio_controller.py`)
that combines pair accumulation, favored-side accumulation, inventory
balancing and adaptive maker repricing in a single valuation, wired into
the live runner as a recorded-only stage that is off by default.

**Verdict on the economics: it improves on the deployed pair, and it is
still not profitable.** Run selectively it cuts the loss per window by about
three quarters out of sample and is indistinguishable from break-even. Run
aggressively it reaches the 20+ fills a window that was asked for and loses
about a dollar a window per contract of clip. **NO PROMOTION; no live
proposal.**

Reproduce: `PYTHONPATH=. python3 analysis/l5/portfolio_controller_replay.py`
(about 8 minutes) and `PYTHONPATH=. python3
analysis/l5/maker_fill_value_surface.py`. Tests:
`tests/test_l5_portfolio_controller.py` (79),
`tests/test_l5_portfolio_controller_replay.py` (150).

Evidence class: **L5's Polymarket price path with PMUS ticks and fees and a
one-minute Coinbase reference. Not PMUS fills and not our own.**

## The controller

It holds no state. Each call takes the whole portfolio - contracts held on
each side at their exact cost, the pairs that makes, what is unmatched,
every maker order resting - plus the current book and a win probability,
and returns the quote wanted on each side with its effect on the
portfolio's settlement.

Every candidate order is scored on two lines:

- **expectation:** `qty x (win probability of that side - uncertainty margin
  - expected adverse selection - price)`;
- **risk:** the change in unmatched contracts it causes, charged at
  `risk x sqrt(p(1-p))` a contract. A hedge earns the charge back.

A side is quoted at the price where those sum to zero, or at the touch if
that is lower. It never crosses and never improves the best bid. That one
rule produces all four behaviours:

| behaviour | how it arises |
|---|---|
| pairs | neither side is rich, so both are quoted |
| favored side | the model puts a side well above its price: quoted at the touch and sized up; the other side drops back or out |
| hedging | unmatched UP raises the DOWN reservation price by twice the risk charge and lowers UP's |
| repricing / replenishment | recomputed from the current book and confirmed inventory each call; `order_actions` emits exact-ID cancels and placements |

Unmatched inventory does not block new orders: both sides stay quoted until
the unmatched cap. Cash, worst-case exposure and order-count caps are
applied before anything is proposed, and a cap that was not supplied
refuses everything.

Win probability comes from strike distance, time to expiry and volatility
(zero-drift lognormal, the baseline already calibrated in
`btc15m_signal/`). **Without a supplied probability the belief is the
book's own mid, labelled BOOK, and no side can look cheap: the controller
can then only pair and hedge.**

## What is and is not wired into production

| | |
|---|---|
| decision module | `src/l5_portfolio_controller.py`, pure, imports nothing that can trade |
| runner stage | `Runner.maybe_portfolio_decision`, on every tick, throttled to 5 s |
| reads | the executor's own ledger (`position`, `leg_cost`, `live_orders`) and the authenticated book |
| writes | one log record, `L5_PORTFOLIO_DECISION`: portfolio, belief, quotes, skipped sides with reasons, exact order actions |
| switch | `L5_PORTFOLIO_CONTROLLER=SHADOW`; unset is OFF; **no value makes it trade** |
| executor, gates, authorization | untouched (tested) |
| live probability feed | **none** - the live process has no spot-and-strike source, so in production the belief is BOOK |

The controller's quotes are not connected to order submission. That is
deliberate and follows from the result below: connecting a controller whose
expected settled value is negative would be promoting a losing strategy to
generate fills.

## One identity decides what any such controller can earn

A portfolio's settlement P&L is exactly the sum, over every contract
bought, of (settlement - price). Pairing does not appear in it. A pair is
two contracts whose risks cancel; it changes the spread of outcomes and
never their mean (tested against random portfolios).

So pairs, hedging and inventory balancing decide **how much the result
varies**. Whether it is positive is decided only by **which fills are
taken**. That turns the question into a measurable one: is there any state
in which a resting maker bid's fills are worth more than they cost?

`maker_fill_value_surface.py` measures it: 526 windows, bids re-quoted
continuously at 0, 2, 5 and 10 ticks behind the print, value = settlement
less price per fill, bucketed by price band, time in window, price band x
time, one-minute momentum, model edge, and model edge x momentum; both fill
rules; window-clustered intervals.

| | all fills, per contract |
|---|---|
| at the touch, conservative rule, out of sample | -0.0399 [-0.0513, -0.0282], 9,768 fills |
| at the touch, conservative rule, in sample | -0.0574 [-0.0682, -0.0463], 7,622 fills |
| at the touch, a touch fills, out of sample | -0.0252 [-0.0335, -0.0166], 16,310 fills |
| 10 ticks back, conservative, out of sample | -0.0415 [-0.0745, -0.0084], 680 fills |

**Cells whose lower bound is above zero in both halves: none**, in any of
the eight depth-and-rule combinations. The least bad states are strong
favorites (price 0.8 and above: -0.007 out of sample) and quotes the model
puts more than ten cents under fair value (-0.008 out of sample, -0.040 in
sample).

## The controller against the deployed pair

Same ruler as `EDGE_COMPARISON.md`. Chronological 60/40 split. Presets were
fixed before any result was read; TUNED is the best of a 240-setting grid
on the first part only, among settings that average at least one fill a
window.

### With a model probability (312 windows, 125 out of sample)

| | fills / window | net / contract | net / window | 95% interval | worst window |
|---|---|---|---|---|---|
| deployed pair | 1.8 | -0.0289 | -0.051 | [-0.085, -0.018] | -0.64 |
| CAUTIOUS (6c haircut, hedge first) | 2.4 | -0.0467 | -0.114 | [-0.162, -0.066] | -0.81 |
| BALANCED (4c haircut) | 10.0 | -0.0420 | -0.419 | [-0.527, -0.311] | -3.17 |
| FAVORED (4c haircut, size on edge) | 23.1 | -0.0387 | -0.892 | [-1.146, -0.638] | -6.22 |
| AGGRESSIVE (no haircut, touch, size on edge) | 31.5 | -0.0368 | -1.159 | [-1.438, -0.880] | -6.50 |
| AGGRESSIVE + hedge skew | 32.0 | -0.0412 | -1.319 | [-1.539, -1.098] | -6.78 |
| **TUNED** (10c haircut, risk 0.3, up to 6 unmatched) | 1.0 | **-0.0120** | **-0.012** | [-0.037, +0.013] | -0.50 |

TUNED against the deployed pair, per window: **+$0.094 in sample [+0.052,
+0.141], +$0.039 out of sample [-0.004, +0.083]**. In sample it was
+$0.005 a window, one of only 2 settings of 240 above zero; out of sample
it is -$0.012. Under the optimistic fill rule it is -$0.007 [-0.048,
+0.033].

What TUNED does: it adds only when the model puts a side about 19 cents
above its touch, then bids the hedge at the touch. Out of sample that was
67 adds and 56 hedges over 125 windows; its 11 contracts left unmatched
averaged +$0.04 each (4 of 11 lost) against -$0.37 (18 of 20 lost) for the
pair. That is favored inventory doing what it should, on a sample far too
small to rely on.

### Does trading more pay?

The best first-part setting in each band of activity, scored out of sample:

| fills / window (OOS) | net / window (OOS) | 95% interval | net / contract | worst window |
|---|---|---|---|---|
| 0.5 | -0.003 | [-0.026, +0.020] | -0.006 | -0.55 |
| 1.0 | -0.012 | [-0.037, +0.013] | -0.012 | -0.50 |
| 2.4 | -0.034 | [-0.098, +0.029] | -0.015 | -1.50 |
| 10.8 | -0.406 | [-0.659, -0.154] | -0.038 | -3.94 |
| 25.2 | -1.021 | [-1.233, -0.809] | -0.041 | -6.51 |

Monotone. The loss per contract rises from under a cent to four cents as
the controller is made to trade, and the loss per window rises three
hundred-fold. The tuning was pushed to the edge of the grid in the
direction of trading less.

### Without a model (all 526 windows, 211 out of sample)

This is what production has today. **No setting of 240 is positive even in
sample.** With a haircut of four cents or more the controller quotes almost
nothing, which beats the deployed pair by $0.06 a window simply by not
trading. The aggressive preset makes 34.5 fills a window and loses $1.34 a
window [-1.45, -1.24]. The same frontier: 0.3 fills -$0.012, 1.8 fills
-$0.056, 5.0 fills -$0.228, 17.6 fills -$0.80, 37.1 fills -$1.42.

### Where the money goes (out of sample, per contract, with a model)

| | entry | fill | completion | unmatched carry | settlement | = net |
|---|---|---|---|---|---|---|
| deployed pair | +0.016 | -0.038 | -0.022 | +0.017 | -0.002 | -0.029 |
| AGGRESSIVE | +0.006 | -0.026 | -0.021 | +0.005 | -0.001 | -0.037 |
| TUNED | +0.017 | -0.035 | -0.026 | +0.027 | +0.006 | -0.012 |

The controller does not change the adverse selection on a fill (-2.6 to
-3.5 cents) or the cost of the hedge (-2.1 to -2.6). TUNED's improvement is
in what it is left holding: the unmatched and settlement columns.

## Answer to the question asked

**Does the integrated approach improve conservative expected settled P&L?**

- **Against the baseline: yes.** The best setting loses $0.012 a window out
  of sample against $0.051, with a smaller worst window (-0.50 against
  -0.64). The improvement was significant in sample and is ahead but not
  significant out of sample.
- **In absolute terms: no.** Its conservative figure (lower bound) is
  **-$0.037 a window, -$0.035 a contract**; its mean is negative; its best
  case is +$0.013 a window. It reaches that by trading once a window.
- **Aggression: no.** Every preset and every grid setting that trades more
  loses more, on both panels and under both fill rules, with intervals well
  below zero.

At one fill a window and under two cents a contract, nothing here
approaches $5 a window; at twenty-five fills a window it costs a dollar.

## What would have to be different

The fill and completion columns are the whole problem and no arrangement
of inventory moves them. They move only if the quote is not there when the
adverse print arrives. The controller already has the input for that - a
supplied probability - and the one-minute model is too slow to show it:
adverse fills happen inside the minute. A sub-second spot reference feeding
the same controller is the one mechanism this corpus cannot test. The
recorded-only stage is the place to measure it, on our own book and our own
fills, once a live probability exists; until then the stage records BOOK
beliefs, which by the result above cannot favor a side.

## Limits of this evidence

- Polymarket's path sampled where L5 traded; PMUS books are thinner.
- Queue position is the trade-through rule and nothing finer.
- The model panel is four days (2026-08-28 to 08-31), one regime.
- One contract per order; no capacity effects.
- TUNED's out-of-sample result rests on 123 fills.

---

# Addendum 2026-11-10: the controller steers the executor, on the live index

## What is connected now

| piece | state |
|---|---|
| exact strike | the venue's own `assetPriceTerms.priceToBeat` for the market, refused unless slug, index (BRTI), market type and both window bounds match |
| live reference | the venue's own settlement series, `GET /v1/asset-prices/history` at one-second buckets: already the 60-second average the market settles on. Free, unauthenticated, same gateway the engine already reads |
| probability | from index, strike distance, time left and the index's own volatility, allowing for the 60-second averaging; volatility inflated 1.25x toward 0.5 |
| rejection | stale index (> 3 s), clock disagreement (> 2 s), index gap, under five minutes of history, strike for another window: no probability, reason recorded |
| latency, measured | newest bucket available 0.2 to 0.9 s after it ends; fetch round trip 0.13 to 0.44 s |
| calibration | 192 windows of the venue's own history (48 h): forecast 0.30 -> realised 0.25, 0.50 -> 0.54, 0.71 -> 0.83 at T+600 s; 98% directional accuracy 60 s before the end. Slightly under-confident by construction |
| cost | $0. No new service, vendor or host; one daemon thread, about one request a second |

## What STEER does (`L5_PORTFOLIO_CONTROLLER=STEER`, off by default)

The existing pair engine keeps its shape and every gate. The controller adds
three things and can only narrow what that engine would do:

1. **Which side may open.** A side the controller would not pay for is not
   opened; with no clear edge, or no usable reference, nothing opens.
2. **How deep.** It can move the plan further behind the touch than the
   approved depth, never closer.
3. **Withdrawal.** A resting opening order priced more than two ticks above
   what the controller would now pay is cancelled by exact id through the
   existing deadman (venue re-read as proof, fills found while cancelling
   booked, ambiguity marks the ledger uncertain).

A leg or order that reduces **filled** inventory is never vetoed and never
withdrawn. The steer exists for one placement call and is cleared in a
`finally`. Authorization, both locks, cash, exposure, order count, daily
loss and the own-settled-evidence gate are untouched.

## The integrated replay

`PYTHONPATH=. python3 analysis/l5/portfolio_controller_replay.py --integrated`
(20 s). The deployed pair engine with the steer and withdrawals exactly as
built, one round a window, order and cancel latency applied. Tuned on the
first 187 windows at 2 s latency; scored on the last 125.

**Evidence class: L5's Polymarket path with a ONE-MINUTE Coinbase reference.
The live venue index has no history on these windows, so this replay cannot
show what the one-second reference adds.**

| out of sample, 125 windows | fills | windows traded | net / contract | net / window | 95% interval |
|---|---|---|---|---|---|
| deployed pair, no controller | 222 | 121 | -0.029 | -0.051 | [-0.085, -0.018] |
| steer, production setting, 0 s latency | 80 | 45 | -0.014 | -0.009 | [-0.029, +0.011] |
| steer, production setting, 2 s latency | 79 | 45 | -0.019 | -0.012 | [-0.033, +0.009] |
| steer, production setting, 5 s latency | 84 | 48 | -0.026 | -0.017 | [-0.041, +0.006] |
| steer, production setting, no withdrawals | 127 | 71 | -0.021 | -0.021 | [-0.049, +0.007] |

Against the deployed pair, per window: +$0.039 [+0.001, +0.078] at 2 s,
+$0.034 [-0.007, +0.074] at 5 s. No setting of the 15 tried was positive
even in sample. Without a reference the steered engine places nothing.

**Conservative net expectancy is not positive.** The steer cuts the loss to
about a quarter of the baseline by trading in a third of the windows;
withdrawals and low cancel latency each help; the lower bound is still
below zero and so is the mean.

## What is not done

- The live index has never been tested against fills: that needs the
  recorded-only stage running with `L5_BTC_REFERENCE=VENUE_INDEX`.
- Sizes are not steered (production clip is already at the minimum).
- Further rounds in a window remain behind the own-settled-evidence gate.
