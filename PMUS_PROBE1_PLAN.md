# First real-money PMUS passive-order probe — plan for approval

Nothing deployed, nothing armed, no order submitted, no Railway setting
changed. This document is the approval request.

## 0. One thing to decide with open eyes

Test A (`4944bce1e`) returned an **economic NO-GO** for PMUS replication of
this mechanism: the mint-free subset lost **−$695.42 over 45,510 shares,
−3.20% on notional**, negative in 10 of 12 markets.

A one-order, 0.01-share probe **cannot** test that and must not be read as
testing it. What it can establish, and what nothing in simulation can, is
**execution mechanics**: does a passive PMUS order at our chosen price
actually get reached, how long does it rest, where is it in the queue, and
do our submit/ack/cancel/settle records reconcile against the venue. That
is a real gap and worth $0.01 to close. It is not economic validation, and
a fill must not be reported as one.

## 1. Observed state

### GitHub

| branch | head | relevance |
|---|---|---|
| `claude/august-l5-forensic-replay-788cml` | `e02073f0a` | deployed branch; research head |
| `claude/e1-shutdown-recovery` | `77a3f81a3` | handover on every exit; signal restore |
| `claude/e1-obligation-ledger` | `99863000e` | delayed-visibility fix |
| `claude/e1-integration-review` | `864ff2d3b` | both patches on `b88ec5eec` |
| `claude/l5-research-plan` | `4944bce1e` | Test A |

**Neither E1 safety patch is on the deployed branch.** The deployed commit
`b88ec5eec` has no `obligations.py` and no `shutdown.py`.

### Railway, read-only (project `arb-ev-wallet-shadow`, production)

| service | commit | startCommand | restart | notes |
|---|---|---|---|---|
| `l5-live-probe-oneshot` `84aa9999` | `b88ec5eec` | `scripts.l5_e1_prelive_validation` | **NEVER** | volume `/state/pmus-ws`; nothing staged |
| `l5-e1-safety-monitor` `4682d717` | `f8d57034` | `scripts.l5_e1_safety_monitor` | *unset* | has credentials |
| `pmus-readonly-recovery` `247304c7` | `5045d3ff` | `scripts.l5_e1_settlement_audit_live` | NEVER | has credentials |

### Authenticated venue state — **I could not read it**

`PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY` are **absent from this
container**. I did not read them from any service, and I will not: reading
a secret to make a convenience call is not a trade I should make. So
positions, open orders and balances are **unverified as of now**. Obtaining
them is request **D1** below — a read-only job, no order code in the image.

The last venue-confirmed reading (2026-10-08, `FINANCIAL_RECONCILIATION`):
`/v1/orders/open` → `{"orders": []}`, positions empty, balances
`openOrders 0, assetNotional 0, unsettledFunds 0`. **That is two days stale
and is not a pre-trade check.**

### Cancellation capability — asymmetric, and this matters

| mechanism | code | venue-confirmed? |
|---|---|---|
| market-scoped sweep `POST /v1/orders/open/cancel` | `l5_e1_cancel_outstanding.py` | **YES** — 20 ids on 2026-10-08, each re-read, `{"ORDER_STATE_CANCELED": 20}` |
| **exact-id** `POST /v1/order/{id}/cancel` | `l5_e1_cancel_by_id.py`, `orders.cancel_exact` | **NO live confirmation** — unit-tested only |
| in-run handover | `pair_lifecycle.deadman` | exact-id, ownership-scoped, verified per id — but only reachable from one branch on the deployed commit |

The path the probe depends on is the one never exercised against the real
venue. Request **D2** closes that before any order exists.

### Out-of-process recovery — refinement of my earlier verdict

I previously wrote that no out-of-process actor exists. More precisely: an
out-of-process **detector** exists and is deployed — `l5-e1-safety-monitor`
alerts on an order still open at handover, a passed executor deadline, an
unexpected position, and its own blindness. It holds a `ReadOnlyTransport`
and "never cancels anything -- there is no code here that could."

So **detection is covered; cancellation still requires a human running
`l5_e1_cancel_by_id.py`.** For a $0.008 exposure that is an acceptable
answer, and it is stated rather than engineered around.

## 2. Smallest safe path, using existing code only

`scripts/l5_e1_cohort_run.py` → `runner.main` → `run_cohort`, on the
deployed commit, with the probe budget narrowed to **one attempt in one
window**. No new service, no new entrypoint, no code change required for
probe #1.

### Why one attempt makes both known defects unreachable

The 20-order burst needs 20 latch slots. `PMUS_E1_MAX_ATTEMPTS=1` means
`latch.consume_attempt` can win exactly **one** `O_CREAT|O_EXCL` named slot
in the whole run, after which it raises `WindowExhausted`. That is a bound
on **files**, which no race, no stale venue view and no lost
acknowledgement can inflate.

So for probe #1 specifically:

* **the 20-order burst cannot express itself** — it requires ≥2 slots;
* **the delayed-visibility hole cannot express itself** — it only ever
  permitted an *extra* first leg, and there is no second slot to take;
* `budget.ProbeBudget.from_env` **fails closed**: missing, blank,
  non-integer, zero, negative or excessive values are a refusal, never a
  silent 240-placement default.

This is why the obligation ledger is **not required for probe #1** and **is
required before any probe with more than one attempt.** I am not claiming
the defects are fixed on the deployed branch; I am claiming the latch makes
them unreachable at this budget.

## 3. Hard caps requested

| cap | value | enforced by |
|---|---|---|
| attempts, whole run | **1** | `PMUS_E1_MAX_ATTEMPTS=1` → O_EXCL latch slot |
| windows | **1** | `PMUS_E1_MAX_WINDOWS=1` |
| outstanding orders | **1** | frozen `max_outstanding_orders`, E3 |
| markets | **1** | `PMUS_E1_GATE_EVIDENCE` + per-submission slug re-check |
| quantity | **0.01 shares** = `VENUE_MIN_QTY` | spec constant |
| entry price | ≤ 0.80 | `entry_ok` |
| complement | **none** — passive leg only | `PMUS_E1_MAX_COMPLEMENT` not armed |
| retries | **none** | latch consumed before the POST, never released |
| size escalation | **none** | single constant, no ladder |
| restart policy | **NEVER**, unchanged | existing config |

`FIRST_LEG_QTY` is `0.13` in the frozen spec, 13× the venue minimum. Probe
#1 at 0.01 therefore needs a **spec-constant change**, which rotates the
fingerprint. That is request **D3** and is the only code change I am asking
for.

## 4. Maximum possible loss

One 0.01-share maker buy at ≤ $0.80, no complement, no retry:

| | |
|---|---|
| cost if filled | ≤ **$0.0080** |
| PMUS maker fee at p=0.50 (0.0125·q·p·(1−p)) | $0.00003 |
| worst case: fills, resolves against us | **−$0.0080** |
| best case: fills, resolves for us | +$0.0020 |
| if the latch budget env is absent | **$0.00** — refusal, no order |
| if the one-outstanding rule failed entirely | still **−$0.0080** — one slot |

**Maximum possible loss: $0.01.** The bound is structural (one O_EXCL file),
not behavioural.

## 5. State recording — six separate records, never merged

Already implemented on the deployed commit; nothing new needed.

| state | source | written by |
|---|---|---|
| SUBMITTED | pre-POST intent + latch slot, fsynced | `latch.consume_attempt`, `FIRST_LEG_SUBMISSION_INTENT` |
| ACKNOWLEDGED | venue order id from the POST | `_submit_once` → `FIRST_LEG_SUBMITTED` |
| AMBIGUOUS | `SubmissionAmbiguous` — may exist, never retried | exception type, not a flag |
| FILLED | per-id `cumQuantity` from `GET /v1/order/{id}` | `order_state.read_order_state` |
| CANCELED | per-id terminal state **re-read after** the cancel | `pair_lifecycle.deadman`, `pmus_e1_postcancel_confirm.py` |
| SETTLED | venue resolution row | `settlement_audit` |
| CASHFLOW-CONFIRMED | attributed by order id only | `l5_e1_attributed_cashflow.py` |

A cancel response is a claim; the open-orders list is the fact. Nothing is
labelled realized before settlement reconciles.

## 6. Post-window comparison, action by action

Against L5's measured behaviour from the existing corpus:

| dimension | our probe | L5 reference |
|---|---|---|
| placement distance | own-bid minus our price, ticks | p50 **2 ticks** at T-30 → 0 at T-0 |
| reached vs crossed | did price come to us | **81.7% reached** / 17.9% crossed |
| time-to-fill | seconds resting | p50 **27s** (RESTING_BID) |
| at-or-below own bid | yes/no | **64.4%** of his fills |
| post-fill drift | mid at +15s / +30s | **−0.005 / −0.01** |
| fills per market | count | median **245** where he traded |
| inventory | residual shares | matched_frac 0.76–0.96 |

**Exactly one evidence-supported adjustment** will be proposed for window
#2, chosen from the single largest measured divergence, and nothing else
changes. No size increase is on the table until mechanics reconcile across
repeated clean windows.

## 7. Exact changes requested for approval

| # | change | scope | reversible |
|---|---|---|---|
| **D1** | redeploy `pmus-readonly-recovery` with `startCommand python3 -m scripts.l5_e1_order_state_verify` to read positions, open orders and balances | read-only image, no order code | yes — revert startCommand |
| **D2** | live exact-id cancel rehearsal: place nothing; run `l5_e1_cancel_by_id.py` against a **non-existent** id to confirm the route, auth and error shape | GET + one cancel POST that cannot match an order | n/a |
| **D3** | add `PMUS_E1_PROBE1_QTY` (default `FIRST_LEG_QTY`) so quantity can be set to `0.01` without editing the frozen constant; rotate the fingerprint and update the pin | one spec file + pin env | yes — revert commit |
| **D4** | cherry-pick `claude/e1-shutdown-recovery` onto the deployed branch | `shutdown.py` + `runner.py` | yes |
| **D5** | set `PMUS_E1_MAX_ATTEMPTS=1`, `PMUS_E1_MAX_WINDOWS=1`, gate + session env on `l5-live-probe-oneshot`; startCommand → `scripts.l5_e1_cohort_run` | env + startCommand | yes |
| **D6** | arm `PMUS_E1_LIVE=YES` + `PMUS_E1_ARM=<session>` — **separate approval, last** | env | yes — unset |

D1–D4 place no order and can proceed on approval. **D5 and D6 are the only
steps that make an order possible**, and D6 should be a distinct
authorization after D1–D4 report clean.

### Preconditions I will not proceed without

1. D1 shows **zero open orders and zero positions** in the probe market.
2. D2 confirms the exact-id cancel route end to end.
3. D4 is merged, so a SIGTERM during the probe reaches the handover.
4. `l5-e1-safety-monitor` is confirmed running with `E1_MONITOR_MARKET` set
   to the probe slug and its handover time set.
5. A named human is on call to run `l5_e1_cancel_by_id.py`, because no
   deployed actor can cancel.

### Not requested

No new service. No restart-policy change. No automatic relaunch. No
complement leg. No obligation ledger for probe #1 — and a hard block on
attempts > 1 until it is merged.
