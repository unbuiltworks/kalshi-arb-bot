# Are we pricing the second side less aggressively than L5?

**Answer: not on average — but we have a structural hole where a quarter of
L5's completions live, and it is a hard cap in our own code.**

Owner's question: *"After our first side fills, are we pricing the second side
less aggressively than L5 does in comparable inventory states?"*

## The comparison had to be fills-vs-fills

L5's public record is **executed trades only**. We cannot see where an unfilled
L5 limit order sat. Our record is mostly **unfilled attempts**.

Comparing L5's executions to our placements would compare survivors against
all-comers and manufacture a gap out of selection bias. So the only unbiased
comparison is **completed pairs against completed pairs** — both fill-conditioned,
both on the venue's own numbers. That is what follows.

## Result 1 — on the pairs we DO complete, we are not less aggressive

| | median completed pair cost |
|---|---|
| **Ours** (30 settled markets) | **0.9524** |
| L5, 2026-03 (earliest era) | 0.9618 |
| L5, all eras | 0.9876 |

We complete **marginally cheaper** than early L5, not dearer. The hypothesis
that our shell keeps us systematically further from the market **on completions
we achieve** is **refuted**.

## Result 2 — the difference is entirely in the tail, and it is a hole

Share of completed pairs by cost band:

| band | L5 2026-03 | L5 2026-04 | L5 2026-08 | **OURS** |
|---|---|---|---|---|
| ≤ 0.90 | 10.2% | 13.6% | 3.5% | 23.3% |
| 0.90–0.96 | 38.4% | 25.5% | 19.8% | 30.0% |
| 0.96–1.00 | 25.5% | 25.7% | 39.1% | **43.3%** |
| **1.00–1.03** | **12.4%** | **14.9%** | **19.7%** | **0.0%** |
| **1.03–1.10** | **9.9%** | **14.8%** | **13.3%** | **0.0%** |
| > 1.10 | 3.6% | 5.5% | 4.6% | 3.3% *(1 market)* |

**We have zero completions in 1.00–1.10, where 22.3% of L5's earliest-era
completions sit.** Our only above-par completion is a single outlier at 1.1107
(−$3.88) — *beyond* L5's normal range, not inside it.

Two signatures confirm a binding constraint rather than a preference:

1. **Mass piles up just below the limit.** 43.3% of our completions land in
   0.96–1.00 against L5's 25.5% — the shape of a distribution pressed against a
   wall.
2. **The wall is in our code.** `pair_cost_max()` in
   `src/pmus_spring_l5_session.py` *"never returns above 1.00"* (its own
   comment, line 643). The R4 rung set completion at **1.00 hard**.

## Result 3 — this is NOT purely incentive-era behaviour

The standing lock in `CLAUDE.md` says near-$1 tolerance belongs to *later
incentive-era* L5. That is **half right, and the half that is wrong matters**:

* L5 completed **above par 25.9% of the time in March 2026** — its earliest
  month, 1,681 pairs, long before the incentive era.
* The tolerance **did widen** with era: 25.9% → 35.2% → 37.6%. So part of the
  gap genuinely is incentive-era and stays locked.

But the early-era baseline alone already exceeds what our cap permits. **Our
0.96 preference allows 48.5% of L5's March completes; our 1.00 hard cap forbids
25.9% of them outright.**

## What the rebate does and does not justify

The session's own comment reasons that the maker rebate (0.808% of filled
dollars) pays for a pair up to **~1.003**. That reconciles exactly with the
credited record: **$0.42 across 119.49 contracts**, ≈0.35¢/contract, ≈0.8% of
filled dollars.

So **rebates justify ~1.003 and no further.** Any completion above that is
justified only by what it avoids — a stranded directional residual — never by
incentive income we do not receive.

## The economic case, stated with its uncertainty intact

Order of magnitude on our own settled record:

* completing at 1.02 costs ~2¢/pair, less ~0.3¢ rebate ≈ **−1.7¢/pair**; on a
  typical ~5-contract position ≈ **−$0.09**;
* our 9 `no_completion` markets lost **−$13.26**, ≈ **−$1.47 each**.

That is roughly **17:1** in favour of completing — *if* the order fills.

**That "if" is doing all the work and is NOT established.** We have no trade
prints during complement rest, so we cannot say a legal above-par order would
have filled, nor that any of the 9 stranded markets would have completed. Per
`PREREG_EXECUTION_QUEUE_FAILURE.md` §8.1 that number is not computed here and
`recoverable_completions()` raises.

## Limitations

* **n = 30** settled markets on our side, **1** above-par observation. Small.
* **Cross-domain.** L5's record is BTC 5-minute crypto windows; ours is PMUS
  sports — different venue, asset and duration. Behavioural comparison, not a
  like-for-like market study.
* **Fill-conditioned both sides.** Says what completions cost, never what
  fraction of attempts filled.

## The concrete candidate

Not "raise the ceiling". Specifically:

> **Permit completion in (1.00, 1.03] — the band holding 12.4% of L5's
> earliest-era completes — only when the alternative is carrying a stranded
> residual to settlement, under a hard per-market cash cap.**

Bounded by construction: it stays inside early-era L5 behaviour, excludes the
incentive-era tail above 1.03 that remains locked, excludes the 1.11 outlier
that produced our worst market, and cannot be justified by rebates we do not
earn.

**No parameter is changed by this document.** `pair_cost_max()` still returns
≤1.00. This is a proposal requiring explicit owner approval before any live
path moves.
