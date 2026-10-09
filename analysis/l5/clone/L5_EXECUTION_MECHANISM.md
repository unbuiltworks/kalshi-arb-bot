# How L5 obtains matched inventory — and why our shadow cannot

Sources, all pre-existing in this repository and re-verified today, not
rebuilt: `analysis/l5/historical_l2/` (Zenodo record 22084977, 1-second
Polymarket L2 for BTC/ETH 15-minute up-down, 2026-08-21..24, joined to L5 by
CTF token id), `bbo_offset.json`, `mint_match.json`, the 760,817-fill corpus,
and the live clone shadow's own tape.

## 1. The matching-rate gap, quantified

| | L5, early regime (2,070 markets) | our P6 shadow |
|---|---|---|
| markets ending two-sided | **98.3%** | 21% of entries |
| fills per market (median) | **80.5** (p90 153) | 1–3 |
| matched fraction of shares | **0.904** | n/a (mostly unmatched) |
| residual at end, share of volume | **9.65%** | ~79% of entries |
| first→last fill span | **744 s** of a 900 s window | 614 s at risk |
| pair cost when two-sided | median **0.9581**, 77.9% <$1, 51.3% ≤0.96 | median 0.94, 100% ≤0.96 |

**L5 reaches a 0.958 pair cost while completing 98% of his markets. P6
reaches 0.94 by completing 21% and leaving the rest unhedged.** Same
headline pair cost, opposite risk profile — so P6 is not reproducing L5.
It is a selection filter over cheap completions.

The mechanism P6 lacks is **frequency inside a single market**: 80 fills per
window against our 1–3. At 80 fills he has dozens of independent chances to
be hit cheaply on each side, and matching is then a law-of-large-numbers
consequence. At 1–3 fills, completion is a coin flip on whether the other
side comes to him before expiry.

## 2. Two acquisition channels, not one — and the second is the answer

Of the 2,795 L5 **maker** fills in the Zenodo overlap (88.3% of 3,166 fills),
classified against the book one second before each fill:

| T−1 class | n | share | mint feasible | pair-sum p50 |
|---|---|---|---|---|
| `RESTING_BID_CONSISTENT` | 2,022 | 72.7% | **1.5%** | 0.98 |
| `AT_OR_ABOVE_ASK` | 738 | **26.5%** | **99.86%** | 1.01 |
| `INSIDE_SPREAD` | 23 | 0.8% | 4.3% | 0.99 |

26.5% of his maker fills price **at or above the prevailing best ask**,
which a resting bid cannot do, and 99.86% of those are feasible as a
**complete-set mint**: two buyers of opposite outcomes matched by minting a
dollar, which needs no offer on the buyer's own book — only a counterparty
bidding the complement at ≥ 1−p. 1.5% against 99.86% is close to a perfect
discriminator, and the alternative explanation (mistimed fills) was tested
and rejected: real block timestamps differ from interpolation by p50 = 0.0 s.

**That is how he obtains matched inventory.** Roughly a quarter of his
acquisition is not "rest a bid and wait" at all; it is being matched against
the opposite-side buyer directly. Our PMUS shadow has no such channel in its
model, and it is the single largest structural difference between his
execution and ours.

`pair_sum` here is L5's fill price plus the complement's best bid — what the
two counterparties jointly pay to mint — **not** L5's own pair cost.

## 3. Where he rests, and the lead dependence that matters

Resting-bid-consistent fills only (mint matches excluded, since folding them
in as "0 ticks" would drag the answer to the touch). Cumulative share by
offset from the touch:

| offset | T−30 | T−5 | T−1 |
|---|---|---|---|
| join the touch | 8.0% | 19.8% | 38.4% |
| through −1 | 16.1% | 37.7% | 58.5% |
| through −2 | 23.8% | 53.5% | 70.3% |
| through −4 | 38.4% | 74.5% | **87.3%** |
| beyond −4 | **61.6%** | 25.5% | 12.7% |

**The touch falls toward his resting order.** Thirty seconds out, 61.6% of
the fills sit beyond −4 ticks from the then-current touch; one second out,
only 12.7% do. He is a patient passive buyer being filled as the market
comes down to him — not a quoter who joins the touch and hopes.

This is the strongest evidence-backed placement policy available, and it is
the opposite of what our shadow's entry rule does (join the own-side best
bid, i.e. the T−1 touch).

## 4. Ranked execution hypotheses

| rank | hypothesis | evidence | confidence |
|---|---|---|---|
| 1 | **Deep patient resting ladder, well below the touch, held across most of the window** | 61.6% beyond −4 ticks at T−30; 744 s median first→last-fill span; 80.5 fills/market | **High** — directly measured on aligned 1-second books |
| 2 | **A second acquisition channel via complete-set mint supplies ~a quarter of maker volume** | 26.5% at-or-above-ask, 99.86% mint-feasible vs 1.5% for resting-consistent | **High** — near-perfect discriminator, alternative rejected |
| 3 | **High matched fraction is a consequence of frequency, not of a completion rule** | 98.3% two-sided, 0.904 matched fraction, 80.5 fills | **Medium-high** — consistent, but "consequence" is inference |
| 4 | **Pair cost is an outcome of patient entry, not a gate** | 77.9% <$1 but only 51.3% ≤0.96; P6 shows that gating on 0.96 collapses completion to 21% | **Medium** |
| 5 | Late aggression is inventory management rather than directional | not re-tested here; the earlier taker-removal test was invalid by construction | **Low — untested** |

## 5. What this means for PMUS

- Our entry rule (join the own-side best bid) reproduces the **least**
  common L5 behaviour (8% of fills at T−30 lead).
- A deeper resting policy is testable on the tape we already collect: the
  ladder offsets are a parameter of the existing replay, not new
  infrastructure.
- **The mint channel may not exist on PMUS in the same form.** Polymarket's
  complete-set mint is a CTF primitive; whether the PMUS CLOB exposes an
  equivalent to a retail maker is unverified and must be checked against
  venue documentation before any policy leans on it. If it does not, ~26% of
  L5's acquisition mechanism is structurally unavailable to us, which would
  itself explain a large part of the matching-rate gap.

## 6. Coverage and honesty limits

- The Zenodo overlap is **12 BTC15m windows, 3,166 fills, 2026-08-21..22**.
  `FIRST_FILL` and `FIRST_OPPOSITE_FILL` populations cannot exceed 12
  observations and carry no decision.
- Our own BTC15m collection covers 2026-03-19..04-21 and 08-28..31 — **zero
  overlap** with the Zenodo window; the overlap fills were backfilled from
  the chain (0 chunk holes, 1,695/1,695 blocks with real timestamps).
- The pre-first-fill complement-liquidity test is **vacuous and labelled so**:
  these books quote continuously across the whole 1¢ grid (liquidity found
  at 1,175 of 1,176 prices), so "was there size at price q" returns yes for
  any q.
