# S3 readiness record — built, tested, launch blocked

**Frozen spec:** `analysis/l5/s3/spec.py`, commit **28c619df**, fingerprint
**`8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df`**.
Verified unchanged; `assert_frozen()` runs at instrument construction, in
the test suite, and as the CI job's first step.

**Nothing was altered on the strength of the historical studies completed
since the freeze.** Reservation 0.96, residual cap 80, quantity 20,
withdrawal logic, cohort size 25, catastrophic gates and fill standards are
all exactly as frozen. Every parameter is **read from the spec** in code
rather than restated, so protocol and run cannot drift.

**S3 is not a claim that 0.96 reproduces L5.** Running pair-cost control was
**REFUTED**; 0.96 is our own pair-economics constraint, labelled
`PAIR_ECONOMICS` in the spec.

## Status

| | |
| --- | --- |
| instrument | **built** (`instrument.py`) |
| cohort runner + frozen gates | **built** (`cohort.py`) |
| synthetic verification generators | **built** (`synth.py`) |
| tests | **71 passing** (`tests/test_s3_instrument.py`, plus august_replay) |
| recorded-tape run | **passes** (real PMUS tape, 2 markets) |
| synthetic two-sided dry run | **passes**, all 10 metrics populate |
| safety gates | **verified** |
| **prospective 25-market launch** | **BLOCKED — see below** |

## Markouts: the central instrumented variable

Ambient pair geometry explains ~0.98 and running pair-cost control is
refuted, so execution quality and adverse selection are the unresolved
economic question. Markouts are instrumented accordingly:

- horizons **+1 / +5 / +10 / +30 / +60 s**, exactly as preregistered;
- **mark source is the best BID**, not the mid — the bid is the exitable
  side for a long position and needs no ask, and asks are precisely what
  the crossed-book defect corrupts;
- every horizon carries its own `markout`, `mark_price`, `staleness_s`,
  `observation_ts`, `crossed_at_mark` and `horizon_fully_elapsed`;
- a horizon with no observation at or after the target is **None with a
  reason** and is **excluded from aggregates**, never carried forward;
- markout blocks are **labelled by ledger** so EVIDENCED and UNCERTAIN are
  never pooled.

Dry-run output shows negative markouts (−0.035 mean) on a synthetic
trade-through, i.e. the instrument detects adverse selection as intended.

## Safety — zero capital by construction, not by discipline

- `assert_no_execution_surface()` **aborts** if any signing/executor module
  is in `sys.modules`; tested in both directions.
- a test asserts the source contains no `place_limit_buy`, `sign(` or
  `private_key`.
- every record reports `orders_submitted: 0`, `orders_signed: 0`; the gate
  treats a non-zero value as **ABSOLUTE** severity.
- the CI job uses **read-only public endpoints**, contains **zero**
  `secrets.` references, and runs the fingerprint check and full test suite
  *before* collecting anything.

## Three bugs the tests caught

1. **Markout horizon falsely "elapsed."** `gap_ok = bool(after) or stale <= k`
   marked a horizon elapsed when the data ended 58 s before the target, so a
   **pre-move price would have been reused as the mark** — understating
   adverse selection, the exact quantity S3 exists to measure. Now requires
   an observation at or after the target, and non-elapsed horizons are
   excluded from aggregates rather than pooled.
2. **The reservation was dead code in the runner.** `run_market` fed one
   outcome per `step()`, so the complement was always unknown and S3's core
   mechanism never evaluated. Rows are now grouped by timestamp with the
   latest state of **both** sides carried forward, bounded by the venue's
   existing freshness constant.
3. **Zero-fill misclassification.** A reservation block was being called an
   instrumentation failure. See the structural finding below.

## Structural finding, before any launch

On PMUS a single slug carries both sides: buying UP at `best_bid`, buying
DOWN at `1 − best_ask`. Resting at **both** touches therefore costs

```
pair cost = best_bid + (1 − best_ask) = 1 − spread
```

| spread | pair cost at both touches | vs frozen 0.96 |
| --- | --- | --- |
| 0.005 (1 crypto tick) | 0.9950 | **blocked** |
| 0.010 | 0.9900 | **blocked** |
| 0.020 | 0.9800 | **blocked** |
| 0.040 (8 ticks) | 0.9600 | passes |
| 0.080 | 0.9200 | passes |

**The frozen 0.96 reservation requires a spread of ≥ 0.04.** At typical
one-tick crypto spreads it will stand down rather than place.

This is **not** a reason to move the reservation, and it has not been
moved. It is a prediction the instrument will test, and if it holds, *"the
venue did not offer a qualifying pair"* is a substantive answer to S3's
question about available maker execution.

Accordingly, `classify_zero_fill` now distinguishes:

| observation | classification |
| --- | --- |
| no orders, **no** reservation stand-downs | `INSTRUMENTATION_FAILURE` |
| no orders **because** the reservation blocked every attempt | `VALID_ZERO_FILL_EVIDENCE` + `zero_orders_because_reservation_blocked: true` |
| orders placed, rested, queue observable, still no evidenced fill | `VALID_ZERO_FILL_EVIDENCE` |
| no snapshots / orders never rested / queue never observable | `INSTRUMENTATION_FAILURE` |

In **every** case `cohort_restarted_or_extended: false`. A gate breach
halts and is reported as a measurement result, never as grounds for
retuning a parameter.

## Disclosed implementation choice

The frozen gate names `bbo_staleness_breaches_share_above: 0.10` but does
not define the freshness bound. Rather than invent one, S3 reads the
repository's existing venue constant
(`src/l5_surface_exec/adapter.py`, `MAX_BBO_AGE_S = 2.0`). **This is not a
frozen-spec parameter**; it is a pre-existing constant used unchanged, and
it is recorded here because it is a judgement the spec did not fix.

## Why the cohort has not launched

Two independent blockers, neither of which I can clear without your
decision:

1. **Network.** This container's policy denies `gateway.polymarket.us`
   (403 at the proxy CONNECT). S3 cannot reach the live read-only feed from
   here.
2. **CI dispatch.** `.github/workflows/s3-cohort.yml` is built, valid and
   pushed to the research branch, but `workflow_dispatch` requires the
   workflow file on the **default branch**, and I do not push to the
   default branch without explicit permission.

Either unblocks a launch:

- **allow `gateway.polymarket.us`** in the environment's network settings
  (Network access → Custom, keeping the default package-manager list), and
  the collector runs directly from this session; or
- **permit the single-file push** of `s3-cohort.yml` to the default branch,
  and the cohort runs in CI.

Until one is chosen, S3 is **built, frozen-verified and fully tested**, and
has produced **no measurement** — which is the correct state, because a
cohort that cannot see a live book must not be reported as a zero-fill
result. The CI job encodes this too: with no tape it writes
`VERDICT: NO_TAPE_COLLECTED` and explicitly does **not** advance the
cohort.
