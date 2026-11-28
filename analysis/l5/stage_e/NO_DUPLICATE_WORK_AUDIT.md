# ALREADY BUILT / BROKEN / MISSING / NEXT FIX

Audit against the canonical pipeline, issues #167/#189/#192, the preserved
live tapes and the deployed service. No live configuration, authorization,
capital limit or deployment mode changed.

## Campaign P&L reconciliation, from the venue's own lines

**Oct 24 preserved campaign** (`analysis/l5/residual_exit/preserved/*.ndjson`,
991 records, 7 windows, `L5_LIVE_SETTLED`):

| window | up | dn | matched | matched P&L | residual | residual P&L | realized |
|---|---|---|---|---|---|---|---|
| 0100z | 1.04 | 1.04 | 1.04 | +0.0416 | 0 | 0 | **+0.0416** |
| 0115z | 0 | 0.96 | 0 | 0 | 0.96 | −0.2304 | **−0.2304** |
| 0130z | 1.27 | 1.27 | 1.27 | +0.1905 | 0 | 0 | **+0.1905** |
| 0145z | 0 | 1.03 | 0 | 0 | 1.03 | −0.3605 | **−0.3605** |
| 0200z | 1.22 | 1.22 | 1.22 | +0.0732 | 0 | 0 | **+0.0732** |
| 0215z | 1.25 | 0 | 0 | 0 | 1.25 | +0.8625 | **+0.8625** |
| 0230z | 1.16 | 1.16 | 1.16 | +0.0812 | 0 | 0 | **+0.0812** |
| **total** | | | | **+0.3865** | | **+0.2716** | **+0.6581** |

**4 of 4 completed pairs were profitable; zero losing pairs.** All losses are
stranded legs. Orders: 42 pre-submits, 20 terminal, 54 `ORDER_PROGRESS`.

Across all three campaigns on record the same invariant holds:

| campaign | pairs | residual | realized |
|---|---|---|---|
| Oct 24 (above) | **+$0.3865** | +$0.2716 | +$0.6581 |
| Campaign #2 (issue #192) | **+$0.8671** | −$2.3255 | −$1.4584 |
| Stage A, Oct 30 | **+$0.1246** | −$0.8537 | −$0.7291 |

**Matched pairs are positive in every campaign. Residual is the only
negative component.** This is settled evidence and is not re-litigated.

### Refusal census, Oct 24 (and whether each was justified)

| refusal | n | justified? |
|---|---|---|
| `PAST_ENTRY_CUTOFF` | 344 | yes — window past entry |
| `COMPLEMENT_PAIR_ABOVE_CAP` | 128 | **yes** — median pair 1.16; 142 of 156 in the prior study were ≥ $1.00, i.e. a certain loss |
| `PAST_COMPLETE_CUTOFF` | 74 | yes |
| `PAIR_UP:CASH_COMMITMENT` (~2.64 > 1.65) | 65 | yes, by the approved deficit bound |
| `SAFETY_OVERLAY:MAX_LIVE_ORDERS` | 54 | see below |
| `PAST_TERMINAL` | 24 | yes |
| `TOO_LATE_TO_WITHDRAW` | 13 | yes |

**Would more order capacity have helped? No.** In the prior complement study
only **6 of 231** complement-stage ticks were blocked by `MAX_LIVE_ORDERS`
(`COMPLEMENT_ADOPTION_VERIFICATION_20261027.md`). More capacity buys more
*first legs*, and the first leg is the only consistently loss-making
component. It would have increased stranded exposure.

## ALREADY BUILT — do not rebuild

| capability | code | evidence |
|---|---|---|
| Complement adoption from the current book, superseding the frozen plan | `src/l5_live_execution.py:6818` | `COMPLEMENT_ADOPTION_VERIFICATION_20261027.md`: 143 of 143 reachable ticks evaluated the complement; **zero** previously refused decisions become newly actionable |
| Completion rescue past the 0.96 opening cap, on `completion_ceiling` | `src/l5_two_sided_probe.py:2372-2392` | live caller passes `residual_budget_usd` from `max_worst_case_deficit_usd` = 1.50, so the rescue is live, not inert |
| Depth / market / side assertions | `COMPLEMENT_DEPTH_INSUFFICIENT`, `COMPLEMENT_MARKET_MISMATCH`, `COMPLEMENT_NOT_OPPOSITE_SIDE` | 48 tests, same doc |
| **Repeated accumulation in one market** (issue #189's ask) | round allocations (`ts_round_allocations` = 4), `acc_leg_attempt` | Stage A **2115z: 9 submissions, 5 fills, 2 completed pairs in one market**. #189's named blocker (`probe_store_plan` freezing one plan) no longer binds |
| Sticky completion retry / stale recovery | `L5_ACC_RECOVER` | `COMPLEMENT_CANCELLATION_CHECK.md`, 4 firings traced |
| Exact-order cancellation + reconciliation | `L5_LIVE_LIFECYCLE`, `L5_LIVE_RECONCILE` | 58 + 15 records in the preserved tape |
| Full-depth book + private order lifecycle recording | `L5_SHADOW_RESIDUAL_BOOK_SAMPLE` (8 rungs/side), `L5_FILL_CALIB_PRESUBMIT`/`TERMINAL` | 1,329 samples on hand |
| Maker/taker + residual-risk experiments | `COMPLEMENT_CANCELLATION_CHECK.md` (post-cancel bid-path rule-out), 48-window held-out in #192 | all four held-out arms negative |

**Two conclusions reused rather than re-tested:**

1. **The Oct 24 cancellations cost nothing.** The prior check traced the
   post-cancel bid path — 0115z cancelled at own 0.70, path minimum **0.73**;
   0145z cancelled at 0.61, minimum **0.65**. Neither would have filled.
   I will not repeat that experiment.
2. **The Oct 24 −$0.5909 was decided by the pair cap against a moved book**,
   i.e. entry selection, not the completion path. "It is the first leg's fill
   that was selected against."

## BROKEN — one defect, and it is the largest demonstrated avoidable loss

`src/l5_book_completion.py`, introduced by commit **`234189cf`**:

```python
own = px if want == "UP" else (D(1) - px)      # WRONG
```

`leg["price"]` from `complement_order` is **already the own price**
(verified: own `0.09` / `wire_price` `0.91` on a 0.90/0.91 book, summing to
1.00). That value fed **two** gates:

1. `_would_cross(want, own, ...)` — which takes an own price and converts
   internally, so DOWN was converted twice and tested `own <= bid` instead
   of `wire <= bid`. A DOWN complement resting at the touch is a YES offer
   **at the ask** — a maker order that cannot cross — and was refused
   `WOULD_CROSS` anyway.
2. `pair = avg + own` — so a DOWN complement's **pair cost** was
   `avg + (1 - px)`. On the 2130z state that is `0.46 + 0.91 = 1.37`
   instead of `0.46 + 0.09 = 0.55`, independently tripping
   `PAIR_ABOVE_REDEMPTION`.

**Every DOWN completion on Route B had two independent spurious refusals in
front of it.** UP was unaffected (the conversion is the identity), which is
why the windows that *did* complete were all UP completions and the defect
survived. Stage A recorded `book_completion:WOULD_CROSS` **41 times**.

**Demonstrated cost — Stage A 2130z**, which held UP 2.05 at a 0.46 basis,
needed DOWN, and settled **−$0.9430 stranded**:

| book | verdict after the fix | pair cost | locked P&L |
|---|---|---|---|
| 0.44/0.45 (at the fill) | refuse `PAIR_ABOVE_REDEMPTION` | 1.01 | −0.0205 (correctly refused) |
| 0.55/0.56 | **COMPLETE** | 0.90 | **+0.2050** |
| 0.69/0.70 | **COMPLETE** | 0.76 | **+0.4920** |
| 0.80/0.81 | **COMPLETE** | 0.65 | **+0.7175** |

Under the defect all four were refused. The engine's own policy said
complete; a unit error refused it.

**The test suite encoded the bug.** `tests/test_l5_route_b_book_completion_hook.py`
used a 0.20/0.21 book and described it as "a DOWN complement at own 0.23 and
a pair of 0.63". On that book DOWN's touch is own **0.79** and the pair is
**1.19**; 0.23/0.63 is the double-converted value. The fixture is corrected
to 0.79/0.80, where the DOWN complement really is own 0.20 and the pair
really is 0.60.

## MISSING — nothing new is required

Explicitly **not** missing, each refused on its own evidence:

- **Crossing authorization.** 91% of the Oct 24 complement refusals were at
  pair ≥ $1.00 — a certain loss. The maker quote did not "fail"; the
  economics did. Stage D separately measured maker completion as feasible
  (69.7% swept, median 27 s) and **better than crossing by $0.0167/contract**.
- **More order capacity.** 6 of 231 complement-stage ticks. It adds first
  legs, which is the loss-making component.
- **A longer completion cutoff.** Fill latency is median 27 s / p90 117 s,
  against 180 s of runway already left at the 720 s cutoff.
- **A new accumulation mechanism.** Built; 2115z completed 2 pairs in one
  market.

**Genuinely open, and not addressed by this fix: first-leg selection.** The
residual is the only negative component in all three campaigns, it is
adverse selection at entry, and the 48-window held-out in #192 found all
four arms negative. No validated opening rule exists. That is the next real
question, and it is not a completion problem.

## NEXT FIX — done, and it is the whole change

One line in `src/l5_book_completion.py` (`own = px`), with the reasoning
recorded in place, plus **18 focused regression tests** in
`tests/test_l5_completion_would_cross_units.py` pinning: the unit contract,
that a DOWN quote at the touch does not cross, the defect's exact false
positive, that genuine crosses are still refused, that the DOWN pair cost is
`basis + own`, the 2130z state end to end, and that a genuinely dear pair is
still refused.

**Baseline parity: 54 failed / 14,716 passed / 26 errors, failure set
BYTE-IDENTICAL to baseline.** 152 existing completion tests pass unchanged.

No live configuration, authorization, capital limit, cutoff, cap or
deployment mode was touched. The executor remains disarmed and crossing
remains unauthorized.

## What this does NOT claim

- The fix removes a spurious refusal. **Whether the resulting maker order
  fills is a separate, unmeasured question** for at-the-touch completions
  (Stage D measured 69.7% only for the sub-0.25 case). The +$0.2050 to
  +$0.7175 figures are locked-P&L-if-filled, not realized.
- It does not address the residual, which is where every campaign's losses
  are.
- 2130z is one window. The defect's *mechanism* is general to DOWN
  completions; its dollar impact across campaigns is not quantified here
  because most recorded windows held DOWN and needed UP.
