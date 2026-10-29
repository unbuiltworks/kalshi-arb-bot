# Three residual treatments, out of sample on L5's own fills

Probe 07 is nine fills. L5's Sep 23–25 are **22,512 real fills across 844
scored market-days with real settlements**, and they are the only set large
enough to say whether a residual rule has an expectancy rather than a story.

## Preregistration, fixed before any result was read

* Thresholds come from **inventory geometry**: the cap is `k ×` the largest
  clip the market has **already** shown (causal, never a look-ahead), the
  age limit is a fraction of the 900s window. Never a sweep over settled
  outcomes.
* `k < 1` is degenerate — a cap below one clip cannot open a position — so
  the grid starts at `k = 1`.
* **Sep 23 is the fit day. Sep 24 and Sep 25 are held out** and scored once.
* The counterfactual is **fill-for-fill**: an action taken fills as recorded,
  an action suppressed does not happen. Load-bearing, and stated.
* Settlement is used to **score** a replay and never to make one.
  `assert_blind` runs on every decision input.
* Incentives are **real for Native** (the venue's own daily rebate rows) and
  **modelled** for every treatment, scaled by the fraction of quantity still
  taken, because the venue reports rebates as one daily lump with no
  `conditionId`. No treatment's rebate is a realized number. **`trading net`
  contains no rebate at all and is the honest line.**

Coverage: 323 / 267 / 254 markets scored; 3 / 2 / 2 dropped as unscorable
(two-sided with no redemption row, so no winner is derivable). All 22,512
rows are BUY — L5 never sells, he acquires and lets settlement happen, which
is exactly the shape this ledger models.

## Held out: Sep 24 + Sep 25

| axis | NATIVE | CAP k=1 | AGE 120s | COMPLEMENT_REPAIR | PAIRCOST ≤1.00 | ≤0.98 | ≤0.96 |
|---|---:|---:|---:|---:|---:|---:|---:|
| actions taken | 12,515 | 7,799 | 5,787 | 4,611 | 9,540 | 9,193 | 9,036 |
| fills suppressed | 0% | 37.7% | 53.8% | 63.2% | 23.8% | 26.5% | 27.8% |
| matched qty | 154,685 | 86,197 | 79,838 | 65,742 | 65,546 | 55,673 | 51,922 |
| matched P&L | +657.8 | +505.8 | +617.3 | **+703.1** | +11,357.8 | +11,036.6 | +10,942.6 |
| avg pair cost | 0.99575 | 0.99413 | 0.99227 | **0.98931** | 0.82672 | 0.80176 | 0.78925 |
| residual qty | 73,146 | 20,225 | 22,415 | **17,850** | 150,497 | 159,592 | 162,160 |
| residual P&L | −4,587.6 | −492.1 | −358.8 | **−336.9** | −21,937.0 | −14,647.1 | −15,017.9 |
| resid loss/share | −0.0627 | −0.0243 | −0.0160 | −0.0189 | −0.1458 | −0.0918 | −0.0926 |
| max residual, 1 mkt | 1,674 | 107 | 495 | **107** | 6,502 | 6,976 | 6,976 |
| maker economics* | +1,587.3 | +799.2 | +754.8 | +619.2 | +1,168.1 | +1,123.9 | +1,103.5 |
| **trading net** | **−3,929.8** | +13.6 | +258.5 | **+366.2** | −10,579.2 | −3,610.5 | −4,075.3 |
| TOTAL | −2,342.4 | +812.9 | +1,013.3 | +985.4 | −9,411.1 | −2,486.7 | −2,971.9 |
| capital deployed | 172,753 | 94,207 | 90,176 | 73,130 | 103,747 | 96,870 | 95,349 |
| return on capital | −227.5 bp | +1.4 bp | +28.7 bp | **+50.1 bp** | −1,019.7 bp | −372.7 bp | −427.4 bp |
| drawdown | 3,967.5 | 664.0 | 802.5 | **591.5** | 12,852.2 | 8,391.6 | 8,153.7 |

`*` modelled for treatments; real only for Native.

Sep 23, the fit day, ranks the same way: Native trading −2,227.7,
COMPLEMENT_REPAIR +395.9, CAP k=1 +242.6. Selecting on the fit day by
trading net picks COMPLEMENT_REPAIR, and it is the best trading arm on the
held-out days too.

## Coverage, and why these totals are smaller than the published day figures

`L5_SEP23_25_DECOMPOSITION.md` reports Sep 24 trading net **−8,876**; this
replay reports **−2,072** for the same day. The difference is not a
disagreement about arithmetic — it is which markets are scorable.

The replay scores a market only where the winner is derivable from an actual
REDEEM row (or from a one-sided position that redeemed nothing). It reproduces
the published redemption to the dollar on the markets it does score: Sep 24
redemption **107,259.51** against the ledger's **107,260**. It covers 94.1%
of the day's spend. The remaining **4 markets over the two held-out days,
7,795 of spend (4.3%)**, had no redemption row at harvest — their settlement
landed after the Sep 25 16:02 cut, including one market of 6,719.69 that is
almost certainly the "$6,739.69 of redemptions for markets traded earlier"
the published file already flagged. The published convention scores a market
with no redemption row as a total loss, which for these four is a harvest
truncation artifact rather than an established loss.

Both arms are scored on the identical market set, so the **delta** is
unaffected. The absolute Native figure here is smaller in magnitude than the
published day total, and should not be quoted as if it were that total.

**Adversarial bound on the excluded four.** Settle them so that Native gains
the most possible and COMPLEMENT_REPAIR gains the least possible — Native
+406.6, REPAIR −115.9 — and the held-out trading edge falls from **+4,296.0
to +3,773.5**. The conclusion does not depend on them.

## The control: is this just "trade less"?

Every treatment cuts volume, and less volume is less exposure. A state-blind
random suppressor matched on each arm's realized take rate, five seeds,
same held-out days:

| arm | take rate | trading | matched-rate random | random spread | **edge** |
|---|---:|---:|---:|---|---:|
| CAP k=1 | 62.3% | +13.6 | −2,400.9 | −3,339.9 … −1,626.7 | **+2,414.6** |
| AGE 120s | 46.2% | +258.5 | −1,668.8 | −2,377.4 … −1,098.7 | **+1,927.2** |
| COMPLEMENT_REPAIR | 36.8% | +366.2 | −1,239.8 | −2,291.8 … −324.1 | **+1,606.1** |

Random suppression reproduces almost exactly the proportional scaling of the
loss (−3,929.8 × 0.623 ≈ −2,448 against an observed −2,400.9) and nothing
more. **Volume reduction shrinks the loss; only the state-dependent rule
flips its sign.** Every treatment lands outside the full random spread.

## The result that matters most is the one that failed

`PAIR_COST_CAP` refuses a completing leg whose pair would cost more than the
threshold — a direct reading of the operating model's own "prefer pair
completion around ≤ 0.96". **It is the worst thing in the table.** It makes
the matched book look magnificent — average pair cost 0.789, matched P&L
+10,943 — by refusing every expensive completion, and in doing so it leaves
the opening legs naked: residual quantity **doubles** to 162,160 and residual
P&L collapses to −15,018. Trading net is worse than Native at ≤0.96 and
catastrophically worse at ≤1.00.

The lesson is precise and it corrects a natural reading of the discipline:
**refusing to complete does not undo the opening leg.** It converts a
slightly-bad pair into a wholly directional bet. The pair-completion price
is not where the money is; the decision to acquire *while already imbalanced*
is.

## The specific question: suppressing same-side acquisition while imbalanced

That is exactly `COMPLEMENT_REPAIR`. Held out:

* residual P&L **−4,587.6 → −336.9**, i.e. **92.7% of the residual loss
  removed**;
* matched **quantity** 154,685 → 65,742, i.e. **57.5% of matched production
  destroyed**;
* matched **P&L** +657.8 → **+703.1**, i.e. matched profit **improved 6.9%**
  despite producing 57.5% less of it, because average pair cost fell from
  0.99575 to 0.98931 — the pairs it keeps are the cheaper ones;
* worst single-market residual 1,674 → 107 shares; drawdown 3,967.5 → 591.5.

So the answer is: **yes, and it does not destroy matched-pair production in
the sense that matters.** It destroys matched *volume*. It does not destroy
matched *profit* — it raises it. Anyone who measures this program by turnover
will read that as a 57% cut; the P&L reads it as the opposite.

## What this does not establish

* The fill-for-fill assumption is load-bearing. `COMPLEMENT_REPAIR` by
  construction wants exactly the side the market is moving away from. In
  L5's record those fills are real, because he got them. **We would be
  resting on the harder side**, and our fill rate there is unmeasured. This
  is the single largest threat to the result and the first thing Probe 08
  must measure.
* Three days of one trader are not a distribution. Sep 23–25 was a losing
  period for L5; a rule that helps most when the residual is bleeding may
  help less when it is not.
* Every rebate figure for a treatment is modelled, never realized.
