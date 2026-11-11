# LANE_A canary, market 1 — `aec-itfwo-meiguo-junzha-2026-09-16`

Armed 2026-09-16T05:56:43Z, first order 05:57:37Z, first fill 05:58:44Z.
Service `l5-r1-probe` `6111a5d4-…`, deployment `4e7f6956-…`, commit `c50e1cb00f56`,
session `80fac513b7dd4aeb`, campaign `R1-INITIAL`, mode **ARMED**.

**P&L LABEL: FILLED, UNSETTLED.** Nothing here is realized. The position is
open, unmatched, and held to settlement. No settlement, no cash reconciliation.

## Pre-submission gate

`orphan_sweep` ran at 05:57:37.917Z, **before** the first submission:
`seen 0, cancelled [], foreign [], errors []`. Clean — no unknown or foreign
open orders, no cancellation failure. The preflight showed no regression
immediately before arming: 13 of 13 non-owner gates PASS.

## Candidate → decision

Rank 1 of 4 admitted from 25 universe rows. State
`p25-45c|tte>10m|flat|open|q<5`, `state_n 83`, `p_buy 0.9694`,
`v2_recommended open` at `p 1.0`. L5 target size p50 = 10 shares; executed
0.02 contracts ($0.0070) — logged as `size_divergence:
venue_minimum_vs_l5_target`, a declared safety override, not L5 behaviour.

## The complement-acceptance order — the correction, working live

```
cap_check  admits:true  already_spent:0  proposed_notional:0.003500
           cap:0.05  remaining_before:0.05  remaining_after:0.046500
           counter: spring_cumulative_notional (one market, all money-moving
                    BUY stages incl. complement acceptance)
order      CH6B48D1JVNK  YES @ 0.3500 x 0.01  post_only
venue      ORDER_STATE_NEW -> resting  (rest_s 1.656)
filled     0
cap_after  0.0000
```

The gate ran **before** the submission, against the unified counter, and named
it. The order did not fill, so `cap_after` returned to zero — the reconciliation
that returns an unfilled submission's budget worked on live venue data.

## Submitted → acknowledged → fills

Six orders, every one maker/post-only and touch-joining
(`touch_join_rate 1.0`, `live_taker_share 0.0`).

| stage | order id | px × qty | filled | terminal |
|---|---|---|---|---|
| spring_open_1 | `CH6B8MPJGVNG` | 0.35 × 0.02 | 0 | deadline, cancelled (31.9 s) |
| spring_open_2 | `CH6BA9K7MVNR` | 0.35 × 0.02 | 0 | deadline, cancelled (31.2 s) |
| **spring_open_3** | **`CH6BMHNS6VNM`** | **0.35 × 0.02** | **0.02** | **FILLED (9.985 s)** |
| spring_extend_4 | `CH6BTD9SEVNQ` | 0.35 × 0.02 | 0 | REJECTED_ZERO_FILL |
| spring_extend_5 | `CH6BNZK9WVNH` | 0.35 × 0.02 | 0 | REJECTED_ZERO_FILL |
| spring_extend_6 | `CH6BTHBRMVNA` | 0.35 × 0.02 | 0 | REJECTED_ZERO_FILL |

No partial fills. The single fill was complete at 0.02.

The three rejections carry `rejection_class: rejected_at_non_crossing_price;
authenticated_book_crossed`, margin 2.00 ticks. The gateway served 0.35/0.37
while the authenticated recorder book served **0.33/0.34**
(`disagrees_with_gateway: true`) — our 0.35 bid was above the real ask, so it
genuinely crossed and **the venue was right to reject**. This is the already
established REST-gateway-lag root cause; reported as a count, not re-derived,
and no retry change implemented.

## Inventory trajectory

```
flat  ->  OPEN x3 attempts, 1 fill  ->  up 0.02 @ 0.3500, dn 0,
                                        matched 0, residual 0.02,
                                        spent $0.0070, unmatched $0.0070
state p25-45c|tte>10m|flat  ->  p25-45c|tte>10m|imb10-50
          ->  EXTEND x3, all rejected  ->  inventory unchanged
```

## Complement behaviour

`complement_preflight` was computed on every OPEN: NO side, px 0.63,
opp_ask 0.65, `spring_pair_target 0.98`, `hard_bound_px 0.640`,
regime `spring_target`, `preview_ok true`.

**No COMPLETE was ever submitted.** Once the leg filled, the observed
`pair_cost` was **1.0000** (held 0.35 + complement 0.65) — at the hard ceiling
and above the 0.98 fee-safe target. The model ranked `extend 0.6732 /
complete 0.3268` and modal execution took EXTEND on all four post-fill
decisions. That is the patient-completion rule behaving as designed: it did
not pay through the fee-safe bound to flatten.

One divergence recorded, not acted on: decision 4's stochastic draw sampled
`complete` while modal acted `extend` — `model deficiency (argmax execution of
a stochastic policy)`.

## Fees / rebates

None observed. One fill, 0.02 × $0.35 = **$0.0070** notional. No commission,
rebate or reward row has appeared for this session. Incentives stay separate
and are never netted into trading P&L.

## Terminal state and venue reconciliation

```
venue_vs_ledger   venue_qty 0.0200   ledger_qty 0.02   delta 0.0000   agrees TRUE
provenance        CH6BMHNS6VNM  venue_cum 0.02  ORDER_STATE_FILLED
```

**Open item, and it meets one of the owner's stop triggers.** The end-of-session
reconciler reports `checked 3`, `contradictions 0`, but:

```
unreadable   [CH6BNZK9WVNH, CH6BTD9SEVNQ, CH6BTHBRMVNA]
still_open   [CH6BNZK9WVNH, CH6BTD9SEVNQ, CH6BTHBRMVNA]
flat_verified false   may_assert_flat false   open_orders_block_flat_claim true
```

Those are the three orders the venue already reported `ORDER_STATE_REJECTED`
with zero fill. `still_open` here is the reconciler's fail-closed default when
it cannot re-read an id — it is a read failure, not an observation that they
are resting. Against that: the boot sweep saw **zero** open orders account-wide,
and the account quantity agrees with our ledger exactly. The account is also
known to be returning HTTP 429 on reads right now, which is the most likely
cause. It is not resolved, and it is not being treated as resolved.

## Envelope

**$0.0070 spent of $0.05.** Acceptance probe contributed $0 (zero fill).
Remaining $0.0430. Every decision row carries its own `cap_check` naming the
unified counter. One market only: the campaign logged
`CAMPAIGN_HOLD:POSITION_HELD (1/1 markets held)` and
`selection_skipped_position_held: 1` — it will not select a second market.

## Campaign state

Not at its stop condition. A resumed session on the SAME market began
05:59:20Z to work the residual. Restart policy NEVER; no re-arm.

## First live fidelity evidence

7 scored decisions, 1 market, 4 post-fill. PASS: `action_distribution_side`,
`price_aggressiveness`, `maker_taker`. FAIL: `action_distribution_relation`
(l1_acted 0.3734 vs 0.20), `post_fill_complete_gap` (−32.7 pp vs 15.0),
`timing_cadence`, `size`, `inventory_trajectory`.

`feasibility: FEASIBLE_UNDER_CURRENT_SHELL`, with
`cap_fallback_attribution.verdict: ALL_CAP_FORCED` — the acted-vs-L5 gap is
attributed to the caps, not to model deficiency. At 0.02 contracts against an
L5 target of 10 shares, `median_executed_over_l5_p50 = 0.002`; a size FAIL is
arithmetically guaranteed at this rung and is not news.
