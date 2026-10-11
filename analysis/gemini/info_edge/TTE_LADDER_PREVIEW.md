# Executable-book ladder at fixed TTE — PREVIEW at n=15 (not a result)

Descriptive only. No strategy fitted, no threshold chosen, **sealed OOS not
read**, no live orders. The full read is due at **100 usable windows**.

## NO COLLECTION CHANGE WAS NEEDED — VERIFIED, NOT ASSUMED

The final-60s trajectory is **already complete** in the existing tapes:

| | |
|---|---|
| depth20 snapshots in final 60 s | **59–60 per window** |
| plus every `@depth` diff | yes, nothing downsampled |
| max inter-event gap | **p50 1.06 s, worst 2.01 s** |
| coverage of the final 60 s | **59.0 s of 60** |

So the T−60/45/30/20/15/10/5/2/1 ladder is an **extraction** from data we
already hold, not new collection. The running collector was not disturbed.

## 1. AN EXECUTABLE UP ASK OFTEN DOES NOT EXIST

| TTE | n | has_ask | **has_ask %** | ask p50 | ask size p50 | spread p50 | two-sided % |
|---|---|---|---|---|---|---|---|
| 60 s | 15 | 8 | **53.3%** | 0.380 | 243.5 | 0.345 | 26.7% |
| 45 s | 15 | 8 | **53.3%** | 0.205 | 188.5 | 0.320 | 20.0% |
| 30 s | 15 | 5 | **33.3%** | 0.500 | 14.0 | — | 0.0% |
| 20 s | 15 | 6 | 40.0% | 0.440 | 13.0 | — | 0.0% |
| 15 s | 15 | 6 | 40.0% | 0.440 | 17.4 | 0.230 | 6.7% |
| 10 s | 15 | 6 | 40.0% | 0.405 | 17.4 | 0.290 | 6.7% |
| **5 s** | 15 | 5 | **33.3%** | 0.280 | 14.0 | — | 0.0% |
| 2 s | 15 | 4 | 26.7% | 0.260 | 10.0 | — | 0.0% |
| 1 s | 15 | 3 | **20.0%** | 0.500 | 14.0 | — | 0.0% |

**The book is two-sided 0–27% of the time late in the window**, and displayed
size collapses from ~240 contracts at T−60 to ~10–17 from T−30 onward.

**10 of 15 windows (67%) had no buyable UP at all at T−5 s.** The forensic
window was typical, not exceptional.

## 2. THE CROSS-TAB YOU ASKED FOR — AND IT INVERTS THE HOPED-FOR ANSWER

"At T−30, Coinbase +$X above strike, UP ask 0.78 × 25 → how often does UP
settle?" Fixed buckets, not fitted.

**TTE = 30 s**

| cb distance | n | **UP won %** | **has_ask** | ask p50 | DOWN px p50 |
|---|---|---|---|---|---|
| ≤ −20 | 4 | **0.0%** | **4** | 0.390 | — |
| −20..−5 | 2 | 0.0% | 1 | 0.500 | — |
| 0..+5 | 2 | 50.0% | **0** | — | — |
| +5..+20 | 3 | **100.0%** | **0** | — | 0.345 |
| > +20 | 4 | **100.0%** | **0** | — | 0.185 |

**TTE = 5 s**

| cb distance | n | **UP won %** | **has_ask** | ask p50 | DOWN px p50 |
|---|---|---|---|---|---|
| ≤ −20 | 4 | **0.0%** | **4** | 0.260 | — |
| −20..−5 | 2 | 0.0% | 1 | 0.500 | — |
| 0..+5 | 1 | 100.0% | 0 | — | — |
| +5..+20 | 4 | 75.0% | **0** | — | 0.565 |
| > +20 | 4 | **100.0%** | **0** | — | 0.090 |

### The pattern, stated plainly

**In every bucket where UP was going to win, `has_ask` = 0 — there was no UP
offer at all. UP was purchasable only in the buckets where it lost 0% of the
time.**

And symmetrically: in the favourable-for-UP buckets the only executable action
was **buying DOWN at 0.185–0.565** — i.e. buying the side that then lost.

**At n=15 the executable side was systematically the LOSING side.**

### Why this is probably not a bug, and probably not an edge either

The mechanism is rational, not pathological: **nobody offers a near-certain
winner.** When BTC sits $20+ above the strike with 30 seconds left, UP is worth
~1.00, and no maker posts it at a price worth lifting. The *absence of an ask*
is the market being efficient, not broken.

This is the same selection effect this programme has now measured four times
in different clothing: maker fills in the closed lane, directional maker fills
at gate A, and now executable *offers* conditional on state.

## 3. WHAT THIS DOES AND DOES NOT ESTABLISH

**Does not establish anything.** n=15, with 1–5 observations per bucket. The
Coinbase-distance → settlement relationship (0% below, 100% above) is also
**largely mechanical** — if BTC is far above the strike near expiry the index
usually settles above it, and the market sees exactly the same thing.

**What it does is sharpen the n=100 question into something falsifiable:**

> Does a cheap UP ask ever exist in the favourable buckets, or do offers appear
> only when the favourite is about to lose?

If the n=15 pattern holds at n=100, the information-edge hypothesis fails not
on prediction but on **availability** — the predictable outcomes are precisely
the ones nobody will sell you. That is a different failure mode from the
earlier lanes and worth stating cleanly when the data supports it.

## 4. ONE PRACTICAL CONSEQUENCE FOR MANUAL TRADING

When a trade is described as "I bought the 70% favorite", the **70% is not
necessarily the fill price and may not have been purchasable at all**. The
fields that matter are `up_ask` / `up_ask_sz` (or `down_price` / `down_sz`) at
the submission instant, which this ladder now records for every window. Actual
order and fill prices from the account would be needed to reconcile any manual
trade against these.
