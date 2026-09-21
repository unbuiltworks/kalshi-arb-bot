# How L5 obtained maker fills despite the queue

P0 of 2026-09-21. Checkpoint `007feaab`. No live orders, no new selector, no
parameter fitted to P&L. The frozen habitat selector is untouched and verified
(`analysis/pmus/freeze_check.py` — FROZEN VALUES INTACT).

## Headline

**Visible-book queue depth is the wrong queue variable for most of L5's maker
fills.** They do not consume resting offers. They settle by MINTING a complete
set against a counterparty buying the other outcome — confirmed directly on
chain, not inferred from the book. What binds is the availability of a
complement BUYER, and that is what PMUS sports markets do not have.

## Two numbers are withdrawn before anything is added

**W-1. "83.1% of our complement rungs were never reached."** Measured over
`comp_log` intervals that `_closelog` ended whenever *any* sibling rung on the
side was cancelled or filled — including, because `_place` appends before it
cancels, by the rung's own placement. **78.0% of intervals had zero length**
and a median of one book snapshot inside them, so "never reached" was near
certain by construction. Fixed by giving each rung its own interval index:
median duration 0.0 s → 69.6 s. The fix is diagnostic-only, proven by running
pre-fix and post-fix code over the same tape: **304 markets, 0 differing
fields**, every fill timestamp, side, price and relation identical.

**W-2. "1.5% vs 99.86% — a near-perfect mint discriminator."** It cannot fail.
On a binary book the complement's bid is one minus this token's ask — measured
at **exactly 1.0000 in 97.86%** of synchronized snapshot pairs (n=5,178), one
tick in 98.34%. So

    mint_feasible ⟺ p + (1 − own_ask) ≥ 1 ⟺ p ≥ own_ask ⟺ AT_OR_ABOVE_ASK

which is the class it was supposed to separate. Checked against the data:
`mint_feasible ≡ (price ≥ own_best_ask)` agrees **120/120 at every lead, zero
disagreements**. The separation restates the class definition. `ZENODO_OVERLAP_
FINDINGS.md` §2 must not be cited as mechanism evidence.

The underlying observation survives: 26.4% of L5's maker BUYS price at or above
the prevailing ask, which a resting bid cannot do. Only its explanation was
unsupported — so it was replaced with one that can come back negative.

## A. L5 queue forensics — `analysis/l5/historical_l2/queue_forensics.py`

2,795 maker fills, 12 windows, 1-second full-depth L2 joined by token id.
At T−1 (shares stable across T−30/T−5/T−1):

| mechanism | n | share | own depth at price p50 | complement pool p50 | through | opp→fill p50 |
|---|---|---|---|---|---|---|
| BOOK_FILL_FIFO_CONSISTENT | 1,566 | 56.0% | 196.1 | 0.0 | 93.5% | 294 s |
| MINT_MATCH_FEASIBLE | 737 | 26.4% | 0.0 | 165.0 | 88.2% | 194 s |
| RESTING_BID_PRICE_NEVER_OFFERED | 456 | 16.3% | 205.0 | 0.0 | 0.0% | never |
| NEWLY_POSTED_IMPROVED_THE_TOUCH | 23 | 0.8% | 0.0 | 0.0 | 87.0% | 839 s |

**The two depth columns are an identity, not a finding.** `BOOK ⟺ p ≤ bid` and
`MINT ⟺ p ≥ ask`, so a bid cannot rest at a MINT price and a complement bid
cannot rest at 1−p for a BOOK price. Both zeros are forced. They are shown so
the forcing is visible, and nothing is concluded from them.

What is not forced: **16.3% of L5's maker fills are at prices the ask never
offered within 900 s of lookback** — and they carry p50 205 shares of depth at
that price. A resting bid cannot fill where no seller ever came.

## A′. The on-chain test — `analysis/l5/historical_l2/mint_onchain.py`

A complete-set mint is not a theory about this venue; it is a state transition
the ConditionalTokens contract records as `PositionSplit`
(`0x2e6bb91f` at `0x4d97dcd9…76045`). So: does the transaction carrying L5's
fill contain one? Observable, falsifiable, and nothing in the book can force it.

Restricted to SINGLE-MATCH transactions — exactly one exchange fill pair, so no
other match in the transaction could have caused the split — and compared
against **non-L5 exchange fills in the same blocks**, bootstrapped over BLOCKS
rather than transactions because transactions in a block share a batch:

| population | n | blocks | PositionSplit | diff vs baseline, 95% block-CI |
|---|---|---|---|---|
| venue baseline, non-L5 | 203 | 36 | **58.6%** | — |
| L5 MINT_MATCH_FEASIBLE | 131 | 79 | **85.5%** | [+0.165, +0.374] excludes 0 |
| L5 RESTING_BID_PRICE_NEVER_OFFERED | 150 | 97 | **81.3%** | [+0.118, +0.331] excludes 0 |
| L5 BOOK_FILL_FIFO_CONSISTENT | 136 | 90 | **73.5%** | [+0.038, +0.253] excludes 0 |
| L5 NEWLY_POSTED | 17 | 12 | 58.8% | [−0.237, +0.286] |

Three findings, in order of how much they change the model:

1. **Minting is the majority settlement path for L5's maker fills in every
   class**, including the ones that looked like ordinary book fills. The
   at/above-ask fills are not a separate mechanism — they are the visible tail
   of something that was already happening to most of the population.
2. **The venue baseline does not wash it out.** 58.6% of other traders' fills
   in the same blocks mint; L5's do at 73.5–85.5%, and three of four classes
   exclude zero under block clustering. This is the control that §7 of the
   overlap findings taught us to run, and unlike that one it discriminates.
3. **The 16.3% "impossible" fills mint at 81.3%.** They were never book fills.

**Therefore conventional visible-book queue depth is not the correct queue
variable for the majority of L5's maker fills.** A mint consumes no offer on
either buyer's book. Depth resting at his price is a bystander to it.

## B. The PMUS equivalent — and a flow-reconstruction defect

Same measurements on our own replayed maker orders, on corrected intervals
(9,548 complement rungs, lifetime p50 69.6 s, 7 book snapshots each):

| | n | share |
|---|---|---|
| market traded THROUGH our price | 1,580 | 16.5% |
| merely REACHED it | 772 | 8.1% |
| NEVER reached it | 7,196 | 75.4% |

That 75.4% is an **upper bound**, not a fact: the PMUS book is polled every
~8 s (p50 8.0, p90 12.7), so intra-gap visits are invisible.

The cross-tab then exposed a second defect. **981 rungs the book says were never
reached still clear the queue on the inferred tape** — 75.5% of all rung-level
fills. Two candidates, both tested:

- *Stale republish?* **Rejected.** A lag sweep of `last_trade_px` against the
  book peaks at T−0 (68.7% inside) and falls monotonically to 8.5% at T−600.
- *Mixed price convention?* **Rejected.** Only 1.9% of out-of-book prices land
  inside under the 1−p mirror.

What is true is worse and simpler. Scored against the quote in **its own HTTP
response** — zero sampling offset — `last_trade_px` lies outside [bid, ask] in
**22.8%** of volume increments. The replay treats `(Δshares_traded_cum,
last_trade_px)` as one print of that size at that price, but Δvolume is every
trade in an ~8 s poll interval at prices we never see, and `last_trade_px` is
only the most recent of them. **Size is overstated at a single price and that
price is a lagging point estimate of a distribution.**

So the queue arm and the flow-attribution arm were confounded. Separating them
by re-running the identical policy on flow filtered to attributable increments:

| | matched, queue charged | matched, queue zero | effect of removing the queue |
|---|---|---|---|
| ALL flow | 884 | 2,293 | **+1,409** |
| ATTRIBUTABLE flow (74.1% of rows) | 553 | 1,553 | **+1,000** |

**The queue effect survives the correction.** It is inflated ~29% by
unattributable flow but remains the largest single execution variable. Dropping
unattributable rows costs 331 (charged) / 740 (zero) matched pairs — smaller
than the queue effect in both columns.

## C. Seven counterfactuals — `analysis/pmus/execution_counterfactuals.py`

One knob each from REAL_QUEUE; policy, model, books, decision gap, residual cap
and completion rule identical throughout. SETTLED-MODELED (state ==
`MARKET_STATE_EXPIRED` only; 1,313/1,352 slugs, 97.1%). The two P&L lines are
never summed. Fees 0; rebates 0 and never estimated.

| arm | flow | fills | matched | resid | basis | turnover $ | matched $ | residual $ |
|---|---|---|---|---|---|---|---|---|
| REAL_QUEUE | ALL | 2,418 | 884 | 650 | 0.9280 | 1,121.54 | −8.76 | −128.69 |
| REAL_QUEUE | ATTRIBUTABLE | 1,703 | 553 | 597 | 0.8851 | 753.45 | −2.45 | −116.12 |
| ZERO_QUEUE | ALL | 5,354 | 2,293 | 768 | 0.9771 | 2,613.84 | −49.86 | −175.54 |
| ZERO_QUEUE | ATTRIBUTABLE | 3,858 | 1,553 | 752 | 0.9587 | 1,847.96 | −31.90 | −151.37 |
| IMPROVED_QUEUE_50 | ATTRIBUTABLE | 1,925 | 643 | 639 | 0.8984 | 864.66 | −3.43 | −145.80 |
| IMPROVED_QUEUE_25 | ATTRIBUTABLE | 2,170 | 749 | 672 | 0.9104 | 987.50 | −7.47 | −152.73 |
| ONE_TICK_BEHIND | ATTRIBUTABLE | 1,368 | 390 | 588 | **0.8345** | 571.18 | **+6.31** | −122.57 |
| ONE_TICK_AHEAD | ATTRIBUTABLE | 1,869 | 627 | 615 | 0.8878 | 829.68 | −1.40 | −124.43 |
| CANCEL_REPRICE | ATTRIBUTABLE | 1,831 | 608 | 615 | 0.9042 | 827.22 | −3.70 | −145.60 |
| SINGLE_PRICE | ATTRIBUTABLE | 615 | 192 | 231 | **0.8280** | 254.72 | **+7.86** | −46.83 |

Read one variable at a time, and note that **no arm is selected on its P&L**:

- **Queue position is the dominant fill lever**, monotone and large: charged →
  ×0.5 → ×0.25 → zero gives 553 → 643 → 749 → 1,553 matched.
- **And it buys fills at a worsening price.** Basis climbs 0.8851 → 0.9587 as
  the queue is removed. More fills at a worse basis is what standing at the
  front of a queue in an adversely-selected book buys you.
- **The only two arms with positive matched-pair economics are the two that
  trade LESS** — one tick behind the touch (0.8345) and a single resting price
  (0.8280). Both quote worse, fill rarer, and get a better basis for it.
- **Persistence is worth little here**: CANCEL_REPRICE 608 vs 553 matched.
- **Residual P&L is negative in every arm** and larger in magnitude than
  matched-pair P&L throughout. Under the surviving evidence, residual exposure
  has no demonstrated skill, and these numbers are consistent with that.

Turnover rises with fills in every arm, so nothing here is a P&L recommendation
— the profitable-looking arms are the low-turnover ones, which is what a
selection effect on a near-zero edge looks like.

## D. The simplest executor the evidence actually requires

Built only from what survived `007feaab` plus today's on-chain result. Nothing
below assumes four rungs, both-side simultaneous ladders, replenishment rules,
incentive magnitude, or any completion cap.

**Required:**

1. **Rest as a BUYER on both outcomes at once.** This is the one genuinely new
   requirement, and it is forced: the fills are mints, and a mint needs a
   counterparty buying the complement. Two-sided resting bids are what makes an
   order *mintable* at all. Under the old book model, two-sided resting was a
   risk-management choice; under the mint evidence it is the fill mechanism.
2. **At least two prices resting simultaneously on a side, one tick apart.**
   (3,434 atomic multi-price sweeps, 99.9% one-tick — survives `007feaab`.)
3. **Patience measured in minutes.** Time from first observable opportunity to
   fill is p50 194–294 s, and the touch falls toward the resting order rather
   than the order chasing the touch.
4. **Maker-only.** 77.3% maker is a venue-supplied field; no taker crossing is
   required by any of this.

**Not required by evidence, and therefore not in the model:** rung count beyond
two, ladder symmetry, replenishment, sizing curves, a completion cap, any
incentive magnitude.

**The selection rule this implies** is not habitat imbalance. It is: *does this
market have a standing population of buyers on the OTHER outcome?* That is a
different measurement from flow imbalance and is not yet instrumented.

## E. The Buccaneers sanity check

`aec-nfl-cle-tb-2026-09-20`, 919 snapshots over 4.8 h. A persistent maker —
one order per side, re-pegged to the touch, never voluntarily cancelled, the
weakest assumption that still counts as a maker:

| | acquisitions | matched | basis | first complete pair |
|---|---|---|---|---|
| real measured queue | **0** | 0 | n/a | **never** |
| zero queue (first in line all game) | 51 | 23 | 0.9950 | **228 min** |

The market's own numbers explain it without any model:

- spread is a **constant 0.5 ¢** (p50 = p10 = min), so rest-both is 0.9950 and
  take-both is 1.0050 — the binary identity, not a market condition;
- touch depth is **282,637 bid / 226,325 ask**;
- total inferred flow across the whole game is **52 increments**.

A quarter of a million shares rest ahead of you and 52 volume increments arrive
all game. First in line for four hours, you assemble one pair after 3.8 hours at
a 0.5% gross basis edge before adverse selection.

**This is the answer to the question asked days ago — why a human could do in
one second what the system could not do in days.** The human *took*: 1.0050,
paying 50 bp for immediacy, available at any instant. The maker cannot buy
immediacy at any queue position, because the flow to fill against does not
exist. It was never an executor defect.

## What this changes

- Stop treating **visible-book queue depth** as the model of L5's fills. For the
  majority of them it is not the binding variable.
- Stop treating **habitat flow imbalance** as the venue test. The evidenced
  requirement is a standing population of complement BUYERS.
- PMUS sports is not disqualified by imbalance — it is disqualified here by
  **depth ≫ flow**: 283k resting against 52 increments per game.

## Open and not claimed

- **Per-fill attribution of a mint is bounded, not exact.** Single-match
  transactions remove the batching confound; they do not prove L5's specific
  order caused the split.
- **Whether PMUS or Kalshi supports mint-style matching at all is untested.**
  The whole D model rests on a mechanism confirmed only on Polymarket
  International. This is the next thing to establish and nothing should be
  built on D until it is.
- **PMUS flow reconstruction remains a bound**, not a tape: 22.8% of increments
  carry an unattributable price, and every PMUS fill count is bracketed by
  ALL/ATTRIBUTABLE rather than stated as one number.
- No arm here is proposed for deployment and none was chosen on its P&L.
