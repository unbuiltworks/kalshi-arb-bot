# SEMANTICS-1 — frozen evidence chain

Authorization **`OWNER-2026-10-03-GEMINI-SEMANTICS-1`** — **SPENT**. One
order, one market, one case. No further live order will be placed without a
fresh grant.

**Scope of what this probe establishes: taker EXECUTION SEMANTICS, and nothing
else.** The fill occurred at TTE ≈ 720 s, **before the strike existed**, so its
win/loss carries no directional information and is excluded from
directional-strategy validation in both directions. It would have been no
evidence *for* the thesis had it won.

## Code and authorization provenance

| | |
|---|---|
| probe tree SHA | `49627ca56197b9443ffa5650d4e86e775ac907da` (pinned; a short SHA resolves as a branch name and fails) |
| fill-detection fix | `27afc08a` (after the fact — see §Defects) |
| launcher | `.github/workflows/gemini-settlement-gate.yml`, `semantics_probe_armed=true` |
| run | [37123139109](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/37123139109) |
| auth smoke (read-only) | issue [#184](https://github.com/unbuiltworks/kalshi-arb-bot/issues/184), 11:46Z: `credentials_present true`, `account_scoped_key true`, `authenticated_ws true`, terms `v1/v1 hasAcceptedLatest`, `order_entry false` |
| gates | 54 at dispatch, run with **no credential in scope**; 57 after the fix |
| taker capability | reachable only when `arming.EXPECTED_AUTH_ID == payload.SEMANTICS_PROBE_AUTH_ID`; asserted in the job before anything else |

## The chain, in order

**1. Signal / preflight — 12:31:05Z**, public reads only, no credential:

```
symbol            GEMI-BTC05M2610031245-UP
case              A  (favoured side has a visible resting offer)
favoured side     yes (UP)
yes ladder        ask 0.50 / bid 0.49      no ladder  ask 0.51 / bid 0.50
complement ident. consistent (no_ask vs 1-yes_bid 0.00; no_bid vs 1-yes_ask 0.0)
depth             asks 0.50 x 750 (1 level); bids 0.49 x 500
live terms        readable; quantityMinimum/Increment 0.01,
                  priceMinimum/Increment 0.01; contract active; market open
tick              0.01   ->   allowed limits ['0.50', '0.51']
expiry            2026-10-03T12:45:00.000Z
strike            NOT YET SET (availableAt 12:40:00Z)
```

**2. Approved order.** Limit **tied to the recorded ask**, not a universal
crossing price: 0.50 = the ask itself. Quantity 1, predeclared. Cash at risk
**$0.50** against the **$5.00** cap.

**3. Pre-submission re-read** (immediately before sending):

```
current_ask 0.5   frozen limit 0.50   displayed size at or below limit 750.0
required 1        -> still executable
```

**4. Submitted body** — the only path in the repository that may set
`makerOrCancel: false`:

```json
{"symbol":"GEMI-BTC05M2610031245-UP","orderType":"limit","side":"buy",
 "quantity":"1","price":"0.50","outcome":"yes",
 "timeInForce":"good-til-cancel","makerOrCancel":false}
```

**5. Acknowledgement — HTTP 201, latency 0.376 s:**

```json
{"orderId":145828836703726947,"status":"filled","filledQuantity":"1",
 "remainingQuantity":"0","avgExecutionPrice":"0.5","price":"0.5","quantity":"1",
 "side":"buy","outcome":"yes","orderType":"limit",
 "timeInForce":"good-til-cancel",
 "createdAt":"2026-10-03T12:33:05.972Z","updatedAt":"2026-10-03T12:33:05.972Z",
 "globalOrderId":"60a1b661...a317c7","hashOrderId":"24QY7YljxJ2A"}
```

**6. Classification: taker.** `makerOrCancel: false`, crossed a resting offer,
`createdAt == updatedAt`, filled on acknowledgement. **No partial** —
`remainingQuantity 0`.

**7. Position.** `/v1/prediction-markets/positions` went **0 rows → 1 row**,
`avgPrice 0.5`, contract `Up`.

**8. Settlement (public, authoritative listing):**

```
ticker           BTC05M2610031245        status settled
strike           84875.6                 (availableAt 12:40:00Z)
settlementValue  84801.331
resolutionSide   no
```

BTC finished **$74.27 below strike**. UP resolved **NO** → payout **$0.00**.

**9. Expected cash.** Spend $0.50; taker fee
`ceil(0.07 · 1 · 0.5 · 0.5, cent) = $0.02`; payout $0.00. **Expected ≈
−$0.52.** *Labelled expected, not realized, pending the account read.*

**10. Authenticated reconciliation — COMPLETE.** Read-only run
[37123964457](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/37123964457),
evidence committed by `gemini-settlement-gate[bot]` at `3c319285`.

*The order, from the venue's own order surface* (`fill_forensics_20261003T130017Z.json`,
raw row preserved verbatim):

```json
{"orderId":145828836703726947,"status":"filled","side":"buy","outcome":"yes",
 "orderType":"limit","timeInForce":"good-til-cancel","quantity":"1",
 "filledQuantity":"1","remainingQuantity":"0","price":"0.5",
 "avgExecutionPrice":"0.5","createdAt":"2026-10-03T12:33:05.968Z"}
```

The same row's `contractMetadata` independently confirms the exclusion
rationale: `startTime` **12:40:00Z** and `strike.availableAt` **12:40:00Z**,
against a fill at **12:33:05.968Z**. The fill preceded the window's own start
by nearly 7 minutes, from the venue's own metadata rather than our inference.

*The account, authenticated at 13:00:11Z* (`continuous_unarmed_20261003T130011Z.json`):

```json
"position_gate": {"read": {"path":"/v1/prediction-markets/positions",
                           "status":200,"ok":true,"total":0},
                  "n_rows_for_symbol":0, "n_rows_on_account":0,
                  "venue_representation":"FLAT_NO_ROWS"}
```

Positions went **0 rows → 1 row (12:33, avgPrice 0.5) → 0 rows (13:00,
FLAT_NO_ROWS)**. The position settled and closed. The account is flat.

**REALIZED: −$0.50.** Confirmed from the authenticated fill (1 @ 0.50), the
authoritative resolution (`no`, payout $0.00) and the authenticated flat
account. This is realized settlement cashflow, not a mark.

**One line NOT confirmed, stated rather than papered over:** the venue's order
surface carries **no fee field**, and this run captured no fee ledger. The
taker fee remains formula-derived — `ceil(0.07 · 1 · 0.5 · 0.5, cent) = $0.02`
— so the total is **−$0.50 realized plus an unconfirmed $0.02 fee, i.e.
−$0.50 to −$0.52**. The fee is not called realized.

## What this establishes

> **When our book says the favoured side is executable at a recorded ask,
> Gemini's actual order and fill behaviour AGREES: a marketable limit BUY tied
> to that ask fills immediately, in full, at that ask — not above it.**

`visible ask 0.50 → marketable limit BUY at 0.50 → filled 1 @ 0.50` in 376 ms.
This is the project's **first knowing taker fill** on Gemini; every prior fill
on record was `maker-or-cancel`.

## Defects found by this probe — both mine, both recorded

**(a) The classifier misread the book shape.** `classify()` read the raw
contract price mapping instead of the per-outcome ladder
`metadata.outcome_book` returns, and called `GEMI-BTC05M2610031200-UP` case B
— "nothing buyable" — while **750 contracts were offered at 0.50**. Caught by
the first public preflight, before any order. Fixed in `49627ca5`: it now
reads the documented keys and **raises** on an unrecognised shape rather than
reading absence into it.

**(b) Fill detection could not see a fill.** Fill status was read **only** from
active-orders — and a filled order is not an active order, so that read is
structurally incapable of ever observing a fill. The probe therefore reported
*"did NOT trade"*, recorded `filled_quantity 0`, latched `UNFILLED`, sent a
cancel for the already-filled order (404 `OrderNotFound`, correctly reported
`OUTSTANDING` by the fail-closed cancel) and wrote a **false**
`SEMANTIC_CONTRADICTION` halt. Its own verdict read *"Gemini DISAGREES."*

Taken at face value, that would have reported the exact opposite of the truth
and halted the programme on a measurement error. Fixed in `27afc08a`: the
acknowledgement is read as the authoritative fill report on a crossing order,
its `filledQuantity`/`status` win over a zero from active-orders, the
execution price is checked against the ack where only the ack carries one, and
a filled order is never sent a cancel. Three regression tests, including the
mutation that matters — **a case A that genuinely does not trade must still
raise the contradiction**, so the fix cannot make every case A pass.

The raw record is preserved verbatim in
`analysis/gemini/info_edge/probe_records/`, **including the wrong verdict and
the wrong latch state**, because the error is part of the evidence.

## Bounds, as honoured

one market · **one** order · $0.50 of a $5.00 cap · no EXTEND · no averaging ·
no chasing (a moved book stops the run) · no FLIP · no complement · no retry ·
no second market · no automatic price selection (price and quantity both
predeclared) · case B never authorized and never run · latch one-shot and
spent before the venue saw anything.

## Status

**CLOSED to further live orders.** The authorization is spent and will never
arm again. Directional work continues on frozen DEV only — see
`POST_STRIKE_DEV_FINDINGS.md`.
