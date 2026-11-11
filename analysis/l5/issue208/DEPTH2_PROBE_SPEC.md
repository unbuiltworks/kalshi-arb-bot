# The depth-2 live probe: exact specification

**Not an authorization.** The shadow lock, the absolute deadline and the
campaign approval are what refuse, and clearing them is the owner's act.

## 1. Configuration — all existing, nothing new

| setting | value | source |
|---|---|---|
| `L5_TS_DEPTH_TICKS` | **2** | already the default |
| `L5_TS_EXPERIMENT_ID` | a probe id | required; without it the probe is inert |
| `L5_TS_MARKET` | the **exact** slug | required; without it the probe is inert |
| `L5_PROVISIONAL_FORCED_SIDE` | **unset** | proven unnecessary — §2 |
| taker crossing | **off**, both switches unset | unchanged |
| `ts_opening_leg_max_contracts` | 1 | unchanged |
| `max_live_orders` | 2 | unchanged |
| `max_cash_deployed_usd` | 12, `NO_RECYCLING` | unchanged |
| `max_worst_case_deficit_usd` | 1.00, check **ACTIVE** | unchanged |
| `max_unmatched_contracts` | 5 | unchanged |
| daily halt | 5.00 | unchanged |
| pair ceiling | 0.96 | unchanged |

**No limit, cap or ceiling is changed.** The only things that move are the
three authorization gates, and only the owner can move them.

## 2. `L5_PROVISIONAL_FORCED_SIDE` is NOT required

Exhaustive over the price grid: **only two books have exactly one
unreadable provisional price, and neither builds a plan at any depth.**

| bid | ask | UP prov | DOWN prov | builder, depths 0–3 |
|--:|--:|--:|--:|---|
| 0.00 | 0.01 | None | 0.99 | `BOOK_NOT_TWO_SIDED` |
| 0.99 | 1.00 | 0.99 | None | `PRICE_OUT_OF_BAND` |

Those two are the repair's entire region of effect: with both provisionals
readable the gate never fired, and with both unreadable the repair refuses
too. So enabling the flag **cannot produce a single order the default
configuration would not** — it only converts an early gate refusal into a
later builder refusal.

The repair is still correct and worth keeping. "Correct" and "needed for
this probe" are different claims, and the second is false. **Leave it
unset.**

## 3. Windows

**One** BTC15m window. A second only if the post-settlement `audit()`
returns `MEASURED`. Two is the ceiling, not the plan.

The exact slug is pre-registered **before** arming, and the probe is
scored on that slug alone — never on account-wide realized.

## 4. Expected behaviour, and what each outcome teaches

At entry the builder emits exactly two post-only MAKER legs from the
authenticated book. On a 0.62/0.63 book: UP own 0.60 (wire 0.60, below the
bid), DOWN own 0.35 (wire 0.65, above the ask), pair 0.95, edge 0.05.

| outcome | replay probability | what it teaches |
|---|--:|---|
| both legs fill — **pair** | 52.5% | the pair settles at the modelled 0.95, or it does not |
| one leg fills — **lone leg** | 42.3% | **the completion path**: book age at evaluation, fill→decision latency, refusal reasons |
| neither fills | 5.3% | our queue position is worse than the PROVEN arm assumed |

**The lone leg is the informative case, not the failure case.** A completed
pair leaves no unmatched inventory, so the completion controller never
evaluates — and book age at evaluation is the one number production has
never produced.

Second-leg behaviour, verified offline on the real controller methods:
`COMPLETE` when the complement is inside the ceiling, `REPRICE` when our
own resting order is worse, `ALREADY_RESERVED` when it is already better,
`HOLD` / `PAIR_ABOVE_REDEMPTION` when the pair exceeds $1, and refusal
where no restable side exists.

## 5. Evidence required — the probe is void without it

| evidence | where it comes from |
|---|---|
| venue order id on every submission | `record_submit` → `live_orders` |
| venue-confirmed fill | `poll_orders` → `apply_order_progress`, delta-booked so a repeated poll books once |
| exact cost basis | `apply_fill` stores `own × qty` from the venue's wire `avg_px`; a depth-2 pair books at **0.95** |
| distinct venue ids | `audit()` refuses duplicates |
| no `UNCERTAIN` order | `audit()` refuses |
| book age at evaluation | `completion_latency.book_age_at_decision_s` |
| fill→decision latency | `detection_to_first_decision_s` |
| actual fees and rebates | venue, kept apart from the schedule estimate; accrued is **not** income |
| settlement | the probe's own market only |

## 6. Verification, before and after

```
# before, must print PASS ok=True
python3 analysis/l5/issue208/run_probe_preflight.py preflight \
    --heartbeat hb.json --market <slug> --bid <bid> --ask <ask> --depth 2

# after settlement
python3 analysis/l5/issue208/run_probe_preflight.py audit \
    --heartbeat hb.json --inventory <state.json> --market <slug>
```

`verdict` and `ok` answer different questions. **`verdict`** is about the
cost basis and decides whether a settled figure may be quoted at all.
**`ok`** additionally needs fees, restart evidence and reconciliation. A
probe can be `MEASURED` with `ok: false` — real P&L, outstanding evidence.

**`UNINTERPRETABLE` is not a bad result. It is the absence of one.** A
settled figure built on an unknown cost basis is an upper bound, and an
upper bound reported as realized P&L is how a placeholder becomes a
performance claim.

## 7. Stop conditions

Automatic, already in force — none of these is new:

- **shadow lock** re-asserted → no submission
- **halt latch** asserted → no submission; cancellation still available
- **daily realized halt** at −$5.00
- **worst-case deficit** above $1.00 → refused at the gate
- **unmatched contracts** above 5 → refused
- **cash envelope** exhausted → refused
- **campaign submission budget** exhausted → refused
- **entry cutoff** at 480 s, **completion cutoff** at 600 s, deadline
  cancel by exact venue id
- **unverifiable cancellation** → the engine disarms rather than guessing
- **watchdog**, independent of the trading process, on a 120 s staleness bound

Manual stop conditions for this probe, which the owner should state when
authorizing:

1. `preflight()` not `ok` → do not arm.
2. Any order reaches `UNCERTAIN` → stop, reconcile, do not re-enter.
3. The probe market appears in `unknown_basis_keys` → the window is
   **uninterpretable**; do not repeat until basis booking is fixed.
4. p95 book age at evaluation above 2.0 s → a blocker in its own right,
   independent of the P&L.

## 8. What this probe cannot do

It cannot establish profitability. One depth-2 entry of one contract per
leg has a replay expectation of **+$0.027** against a bounded worst case
of **−$1.00**, and the gap to profitable matched-pair execution is
**+0.0651/pair — 6.5 ticks**. No one- or two-window probe resolves that.

It is a **diagnostic**: does a first leg fill, does inventory register,
does the second-leg decision run on a fresh book, and does settlement
reconcile exactly. Modelled profitability is not settled realized
profitability, and nothing from this probe will be reported as the latter
until the venue has settled it and `audit()` returns `MEASURED`.
