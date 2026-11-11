# EP010 — BTC15m q=1 live window: it traded

**Authorization:** `OWNER-2026-09-21-GEMINI-BTC15M-Q1-LIVE-1` — **SPENT, now DISARMED**.
**Run:** [35628347483](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35628347483),
armed, on the authorized SHA `0d6066b`.
**Readiness that preceded it:** [35626109936](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35626109936)
PASS, manifest `d7ed529b6eca1c7ed39c477469727cce9f1f3a8b3692227dd1a0fd5d69361880`.

```
executable  6846ebe397c56bfd5a8515648cf91db0a6eafeb4b5f14ca9ae6886272e2a479c
envelope    625375d1f3f5d37372cba91d7c22e074c2adef8477ac5e1a959486c45f8bd97b
bundle      85bb4373a1c021bc62330f89f865b5d80c173f011fa5912404e4409aa30f4dc4
spec_sha    d2f1f05d789387f52fc2e72cb4516342684c560c1b2484f54fe2a282c5e05a3c
            expected == resolved, checked before the latch
```

## The chain

| stage | evidence |
|---|---|
| market | `GEMI-BTC15M2609211715-UP`, entered at **tte 878.1s**, band `[840, 880]` |
| envelope | `GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_V1` / `GEMI-BTC15M`, authorized `True` |
| latch | consumed **before** the session connected, pushable, on disk |
| **SUBMITTED** | 3 × `order.place`, **every frame `quantity: 1`** |
| **ACKNOWLEDGED** | 3 × `status 200`, `accepted: true`, venue orderIds below |
| **FILLED** | **2 of 3**, both NO, both `maker-or-cancel`, both `filledQuantity: 1` |
| not filled | YES 0.71 rested, never filled, cancelled by terminal withdrawal |
| stop | the **risk gate** refused the next rung |
| **terminal withdrawal** | `CLEANUP_CONFIRMED`, 1 cancel, 0 failed, `remaining: []` |
| authoritative position | read `200 / ok`, verdict **AGREE**, tradeable |
| **SETTLED** | **PENDING** — see below |
| **REALIZED** | **NO.** No attributable cash movement has been read back. |

```
17:10:11  PLACE  YES 0.71  q=1   ack 200  orderId 145828836330164001   -> cancelled
17:10:11  PLACE  NO  0.22  q=1   ack 200  orderId 145828836330164022   -> FILLED 17:10:17
17:10:23  PLACE  NO  0.17  q=1   ack 200  orderId 145828836330167469   -> FILLED 17:10:49
```

The 0.22 fill carries `m: true` (maker) and `n: "0.0000"` (fee) on the venue's own
`orderUpdate`. Both fills are confirmed a second time by the authoritative order-history
read: `status: filled`, `filledQuantity: 1`, `timeInForce: maker-or-cancel`.

**Why it stopped** — the envelope, not the market:

```
risk gate refused the rung: a fill would take imbalance to 3, past 2;
                            a fill would take NO to 3, past 2
```

## Every stop condition in the grant, checked

| condition | observed |
|---|---|
| readiness / hash / spec / envelope mismatch | none — `spec_sha_check.ok`, all three hashes self-consistent |
| q != 1 | never; all 3 wire frames and both venue rows say `1` |
| unreadable / disagreeing authoritative position | read `200/ok`, verdict `AGREE` |
| ledger / cohort failure | cohort 3/12, predecessor intact, `ledger_unresolved: []` |
| recovery anomaly | none; `pre_latch_recovery` checked before the latch |
| unexpected safety state | none |
| **> 2 working orders** | max **2** concurrently; `working_orders: []` at the end |
| **mutation inconsistent with the 30s floor** | **zero in-window cancels.** The only cancel was the terminal withdrawal, an enumerated safety reason that the floor does not protect |
| terminal withdrawal failure | `CLEANUP_CONFIRMED`, `orders_live_after_stop: 0` |

## Position, and what is NOT claimed

Carried inventory: **2 NO contracts**, average cost **0.195**, total spend **$0.39**.
Directional residual −2, imbalance 2 — at the authorized bound, which is what stopped it.
No taker cross was introduced to flatten it.

**Settlement is PENDING.** Three authoritative reads during the window returned
`active / marketState open`, no flap, `resolutionSide`, `resolvedAt` and
`settlementValue` all absent. Expiry is `2026-09-21T17:15:00.000Z`; the window finished
`17:11:02Z`. The post-run read-only pass ([35630…](https://github.com/unbuiltworks/kalshi-arb-bot/actions))
did **not** capture it: the settlement-gate step probes symbols chosen by the workflow's
own `Choose symbols` step, and `fill_forensics` runs a fixed `2026-08-26 → 2026-09-03`
window. Neither covers this contract. **So settlement for this symbol has not been read
back, and nothing here is realized.**

For completeness and clearly labelled as **modelled, not realized**: the contract
resolves Up if BTC ≥ the reference strike `85984.92` (published `17:00:00Z`) at
`17:15:00Z`. Holding 2 NO against $0.39 paid, the settlement outcome is binary — $2.00
if Down, $0.00 if Up — before fees and any rebate. **That is a range, not a result.**

## Accounting ladder reached

`historical replay → shadow → live probe → SUBMITTED → FILLED → settled → credited → realized`

Reached **FILLED**. `settled`, `credited` and `realized` are NOT reached.

## Disarmed

All five constants are back to `None`/sentinel, `GEMINI_CANARY_AUTH` is the sentinel, and
`authorization_state().authorized` is `False`. The cohort keeps its 9 unused slots
unused; a further window needs a further grant. The latch
`latch_OWNER-2026-09-21-GEMINI-BTC15M-Q1-LIVE-1.json` stays on disk on purpose — that is
what makes one-authorization-one-window durable across a fresh runner.
