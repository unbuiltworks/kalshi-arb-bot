# CONTINUOUS-6 — same question, with the cancel fixed

The research purpose is unchanged from CONTINUOUS-5:

> **REAL FILL → AUTHORITATIVE REFRESH → INVENTORY CHANGE → CHANGED L5 ACTION**

C5 never reached it: its window lived nine seconds and died on a cancel the
venue rejected. That defect is now fixed at the normalization boundary.

## Hashes

```
executable_sha  7dc3daaefcbc39488611fbb94efdb3830c84f760aa866d53dcfaeb99b2aa01ff   NEW
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17   UNCHANGED
bundle_sha      3bd2872fdb462ea48aa28dc04b299232c9df6da45f88030af96faca5f993497d   NEW
```

The envelope did not move because **no bound moved**: this is a
representation fix, not a risk change. The executable moved because the fix is
in the driver, which is exactly what the detached signature is for — it
invalidated the C5 authorization automatically.

## The fix

`canonical_order_id()` renders an int and returns a string as it came. Both
`WorkingOrders` write paths use it, so the working set cannot hold a shape
`order.cancel` will reject. Missing, empty, blank, boolean, non-positive,
float and structured ids are **refused**, never invented; in `reconcile` that
stops the window, because an order the venue says is resting that we cannot
address is not a row to accept quietly.

`reconcile` also selects the id key by **presence** rather than truthiness:
`o.get("orderId") or o.get("order_id")` fell through whenever the first was
`0` or `""`.

## Cohort

```
cohort_id             COHORT-2026-09-18-C6
budget                6   (of the envelope's 12)
consumed / remaining  0 / 6
predecessor           analysis/gemini/cohorts/COHORT-2026-09-18-C5/ledger.jsonl
predecessor sha256    b24040350fda1891f084097762cef35facf84a51f8f8cf238d8198b0fead5578
predecessor consumed  3   (of C5's 6 — permanently spent, UNKNOWN included)
predecessor intact    true
```

**Fresh, not recycled.** C5 keeps its three unused slots unused: budget is not
regained by rewriting an UNKNOWN. C6 is chained to **C5's ledger** rather than
the historical one, so any rewrite of C5's evidence — including its UNKNOWN
cancel — makes this cohort refuse before the latch. The historical 12/12
ledger remains protected by C5's own record, which C6 now protects in turn.

## Unarmed verification

`analysis/gemini/continuous_unarmed_20260918T214609Z.json`, Actions run
35398258335 on `0ebee712`, contract `GEMI-BTC05M2609182150-UP` at TTE 230.7s.

| | |
|---|---|
| executable | `7dc3daae…01ff` |
| envelope | `b3bff654…aa17` — unchanged |
| bundle | `3bd2872f…497d` |
| cohort | `COHORT-2026-09-18-C6`, 6 / 0 consumed / 6 remaining |
| predecessor | C5 ledger, recorded == current == `b2404035…`, 3 == 3, intact |
| recovery | `recovered: true`, `budget_left 6`, checked before the latch |
| preflight | `agrees: true, discrepancies: []` |
| latch | `consumed: false` |
| terminal state | **`READY_TO_MUTATE_BUT_UNAUTHORIZED`** |

**Authorization is the sole blocker.** `why_not` lists exactly three reasons
and nothing else:

1. `REQUIRED_AUTH_ID is None`
2. `AUTHORIZED_SHA is None`
3. `transport arm: GEMINI_CANARY_LIVE is not YES`

No session was constructed, no frame sent, no latch consumed. 6,374 tests pass.

## What CONTINUOUS-5 leaves on the table

Its NO leg filled **after** the window stopped and settled against us —
expected −$0.0046, unmatched directional residual, never realized. The lesson
carried into C6 is not a code change: a fail-closed stop halts the controller,
not the orders it already placed.

## Proposed authorization statement

> I authorize ONE bounded live Gemini BTC5m continuous-inventory window using
> authorization ID `OWNER-2026-09-18-GEMINI-CONTINUOUS-6`, executable SHA
> `7dc3daaefcbc39488611fbb94efdb3830c84f760aa866d53dcfaeb99b2aa01ff`, envelope
> SHA `b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17`,
> bundle SHA `3bd2872fdb462ea48aa28dc04b299232c9df6da45f88030af96faca5f993497d`,
> and mutation cohort `COHORT-2026-09-18-C6` with its bounded budget of 6
> slots. Its sole purpose is to observe real fill → authoritative refresh →
> changed inventory → changed L5 action, with every cycle's decision captured.
> No pair-cost ceiling is added; a COMPLETE above $1.00 is permitted when the
> controller chooses it and every absolute bound passes. Fail closed on
> unreadable or ambiguous authoritative state or an actual risk-envelope
> violation. No second window, automatic cohort, rollover, budget reset,
> scaling, SELL, IOC/FOK, taker crossing, forced flatten, strategy/controller
> change, risk-envelope change, or CONTINUOUS-7 without new approval.
> CONTINUOUS-5 stays spent at 3 of 6 with its UNKNOWN intact, and the
> historical 12/12 ledger stays byte-for-byte.

**No CONTINUOUS-6 will run without that statement.**
