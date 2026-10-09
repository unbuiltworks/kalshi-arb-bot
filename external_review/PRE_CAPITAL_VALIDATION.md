# Pre-capital validation — items 1–6, and the bounded live-test recommendation

Scripts: `../scripts/C1_settlement_and_markout.py`,
`../scripts/C2_twomode_and_prediction.py`,
`../scripts/C2b_prediction_decomposition.py`.
Results: `C1_SETTLEMENT_AND_MARKOUT.json`,
`C2_TWOMODE_AND_PREDICTION.json`, `C2b_PREDICTION_DECOMPOSITION.json`.

L5 is **not** closed. Live trading is **not** authorized and nothing was
armed. No service, collector or dataset was modified.

---

## Item 1 — Kalshi settlement validation and proxy sensitivity

**There is no authoritative resolution record available, and I did not
manufacture one.** The tapes carry no settlement field, and the
container's egress proxy refuses CONNECT to every Kalshi host, so the
authoritative endpoint is unreachable from here. What was done instead:

**A second independent proxy.** Final BBO mid before close, cross-checked
against the last-print proxy:

| | value |
|---|---|
| markets with both proxies | 184 |
| **agree** | **182 (98.91%)** |
| disagree | 2 |
| median \|last print − 0.50\| | **0.473** |
| share within 0.05 of 0.50 (coin-flip cases) | **4.7%** |
| share beyond 0.40 of 0.50 (decisive) | **63.5%** |

So the proxy is decisive on most markets and the two methods disagree on
about 1%.

**Sensitivity sweep** — Track B's best configuration, AGG(θ=0.25),
re-settled with a random fraction of outcomes flipped (fills do not depend
on the outcome, so only settlement changes):

| flip fraction | mean P&L/market | CI95 | verdict |
|---|---|---|---|
| 0.00 | −0.0794 | [−0.117, −0.039] | NEGATIVE |
| 0.02 | −0.0685 | [−0.108, −0.027] | NEGATIVE |
| **0.05** | **−0.0577** | **[−0.096, −0.016]** | **NEGATIVE** |
| 0.10 | −0.0251 | [−0.070, +0.022] | INDETERMINATE |
| 0.20 | +0.0565 | [−0.001, +0.113] | INDETERMINATE |
| 0.30 | +0.1489 | [+0.088, +0.213] | POSITIVE |

**The negative verdict survives to a 5% error rate and breaks at 10%.**
The implied error rate from proxy disagreement is ~1%, comfortably inside
the robust zone — but the margin is only 5 percentage points, which is
thinner than I would want before capital. **This is the weakest link in
Track B and it is fixable only with real resolution data.**

One thing the sweep reveals that matters more than the robustness check:
**flipping outcomes turns the strategy profitable.** That is only possible
if the legs it bought were *systematically the losing side*. On a binary
market, a leg's ask reaches 0.25 mainly because it is heading to zero. So
"buy the cheap leg" late in a window is "buy the loser", and that — not
fill quality — is why Kalshi fails. See item 2.

## Item 2 — equivalent markout comparison

Same definition both venues: `markout(h) = (mid at fill+h) − fill price`,
signed positive-is-good. Fees reported separately, never folded in,
because the schedules differ.

**Kalshi maker — the maker side of every real print** (`taker_side`
identifies the maker exactly), 733,088 actual historical fills:

| horizon | n | mean markout | CI95 | share positive |
|---|---|---|---|---|
| +5s | 710,460 | **+0.00141** | [+0.00072, +0.00206] | 0.534 |
| +30s | 662,107 | +0.00103 | [−0.00032, +0.00234] | 0.511 |
| +60s | 607,225 | +0.00063 | [−0.00123, +0.00242] | 0.510 |

**Polymarket — L5's own actual fills** against the Zenodo ladders:

| horizon | n | mean markout | CI95 | share positive |
|---|---|---|---|---|
| +5s | 3,030 | −0.00101 | [−0.00389, +0.00164] | 0.510 |
| +30s | 2,884 | **+0.01010** | [+0.00557, +0.01444] | 0.525 |
| +60s | 2,727 | **+0.00724** | [+0.00147, +0.01273] | 0.493 |

**This corrects a claim I made in the Track B write-up.** I said Kalshi's
passive loss was adverse selection. It is not: Kalshi maker markout is
**slightly positive at 5s and flat by 30–60s** — not toxic. The passive
loss in Track B comes from **holding unpaired legs to settlement**, which
is directional risk, not fill quality.

The real venue difference: **L5's Polymarket markout is ~+1.0 cent at 30s
and persists to 60s; Kalshi's is ~+0.14 cent at 5s and decays to zero.**
Roughly 7–10× larger and durable versus transient.

**Limitation:** the maker/taker label window (Aug 28–31) and the book
window (Aug 21–24) **do not overlap**, so the Polymarket markouts cannot
be split by execution class. All 3,030 are UNLABELLED on that axis.

## Item 3 — the two-mode behaviour: do late aggressive fills help?

**They add P&L and they increase directional risk. Both.**

Residual effect per fill:

| class | n | mean Δresidual | share reducing residual |
|---|---|---|---|
| **TAKER_OBSERVED** | 11,810 | **+11.80** | **0.338** |

Counterfactual — each of 2,157 windows replayed twice with FIFO pairing,
once as it happened and once with all 11,810 taker fills removed:

| | value | CI95 |
|---|---|---|
| **mean P&L effect per window** | **+2.224** | **[+0.614, +3.745]** |
| windows improved by aggressing | 0.594 | |
| **mean residual effect** | **+61.88** | **[+58.52, +65.25]** |

So the late aggressive mode is a **profitable directional overlay**, not
pair completion — consistent with taker fills completing pairs *less*
often than maker fills (0.458 vs 0.486). L5 has two edges, not one:
passive cheap accumulation, and a late directional bet that pays.

**Assumption, stated:** the counterfactual assumes the maker fills would
still have arrived had L5 not aggressed. It also inherits the directional
variance — the CI is wide and the lower bound is near zero.

## Item 4 — reconciling the flat hazard, out of sample

Chronological 70/30 split by window; the test set is strictly later; no
window spans the boundary. Predicting "does the next fill switch leg".

| model | full corpus AUC | labelled-window-only AUC |
|---|---|---|
| M0 constant hazard | 0.516 | 0.506 |
| M1 + inventory, residual VWAP, clock | **0.519** | 0.519 |
| M2a + current fill's size and price | **0.624** | **0.626** |
| M2b + is_taker only | 0.519 | 0.548 |
| M2 full | 0.624 | 0.626 |

**The answer is more specific than "mode helps".** My first pass said
execution-mode features improve prediction; the decomposition shows that
was right in letter and wrong in attribution:

* **Inventory and clock features add essentially nothing** (0.516 → 0.519).
  **The flat-hazard finding stands out of sample for exactly the variables
  the nonlinear behavioural model conditions on.** The model's
  inventory-dependence — P(COMPLEMENT) rising with imbalance — does **not**
  survive an out-of-sample test.
* **The current fill's own size and price do predict the next side**,
  AUC 0.52 → **0.62**. That is a real, previously unused signal.
* **`is_taker` is redundant** with size and price (0.626 vs 0.626). On its
  own it is worth 0.506 → 0.548. The mode is largely *identified by* size
  and price, so it carries no information beyond them.

I ran the labelled-window-only variant specifically because `is_taker`
defaults to 0 for the 90% of fills outside the label window, which would
let a model learn "is this fill inside the labelled period" instead of
"is it a taker". It does not change the conclusion.

## Item 5 — the Grok/Perplexity package

Complete, verified and on disk. 109 files, 114.9 MB, `SHA256SUMS` written,
all integrity checks passing. **Blind prompts and downloadable files:**

| what | path |
|---|---|
| Grok brief (blind) | `PROMPT_GROK.md` |
| Perplexity brief (blind) | `PROMPT_PERPLEXITY.md` |
| upload order and branch instructions | `README_UPLOAD.md` |
| scope note for repo-based readers | `BRANCH_SCOPE.md` |
| data dictionary | `DATA_DICTIONARY.md` |
| what public fills cannot reveal | `LIMITS_OF_PUBLIC_FILLS.md` |
| 29 complete window case studies | `case_studies/CASE_STUDIES_ALL.md` |
| all 760,817 fills, 14 parts ≤6 MB | `dataset/fills/l5_fills_enriched_part01..14_*.csv.gz` |
| all 17,046 windows | `dataset/l5_window_summary.csv.gz` |
| uncompressed views for repo readers | `plain/l5_window_summary.csv`, `plain/l5_fills_sample.csv`, `plain/cashflow.csv`, `plain/settlement.csv` |
| hypothesis harness + template | `scripts/50_test_hypothesis.py`, `hypothesis_tests/` |
| our conclusions, HELD BACK | `../verification_HOLD_BACK/OUR_HYPOTHESES_UPLOAD_LAST.md` |

Also published as orphan branch `l5-blind-package` (no ancestry to any
analysis branch). Perplexity has not been run yet; its file set is ready.

## Item 6 — infrastructure

Nothing started, stopped, rebuilt or armed. No Railway call was made in
this work; all analysis is read-only against committed data. Shadow
collectors untouched.

---

# Final recommendation — the most informative bounded live maker test

## What to run

**A two-sided passive quoting probe on Kalshi KXBTC15M/KXETH15M,
restricted to the EARLY window, with no aggressive leg and no completion
logic.**

| parameter | value | why |
|---|---|---|
| venue / series | Kalshi KXBTC15M, KXETH15M | the only accessible venue with adequate evidence |
| quote size | **1 contract** per leg | minimum that produces a fill observation |
| entry window | **only while > 300s to close** | the sensitivity sweep showed cheap-late = buying the loser; L5's maker fills sit at a median 441s out |
| quote price | ≤ 0.45 on each leg, post-only | inside L5's measured maker band (mean accepted 0.422) |
| legs | both, independently, **no completion** | the question is fill rate, not pair economics |
| max concurrent markets | 2 | matches the existing canary's shape |
| total markets | **400** | see power note below |
| hard stop | **$250 cumulative realized loss** | below the max-loss bound |

## Exactly what it measures

1. **Our own passive fill rate** — what fraction of posted quotes fill,
   by time-to-close bucket and by distance below mid. **This is the single
   number that gates everything** and no historical data contains it.
2. **Our own queue behaviour** — time-to-fill and whether fills arrive
   before or after the book trades through our price, which tests the queue
   model Track B had to assume.
3. **Our own markout** — mid at +5s/+30s/+60s minus our fill price, the
   identical definition used in item 2, so it drops straight into the
   comparison table against Kalshi's 733,088-print baseline (+0.00141 at
   5s) and L5's Polymarket figure (+0.01010 at 30s).
4. **Whether early-window cheapness is non-informative** — the one
   hypothesis that could still rescue Kalshi, and the direct implication of
   the flip-sensitivity result.

## Maximum possible loss

Per market, worst case: both legs fill at 0.45 and **both** settle
worthless. That cannot happen — the two outcomes are complements, so if
both legs fill the pair pays exactly $1.00 and the worst case is
`0.90 + fees − 1.00 ≈ −$0.00` on a matched pair. The real exposure is a
**single unpaired leg settling at zero**:

| component | worst case |
|---|---|
| premium at risk per unpaired leg | 1 × $0.45 |
| unpaired legs per market | ≤ 2 (if both fill but at different… no — ≤1 unpaired by construction; use 2 for safety) |
| per market | ≤ **$0.90** |
| 400 markets | ≤ **$360** |
| Kalshi maker fees, ceil(0.0175·C·P·(1−P)) ≈ $0.01/fill, ≤800 fills | ≤ **$8** |
| **absolute worst case** | **≤ $368** |
| **with the $250 hard stop** | **≤ $250 + one market's slippage ≈ $251** |

That worst case assumes **every** filled leg settles worthless, which has
probability near zero; the expected loss if Kalshi behaves as Track B
suggests is roughly 400 × −$0.06 ≈ **−$24**.

**Power note:** 400 markets at ~2 quote-opportunities each gives on the
order of 200–400 fill observations if the fill rate is 25–50%, enough to
estimate fill rate to about ±5 percentage points and markout at +30s to
about ±0.004 — adequate to distinguish Kalshi's ~+0.001 from L5's ~+0.010.
If the fill rate turns out below 10%, the probe answers the question
anyway, in the negative, and should be stopped early.

## Why historical data cannot answer this

Whether a resting order fills is a function of **flow arriving against our
specific order** — our price, our size, our position in a queue we joined
at a specific moment. None of that exists in any observed book. Track B
had to *assume* a queue model (join behind all depth at or better, never
re-price, never cancel), and that assumption is doing real work in the
result. Three further things are structurally absent: our latency, our
reject/partial-fill behaviour, and the reflexive effect of our own quote on
the flow that hits it.

The markout comparison in item 2 is the closest historical substitute, and
it is a measurement of **other people's** maker fills. It tells us the
venue's maker fills are not toxic. It cannot tell us ours would arrive.

## What would make this unnecessary

Authoritative Kalshi resolution records plus a maker/taker-flagged fill
feed for our own account, obtained **without** trading. Both exist as
Kalshi endpoints. Both are currently blocked by the container's egress
policy. **Unblocking that egress is strictly cheaper than the probe and
should be attempted first** — it would also repair item 1's 5-percentage-
point robustness margin, which is the weakest number in this report.

## Explicitly not recommended

Scaling toward $480/day on this evidence. L5's own trading-only peak was
$240/day and its $480/day month had negative trading P&L. Nothing measured
here establishes positive net expectancy for us on any accessible venue,
and the probe above is sized to *measure*, not to earn.
