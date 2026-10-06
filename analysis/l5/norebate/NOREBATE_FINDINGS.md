# No-rebate economics audit — results

Frozen protocol **3fb8fccb**. S3 untouched at `8b20a259…87df`; no live or
shadow strategy modified; no orders. **Historical L5 cash is never our
realized profit.**

---

# CONCLUSION 1 — OBSERVED L5 CASH: **NEGATIVE EX INCENTIVES**

Rebates and rewards set to exactly **$0.00**, every era:

| era | N | obs. days | total | $/event | $/obs-day | median event | win rate | P(total>0) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **E1 genuinely pre-rebate** | 211 | 17 | **−$168.46** | −0.80 | −9.91 | **−0.76** | 0.483 | 0.276 |
| E2 early rebate | 5 | 1 | −$1,855.08 | −371.02 | −1,855.08 | −303.27 | 0.000 | 0.000 |
| **E3 incentive era** | 2,002 | 4 | **−$721.99** | −0.36 | **−180.50** | +2.35 | 0.569 | 0.417 |
| **FULL CORPUS** | 2,218 | 22 | **−$2,745.53** | −1.24 | −124.80 | +1.93 | 0.559 | 0.213 |

**Negative in every era, including the genuinely pre-rebate one.**

Not inferred from the total alone, as the protocol requires:

- **E1** — median event is *also* negative (−$0.76) and the win rate is
  below half (0.483). Bootstrap CI95 **[−731.84, +391.63]**, P(positive)
  **0.276**: negative but not significantly so at n = 211.
- **E3** — profile is **LOSS_CONCENTRATED**: median event **+$2.35** and win
  rate 0.569, yet the total is negative. Removing the single worst event
  flips it to **+$94.72**; removing the worst five gives **+$1,425.61**.
  Many small wins, a few large losses.
- **FULL** — max drawdown **−$5,265.67**; bootstrap P(positive) **0.213**.

## The decomposition is the real story

| component | E1 | E3 | FULL |
| --- | --- | --- | --- |
| **matched-pair economics** | **+$884.42** | **+$21,542.38** | **+$22,568.25** |
| **directional residual** | **−$1,052.89** | **−$22,264.37** | **−$25,313.78** |
| charged fees (embedded) | $0.00 | $3,993.43 | $3,993.43 |
| **rebates / rewards** | **$0.00** | **$0.00** | **$0.00** |

**In every era the matched component is positive and the directional
residual is larger and negative.** The pair mechanism makes money; the
leftover directional exposure gives it all back and a little more. This is
the same residual that `CLAUDE.md` already warns carries no predictive
skill, now priced.

## Incentive scale — only 4 days observable

| day | maker | taker | reward | **total** | × $480 target |
| --- | --- | --- | --- | --- | --- |
| 2026-04-21 | 29.94 | — | — | **29.94** | 0.06× |
| 2026-08-29 | 1,233.57 | 192.11 | 1,794.66 | **3,220.34** | **6.7×** |
| 2026-08-30 | 1,346.88 | 192.21 | 2,205.72 | **3,744.81** | **7.8×** |
| 2026-08-31 | 1,298.91 | 203.01 | 1,841.42 | **3,343.34** | **7.0×** |

Against **−$180.50/day** of trading cash in E3, incentives of
**$3,220–3,745/day** are not a supplement to a profitable strategy — on the
observable evidence **they are the entire return**, roughly 18–21× the
trading loss they offset.

The **corpus total of incentives is UNIDENTIFIABLE** (only 4 days exist in
the feed) and none is reported.

## Fee finding, and a correction to my own first pass

**Fees exist only in the incentive era.** March and April buys carry **no
fee at all** (median and max residual exactly 0.00); August carries
$3,993.43 across 12,136 of 75,862 buys (**16.0 %**).

The `0.07·p·(1−p)` identity initially read **UNCONFIRMED** — an artifact of
pooling fee-charged with fee-free buys, where the model predicts a fee and
the data shows zero. **On charged buys the identity is exact**: median
|residual − model| = **0.0**, median ratio = **1.000000**. Verdict
`FEE_IDENTITY_CONFIRMED` on charged buys, with the fee-free share reported
beside it.

I also had to add loss-side concentration: a share-of-total ratio is
**uninterpretable on a negative total** (it produced values like −1.671),
so the loss side is reported in absolute dollars.

---

# CONCLUSION 2 — NO-REBATE MECHANISM: **NOT SUPPORTED**

Two independent lines, and a regime finding that limits what either can
mean.

## Q2 — the executable replay already exists, frozen

The protocol's Q2 mechanism — near-touch maker exposure, partial fills as
execution, exposure moving with the touch, inventory/TTE controls, strictest
fill evidence, no touch=fill, no invented queue priority, **no rebates** —
is precisely **frozen diagnostic S2** (`5650ca16…7865`), already run on all
12 L2-overlap windows under the less-biased third-party-print arm with every
L5 leg removed.

| arm | L5 | S1 | **S2** |
| --- | --- | --- | --- |
| net per dollar spent | −0.18 % | −5.58 % | **−5.96 %** |
| matched fraction | 0.952 | 0.794 | 0.959 |

S2 reproduced L5's *matched-acquisition behaviour* almost exactly (0.959 vs
0.952) and still lost **−5.96 %** per dollar, with **zero** rebates. The
mechanism reproduces the behaviour and not the economics.

Recorded in the protocol before results: Q2 was expected negative, and it
is. Re-running it with the newer coordinate-system wording would change
nothing material — S2's offset-0 placement with displacement repricing *is*
at-touch exposure that moves with the touch.

## The regime comparison — and why it caps the whole audit

**Deleting rebate payments from an incentive-era strategy is not the same as
observing that strategy without incentives.** The data confirm this
emphatically: **9 of 10 metrics changed materially.**

| metric | pre-rebate median/day | incentive-era median/day | ratio | Cliff's δ | material |
| --- | --- | --- | --- | --- | --- |
| trades | 150 | **14,911** | **99.4×** | 0.835 | ✅ |
| notional | $862.87 | **$149,634** | **173.4×** | 0.882 | ✅ |
| markets | 9 | **474** | 52.7× | 0.682 | ✅ |
| matched shares | 908.51 | **158,829** | 174.8× | 0.906 | ✅ |
| residual shares | 50.66 | 470.53 | 9.3× | 0.694 | ✅ |
| median trade size | 10.0 | 13.45 | 1.3× | 0.482 | ✅ |
| median trade price | 0.475 | 0.42 | 0.88× | −0.929 | ✅ |
| matched fraction | 0.9749 | 0.9908 | 1.02× | 0.647 | ✅ |
| fees | $0.00 | $823.67 | — | 0.800 | ✅ |
| **pair cost** | **0.9548** | **0.9513** | **0.996×** | −0.247 | ❌ |

**The strategy scaled ~175× in volume when incentives appeared, and the one
thing that did not change is pair cost.** That is exactly what the earlier
REFUTED pair-cost study predicts: pair cost is ambient complement-sum
arithmetic, a property of the market rather than of the strategy, so it is
invariant to a 175× change in behaviour.

**Consequence, per the frozen protocol:** E3's "rebates = $0" figure
**may not be read as the no-incentive counterfactual.** The behaviour
measured there was produced *under* incentives, at 175× the pre-incentive
scale. The genuinely pre-rebate era (E1) is the only clean observation, and
it is **−$168.46 over 17 days with a negative median**.

## Reconciliation with the prior canonical audit

`src/pmus_incentive_audit.py` records a prior full-corpus finding:

> maker rebates **+$33,929**, liquidity rewards **+$26,956**, taker rebates
> **+$7,323** of **+$81,895** total on **$7.4 M** turnover — **83 %
> incentives** — with a trading edge net of residual/timing of **0.18 %** of
> turnover.

That is **consistent** with this audit once two differences are stated:

1. **Coverage.** My corpus is $767 k of BUY debits — roughly **10 %** of the
   $7.4 M turnover — because the feed hit the 5,000-offset cap and
   **May–July is entirely absent**.
2. **Decomposition.** "Trading edge **net of residual/timing**" removes
   exactly the component that sinks the raw cashflow. My matched line is
   **+$22,568** and residual **−$25,314**; strip the residual and the edge
   is positive, as that audit reports. Keep it, as actual cash requires,
   and it is negative.

Both audits therefore agree on the decisive point: **L5's money was
overwhelmingly incentives, and the directional residual consumes the pair
edge.**

## PMUS rewards, quantified separately — **UNKNOWN**

`pmus_incentive_audit.py` reads `/v1/incentives/earnings` and
`/v1/portfolio/activities`, both **authenticated**. This session cannot
reach them: `gateway.polymarket.us` is denied by the container's network
policy, and authentication would require signing, which is forbidden here
and by S3's zero-capital mandate.

Therefore **PMUS reward economics for our account are UNKNOWN and not
source-confirmed**, per the standing rule that no rebate is counted until
actually credited. Published programs existing is not a credit. This is the
single largest open input to the decision below, and S3 — which counts
rebates as **$0** by construction — is deliberately specified not to depend
on it.

---

# What this means for the next decision

The question was whether PMUS provides the execution economics the
evidence-supported L5 mechanism requires. The audit narrows it sharply:

1. **The mechanism's matched component is genuinely positive** (+$22.6 k
   observed) — that part is real and reproducible (S2 matched fraction
   0.959 vs L5's 0.952).
2. **The directional residual destroys it** (−$25.3 k), and residual
   exposure grew **9.3×** in the incentive era.
3. **Ex-incentives, every observable era is cash-negative**, including the
   clean pre-rebate one.
4. **Incentives were 6.7–7.8× the $480/day target** and ~18–21× the daily
   trading loss they offset.
5. **A no-rebate PMUS deployment therefore has to clear a bar L5 itself
   never cleared without incentives** — and S2 quantifies the gap at
   **−5.96 %** per dollar with the behaviour faithfully reproduced.

So the economics hinge on something this audit cannot supply: **whether PMUS
credits our account comparable incentives**, which is **UNKNOWN**. That is
precisely what the running zero-capital S3 cohort is instrumented to bound
on the execution side — queue position and maker-fill rate — with rebates
held at $0 so the answer never depends on an uncredited assumption.

**Nothing here authorizes a strategy change, and none was made.** S3 remains
frozen; the 0.96 reservation is untouched.
