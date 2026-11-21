# CP1 states its own action size

**OWNER AUTHORIZATION 2026-09-16, "CP1 explicit sizing fix".**
`target_notional_usd: "0.10"`, `max_leg_notional_usd: "0.125"`.

## The defect this closes

CP1 named neither lever, and absence is not "no lever" — it is
`DEFAULT_TARGET_NOTIONAL $0.01` / `DEFAULT_MAX_LEG_NOTIONAL $0.02`.
`market_micro_spec()` reads those and `choose_micro_quantity()` turns them into
the submitted quantity (`qty = spec["qty"]`, `pmus_spring_l5_session.py:3500`
OPEN/EXTEND and `:3714` COMPLETE). The probe sized at **$0.0090 per action**
and ~$0.054 per market against a $5.00 authorization: Option A raised the
**cap** but not the **spend**.

## Derived from frozen evidence, not picked

L5's own per-fill sizes, from the frozen historical record:

| price band | L5 median qty | × k | L5 median $ | × k | L5 p75 $ | × k |
|---|---|---|---|---|---|---|
| 0.2–0.3 | 8.84 | 0.0547 | $1.8820 | $0.0116 | $5.1489 | $0.0319 |
| 0.4–0.5 | 12.0 | 0.0743 | $1.8820 | $0.0116 | $5.1489 | $0.0319 |
| 0.5–0.6 | 14.56 | 0.0901 | $7.7479 | $0.0480 | $24.3025 | $0.1504 |
| 0.9–1.0 | 25.0 | 0.1548 | $23.5465 | $0.1458 | $46.0000 | $0.2847 |

Sources: `l5_behavior.json` `q5_fill_size_by_price_band` (71,982 buy fills) and
`l5_analysis_stats.json` `q1_size_vs_price`. Scale factor is the authorized
**k = 0.00619, unchanged**.

The chosen values put our action **inside** that k-scaled distribution
(between L5's scaled median and p75) rather than outside it.

**Shape preservation, stated precisely.** The mechanism has two scalars, so it
cannot reproduce L5's price-dependent size *curve* — L5's dollar size rises
with price, a fixed dollar target does not. What is preserved is the property
that actually matters to the L5 thesis: an action is a **fraction of desired
inventory**, so `OPEN → EXTEND → EXTEND → COMPLETE` survives instead of one
action jumping to target. Our action is 0.11–0.22 contracts against a scaled
desired inventory whose shadow median is ~1.1–1.55 contracts and p90 ~5.0.

## Smallest practical, as required — the alternatives were computed

| target | max_leg | q_max | worst leg | 6 actions | resid overshoot | × micro @0.45 |
|---|---|---|---|---|---|---|
| 0.05 | 0.06 | 0.10 | $0.0600 | $0.36 | $0.10 | 5.0× |
| 0.08 | 0.10 | 0.17 | $0.0996 | $0.60 | $0.17 | 8.5× |
| **0.10** | **0.125** | **0.22** | **$0.1248** | **$0.7488** | **$0.22** | **11.0×** |
| 0.15 | 0.20 | 0.34 | $0.2000 | $1.20 | $0.34 | 16.5× |
| 0.25 | 0.40 | 0.64 | $0.4000 | $2.40 | $0.64 | 27.5× |

`$0.10/$0.125` is the smallest that clears "materially exceeds the ~$0.01
micro regime" while keeping **11–22 lots** of the 0.01 venue minimum — the
floor at which a *partial* fill and a queue position are observable at all.
Two lots, under the micro default, are not.

## The five requirements, measured

| price | qty | entry $ | worst leg $ | actions to 3 contracts | actions to $5 cap |
|---|---|---|---|---|---|
| 0.10 | 0.11 | $0.0110 | $0.0990 | 27.3 | 50.5 |
| 0.25 | 0.16 | $0.0400 | $0.1200 | 18.8 | 41.7 |
| 0.45 | 0.22 | $0.0990 | $0.1210 | 13.6 | 41.3 |
| 0.90 | 0.11 | $0.0990 | $0.0990 | 27.3 | 50.5 |

1. **Materially exceeds micro** — $0.0990 vs $0.0090 at $0.45, **11.0×**.
2. **Several sequential decisions before the cap binds** — 41–50 actions to the
   $5.00 cap; the **residual ceiling binds first**, at 13–27 actions. The cap
   is an outer bound, not the operating constraint, so the *controller*
   decides. This matches the shadow, where `residual_cap_rate` is 0.10–0.17
   and `exposure_cap_rate` is 0.0000.
3. **No single action dominates** — worst leg $0.1248 = **2.5%** of $5.00.
4. **Compatible with the 3-contract/$3 ceiling** — see the overshoot note.
5. **Venue rounding** — every quantity on the 0.01 lot grid, swept over all 99
   ticks; no `MICRO_BAD_NOTIONAL_BOUNDS` and no `MICRO_MIN_LOT_EXCEEDS_MAX`.

**Worst-case six-action cumulative spend: $0.7488 — 15.0% of the $5.00 cap.**
Bounded analytically, not only by sampling: `choose_micro_quantity` validates
`qty*p <= max_leg` *and* `qty*(1-p) <= max_leg`, so cash at risk per action can
never exceed `max_leg` at any price; six of those is 6 × $0.125 = $0.75.

## The one place sizing touches a risk boundary — disclosed

Both the decision gate (`abs(behavioral_residual()) >= max_residual_units()`,
`pmus_spring_l5_session.py:3190`) and the wire guard (`resid > max_residual`,
`pmus_live_mandate.py:492`) test the residual **already held**, not the
residual the proposed order would create. So one action may cross the ceiling,
and the overshoot is exactly one action's contracts. Unmatched inventory is
priced at **$1/share** by `_residuals()`, so contracts and worst-case dollars
are the same number:

| | max qty/action | worst-case residual |
|---|---|---|
| inherited micro sizing (before) | 0.02 | **$3.02** |
| $0.10 / $0.125 (now) | 0.22 | **$3.22** |

**This is pre-existing, not introduced here** — the $3.00 ceiling was already
exceedable by one action's worth. What changed is the magnitude, from $0.02 to
$0.22, because the overshoot scales with action size. It is self-limiting: once
residual exceeds $3.00 the wire guard refuses every further order in that
market. It stays far inside the $15.00 campaign and $6.00 daily bounds.
`test_the_residual_overshoot_is_bounded_and_disclosed` pins both numbers, so a
later sizing change cannot widen it quietly.

## B3 preserved exactly

`pinned CP1 + tighter permissible persisted state + immutable mandate`, no
dimension loosened. With R3L on the volume the effective sizing is CP1's
$0.10/$0.125, not R3L's $2.50/$3.25 (25× and 26× larger). A genuinely tighter
stored sizing still tightens.

## Tests

`tests/test_cp1_explicit_sizing.py` (18) covers all seven the owner required.
Three tests in `tests/test_b3_persisted_rung_intersection.py` were **restated,
not deleted**: they asserted the inherited $0.01/$0.02, which is precisely what
this authorization replaced. They now assert against CP1's *own declared*
values rather than literals, so they keep testing the real property across
future authorized sizes.

Full regression suite: **4949 passed, 27 skipped, 1 xfailed, 0 failed.**
