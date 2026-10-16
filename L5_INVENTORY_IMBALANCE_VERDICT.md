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

Over 5,546 windows and 165.5 days: **+$73.15/day**, 53.2% of windows
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

Cross-checked three ways, all agreeing on sign and order of magnitude:
`+$12,106` (this build), `+$17,789` (same build, duplicate rows removed),
`+$19,177` (the pre-existing `data/l5_master/btc15m_sequences.jsonl`). Call it
**+$12k to +$19k total, +0.19% to +0.30% of deployed cash, $73–$116/day.**

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

1. **Taker completion stays disabled.** −2.05% of spend at every size, against
   a +0.26% trading edge. Not marginal; not recoverable by sizing.
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
