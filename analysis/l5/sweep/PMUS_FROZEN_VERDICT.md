# PMUS frozen sweep — preregistered verdict

`analysis/l5/sweep/` (frozen before collection) · tape closed **17:15:44Z** ·
**34,127 rows, 325 markets, 270 combinations** · manifest
`manifest_pmus_book_20260917.json`

**Collection note, disclosed not repaired.** The container restarted at ~17:16Z
and killed the armed `on_close` handoff before it fired. The tape itself
reached its scheduled end — `--minutes 150` from 14:45:33 gives 17:15:33, and
the last row is a well-formed `bbo` at **17:15:44Z**. The frozen analysis was
then run on the closed files unchanged. **Nothing was re-collected**, the
candidate grid, ranking, pair ceilings and denominator rule are untouched, and
both raw tapes are hashed in the manifest
(`d45dee47e9fc7277`, `4766640c2fba8ebc`).

## The result

20,180 EXTEND requests, identical across every placement. Suppression off,
gate off, residual cap off:

| placement | RESTABLE | would CROSS | **extend rejection rate** | complete requests | complete executable rate |
|---|---|---|---|---|---|
| **`pair_target`** (what the live probe used) | 4,168 | **9,478** | **0.4697** | 4,947 | 0.6450 |
| **`nearest_maker`** | **13,361** | **0** | **0.0000** | **14,598** | 0.3768 |
| `one_behind` | 13,196 | 0 | 0.0000 | 14,598 | 0.3768 |

*(completion rates shown at ceiling 0.96; they rise monotonically to 0.6909 /
0.4332 at ceiling 1.05.)*

**1. The live rejection problem is reproduced exactly and its cause is the
placement rule.** `pair_target` would have **47% of its EXTENDs rejected** —
9,478 of 20,180 are marketable, so post-only refuses them. The live cohort saw
13 rejected orders out of 47, **every one an EXTEND**. This is the same
mechanism at 325-market scale.

**2. `nearest_maker` and `one_behind` reject nothing.** Zero crossing extends
in 20,180 requests.

**3. The higher completion *rate* of `pair_target` is a denominator effect, and
the absolute counts invert it.** 0.645 × 4,947 ≈ **3,190** executable
completions for `pair_target` against 0.3768 × 14,598 ≈ **5,500** for
`nearest_maker` (≈6,300 at ceiling 1.05). Asking for three times as many
completions at a lower hit rate still yields **roughly twice** as many.

**4. `acquisition_gate: wait` is destructive.** Complete-executable rate
collapses **0.4033 → 0.0127** and requests halve (14,576 → 7,247). Waiting for
flat before acquiring removes the completions it was meant to protect.

**5. `extend_suppression` and `proposed_residual_cap` are INERT here.** Linear
and hard suppression withhold 13,862 and 14,179 extends respectively, and the
completion metrics do not move at all — identical `complete_executable_rate`
and `complete_requests` across every setting. In a book-aware replay these two
axes change what we ask for and nothing about what we could get.

**6. No completion would ever cross.** `complete_cross = 0` in all 270
combinations.

## What this does NOT say

**RESTABLE is not FILLED.** 13,361 restable extends are 13,361 *legal maker
orders*, not fills. The frozen diagnostic on the deepest market measured
`book_reached_quote` at 97–98% against `trade_flow_through_quote` at 46–51% —
roughly half the time the touch drifted through our price with **no print
there**. Nothing in this table is a fill, a pair, or a dollar.

It also says nothing about queue position, which is unpublished, and nothing
about whether `nearest_maker`'s three-times-larger completion request stream
would survive contact with the queue.

## Standing disposition

`nearest_maker` is **not promoted**. The prospective arms harness keeps the
placement axis **pinned** and `combine_with_placement()` refuses to move it
until this tape closed — which it now has, so that axis is unlocked for the
*next* prospective comparison, with inventory policy and placement still
recorded separately so either change remains attributable.
