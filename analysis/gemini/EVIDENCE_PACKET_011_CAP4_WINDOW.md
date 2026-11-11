# EP011 — the CAP-4 BTC15m q=1 window

**Authorization:** `OWNER-2026-09-21-GEMINI-BTC15M-Q1-CAP4-LIVE-1` — SPENT, DISARMED.
**Run:** [35641166387](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35641166387)
on `bf1bb4b`. **Readiness:** [35638920330](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35638920330)
PASS, manifest `b7e3536a2923dff3f97ff016c5ba1a52750ccb6337c1fa9266f59f195e8dacc7`.
**Cohort** `COHORT-2026-09-21-BTC15M-CAP4-1`, 5/12 consumed, 7 unused and they stay unused.

```
spec 7fda21b1…  executable 6846ebe3…  envelope be8b544b…  bundle 24cf29f5…
symbol GEMI-BTC15M2609211915-UP, entered tte 878.3s, band [840,880]
```

## The complete action sequence

| time | action | side | px | q | ack | outcome |
|---|---|---|---|---|---|---|
| 19:10:09 | OPEN | YES | 0.19 | 1 | 200 `…476520` | **FILLED 19:10:16** (rested 7s) |
| 19:10:09 | OPEN | NO | 0.72 | 1 | 200 `…476526` | **FILLED 19:11:49** (rested 100s) |
| 19:10:21 | EXTEND | YES | 0.11 | 1 | 200 `…481727` | never filled → cancelled on withdrawal |
| 19:11:59 | — | NO | 0.69 | 1 | 200 `…511839` | **CANCELED same second — maker-or-cancel** |
| 19:12:11 | — | NO | 0.56 | 1 | 200 `…514914` | **FILLED 19:12:42** (rested 31s) |

The 0.69 cancel is the post-only guarantee working: the venue refused to let it cross.
Not a rejection of ours, and not a failure.

## Inventory transitions — what happened after imbalance reached 2

It never reached 2.

```
flat                                      imbalance 0
19:10:16  YES 1                           imbalance 1   spend 0.19
19:11:49  YES 1  NO 1   MATCHED 1         imbalance 0   spend 0.91   <- crossed THROUGH zero
19:12:42  YES 1  NO 2   matched 1         imbalance 1   spend 1.47
```

**The first opposite-side fill this programme has observed live.** The oscillator turned
around by itself at 19:11:49 — the behaviour L5's history attributes to 84.3% of
imbalance episodes, and the behaviour cap-2 had been truncating.

## What stopped it — not the cap that was widened

```
risk gate refused the rung: filled 1.47 + resting 0.11
                          + durable outstanding 2.16 + this rung 0.64
                          = 4.38 committed, past 4.00
```

The binding constraint was **committed cash**, and it bound because **2.16 of DURABLE
OUTSTANDING from earlier windows** was already counted against the $4.00. Fresh headroom
was nearer $1.70 than $4.00.

So the two windows stopped for two different reasons, and **neither was the inventory
cap the CAP-4 grant was raised to relieve**:

| | cap-2 window (35628347483) | cap-4 window (35641166387) |
|---|---|---|
| fills | 2, both NO | **3, both sides** |
| matched | 0 | **1** |
| peak imbalance | 2 | 1 |
| stopped by | imbalance cap (2) | committed-cash cap ($4.00) |
| actions | 3 | 5 |

## Terminal state

Terminal withdrawal cancelled the resting YES 0.11: `CLEANUP_CONFIRMED`, 0 failed,
`remaining: []`, **orders_live_after_stop 0**. Authoritative position read, verdict
**AGREE**. Max 2 working orders throughout. No mutation touched the 30s floor: the only
in-window cancel was the venue's own maker-or-cancel.

## Settlement and cash

**SETTLED 19:15:07.443Z, resolutionSide NO.** `settlementValue 86059.79475` vs
`strike 86066.25` — below the strike, so UP resolves NO. Independent strike check agrees.

| leg | px | q | payout |
|---|---|---|---|
| YES `…476520` | 0.19 | 1 | $0.00 |
| NO `…476526` | 0.72 | 1 | **$1.00** |
| NO `…514914` | 0.56 | 1 | **$1.00** |

```
COST $1.47   PAYOUT $2.00   FEES $0.00   REBATE not credited
RECONSTRUCTED  +$0.53
```

**NOT REALIZED.** `proof_chain.first_break = settlement_cashflow` — *"no attributable
cash movement; a balance change without source identity is not attribution."*
Classification: **LIVE EXECUTION — q=1 — BTC15m — CAP-4 — reconstructed +$0.53 — cash
attribution unavailable — NOT REALIZED.**

The matched pair's 0.83 effective cost is OUR bookkeeping. The venue position
representation remains UNRESOLVED (`matched_is_verified: false`), so it is not reported
as Gemini economics.

## Disarmed

All five constants None/sentinel, `GEMINI_CANARY_AUTH` the sentinel, `authorized: False`.
One window only.
