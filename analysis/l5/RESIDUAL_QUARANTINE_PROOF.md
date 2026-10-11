# Residual quarantine: implemented, mutation-proven, replayed. Not enabled.

OWNER GO 2026-09-10: *"implement and prove residual quarantine, but do not
activate it on the current live campaign."*

**Status: SHIPS DARK.** `PMUS_RESIDUAL_QUARANTINE` defaults OFF, nothing is
wired into the live session, and the live probe `7ed717fc` (`a88dff63`) is
untouched and still holding. Activation waits for a clean settlement
boundary. 2,400 tests pass.

---

## What it changes: exactly one thing

A market that is **dormant** — zero resting orders, inventory venue-confirmed
— stops occupying the single active **acquisition** slot.

Active acquisition concurrency stays **1**. Quarantine is not a route to
raising it.

## What it does not change — each enforced by a mutation test

| invariant | test that fails if removed |
|---|---|
| stays in `held`, continuously | `test_MUTATION_dropping_a_quarantined_residual_from_held_changes_the_budget` |
| `held` cannot be filtered by state | `test_MUTATION_held_inventories_cannot_be_filtered_by_state` |
| full conservative downside in `combined_loss`, undiscounted | `test_MUTATION_full_conservative_downside_is_carried_undiscounted` |
| still blocks admission at the limit | `test_MUTATION_a_quarantined_market_still_blocks_admission_at_the_limit` |
| settlement monitoring is a **precondition** | `test_MUTATION_settlement_monitoring_is_a_precondition_not_a_consequence` |
| complement management survives | `test_MUTATION_complement_management_survives_quarantine` |
| no dollar cap rises, no exposure forgiven | `test_quarantine_raises_no_cap` |

### The mutations were actually run, not just written

| mutation applied to the module | result |
|---|---|
| drop `QUARANTINED` from `held_inventories` | **6 failed** |
| halve the dormant floor in `combined_loss` | **4 failed** |
| stop requiring settlement monitoring | **2 failed** |
| allow a resting order to remain | **2 failed** |
| *(restored)* | 22 passed |

## The gate — the owner's expression, in the existing machinery

```
combined_loss(realized, ALL held incl. quarantined)
  + worst_case(proposed acquisition)   >   daily_loss_stop()
```

Strictly greater than, matching `combined_budget_tripped`'s inclusive
boundary: at exactly the stop, the stop has fired. Existing R5F entry gates
still apply on top. A positive "worst case" is **refused**, not negated — a
worst case that is a gain would buy headroom.

> The gate reads the **active rung**, not a hard-coded −$15. Under the test
> suite's default −$1.00 rung the same proposal is refused, and a test pins
> that. This is the second time assuming −$15 has caught me; the constant
> lives in the ladder, not in this module.

## Replay of the county-cricket stall

**What actually happened:** 20 markets in 93 minutes, then at 14:59:56Z a
residual in `aec-county-not-ham-2026-09-08` — a multi-day fixture whose book
sat at 0.77/0.84, **outside** the 0.25–0.75 band, so the engine could not
work the position it was guarding. By 20:20:06Z: **5h20m, zero further first
legs.** For a $5.70 position with a −$0.8015 guaranteed floor.

Cadence is the one observed *before* the stall (12.9 markets/hour). Two arms,
because one alone would misdescribe itself:

| | opportunity slots | **additional markets admitted** | final combined loss | gate refused at |
|---|---:|---:|---:|---|
| actual (no quarantine) | 68 | **0** | −$0.8015 | — |
| **empirical** | 68 | **9** | −$11.9099 | attempt 10 |
| **pessimistic** | 68 | **1** | −$8.3015 | attempt 2 |

**Empirical** replays the real end-state inventories this campaign produced
today — completions and strandings in the proportion they actually occurred.
**Pessimistic** assumes nothing ever completes and every market strands
one-sided at the full $7.50 cap; that arm is the true lower bound.

So the answer is a range: **1 to 9 additional opportunities**, and which end
depends entirely on completion rate.

### The gate refused correctly in both arms

```
empirical    −11.9099 + −3.9028 = −15.8127  vs stop −15.00  → REFUSED
pessimistic   −8.3015 + −7.5000 = −15.8015  vs stop −15.00  → REFUSED
```

Neither arm breaches. In both, the final carried loss stays inside −$15.

### The finding that matters most

**68 opportunity slots existed; the budget stopped acquisition long before
the clock did.** That is the argument for a portfolio gate over a concurrency
dial: the constraint becomes *how much worst case is outstanding*, not *how
many markets are open*. Raising concurrency 1 → 5 would have permitted five
strandings; this permits one more only while the portfolio's worst case still
fits the same budget that governs today.

### And the mechanism by which completing buys capacity

In the empirical arm, 6 of the 9 admitted markets consumed **zero** budget —
a completed pair below par has floor `min(up,dn) − spent ≥ 0`. Completion is
not merely profitable; it is what returns budget headroom. That is why the
empirical arm fits 9 and the pessimistic arm fits 1.

## Correction to my own first draft

The replay's first version claimed *"no acquisition is credited with
completing… deliberately pessimistic."* It was replaying real end-state
inventories, three of which are fully matched and cost the budget nothing.
That is an **empirical** replay, not a pessimistic one, and describing it as
pessimistic overstated the result. Both arms are now reported separately.

Similarly, `may_complete_in_quarantine` first called a `pair_cost_after_ok`
that does not exist, behind a `hasattr` guard that defaulted to `True` — a
ceiling check that always passes, in the one function whose job is refusing
an above-par completion. It now binds `pair_invariant_ok` directly, so a
missing predicate breaks the import instead of quietly approving.

## Not done

- **Not enabled.** Dark by default; no live wiring.
- **Not deployed.** Waiting for the held residual to reach a clean
  settlement boundary, per instruction.
- No cap, ceiling, band or concurrency change.

Raw both-arm output: `analysis/l5/quarantine_stall_replay.json`.
