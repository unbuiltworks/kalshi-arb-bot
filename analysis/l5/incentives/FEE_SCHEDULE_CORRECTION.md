# Fee-schedule correction — the stale taker constant, separately fingerprinted

**Lane: incentive accounting, 2026-10-07.** Code and analysis only. No order,
no signing, no Railway call, no git command. `analysis/l5/s3/**`, `live_exec*`,
`deploy/**`, `residual_frontier/**` and `action_scorecard/**` untouched.

## What was wrong

`src/pmus_incentive_audit.py` pinned

    TAKER_RATE = D("0.06")

Two defects, not one:

1. **It was stale.** PMUS republished the taker coefficient at **0.0695**.
2. **A scalar is the wrong shape.** This account's fill corpus straddles the
   change, and no single number can price a straddling corpus correctly. Had
   the scalar simply been bumped to 0.0695, every pre-change figure would have
   been silently re-rated — corrupting evidence that was correct when it was
   recorded. Understating a taker cost also *loosens* any bound derived from
   it, which is the direction that matters.

So the fix is a **frozen, effective-dated schedule**, not an edited constant.

## What was built

`src/pmus_fee_schedule.py` — a frozen schedule object with explicit
`effective_from` dates. A fill is priced by the schedule in force **at its own
timestamp** (`price_fill(..., fill_ts=...)`, which returns the theta and the
schedule entry it used).

| effective from (UTC) | label | taker theta | maker rebate theta |
|---|---|---|---|
| 2026-07-01T04:00:00Z | 12 AM ET, Wed July 1, 2026 | **0.06** | 0.0125 |
| 2026-09-17T04:00:00Z | 12 AM ET, Thu Sept 17, 2026 | **0.0695** | 0.0125 |

**Fingerprint (sha256 over canonical sorted-key JSON):**

    ecc240bd8a63b668e0bdbde872c6a3ebd9897b18781443bbeffd3ae61ca91629

Hard-coded as a literal `FINGERPRINT`, checked by `assert_frozen()`, following
`analysis/l5/live_exec5/spec.py`. The assertion is **not vacuous**: it does not
recompute its expectation from the object under test, and
`tests/test_pmus_fee_schedule.py::test_the_assertion_is_not_vacuous` mutates
the schedule and requires the assertion to fail.

`assert_frozen()` runs at import of `src/pmus_incentive_audit.py`, and
`assert_consistent_with_pmus_fees()` pins this schedule against the execution
path's own `src/pmus_fees.py` so the two cannot drift apart.

### An effective-date conflict, recorded rather than resolved

* **PRIMARY (used): 2026-09-17.** `analysis/pmus/pmus_published_fee_schedule.json`
  is our verbatim capture of `docs.polymarket.us/fees` — taker 0.0695, maker
  −0.0125, "12 AM ET, Thursday September 17, 2026" — plus the published 99-row
  price table, **every row of which reproduces exactly** from those two
  coefficients with banker's rounding (`tests/test_pmus_fees.py`).
* **SECONDARY: 2026-10-01.** The WebSearch summary recorded in
  `PMUS_INCENTIVES_CURRENT.md` Q1 (and in the task brief).

They disagree only about *when*, never about the coefficient. The schedule
takes the primary date, which is also the conservative one (higher taker cost
starts earlier, so no fill is ever priced below what the venue may have
charged). The secondary claim is kept in `SCHEDULE["discrepancies"]`, and a
fill in the 09-17 → 10-01 window is flagged `effective_date_ambiguous: true`
rather than quietly rated.

## Re-run: every figure that MOVED

`src/pmus_incentive_audit.py` used `TAKER_RATE` in exactly **one** place —
`size_arithmetic.taker_fee_at_l5_p50_size`, an illustrative figure (10
contracts at p=0.45), never a credited number.

| figure | before | after | note |
|---|---|---|---|
| `size_arithmetic.taker_fee_at_l5_p50_size` | **$0.15** | **$0.17** | now priced at the schedule in force *now* (theta 0.0695) |
| `size_arithmetic.taker_fee_at_l5_p50_size_by_schedule` | — | `{2026-07-01: $0.15, 2026-09-17: $0.17}` | **new**: both eras published side by side, so no era is silently re-rated |
| `size_arithmetic.fee_schedule` | — | provenance block incl. fingerprint | **new** |
| `size_arithmetic.source` | "0.06 / 0.0125 × C × p(1−p)" | "theta × C × p(1−p), theta from the frozen effective-dated schedule" | wording |

Nothing else in the module's output changed.

## Re-run: every figure that did NOT move

**The maker coefficient did not move** (−0.0125 before and after). Therefore:

| figure | value | moved? |
|---|---|---|
| maker rebates credited, account lifetime to 2026-09-14 | **$9.14** (737 of 1,174 fills) | **no** — OBSERVED credits, not modelled |
| liquidity rewards PAID | **$7.04** (3 transfers, 44 PAID rows) | **no** |
| taker rebates | **$0.00** | **no** (below the $250k first tier) |
| Volume Incentive | **$0.00** | **no** (taker-only eligibility; we are maker-only) |
| confirmed incentives | **$16.18** | **no** |
| `estimated_rebate_on_our_fills` | **$9.31** raw / rounded | **no** — maker theta unchanged |
| `rebate_at_our_size`, `rebate_at_l5_p50_size` | $0.00 / $0.03 | **no** |
| `min_shares_for_one_cent_rebate_at_p0.45` | **1.62** | **no** |
| `credited_on_fills.*` (`credited_to_us_usd`, `charged_to_us_usd`) | venue's own numbers | **no** — read from the venue, never modelled |
| break-even ceilings in `PMUS_INCENTIVE_BREAKEVEN.md` / `l5_incentive_breakeven.py` | — | **no** — they consume `MAKER_REBATE_RATE`, which is still 0.0125 |
| published max maker rebate | **$0.31 / 100 contracts** | **no** |

**Plainly stated: this correction changes no realized, credited or
settlement-labelled figure at all.** Our corpus is maker-only by owner rule
(`venue_charged_us_on_our_fills_usd 0.0000` across every audit), and the only
figure that moved is an illustrative taker-cost line. A correction that moves
no money figure is still worth making — the stale constant was a latent error
waiting for the first taker fill, and it is now impossible to use one number
for a corpus that straddles a schedule change.

Published max taker fee per 100 contracts at p=0.50, both eras, as a
cross-check against the venue's own table: **$1.50** at 0.06, **$1.74** at
0.0695. Both reproduce (`tests/test_pmus_fee_schedule.py`).

## Tests

`tests/test_pmus_fee_schedule.py` — 11 tests: the hard-coded fingerprint; the
freeze assertion is not vacuous; the coefficient is effective-dated, not
scalar; the maker coefficient and every maker-derived figure are unchanged; the
published max fees reproduce on both sides; the stale scalar is gone; the date
conflict is recorded and flagged; no disagreement with `src/pmus_fees.py`; the
rebate is a positive credit, never a negative fee; nanosecond venue timestamps
parse.

Regression: `tests/test_pmus_incentive_audit.py`, `tests/test_pmus_fees.py`,
`tests/test_three_economic_streams.py`, `tests/test_l5_realized_bridge.py`,
`tests/test_l5_incentive_breakeven.py`, `tests/test_pmus_mlb_program_scan.py`,
`tests/test_pmus_read_rate_limit.py`, `tests/test_l5_breakeven.py` —
all pass. `tests/test_r1_source_lineage.py` fails on a **pre-existing** drift
in `src/pmus_micro_executor.py` and `src/pmus_q1_executor.py`, neither of which
this lane touched.
