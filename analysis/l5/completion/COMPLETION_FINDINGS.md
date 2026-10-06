# Terminal completion economics — results

Protocol frozen at **b9bd217a** before any result below. 12 BTC15m windows,
1,936 inferred clips, 3,166 verified fills. S3 unchanged and verified still
frozen at `8b20a259…87df`. No strategy fitting. No P&L-selected threshold.
No live orders.

## Headline

**The opportunistic-accumulation signature is real; its claimed economic
payoff is not established.** L5 demonstrably builds imbalance on the
*cheaper* side and completes on the *dearer* side. But the inventory he
acquires that way is **not** reliably cheaper to complete, and no stable
per-parcel pair-cost ceiling survives confirmation.

The most reproducible quantity in the entire study turned out to be
something the protocol tested only indirectly: **cumulative matched pair
cost sits at 0.973–1.013 at every one of 12 checkpoints, in both alignments,
across all 12 windows.**

## Preregistered endpoints

| endpoint | bar | result | verdict |
| --- | --- | --- | --- |
| **T1** EXTEND vs COMPRESS economics | \|delta\| ≥ 0.33 on ≥1 variable, sign in ≥9/12 | acquisition price **delta −0.387**, sign **10/12** | **PASS** |
| **T2** EXTEND buys cheap completion | ≥0.01 cheaper in ≥9/12 | **7/11**, median advantage +0.02 | **FAIL** |
| **T3** stable pair-cost ceiling | ≥90 % below discovery `C` in ≥9/11 | **7/11**, `C` = 1.12 | **FAIL** |
| **T4** which objective | rho ≤ −0.50 supports O3 | rho **−0.42** (p = 0.18) | **inconclusive** |
| **T5** inventory- vs time-space | median IQR inv < 0.5 × time | matched frac **0.4994** ✓, pair cost **1.25** ✗ | **MIXED** |

### My verdict taxonomy had a gap

The frozen classes were: both T1+T2 pass with T3 → *opportunistic deferred
completion*; T3 without T2 → *bounded, no opportunism*; T2 without T3 →
*opportunism without a ceiling*; and **"neither T1 nor T2 passes" →
refuted.** The actual outcome — **T1 passes while T2 and T3 both fail** — is
covered by none of them. I am not force-fitting it into the nearest label.
The accurate statement is: *directional evidence of opportunistic
accumulation, without demonstrated economic benefit or a stable ceiling.*
That gap is a preregistration lesson, recorded rather than smoothed over.

## T1 — the one finding that passes, and it is one finding

| variable | EXTEND median | COMPRESS median | delta | sign |
| --- | --- | --- | --- | --- |
| **acquisition price** | **0.37** | **0.61** | **−0.387** | 10/12 |
| probability of acquired side | 0.385 | 0.61 | −0.375 | 10/12 |
| residual before | 102 | 129 | −0.175 | **12/12** |
| marginal eventual pair cost | 1.00 | 1.00 | −0.056 | 9/12 |
| cumulative pair cost before | 0.9900 | 0.9913 | −0.030 | 7/12 |
| TTE | 474 s | 479 s | +0.002 | 7/12 |

**The two passing variables are the same variable.** In a prediction market
the price of a side *is* its probability: median \|price − probability\| =
**0.015** (p90 = 0.055, n = 1,920). Reported as **one** result, not two —
the third such collapse in this program, which is why the check is now
routine.

**It is not a tautology of the inventory state.** One might object that
whichever side you buy more of becomes the heavy side, so "EXTEND buys
cheap" merely restates "L5 holds the cheap side." But the heavy side is the
cheap side in only **60.6 %** of clip states (n = 1,912) — well short of the
~100 % that a mechanical identity would require. The price effect is
substantially independent of the imbalance state.

So: **L5 builds imbalance at ~0.37 and completes at ~0.61.** This is a clear
behavioural signature, and it is exactly what the hypothesis predicted —
including predicting A2's refutation of balance-seeking as a *consequence*.

A weaker but perfectly consistent companion: COMPRESS happens at larger
residual than EXTEND (129 vs 102 shares) with the sign holding in **12 of
12** windows, though the effect size (−0.175) is below the bar.

## T2 — the economic payoff is not established

FIFO-attributing every matched share to its source clips:

| window | EXTEND-sourced | COMPRESS-sourced | advantage |
| --- | --- | --- | --- |
| 1787305500 | 0.99 | 1.08 | **+0.09** |
| 1787365800 | 1.02 | 1.09 | **+0.07** |
| 1787307300 | 1.01 | 1.05 | +0.04 |
| 1787306400 / 1787369400 | 0.97 / 1.00 | 1.00 / 1.03 | +0.03 |
| 1787311800 / 1787362200 | 0.99 / 0.97 | 1.01 / 0.99 | +0.02 |
| 1787296500 / 1787363100 | 1.00 / 0.98 | 1.00 / 0.98 | 0.00 |
| 1787290200 | 0.99 | 0.98 | **−0.01** |
| 1787300100 | 1.00 | 0.98 | **−0.02** |

**7 of 11** windows clear +0.01, against a bar of 9. Nine of eleven are
non-negative and the median advantage is +0.02, so the *direction* is
favourable — but two windows run the other way, including the discovery
window itself. **The claim that EXTEND decisions buy cheaper completion is
directionally supported and statistically unestablished.**

## T3 — no stable per-parcel ceiling

Ceiling estimated on the discovery window `…1787300100` **only** (4,834
matched shares), then frozen before the 11 confirmation windows were read.

**C = 1.12.** Share of matched shares below it: **7 of 11** windows reach
90 %, against a bar of 9. Range 0.642–1.000; the two worst are 0.642 and
0.788.

*Protocol deviation, disclosed:* the preregistration named **p95**; the
shared quantile helper emits p90 and not p95. I used **p90** and labelled it
`CEILING_C_p90` rather than silently reporting a different statistic under
the p95 label. p90 is the *looser* of the two for this test (a higher
ceiling lets more shares pass), so the failure is not an artifact of the
substitution.

Note what C = 1.12 means: on the discovery window, 10 % of matched shares
cost **more than 1.12** — loss-making pairs. The per-parcel distribution has
heavy tails.

## T4 — the objective is not discriminated, because the constraint never binds

Terminal state, 12 windows: matched fraction **0.955** (IQR 0.031), pair
cost **0.978** (IQR 0.027), w_up IQR **0.036**.

The discriminating test — do windows with expensive completion end with less
matching? — gives rho = **−0.42**, in the predicted direction but short of
the −0.50 bar and not significant at n = 12 (p = 0.18).

**Both** terminal matching **and** terminal pair cost are tight. O2 is
rejected (it required dispersed `w`). O1 and a **non-binding** O3 are
observationally equivalent here: no window was ever forced to trade matching
against cost, so nothing in the data can separate "maximise matching" from
"maximise matching subject to a ceiling that was never reached." That is a
genuine identification limit, not a weak result.

As preregistered, O1's win is **one** result: equal shares and maximum
matched fraction are the same objective.

## T5 — mixed, and the reason is informative

| quantity | median IQR time-space | median IQR inventory-space | ratio |
| --- | --- | --- | --- |
| matched fraction | 0.0775 | 0.0387 | **0.4994** |
| pair cost | 0.0277 | 0.0347 | 1.2527 |

Matched fraction passes — but at **0.4994 against a 0.5 bar**, a margin far
inside noise. I am not treating that as a robust pass.

Pair cost fails for a revealing reason: **it is already tight in time
space**, so inventory alignment cannot improve it. Matched fraction is
dispersed early in clock time (IQR 0.53 at T+30) and much tighter at 10 %
and 25 % of final inventory (0.35, 0.11) — so inventory-space does help
*matching* specifically.

**Recorded in advance and still binding:** inventory-space alignment
normalises by the window's **final** inventory and therefore uses future
information. It describes what L5 optimised; it can **never** be a live
signal, because a live policy does not know its own final inventory.

## The finding the protocol under-tested

Across **all 12 checkpoints, in both alignments, all 12 windows**:

- cumulative matched pair cost median: **0.9734 – 1.0132**
- its cross-window IQR: **0.0112 – 0.0763**

compared with matched fraction, whose IQR ranges **0.0140 – 0.5277**.

Cumulative pair cost is **an order of magnitude more reproducible** than
matched fraction, and it is pinned from the first match onward rather than
converging. Meanwhile *per-parcel* pair cost has heavy tails (p90 = 1.12,
T3). Those two facts together are the signature of a trader managing a
**running average**, not each individual pair — which is precisely why T3,
specified on per-parcel costs, failed while the cumulative quantity is rock
stable.

This was not a preregistered endpoint. It is **descriptive, and it is the
hypothesis a next study should preregister** — not a result I am claiming.

## Final answer

**What appears to be L5 optimising over the entire window?**

> **Accumulate as much matched inventory as possible while holding the
> running cumulative matched pair cost at ≈ 0.98.**

Formally, the smallest objective consistent with the evidence:

$$\max\ M(T) \quad\text{subject to}\quad \overline{PC}(t) \approx 0.98 \ \ \forall t$$

where $M$ is matched shares and $\overline{PC}$ is the volume-weighted
cumulative pair cost. One objective, one constraint, one constant.

The mechanism reaching it, in the order the evidence supports it:

1. **Buy the cheap side when it is cheap** (EXTEND at ~0.37) — T1, 10/12.
2. **Tolerate large intermediate imbalance** — residual reaches 102–129
   shares routinely, and early allocation is often 100 % one-sided.
3. **Complete on the dearer side** (COMPRESS at ~0.61) — T1, and at
   *larger* residual, 12/12 in sign.
4. **Hold the running average near 0.98 throughout**, not each pair — which
   is why individual matched parcels scatter up to 1.12 and beyond.
5. **Terminal matching ~0.955** falls out as a consequence of (1)–(4), not
   as an independently targeted quantity.

### What this does not support

- **Not** that EXTEND decisions are *economically* superior: T2 is 7/11.
- **Not** a per-parcel pair-cost ceiling: T3 is 7/11, and 10 % of discovery
  matched shares exceed 1.12.
- **Not** a constrained-optimisation reading over a maximising one: T4
  cannot separate them, because the constraint never bound.
- **Not** anything implementable from inventory-space alignment, which uses
  future information.
- **Not** a reason to change S3, and I have not. S3's reservation of 0.96 is
  *tighter* than L5's observed 0.98 running average; whether that is right
  is beyond what this study can settle, and changing it on the strength of a
  descriptive finding that was not a preregistered endpoint would be exactly
  the fitting the brief forbids.
