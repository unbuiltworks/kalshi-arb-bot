# L5 — eight-window campaign #2: pre-arm verification and authorization package

Commit of record **`1752e51b`** · deploy **`21d2f2b7`** (SUCCESS 12:01:30Z).
**Shadow lock engaged. Not armed. No live order has been submitted.**

---

## 1. Can multiple $0.80 openings bypass the $2 deficit limit?

**No. The gate refuses the third same-side opening and the second fully-resting
pair.** Proven against the real gate, not asserted — `tests/test_l5_notional_cap_cannot_stack.py`, 9 tests.

**The honest framing first.** The notional cap is **per plan**. `build_orders`
sizes one opening pair and knows nothing about inventory already held, so the
cap alone does **not** bound a sequence: two rounds of $0.80 are $1.60 and each
round satisfies its own cap separately. What stops a third is the worst-case
deficit gate. So the answer depends entirely on that gate counting everything,
which is what I verified.

### Same-side accumulation (the stacking case)

| round | held DOWN | executed cost | verdict |
|---|---|---|---|
| 1 | 0 | 0 | ALLOW (deficit 0.8231) |
| 2 | 1.03 | $0.8231 | ALLOW (deficit 1.6462) |
| **3** | **2.06** | **$1.6462** | **REFUSE** `WORST_CASE_DEFICIT:2.4693>2.00:none` |

Maximum that can accumulate: **$1.6462**, inside $2.00.

### Resting pairs, adversarial

| pairs resting | verdict |
|---|---|
| 1 | ALLOW |
| **2** | **REFUSE** `WORST_CASE_DEFICIT:2.5493>2.00:dn_only` |

The binding corner is `dn_only` — the gate maximises over *which* outstanding
legs fill rather than assuming they all do or none do.

### All four inventory states the owner named

| state | how it is counted | verified |
|---|---|---|
| **filled** | `up_qty/dn_qty/up_cost/dn_cost`, cumulative across rounds | yes — scenario above |
| **resting** | `outstanding_for_gates()` via each order's `remaining` | yes — `dn_only` corner binds |
| **pending / unacked** | the store records the order **before the wire**, so it is already in `live_orders()`; an unreadable price is charged at **$1/share** | yes — two unreadable legs refuse |
| **partially filled** | executed part in inventory at cost, unfilled remainder in `outstanding` — neither dropped nor double counted | yes — 0.50 filled + 0.53 resting |

A completing order is **never** blocked by the deficit (verified): it reduces
the deficit, and blocking it is what stranded 0130z.

**One correction to my own method.** I first tested `worst_case_deficit()` and
read its `ok: True` as "within bound". It is not — `ok` means the state was
*readable*; the bound is enforced in `refuse()`. Testing the former would have
reported a nonexistent hole. Every figure above goes through `refuse()`.

## 2. Effective risk settings, from the running process

`L5_EFFECTIVE_RUNTIME_CONFIG` at 12:01:33Z — **`PASS: true`, `checked: 26`,
`failed: []`**. Every value is what the money path's own accessor returns.

| control | effective |
|---|---|
| **opening notional cap** | **$0.80** per opening plan, on the dearer leg |
| worst-case deficit, per order | **$2.00** |
| instantaneous aggregate exposure | **$5** (variable and boot-frozen agree) |
| campaign additional-loss bound | **$5.00** |
| daily realized-loss halt | **$5** |
| account lifetime loss ceiling | $20 — realized −$11.4957, **headroom $8.5043** |
| maker pair ceiling | **0.96** |
| execution mode | **maker-only**; `maker_only_forbids_defensive_crossing: true`, `defensive_execution_enabled: false` |
| max live orders | 1 (plus the two-order pair) |
| opening leg contract cap | 2 contracts |
| round allocations | 2 |
| min first-leg own price | 0.20 |
| order TTL / entry cutoff / complete cutoff | 120s / 600s / 720s |
| residual ceiling | k=2, absolute 3 contracts, **ABSOLUTE binds** |
| mandate signing key in runtime | **absent** (`true`) |
| shadow lock | **engaged** |

Two allocation rounds at $0.80 is **$1.60** of worst-case opening exposure —
inside the $2.00 deficit bound and far inside the $5 exposure ceiling.

## 3. Authorization preflight — exactly three blockers, all yours

`L5_AUTHORIZATION_PREFLIGHT` at 12:01:33Z, `ok: false`:

| check | verdict | detail |
|---|---|---|
| `shadow_lock` | **FAIL** | `engaged: true` → blocks `SHADOW_ONLY_LOCK` (step 9) |
| `absolute_deadline` | **FAIL** | `EXPIRED`, `expires_in_s: -28893.5` |
| `campaign_approval` | **FAIL** | `approval_stale`, old sha `74ab4250…`, `reserved_nothing: true` |
| `owner_arm_token` | PASS | present |
| `halt_gate` | PASS | `permitted: true` |
| `window_budget_env` | PASS | `max_windows_env: 8` |
| `residual_ceiling` | PASS | effective 3 contracts |
| `claim_ttl_s` | PASS | 900s (default) |
| `market_series`, `role_binding`, `session_id` | PASS | — |

Nothing else blocks. **The expired approval is not reused** — it fails as
`approval_stale` and reserves nothing.

## 4. Authorization counters

| experiment | OPEN | COMPLETE | REPLACE |
|---|---|---|---|
| `l5-ts-nokey-20261025T0000Z` (current, **spent**) | **16/16** | 14/24 | 5/32 |
| `l5-btc15m-notional-20261025-3b484e74` (**new**) | **0/16** | **0/24** | **0/32** |

Mechanism, verified: `class_spent()` globs
`l5_auth_claim_cls1.<experiment>.<KLASS>.*`, so an unused experiment id matches
no lock file and every class starts at zero. The window ledger is keyed on the
approval **nonce**, so a fresh nonce starts its own window count.

## 5. Account reconciliation

`reconciled: true` · `uncertainty_cleared: true` · `authority: VENUE` ·
`ALERT: null` · `open_orders_at_venue: 0` · `pending_terminal_total: 0` ·
`live_orders_pending_terminal: []` · `realized_today: −11.49570000`.

Outstanding and **unchanged** — not introduced by this work, not repaired here:

- `gross_detail_lost` on two **legacy** markets (2026-10-12-0030z: ours DOWN 2
  vs venue 0; 2026-10-14-2230z: ours UP 2.9 vs venue 0), both
  `stranded_without_stamp`.
- `leg_cost_basis_unknown`: **48 legs** — issue **A1**, still **OPEN**.

## 6. Independent watchdog

`l5-halt-watchdog` (`fb038b8c`, deploy `642a2fbb`), separate service, sweeping
every ~20s. At 12:06:51Z: `cancel_enabled: true`, `max_order_age_s: 300.0`,
`open_orders: 0`, `overdue: []`, `unverified: []`, `still_open: []`,
`read_error: null`, `halt: false`, heartbeat fresh. The engine's own halt gate
is read **first**, before translation or any other gate, and refuses on absent,
stale or unreachable.

## 7. Automatic disarm — every path that stops the campaign without me

1. **Window budget**: `max_windows: 8` → `halt: true`, `armed: false`,
   `submission_gate.reason: NOT_ARMED`. **Demonstrated** — campaign #1 stopped
   itself exactly this way at 02:30Z.
2. **Approval expiry**: `expires_utc` → `approval_expired`.
3. **Approval staleness**: issued more than **3600s** ago → `approval_stale`,
   regardless of expiry.
4. **Campaign duration ceiling**: `eff_expires = min(expires, issued + 14400s)`.
5. **Independent deadline**: `L5_LIVE_AUTH_NOT_AFTER` →
   `AUTHORIZATION_EXPIRED` at step 26, evaluated separately from the approval.
6. **Submission budgets**: OPEN 16, COMPLETE 24, REPLACE 32, backstop 56 →
   `*_BUDGET_EXHAUSTED`. Claims are never returned.
7. **Four loss ceilings**: lifetime $20, L5 cumulative $20, L5 daily $5,
   campaign additional $5.00 — each counting unrealized loss too.
8. **Per-order**: $2.00 deficit, $5 exposure, 0.96 pair ceiling.
9. **Watchdog halt**, asserted independently.
10. **Shadow lock**.
11. A money-moving session **fails closed and does not relaunch itself** after a
    crash or completion.

## 8. Proposed campaign window

Worked example for a **13:00Z** start. Eight BTC15m windows:

| # | window | settles |
|---|---|---|
| 1 | 1300z | 13:15Z |
| 2 | 1315z | 13:30Z |
| 3 | 1330z | 13:45Z |
| 4 | 1345z | 14:00Z |
| 5 | 1400z | 14:15Z |
| 6 | 1415z | 14:30Z |
| 7 | 1430z | 14:45Z |
| 8 | 1445z | **15:00Z** |

- campaign start **13:00:00Z**, last window closes **15:00:00Z**
- `expires_utc` / `L5_LIVE_AUTH_NOT_AFTER` = **15:30:00Z** (30 min settlement slack)
- `issued + 14400s` ceiling = 16:55Z, so **`expires_utc` binds**, as intended

The 8 windows are counted as windows *traded*, not elapsed, so a quiet window
does not consume one and the campaign may run past 15:00Z — which is why the
deadline is the independent backstop.

## 9. Approval payload for your review

**Schema validated against the real validator**: `ok: True`,
`state: approved`, `max_windows: 8`, exposure `"5"`, daily loss `"5"`,
`clamped: all false` (every value is at or below its code ceiling).

```
{"approved":true,"expires_utc":"2026-10-25T15:30:00Z","issued_utc":"<MINT AT PASTE TIME>","max_daily_loss_usd":"5","max_total_exposure_usd":"5","max_windows":8,"nonce":"<FRESH 24-HEX>","series":"cpc-btc-updown-15m-"}
```

**`issued_utc` cannot be pre-filled.** The validator reads wall-clock and
refuses `approval_issued_in_the_future`; and once issued it is stale after 60
minutes. So the payload must be minted within the hour before arming. Say the
word and I will mint it with a current `issued_utc` and a fresh nonce for you to
paste — **I will not write `L5_CAMPAIGN_APPROVAL` or `L5_LIVE_AUTH_NOT_AFTER`
myself.**

## 10. The five variable changes, in order

| # | variable | value |
|---|---|---|
| 1 | `L5_EXPERIMENT_ID` | `l5-btc15m-notional-20261025-3b484e74` |
| 2 | `L5_TS_EXPERIMENT_ID` | `l5-btc15m-notional-20261025-3b484e74` |
| 3 | `L5_CAMPAIGN_APPROVAL` | the minted payload (§9) |
| 4 | `L5_LIVE_AUTH_NOT_AFTER` | `2026-10-25T15:30:00Z` |
| 5 | `L5_SHADOW_ONLY` | `0` — **last**, only after 1–4 verify |

Unchanged and correct: `L5_TS_OPENING_NOTIONAL_BUDGET_USD=0.80`,
`L5_MAX_TOTAL_EXPOSURE_USD=5`, `L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD=5.00`,
`L5_MAX_WORST_CASE_DEFICIT_USD=2.00`, `L5_DAILY_REALIZED_LOSS_HALT_USD=5`,
`L5_MAX_LIVE_ORDERS=1`, `L5_TS_OPENING_LEG_MAX_CONTRACTS=2`,
`L5_TS_ROUND_ALLOCATIONS=2`, `L5_TS_MIN_FIRST_LEG_PRICE=0.20`,
`L5_TS_TWO_ORDER_PAIR=1`, `L5_WINDOW_STRATEGY=TWO_SIDED`,
`L5_LIVE_AUTH_MAX_WINDOWS=8`.

## 11. Once authorized

Objective is **repeated completed pairs per window** within the standing
bounds. Frequency comes from the two allocation rounds and the REPLACE budget
(32, barely touched at 5/32 last time), **not** from larger clips: the opening
notional stays at $0.80 whatever the price, and no bound moves to generate more
trades.

Not implemented and not requested: taker completion, any sell path, a maximum
opening price, dynamic order mirroring, larger submission budgets, any increase
in capital or limits. No new services. The legacy test backlog is untouched.
