# CP1 breadth-10 probe — SETTLED, REALIZED, and CAMPAIGN STOPPED

First complete money lifecycle of the program. **It is a loss.**

## Financial lifecycle — complete and strict

| stage | state | evidence |
|---|---|---|
| ARMED | YES | 2026-09-17T01:30:24Z, deployment `cd5667c1`, commit `78c5e2ba3f88` |
| SUBMITTED | YES | 267 clean orders across 19 markets in the ledger cohort |
| FILLED | YES | 3 fills, 0.48 contracts, all **maker** |
| SETTLED | **YES** | venue `POSITION_RESOLUTION_SIDE_LONG`, `beforeNet −0.4800 → afterNet 0.0000` at 02:26:39Z |
| REALIZED | **YES — −$0.1836** | `realized_confirmed: true`, `settled_by: position_resolution_activity` |

This is the first figure in this program that may be called **realized**: the
venue resolved the market, the resolution appears in our own account activity,
`position_net` is `0`, and both ledger rows (`settled`, `realized`) were written.

## The trade, end to end

`aec-itfwo-erishi-jiahan-2026-09-17` — Eri Shimizu vs. Jiangxue HAN.
We bought **NO** (the Jiangxue HAN side) three times and never completed a pair.

| # | order | relation | NO px | qty | rest_s | venue trade | cost |
|---|---|---|---|---|---|---|---|
| 1 | `CHQ5YQT34VNJ` | open | 0.43 | 0.18 | 27.33 | `CHQA0HARGVAY` @ 0.5700 YES-side | $0.0774 |
| 2 | `CHQ7CAEW2VNQ` | extend | 0.45 | 0.18 | 23.69 | `CHQB7VW2TVAY` @ 0.5500 YES-side | $0.0810 |
| 3 | `CHQDF60BAVNP` | extend | 0.21 | 0.12 | 18.90 | `CHQGZWCP8VAY` @ 0.7900 YES-side | $0.0252 |

**The two price conventions reconcile exactly.** The venue records these as
`ORDER_SIDE_SELL` / `ORDER_INTENT_BUY_SHORT` on the YES side, so its 0.57 / 0.55
/ 0.79 are `1 − 0.43` / `1 − 0.45` / `1 − 0.21`. Costs sum to **$0.1836**, which
is the realized loss to the cent. Nothing is unaccounted for.

Shimizu won. `outcomePrices ["1","0"]` — our side settled at **zero**.

## Three streams, never summed into one number

```
matched_pair_pnl_usd        $0         no pair was ever completed
directional_residual_pnl_usd −$0.1836  the entire loss
incentives_usd              $0.0000    credited_to_us_usd "0"
fees_usd                    venue_unreported
```

**100% of the loss is directional residual.** The sequence ran
OPEN → EXTEND → EXTEND → settle, with no COMPLETE, so the position was carried
to resolution as pure one-sided directional exposure. No rebate or reward was
credited, and none was assumed.

This is exactly what `L5_OPERATING_MODEL.md` warns about: *"Do not assume
residual directional exposure has predictive skill; historical evidence does not
support that."* The outcome is consistent with that instruction, not a
refutation of it.

Worth stating plainly: the loss is not an execution failure. The fills were
maker fills at the prices the controller asked for, and the venue reconciliation
never disagreed by a cent at any checkpoint. We were simply on the losing side
of a tennis match with no completed pair to neutralise it.

## Campaign end

```
kind campaign_end   status CAMPAIGN_STOP:HALT
sessions 19   settled 4   pending 0
realized_pnl "-0.1836"   realized_confirmed_by_venue true   daily_stop null
next "none: this status is an owner decision or a stop, and is never auto-restarted"
```

The final session on `aec-setkameua-shyant-mitole-2026-09-17` refused with
`MandateRefusal:max_concurrent_markets_exhausted`: the model wanted an OPEN
(`planned_safe_size` 0.21 contracts / $0.0966) and the mandate declined it. The
refusal is **fail-closed** — it prevented an open, it did not permit one — and
the campaign then stopped rather than rolling on. `daily_stop` is null; the
$6.00 daily loss stop was never approached.

**The probe is no longer trading and will not restart itself.** That matches the
standing rule that a completed live probe never automatically repeats. I have
not restarted it.

## Fidelity readout at campaign end

The split between the two layers is the headline:

**Behavioural layer — every gate PASSES.**

| gate | value |
|---|---|
| `action_distribution_relation` | L1 sampled vs L5 **0.0104** |
| `post_fill_complete_gap` | **−0.4 pp** |
| `off_distribution_rate` | **0.0** |
| `action_distribution_side` | **1.0** |

**Executable layer — FAILS, and the gate says why itself.**

`feasibility.status: GATE_FEASIBILITY_CONFLICT` —
*"the frozen ACTED thresholds are unreachable under the current safety shell —
gate and caps untouched, not a model deficiency."*

`cap_fallback_attribution.verdict: ALL_CAP_FORCED`. The binding rules are
`hold:residual_cap`, `hold:complement_not_priceable`, `hold:authenticated_book_unusable`
and `spend_cap`. Size is the clearest case: `median_executed_over_l5_p50 =
0.0132` — we trade at **1.3%** of L5's size, which is the $5.00/market cap doing
exactly what it was authorized to do.

Two gates that pass and matter here: `maker_taker` (live maker share **1.0** vs
L5 0.971) and `timing_cadence` (median interval 30.1 s, ratio to model cadence
**1.008**).

### The freshness gate's production verification

`divergence_classes` now carries, from live trading:

```
"venue/execution adaptation (authenticated book not fresh enough
 to validate post-only placement)": 3
```

and `hold_refusals` counts `authenticated_book_unusable: 3` **separately** from
model behaviour. The property the owner required is confirmed in production, not
merely in tests: a freshness skip is recorded as an execution adaptation and is
**not** scored as a model WAIT. `MODEL WANTED ACTION ≠ EXECUTION COULD SAFELY
SUBMIT ACTION` held.

## Position and exposure now

Flat on this market: `position_net 0`, `afterNet 0.0000`. Campaign realized
**−$0.1836** against a $15.00 cap and a $6.00 daily stop. Nothing outstanding on
this authorization.

The concurrency slot that blocked the MLB cohort is now free — but the campaign
has also stopped, so activating MLB needs a deploy and a fresh mandate either
way. That remains the owner's decision and is not self-authorized.
