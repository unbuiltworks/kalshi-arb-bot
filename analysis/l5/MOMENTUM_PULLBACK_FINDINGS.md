# TWO-SIDED BASE + FAVORITE MOMENTUM + PULLBACK EXIT

**This is not the strategy that was rejected.** The hold-to-settlement
favorite/flip rule lost because after a flip it held both legs bought
above 0.50, so matched pairs cost >$1.00. That test had no exit rule, so
its −$0.395/window says nothing here. Separate lane from the frozen .96
control and the L5 model.

Data: Zenodo 22084977 `updown_15m.parquet`, 33 BTC15m windows, 1-second
full **bid and ask** ladders. **BUYS walk the ask ladder upward; SELLS
walk the bid ladder downward.** The pullback high is measured on the
**bid** — what we could actually sell into — because measuring it on the
mid or ask would manufacture the entire edge. Nothing held is called
realized. Split by window, 19 train / 14 test; threshold chosen on
**train only**.

## Headline: positive at zero fees, negative with fees, and it inverts with size

Per window, all 33 windows, $1 base + $1 extensions, 60 s cadence:

| arm | train | **test** | all 33 | win% | ext realized | max DD |
|---|---|---|---|---|---|---|
| 1 momentum, hold to settlement | −0.361 | +0.939 | +0.190 | 63.6 | −0.092 | −11.02 |
| 2 momentum, no base, 3¢ exit | +0.012 | +0.573 | +0.250 | 69.7 | +0.112 | −2.99 |
| **3 base + momentum, 3¢ exit** ← selected on train | **+0.024** | **+0.554** | **+0.249** | 66.7 | +0.112 | −2.99 |
| 3 base + momentum, 5¢ exit | +0.019 | +0.962 | +0.419 | 69.7 | +0.167 | −3.59 |
| 4 liquidate base too, 5¢ exit | +0.125 | +1.041 | +0.514 | 69.7 | +0.259 | −4.10 |

### Four things that must be said plainly

**1. The train/test gap is sample luck, not evidence.** *Every* arm —
including the hold-to-settlement control — goes from ≈0 on train to
+0.55…+1.04 on test. The 14 test windows are simply easier. The honest
figure is the all-33 column, where arm 3 is **+0.25/window**.

**2. My arm 1 is not the previously rejected strategy.** It extends only
when the side is *strengthening*, where the rejected rule bought every
cadence tick regardless of direction. The true negative control remains
the earlier −$0.395/window result. I am not claiming this run reversed it.

**3. The two-sided base contributes nothing.** Arm 2 (no base) and arm 3
(base) are identical to three decimals at every threshold. Base pair cost
is **1.0112** (median 1.01) — buying both sides at the same instant costs
more than a dollar, so the base is a small *guaranteed* loss
(−$0.0014/window, −$0.046 total). It is economically inert at best.

**4. Fees erase it.** At a 2%-per-dollar sensitivity, arm 3 goes from
**+0.249 to −0.063** per window. Gross bought is ~$15/window, so 2% is
~$0.30 against a $0.25 edge. There is no authenticated Polymarket fee
measurement in this repository, so the base case is zero and labelled —
but the margin is thinner than plausible fees.

## Your central catch, answered: the sell does not survive size

Arm 3, 3¢ exit, varying the extension size:

| ext $ | net/win | **ext realized** | win% | peak dir. exposure | net @ 2% fees |
|---|---|---|---|---|---|
| 1 | +0.249 | +0.112 | 66.7 | $5.9 | −0.063 |
| 5 | +1.044 | +0.355 | 63.6 | $29.5 | −0.357 |
| **10** | **+1.563** | +0.186 | 63.6 | $59.1 | −1.198 |
| 25 | +1.522 | **−1.909** | 57.6 | $147.7 | −5.321 |
| 50 | −1.783 | **−8.638** | 51.5 | $295.5 | −15.429 |
| 100 | −17.673 | **−31.023** | 51.5 | $590.9 | −44.926 |

**Extension realized P&L — the part the strategy exists to earn — turns
negative at $25 and collapses to −$31/window at $100.** Walking down the
bid ladder destroys it exactly as you predicted. Note `unsold_shares = 0`
at every size: the ladder always had *some* depth, so nothing failed to
fill — it filled at progressively worse prices. **That is why $1 looked
clean and tells us nothing about capacity.**

Max defensible extension is roughly **$10**, giving ~+$1.56/window at
zero fees and **−$1.20 with 2% fees**.

## Flip conditioning reverses, but flip-heavy windows still lose

| flips | windows | net mean | ext realized mean |
|---|---|---|---|
| 0 | 2 | **+1.091** | +1.220 |
| 1 | 13 | +0.808 | +0.563 |
| 2 | 8 | +0.163 | +0.273 |
| 3+ | 10 | **−0.578** | **−0.824** |

Unlike the rejected rule, 1-flip windows are now profitable — the exit
does help. But **3+ flip windows still lose**, and the extension itself
is the loser there. Choppy windows are not harvested; they are paid for.

## Does the live executor support the BUY → SELL lifecycle?

**Partly, and it has never been exercised.**

- `spec.SIDE_SELL = "sell"` exists and `payload.validate_structure`
  accepts it, so a sell is *expressible*.
- But `ci15m_probe.submit()` hardcodes `side=payload.spec.SIDE_BUY`,
  `assert_payload_is_q1` builds a buy body only, and **no sell has ever
  been sent to Gemini from this repository**. There is zero authenticated
  evidence the venue accepts one, what it does to a position, or what it
  costs.
- `transport` has `place` and `cancel_and_confirm`. **Cancel is not
  sell** — cancelling removes a resting order; it does not liquidate an
  acquired position.
- On Gemini, DOWN is *synthesized* as 1 − UP. If selling UP is
  unsupported, the only exit is buying DOWN — which creates a matched
  pair rather than releasing capital, and is therefore **not this
  strategy**.

**So the Gemini lane cannot run this yet, and the gap is a venue
capability question, not a tuning one.** It needs a read-only
determination of whether the prediction-markets API accepts a sell and
what it does to inventory — before any shadow, let alone a probe.

## Where this leaves the hypothesis

Not rejected, not established. At zero fees and small size it is
positive; plausible fees erase it; and the edge inverts well before
interesting size. The next steps that would actually move it, in order:

1. **Read-only**: does Gemini accept a sell on a prediction-market
   position at all? Everything downstream depends on this.
2. An authenticated **fee** measurement for whichever venue is used —
   the strategy lives or dies inside the fee margin.
3. More windows. 33 windows over 4 days, with the train half flat, is not
   an expectancy estimate.

**No live capital should follow this, and it should not touch the frozen
strategy's authorization.**
