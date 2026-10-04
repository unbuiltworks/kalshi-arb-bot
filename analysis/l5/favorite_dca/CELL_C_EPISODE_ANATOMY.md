# Cell C economic anatomy — read-only, 300 episodes, no rule changed

Instrumentation only. Nothing reads the episode records, so no trade can
change, and the frozen cells re-run unchanged to prove it:

| | gross | net/window |
|---|---|---|
| V1-no-seed (A) | −27.0638 | −1.17787 |
| B strengthening only | −13.3905 | −0.55430 |
| **C reversal only** | **+10.0346** | **−0.04948** |
| D0 (D) | +3.8959 | −0.00636 |

No C2. No threshold. No replay change. No live orders.

Cell C produced **300 episodes**: 279 closed by a reversal exit, 21 still
open at expiry and settled. 387 entries across them (1.29 additions each).

## Cell C episode anatomy — 300 episodes over 28 windows

| | total | mean | median | p10 | p90 |
|---|---|---|---|---|---|
| gross P&L | +10.0355 | +0.03345 | -0.00000 | -0.05660 | +0.16279 |
| total fees | +11.4202 | +0.03807 | +0.03870 | +0.00690 | +0.06420 |
| net P&L | -1.3847 | -0.00462 | -0.02398 | -0.11734 | +0.12352 |
| holding seconds | — | 36.7 | 13.4 | 1.7 | 80.3 |
| additions per episode | 387 | 1.29 | 1 | 1 | 2 |
| shares | 519.4 | 1.73 | 1.52 | | |
| notional $ | 386.67 | 1.289 | 1.000 | | |
| MFE per share | — | +0.03142 | +0.01000 | -0.01000 | +0.11000 |
| MAE per share | — | -0.04068 | -0.03753 | -0.07495 | -0.01000 |

### The two questions

- **episodes whose gross is smaller than their own round-trip fee: 193 of 300 = 64.3%**
  - they carry -4.5820 of gross (-45.7% of the +10.0346) and 7.6995 of fees (67.4% of the 11.42), net -12.2815
- the other 107 episodes carry 14.6175 gross, 3.7207 fees, net +10.8968
- cumulative gross ranked by episode gross (DESCRIPTIVE ONLY, not a rule):
  - top **13** episodes (4.3%) reach 50% of total gross
  - top **24** episodes (8.0%) reach 80% of total gross
  - top **29** episodes (9.7%) reach 90% of total gross
  - top **35** episodes (11.7%) reach 100% of total gross
- gross-negative episodes: 154 (51.3%), total -5.3832

### Descriptive buckets — exogenous dimensions already in the frozen strategy

**holding duration**

| bucket | n | gross | fees | net | gross/fees | mean gross/episode | median hold s |
|---|---|---|---|---|---|---|---|
| <5s | 69 | -1.7114 | 2.3676 | -4.0790 | -0.723 | -0.02480 | 2 |
| 5-20s | 101 | -0.1806 | 4.1626 | -4.3432 | -0.043 | -0.00179 | 10 |
| 20-60s | 80 | +5.7766 | 2.9563 | +2.8203 | 1.954 | +0.07221 | 33 |
| 60-180s | 38 | +4.6928 | 1.7841 | +2.9087 | 2.630 | +0.12350 | 77 |
| >=180s | 12 | +1.4580 | 0.1496 | +1.3084 | 9.746 | +0.12150 | 240 |

**additions during episode**

| bucket | n | gross | fees | net | gross/fees | mean gross/episode | median hold s |
|---|---|---|---|---|---|---|---|
| 1 | 253 | +4.0885 | 9.4990 | -5.4105 | 0.430 | +0.01616 | 11 |
| 2 | 31 | +3.8873 | 1.5801 | +2.3072 | 2.460 | +0.12540 | 76 |
| 3-4 | 11 | +1.3817 | 0.2956 | +1.0861 | 4.674 | +0.12561 | 189 |
| 5+ | 5 | +0.6781 | 0.0455 | +0.6326 | 14.902 | +0.13561 | 360 |

**entry favourite price**

| bucket | n | gross | fees | net | gross/fees | mean gross/episode | median hold s |
|---|---|---|---|---|---|---|---|
| <0.60 | 64 | +1.5146 | 4.1897 | -2.6751 | 0.362 | +0.02367 | 13 |
| 0.60-0.75 | 96 | +5.4849 | 4.7972 | +0.6877 | 1.143 | +0.05713 | 12 |
| 0.75-0.90 | 70 | +2.1958 | 1.8961 | +0.2997 | 1.158 | +0.03137 | 14 |
| >=0.90 | 70 | +0.8402 | 0.5372 | +0.3030 | 1.564 | +0.01200 | 20 |

**seconds to settlement at entry**

| bucket | n | gross | fees | net | gross/fees | mean gross/episode | median hold s |
|---|---|---|---|---|---|---|---|
| >600s | 96 | +3.6010 | 5.2309 | -1.6299 | 0.688 | +0.03751 | 15 |
| 300-600s | 121 | +3.6322 | 4.4039 | -0.7717 | 0.825 | +0.03002 | 13 |
| 120-300s | 55 | +1.5269 | 1.3771 | +0.1498 | 1.109 | +0.02776 | 11 |
| <=120s | 28 | +1.2755 | 0.4083 | +0.8672 | 3.124 | +0.04555 | 18 |

**leadership flip during episode**

| bucket | n | gross | fees | net | gross/fees | mean gross/episode | median hold s |
|---|---|---|---|---|---|---|---|
| no flip | 300 | +10.0355 | 11.4202 | -1.3847 | 0.879 | +0.03345 | 13 |

**outcome class**

| bucket | n | gross | fees | net | gross/fees | mean gross/episode | median hold s |
|---|---|---|---|---|---|---|---|
| reversal exit | 279 | +8.7012 | 11.3314 | -2.6302 | 0.768 | +0.03119 | 12 |
| held_to_settlement | 21 | +1.3343 | 0.0888 | +1.2455 | 15.026 | +0.06354 | 120 |

### Fee reduction arithmetic (gross held constant at +$10.0346)

| target net | net over 28 windows | fees allowed | **fee reduction required** |
|---|---|---|---|
| break-even | 0.0000 | 10.0346 | **12.1%** |
| $0.05/window | 1.4000 | 8.6346 | **24.4%** |
| $0.10/window | 2.8000 | 7.2346 | **36.7%** |
| $0.20/window | 5.6000 | 4.4346 | **61.2%** |
| $0.30/window | 8.4000 | 1.6346 | **85.7%** |
| $0.358/window (all gross) | 10.0352 | **impossible** — exceeds total gross | n/a |
| $5/window | 140.0000 | **impossible** — exceeds total gross | n/a |

### Available DURING the episode, not only after it

- survived **5s**: n=231 gross +11.7469 fees 9.0526 ratio 1.298 net +2.6943  |  died earlier: n=69 gross -1.7114 fees 2.3676 net -4.0790
- survived **20s**: n=130 gross +11.9275 fees 4.8900 ratio 2.439 net +7.0375  |  died earlier: n=170 gross -1.8920 fees 6.5302 net -8.4222
- survived **60s**: n=50 gross +6.1509 fees 1.9337 ratio 3.181 net +4.2172  |  died earlier: n=250 gross +3.8846 fees 9.4865 net -5.6019

### By window

- episodes per window: median 11, min 5, max 14
- window gross: median +0.4180, 24 of 28 positive, worst -0.2148, best +0.7954
- correlation check: windows with more episodes are not the profitable ones — gross per episode by window-episode-count tercile: 0.0442, 0.0272, 0.0297

## Reading

**1. Two thirds of the turnover is economically useless, and it is worse
than useless.** 193 of 300 episodes (64.3%) fail to clear their own
round-trip fee. Together they produce **−$4.58 of gross** — they do not
merely contribute little, they *destroy* gross — while consuming **$7.70 of
fees, 67.4% of the whole fee bill**, for a net of **−$12.28**. The other 107
episodes make **+$14.62 of gross on $3.72 of fees, net +$10.90.**

**2. The gross is concentrated, but the concentration is not fragile in the
way B's was.** The top 13 episodes (4.3%) carry half the gross and the top
35 (11.7%) carry all of it — yet the *window-level* distribution stays
robust: 24 of 28 windows gross-positive, median +$0.418, worst −$0.215. The
concentration is across episodes within windows, not across windows, which
is why leave-best-3 survived at +$0.307/window.

**3. MFE/MAE says the exit is cutting losers, not harvesting winners.** Mean
MFE is **+3.1¢/share** against mean MAE **−4.1¢/share**, and median MFE is
only **+1.0¢** — the typical episode never goes meaningfully in the money.
The strategy's gross comes from the right tail of favourable excursion
(p90 MFE +11¢) while the trail caps the left.

**4. The flip bucket is degenerate and cannot discriminate.** Zero of 300
episodes saw a leadership flip while open — the trail closes the episode
first. That dimension carries no information here and is reported as such,
not as a finding.

## Is there a distinction available before, or during, the episode?

**Before entry — weak but real, in the pre-entry observables:**

| dimension (known at entry) | worst bucket gross/fees | best bucket gross/fees |
|---|---|---|
| entry favourite price | 0.362 (<$0.60) | 1.564 (≥$0.90) |
| seconds to settlement | 0.688 (>600 s) | 3.124 (≤120 s) |

Both are monotone, and both are modest: no pre-entry bucket is strongly
positive and the best ones are also the smallest (28 and 70 episodes). On
this evidence I would **not** claim an entry filter exists.

**During the episode — much stronger, and genuinely observable in real
time:**

| | n | gross | fees | gross/fees | net |
|---|---|---|---|---|---|
| survived 5 s | 231 | +11.75 | 9.05 | 1.298 | +2.69 |
| died inside 5 s | 69 | **−1.71** | 2.37 | −0.723 | −4.08 |
| survived 20 s | 130 | +11.93 | 4.89 | **2.439** | +7.04 |
| died inside 20 s | 170 | **−1.89** | 6.53 | −0.290 | −8.42 |
| survived 60 s | 50 | +6.15 | 1.93 | 3.181 | +4.22 |
| died inside 60 s | 250 | +3.88 | 9.49 | 0.409 | −5.60 |

**Episodes that die young are collectively gross-negative.** Everything
positive in cell C is in the episodes that last. Survival to a given age is
known *while the episode is running*, unlike the exit-time duration bucket,
which is only known afterwards — that distinction matters and I have kept
them separate.

The same signal appears in the additions dimension, which is also live:
single-addition episodes (253 of 300) produce +$4.09 of gross on **$9.50 of
fees** (ratio 0.430), while episodes that got to 2+ additions produce
+$5.95 on $1.92 (ratio 3.10). A second addition only happens if the position
survived a minute, so this is the survival effect seen through another lens,
not independent evidence.

**I am not turning any of this into a rule.** Survivorship conditioning is
exactly the shape that produces a beautiful backtest and no edge: the
episodes that survive are selected after the fact by the very price path
that makes them profitable. What it legitimately establishes is **where the
fee bill goes** — 250 of 300 episodes die inside 60 s and spend $9.49 of the
$11.42.

## Fee reduction required, gross held constant

Your 12.1% is confirmed exactly: `1 − 10.0346/11.4202 = 12.13%`.

The ladder above is the honest limit of this line: **even a 100% fee
reduction caps cell C at +$0.358/window**, its entire gross. **$5/window is
unreachable by any fee reduction at this scale** — it needs $140 of net from
$10.03 of gross.

What fee elimination *would* do is make the thing scalable: at zero fees
C is +$0.358/window on $8.03 of peak capital, and that ratio (4.5% of peak
capital per window) is the first number in this programme that could reach
$5 inside the $200 ceiling if it held. It does not hold, because fees are
proportional and cannot be eliminated — but it locates the target: **gross
per unit of turnover, not fees alone.**

## Where this leaves the two levers you named

- **capture more favourable excursion per episode** — the MFE data says the
  room is small: median MFE +1.0¢/share against a round-trip cost of roughly
  1.2¢. Half the episodes never offer enough excursion to pay for
  themselves at all.
- **stop spending round-trip fees on low-value episodes** — the 193
  sub-fee episodes are a real, large, identified pool (67.4% of fees), but
  every dimension that separates them cleanly is survivorship, i.e. known
  too late or selected by outcome.

That is the genuine tension, and it is now measured rather than assumed. No
rule follows from it without a preregistration that confronts the
survivorship problem directly.

Everything here is `historical replay`. Verified compensation $0.0000.
