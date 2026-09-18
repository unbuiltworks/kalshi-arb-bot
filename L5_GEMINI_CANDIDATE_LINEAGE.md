# Candidate lineage — HELD pending CONTINUOUS-4 settlement

**Status: HOLD.** No authorization is in force. No further engineering on the
Gemini execution path until settlement lands and the owner decides on a new
cohort.

```
commit          2ad8f045
executable_sha  4d9f2e4c4884bd72c484fdf943377c2d0c0ad663adfccb86207911d6a3382792
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17
bundle_sha      a902bd4a0b7fbce2074b8180a43bb1181217d29ef31484e5c8b34511c1f20fea
```

`REQUIRED_AUTH_ID`, `AUTHORIZED_SHA` and `AUTHORIZED_BUNDLE` are all `None`;
`arming.EXPECTED_AUTH_ID` is a sentinel matching no environment value. The
workflow's armed step cannot arm.

## What this lineage is

The first execution loop with the shape the L5 hypothesis actually needs:

**venue state → authoritative inventory → L5 decision → mutations →
authoritative reconciliation → next L5 decision**

## What CONTINUOUS-4's $1.06 pair was, and was not

**It was execution-state corruption, not a strategy failure.** The replay
establishes the causal chain: Gemini already held `NO 0.01 @ 0.14` while the
controller saw `NO = 0`, because `apply_fill` had no caller in the live loop
and a 200 cancel ack with an empty result had erased the order from the working
set. `CP.plan` builds `state["inventory"]` from the Inventory object, so the
oscillator was handed a fictitious flat book and no risk gate could refuse a
rung on inventory it did not know existed.

It is **not** evidence that the behavioural model wanted a $1.06 pair, and it
must not be read as a reason to tune the controller or move the `<= 0.96`
target.

## Deliberately NOT done

- no controller tuning
- no change to the `<= 0.96` pair discipline
- no `orderUpdate` parser — the schema has never been observed, and this
  package has been burned repeatedly by assuming venue response shapes.
  Authoritative re-read is the conservative choice until real frames are
  captured. Events are drained and **recorded**; nothing decides on them.
- no broader strategy work
- **the 12/12 ledger is not reset, edited or rewritten**

## Two gates before another bounded window

### 1. Settlement of CONTINUOUS-4 — accounting only

As of 17:55:26Z, 25 minutes past expiry: `position "0.02"`, `mytrades rows 2`,
`fees "0"`, `authoritative_outcome` **absent**, `settlement_cashflow`
**absent**.

**FILLED ≠ SETTLED. REALIZED is $0.00 and that is correct.** When Gemini
provides an authoritative outcome and cashflow: record **SETTLED**, then
**REALIZED**, with matched-pair and directional-residual kept apart and fees
and incentives separate again. The −$0.06 per unit is a cost basis until then.

### 2. A fresh bounded budget cohort — not a reset

CONTINUOUS-2, -3 and -4 are **permanently spent** and their ledger rows stay
byte-for-byte as written. Any further window needs a **new cohort** with
provenance recording that fact — a new budget alongside the old one, never a
reset of it.

Requirement only. **Not built**, because building it now would be exactly the
"anything else" this hold excludes.

## What the next live window would be for

Qualitatively different from the first four. Whether Gemini accepts an order is
settled. The milestone is:

**real fill → authoritative refresh → changed inventory → changed L5 action**

That is the first test of whether the continuous-inventory oscillator works
under live conditions, and it is the reason to authorize a window — not
"another canary".
