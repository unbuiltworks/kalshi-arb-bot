# R2 execution-validation cohort — authorization request

**NOTHING IS ARMED.** `arming.EXPECTED_AUTH_ID` holds the **spent**
`OWNER-2026-10-03-GEMINI-SEMANTICS-1`, and `payload.SEMANTICS_PROBE_AUTH_ID`
still equals it, so the taker path is unreachable and every strategy is
maker-only. No order can be submitted until the owner installs the identifier
below.

---

## 1. The exact cohort authorization

```
identifier        OWNER-2026-10-03-GEMINI-R2-EXEC-1
purpose           R2 EXECUTION VALIDATION ONLY.
                  NOT a profitability estimate for R2. Ten q=1 fills cannot
                  distinguish +0.04/contract from zero.
qualification     frozen R2, IMPORTED from
                  analysis/gemini/info_edge/r2_frozen.py at
                  6bc5c1f730dd0d4639eb4c9459803dbf9d68dee5
                  fingerprint 64b178378806b5c257b58b29d0ba49da
                  -- never reimplemented in the live path
budget            10 submitted probes, total
per market        1 submitted probe, and a MISSED market is never revisited
quantity          q = 1, fixed
outstanding       1 order maximum, checked against the VENUE
loss stop         $8.00 cumulative CONFIRMED realized principal loss
structural max    $10.20   (proven in §5)
expires           at the 10th submitted probe, OR on arrival of the
                  100-independent-window statistical gate, whichever first
latch             every submission consumes a durable NUMBERED slot, fsynced,
                  BEFORE the venue is touched
ambiguity         an ambiguous send has already spent its slot and is NEVER
                  retried -- there is no release path in the code
restart           consumed slots are RE-COUNTED FROM DISK, never cached,
                  never reset
replenishment     NONE. No function can raise the budget. A further probe
                  needs a new cohort, created by a human.
```

**To install (three edits, one commit):**

1. `arming.EXPECTED_AUTH_ID = "OWNER-2026-10-03-GEMINI-R2-EXEC-1"`
2. `payload.SEMANTICS_PROBE_AUTH_ID = "OWNER-2026-10-03-GEMINI-R2-EXEC-1"` —
   **required**, because `transport.place(allow_taker=True)` refuses unless
   the live authorization equals this constant. Miss it and the taker path
   stays shut.
3. The workflow step's `GEMINI_CANARY_AUTH` + a fresh
   `GEMINI_CANARY_AUTH_EXPIRES`.

Then `r2_exec_budget.create("COHORT-2026-10-03-R2-EXEC-1")` — once, by a
human. A second call is a refusal, not a no-op.

## 2. Per-market decision sequence

Frozen R2, imported. No additional predictive filter, no threshold
optimization, no TTE selection.

```
1  budget gate      may_probe(): not expired, market not already finished
2  POST-STRIKE      observation time >= strike.availableAt           (CHECKED)
3  FAVORED SIDE     UP if coinbase - strike > 0 else DOWN
4  |z| >= 1.0       z from the frozen definition, imported
5  EXECUTABLE       favoured side has an offer; price in (0,1)
6  FREEZE           record price, size, depth at/inside that price
7  PRE-SUBMIT RE-READ   an offer must STILL sit at or below the frozen price
                        with >= 1 contract of displayed size at or below it
8  CONSUME SLOT     numbered, fsynced, market marked SUBMITTED
9  SUBMIT           ONE limit BUY, q=1, limit = frozen price, taker
10 RECORD           the seven measurements, settle, reconcile
```

**Step 7 fails → `MISSED / NOT_EXECUTABLE`.** No slot consumed. No chase, no
reprice, no substitute price. **The market is finished anyway** and is never
revisited — enforced by `finished_markets()`, which treats MISSED exactly like
SUBMITTED for revisit purposes.

Recorded per probe: modelled price/depth · actual fill price/qty ·
signal→submission latency · offer survival · venue acknowledgement · observed
fee (and explicitly "unobservable" when the venue reports none) · settlement ·
authenticated reconciliation.

## 3. Durable budget / numbered-slot implementation

`src/gemini_execution/r2_exec_budget.py`, built on the already-reviewed
`cohort.py` primitives rather than a second ledger design.

- **Slot consumption is append-then-fsync-then-fsync-the-directory**, and the
  caller may touch the venue only after `consume_slot()` returns. A crash
  therefore leaves the slot **spent**, not free.
- **Slots are numbered** `0..9`; the number is the consumed count at the
  moment of consumption, so numbering cannot collide or restart.
- **A torn row counts as consumed** (`cohort._consumed_in`). A half-written
  row may or may not have reached the venue; counting it free could send an
  eleventh probe, counting it spent wastes one. The safe direction is chosen.
- **Three separate append-only files**, because they answer different
  questions: `ledger.jsonl` (submissions = budget), `markets.jsonl` (markets
  finished, including MISSED), `settled.jsonl` (confirmed cashflow only).
- **No release path.** There is no `release`, `refund`, `unconsume` or
  `rollback` function, and a test asserts their absence. That is what makes
  "an ambiguous send is never retried" true rather than merely intended.
- **No replenishment.** One module-scope assignment to `BUDGET`; a test
  asserts there is exactly one and that no `replenish`/`extend`/`increase`/
  `reset` function exists.

## 4. Restart recovery

Nothing is held in memory. `consumed()` re-reads `ledger.jsonl` on **every**
call and `remaining()` is `BUDGET − consumed()`. A new process, a new
container, or a reloaded module all see the same number, because the number
lives on disk.

Proven by `test_restart_recovers_consumed_slots_rather_than_resetting`: four
slots consumed, the module reloaded, and the fresh module reports
`consumed == 4`, `remaining == 6`.

The cohort directory lives under `analysis/gemini/cohorts/`, inside the repo,
so the ledger can be committed and survives container reclamation — the same
git-bound durability SEMANTICS-1 used.

## 5. Maximum-risk proof

**Per probe.** A q=1 BUY can lose at most what it pays: the price, plus the
fee charged at that price. Price is bounded by the venue grid at `1.00` and
checked in `consume_slot` (`0 < price <= 1.00`). The venue's taker fee is
`ceil(0.07 · C · P · (1−P), cent)`; over `C = 1` and every admissible cent
price `P ∈ {0.01 … 0.99}` its maximum is **$0.02**, at `P = 0.50`. This is
computed over all 99 prices in `test_max_fee_claim_holds_at_every_cent`, not
assumed.

```
MAX_LOSS_PER_PROBE = 1.00 + 0.02 = $1.02
```

**Aggregate.** At most `BUDGET` submissions can ever occur, because every
submission consumes a slot before the venue is touched and no slot is ever
released:

```
STRUCTURAL_MAX_LOSS = MAX_LOSS_PER_PROBE × BUDGET = 1.02 × 10 = $10.20
```

`test_structural_maximum_is_derived_not_asserted` checks that 10.20 is a
*consequence* of the two bounds rather than a number typed in.

**The stop that bites first.** The $8.00 confirmed-realized-principal-loss
stop halts the cohort before the structural ceiling in any realistic path.
Two properties worth stating:

- Only **authenticated** cashflow counts. A resolution side is not cash, a
  mark is not cash, a formula-derived fee is not cash
  (`test_only_confirmed_cashflow_counts_toward_the_loss_stop`).
- **Winners do not buy more room.** The stop is on cumulative *loss*, not on
  net, so a profitable probe cannot offset losses into additional risk
  (`test_wins_do_not_offset_the_loss_stop_into_more_room`).

**Expected cost**, if R2's prospective figures hold (86.2% correct at mean
price 0.810): roughly **−$0.40 net across 10 probes**. The $8.00 stop and the
$10.20 ceiling exist for the case where they do not.

## 6. Tests — 19, each paired with its negative

```
budget is 10, q is 1                                    PASS
structural max is DERIVED (1.02 x 10 = 10.20)           PASS
max fee 0.02 holds at all 99 admissible prices          PASS
no function can raise the budget                        PASS
ten slots then BUDGET_EXHAUSTED                         PASS
one submission per market                               PASS
a refusal does NOT spend a slot                         PASS
MISSED spends no slot but is never revisited            PASS
q must be exactly 1 (2 / 0.01 / 0 / 1.5 refused)        PASS
price must be in (0, 1.00]                              PASS
slot is on disk before the caller can submit            PASS
restart re-counts from disk, does not reset             PASS
a torn row counts as CONSUMED                           PASS
ambiguous leaves the slot spent; no release path exists PASS
only CONFIRMED cashflow counts toward the stop          PASS
loss stop expires the cohort at $8.00                   PASS
wins do not offset the stop into more room              PASS
statistical gate arrival expires the cohort             PASS
a cohort is created once; state opens no mutation path  PASS
```

19 passed. Combined with the existing Gemini execution suite: **204 passed**.

## 7. HALT conditions — any one blocks every later probe

Fill above the frozen limit · side/outcome/price/quantity mapping
contradiction · ambiguous send · unexplained partial · reconciliation failure ·
account-state contradiction (not flat at probe start). Each writes the HALT
file, which `semantics_probe.halted()` checks **before** anything else and
which only a human clears. An unreadable HALT file is treated as halted.

## 8. Separation from the statistical gate — unchanged

- Probe markets are written to `probe_windows.json` and **excluded** from the
  statistical cohort and from the blind gate count.
- The gate counter prints a window count and discards correctness, price and
  EV. **No interim R2 performance read exists.**
- **Live results may not alter R2.** R2 is immutable at `6bc5c1f7`.
- Live P&L is its own ledger and is never pooled with the statistical result.

## 9. What I still need from you

1. Grant `OWNER-2026-10-03-GEMINI-R2-EXEC-1` (or name your own id).
2. Confirm you want the three-edit install above, including moving
   `payload.SEMANTICS_PROBE_AUTH_ID` — without it the taker path stays shut
   and the cohort cannot submit.

On your word I install it, create the cohort, and run the first probe only.
**No order will be submitted before that.**
