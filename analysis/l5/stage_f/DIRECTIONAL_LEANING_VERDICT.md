# Does probability-aware directional leaning improve expected net profit?

**Recommendation: NO. Do not add directional leaning.**

Read-only. The live campaign's policy was not touched and no risk was
increased. Built from existing artifacts rather than a new experiment, because
existing artifacts already answer it decisively.

---

## 1. A data constraint I have to state first

**No BTC spot or settlement-reference price series exists in this
repository.** `analysis/l5/next_side/features.py:50` says so outright — *"no
BTC spot series exists in this repository"* — and I verified it rather than
taking it on trust: every `data/transfer_backtest/gemini_*` file is
**prediction-market contract data** (`GEMI-BTC15M2608310030-UP`, bid/ask on
the UP contract), not BTC. There is no btcusd series anywhere under `data/`.

So of the eight reconstruction items requested, **two cannot be computed from
local data**: BTC distance from the settlement reference, and short-term BTC
volatility.

**What legitimately substitutes, and what does not.** For a binary UP/DOWN
market the contract price *is* the market's settlement probability, and it is
a monotone transform of BTC's distance to reference at a given time and
volatility. So `p_up` answers "estimated UP/DOWN settlement probability"
directly, and `dp_up_1 / dp_up_3` proxy short-term movement. That is what the
prior work used, and it is sound.

**What it cannot do is measure mispricing**, because you cannot detect the
market's error using the market's own price. That is the one version of
"probability-aware" the local data cannot test, and I flag it in §5 rather
than pretending otherwise.

## 2. The three hypotheses, answered — 61,493 BTC15m fills, chronological OOS

`analysis/l5/next_side/NEXT_SIDE_ANSWER.md`. Split at a window boundary,
358 train / 154 test windows, no window spanning both.

| policy for "next side" | OOS accuracy |
|---|---|
| majority side | 0.5037 |
| **repeat last side** | **0.7039** |
| **buy cheaper outcome** | **0.5772** |
| **repair imbalance** | **0.4780** |
| logit, all 24 features | 0.7038 |

Nested ablation, incremental accuracy over last-side alone:

| block added | Δ accuracy |
|---|---|
| **market_price** (`p_up`) | **+0.0000** |
| inventory / imbalance | +0.0001 |
| tte_progress | +0.0001 |
| recent_movement | **−0.0004** |
| **total over last-side-only** | **+0.0003** |

**Answering your three options directly:**

1. **"Leans toward the more probable winner"** — **NO.** Market-implied
   settlement probability adds **+0.0000** accuracy beyond persistence. The
   24-feature model scores 0.7038 against persistence's 0.7039: it has learned
   to be "repeat last side".
2. **"Buys underpriced outcomes regardless of side"** — **NO.** That policy
   scores **0.5772** against persistence's 0.7039 — much worse than simply
   continuing.
3. **"Accumulates both sides opportunistically"** — **closest, and worth
   stating precisely:** L5 works *one* side in runs and then switches. Not
   opportunistic two-sided accumulation, and emphatically not rebalancing —
   **"repair imbalance" at 0.4780 is worse than a coin flip.**

`HYPOTHESIS_DECISION.md` reaches the same verdict independently:
**`INVENTORY_SKEWED_DUAL_SIDED` (H2) is refuted** — imbalance adds +0.0046
AUC, the SWITCH rate is flat across imbalance, and the offset correlation is
**0.01**.

What the features *do* buy is calibration, not direction: AUC 0.7039 → 0.7551,
Brier 0.296 → 0.201. **The model knows how confident to be; it does not know
something different.**

## 3. The economics, fee-inclusive and out of sample

This is the part that decides the recommendation, because accuracy is not
money.

**`analysis/l5/fees/FEE_RECONSTRUCTION.md`** — 10,521 cycles, residual cap
chosen on TRAIN and reported on TEST, fees reconstructed from the tape and
redemption-selection corrected:

| cap | matched P&L | residual P&L | fee | net | bp/$ |
|---|---|---|---|---|---|
| 6 (live) | **−11** | +30 | 28 | +19 | +155 |
| 10 | **−5** | +107 | 74 | +102 | +324 |
| 30 | **−276** | +508 | 444 | +232 | +122 |
| **none** | **+1,848** | **−1,745** | 7,024 | +103 | **+3** |

Its own conclusion: *"matched P&L is ≈0 or negative at every bounded cap.
Whatever net appears there is directional residual — the thing shown to have
no skill and a −100% tail."*

**`analysis/l5/saturation/PROD_VS_CAPS.md`, on L5's BTC15m OOS sequences** —
the decisive table:

| arm | matched | residual | net | bp/$ | max DD |
|---|---|---|---|---|---|
| CURRENT PROD (cap 6) | +$19,233 | −$10,956 | +$8,277 | 35 | −$428 |
| cap35 | +$15,657 | −$6,685 | **+$8,971** | **45** | **−$257** |
| **CONTROL exact-L5 (uncapped)** | **+$89,028** | **−$90,452** | **−$1,424** | **−4** | **−$8,497** |

**Read the control row carefully. Copying L5's directional inventory exactly
turns +$89,028 of matched edge into a net loss of $1,424.** The residual gives
back more than the whole edge. Every capped arm is positive; the uncapped one
is negative.

**So directional exposure is not the source of L5's profit — it is the tax on
it, and bounding it is what makes the policy work.**

## 4. Our own residuals say the same at small scale

Stage B2, seven traded windows, settlement-only:

- **5 of 7 stranded** (71.4%). Three strands won **+$3.1642**, two lost
  **−$2.1382**.
- The residual line read **+$2.09** at peak, **−$0.04** after the second loss,
  **+$1.03** after the last one won.
- **72.5% of the headline +$1.4155 was directional.** Matched was **+$0.3895**.

Two clean zero-residual windows earned +$0.0591 and +$0.0158 — small and
certain. The stranded windows swung **$5.30** between best and worst. That is
the same shape as the 10,521-cycle result, at n=7.

## 5. The recommendation

**Do not add directional leaning. One sentence of mechanism: the signal that
would justify it adds +0.0000 to side prediction, and the exposure it creates
turns +$89k of matched edge into −$1.4k net out of sample.**

**The live experiment is already pointed the right way.** Simultaneous
two-sided quoting attacks residual by *pricing both legs at once* — the pair
cost is fixed at quote time by the spread (`pair = 1 − spread − 2·d·t`), with
no dependence on guessing a winner. That is the opposite of directional
leaning and it is the correct direction on this evidence.

**What would change my mind, stated so it is falsifiable.** An independent
BTC-reference series would permit a model probability `p̂` to be compared
against the market's `p_up`, and the residual `p̂ − p_up` is mispricing, which
the contract price cannot reveal about itself. If that residual predicted
settlement out of sample with enough margin to clear fees and adverse
selection, directional leaning would become arguable. **I cannot test it
today** — there is no such series locally. The `btc-ref-collector` service may
hold one on its volume; that is a separate read-only question, and I am not
proposing to act on it during this campaign.

**I am not proposing any change to the running policy.** Nothing here
justifies touching an approved experiment mid-flight.
