# L5 — campaign #2 authorization package

Commit of record **`1752e51b`** · deploy **`21d2f2b7`**.
**Shadow lock engaged. Not armed. No live order submitted.**

---

## 1. Why the test reports $0.8231 against a $0.80 cap

**It is not a cap breach. They are two different quantities, and both are
intended.**

```
$0.7931  =  1.03 contracts x 0.77 own price      <- what the CAP bounds
$0.0300  =  the deficit gate's conservative fee
$0.8231  =  the fee-inclusive executed cost      <- what the DEFICIT gate charges
```

### The precise invariants

**Invariant A — the opening-notional cap**, enforced in `build_orders`:

```
qty x dearer_leg_own_price  <=  L5_TS_OPENING_NOTIONAL_BUDGET_USD   ($0.80)
```

Pure notional. No fee term. Bounds **one opening plan**, on the dearer of the
two legs — the one whose solo fill is the strand.

**Invariant B — the worst-case deficit**, enforced in `ValidationGates.refuse`:

```
C - min(up_qty, dn_qty)  <=  L5_MAX_WORST_CASE_DEFICIT_USD   ($2.00)

where C = sum of executed cost
        + qty x price + fee_worst(qty, price) for the proposed order
        + the worst-filling subset of outstanding orders
```

Fee-inclusive, maximised over four corners of which outstanding legs fill.
Bounds **the whole book per market**, cumulatively.

`fee_worst` = `theta_TAKER x C x p x (1-p)` + `$0.01` per order, rounded **up**
to the cent: `0.0695 x 1.03 x 0.77 x 0.23 = 0.01268`, `+0.01`, `-> $0.03`.
It uses the **taker** theta and **never nets the maker rebate** — "a credit we
are not guaranteed, applied to a worst case, is not a worst case."

### Why notional is the right thing for the cap to bound

A stranded maker leg loses its notional, exactly. 0130z settled at
**−$1.1594 = 1.87 x 0.62**, and every leg of campaign #1 carried
`commission: 0.0000`. So for a maker fill at zero commission **notional *is*
the realized loss**, which is precisely the quantity the cap was chosen to
bound. Invariant B is deliberately stricter than reality; invariant A matches it.

### If you would rather the cap were fee-inclusive

One line, and it would shrink clips for no realized benefit at today's fees:

| dearer own px | notional-cap qty | fee-inclusive qty | clip retained |
|---|---|---|---|
| 0.77 | 1.03 | 1.00 | 97.1% |
| 0.62 | 1.29 | 1.23 | 95.3% |
| 0.55 | 1.45 | 1.37 | 94.5% |
| 0.35 | 2.28 | 2.10 | 92.1% |

**My recommendation: leave it as notional.** Maker commission is zero on the
published schedule and was zero on all 15 fills of campaign #1, so the
fee-inclusive version would cut 3–8% of clip to protect against a cost that
does not occur under maker-only execution. Invariant B already carries the
conservative fee. Say the word if you want it changed anyway.

Pinned by tests: `tests/test_l5_notional_cap_cannot_stack.py` (12 tests).

## 2. Next eight eligible windows — computed from current UTC

**Current UTC `2026-10-25T12:25:32Z`.** The 12:15z window is **not** enterable:
its 600s entry cutoff passed 32s ago and entry needs 300s of runway.

| # | window | opens | entry cutoff | closes / settles |
|---|---|---|---|---|
| 1 | `1230z` | 12:30Z | 12:40Z | 12:45Z |
| 2 | `1245z` | 12:45Z | 12:55Z | 13:00Z |
| 3 | `1300z` | 13:00Z | 13:10Z | 13:15Z |
| 4 | `1315z` | 13:15Z | 13:25Z | 13:30Z |
| 5 | `1330z` | 13:30Z | 13:40Z | 13:45Z |
| 6 | `1345z` | 13:45Z | 13:55Z | 14:00Z |
| 7 | `1400z` | 14:00Z | 14:10Z | 14:15Z |
| 8 | `1415z` | 14:15Z | 14:25Z | **14:30Z** |

- campaign start **12:30:00Z**, eighth window closes **14:30:00Z**
- deadline **15:00:00Z** (+30 min settlement slack)
- `issued + 14400s` ceiling is 16:25:32Z, so **the deadline binds**, as intended

**Two timing constraints on arming:**

1. The payload goes **stale at 13:25:32Z** (3600s after issue). After that I
   must re-mint.
2. To fit all eight windows before the 15:00Z deadline the first window must
   open no later than 13:00Z, so **arm by 13:00Z** or the deadline truncates
   the campaign to fewer than eight windows. (`max_windows` counts windows
   *traded*, so a quiet window costs nothing, but wall-clock still runs.)

Practical arming window: **now → 13:00Z.**

## 3. Minted authorization payload — validated

`ok: True` · `state: approved` · `clamped: all false` (every value at or below
its code ceiling).

| field | value |
|---|---|
| nonce | `962968d3f68c832741d043a8` |
| issued_utc | `2026-10-25T12:25:32Z` |
| expires_utc | `2026-10-25T15:00:00Z` |
| max_windows | 8 |
| max_total_exposure_usd | `5` |
| max_daily_loss_usd | `5` |
| series | `cpc-btc-updown-15m-` |
| **document sha256** | `ce6db22ec6b078fc065ab1955106bfc15b1e16a42a86098e592ccc060637163b` |
| **stale after** | **13:25:32Z** |

This is a **new nonce and a new document**. The previous approval
(sha `74ab4250…`) is not reused; it fails as `approval_stale` and reserves
nothing.

## 4. The five variable changes, in order

| # | variable | value |
|---|---|---|
| 1 | `L5_EXPERIMENT_ID` | `l5-btc15m-notional-20261025-3b484e74` |
| 2 | `L5_TS_EXPERIMENT_ID` | `l5-btc15m-notional-20261025-3b484e74` |
| 3 | `L5_CAMPAIGN_APPROVAL` | `{"approved":true,"expires_utc":"2026-10-25T15:00:00Z","issued_utc":"2026-10-25T12:25:32Z","max_daily_loss_usd":"5","max_total_exposure_usd":"5","max_windows":8,"nonce":"962968d3f68c832741d043a8","series":"cpc-btc-updown-15m-"}` |
| 4 | `L5_LIVE_AUTH_NOT_AFTER` | `2026-10-25T15:00:00Z` |
| 5 | `L5_SHADOW_ONLY` | `0` — **last**, only after 1–4 verify |

**I will not write #3 or #4.** Shadow stays engaged until you set #5.

## 5. Risk envelope — unchanged, all verified in force

| control | value | source |
|---|---|---|
| opening-notional cap | **$0.80** | verifier row, effective `0.80` |
| worst-case deficit | **$2.00** | per order, fee-inclusive |
| total exposure | **$5** | variable and boot-frozen agree |
| campaign loss limit | **$5.00** | independent of the lifetime gate |
| window maximum | **8** | code ceiling 8, approval 8, env 8 |
| execution | **maker-only** | `maker_only_forbids_defensive_crossing: true` |
| pair ceiling | **0.96** | — |
| submission budgets | OPEN 16 / COMPLETE 24 / REPLACE 32 / backstop 56 | fresh on the new experiment id (0/16, 0/24, 0/32) |
| watchdog | independent, ~20s sweep | `cancel_enabled: true`, fresh heartbeat |
| daily realized-loss halt | $5 | — |
| lifetime headroom | $8.5043 | realized −$11.4957 vs $20 |

Two allocation rounds at $0.80 = **$1.60** worst-case opening exposure, inside
the $2.00 deficit bound.

## 6. What I will measure once authorized

| metric | source |
|---|---|
| **completed pairs per window** | `L5_LIVE_SETTLED` `matched_qty` / `matched_pnl` per slug |
| **stranded inventory** | `residual_qty`, `residual_side`, `residual_pnl`, and the realized loss per stranded leg |
| **total settled net P&L** | sum of `settlement_pnl`, fee-inclusive, labelled settlement-only, reconciled against the `realized_today` delta |
| **opportunities rejected per gate** | tally of `L5_LIVE_DECISION.key` prefixes — `TWO_SIDED_PROBE:*`, `SUBMISSION_CLAIM:*`, `VALIDATION_GATES:*`, `WORST_CASE_DEFICIT:*`, `OPENING_NOTIONAL_CAP_BELOW_VENUE_MINIMUM`, `PRESEND_CROSS:*` — so the cap's own refusals are counted separately from the pre-existing gates |
| cap effect | how often `sized_by == OPENING_NOTIONAL_CAP`, and the clip it produced vs what the other bounds would have allowed |

No bound moves to produce more trades. Frequency comes from the two allocation
rounds and the REPLACE budget (5/32 used last campaign), not from larger clips.

Not implemented, not requested: taker completion, any sell path, maximum
opening price, dynamic order mirroring, larger submission budgets, capital
increases, new services. Legacy test backlog untouched.
