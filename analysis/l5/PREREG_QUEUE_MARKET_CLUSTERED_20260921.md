# Preregistration — H-QUEUE-MISMEASURED, market-clustered design

Written **2026-09-21T11:0xZ, before any result under this design was computed.**
Supersedes the *analysis plan* of the n=200 experiment. It does **not** supersede
the hypothesis, and it does not rescue the old experiment.

## Status of the previous experiment

The n≥200-scorable-fills-within-one-market experiment is **NOT SCORED**. It is
**not failed and not falsified** — it was never run, because its threshold was
never reached.

Its threshold **is not lowered.** A `btc-updown-15m` window lives 900 s and
yields roughly 100 scorable fills before the market ends, so no single market in
that habitat can reach 200. That is a property of the habitat, discovered after
the threshold was set. Lowering 200 to fit what the data happened to supply is
the manoeuvre preregistration exists to prevent, and it is refused here.

**The hypothesis is unchanged: H-QUEUE-MISMEASURED.** `depth_ahead_of_fill_price`,
computed from the public CLOB ladder, may not represent the effective queue that
governed L5's maker fill. **H-QUEUE-IRRELEVANT stays withdrawn and is not
resurrected.**

## Unit of replication

**The market/window.** One `{coin}-updown-{5m|15m}-{epoch}` window is one
replicate. Fills within a window are **not** independent observations of the
venue's queue behaviour: they share one book, one counterparty population, one
15-minute volatility regime, and frequently one transaction. Treating them as
independent rows is pseudo-replication and is prohibited below.

## Fixed before looking at any result under this design

1. **Minimum scorable fills per market: `n_min = 30`.**
   Chosen from what a window structurally supplies, not from a result: the
   observed durable distribution is ~100 per 15 m window and ~1–5 per 5 m
   window, so 30 admits 15 m windows as replicates and excludes 5 m windows,
   whose within-market estimate would be too noisy to carry a cluster weight.
   A market below `n_min` is **retained and reported**, never silently dropped,
   and contributes to no combined estimate.

2. **Minimum number of qualifying markets: `m_min = 20`.**
   Cluster-robust inference on fewer than ~20 clusters is unreliable, and the
   wild bootstrap below is what makes 20 usable. Nothing is combined until
   `m_min` qualifying markets exist.

3. **Scorable** keeps its existing meaning: venue-labelled MAKER, a pre-fill
   book present, at least one share ahead, and a verdict in
   {`ACCOUNTED_BY_TRADES`, `ACCOUNTED_WITH_CANCELS`, `INCONCLUSIVE_GAP`,
   `UNEXPLAINED`}. `NO_COVERAGE` and `NO_QUEUE` are excluded, as before.

## Estimation, in this order

1. **Within each market, separately.** For market *j*, estimate the
   depth/flow relationship — the association between
   `depth_ahead_at_first_look` and fill occurrence/latency — and the share of
   `UNEXPLAINED` verdicts `p_j`, with its own `n_j`. **Every market is preserved
   with its own estimate, its own denominator, and its own verdict counts.** No
   market is merged into another at this stage.

2. **Only then, combine.** Across qualifying markets, by **cluster-robust
   inference with the market as the cluster**: a cluster bootstrap resampling
   *markets* (not rows), reported with a **wild cluster bootstrap-t** as the
   primary interval, since `m` will be small. The point estimate is the
   market-weighted mean of `p_j`, and the interval comes from the bootstrap —
   never from a row-level standard error.

3. **Heterogeneity is a reported result, not a nuisance.** `I²` and the spread
   of `p_j` across markets are reported alongside the combined estimate. If
   markets disagree materially, **that is the finding**, and the combined number
   is reported as descriptive with the heterogeneity stated beside it rather
   than presented as a single venue-wide rate.

## Prohibited under this preregistration

- **Row-pooled pseudo-replication.** Concatenating fills across markets and
  computing one rate, one regression or one p-value over the pooled rows.
  `l5_queue_reconciliation.pooled()` continues to raise, and its floor is not
  the floor for *this* design — this design combines only as in §2.
- Lowering `n_min`, `m_min`, or any verdict rule after seeing a result.
- Re-deriving `UNEXPLAINED` from a truncated print window (already a refusal).
- Scoring before both `n_min` and `m_min` are met.
- Reading a confirmed H-QUEUE-MISMEASURED as support for any particular
  replacement mechanism.

## Censored intervals

`pmus_flow` 2026-09-21T03:55:13Z–06:07:17Z remains **CENSORED** — an absence of
observations, never zero flow, and never a miss. It self-recovered without a
restart; the cause was the liveness-map coverage defect, fixed in `85c8a741`.

## What would falsify H-QUEUE-MISMEASURED under this design

`UNEXPLAINED` is rare within markets once both trade conventions and
`INCONCLUSIVE_GAP` are applied; or `p_j` tracks each market's
`unobserved_interval_s` rather than its market state, which would make the
signal an artefact of our own sampling cadence.
