# P0 — built, tested, STOPPED: applying needs privileged approval

Per your instruction — *"If the operation requires a separate privileged
approval, stop and provide that exact request"* — it does, and this is it.

## What is built

`apply_cost_basis_corrections()` iterates the audit's **own** legs from the
walk that just ran. Nothing is hardcoded and no cost is carried in from a
previous session, so a leg whose venue fills have changed fails its own
reconciliation rather than being corrected against a stale figure. It adds
**no authority**: all ten guards live in `correct_cost_basis` and run on every
call.

**`INSPECT` is the default and writes nothing.** `L5_COST_BASIS_APPLY` unset,
empty or `INSPECT` is a dry run. Anything else is read as your batch id and
authorizes the write. Wired **boot-only**, so it cannot run twice in a
session, and the correction id carries the batch (`<batch>|<market>|<side>`)
so a rerun is idempotent.

115 tests across the verb and the applier. I also narrowed the module's purity
claim rather than letting a stale one pass — it is no longer read-only as a
whole, so the test now asserts `correct_cost_basis` is the only write verb it
calls, from exactly one function, with `audit()` still pure.

## Why I stopped

Applying needs three privileged acts I will not take on my own:

1. **A push to `claude/gemini-btc-economic-viability-h3u7zl`** — the branch the
   money-capable service builds from. You authorized one cherry-pick to it;
   that permission was specific and I am not treating it as standing.
2. **A deploy of `l5-pmus-live`**, the money-capable service.
3. **Setting `L5_COST_BASIS_APPLY=<batch-id>` on that service** — which
   authorizes a **write to the live realized ledger** on the volume.

The third is the one that matters. It moves `realized_today` from
−$233.98540000 toward −$24.3216 on the production accounting store. The guards
make it safe; they do not make it mine to trigger.

## The exact request, in two steps

**Step 1 — the dry run (writes nothing, and I recommend it first):**

> *"Push the applier to claude/gemini-btc-economic-viability-h3u7zl and deploy
> it to l5-pmus-live with L5_COST_BASIS_APPLY unset. Report the INSPECT
> output."*

That emits `L5_COST_BASIS_APPLY` with mode `INSPECT`: every leg it **would**
correct, before/after per leg, the trade ids, which legs stay UNKNOWN and why,
and the total delta — from a fresh venue walk. No write. You then see the real
47-leg plan against live data before authorizing anything.

**Step 2 — the apply, only after you have read step 1's output:**

> *"Set L5_COST_BASIS_APPLY=<batch-id> on l5-pmus-live and apply."*

On the next boot each established leg passes all ten guards and writes, with
`cost_superseded` holding the original verbatim. The two quantity-mismatched
legs are refused by guard 7 and keep cost UNKNOWN — by construction, not by
anyone remembering. HALT is neither read nor cleared; no owner field is
written, so `verified_l5` stays 0 and `manual` stays null.

## P1 — DONE and verified live

`PMUS_WS_LADDER_ROLLING=YES` is active on `pmus-ws-evidence-recorder` with
rungs 16, history 240, ahead 1. **Trading not activated** (`shadow_enabled:
false`; it is the read-only recorder).

**Before activating I checked the memory bound rather than assuming it, and
found `drop_stale_ladders` was dead code** — defined and never called. Unwired,
the rolling ladder would have held one ladder per *retired* window for
`prune_state`'s full two-hour grace period: eight windows on a 15m product,
the exact failure the function exists to prevent. Now wired and firing:

```
PMUS_WS_STATUS {"ladder_release":{"markets":1,"retaining":[
  "cpc-btc-updown-15m-2026-10-16-1630z","cpc-btc-updown-15m-2026-10-16-1645z"]}}
```

Rolling exact-slug selection and eviction both working across rotation.
Durability in the same window: `PMUS_BOOK_ROWS` 49 rows with `bid_qty`/
`ask_qty` depth and `both_sides_served: true`; `trades_1h` 500 with
`two_sided_trades: true`; `books_300s` 1,576; `trades_300s` 820. Memory
**`rss_gb` 0.163** — against the 8.27 GB that put `prune_state` there.

## P2 — the audit found a real defect in my own proposal

Worst-case loss across every state at q = 1.01:

| state | cash out | **worst loss** |
|---|---|---|
| neither leg fills (cancelled) | $0.0000 | **$0.0000** |
| DOWN partial 0.01 only, loses | $0.0068 | $0.0068 |
| UP full + DOWN partial, UP loses | $0.3199 | $0.3099 |
| UP only, held, UP loses | $0.3131 | $0.3131 |
| **DOWN only, held, DOWN loses** | $0.6868 | **$0.6868 ← maximum** |
| DOWN only, cross at 0.32 + fee | $1.0300 | $0.0200 |
| DOWN only, cross at 0.99 + fee | $1.6867 | $0.6767 |
| both filled (matched pair) | $0.9999 | *profit, excluded* |

**Maximum loss: $0.6868**, headroom $0.3132. The maker rebate is a credit and
rounds to $0.00 at this size, so it never adds to loss; cancellation costs
nothing. **The +$0.0101 matched-pair result is excluded from every loss figure
— profit is not a risk bound.**

**The defect:** `cash_policy` is `NO_RECYCLING` and `max_cash_deployed_usd` is
$1, but a completion order needs **new** cash on top of the pair — $1.03 to
$1.69 total. **At q = 1.01 selective completion is structurally unreachable:
the cash gate refuses it**, so the validation could only ever test the hold
path, which is not the envelope you authorized.

**Corrected sizing — q = 0.59**, where the whole authorized path fits $1:

| | |
|---|---|
| pair cash | $0.5841 |
| worst completion + fee | $0.4071 + fee |
| **total cash** | **≤ $1.00** ✓ |
| **worst-case loss** | **$0.4012** ✓ |
| net if both fill | **+$0.0059** |

Smaller edge, but it tests completion — which q = 1.01 cannot.

## P3 — the backstop cannot conceal or relax anything

Eleven proofs (`tests/test_l5_backstop_cannot_conceal.py`). The decisive one:

**With the backstop satisfied at any bound from $0 to $999,999, realized
−$233.9854 still returns `CUMULATIVE_LOSS_LIMIT`.** A satisfied backstop does
not unlock trading.

- It sits **after** the account-wide ceiling, so it can only **add** a refusal.
- An **unreadable** unknown is the *maximum* unknown and refuses
  (`UNRESOLVED_UNKNOWN_UNAVAILABLE`).
- An unexplained **credit** refuses exactly like a debit — magnitude, either
  sign.
- Its block reads **no** capital or exposure control (asserted on the source).
- Every gate after it still runs: `CASH_COMMITMENT`,
  `INSUFFICIENT_AVAILABLE_CASH`, `WORST_CASE_DEFICIT`.
- A zero bound permits only a fully reconciled book.

**Rescoping is NOT activated** and needs separate approval.

## The precise remaining blocker to the authorized live validation

Unchanged, and it is one thing:

**`CUMULATIVE_LOSS_LIMIT` — `realized_today` is −$233.98540000.**

You have declined to raise the ceiling, which I agree with. Applying the
correction brings it to **−$24.3216** — a 9.6× reduction that still exceeds
any $1-appropriate ceiling by 24×. So after P0 the blocker is *the same
blocker, 9.6× smaller*, and it still blocks.

The only route that does not require accepting an unresolved figure above the
ceiling is the P3 rescoping, now proven safe but **not activated**. That is
the next decision, and it is yours.

## State

HALT latched, `SHADOW_ONLY_LOCK`, `armed: false`, `live_orders: 0`, all limits
unchanged, nothing deployed to the money-capable service, the 47-leg
correction built and unapplied, rescoping inactive. The read-only ladder
recorder is the only thing I turned on.
