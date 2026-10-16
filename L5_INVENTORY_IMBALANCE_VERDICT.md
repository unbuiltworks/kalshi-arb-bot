# Verdict: inventory imbalance is a cost, not an advantage

Read-only audit. No orders, no deployment, no configuration change. Live
execution remains halted and `L5_SHADOW_ONLY` remains set.

Reproduce: `python3 -I scripts/l5_settlement_decomposition_audit.py` — it rebuilds the
per-window leg sequences from `data/l5/raw` when the untracked derivation is
absent, so every figure here is reproducible from the raw capture alone.

---

## Headline

**Inventory imbalance is not a demonstrated economic advantage. It is the
single largest cost in L5's book, and it performs worse than random.**

L5's entire realized profit comes from the *matched* pair book bought below
$1.00. The residual — the imbalance — destroys 88% of that profit.

| component | shares | cash | realized |
|---|---|---|---|
| matched pairs | 6,170,303 | $6,006,130 | **+$164,172.76** (+2.73%) |
| residual inventory | 1,126,322 | $425,622 | **−$145,244.95** (−34.13%) |
| unredeemed / price rounding | — | — | −$7,000.23 |
| **actual realized net** | | **$6,434,084 spent** | **+$12,105.85 (+0.188%)** |

Over 5,546 windows and 165.5 days: **+$73.15/day** (see "the three totals" below), 53.2% of windows
profitable, median window +$3.67.

---

## 1. Two earlier figures were wrong. Both came from a truncated capture.

`data/l5/raw/trades.jsonl` is **one offset-paginated query that ran out of
pages at offset 10,500** — 10,500 rows of a ~768,000-row history, 1.4%, cut at
the tail. The 51-window reconstruction built from it, and everything derived
from it, does not survive contact with the complete capture:

| claim from the 51-window sample | complete capture (5,546 windows) |
|---|---|
| mean pair cost **$1.0464** — matched book *loses* 4.4% | mean **$0.9715**, median $0.9725, below $1.00 in **73.1%** of windows |
| residual is the earning component | residual is **−$145,245**, the largest loss in the book |
| −$3,712.82 / +$13,446.39 split; 21.6% vs 49.4% break-even | superseded — those payoffs were a 1.4% sample, and the question is answered directly from actual settlements |

The 21.6% figure the owner computed was arithmetically correct for the inputs
I gave it. The inputs were the problem. The break-even debate is moot: actual
settlements are available and have now been used.

`activity.jsonl` + `activity_gap.jsonl.gz` are **time-sliced**, not a single
offset run, and together hold 784,500 rows covering 2026-03-19 → 2026-08-31.
That is the authoritative source and the one used below.

## 2. Settlement-anchored, not inferred

Every window below uses the venue's own `REDEEM` rows for the winner and the
proceeds, and `usdcSize` — the cash that actually moved — for spend. Not
`size × price`: the reported price is rounded and does not reconcile to the
cashflow (a $27,610 gap across the history).

### The three totals, and which one is right

Cross-checked three ways. The spread is fully attributable — it is two
bookkeeping choices, not three measurements:

| total | basis | difference explained |
|---|---|---|
| **+$12,105.85** | undeduped, cash = `usdcSize` | **the authoritative figure** |
| +$17,788.58 | deduped, cash = `usdcSize` | 3,969 duplicate-key rows contribute **+$5,682.73** net cash; dropping them raises net by exactly that |
| +$19,176.71 | `data/l5_master`, cash = price×qty | `price×qty` undercounts buys by **$27,610.81** against actual cash, and its redemption total is $50,160 lower in coverage |

**+$12,105.85 is the one to use, so $73.15/day is the defensible figure, not
the middle of the range.** It uses the cash that actually moved, and it never
discards a row that might be a real fill. The two higher numbers come from
either dropping rows that may be genuine split fills (same `transactionHash`,
asset, type, timestamp, size, price and outcome — a split fill looks exactly
like a duplicate here, and I cannot distinguish them from this capture) or
from reconstructing cash as `price × qty`, where the reported price is rounded
and does not reconcile to the cashflow.

**Provisional only in this direction:** if those 3,969 rows turn out to be API
duplicates rather than split fills, the true figure rises toward $17.8k
($107/day). It cannot go below $12.1k. Resolving it needs per-fill identifiers
the public capture does not carry.

L5 **never sold**: 0.00 SELL shares across 700,703 trades. It also earned
**$0.00 in rebates or rewards** on btc-updown-15m. L5 was not rebate-funded.

## 3. The residual loses to a coin flip

L5 accumulates the *cheaper* side 58.95% of the time. That side is priced at
44.86% implied and **wins 39.58% of the time.** The imbalance is not a view —
it is adverse fill selection: the side that keeps filling is the side the
market is leaving.

Same residual shares, same prices, same timing:

| baseline | residual P&L |
|---|---|
| **L5 actual** | **−$145,244.95** |
| price-fair expectation (winner prob = own avg price) | $0.00 by construction |
| randomized winner, L5's own side choice | **+$136,877** (sd $10,628) |
| randomized *side* choice, actual winners | **+$22,531** (sd $7,404) |
| always load the dearer side instead | **+$216,424** |
| always load the cheaper side instead | −$171,080 |

L5's residual is ~$282,000 worse than a fair coin and ~$168,000 worse than
picking a side at random. **Picking the opposite side every time would have
been worth +$216,424.** That is not an edge being harvested; it is an edge
being paid.

### The honest caveat

Matched and residual are **not separable in practice.** The matched pairs
exist only *because* L5 quoted both sides passively and accepted that
sometimes only one side filled. The −34% residual is the price of the +2.73%
matched book. "Buy only matched pairs at those prices" is not an available
strategy — it is the same strategy with the losses deleted.

What this does establish, decisively: the imbalance is **not** a profit
mechanism to replicate, enlarge, or protect. Our engine's existing residual
suppression (`max_unmatched_contracts 5`, `max_abs_residual 10`, residual cap
k=2) and its refusal to place unless the pair prices below $1.00 are pointed
in the **correct** direction. My earlier conclusion that we were "optimising
the losing component and suppressing the earning one" was backwards.

## 4. Capital scaling: no size fixes the fee problem

Each window's own leg sequence replayed at a fixed cash budget, PMUS
constraints (0.01 qty increment and minimum, 0.01 price tick, legs that no
longer reach the minimum are **dropped**, not rounded up), settled at the
actual winner.

A replay pays $1.00 for every winning share; the venue did not (some winning
shares were never redeemed in the capture). Replay basis reads +0.7287% of
spend against +0.1882% actually realized, so the trading line carries a
**×0.258 haircut** below. Fees are shown exact (unrounded) — see the artifact
note.

| budget/window | maker | taker |
|---|---|---|
| $5 | **+0.76%** | **−1.95%** |
| $20 | +0.75% | −1.97% |
| $50 | +0.75% | −1.97% |
| $100 | +0.75% | −1.97% |
| $250 | +0.75% | −1.97% |

**Size-invariant.** Of the +0.75% maker figure, **+0.41pp is the maker rebate**
and only +0.33pp is trading. Taker is fatally negative at every size:
`Θ_taker × p(1−p)` ≈ 1.7% of notional against a 0.33% edge.

(Sequences rebuilt from `data/l5/raw` — 5,530 settled windows, cash from
`usdcSize`. The 375MB `data/l5_master` derivation gives +0.68% / −2.05% on
5,546 windows, because it carries 16 unsettled windows and reconstructs cash
as price×qty. Same conclusion either way.)

### Artifact note — do not read the small-budget numbers as printed

The venue rounds fees **per order, away from zero, to the cent.** At $5/window
the true maker rebate on a 0.01-contract order is ~$0.00003 and rounds to a
full $0.01 — a 300× inflation. With cent rounding the table reads +23.33% at
$5 and +1.61% at $250, purely from that. All 580,885 legs × $0.01 = the entire
$5,809 "rebate." Per `CLAUDE.md`, an uncredited rebate is not an assumption we
are allowed to bank, so the exact-fee column above is the one that counts.

### What $480/day would require

At +0.188% of deployed cash, $480/day needs **~$255,000 of cash deployed per
day** — ~96 windows/day at ~$2,650 per window. At $250/window that is
~$45/day. The historical target is not reachable at any capital level we have
authorized, and L5 itself only earned $73–$116/day on $6.4M of turnover.


---

## 5. The economic test: maker-only matched pairs, zero rebate

Requested constraints applied: **no rebate is assumed**, residual settlement
losses are the **actual** ones, and nothing here treats a scaled L5 trade as
an executable PMUS order (see the two caveats below).

> **Correction.** I wrote earlier that this account "has never been credited"
> a rebate, citing `rebates_ever_credited: false`. **That reading was wrong**,
> and for the same reason an earlier one was: before the structured-money
> extraction landed, all 1,911 commissions were *unreadable*, so the flag was
> false for want of a parser, not for want of rebates. With the extraction
> live the production audit reports **`rebates_ever_credited: true`** — 831
> credited-rebate rows, 975 zero, 105 charges, 0 unreadable, netting a
> **−$5.88 credit**. The account has received maker rebates.
>
> Everything below still assumes **zero** rebate. That is now a deliberate
> conservative floor rather than a statement of fact, and it means the
> maker-only case is understated, not overstated.

### 5a. The residual ratio is the whole lever

Per matched share, from the realized aggregate: the matched pair edge is
**+$0.026607** and a residual share is worth **−$0.128955**. Hold the residual
to ratio *k* of matched:

| k (residual / matched) | net per matched share | % of spend |
|---|---|---|
| 0.00 | +$0.026607 | **+2.73%** |
| 0.05 | +$0.020159 | +2.03% |
| 0.10 | +$0.013711 | +1.36% |
| **0.1825 (L5's own)** | +$0.003098 | **+0.30%** |
| 0.30 | −$0.012080 | −1.11% |

**Break-even k = 0.206.** L5 ran at 0.1825 — inside break-even by a hair, which
is the whole story of its $73–$116/day. Our existing residual caps put us far
below that, on the right side of the lever.

**Break-even both-leg fill rate, zero rebate: 82.90%.** Below that, one-sided
fills dominate and the pair edge cannot carry them.

### 5b. I had the taker question wrong

My earlier verdict said taker is "fatally negative, not recoverable by
sizing." That was measured with **every leg** crossing. Using a taker order
**only to complete a residual** is a different trade, and the data reverses
the conclusion:

| residual policy | realized | per share |
|---|---|---|
| **hold to settlement** (what L5 did) | **−$145,244.95** | −$0.12896 |
| **complete by crossing** the complement | **+$29,964.64** | +$0.02660 |
| — PMUS taker fees inside that | $15,379.11 | $0.01365 |

**Completing beats holding by $175,210**, and is individually better in 60.4%
of windows. Total goes from **+0.29% to +3.02% of spend.** A 1.4¢ taker fee is
cheap against a 12.9¢ adverse-settlement loss.

### 5c. Where this is fragile

The completion price above is the complement's **average traded price across
the window**, not the ask we would actually pay. Adding slippage *s* per share:

| slippage | completion | total % of spend |
|---|---|---|
| 0¢ | +$29,965 | +3.02% |
| 1¢ | +$18,838 | +2.85% |
| **2.7¢** | **$0 — break-even** | +2.55% |
| 10¢ | −$80,236 | +1.31% |
| **15.9¢** | — | **ties L5's hold-to-settlement** |

Completion stays better than holding until ~**15.9¢** of slippage, but is only
**absolutely profitable to ~2.7¢** — and measured adverse selection on this
venue was 1.0–1.3¢. That margin is real but thin. The robustness comes from
the matched book, not from completion: even at 10¢ slippage the total is
+1.31%, still 4× L5's realized rate.

### 5d. Two caveats that bound every number above

1. **Pair-cost selection is hindsight.** Restricting to windows that achieved
   pair ≤$0.96 gives +3.24% of spend, and ≤$0.94 gives +4.16% — but the
   realized pair cost is only knowable *after* both legs filled. You cannot
   select on it ex ante. Treat those as an upper bound, not a plan.
2. **No measured PMUS maker fill rate exists.** The depth comparison has
   reported `NOT_COMPARABLE` on every run because nothing has ever been
   placed. The 82.90% break-even both-fill rate is therefore an unmet
   *requirement*, not a satisfied condition. This is the single binding
   unknown.

### Verdict on the economics

**CONDITIONAL GO on measurement, NO-GO on capital.** The matched-pair maker
book has a defensible +2.73% edge on matched cash with zero rebate assumed,
and completing residuals by crossing is worth another ~$175k against holding
them. What is missing is not economics but one measured number: whether we
achieve an 82.90% both-leg fill rate at depth 1 on PMUS. Nothing in this
repository has measured it. Until it is measured, position size should not
move.

---

## Verdict

**Inventory imbalance is merely a description of how L5's realized gains were
distributed — and an unflattering one.** It is where the losses are, not where
the gains are. It loses to a coin flip by ~$168k and to a price-fair bet by
~$145k.

The replicable mechanism is narrower than the behavioral thesis assumed:
**passive two-sided quoting that accumulates matched pairs at a mean cost of
$0.9715, while treating the resulting imbalance as an unavoidable cost to be
bounded.** That is worth +2.73% on matched cash and +0.19% to +0.30% on total
deployed cash, before venue fees.

## Consequences for the live program

1. **Taker completion deserves a second look, and I was wrong to close it.**
   Crossing *every* leg is −1.97% of spend. Crossing *only to complete a
   residual* is worth +$175,210 against holding, with ~15.9¢ of slack before
   it loses and ~2.7¢ before it stops being profitable outright. That is a
   measurement question for `L5_TS_COMPLETION_CROSS`, not a settled no.
2. **Maker-only is the only viable posture,** and 55% of its margin is a
   rebate we have not seen credited. `L5_MAX_UNRESOLVED_UNKNOWN_USD` and the
   "no unsupported rebate assumptions" rule both still bind.
3. **Residual suppression is correct and should not be relaxed** to imitate
   L5's imbalance. The imbalance is the loss.
4. **The depth-1 configuration remains NO-GO** and this changes nothing about
   that: at depth 1 `net_if_taker` is −0.003 and the matched edge is thinner
   than the measured +2.73%.
5. **$480/day is not a reachable target** at authorized capital. It should be
   restated or retired rather than measured against.

Nothing here justifies a live order. No limit, gate, threshold, or mode was
changed.
