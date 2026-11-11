# GEMINI CONTINUOUS WINDOW #2 — FAIL CLOSED AT PREFLIGHT, NO ORDER PLACED

**Authorization:** `OWNER-2026-09-18-GEMINI-CONTINUOUS-1`
**Artifact:** `executable_sha 2793c4cd…8d5d`, `envelope_sha b3bff654…aa17` — both bound and verified
**Run:** [35350729948](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35350729948), armed step 13:33:10–13:33:19Z
**Outcome:** `DO_NOT_TRADE`. **Zero mutations. Zero orders. Zero exposure.**
**The authorization is SPENT** — and that is one of the three defects below.

---

## What actually happened

The arm worked. Every gate before the driver passed. The symbol was selected
and bound. Then the driver's own preflight refused:

```
eligible window found on attempt 1
symbol_selection: GEMI-BTC05M2609181335-UP, tte_s 109.8, active/open
driver_executable_sha: 2793c4cda4577debb00b3c3a04120138d8f78c8b38f057497105024a4cdc8d5d

state: DO_NOT_TRADE
why:   fresh preflight differs from the frozen assumptions the envelope was
       sized against: [{"field": "terms_accepted", "expected": "True",
       "got": null, "why": "not present in the fresh preflight; an unread
       value is never read as agreeing"}]
cycles: 0   turns: []   budget_consumed: null   budget_left: 12
```

**The fail-closed rule did exactly its job.** "An unread value is never read as
agreeing" is the line that stopped it, and it is the right line.

### No order reached the venue — three independent confirmations

| evidence | reading |
|---|---|
| `continuous_ledger.jsonl` | **does not exist.** Every mutation attempt writes and fsyncs a row BEFORE the frame goes out. No file ⇒ no attempt. |
| latch record | `state: INTENT_PERSISTED`, `venue_order_id: null` — never advanced to SUBMITTED |
| driver report | `cycles: 0`, `turns: []`, `budget_consumed: null`, `budget_left: 12` |

---

## DEFECT 1 — the driver could never have traded

`MD.terms_status` is a **coroutine function**. `continuous_preflight.preflight_now`
is synchronous and called it without awaiting, so `ts` was a coroutine object,
`ts.get(...)` raised `AttributeError`, the handler recorded
`terms_accepted = None`, and an unread value is a discrepancy.

**Every run would have refused. On every contract. Forever.** The runner even
printed the diagnosis and nobody was reading for it:

```
RuntimeWarning: coroutine 'terms_status' was never awaited
```

This is the second "could never work" defect in this driver, and it has the
same shape as the first: **the tests only ever exercised the refusing
direction.** Every preflight test injected plain lambdas through `readers`;
not one passed the real async reader. A suite that only checks that the safe
path refuses cannot tell "correctly gated" from "permanently broken."

**Fixed** by making the misuse loud — `preflight_now` now detects a returned
coroutine and says so by name instead of swallowing it — and by having the
async invoker `await MD.terms_status()` once and pass the VALUE in `readers`.
A regression test passes the real coroutine function and asserts the loud
message; another asserts an awaited value makes the preflight AGREE.

## DEFECT 2 — a fail-closed preflight burned the authorization

The invoker consumed the durable latch **before** the driver ran, so the
preflight refusal spent `OWNER-2026-09-18-GEMINI-CONTINUOUS-1` with no order
attempted. The latch is committed and pushed (`43b598e3`); it is genuinely
spent and will not be reused.

My reading of "consume the latch before the first mutation" was too eager. **A
preflight is a READ, not a mutation.** The correct order is
preflight → latch → mutations, so a refusal costs nothing.

**Fixed**: the invoker now runs `preflight_now` + `check_frozen` first and
returns `DO_NOT_TRADE` with `latch: {consumed: false, why: "the preflight
refused BEFORE the latch was spent, so the authorization is untouched"}`. Tests
pin the ordering and pin that the refusal path returns rather than falls
through.

## DEFECT 3 — the evidence commit destroyed the evidence

`git add` on a pathspec that matches nothing exits 128. A window that attempts
no mutation writes no ledger, so the step died with

```
fatal: pathspec 'analysis/gemini/continuous_ledger.jsonl' did not match any files
```

and the run artifact, preflight and readiness evidence it HAD gathered were
never committed — **losing the record of a refusal, which is exactly the record
worth keeping.** Fixed with a per-file existence check.

---

## What this run did establish

Not nothing. This was the first time the armed path ran end to end, and it
proved the parts before the defect:

- the authorization **binds**: `executable_sha()` == `AUTHORIZED_SHA` at
  runtime, on the real artifact, in a real job;
- the dual key works — the arm was supplied by dispatch, not by the repo;
- symbol selection works: it found an eligible BTC5m contract at tte 109.8s on
  the first attempt and refused nothing incorrectly;
- the durable latch works: consumed, committed and pushed before anything else;
- the fail-closed rule works, on a real venue answer, with real credentials.

## State now

- **No Gemini order has ever been placed by the continuous driver.**
- `OWNER-2026-09-18-GEMINI-CONTINUOUS-1` is **SPENT**. A further window needs a
  new authorization identifier; this one cannot be reused and must not be.
- The driver's `executable_sha` is **unchanged** at `2793c4cd…8d5d` — all three
  fixes are in the invoker, the preflight module and the workflow, none in the
  authorized executable.
