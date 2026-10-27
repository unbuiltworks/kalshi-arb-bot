# From $0.21/window to $5/window: measured unit economics and the one change

Basis: the 16 authorized windows of campaigns 1 and 2 (2026-10-27 0000z–0145z
and 1330z–1515z), 15 of which took fills. Every figure is from
`L5_LIVE_SETTLED` lines with `resolution_source: VENUE_SETTLEMENT_PX` and
`venue_state: MARKET_STATE_EXPIRED`, plus `L5_CAMPAIGN_ROLLING` and
`L5_LIVE_DECISION` records. No rebate is inside any net figure. Production
unchanged.

## 0. What the $0.2086 actually is

| | total | per window |
|---|---:|---:|
| confirmed settled net (16 authorized) | **+$3.3376** | **$0.2086** |
| confirmed settled net (15 with fills) | +$3.3376 | $0.2225 |
| **matched surplus** | **+$2.7569** | $0.1838 |
| residual P&L, observed | +$0.5807 | +$0.0387 |
| fees | $0 (maker-only) | — |

**53% of the headline net is one directional windfall.** 1415z stranded 2.38
UP contracts that happened to settle at $1.00: **+$1.7612** on a $0.6188 cost
basis. Strip it and the two campaigns made **+$1.5764, $0.1051/window**.

Residual P&L came in at **+32.3% of residual cost** on n=3 strands. L5's own
corpus puts unpaired first legs at **−46.8%** (n=378) and our own earlier six
strands at −55.5%. Applying −46.8%:

| | total | per window |
|---|---:|---:|
| matched surplus | +$2.7569 | $0.1838 |
| expected residual P&L | −$0.8421 | −$0.0561 |
| **expectancy-adjusted net** | **+$1.9148** | **$0.1277** |

**$0.128/window is the repeatable number.** Everything below is built on it,
not on $0.2086.

## 1. Unit economics (measured, n=15 windows / 35.84 matched contracts)

| quantity | value |
|---|---:|
| matched contracts | 35.84 (2.389/window) |
| mean achieved pair cost | **0.9231** |
| **matched surplus per matched contract** | **$0.07692** |
| residual cost per matched contract | $0.05020 |
| expected residual loss per matched contract (at −46.8%) | $0.02350 |
| **net per matched contract** | **$0.05343** |
| realised cash cost per matched contract | $0.97328 |
| gate obligation per matched contract | ~$1.50 (measured 1.32–1.64) |
| **net per obligation dollar per window** | **$0.0356** |

The obligation is the binding number, not the cash cost: a resting DOWN leg is
a `BUY_SHORT` and the cash gate charges it a dollar a share, so each round
books ~$3.00 of obligation against ~$1.84 of realised cost.

**3.6% per obligation-dollar per 15-minute window is a good return.** The
problem is not the rate. It is that we deploy almost none of the envelope we
already have.

## 2. The binding constraint is envelope utilisation, not the envelope

From `L5_LIVE_DECISION` on 1500z, on every opening plan:

```
sized_by: "CASH_ENVELOPE"          cash_envelope_total_usd: "12.00"
round_allocations: 4                cash_envelope_per_round_usd: "3.00"
opening_leg_cap_binds: false        opening_leg_max_contracts: "3"
opening_notional_binds: false       cash_gate_obligation_usd: "2.9848"
```

So the per-round envelope binds, the 3-contract opening cap does **not**, and
each round consumes essentially the whole $3.00. Four rounds would spend the
full $12.00.

Measured utilisation: 2.389 matched contracts/window × ~$1.50 = **$3.58 of
obligation against a $12.00 envelope — 30%.** Only 1500z reached round 4, and
it was the best window of both campaigns (5.52 matched, +$0.668).

Fully using the envelope already configured gives ~7.3 matched contracts and
**~$0.39/window** expectancy-adjusted — 3× today — with no capital increase,
no limit change and no strategy change.

## 3. Why the rounds do not happen, and the one change

Rounds are sequential and each takes about 99 seconds (1500z: first order
+142.4s, round 4 by +538s, four rounds in ~396s). A 900-second window with a
clean start supports roughly 7–8 rounds; the engine is configured for 4.

**It loses the first ~95 seconds of every window to its own rate limiter.**
Measured last pass and restated here because the economics changed:

| transition | requests | throttled_waits | **total_wait_s** | http_429 | in_cooloff |
|---|---|---|---|---|---|
| 1515z | +121 | +111 | **+61.14s** | 0 | false |
| 0015z | +128 | +111 | **+60.15s** | 0 | false |

~125 venue reads at the boundary, paced at `rps=1.5`, with
`VenueRateLimiter.before()` calling `_sleep()` on the money-path tick. First
order lands at **84.4–142.4s, median 93.2s** after the window opens. No 429s,
no cool-off: this is self-inflicted.

95 seconds ≈ **one round** ≈ 1.84 matched contracts ≈ **+$0.098/window**
expectancy-adjusted — **+77% on a $0.128 base** — spending $3.00 of the $8.42
of envelope that currently goes unused.

> **The one change: cut the window-boundary read burst so the first round
> opens ~90s earlier.** Three candidates, none implemented, none authorized:
> (a) do not re-run full preflight from scratch each boundary when series,
> account and venue facts are unchanged; (b) move `verify_alignment`'s
> pagination (up to 40 sequential GETs, measured 8.21s) off the tick; (c) let
> the limiter's wait yield instead of blocking the tick.

Per dollar of **new** capital at risk this is the best item available, because
it needs none: it raises utilisation of an envelope already configured at
$12.00 under an unchanged $20 hard exposure ceiling. Every other way of
raising net buys contracts at the same $0.0356/obligation-dollar and requires
authorizing more capital.

**This does not reopen a rejected experiment.** "Earlier opening" was a NO-GO
when the delay was believed to be venue settlement latency, and the
settlement-pending concurrency feature was a NO-GO last pass. The cause
measured here is a third and different one — our own limiter pacing — and the
counter deltas above are new evidence.

## 4. Why no first-leg predictor of completion-vs-strand can exist

Item 3 asked for a decision-time predictor validated out of sample. It cannot
be found, and the reason is structural rather than statistical.

At any single instant `UP_own + DOWN_own = 1.00 + spread`. Completing a pair
at the 0.95 target therefore requires a **≥5-cent favourable move between the
two legs**. A strand is the same event with the opposite sign. So "profitable
completion vs residual loss" *is* the sign of the BTC move over the next few
minutes, and directional skill has already been tested and rejected
(`a575aa33`; `L5_OPERATING_MODEL.md`: residual exposure has no predictive
skill).

The decision record confirms the degeneracy directly. On **every** opening
plan in 1500z, whatever the book:

```
depth_ticks: 2    pair_cost: "0.9500"    edge_per_contract: "0.0500"
```

The planned pair economics are a constant. There is no decision-time variation
to regress on.

What our own 15 windows say, with the power they have (n=3 strands):

- **Opening lateness does not predict stranding.** Strands opened at +87.2s,
  +108.8s, +86.3s; the *latest* opener of all, 1500z at +142.4s, produced the
  best window of both campaigns.
- **Capacity remains the only supported variable**, as
  `FIRST_LEG_ADMISSION_FINDINGS.md` concluded on L5's corpus. Both windows
  that ran ≥2 rounds (1445z, 1500z) completed fully and produced the two
  largest matched surpluses; 2 of 6 one-round campaign-2 windows stranded
  entirely. n=8, underpowered, directionally consistent, not significant.

**Missing evidence, stated exactly:** to test any first-leg admission rule we
would need ≥200 of our own first-leg cohorts with venue-confirmed settlement,
against ~15 today. At 8 windows/campaign and ~1.5 cohorts/window that is
~17 campaigns. No offline substitute exists: L5's corpus has no book, and the
public tape has no fills.

## 5. The progression

Net per matched contract $0.05343; obligation ~$1.50/contract; residual
contracts run at 0.174 per matched contract.

| target $/window | matched contracts | gate obligation $ | realised cash $ | residual-loss allowance $/window | contract-fills/window | filled orders @clip 2.0 | @clip 10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **$0.128 today** | 2.4 | 3.6 | 2.3 | 0.06 | 5 | 3 | 1 |
| **$0.39** full existing envelope | 7.3 | **10.95** | 7.11 | 0.17 | 16 | 8 | 2 |
| **$1.00** | 18.7 | **28.08** | 18.22 | 0.44 | 41 | 20 | 4 |
| **$2.50** | 46.8 | **70.20** | 45.55 | 1.10 | 102 | 51 | 10 |
| **$5.00** | 93.6 | **140.40** | 91.10 | 2.20 | 203 | 102 | 20 |

$140 of obligation for $5/window agrees with the independent $107–138 estimate
in `FIRST_LEG_ADMISSION_FINDINGS.md`, derived from L5's corpus rather than
ours.

### What each rung requires

**$0.39 — inside every existing limit.** $12.00 envelope, $20 exposure
ceiling, 3-contract opening cap, 0.95 target, maker-only: all unchanged. Needs
only that 4 rounds per window actually open. The boundary-burst fix is most of
the way there.

**$1.00 — first limit change.** $28 of obligation exceeds both the $12
envelope and the **$20 `MAX_TOTAL_EXPOSURE_USD` hard code ceiling**. Needs
~4.7 contracts/round, so `OPENING_LEG_MAX_CONTRACTS` must rise from 3 as well.

**$2.50 — $70 obligation**, ~10-contract clips, ~51 filled orders/window at
today's clip or ~10 at a 10-contract clip.

**$5.00 — $140 obligation**, ~94 matched contracts, ~20 filled orders/window
at a 10-contract clip. That is **inside L5's own observed regime** (32.5
fills/window at a 10.26 clip) and well inside the book: measured level depth
at the touch is 150–1,866 shares against clips of 1.8–2.3 today, so a
10-contract order is 0.5–7% of one level. **Venue liquidity is not the
constraint at any rung in this table.**

### The constraint nobody has named yet

`max_l5_daily_loss_usd` is **$5** and `max_cumulative_loss_usd` is $20. The
expected *residual* loss alone over an 8-window day:

| target | residual loss/window | over 8 windows | vs $5 daily halt |
|---|---:|---:|---|
| $0.39 | $0.17 | $1.37 | fine |
| $1.00 | $0.44 | $3.52 | **marginal** |
| $2.50 | $1.10 | $8.80 | **halts** |
| $5.00 | $2.20 | $17.60 | **halts on day one** |

**The $5 daily realized-loss halt caps the achievable target at roughly
$1/window**, independent of capital. Residual losses are not errors to
engineer away — they are the cost side of the matched surplus and they scale
with it. Any path past $1/window requires the owner to re-scale the loss halts
in the same proportion as the capital, and that is a capital-risk decision,
not an engineering one.

## 6. Ranking by incremental settled net per dollar of capital at risk

| # | change | Δ net/window | new capital at risk | $/new-$ | status |
|---|---|---:|---:|---|---|
| 1 | cut the boundary read burst (~95s → ~1 extra round) | **+$0.098** | **$0** (uses authorized envelope) | **∞** | measured, not implemented |
| 2 | reach 4 rounds/window within the $12 envelope | +$0.26 total | $0 | ∞ | needs #1 plus ticket budget |
| 3 | verify maker rebates are credited | +$0.0056/contract (+7.3% on margin) | $0 | ∞ | `rebate_credited_usd: 0`; read-only check |
| 4 | raise envelope / clip / exposure ceiling | linear | linear | $0.0356/$ | owner capital decision |
| 5 | tighten the pair ceiling below 0.95 | negative | — | — | already refused; forgoes completions |

Items 1–3 cost no new capital. Item 4 is the only route past $0.39/window and
it is the owner's call, gated on the loss-halt arithmetic in §5.

On item 3: the engine reports `maker_credit_per_contract: "0.0056"` and
`net_if_maker: "0.0556"` against `net_if_taker: "0.0157"` on every plan, while
`rebate_credited_usd` has been **0 in both campaigns**. Across 77.92 contracts
filled that is roughly $0.21 unaccounted. It is additive economics and must
not be assumed — but confirming it from the venue's own records is free and
has never been done.

## 7. Status against the five-item sequence

1. **Settlement-pending validation — closed, NO-GO.** The 85–116s is the
   boundary read burst, not the slot. Recoverable eligibility ≤4.9s/transition
   against a 2.79s unblocked control. Uncommitted capital was ample
   ($7.15–$12.00 against a $3.00 round envelope, 14/14) — capital was never
   what stopped the next window.
2. **Expiry producer — not started, correctly.** Gated on item 1 being
   positive. It is not. Venue permission also remains unverified.
3. **First-leg predictor — none can exist** (§4), with the exact missing
   evidence named.
4. **Best improvement — the boundary read burst** (§6), the only item with a
   measured effect and no capital cost.
5. **Progression — §5**, with the $5 daily loss halt identified as the binding
   constraint above ~$1/window.

Nothing here has been implemented or deployed. Executor remains `f6ad0f55`;
all limits unchanged.
