# Smallest maker-only live validation — proposal

**NOTHING IS ARMED AND NOTHING MONEY-CAPABLE IS DEPLOYED.** No variable
is set, no deploy trigger touched, taker execution disabled, production
disarmed. This document asks for approval; it does not act on one.

---

## 1. What was implemented

| ask | status |
|---|---|
| replace the one-fill-per-side lockout with bounded repeated entries + explicit unmatched ceiling | **done** — `straddle_repeat_enabled`, `straddle_max_side_usd`, `straddle_max_unmatched_usd` |
| inventory-aware maker repricing and replenishment | **done** — `straddle_reprice_ticks_per_clip`; replenishment is the lockout's removal |
| preserve two-sided quoting, exact-id cancellation, reconciliation, watchdog, capital/loss limits | **unchanged** — no cancel, ledger, registry, watchdog or ceiling code touched |
| correct the taker coefficient to the verified account value, taker disabled | **done** — `pmus_fees.thetas()` now returns Tier 1 (0.095); `taker_allowed` untouched and asserted False by test |
| regression tests for repeated fills, partial fills, cancellations, inventory limits, rollover, settlement | **done** — 38 new tests |

### Regression status, with the baseline stated correctly

Full suite, same seed and ordering, before and after:

| | failed | errors | passed |
|---|---|---|---|
| baseline (`70915b4ed`) | 34 | 14 | 22,157 |
| **after this work** | **34** | **14** | **22,229** |

The failing and erroring SETS are identical in both directions: nothing
introduced, nothing incidentally fixed. The passed count rises by 72,
which is this change's new tests.

**A correction to my own earlier reporting.** I twice described the
baseline as having "one pre-existing failure", including in two commit
messages on this branch. That was wrong: the baseline carries 34 failures
and 14 errors. The error came from reading the alphabetically-last line
of a sorted failure list as though it were a total. The *diffs* I acted
on were computed as set differences and are unaffected -- the 28
failures the first coefficient attempt introduced really were introduced,
and this change really introduces none -- but the baseline was never
clean, and the 34+14 are pre-existing debt this work neither caused nor
fixed.

**The default is byte-for-byte the old behaviour.** `straddle_repeat_enabled`
defaults False, which keeps the lockout exactly; the two dollar bounds
default to zero, which **refuses the mode** (`REPEAT_WITHOUT_BOUNDS`)
rather than inventing a bound. Repetition cannot be switched on without
also stating how lopsided the book may get.

### What the coefficient correction actually moved

It tightened bounds, in the safe direction, and I want the consequences
stated rather than buried:

- the preflight's worst case on a $5 ceiling: **$5.1738 → $5.2375**
- the principal a $5 all-in ceiling admits: **$4.69 → $4.58**
- the fee-safe taker completion against a 0.45 leg: **0.52 now refused**, 0.51 still admitted
- the maker rebate this account is credited: **$0.0125·C·p(1−p) → ZERO** (Maker Tier 1 is below the $10,000,000 threshold)

Nothing became riskier. A cost that was always being paid is now being
counted. Historical analyses are unaffected: the schedule is still
effective-dated, so evidence recorded before 2026-11-14 prices at the
coefficient that applied to it.

## 2. The EV gate, and what the unmeasured fill probability requires

**The gate is not bypassed and no EV is invented.** `l5_straddle_ev`
requires `p_pair_natural`, `p_complete` and `p_lone_win` as inputs with
no defaults, by design: "a guessed rate here would manufacture the edge
the gate exists to test for." A new test asserts repetition does not
supply them — an unset rate still refuses every rung.

**Where those rates exist, and where they do not:**

| rung | rates | gate |
|---|---|---|
| **0.05–0.10** | MEASURED over 503 shadow windows (lone-leg win 9.81% against an 8% price; opposite side reaching 0.08 in 19 of 245) | **admits** |
| **0.40–0.47** (the 0x8dxd region) | **NOT MEASURED** | **refuses**, correctly |

So the proposal below runs at the **measured** rungs. That is not a
compromise dressed up as a plan — it is the only configuration that can
run without either bypassing the gate or feeding it numbers from a
different trade.

### What would be needed to operate at mid price

Exactly one of:

1. **Measure the three rates at those prices.** This is what the
   read-only campaign would have produced and it has been stopped by
   instruction. It needs no live orders and no capital, only time.
2. **A bounded live-test exception**, explicitly granted: permission to
   operate at a rung whose `p_pair_natural` is unmeasured, inside a hard
   dollar bound, *because the fill probability cannot be observed without
   placing an order at all*. This is a real gap and not a loophole: queue
   position is the one quantity no amount of book-watching settles,
   since our own order's place in the queue does not exist until it is
   sent. What such an exception buys is the fill distribution; what it
   costs is bounded by the unmatched ceiling.

I am **not** requesting (2) in this document. The proposal below needs no
exception at all.

## 3. The proposal

| | |
|---|---|
| **Branch / SHA** | `claude/btc5m-favorite-accumulation-wzh2xo` @ **`5f33ec915`** (code); this document is the commit on top of it |
| Market | **BTC5m only**, one market at a time |
| Strategy | STRADDLE, **maker-only, `post_only`, taker disabled** |
| Rung | **0.08** single rung (where the EV gate's rates are measured) |
| Clip | **$0.40 per side** (5.00 contracts at 0.08) |
| Repetition | **enabled**, `straddle_max_side_usd = $0.80` (two clips a side) |
| Unmatched ceiling | **`straddle_max_unmatched_usd = $0.40`** (one clip) |
| Repricing | **1 tick per clip** of imbalance |
| Windows | **4 consecutive**, then stop |
| Eligibility | from t=0, entry closes at **t=270s**, cancel sweep in the final 30s |

### Exposure and worst-case loss

- **Maximum committed at any instant: $1.60** ($0.80 per side), inside
  the unchanged `max_exposure_usd` $5.00 per-market and the $10.00
  account-inclusive aggregate.
- **Worst-case loss per window: $0.40.** A matched pair at 2×0.08 costs
  $0.16 a contract and pays $1.00, so matched inventory cannot lose — the
  only way a window loses money is unmatched inventory, and that is what
  the $0.40 ceiling bounds. After one unmatched clip the next clip on
  that side is refused (`UNMATCHED_CEILING`).
- **Worst-case loss for the campaign: $1.60** (4 × $0.40), against the
  unchanged $5.00 daily settled-loss stop.
- No capital increase. Every ceiling is the one already authorised.

### Watchdog and cancellation — status, not assertion

The code provides: exact-venue-id cancellation of every own order at
t=270s, the durable `STOPPED` latch (written *before* any cancel is
attempted, and a cancel's return value decides nothing — the order is
re-read), the ledger, and the ownership-registry document the independent
watchdog reads.

**What I cannot assert from here:** that production is currently serving
that registry document and that the watchdog is reading it. That was the
one outstanding safety failure earlier in this programme
(`LANE_NOT_IN_OWNERSHIP_REGISTRY`), and it must be **re-verified against
the live deployment at arming time, not taken from this document.** I
will run `src/l5_live_safety.check()` and report every failure before
anything is armed.

### Automatic disarm

1. `L5_BTC5M_NOT_AFTER` set to the fourth window's close — a hard stop,
   and a campaign approval naming a past window refuses rather than
   sliding forward.
2. `restartPolicyType: NEVER` — a crashed or completed money-moving
   session does not relaunch itself.
3. No automatic continuation after the fourth window; a further campaign
   is a new approval.
4. The `STOPPED` latch halts on demand and does not resume when the halt
   later reads clear.

### What this validation buys

The three things no read-only work produced: **whether a maker order at
a measured rung actually fills**, **whether repeated entry and repricing
behave on a live book as they do in tests**, and **whether settlement
reconciles against the venue's own figures**. It cannot establish that
the strategy is profitable — four windows at a $0.40 clip cannot — and I
will not report it as having done so.

## 4. The authorization statement I need

> I authorize deployment of SHA `5f33ec915` to `l5-btc5m-live` and a live
> maker-only STRADDLE campaign on BTC5m for exactly four consecutive
> windows, at rung 0.08 with a $0.40 per-side clip, repetition enabled
> under a $0.80 per-side cap and a $0.40 unmatched ceiling, 1-tick
> inventory repricing, taker disabled, inside the unchanged $5.00
> per-market / $10.00 aggregate / $5.00 daily-loss ceilings, with
> `L5_BTC5M_NOT_AFTER` at the fourth window's close and no automatic
> continuation.

Plus: the four exact UTC windows, which I will generate **after** the
image is confirmed healthy so the deployment does not consume them, and
confirmation that I may set the straddle and repetition variables on
`l5-btc5m-live`.

Until that statement is given: no variable is set, no deploy trigger is
touched, and nothing is armed.
