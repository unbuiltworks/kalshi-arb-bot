# PREREGISTRATION — residual-rescue completion band (1.00, 1.03]

**Frozen 2026-09-09, before the instrumentation existed and before any
rescue-band observation was taken.** Written from the shadow's known fields.

Owner GO: *"Only after a real first-leg fill, the candidate may rescue residual
inventory with completed pair cost $1.00 < pair cost ≤ $1.03. It is not allowed
to initiate trades targeting $1.02 pairs. It's an emergency completion
mechanism."* Shadow/counterfactual first; **no live probe** authorised by this
document.

## 1. What is being tested

Whether **executable flow actually exists** in the band our 1.00 hard cap
currently forbids.

Not whether it would be profitable. Not how many completions it would recover.
Only: *does the complement's ask reach a rescue price, with a trade printing at
that moment, in cases where it never reached our capped price?*

## 2. The measurement

At every poll of an open window, with `L` = the hypothetical first-leg price:

```
cap_price     = 1.00 - L      # the best price the current rule permits
rescue_price  = 1.03 - L      # the best price the candidate would permit
```

A **capped opportunity** is: the complement's ask reached `cap_price` AND a
print occurred in the same poll.
A **rescue opportunity** is the same test at `rescue_price`.

Because `rescue_price > cap_price`, every capped opportunity is also a rescue
opportunity. The quantity of interest is the **increment**:

```
incremental = rescue_opportunities - capped_opportunities
lift        = rescue_opportunities / capped_opportunities
```

Both are counted from the **same polls of the same windows** — a paired design,
not two cohorts, so market selection cannot differ between arms.

**This is an UPPER BOUND on opportunity, never a fill.** A reachable price with
a contemporaneous print is not a fill: queue position and depth are unmeasured.
Standing rule from `PREREG_EXECUTION_QUEUE_FAILURE.md` §8.1 applies unchanged —
no count here converts into recovered completions, and
`recoverable_completions()` still raises.

## 3. Decision rule — bars sized to the decision, not to a row count

The pre-entry-drift test died with a power bar too weak to separate its own
threshold from the null. That defect is not repeated.

* **R1 — power.** At least **30 capped opportunities** must accumulate, so the
  lift has an estimable denominator. Below that: **UNDERPOWERED**, report the
  deficit, compute no lift.
* **R2 — the effect must clear the null.** Lift ≥ **1.20**, **and** the lower
  bound of its 95% CI must exceed **1.00**. A point estimate above 1.20 whose
  interval includes "no difference" is a FAIL, not a pass. This is the
  clause that the drift prereg lacked.
* **R3 — the band must be non-empty.** At least **10** incremental
  opportunities must fall strictly inside (1.00, 1.03]. If the increment is
  zero or trivial, the rule is **inert on PMUS** whatever L5 does, and that is
  a complete answer.

All three must hold. **PASS** stages a bounded live probe design for owner
approval; it does not authorise one.

## 4. Kill condition

If R2's interval includes 1.00, or R3 is unmet, the rescue band **does not
demonstrably unlock flow on PMUS** and the candidate is dead — regardless of
how well it fits L5's history. L5 doing it is the reason to test it, never
evidence that it works here.

No re-cutting of the band, no alternative thresholds, no second lookback. One
band, (1.00, 1.03], as the owner specified.

## 5. What stays fixed while this runs

* **Maker-only.** L5's 15.4% taker behaviour is NOT adopted. One change at a
  time, as directed.
* **Rescue only, never initiation.** The band is reachable only when a
  hypothetical first leg is already held. It may never open a position.
* **1.03 is a ceiling, not a target.** The candidate rests at the cheapest
  price that completes, exactly as now; the band only widens what is legal.
* Above 1.03 stays locked (incentive-era L5 behaviour, not source-confirmed
  for our account).
* Rebates justify ~1.003 and are not counted toward anything above that.

## 6. Accounting

Nothing here is a fill. The shadow places no orders and holds no inventory.
`pair_cost_max()` continues to return ≤ 1.00 on every live path; the candidate
ships **default-OFF** and cannot be enabled by this document.

Standard unchanged: **filled → settled → realized.**
