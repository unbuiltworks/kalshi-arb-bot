# CONFIRMED SAFETY-CONTROL DEFECT — the residual cap did not bind

**Severity: the control the arming preflight advertises as `effective_cap_contracts: 3`
did not refuse an order that its own arithmetic says it must refuse.
Unmatched inventory reached 4.25 contracts, 42% above that cap.**

Found in the 1415z window of the Stage B2 campaign, 2026-10-31. New entries
were halted on discovery. This is NOT a breach of the owner's authorized
dollar limits — every one of those held — and that is precisely why it is
reported separately: a contract-count control silently failing while the
dollar cap happens to hold is the dangerous case, not a harmless one.

## The fill trace, from `L5_LIVE_LIFECYCLE`

| # | utc | coid | side | qty | own px | residual after |
|---|---|---|---|---|---|---|
| 1 | 14:16:34 | `l5f-49f7834a…` | UP | 1.96 | 0.32 | UP 1.96 |
| 2 | 14:17:08 | `l5f-c0293abc…` | DOWN | 1.96 | 0.68 | **0** (pair 1.0000) |
| 3 | 14:17:41 | `l5f-2403d6ab…` | UP | 1.83 | 0.27 | UP 1.83 |
| 4 | 14:19:06 | `l5f-e35557f9…` | DOWN | 1.83 | 0.67 | **0** (pair 0.9400) |
| 5 | 14:20:38 | `l5f-0b6b1d14…` | UP | **2.17** | 0.28 | UP 2.17 |
| 6 | 14:21:13 | `l5f-fe3923af…` | UP | **2.08** | 0.25 | **UP 4.25** |

Ties out exactly to the venue settlement line: `up_qty 8.04`, `dn_qty 3.79`,
`matched_qty 3.79`, `residual_qty 4.25 UP`. Every fill was maker, commission
`0.0000`.

**Fills 5 and 6 are both UP, 35 seconds apart, with no DOWN between them.**

## What the gate should have done

`src/l5_live_execution.py:7286`:

```python
exempt_open = b_abs == 0                                  # flat book: never capped
increases   = a_abs > b_abs
clip        = max(self.store.max_clip(market), tr["qty"])
cap         = min(self.residual_cap_k * clip, ABS_RESIDUAL_CAP_CONTRACTS)
if (not exempt_open) and increases and a_abs > cap:
    return prov.refuse("SAFETY_OVERLAY", "RESIDUAL_ABOVE_CAP")
```

Evaluated on order 6:

| term | value |
|---|---|
| `b_abs` (prospective before) | **2.17** → `exempt_open` **False** |
| `a_abs` (prospective after) | **4.25** → `increases` **True** |
| `clip` | max(2.17, 2.08) = 2.17 |
| `cap` | min(2 × 2.17, 3) = **3** |
| `a_abs > cap` | 4.25 > 3 → **True** |
| **`binds`** | **True → must refuse** |

It was not refused.

## The three hypotheses, resolved

**1. Intentional, documented reducing-order overshoot — RULED OUT.**
Order 6 was not a reducing order. Residual was already UP 2.17 and the order
*increased* |residual| to 4.25. The reducing-leg exemption ("a leg that
shrinks |prospective| is never refused, at any depth") cannot apply to a leg
that grows it. The opening-leg exemption cannot apply either: it requires
`b_abs == 0` and the book held 2.17.

**2. Stale or misleading preflight cap — RULED OUT as the explanation.**
The preflight computes `min(k × largest_recorded_clip, ABS)` from a historical
37.73-contract XRP clip, reporting `which_binds: ABSOLUTE` = 3. The per-order
gate uses the *per-market* clip: `min(2 × 2.17, 3)` = **also 3**. Both routes
yield 3, so a stale clip does not explain 4.25. The preflight figure was
correct; it simply was not enforced.

**3. Genuine failure to enforce the intended residual limit — CONFIRMED.**

Corroborating evidence:

- heartbeat `policy`: **`residual_cap_binds_session: 0`**,
  `residual_cap_binds_this_market: 0` — the cap refused nothing, ever, this
  session.
- **RETRACTED.** I first cited "zero `RESIDUAL_CAP` evaluation notes in the
  campaign" as corroboration. That evidence does not hold: `INVENTORY_CLASS`
  notes, emitted unconditionally eleven lines earlier at the same call site,
  are **also** absent from the logs. So `prov.note(...)` records are simply
  **not log-visible in this deployment**, and their absence says nothing
  about whether the gate ran. Withdrawn.

**That unobservability is itself the blocking defect.** The gate's own comment
says the note is emitted either way "so the treatment is read off the record
rather than inferred from what happened next" — and it cannot be, because the
notes do not reach the logs. The single most important control in the residual
path is undebuggable in production.

## Why the dollar cap holding does not excuse it

The 1415z worst case came to **$1.1888 against the $1.50 per-market deficit
cap** — it held with 31 cents to spare, and it held because the two UP legs
were cheap (own 0.28 and 0.25), not because any control bounded the contract
count. At a higher own price the same 4.25 contracts would have breached the
dollar cap too. The contract control is the one that was supposed to stop the
position from getting there, and it did not.

## Action taken

`PMUS_Q1_ARM` cleared at 14:44:2x — the documented revocation ("arming is
revocable by unsetting the owner token"). Cancellation, reconciliation and
settlement accounting are not gated by it, so risk-reducing operations are
preserved. Confirmed landed: `L5_LIVE_START` 14:51:22 with
**`owner_arm_token: false`**. Watchdog at 14:51:59: `open_orders 0`,
`cancel_enabled true`, `read_error null`, no orphans.

**A race was lost and must be recorded.** A Railway variable write triggers a
redeploy, and the running process keeps its old environment until restart.
**Window 8 (1445z) armed at 14:45:12, 52 seconds after the write**, and traded
on the defective process until the 14:51:22 restart. It filled one leg —
**DOWN 2.02 @ 0.53 wire (own 0.47)** at 14:46:11 — and no completion filled.
Because the engine is now disarmed, that leg cannot be completed and will
settle unmatched at a worst case of about **−$0.95**, inside the $1.50 cap.
No faster in-band mechanism was available from this session: the halt latch is
file/endpoint-driven on an internal private domain.

## Root cause, narrowed by reproduction

`tests/test_l5_residual_cap_1415z_regression.py` (10 tests, all passing)
drives the **real `InventoryStore`** with the five real fills and evaluates
the gate's own predicate for order 6:

```
resid_before 2.17   (real store: UP 5.96 - DOWN 3.79)
max_clip     2.17   (real store)
b_abs 2.17 -> exempt_open False
a_abs 4.25 -> increases   True
cap   min(2 * 2.17, 3) = 3
binds TRUE   -> must refuse RESIDUAL_ABOVE_CAP
```

**So the predicate is correct and the store functions are correct.** Given
these inputs the gate refuses. Two further findings from the same run:

- the gate is **sound on overshoot**, contrary to my first hypothesis. I
  assumed a "reducing" order that overshoots past flat would slip through;
  it does not, because `increases` compares MAGNITUDES. DOWN 6.00 against
  UP 2.17 lands at |3.83| > |2.17|, so it binds. The only remaining
  exemption is an overshoot that lands *closer* to flat, which is harmless
  and intended.
- the policy is OFF only when `PMUS_RESIDUAL_CAP_K` is unset, and the
  heartbeat echoed `k=2` specifically so "which policy ran is read off the
  record". So the cap was ON.

**Therefore the defect is NOT in the predicate, the store, or the
configuration. It is in reachability or in the state the gate was handed at
evaluation time** — and it cannot be narrowed further from production
evidence because the gate's decision record never reaches the logs.

## Required before any further live authorization

1. **Make the gate observable.** `prov.note` must reach the logs (or an
   equivalent record must), for `RESIDUAL_CAP` and `INVENTORY_CLASS` at
   minimum. Without it the next incident is equally undebuggable. This is
   the first fix, because every other answer depends on it.
2. **Then** determine whether the order reached the gate at all. Candidate
   paths to audit: the accumulation-round first leg (round 4 opening while
   round 3 held unmatched inventory, after round 3's DOWN leg was claimed and
   cancelled), `acc_recover` cancel-and-replace, Route B `book_completion`,
   and `l5_defensive_completion` — any submitter that does not pass through
   `handle()`'s common gates.
3. Make the arming preflight's `effective_cap_contracts` row state which
   execution paths actually evaluate it, so a control cannot be advertised on
   a path that never calls it.
4. Re-verify on recorded books before re-arming. No capital increase, no
   crossing and no new services are implicated or requested.
