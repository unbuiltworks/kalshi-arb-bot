# L5-style sequential pairing — live experiment: configuration and authorization

**2026-10-26.** PMUS only; no Gemini figure is used. Nothing armed. No gate,
limit, capital or lock changed. Production is `b1a8d3bf` at `ff1f8ea3`,
executor **disarmed**, campaign **expired** and not restarted.

---

## Verdict first: the proposed limits cannot all hold at once

| | |
|---|---|
| **$0.80 opening cap vs PMUS minimum** | **No conflict.** PMUS minimum quantity is **0.01 contracts** and tick is $0.01, both read live from market metadata. $0.80 is 80–8,000× the minimum. |
| **$50 aggregate capital** | **Cannot be authorized by the deployed code.** `l5_campaign_approval.MAX_TOTAL_EXPOSURE_USD = 20` is a code constant and `state()` intersects, never widens. A $50 experiment needs a code change. |
| **$5/window** | needs **37.4 contracts** per completed pair ($32.37 both legs, **$21.42 first-leg risk**) — or 78.3 contracts for $5 *per window on average*. |
| **$10/window** | needs **74.7–156.6 contracts**, $64.73–$135.63 capital. Beyond both $50 and the code's $20. |
| **The binding constraint is not capital — it is the loss limit.** | One strand at the $5/window size loses **$21.42 = 2.1× the entire $10 cumulative budget**, and the experiment halts with **56.6%** probability inside 8 windows. |

**Recommendation: run the experiment to measure fills, not to earn $5/window.**
The fill rate is the one input every other number depends on, it has never
been observed, and it is cheap to measure. At any size that respects a $10
loss cap, expected profit is **$0.5–1.5 over 8 windows**. That is the honest
trade, and it is still worth doing.

---

## 1. The $0.80 opening-notional cap — already implemented and verified

Not new work: it is deployed, enforced, and tested.

- Applied at `l5_two_sided_probe.py:1150` via `opening_notional_budget_usd()`,
  clamped by the code constant `OPENING_NOTIONAL_MAX_USD = 5`.
- Env `L5_TS_OPENING_NOTIONAL_BUDGET_USD`, effective **0.80**, confirmed by the
  money path's own accessor in `L5_EFFECTIVE_RUNTIME_CONFIG` at boot.
- Second, independent bound: `opening_leg_max_contracts()` = **2** (env), code
  ceiling 3.
- **53 tests pass** (`test_l5_opening_notional_cap.py`,
  `test_l5_notional_cap_cannot_stack.py`).

### Does it conflict with PMUS minimum order size? No.

Read live from the venue's own market metadata, not assumed:

```
quantity_constraint_known  passed  detail "0.01"
tick_constraint_known      passed  detail "0.01"
```

The gate is real, not decorative: **6 `QUANTITY_BELOW_MIN_INCREMENT` refusals**
have fired in production. Minimum executable notional is `0.01 × price`, i.e.
**$0.0001–$0.0099**. The $0.80 cap is far above it, so nothing is bypassed.

**What the cap permits**, at measured prices:

| own price | contracts | first-leg $ | locked profit/pair | which binds |
|---|---|---|---|---|
| 0.20 | 2.00 | $0.40 | $0.2676 | contracts |
| 0.30 | 2.00 | $0.60 | $0.2676 | contracts |
| 0.45 | 1.78 | $0.80 | $0.2379 | notional |
| **0.5733** (mean) | **1.40** | **$0.80** | **$0.1867** | notional |
| 0.70 | 1.14 | $0.80 | $0.1529 | notional |
| 0.90 | 0.89 | $0.80 | $0.1189 | notional |

---

## 2. Windows where the engine opens one side and then completes favourably

**21 sequences across 39 PMUS windows; completion rate 47.7% (21/44).** This is
sequential inventory-building, not simultaneous arbitrage: leg 1 rests and
fills, then the complement is acquired later at its own price.

| window | leg 1 own | leg 2 own | pair | qty | locked |
|---|---|---|---|---|---|
| 10-25 1730z | 0.5400 | 0.4100 | 0.9500 | 1.48 | 0.0740 |
| 10-25 1730z | 0.5000 | 0.3200 | 0.8200 | 1.60 | 0.2880 |
| 10-25 1745z | 0.4900 | 0.4400 | 0.9300 | 1.63 | 0.1141 |
| 10-26 0930z | 0.7300 | 0.1900 | 0.9200 | 1.09 | 0.0872 |
| 10-26 1030z | 0.7100 | 0.1800 | 0.8900 | 1.12 | 0.1232 |
| 10-26 1045z | 0.2900 | 0.4500 | 0.7400 | 2.00 | 0.5200 |
| 10-26 1100z | 0.3100 | 0.5400 | 0.8500 | 2.00 | 0.3000 |
| 10-26 1115z | 0.5700 | 0.0900 | 0.6600 | 1.40 | 0.4760 |
| 10-26 1145z | 0.4800 | 0.0900 | 0.5700 | 1.66 | 0.7138 |
| 10-26 1200z | 0.7500 | 0.0800 | 0.8300 | 1.06 | 0.1802 |
| 10-26 1215z | 0.3000 | 0.6300 | 0.9300 | 2.00 | 0.1400 |
| 10-26 1230z | 0.7000 | 0.2300 | 0.9300 | 1.14 | 0.0798 |
| 10-26 1545z | 0.4300 | 0.5300 | 0.9600 | 1.86 | 0.0744 |
| 10-26 1600z | 0.4600 | 0.3900 | 0.8500 | 1.73 | 0.2595 |
| 10-26 1600z | 0.8300 | 0.0700 | 0.9000 | 0.96 | 0.0960 |
| 10-26 1615z | 0.6800 | 0.1300 | 0.8100 | 1.17 | 0.2223 |
| 10-26 1630z | 0.7800 | 0.1800 | 0.9600 | 1.02 | 0.0408 |
| 10-26 1645z | 0.5000 | 0.4000 | 0.9000 | 1.60 | 0.1600 |
| 10-26 1700z | 0.6800 | 0.2500 | 0.9300 | 1.17 | 0.0819 |
| 10-26 1730z | 0.7100 | 0.1900 | 0.9000 | 1.12 | 0.1120 |
| 10-26 1745z | 0.6000 | 0.3600 | 0.9600 | 1.33 | 0.0532 |

Pair cost: min 0.5700, median 0.9000, max 0.9600 — **all below the 0.96
ceiling**. The complement arrives **cheaper than leg 1 in 17 of 21** sequences,
which is the sequential effect the strategy is built on: the move that
threatens leg 1 is the move that makes leg 2 cheap.

**Every fill above is modelled.** `STRICT` queue model: the opposite touch must
pass strictly *through* our resting price.

---

## 3. What is actually measured, and what is not

| measurement | status |
|---|---|
| **venue-confirmed MAKER fills** | **0 — none exist in this programme** |
| venue-confirmed fills of any kind | 6 (5 opening legs + 1 complement, Stage 1) |
| **maker fill rate** | **UNMEASURED — this is what the experiment is for** |
| completion rate | 47.7% **modelled** (21/44), never venue-confirmed |
| observation latency | **MEASURED, real**: n=3,431, p50 **15.5 s**, p90 29.9 s, max 96.1 s; 22.7% under 5 s, 90.1% under 30 s |
| submit→ack latency | UNMEASURED — needs a live submission |
| partial fills | UNMEASURED — no resting order has ever existed |
| fees | schedule verified (99/99 published rows); **maker pays no fee** |
| maker rebate credited | $0.42 historical on 25 prints; **$0.00 at our clip size** (per-print cent floor) |
| residual inventory | modelled only; 23 strands, settlement `TERMINAL_BOOK_INFERRED` |
| settlement cashflows | **0 venue settlements** for any modelled window |

**This is the case for the experiment.** No amount of further replay can
produce a venue-confirmed maker fill, and the completion rate — on which every
economic number here depends — is currently an assumption.

A caveat on the latency data: **p10 is −4.9 s**, i.e. some L5 events are
stamped *after* our read of them. 31 such rows remain unexplained; they do not
affect the p50/p90 but they mean the latency distribution's left tail is not
trustworthy.

---

## 4. Position size and capital for $5 and $10 net per window

Measured inputs (39 PMUS windows, both corpora, `STRICT`): mean completed pair
cost **0.8662**, locked profit **$0.1338/contract**, mean first-leg own cost
**0.5733**, completion **47.7%**.

| target | basis | contracts | capital (both legs) | first-leg risk |
|---|---|---|---|---|
| **$5** | per completed pair | **37.37** | **$32.37** | **$21.42** |
| $5 | per window average | 78.29 | $67.82 | $44.89 |
| **$10** | per completed pair | **74.73** | **$64.73** | $42.84 |
| $10 | per window average | 156.58 | $135.63 | $89.77 |

**Locked paired profit vs directional exposure, kept separate:** the locked
figure is `qty × (1 − pair)` and is certain once both legs fill. The first-leg
risk column is **not** a loss — it is the notional of a residual that settles 0
or 1. PMUS books measure **fair-priced** (bias +0.0002/contract over 4,950
observations), so residual EV is ~0 with large variance. It is variance, not
negative expectancy — but variance is what trips a loss limit.

### Why the $10 cumulative loss limit is the real ceiling

Monte Carlo, 20,000 trials, 8 windows, completion 47.7%, strands settling 0/1
at their own cost, with the $5/day and $10/cumulative halts live:

| contracts | first-leg $ | **P(halt)** | mean net | p5 net | windows run |
|---|---|---|---|---|---|
| 1.00 | $0.57 | **0.0%** | $0.51 | −$1.17 | 8.0 of 8 |
| **1.40** (the $0.80 cap) | $0.80 | **~0.2%** | ~$0.71 | ~−$1.6 | 8.0 of 8 |
| 2.00 | $1.15 | 0.5% | $1.03 | −$2.34 | 8.0 of 8 |
| **3.00** (code max) | $1.72 | **4.3%** | **$1.53** | −$4.40 | 7.9 of 8 |
| 4.00 | $2.29 | 9.4% | $1.99 | −$5.81 | 7.7 of 8 |
| 8.00 | $4.59 | 25.6% | $3.63 | −$9.17 | 7.0 of 8 |
| 20.00 | $11.47 | 50.3% | $6.82 | −$11.72 | 5.2 of 8 |
| **37.40** ($5/pair) | $21.42 | **56.6%** | $11.85 | −$21.44 | **4.8 of 8** |

At the $5/window size the experiment halts more often than it finishes, and
returns a 5-window sample instead of an 8-window one. **Raising capital does
not fix this; only raising the loss tolerance would, and that is the owner's
call, not mine to assume.**

---

## 5. Hard code ceilings that bound any authorization

These are **code constants**, not environment variables. `state()` intersects
and never widens, so no document or variable can exceed them:

| constant | value | file |
|---|---|---|
| `MAX_TOTAL_EXPOSURE_USD` | **$20** | `l5_campaign_approval.py:49` |
| `MAX_DAILY_LOSS_USD` | $5 | `l5_campaign_approval.py:54` |
| `MAX_WINDOWS` | 8 | `l5_campaign_approval.py:63` |
| `MAX_CAMPAIGN_S` | 4 h | `l5_campaign_approval.py:66` |
| `ALLOWED_SERIES` | `cpc-btc-updown-15m-` | `l5_campaign_approval.py:69` |
| `OPENING_NOTIONAL_MAX_USD` | **$5** | `l5_two_sided_probe.py:208` |
| `OPENING_LEG_MAX_CONTRACTS` | **3** | `l5_two_sided_probe.py:164` |

**So the requested $50 aggregate capital cannot be authorized as-is** — $20 is
the code's ceiling. And the largest opening leg the code permits is
`min(3 contracts, $5 notional)`.

**A convergence worth noting:** 3 contracts is both the code's hard maximum and
the largest size with a <5% halt probability under the $10 loss cap. The
existing ceilings already sit at the risk-defensible size, so **no code change
is needed to run the strongest experiment the loss limit allows.**

---

## 6. Safety gates verified, on the running deployment

| gate | state |
|---|---|
| deployment | `b1a8d3bf` **SUCCESS** at `ff1f8ea3` |
| executor armed | **false**; submission gate `blocked: true, reason: NOT_ARMED` |
| halt latch | **CLEAR** |
| independent watchdog | `BOTH_DOCUMENTS_FRESH` from `l5-halt-watchdog.railway.internal:8080` |
| durable reconciliation | ingest cursor `resumed: true`, `durable: true`; auth ledger `durable: true` |
| account-wide exposure | `ok: true` |
| account-wide realized | **−$13.6405** all-time; **day realized $0** for 2026-10-26 |
| available cash | **$339.16685**, `read_ok: true`, authoritative `/v1/account/balances` |
| existing positions | up_qty 0, dn_qty 0 on the live window; no unexpected orders |
| venue eligibility | series `cpc-btc-updown-15m-` allow-listed; tick 0.01, min qty 0.01 confirmed |
| maker-only | `EXECUTION_MODE` forbids defensive crossing — verified true |
| defensive completion | **disabled** (`L5_DEFENSIVE_COMPLETION=0`) |
| 0.99 maker gate | **not set**, and its code is **not in this lineage** |
| first-leg floor | **0.20** (unchanged) |
| pair ceiling | **0.96** (unchanged) |
| max live orders | **1** |
| worst-case deficit | $0.90 |
| budget selfcheck | `all_passed: true` |
| residual-side selfcheck | `PASS`, 7/7 |
| campaign | **expired** (`approval_stale`), 4/4 windows spent, **not restarted** |
| authorization deadline | **EXPIRED** (−4394 s) |

Two things that will fail preflight until a fresh authorization is issued, by
design: `absolute_deadline` and `campaign_approval`. Everything else PASSes.

---

## 7. Exact authorization needed to activate

**Nothing below is applied.** This is the scope for the owner to approve.

### A. What I need the owner to decide first

1. **Capital.** $50 exceeds the code's `MAX_TOTAL_EXPOSURE_USD = $20`. Either
   cap the experiment at **$20** (no code change) or authorize a reviewed
   change to that constant.
2. **Size vs loss limit.** At the $0.80 cap the experiment earns ~**$0.19 per
   completed pair** and ~$0.71 over 8 windows. $5/window needs 37 contracts and
   a loss tolerance of ~$25/strand. **Which do you want:** the measurement
   (keep $0.80, or go to the code max of 3 contracts / $5 notional), or the
   profit target (which needs the $10 cumulative limit raised to roughly $50)?

### B. Configuration, if run at the code maximum within the stated loss limits

| variable | value | note |
|---|---|---|
| `L5_TS_OPENING_NOTIONAL_BUDGET_USD` | `0.80` → optionally `5` | $5 is the code ceiling; 3 contracts is the risk-defensible max |
| `L5_TS_OPENING_LEG_MAX_CONTRACTS` | `2` → optionally `3` | code ceiling 3 |
| `L5_MAX_TOTAL_EXPOSURE_USD` | `20` | code ceiling; **not $50** |
| `L5_MAX_CASH_DEPLOYED_USD` | `20` | fee-inclusive; re-derive the worst case before applying |
| `L5_MAX_L5_DAILY_LOSS_USD` | `5` | as proposed |
| `L5_MAX_CUMULATIVE_LOSS_USD` | `10` | as proposed |
| `L5_MAX_WORST_CASE_DEFICIT_USD` | re-derive | 0.90 was sized for $4.64; it must be recomputed for a larger cap |
| `L5_LIVE_AUTH_MAX_WINDOWS` | `8` | code ceiling 8 |
| `L5_LIVE_AUTH_MAX_OPEN` / `_COMPLETE` / `_REPLACE` | re-derive | currently 6/9/12 for a 4-window campaign |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | re-derive | currently 21 |
| `L5_LIVE_AUTH_NOT_AFTER` | fresh, ≤ 4 h | currently EXPIRED |
| `L5_CAMPAIGN_APPROVAL` | **fresh document** | new nonce ≥8 chars, series `cpc-btc-updown-15m-`, `max_total_exposure_usd ≤ 20`, `max_daily_loss_usd ≤ 5`, `max_windows ≤ 8` |
| `PMUS_Q1_LIVE` / `PMUS_Q1_ARM` | `YES` / `=PMUS_Q1_SESSION_ID` | the arming handshake |

**Unchanged and not up for negotiation in this experiment:**
`L5_TS_MIN_FIRST_LEG_PRICE=0.20`, pair ceiling `0.96`, `L5_MAX_LIVE_ORDERS=1`,
`L5_DEFENSIVE_COMPLETION=0`, `L5_TS_COMPLETION_CROSS` disabled,
`L5_MAKER_PAIR_GATE` unset, maker-only execution, the independent watchdog,
exact-order cancellation, durable reconciliation, and the account-wide loss
gates. **No safety lock is removed and no account-wide gate is overridden.**

### C. What the experiment will and will not establish

**Will:** the first venue-confirmed maker fills, a real maker fill rate, real
submit→ack latency, real partial-fill behaviour, real fees charged, real
settlement cashflows, and completed-pair economics separated from residual
inventory — each compared action-by-action against L5's own decisions.

**Will not:** $5–10/window. At any size respecting a $10 cumulative loss cap
the expected net is **$0.5–1.5 over 8 windows**, and the sample is 8 windows,
so even a positive result will not have a significant per-window CI. It is a
**measurement** experiment, and the measurement is the thing currently blocking
every other decision.

### D. Scaling arithmetic toward $480/day, stated plainly

At the measured $0.1338 locked per contract and 47.7% completion, $480/day
needs ~**7,500 contracts/day** of completed pairs — roughly **75 contracts per
window across 96 windows**, i.e. ~$65 of capital per window turned over
continuously, with residual variance scaled 50× above anything yet authorized.
That is three orders of magnitude from this experiment, and the first step is
not size: it is confirming the fill rate is real.
