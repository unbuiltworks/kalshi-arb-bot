# EVIDENCE PACKET 003 — Gemini position semantics: can one account hold YES and NO at once?

**Branch:** `claude/gemini-btc-economic-viability-h3u7zl`
**Status of this document:** EVIDENCE ONLY. No strategy code, no runtime config and no
Railway service was changed to produce it. No order was submitted. The
`gemini-l5-faithful` service is running unchanged.

**Headline:** **VENUE-SEMANTICS MISMATCH — FLAGGED, NOT FIXED.**
The Gemini prediction-market venue represents an account's holding in a contract as
**one signed position per instrument**, where the sign *is* the outcome. Our
`Inventory(yes_qty, no_qty)` representation permits states that representation cannot
express. The mismatch is **latent**: it has not yet contaminated any recorded
prospective result, because the faithful run has produced **zero COMPLETE fills**.

---

## 1. The question, and the four candidate answers

The Gemini consumer UI says: *"You have an active Yes prediction… If you want to place
a No prediction, you need to first cash out your existing prediction."*

Candidates:

1. buying the opposite outcome creates **independent** YES and NO inventories;
2. it **nets/reduces** the existing position;
3. it is **rejected** while the opposite position exists;
4. it **requires an explicit sell/cash-out first**.

---

## 2. OBSERVATION — options 3 and 4 are FALSIFIED by our own venue-confirmed evidence

Source: `analysis/gemini/continuous_run_20260918T172750Z.json`, key `after.orders_seen`.
These rows are the **venue's own order-history records**, not our labels.

| field | NO leg | YES leg |
|---|---|---|
| `orderId` | 145828836246611835 | 145828836246613472 |
| `symbol` | GEMI-BTC05M2609181730-UP | GEMI-BTC05M2609181730-UP |
| `side` / `outcome` | `buy` / `no` | `buy` / `yes` |
| `price` | 0.14 | 0.92 |
| `quantity` / `filledQuantity` | 0.01 / 0.01 | 0.01 / 0.01 |
| `status` | `filled` | `filled` |
| `timeInForce` | `maker-or-cancel` | `maker-or-cancel` |
| `createdAt` | 2026-09-18T17:27:59.483Z | 2026-09-18T17:28:05.939Z |
| `updatedAt` | 2026-09-18T17:27:59.492Z | 2026-09-18T17:28:33.389Z |

Read the timestamps. The NO leg was **created and terminal-filled within 9 ms**, at
17:27:59.492Z. The YES order on the **same instrument** was created **6.4 s later**, at
17:28:05.939Z — while the account held the NO — was **accepted**, **rested as a maker
for 27 s**, and **filled** at 17:28:33.389Z. No sell was issued. No cash-out was issued.

- **FALSIFIED (3):** an opposite-outcome buy is *not* rejected while an opposite
  position exists. The venue accepted it and filled it.
- **FALSIFIED (4):** an explicit sell/cash-out is *not* required first at the API level.
  The UI restriction is a **consumer-app** constraint, not an API constraint.

This is a single observation (n=1 instrument, n=1 episode). It is sufficient to falsify
"rejected"/"requires cash-out first" because those are universal claims, and one
counter-example refutes a universal claim. It is **not** sufficient to decide between
(1) and (2), because a fill is not a position.

---

## 3. OBSERVATION — we have NEVER read a Gemini position

This is the hole, and it is worth stating plainly before the docs evidence.

- `/v1/prediction-markets/positions` was probed on **2026-09-18T10:48Z** and in every
  canary preflight since: **HTTP 200, shape `{positions[], total}`, `rows: 0`,
  `row_keys: []`** (`analysis/gemini/settlement_gate_20260918T104816Z.json`,
  `canary_preflight_*.json`). Never once populated.
- A scan of **all nine** `analysis/gemini/continuous_run_*.json` records finds **zero**
  keys matching `position` anywhere in the tree. `after.keys` for CONTINUOUS-4 is
  `['symbol','fills','orders_seen','order_history_raw','open_orders','active_raw']` —
  no position surface at all.
- The `position "0.02"` figure in `CONTINUOUS_4_SETTLEMENT.md` is **ours, not the
  venue's**: `gemini_settlement.Settlement.position()` is
  `sum(+qty for BUY, -qty for SELL)` over legs, so two BUYs sum to 0.02 gross by
  construction. It is arithmetic over fills, labelled `source: /v1/mytrades`.
- `positions@account` is subscribed **only** in `gemini_prediction_auth
  .verify_authenticated_ws`, which reads the 200 ack and discards every frame. No
  `positionReport` frame exists anywhere in the repository.
- CONTINUOUS-4 would have been the decisive natural experiment — both legs held into a
  resolution — but **no position read was taken between the 17:28:33Z YES fill and the
  17:30:00Z expiry**, and REALIZED cash was never attributed (`/v1/settlements` 404,
  `/v1/prediction-markets/settlements` 404, positions 0 rows, `/v1/mytrades` carries the
  two fills and no redemption row).

**So our own account evidence cannot decide (1) vs (2).** Anything below that decides it
comes from the venue's published schemas.

---

## 4. OBSERVATION — the venue's published schema says ONE SIGNED POSITION PER INSTRUMENT

Source: `developer.gemini.com` REST/WebSocket reference, read 2026-09-20.

**4a. Settled positions — verbatim field description**
(`POST /v1/prediction-markets/positions/settled`, `getSettledPositions`):

> `positions[].position` — string — **"Signed position held at settlement. Positive
> values represent a `yes` position; negative values represent a `no` position."**
> `positions[].positionQuantity` — string — "Absolute quantity held at settlement (unsigned)"

One signed number per instrument, and **`outcome` is a label for its sign**, not a
second inventory. Its example returns two rows — but on **two different instruments**
(`instrumentId` 1001 `position "125"` outcome `yes`; `instrumentId` 1002
`position "-200"` outcome `no`), which is exactly what a signed-per-instrument model
produces.

**4b. WebSocket `positions@account` — the live trading state**
(`/prediction-markets/websocket/streams#position-updates`). The `positionReport` row is:

```json
{"t": "ec", "s": "GEMI-BTC05M2606011000-UP", "a": [{"t": "position", "v": "2.5"}]}
```

Keyed by instrument symbol `s`. **There is no outcome dimension on the position row.**
The field table states `P[].a[].v` — *"`position` is signed"*. The only `o` (outcome)
field in the whole schema hangs off `settlement_payout`, not off the quantity. The docs
call this stream the one to *"use for live trading state"*, with REST as the
reconciliation snapshot of the same state — so they must represent the same thing.
**This shape cannot encode YES=0.01 AND NO=0.01 on one instrument.**

**4c. Changelog corroboration** (`/changelog/revision-history`):
- *"sign convention (**negative value = short**)"* for `positions@account`;
- *"Aligned OpenAPI contract examples with the **single-instrument YES/NO outcome
  model**"*.

**4d. Collateral Return** (`/prediction-markets/collateral-return`, released
2026-09-11) shows within-contract netting in a worked example:

| Step | Action | No positions |
|---|---|---|
| 4 | Buy 50 Yes on Team A (**reduces A No to 50**) | A: 50 |
| 5 | Buy 150 Yes on Team A (**flips A to +50 Yes**) | A: **+50 Yes** |

and states *"To reduce No exposure, use **Buy Yes** on the same contract."* Buying the
opposite outcome reduces, then flips, one signed position.

**4e. The realized-P&L fields agree.** Settled positions carry
`realizedPnl` — *"Realized profit or loss recorded from **sells prior to settlement**"* —
and `costBasis` — *"net of any prior realized P&L from **partial sells**"*. A position
that can be partially realized before settlement is a position that can be reduced.
`withCashOuts=true` exposes a `CashedOutPosition` schema (`side`, `proceeds`,
`costBasis`, `netProfit`) for *"early position exits"* — the API name for the UI's
"cash out".

---

## 5. The one piece of contrary evidence, stated rather than buried

`POST /v1/prediction-markets/positions` (`getPositions`) uses `totalQuantity`
("Total position size", **unsigned**) + `outcome`, with **no signed field**, and its
response example shows **two rows on the SAME `instrumentId` 1001** — one
`outcome: "yes"`, `totalQuantity: "125"`, and one `outcome: "no"`,
`totalQuantity: "200"`.

Under §4 that state is unreachable. I read the example as a **documentation defect**:
both rows carry byte-identical `contractMetadata` down to `contractId: "contract_123"`,
which is the signature of a copy-paste illustrating the two possible values of the
`outcome` enum rather than a reachable account state. I cannot prove that, so it is
recorded as the counter-evidence it is.

---

## 6. VERDICT

| claim | label |
|---|---|
| An opposite-outcome buy is **rejected** while the opposite position exists (option 3) | **FALSIFIED** — venue order history, §2 |
| An explicit **sell/cash-out is required first** at the API level (option 4) | **FALSIFIED** — venue order history, §2 |
| Buying the opposite outcome **nets/reduces** one signed position (option 2) | **SUPPORTED** — three independent venue schema surfaces, §4 |
| The account holds **independent** YES and NO inventories (option 1) | **NOT SUPPORTED**, one contrary doc example, §5 |
| Our account has been **observed** to hold both simultaneously | **UNRESOLVED** — we have never read a Gemini position, §3 |

**Confidence:** high on 3 and 4 (our own venue records). Strong-but-not-proven on 2
(published schemas, three surfaces agreeing, one example disagreeing; zero observations
of our own).

---

## 7. Does `Inventory(yes_qty, no_qty)` permit states the venue cannot represent? YES.

`src/gemini_execution/continuous_probe.py`:

- `apply_fill()` is **BUY-only and monotone**: `yes_qty += q` or `no_qty += q`. There is
  no decrement, no sell, no reduce path anywhere in the class.
- Therefore **any state with `matched() = min(yes_qty, no_qty) > 0` is unrepresentable**
  on a signed-scalar venue. Under §4 the venue would hold `yes_qty − no_qty`.

What survives the mismatch and what does not:

| element | fate under a netting venue |
|---|---|
| `classify()` — OPEN / EXTEND / COMPLETE | **SURVIVES.** It reads only `residual()` (plus the both-zero case). The action sequence is a function of the signed residual alone. |
| L5 overshoot **through** zero (84.3% of episodes) | **SURVIVES.** A signed position crosses zero natively; this is the one L5 behaviour the venue models exactly. |
| `residual()` / `imbalance()` / `MAX_RESIDUAL` / `MAX_IMBALANCE` = 0.04 | **SURVIVES.** `|residual|` maps 1:1 onto `|signed position|`; these gates stay meaningful. |
| `matched()` and `effective_matched_pair_cost()` | **DOES NOT.** Describes a two-leg holding the venue would not carry. |
| The `<= 0.96` pair-basis discipline | **DOES NOT** apply to a held state; it can only describe the round-trip price of a reduce. |
| `spend()` / `MAX_TOTAL_SPEND` = 0.15 | **DOES NOT.** Gross and monotone; a netting COMPLETE returns cash that `spend()` never subtracts, so the cap over-counts. |
| `MAX_YES` / `MAX_NO` = 0.10 per-leg | **DOES NOT.** Caps a per-leg gross quantity the venue does not carry. (Conservative direction: they bind earlier than reality, never later.) |
| Settlement accounting that pays **both** legs (`econ/terminal.py`) | **DOES NOT** for the matched portion — see §8. |

---

## 8. HYPOTHESIS (not yet tested): the matched-pair *arithmetic* is unchanged; the *timing* is not

Under the two-leg model, CONTINUOUS-4 is: pay 0.0106, hold both into resolution, YES
redeems 1 and NO redeems 0, payout 0.0100, **P&L −0.0006 at settlement**.

Under netting, the YES buy at 0.92 reduces the NO position. If the venue prices that
reduce as a sell of NO at `1 − 0.92 = 0.08`, then NO bought at 0.14 and sold at 0.08 is
**−0.0006 realized at 17:28:33Z** — the same number, ~91 seconds earlier. Gemini's fee
`0.0175 × C × P × (1−P)` is symmetric in `P`, so 0.92 and 0.08 carry identical fees, and
both sit outside the $0.20–$0.80 rebate band either way.

This is labelled **HYPOTHESIS**, not SUPPORTED: `realizedPnl`/`costBasis`/`CashedOutPosition`
imply a reduce is accounted as a sell with realized P&L, but nothing published states
the price at which a *buy-opposite* reduce is booked. If it is booked at anything other
than `1 − opposite_price`, the economics are **not** equivalent and this changes.

Consequences if the hypothesis holds: matched-pair P&L is **realized at completion, not
at settlement**, so the "wait for settlement" cohort would be empty for matched pairs —
our terminal-economics pipeline would be looking in the wrong place. Rebate accrual per
maker fill is unchanged either way, which matters for the rebate-first line of work.

---

## 9. Why this has NOT yet contaminated the prospective evidence

As of the 22:07Z trigger report, `gemini-l5-faithful` had:

- BTC5m: 28 windows, 6 first fills, **0 COMPLETE fills**;
- BTC15m: 9 windows, 1 first fill, **0 COMPLETE fills**;
- 2 EXTEND fills (the first in the programme), both **same-side**;
- ~66 COMPLETE rungs rested, **none filled**; `mutations_sent: 0`.

Every shadow state reached so far is a **single-sided** holding. An EXTEND is
same-side accumulation, which a signed position represents exactly. **No recorded
state has yet depended on holding both outcomes at once.** The mismatch is latent.
It becomes load-bearing on the first COMPLETE fill.

---

## 10. The one decisive read-only test, available and not yet run

`POST /v1/prediction-markets/positions/settled` — **an endpoint this repository has
never called.** The settlement probe only tried `/v1/settlements`,
`/v1/prediction-markets/settlements` and `/v1/prediction-markets/positions`. Results are
retained for the past year, so CONTINUOUS-4 (2026-09-18) is in range.

It answers the question outright:

- **two rows** for `GEMI-BTC05M2609181730-UP` (`position "0.01"`/`outcome yes`/`payout`
  0.01, and `position "-0.01"`/`outcome no`/`payout` 0) → **option 1**, and our
  `Inventory` is faithful;
- **one row**, or **no row** plus a `realizedPnl` reflecting an early exit → **option 2**
  confirmed against our own account, and the mismatch is real;
- and `withCashOuts=true` would show whether the venue booked the YES buy as a cash-out
  of the NO leg, which also settles §8.

It is a **read**. It submits no order. It cannot be run from this container: the venue
is reachable (`api.gemini.com` public endpoint answers 200 from here) but **no Gemini
credentials exist in this environment**, and the credentials must not be moved into a
transcript. Running it needs a bounded read-only job where the credentials already live.

**Not run. Awaiting owner direction, per the standing rule that evidence is returned
before anything is changed.**

---

## 11. What is explicitly NOT being done

Per the standing operating rule — separate evidence discovery from strategy changes:

- `gemini-l5-faithful` is **untouched** and still running `FROZEN_L5_NATIVE`.
- No change to `Inventory`, `classify`, `would_breach`, the risk caps, or the TTE overlay.
- No order submitted, anywhere, for any reason.
- Nothing here is REALIZED. CONTINUOUS-4's −$0.0006 remains `reconstructed_cash`, a
  cross-check over our own legs, not a cash fact.
