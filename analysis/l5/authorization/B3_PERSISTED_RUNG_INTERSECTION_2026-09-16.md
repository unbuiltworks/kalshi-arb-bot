# B3 — a stored rung may only tighten the pin

**OWNER AUTHORIZATION 2026-09-16 "B3".** Implemented, tested, and deployed
HALTED. **HALT still present. No `live_mandate.json`. Nothing armed.**

## The invariant, as implemented

> A persisted historical rung may tighten the currently authorized/pinned
> configuration, but it may NEVER loosen, enlarge, or replace it.

`ladder.tightest(pinned, stored)` compares the **effective** value of every
risk dimension independently and keeps the tighter of each. Identity is always
the pinned rung's, so CP1 can be restricted, never renamed into R3L.

**Not a ladder-order comparison**, because the owner forbade one unless the
ordering is proven no-less-permissive — and it is not: `R2C2` allows
concurrency 2 where `R3L` allows 1, while `R3L` allows the larger per-market
cap. Neither dominates. `test_the_ladder_is_not_totally_ordered_so_name_order_cannot_be_used`
fails if that ever stops being true.

**Effective, not declared.** Every comparison runs on the value the accessors
actually yield, defaults applied. This is the load-bearing detail: CP1 states
no `target_notional_usd`, and absence there is not "no lever" — it is the
`$0.01` micro default, **250× tighter** than R3L's `$2.50`. Comparing raw dict
values would have read absence as unknown and let R3L's sizing through.

| dimension | CP1 (pinned) | R3L (stored) | effective | absent means |
|---|---|---|---|---|
| `per_market_cap_usd` | 5.00 | 7.50 | **5.00** | — |
| `residual_units` | 3 | 6 | **3** | 3 |
| `daily_loss_stop_usd` | −6.00 | −15.00 | **−6.00** | −1.00 (tighter ⇒ `max`) |
| `target_notional_usd` | absent | 2.50 | **0.01** | $0.01 micro (tightest) |
| `max_leg_notional_usd` | absent | 3.25 | **0.02** | $0.02 micro (tightest) |
| `max_unmatched_usd` | absent | absent | absent | **no cap (loosest)** |
| `markets_per_campaign` | 3 | 3 | **3** | continuous (loosest) |
| `pair_cost_max` | absent | absent | 0.98 | 0.98 |
| `concurrency` / `campaigns_per_day` / `max_actions_per_session` | 1 / 1 / 6 | 1 / 1 / 6 | **1 / 1 / 6** | — |
| `complement_pricing` | absent | absent | fee_safe | fee_safe (the meet) |

`absent` points in **both** directions depending on the lever, which is why
this is a table rather than a `min()` over the dicts.

## Scope, and one place it was deliberately NOT applied

The intersection applies to **durable campaign state** (`stored_state=True`),
the one path the owner named. Applying it to the **approval gate** as well was
implemented, tested, and reverted: it silently disabled the owner's
advance-by-approval mechanism (`test_campaign_applies_the_approved_rung_at_the_gate_and_restores_it`
went red), which is a behaviour change B3 did not authorize. The gate keeps its
own guard — `_approval_predates_mandate` refuses any approval that does not
POSTDATE the active mandate, which is exactly what stops the 2026-09-06
R2C/R2C2/R3L chain that caused the breach.

**It also requires an EXPLICIT pin.** With `PMUS_SPRING_RUNG` unset there is no
pinned configuration; `ladder.rung()` returns the R1 *default*, and
intersecting against a default is not protecting an authorization — it clamps
every campaign to R1. The probe image pins `CP1` explicitly, and
`test_the_protection_requires_an_explicit_pin_and_the_image_supplies_one`
fails if that ENV line is ever dropped, so the guard cannot lapse in silence.

Two further safeguards:

- **Unknown stored id.** `rung()` falls back to R1 for an unknown id — right
  for an env typo, wrong for stored state, where it would shrink the owner's
  $5.00 envelope to $0.05 without saying so. `known_rung()` returns `None`
  and the caller logs `stored_rung_unknown`; the pin stands.
- **Unclassified keys fail closed AND fail the suite.** A key not in
  `RISK_DIMENSIONS` / `UNORDERED_LEVERS` / `IDENTITY_KEYS` is never taken from
  stored state, and `test_every_rung_key_is_classified` goes red, so a new
  risk lever cannot enter unnoticed.
- **`rest_policy` / `rest_floor_s` have no proven ordering**, so the pinned
  value stands in both directions. Inventing an ordering to look complete is
  how a "tightening" silently loosens something.

## Tests

`tests/test_b3_persisted_rung_intersection.py`, **41 tests**:

- the production fixture `pinned=CP1, persisted=R3L` driven through
  `run_campaign`'s **armed restore** — asserting on `tightest()` alone would
  have passed while production still armed as R3L;
- assertions on what the **session layer** read, not only the report, and
  every such loop asserts the observation list is **non-empty** (a `for` over
  an empty list passes vacuously and would look identical to success);
- the inverse: a genuinely tighter stored rung (R1, R5FT) still tightens;
- **restart/resume**, including re-seeding R3L on every boot for three boots;
- a sweep of **every (pinned, stored) pair in the ladder × every dimension**;
- `min(rung, mandate)` preserved and a tighter mandate still clamping;
- `pmus_live_mandate.py` free of every B3 symbol.

Full regression suite: **4931 passed, 27 skipped, 1 xfailed, 0 failed.**

## Verified on the halted container

```json
"persisted_rung": "R3L",
"after_restore": {
  "pinned_rung": "CP1", "stored_rung": "R3L", "stored_rung_recognized": true,
  "effective_rung": "CP1", "per_market_cap_usd": "5.00", "residual_units": 3,
  "daily_loss_stop_usd": "-6.00", "target_notional_usd": "0.01",
  "max_leg_notional_usd": "0.02", "markets_per_campaign": 3,
  "concurrency": 1, "max_actions_per_session": 6,
  "tightened_by_stored": [], "no_dimension_loosened": true
}
```

The halted process now computes the SAME intersection the armed path will, so
a pin/stored mismatch is visible **before** the halt is lifted rather than
after. Before B3 this disclosure said CP1 while the armed process would have
run R3L.
