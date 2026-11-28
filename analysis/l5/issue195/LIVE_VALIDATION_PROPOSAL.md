# Bounded maker-only live validation — proposal, NOT authorized

Requires separate owner approval. Nothing below has been applied.

## What this window is for

**To produce the first real maker-order lifetime observations**, so that
`P(fill | queue_ahead)` stops being unmeasured. It is **not** a profitability
test: the measured expectancy of maker-only quoting is negative at every
depth in both samples, and one window cannot change that.

Stated plainly: **I expect this window to lose money or break even.** Its
value is the venue-confirmed decision tape and the fill observations, which
nothing else can produce.

## Exact configuration (all already in force — no variable changes)

| setting | value | source |
|---|---|---|
| aggregate exposure | **$1.00** | `L5_MAX_TOTAL_EXPOSURE_USD`, unchanged |
| opening contracts per leg | **1** | `L5_TS_OPENING_LEG_MAX_CONTRACTS`, unchanged |
| allocation rounds | **1** | `L5_TS_ROUND_ALLOCATIONS`, unchanged |
| max live orders | **2** (one per side) | unchanged |
| opening pair ceiling | **0.96** | `L5_TS_MAX_PAIR_COST`, unchanged |
| requote pair ceiling | **0.96** | PR #200 default, no variable |
| taker crossing | **OFF** | both switches unchanged |
| daily realized loss halt | **$5** | unchanged |
| watchdog | independent service, unchanged | |

**Maximum loss if everything goes wrong: $1.00** — one unmatched leg at a
$1.00 exposure cap, which is also the worst case the decision record will
print at the time.

## What must be written, and only this

Two variables, both strictly bounded, and only on separate approval:

1. `L5_CAMPAIGN_APPROVAL` — a fresh single-window document with a new
   nonce, `max_windows: 1`, `max_total_exposure_usd: 1.00`,
   `max_daily_loss_usd: 5`, and `window_starts` naming exactly one window.
2. `L5_LIVE_AUTH_NOT_AFTER` — the window end plus its settlement period,
   and nothing beyond.

**Not** touched: capital limits, loss limits, the taker switches, the
defensive budget (the **$0.0521** discrepancy stays open and unreset), the
watchdog.

## Acceptance criteria, decided in advance

| outcome | criterion |
|---|---|
| **execution success** | ≥1 venue-confirmed matched pair at a pair cost ≤ 0.96 |
| **measurement success** | ≥2 maker orders observed to terminal state with `queue_ahead` at placement recorded — *this is the real objective* |
| **controller success** | every tick with a residual carries an `L5_UNMATCHED_DISPOSITION` record; no `N/A`; no cancellation of a resting quote inside the pair ceiling |
| inconclusive | no first-leg fill |
| failure | loss beyond $1.00, any unapproved order, any safety gate failing, or a profitable resting quote cancelled |

## Pre-arm checklist (all verified before arming, else remain disarmed)

1. Deployment SUCCESS on the controller-stack SHA, container booted.
2. `completion_max_pair` reads **0.96** in `L5_EFFECTIVE_RUNTIME_CONFIG`.
3. `authorization_preflight` `ok: true` for the authorized window.
4. Watchdog sweeping, `open_orders: 0`, `read_error: null`.
5. `unresolved_accounting.count: 0`.
6. `taker_crossing_reachable: false`.
7. Entry cutoff not yet passed.

## Why one window and not more

The controller stack is new in production. One window produces the
decision tape and the first fill observations at a $1.00 bound; a second
window adds little evidence and doubles the exposure. **Frequency before
size** applies only once a window has run clean.

## What this does not do

It does not test taker completion, does not test size, does not test
concurrency, and does not move us measurably toward $480/day. On the
measured out-of-sample figure of **+$0.0048 per contract-attempt** at best,
that target needs roughly **1,000 contracts per leg per window** — three
orders of magnitude above this proposal.
