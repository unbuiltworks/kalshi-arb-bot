# Mechanism audit: `trade_imbalance >= 0.75 @ 2s`, and restricted lead/lag

Signal taken exactly as pre-declared. No threshold, horizon, ceiling, ensemble or
latency was varied. 1730z and 1745z produced this candidate and therefore cannot
validate it; nothing is promoted here.

## Lead/lag, 1730z + 1745z only (replaces the withdrawn five-window figure)

`excluded_by_only: None` — the two windows were the only ones admitted, so no
filtering was needed this time. n = 15,912; reference skew p50 0.0041 s, p90 0.0194 s.

**Peak correlation at lag +0.05 s, corr 0.5138**, with a near-symmetric shoulder
(0.5062 at −0.05 s, 0.5124 at 0.00 s, 0.5015 at +0.10 s) decaying to 0.0643 at −1 s
and 0.0704 at +1 s. A 50 ms nominal lead at p90 skew of 19 ms is inside the noise of
what this pair of clocks can resolve. **The reference does not usefully lead PMUS**,
confirming the earlier conclusion on clean data and a 3.8× larger sample.

## Causal and temporal provenance — clean

- **Formula, verbatim:** `seg = [y for y in rows[:i] if y["t"] >= t - 2.0]`;
  `adv / (buy_qty + sell_qty)` where `adv` is sell volume for a resting BUY and buy
  volume for a resting SELL.
- **Adverse direction:** `dir` is the taker's side. A taker SELL consumes the BID, so
  it is adverse to our resting BUY; a taker BUY consumes the ASK, adverse to our
  resting SELL. The mapping is correct.
- **Strictly before the fill:** checked against the data, not inferred from the code.
  **890 of 890 lots: zero violations.** `rows[:i]` excludes the filling trade.
- **Computable live:** onset is found by re-evaluating the same feature at each
  preceding trade event using only `rows[:j]`, so every value reflects information the
  controller would already hold. No future data enters.

## Where it separates — and where it does not

| window | ceiling | n_tox | n_qual | P(flag\|tox) | P(flag\|qual) |
|---|---|---|---|---|---|
| **1730z** | 0.99 | 19 | 406 | **0.3158** | 0.1675 |
| 1730z | 0.97 | 75 | 350 | **0.1333** | 0.1829 |
| 1730z | 0.95 | 81 | 344 | **0.1358** | 0.1831 |
| **1745z** | 0.99 | 123 | 342 | **0.8455** | 0.2193 |
| 1745z | 0.97 | 181 | 284 | 0.6519 | 0.2148 |
| 1745z | 0.95 | 248 | 217 | 0.4960 | 0.2581 |

**The pooled 0.775 was one window.** In 1730z the signal is weak at c.99 and
**inverted** at c.97 and c.95 — it flags qualifying fills slightly more often than
toxic ones. In 1745z it is strong (0.8455 vs 0.2193 at c.99). The two windows do not
agree, and 1730z has only 19 toxic lots at c.99, so the pooled figure was dominated
by 1745z.

Onset lead also differs sharply: 1730z p50 0.613 s with 50.0% at or above the 0.612 s
p90 RTT; 1745z p50 4.008 s with 72.6%.

## Economic counterfactual, priced from actual per-lot cash

`DELTA_NET` is the change in window net if every flagged lot whose warning arrived at
least one cancel RTT early were cancelled. A warning inside the latency counts as
**not avoided**.

| window | ceiling | latency | avoided toxic (net removed) | sacrificed qualifying (net removed) | too late | **DELTA_NET** |
|---|---|---|---|---|---|---|
| 1730z | 0.99 | p90 0.612 s | 6 (−0.93) | 31 (+5.86) | 0 | **−4.93** |
| 1730z | 0.97 | p90 | 10 (−1.21) | 27 (+9.03) | 0 | **−7.83** |
| 1730z | 0.95 | p90 | 11 (−1.27) | 26 (+16.82) | 0 | **−15.55** |
| **1745z** | 0.99 | p90 | 82 (−51.08) | 48 (+5.15) | 22 | **+45.94** |
| 1745z | 0.97 | p90 | 87 (−61.23) | 43 (+13.94) | 31 | **+47.28** |
| 1745z | 0.95 | p90 | 87 (−61.23) | 43 (+23.02) | 36 | **+38.21** |

At p50 latency 1745z reaches **+71.96** at c.97; at the conservative 1.0 s, **+40.77**.
1730z is negative at every ceiling and every latency.

**Your economic intuition is confirmed where the signal fires, and the asymmetry is
real on clean data.** In 1745z at c.97 the 118 flagged toxic lots carry −90.85 of net
while the 61 flagged qualifying lots carry only +24.50 — so cancelling removes about
3.7× more loss than profit. That is not inherited from the censored tape's
−$4.21/+$0.21; it is recomputed from these windows.

But the same table shows why this is not yet a rule: **1730z's baseline net is
+35.29 (c.99) and the signal would have made it worse at every setting**, while
1745z's baseline is **−56.50** and the signal recovers most of it. The signal helps
in the window that was losing and hurts in the window that was winning.

## Verdict

- Causal: **yes.** Correct adverse-direction mapping, strictly pre-fill, zero
  provenance violations, computable from live information.
- Actionable: **yes, where it fires.** 72.6% of 1745z warnings arrive ≥0.612 s ahead;
  50.0% in 1730z.
- Economically meaningful: **only in one of two windows.** +45.94 against −4.93 at
  matched settings.

The standard was "freeze exactly one candidate rule **only if** the mechanism remains
causal, actionable and economically meaningful." It is causal and actionable, and
economically meaningful in one window out of two, with inverted selectivity in the
other. **I am not freezing a rule on that basis.** Doing so would repeat the A420/A540
error — promoting a result that one window produced.

The honest next step is more pristine windows with the signal computed but not acted
on, to see whether 1730z or 1745z is the exception. No parameter was tuned, no rule
frozen, no orders.
