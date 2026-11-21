# `RESIDUAL_CAP_k2` — arm-readiness pack

Implemented, tested, **DISARMED, not deployed, no order placed.** The policy
is OFF by default and the executor with it OFF is byte-for-byte frozen
`e2bd442` behaviour.

## 1. The formula and its units

```
klass  = FLAT_OPEN | INCREASE_RESIDUAL | REDUCE_RESIDUAL     (existing)
clip   = max( largest single quantity this market has FILLED , this order's qty )
cap    = k * clip                                            k = 2
after  = | residual_realized + (+qty if UP else -qty) |
refuse iff  klass == INCREASE_RESIDUAL  and  after > cap
```

Every quantity is in **contracts**. `k` is dimensionless. `residual_realized`
is the existing `InventoryStore.residual`, which counts filled inventory only
— an unfilled order has not moved the imbalance, and the existing
`risk_exposure_usd` already prices outstanding orders as if they fill in full,
so the dollar bound is unaffected by that choice.

Comparison is strict: **equality passes.** `after == cap` is inside the bound.

## 2. Worked examples, driven through the real executor

At the live envelope (L5 size 20, λ = 0.04518752824220514525 → 0.9038 desired,
floored to the venue's 0.01 grid → **0.90 contracts**), `k = 2`:

| step | side | residual in | class | qty | clip | cap | after | outcome |
|---|---|---|---|---|---|---|---|---|
| 1 | UP | 0 | FLAT_OPEN | 0.90 | — | — | — | SUBMITTED |
| 2 | UP | 0.9 | INCREASE_RESIDUAL | 0.90 | 0.90 | 1.80 | **1.80** | **SUBMITTED** (equality passes) |
| 3 | UP | 1.8 | INCREASE_RESIDUAL | 0.90 | 0.90 | 1.80 | 2.70 | **RESIDUAL_ABOVE_CAP** |
| 4 | UP | 1.8 | INCREASE_RESIDUAL | 0.90 | 0.90 | 1.80 | 2.70 | **RESIDUAL_ABOVE_CAP** |
| 5 | DOWN | 1.8 | REDUCE_RESIDUAL | 0.90 | — | — | — | SUBMITTED |
| 6 | DOWN | 0.9 | REDUCE_RESIDUAL | 0.90 | — | — | — | SUBMITTED |
| 7 | UP | 0.0 | FLAT_OPEN | 0.90 | — | — | — | SUBMITTED |

Steps 1, 5, 6 and 7 show a dash under cap because **the cap is not evaluated
at all** on those actions, not because it evaluated and allowed them.

## 3. Why an OPEN and a repairing leg cannot be refused

Structural, not a check that could be got wrong: the entire block sits behind

```python
if self.residual_cap_k is not None and klass == INCREASE_RESIDUAL:
```

`inventory_class` returns `FLAT_OPEN` for every action taken from zero
residual and `REDUCE_RESIDUAL` for every action on the side that shrinks the
imbalance. Neither reaches the comparison for any `k`, including `k` so small
that no order could satisfy it. `test_an_opening_leg_from_flat_is_never_reachable`
asserts this at k = 0.0001, 1, 2 and 1000, and asserts the `RESIDUAL_CAP` note
is *absent*, so an implementation that evaluated and then happened to allow
would fail.

The cap also sits **after every frozen check**. A `RESIDUAL_ABOVE_CAP` refusal
is therefore always an action the frozen executor *would have submitted*: the
policy can only ever subtract from `L5_NATIVE`, never relabel an existing
refusal, and never change a price, side, size or mode.
`test_every_action_the_cap_refuses_was_permitted_by_the_frozen_path` runs the
same actions twice, cap off and cap on, and asserts each capped refusal
corresponds to a frozen-path **submission**.

## 4. Tests

`tests/test_l5_residual_cap.py`, **32 passing**:

- clip: largest single fill not the total; per-market; survives reload; refuses
  an unidentified market key
- unreachable for `FLAT_OPEN` at four values of k, and for a repairing leg on
  a long and on a short residual, at k = 0.0001
- only `INCREASE_RESIDUAL` is ever evaluated (both signs)
- the formula: cap is `k × clip`, clip includes this order, residual compared
  is post-full-fill, a short residual compared on magnitude
- boundary: exactly at the cap passes; **one hundredth of a contract** past it
  refuses; one hundredth below passes
- a refusal sends nothing, reserves nothing, moves no inventory
- the cap never changes price, side or mode
- default is OFF; OFF never evaluates and never refuses
- the env switch takes only a positive number — blank, `0`, `-2`, `two`, `YES`
  all mean OFF

## 5. Expected retention, from the OOS replay

| | DEV | OOS | whole corpus |
|---|---|---|---|
| actions retained | 83.4% | 84.1% | 83.7% |
| contracts retained | — | — | 78.5% |
| matched contracts retained | 83.9% | 83.7% | — |
| maker fills retained | 83.8% | 83.9% | — |
| matched P&L retained | 78.6% | **45.6%** | — |
| peak \|residual\| | — | — | −67% (1,180 → 389) |
| max drawdown | — | — | −68% ($4,295 → $1,388) |

Predeclared floors are on **volume**, where the estimate is stable across
halves: action retention ≥ 0.75, contract retention ≥ 0.70. No floor on
matched P&L, which is not stable (78.6% → 45.6%) and whose movement is the
intended consequence of clipping the exposure tail in both directions.

## 6. PMUS fee and rebate accounting

The probe is `maker_only=True`, so **no taker fee is payable at all** — every
fill is a maker fill. The $5 worst-case gate still carries the taker-fee bound
(`max_taker_fee_usd 0.1738` at the fee-maximising price) because the gate
bounds the worst case, not the expected one.

The maker rebate accrues at θ_m = 0.0125 per `pmus_fees.maker_rebate`, is
reported on its own line, and is **never netted** against a cost or added to
trading P&L. Nothing is income until the venue credits it, and a zero
commission on a fill row is not a rebate. The cap removes some maker fills and
therefore some accrual; that reduction is reported, not offset.

The corpus replay says L5's own behaviour rated at the PMUS schedule is
**−$1,020 net pre-rebate**. No result from this probe may be called profitable
on accrued-but-uncredited rebates.

## 7. The envelope is unchanged

`max_usd_per_market 5`, `max_abs_residual 10`, `max_total_exposure_usd 5`,
`max_concurrent_markets 1`, `max_live_orders 8`,
`daily_realized_loss_halt_usd 5`, `PMUS_RPS 1.5`, maker-first, one market at a
time, settlement-only realized labelling, no pair-cost ceiling. The cap is a
**tightening inside** that envelope — at a 0.90-contract clip it binds at 1.80
contracts, well inside `max_abs_residual 10` — and it can never loosen one,
because it sits behind every existing check and can only refuse.

## 8. Probe stop criteria, exact

**Hard stop — halt, disarm, do not relaunch, report:**

1. `|residual|` exceeds `k × clip` at any recorded point in any market.
2. A `RESIDUAL_ABOVE_CAP` refusal appears on an action classified `FLAT_OPEN`
   or `REDUCE_RESIDUAL`. Structurally impossible; seeing it means the gate
   moved.
3. Any existing envelope stop fires: realized loss ≥ $5, worst-case exposure
   > $5 in a market, more than one concurrent market, more than 8 live orders,
   `|residual| > 10`.
4. Any quarantined order, any corrupt market key, any `book_violations` entry.
5. A settled market whose ledger-derived change does not close **exactly**
   against the authoritative cash read, or whose difference is not explicitly
   attributable.

**Clean stop — stop, do not re-arm, report:**

6. Four markets settle with a complete per-order chain.
7. Action retention < 0.75 or contract retention < 0.70 over the probe.
8. The cap never binds across the whole probe. This is **not** a pass: it
   means the probe was uninformative about the mechanism and says so.

Losing money inside the authorized research-loss envelope is **not** a stop.

## 9. Exact diff from frozen `e2bd442`

Three files, +171 / −2, of which the executor path is two files:

| file | added | what |
|---|---|---|
| `src/l5_live_execution.py` | +49 | `max_clip` recorded on every fill; `InventoryStore.max_clip()`; `residual_cap_k` ctor arg defaulting to `None`; the gate, last in the overlay; `residual_cap_k` echoed in the existing `INVENTORY_CLASS` note |
| `src/l5_pmus_live_runner.py` | +43 | `residual_cap_k()` reading `PMUS_RESIDUAL_CAP_K`; wired into the one `FaithfulExecutor` construction; a `policy` block in the heartbeat carrying the name, k, session and per-market bind counts, and the filled clips |
| `src/l5_expectancy_replay.py` | +57 | `RunLength` and `LateNoExtend` analysis policies — **not on the execution path** |

No existing line of executor logic is modified. The only edits to existing
statements are two additive dictionary keys.

`src/l5_expectancy_replay.py` is not in the executor's `watchPatterns`; the
other two are, so a push of this change **will** redeploy the executor. It
must be deployed with `PMUS_RESIDUAL_CAP_K` unset (OFF) and verified disarmed
before k is ever set.

## 10. What is still outstanding before ARM

- Deploy DISARMED with the policy OFF and confirm the heartbeat reports
  `policy.name = "L5_NATIVE"`.
- Then set `PMUS_RESIDUAL_CAP_K=2`, redeploy **still disarmed**, and confirm
  the heartbeat reports `RESIDUAL_CAP_k2` and that the frozen decision stream
  is otherwise unchanged.
- Only then, with explicit authorization, an authoritative preflight PASS and
  a matching arm token.

<!-- 2026-10-15: watchdog deploy isolation pinned (watchPatterns ->
     deploy/l5_halt_watchdog/DEPLOY_TRIGGER, builder DOCKERFILE, start
     command pinned). This line exists to prove the isolation: it is an
     unrelated file, so pushing it must NOT redeploy the watchdog. -->
