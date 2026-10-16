# Smallest economically meaningful PMUS BTC15m validation

All figures from the live PMUS authenticated book, PMUS venue minimums, and
the PMUS maker rebate confirmed on our own account. No other venue informs
anything below.

## Current executable PMUS prices

`cpc-btc-updown-15m-2026-10-16-1600z`, `MARKET_STATE_OPEN`,
**bid 0.3100 / ask 0.3200** (1¢ spread), depth 7,595.72 / 481.01,
`stream_age_s` 0.002, source-to-receipt lag median 0.033s,
`gap_suspected: false`, available cash $344.41255.

Maker pair: **UP at 0.31**, **DOWN at 0.68** (= 1 − ask) →
**pair cost 0.9900**, gross matched edge **$0.0100 per pair**.

## Sizing, with the rebate rounding shown explicitly

| q | cash | worst-case loss | rebate UP | rebate DOWN | **rebate PAID** | net if both fill |
|---|---|---|---|---|---|---|
| 0.01 (venue min) | $0.0099 | $0.0068 | $0.000027 | $0.000027 | **$0.00** | $0.0001 |
| 1.01 (**$1 cap**) | **$0.9999** | **$0.6868** | $0.002700 | $0.002747 | **$0.00** | **$0.0101** |
| 1.47 ($1 loss cap) | $1.4553 | $0.9996 | $0.003931 | $0.003998 | $0.00 | $0.0147 |
| **1.87** | $1.8513 | $1.2716 | $0.005000 | $0.005086 | **$0.01** | $0.0287 |
| 2.00 | $1.9800 | $1.3600 | $0.005347 | $0.005440 | **$0.02** | $0.0400 |

The rebate crosses into a credited cent at **q = 1.87**, because our account
pays a cent where the modelled rebate reaches ≈$0.005 (observed: $0.0100 paid
on modelled $0.0070, $0.0092 and $0.0123 — nearest-cent rounding).

## The proposal: q = 1.01 per side

**Inside both authorized caps, and no increase requested.**

| | |
|---|---|
| quantity | **1.01 contracts per side** (venue increment 0.01) |
| UP leg | maker buy 1.01 @ **0.31** = $0.3131 |
| DOWN leg | maker buy 1.01 @ **0.68** = $0.6868 |
| **gross cash requirement** | **$0.9999** ✓ under $1 |
| **maximum loss including fees** | **$0.6868** ✓ under $1 |
| maker rebate at this size | **$0.00 — rounds away on both legs** |
| net if both legs fill | **+$0.0101** (settled, fee-inclusive) |
| net if one leg fills and we hold | +$0.6969 if it wins, −$0.3131 if not |

Worst case is one leg filling and settling worthless. There is **no fee charge
in any branch**: both legs are maker, so the fee is a rebate that rounds to
$0.00 rather than a cost. A taker completion, if the economics gate allows it,
adds at most $0.0695 × 1.01 × p(1−p) ≈ **$0.0151** — inside the $1 bound with
$0.29 to spare.

Every existing control has headroom: account-wide worst case $2.0400 + $0.6868
= **$2.7268 of $5**; per-market $0.6868 of $5; unmatched 1.01 of 5; residual
1.01 of 10; `residual_cap_k` 2 from flat.

## Information gained

This is the honest list. It is narrow, and it is the only PMUS-native evidence
that exists to be had at this size.

**What it establishes:**

1. **Whether our maker order rests and fills on PMUS at all.** Currently
   unmeasured: the depth comparison has reported `NOT_COMPARABLE` on every run
   because nothing has ever been placed. `l5_owned_fills` is 0 on all 49
   historical legs, so **no PMUS fill has ever been attributable to this
   engine.** One fill changes that from unknown to measured.
2. **One real PMUS realized net number**, settlement-labelled, with fees and
   rebates on separate lines — the first that is ours.
3. **The paired-completion path end to end**: submit → rest → fill → complete
   or hold → settle → reconcile → retire, through the real executor.
4. **The rebate-rounding prediction.** I predict **$0.00** credited at
   q = 1.01. If a cent appears, my ≈$0.005 threshold is wrong and the
   minimum-size arithmetic above needs redoing.
5. **Queue reality at the touch.** `bid_shares` 7,595.72 against our 1.01
   means we join behind ~7,500 contracts. Whether that fills inside a 15m
   window is exactly the unknown.

**What it does not establish:** a fill *rate*. One pair is n = 1. It cannot
confirm or refute a 69–77% requirement, and I will not report it as if it
could.

## The precise decision needed before execution

Four changes, all yours. I have made none.

| # | change | why |
|---|---|---|
| 1 | clear HALT via `deploy/l5_halt_reset` | latched; watchdog refuses on every sweep, correctly |
| 2 | unset `L5_SHADOW_ONLY` | `submission_gate.blocked: true` / `SHADOW_ONLY_LOCK` |
| 3 | `L5_MAX_LIVE_ORDERS` 1 → **2** | a maker pair needs two resting orders; your envelope allows two |
| 4 | `L5_TS_COMPLETION_CROSS=YES` | selective completion, economics-gated, which your envelope authorizes |

**And the one protected accounting/risk-policy decision that actually
blocks:**

`refuse()` computes `loss = max(0, −realized) + max(0, unrealized)` and
refuses at `loss >= max_cumulative_loss_usd`. Live `realized_today` is
**−$233.98540000**.

You have decided **not** to raise the ceiling to bypass historical losses. I
agree with that decision, and it means **this validation cannot execute
today.** I am not going to describe it as ready when a gate refuses it.

The ceiling would have to exceed **$233.9854** now, or **$24.3216** with the
47-leg correction applied. Applying the correction does **not** clear it — it
narrows the gap 9.6× and leaves a 24× breach against a $1-appropriate ceiling.

So the decision is one of:

- **(a)** Apply the 47-leg correction (the verb is built, 24 guard tests
  passing, not wired to any call site), then decide on a ceiling above
  $24.3216 — a far smaller judgement than $233.99.
- **(b)** Set `L5_MAX_UNRESOLVED_UNKNOWN_USD` and rescope the ceiling to the
  `verified_l5` population, which the backstop was built as the precondition
  for. This is the only route that does not require accepting an unresolved
  figure above the ceiling, and it needs security review on its own terms.
- **(c)** Leave it. The executor stays safe and ready; no PMUS validation runs.

I recommend **(a) then (b)**: correct the cost basis first so any ceiling
conversation is about $24.32 rather than $233.99, then let the backstop carry
the rescoping rather than raising a number.

## State

`submission_gate.blocked: true` / `SHADOW_ONLY_LOCK`, HALT latched,
`armed: false`, `live_orders: 0`, `max_live_orders: 1`,
`max_cash_deployed_usd: 1`, `L5_TS_COMPLETION_CROSS` unset,
`L5_MAX_UNRESOLVED_UNKNOWN_USD` unset, account exposure $2.0400 of $5,
ladder recorder committed and **OFF**. No order submitted, no variable
changed, no gate weakened, no service or branch created. The 47-leg
correction is implemented, tested, unwired and unapplied.
