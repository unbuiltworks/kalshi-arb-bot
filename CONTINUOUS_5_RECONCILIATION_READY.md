# Reconciled-state execution: the CONTINUOUS-4 P0, fixed and verified

```
executable_sha  4d9f2e4c4884bd72c484fdf943377c2d0c0ad663adfccb86207911d6a3382792   NEW
envelope_sha    b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17   UNCHANGED
bundle_sha      a902bd4a0b7fbce2074b8180a43bb1181217d29ef31484e5c8b34511c1f20fea   NEW
```

The envelope hash is unchanged because **no bound moved**. The driver hash moved
because the driver did: this is an execution-path correction, not a strategy
change. No authorization is in force — CONTINUOUS-4 is spent, the three
constants are `None`, and the workflow names a sentinel that cannot arm.

## The behavioural difference, at the exact CONTINUOUS-4 state

`src/gemini_execution/replay_continuous4.py`, over the recorded chronology.
At the moment **YES 0.92** was decided (17:28:05.977Z, 0.2s after the NO cancel
returned 200/empty):

| | yes_qty | no_qty | residual | imbalance | spend | avg_no_cost | n_fills |
|---|---|---|---|---|---|---|---|
| **what the driver believed** | 0 | 0 | 0 | 0 | 0 | null | 0 |
| **what the venue held** | 0 | **0.01** | −0.01 | 0.01 | 0.0014 | **0.14** | **1** |

`CP.plan` builds `state["inventory"]` *from* the Inventory object — its own
comment warns that a caller which forgets "gets a surface computed as if flat".
So the oscillator never saw the NO leg, and no risk gate could refuse a rung on
inventory it did not know existed. The pair completed at **1.06** against a
`<= 0.96` discipline.

Final reconciled state: `yes 0.01 / no 0.01 / matched 0.01 / residual 0.00 /
spend 0.0106 / avg 0.92 and 0.14`. **That 1.06 is a COST BASIS, not a loss.**
Settlement has not paid.

## The fix

**Reconcile before every decision, and again after cancels when a place is
pending.** The second refresh exists because CONTINUOUS-4 was exactly a fill
landing between a cancel and its replacement; deciding on pre-cancel inventory
and then placing afterwards is how a replacement gets sized against stale zero.

**Idempotent by construction, not bookkeeping.** `CP.recover` builds a fresh
`Inventory` from the authoritative list on every call and
`WorkingOrders.reconcile` replaces its view. Each fill applies exactly once
however often it runs, and a restart lands on the same state. There is no
"already seen" set to drift out of step.

**Re-read rather than parse events.** The `orderUpdate` shape has never been
observed. A parser written against an imagined shape is what produced the last
several defects here, so events are drained and **recorded** — nothing decides
on them — while inventory comes from reads whose shapes are observed.

**A 200 cancel ack with no `orderId` in `result` is UNCONFIRMED.** The slot is
still spent (the frame went out), but the order stays in the working set for
authoritative reconciliation. The venue is the authority; an ack is a receipt.

**Unreadable stays fail-closed.** `None` stops the window. Mid-window is no
more entitled to assume a flat account than a restart is.

## The nine proofs

| # | required | where it is proven |
|---|---|---|
| 1 | fills are recovered | **Production, on real money.** `authoritative_state` returned `[YES 0.01 @ 0.92, NO 0.01 @ 0.14]` from the authenticated account in CONTINUOUS-4 — and that is the exact function `_refresh` calls each cycle. Plus unit tests. |
| 2 | fill application is idempotent | five refreshes over two fills → `n_fills 2`, `no_qty 0.01`, `yes_qty 0.01` |
| 3 | working orders reconcile correctly | venue-listed order adopted, stale local order dropped; unarmed run reports `venue authoritative: 0 resting` |
| 4 | ambiguous cancel cannot erase a filled/resting order | on the **real** empty ack: `cancelled False`, order still in the working set, slot still spent |
| 5 | controller sees current inventory | `turns[].inventory` now records the state each decision used, with all nine fields |
| 6 | risk checks use current inventory | `would_breach("NO", …)` refuses at `no_qty 0.10` and **passes** on the flat inventory the driver used to hold — the defect, stated as a test |
| 7 | restart recovers the same state | `recover()` and `_refresh()` produce identical `inv.report()` |
| 8 | zero socket mutation frames | unarmed run: `wire: null`, `session: null` — no session was ever constructed |
| 9 | zero new authorization latch | `latch.consumed: false`; five latch files on disk, no sixth |

Unarmed production-path run `continuous_unarmed_20260918T175549Z.json`:
terminal `READY_TO_MUTATE_BUT_UNAUTHORIZED`, real credentials and readers,
preflight `agrees: true / discrepancies: []`, recovery `recovered: true`,
ledger `12 consumed / 0 unresolved`, refused by the authorization gate alone.

**One honest limit:** that run selected a *fresh* contract with no order history,
so it exercised the wiring rather than fill recovery. Proof 1 rests on the
CONTINUOUS-4 production read through the same function, and on the replay and
unit tests against the venue's own frames — not on this run.

## Fixtures preserve the real venue shapes

- successful place ack carries `result.orderId`
- `orderId` is a **string** in the ack and an **integer** in listings
- a successful cancel returns `status 200` with an **empty** result

## Unchanged on purpose

- **The 12/12 mutation budget is not reset.** A budget reset or a new live
  cohort is a separate owner decision.
- KEEP semantics: an unchanged resting rung still costs **zero** slots.
- Every envelope bound, and the strategy.

## CONTINUOUS-4 settlement watch

Read again 17:55:26Z, 25 minutes past expiry:
`position "0.02"`, `mytrades rows 2`, `fees "0"`, and
**`authoritative_outcome` absent, `settlement_cashflow` absent.**

**FILLED ≠ SETTLED. REALIZED remains $0.00.** The −$0.06/unit is a cost basis
until the venue pays, and it will be reported with matched-pair and
directional-residual kept apart when it does.
