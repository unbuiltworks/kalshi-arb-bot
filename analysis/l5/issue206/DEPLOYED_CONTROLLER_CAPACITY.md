# The deployed controller on real books — what limits our pair count

Measured against deployment **`18deb02d`** (commit
`a2f6537517bdbd04e991015a86fa4c727dfe3dff`, branch
`deploy/l5-disarmed-a2f653751-20261107`, SUCCESS 2026-11-07T23:00:22Z), at
the exact configuration its heartbeat reports.

**Nothing was deployed, armed, merged or configured by this work.**

## 1. Deployment, watchdog, heartbeat — all verified against the manifest

Heartbeat 2026-11-07T23:25:36Z:

| manifest check | required | observed | |
|---|---|---|---|
| V2 cannot submit | blocked / shadow_lock / armed | **true / true / false**, `halt_local_env: true` | ✅ |
| V3 authorization blocked | three failures | `ok: false`, fail = **shadow_lock, absolute_deadline, campaign_approval** | ✅ |
| V5 orders | 0 live, 0 unresolved | **0 / 0** | ✅ |
| V7a cumulative | −15.61090000 | **−15.61090000** | ✅ |
| V7b day-scoped | −0.9000 on 2026-11-07 | **−0.9000**, room 4.1000 | ✅ |
| V7c verified L5 | −3.410300 | **−3.410300** | ✅ |
| defensive budget | never reset | **0.49916626650000** | ✅ |

**V4 deviates from the manifest, and the deviation is the blocker being
closed.** The manifest expected `max_worst_case_deficit_usd: null`. It now
reads **`1.00`** with `worst_case_deficit_check: ACTIVE` — the owner set
`L5_MAX_WORST_CASE_DEFICIT_USD`. That is a **strictly tighter** change: the
bound only ever refuses, and the three submission locks are untouched. The
Stage 1 configuration blocker is therefore **closed in production**.

**Independent watchdog:** service `fb038b8c`, deployment `5b1a90c0`,
SUCCESS, carrying the bounded-retry fix for the rate-limited open-orders
read. Separate service, separate deployment — unchanged by this work.

**Full deployed configuration, from the heartbeat:**

```
ValidationGates  max_cash_deployed_usd 12   max_worst_case_deficit_usd 1.00
                 max_unmatched_contracts 5  cash_policy NO_RECYCLING
SafetyLimits     max_usd_per_market 20      max_abs_residual 10
                 max_total_exposure_usd 1.00   max_concurrent_markets 1
                 max_live_orders 2          daily_realized_loss_halt_usd 5
```

## 2–4. Two-sided quoting, completion, replenishment — on recorded books

`analysis/l5/issue206/replay_executor_on_books.py` feeds **recorded ladder
frames** into the real `FaithfulExecutor.handle` at the configuration above.
13 out-of-sample windows with ≥10 frames.

| | |
|---|---|
| orders sent | **29** |
| **UP / DOWN orders** | **14 / 15** |
| **pairs completed** | **12** |
| residual carried | **5** contracts |
| orders that became pair halves | **24 of 29 (83%)** |

**Two-sided quoting works** and the 14/15 split is near-perfectly balanced —
the same signature as L5's own 49.8/50.2 across 62,020 trades.
**Replenishment works**: 12 pairs across 13 windows is not one-and-stop.
**Unmatched legs are preserved and completed**: only 5 contracts of residual
against 12 completed pairs.

**THESE ARE MODELLED FILLS ON REAL PRICES.** The harness transport
acknowledges and fills synthetically. No figure above is a venue fill and
none is realized money.

## 5. The biggest remaining reason — and it reverses my earlier answer

**Refusal histogram over ~800 ticks at the deployed configuration:**

| refusal | count |
|---|---|
| **`PAIR_COST_ABOVE_CEILING`** | **391** |
| `FIRST_LEG_ABOVE_MAX_PRICE` | 173 |
| `PRICE_OUT_OF_BAND` | 38 |
| `FIRST_LEG_BELOW_MIN_PRICE` | 33 |
| SUBMITTED | 29 |
| `COMPLEMENT_DEPTH_INSUFFICIENT` | 1 |
| **every capital / inventory refusal** | **0** |

**The capital limits never bind.** `CASH_COMMITMENT`,
`MAX_TOTAL_EXPOSURE`, `MAX_USD_PER_MARKET`, `MAX_ABS_RESIDUAL`,
`WORST_CASE_DEFICIT` and `DAILY_REALIZED_LOSS_HALT` each appear **zero**
times. Running the same 13 windows at **12× the exposure bound and 4× the
live-order cap gives the identical 29 orders, 12 pairs and 5 residual.**

**This reverses what I previously reported.** On the static-book harness the
cash gate bound and more capital bought more pairs, and I reported capital as
the constraint. On real books it is not: **the book simply does not offer a
pair at ≤0.96 most of the time**, which matches L5's own distribution — its
median pair basis is 0.9644 and only 51% of its windows sit at or below our
ceiling.

### Two measurement artifacts I had to discard first

1. **A static book is not evidence about a limit.** On the fixed 0.60/0.61
   fixture, `PAIR_COST_ABOVE_CEILING` fired in 21 of 24 ticks — a property
   of a book that never moves, not of the engine.
2. **A shared claim ledger invents a constraint.** My first book replay used
   one `L5_LIVE_AUTH_LEDGER` across all 13 windows, so claims leaked, every
   round read as spent, and `ACCUMULATION_EXHAUSTED` fired **375** times. I
   had begun writing that up as "the round budget is the binding
   constraint". With per-window isolation it appears **zero** times. The
   regression test pins the isolation so the artifact cannot return.

## 6. Demonstrated defects — none in the gates

The gates behave correctly: `risk_by_market` is supplied in production from
`account_wide_risk_by_market`, which prices worst-case loss under both
settlements, so a matched pair correctly contributes ~0 to
`max_total_exposure_usd`. There is no cash/risk conflation in that bound.

**No code defect is demonstrated by this measurement.** What it demonstrates
is that the binding constraint is the **market**, not our configuration.
11 regression tests added (`tests/test_l5_book_replay_capacity.py`),
including the two artifact guards.

## 7. Safety controls, confirmed

| control | state |
|---|---|
| `max_worst_case_deficit_usd` | **1.00, ACTIVE** — was the Stage 1 blocker, now set |
| `max_total_exposure_usd` | 1.00 |
| `max_cash_deployed_usd` | 12, `NO_RECYCLING` |
| daily realized-loss halt | 5, room 4.1000, `would_halt false` |
| independent watchdog | separate service, SUCCESS |
| three submission locks | all asserted |
| defensive budget | 0.49916626650000, never reset |
| taker crossing | both switches off |

Two distinct $1 bounds now exist on different quantities:
`max_worst_case_deficit_usd` bounds `C − min(up, dn)`;
`max_total_exposure_usd` bounds account-wide worst-case exposure. Neither is
the other's default and the smaller binds.

## 8. The numbers, kept apart

| | value | kind |
|---|---|---|
| **venue-confirmed maker fills** | **20 of 34 orders**, 8 of 11 rounds paired | **real, ours** |
| modelled fills on real prices | 29 orders, 12 pairs, 5 residual | **simulation** |
| **confirmed maker rebates** | **+$2.1300** over 293 fills | **real, credited** |
| rebates in the Stage 1 windows | **$0** | real |
| rewards accrued, not credited | $1.48 (PENDING/SKIPPED) | **not income** |
| non-trading `remediation` credit | $10.00 | **balance, not performance** |
| **settled realized, account-wide** | **−$15.61090000** | **real** |
| settled realized, day (2026-11-07) | −$0.9000 | real |
| settled realized, verified L5 | −$3.410300 | real |
| rebate-adjusted account-wide | −$13.4809 | real, bridge applied |

## Blockers to live validation

1. **The residual sign**, unchanged: negative at ~2 points of win rate on
   1.24M of L5's contracts, 51% of windows negative, wins recovering 82.9%
   of losses.
2. **−$1.27 of the realized bridge is unreconciled**, with eight
   2026-10-14/15 markets of unattested basis behind
   `UNPROVEN_LEGACY_ATTRIBUTION`. Closing it needs a read-only authenticated
   venue read — **no trading authorization required**.
3. **The pair ceiling is now known to be the operative constraint**, and the
   evidence says loosening it gains ~9% expected value — inside the noise of
   408 windows of someone else's fills. It is `CLAUDE.md`-locked and I did
   not change it.
