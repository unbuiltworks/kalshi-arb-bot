# The 0015z deadlock: reproduced root cause and the correction

Reproduced from the committed production tape against the deployed SHA
`239ce8d2`. No new pricing mechanism was needed, and none was added.

## The production state immediately before the first refusal

From the raw record at `2026-10-28T00:17:34.676042719Z`:

| | |
|---|---|
| market | `cpc-btc-updown-15m-2026-10-28-0015z` |
| accumulation round | 1 of 8, `round_allocations: 4` |
| legs claimed | `{UP: false, DOWN: true}` |
| frozen plan | UP 0.6000 / DOWN 0.3500, pair 0.9500, qty 1.87 |
| `touch_at_placement` | UP **0.6200** — priced against a 0.62 bid |
| book now | bid 0.4600 / ask 0.4700, `stream_age_s` 0.296, `authenticated_ws` |
| inventory | UP **1.87** and DOWN **1.87** — round 1's pair, filled at +29s (DOWN own 0.33) and +74s (UP own 0.61) |
| residual | **0** — `decision_role: FLAT_OPEN` |
| complement | `two_sided_complement: null` |
| live orders | none (`order: null`, `intent: null`) |
| `plan_unexecutable` | **`LEG_WOULD_CROSS:UP`** |
| `retired` | **false** |
| `retirement_blocked_by` | **`LEG_HOLDS_INVENTORY:UP`** |
| decision latency | 32.247 s |

## Why all three mechanisms declined

* **Retirement** detected the cross correctly and was then **refused**,
  because `probe_round_is_safe_to_retire` blocked on *any* filled quantity
  on either leg — and both legs held 1.87. The pair was complete and
  perfectly balanced, so there was nothing to strand.
* **Complement repricing** never applied: residual is zero, the role is
  `FLAT_OPEN`, and there is no imbalance to complete.
* **Other-leg selection** could not help: DOWN was already claimed, so UP
  was the leg to place, at the frozen 0.6000.

The round could therefore neither execute nor retire. `plan_executable`
returned `LEG_WOULD_CROSS:UP` on every subsequent tick, the UP leg at own
0.6000 was re-proposed, and the pre-send cross guard refused each one —
**23 refusals from 00:17:34 to 00:22:01**.

## This is not one window

Scanning every `TWO_SIDED_ROUND` evaluation in the preserved tapes:

| outcome | ticks |
|---|---|
| blocked `LEG_HOLDS_INVENTORY:*`, not retired | **207** |
| blocked `ORDER_STILL_LIVE` (legitimate) | 58 |
| retired successfully | 6 |

Of the 207, **139 were `FLAT_OPEN` — zero residual, matched inventory,
wrongly blocked**:

| window | wrongly-blocked ticks |
|---|---|
| 0015z | **23** |
| 0030z | 4 |
| 0045z | 3 |
| 0100z | **73** |
| 1330z | 9 |
| 1430z | 14 |
| 1445z | 10 |
| 1500z | 3 |

0015z's count is **exactly 23**, matching the 23 `PRESEND_WOULD_CROSS:UP`
refusals one for one. That correspondence is what makes this a reproduced
cause rather than a plausible one. (The 44 `NEW_OPEN` ticks may also be
matched; 139 is the conservative count, taking only the unambiguous
`FLAT_OPEN` rows.) A complement was usable on just 2 of the 207.

## The correction

`probe_round_is_safe_to_retire` now blocks on **unmatched** quantity rather
than on holding anything:

```python
up = self.store.position(market, UP)
dn = self.store.position(market, DOWN)
if up != dn:
    return "LEG_HOLDS_UNMATCHED:%s" % (UP if up > dn else DOWN)
```

This is the same correction the opening floor already carries, whose own
comment states the principle: *"KEYED ON THE IMBALANCE, NOT ON HOLDING
ANYTHING... After a pair completes, `up` and `dn` are both non-zero AND
EQUAL: there is no imbalance to manage."*

Order safety is untouched and still checked **first**: `ORDER_STILL_LIVE`
and `ORDER_STILL_FILLABLE` precede the balance test, and an unreadable
position still returns `STATE_UNREADABLE`.

Measured before and after, across the four inventory states a crossing
frozen plan can meet:

| state | before | after |
|---|---|---|
| flat | retired, sent 0.41 | unchanged |
| DOWN held (imbalance) | blocked, complement sent 0.41 | unchanged, reason renamed |
| UP held (imbalance) | blocked, nothing sent | unchanged, reason renamed |
| **matched** | **blocked, re-proposed 0.35** | **retired, re-plans, sends 0.41** |

Only the deadlocked state changes. Every imbalance still blocks.

## Measurable expected effect

**Throughput.** In 0015z the deadlock began at +154 s and the entry cutoff
is +600 s, so it consumed **446 of 600 seconds (74%) of the window's entry
capacity**, and at the 120 s order TTL that is up to **3 further round
attempts**. The campaign authorized **4 round allocations** and only round 1
was usable: **3 of 4 were blocked**. Cash was not the constraint — round 1
consumed $1.7578 of the $12.00 envelope, leaving $10.24 across rounds 2–4.

What is *not* claimed: that those rounds would have filled or been
profitable. A plan is not a fill, the earlier uncertainty analysis applies
unchanged, and 23 refusals becoming 3 attempts is a capacity figure, not a
P&L figure.

**Residual risk.** The bound is **unchanged**: residual cap 3 contracts
(ABSOLUTE binds), worst-case deficit $3.00, daily halt $5, cumulative cap
$20 — the fix touches none of them. What rises is the *frequency* of
approaching them, because a window that can open rounds has more chances to
strand a leg. The fix also removes one specific harm: it does not create
the late stranded leg 0015z suffered at +491 s, but it does mean the engine
works rounds from +154 s instead of idling, so the composition of that risk
changes while its ceiling does not.

**No new pricing mechanism.** With retirement unblocked, the round retires
and re-plans from the current book at 0.41 — the same price a reprice would
have produced. `L5_FRESH_BOOK_REQUOTE` remains unset and inert, and the
handle-to-wire tests confirm it stays unreached.
