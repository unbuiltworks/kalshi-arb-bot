# Preregistration — running pair-cost control (confirmation study)

**Frozen before any confirmation analysis.** Spec fingerprint
`e90895545a94999b9f9ac5ff472e92efb1042f0be00bf2d42d0eb738f5523b10`
(`analysis/l5/paircost/spec.py`; the literal is hard-coded so
`assert_frozen()` cannot be vacuous, and it is verified to catch tampering).

**S3 untouched** at `8b20a259…87df`. No live orders. No threshold
optimization. The target is **not** refitted here.

## 0. Status of the hypothesis

The completion study produced this as a **non-preregistered observation**,
and it is treated strictly as a discovery hypothesis:

> L5 may manage cumulative matched-inventory cost basis rather than
> individual pair costs.

**Primary hypothesis.** L5's control variable is

```
running_matched_pair_cost = matched-attributable cumulative acquisition cost
                            / matched shares
```

rather than marginal / per-parcel pair cost.

**Frozen discovery value: TARGET_COST = 0.98**, provenance
`COMPLETION_FINDINGS.md @ fc19d586` (cumulative matched pair cost measured
0.973–1.013 across all 12 checkpoints, cross-window IQR 0.0112–0.0763).
**Primary band 0.96–1.00** (±0.02, rounded out from the observed terminal
IQR of 0.0267). Sensitivity bands **±0.01** and **±0.05** are fixed *now*,
so that a band cannot be selected after seeing which one passes.

## 1. The primary null — and why the burden of proof is on control

This must be stated first, because the existing record already makes it the
likeliest explanation.

**NULL_M (complement sum).** If the two complementary books sum to
`S(t) ≈ 0.98` and a trader acquires both outcomes at or near the touch, the
realised pair cost is `≈ S(t)` **by arithmetic**, with no control of any
kind.

Evidence already on record *for the null*, from work completed before this
protocol:

| statistic | value | source |
| --- | --- | --- |
| mean(UP price) + mean(DOWN price) per window, median | **0.977** | `STRUCTURE.json` §5 ladder geometry |
| sum of the two touches, essentially the whole window | **≈0.99** | `S2_FINDINGS.md` §5 |
| terminal cumulative pair cost, median | **0.978** | `COMPLETION.json` T4 |

The quantity this study is asked to explain (0.978) is **numerically
indistinguishable** from the sum of L5's own mean prices (0.977), which is
in turn what the books hand to anyone buying both sides at the touch. So
NULL_M is the primary null and **the burden of proof rests on control**, not
on the null.

## 2. The discriminating test — SELECTION_EDGE

The only thing that can separate control from arithmetic is **selection in
time**: does L5 transact when the available complement sum is transiently
*low*, rather than at an arbitrary moment?

```
available_sum(t)   = best ask UP(t) + best ask DOWN(t)      [taker view]
                   = best bid UP(t) + best bid DOWN(t)      [maker view]
time_weighted_sum  = mean of available_sum over the window's seconds
L5_realised        = volume-weighted realised pair cost
SELECTION_EDGE     = time_weighted_sum − L5_realised
```

Both the maker and taker views are required. Interpretation, fixed now:

- `SELECTION_EDGE ≤ 0` within noise → **no control**; NULL_M stands.
- `SELECTION_EDGE > 0` reproducibly → **genuine timing control**.

*Bar:* edge ≥ **0.005** in ≥ **9 of 12** windows.

This is the hinge of the entire study. Every other test below is secondary
to it, because a stable band with zero selection edge is a property of the
market, not of L5.

## 3. The seven falsification tests, window by window

| # | test |
| --- | --- |
| **F1** | matched shares and seconds until the band is first entered |
| **F2** | fraction of subsequent matched inventory **and** time inside the band |
| **F3** | maximum absolute excursion from TARGET_COST |
| **F4** | does it re-enter after an excursion, and how fast |
| **F5** | does the frozen 0.98 survive leave-one-window-out |
| **F6** | is cumulative materially more stable than marginal / per-parcel |
| **F7** | is it materially more stable than matched fraction, share allocation, dollar allocation, and raw price/probability |

### A fairness requirement on F6 and F7, fixed in advance

A cumulative average is **mechanically smoother than the increments it
averages**. "Cumulative beats marginal" is therefore close to tautological
and is **not** evidence of control. F6 and F7 must compare against:

1. the **same cumulative transform** of every competing quantity, and
2. **NULL_M's own cumulative pair cost**.

Comparing a cumulative series against a marginal one would be exactly the
tautology this program has already walked into twice (cumulative clip count
vs TTE; and the vacuous per-window rank test). It is pre-barred here.

## 4. The budget / headroom causal test

For every prospective EXTEND/COMPRESS opportunity, using **only information
available at that instant**:

```
budget(t) = TARGET_COST × prospective_matched_shares
            − prospective_total_matched_cost
```

Question: does cheap EXTEND inventory create measurable headroom that is
subsequently **spent** on dearer COMPRESS inventory? Quantified
window-by-window as *cheap-side savings → subsequent complement premium*.

*Bar:* rho ≥ **0.40** in ≥ **9 of 12** windows.

**Causal caveat, fixed now:** savings during EXTEND and premium during
COMPRESS are both driven by the same underlying price path. A positive
correlation is *consistent with* budget-driven choice but does **not**
establish it. It will be reported as an association only, unless
SELECTION_EDGE also passes.

## 5. Counterfactual controllers

| id | controller |
| --- | --- |
| **CF0** | **NULL_M** — touch-taking both sides, no control at all |
| CF1 | always buy the lighter side |
| CF2 | fixed per-pair ceiling |
| CF3 | fixed per-action reservation price |
| CF4 | equal-share controller |
| CF5 | random permutation of observed actions, preserving prices and sizes |
| CF6 | running-average pair-cost controller |

Each must reproduce **both** (a) observed matched-inventory accumulation and
(b) observed cumulative pair-cost trajectory, scored as L1 distance to L5's
own trajectory per window. **A controller that reproduces one but not the
other FAILS.** Among those reproducing both within tolerance, the one with
the **fewest free parameters** is the answer.

CF0 is the decisive comparison: if it matches L5 on both, the verdict is
REFUTED regardless of how tight the band looks.

## 6. Frozen regime bins — no subdivision search

Probability `0.0…1.0` in 0.10 steps · TTE `{0,120,300,600,900}` ·
imbalance `{0,40,80,160,320,∞}` shares · large price move `≥0.05 over 30 s`
· action class `{EXTENDS, COMPRESSES, NEUTRAL}` · role `{maker, taker}`
where historically identifiable.

Minimum **n = 30** per cell. Underpowered cells are reported as
UNDERPOWERED and **excluded from any verdict, never pooled into a
neighbour**. No favourable subdivision is searched for.

## 7. Accounting — attribution is genuinely ambiguous

Inventory is fungible, so attributing acquisition dollars to matched versus
residual shares has no unique answer. **All three conventions are run:**

| id | convention |
| --- | --- |
| C1 | FIFO — matched shares take the earliest open lots per side |
| C2 | LIFO — matched shares take the most recent open lots |
| **C3** | **VWAP-pooled — matched shares carry each side's running VWAP, no lot identity (PRIMARY)** |

C3 is primary because it is the only convention that does not impose a
lot-matching story the chain data cannot evidence. **The report must state
explicitly whether the conclusion depends on the convention.**

- **Fees are accounted separately** from raw acquisition pair cost
  (model-implied `0.07·p·(1−p)`), on their own line, never folded in.
- **Atomic \$1.00 taker pairs are separated.** Inserting par inventory
  mechanically pulls any running average toward 1.00 and could manufacture a
  false appearance of active control. Par pairs are 9.72 % of BTC15m volume.
  **The primary result is repeated with atomic par pairs excluded**, and
  both versions are reported.

## 8. Required conclusion

Exactly one of:

- **REFUTED** — the band is not maintained, **or** NULL_M reproduces the
  trajectory as well as L5 does
- **DESCRIPTIVE ONLY** — the band is real and stable but SELECTION_EDGE ≤ 0
  within noise, so the stability is inherited from the book
- **PREDICTIVE CONTROL SIGNAL** — SELECTION_EDGE passes **and** the budget
  test passes
- **ECONOMIC CONTROL SIGNAL** — the above **and** the control demonstrably
  improves acquisition economics against every counterfactual, with fees
  separate and par pairs excluded

And a direct answer to:

> **Is ~0.98 genuinely a maintained L5 state variable, or is its stability a
> mathematical consequence of acquiring both outcomes in these markets?**

A REFUTED or DESCRIPTIVE-ONLY outcome is a complete answer and will be
reported as one. Given the §5 arithmetic above, I record now that I consider
DESCRIPTIVE ONLY the most likely verdict, so that a null cannot later be
presented as a surprise or softened.
