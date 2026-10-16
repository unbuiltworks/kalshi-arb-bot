# Continuous shadow policy evaluation — deployment plan (AWAITING APPROVAL)

**Nothing in this directory is deployed.** `RECORDER_PATCH.diff` is the exact change, held
outside the watched file on purpose (see the boundary section).

## 1. Is it running automatically today? No.

The three scored windows (2030z / 2045z / 2100z) were produced by **manual invocation**: I pulled
Railway log dumps by hand and ran `scripts/l5_shadow_policy_run.py` against them. There is no
automation. That is the gap this patch closes.

## 2. What was verified first

### Complete-window print retention — measured, and it rules out the §25 approach

Per BTC15m window, from the emitted evidence of 2026-10-16:

| window | ladder snapshots | trade prints |
|---|---|---|
| 2030z | 5,991 | 3,750 |
| 2045z | 5,121 | 4,492 |
| 2100z | 5,756 | 4,325 |

In-process retention: `LADDER_HISTORY_DEFAULT = 240`, **hard cap 1,000**; trades
`del s["trades"][:-500]`.

**§25 understated this.** Scoring at rollover from retained history needs **~6× the ladder hard
cap and ~9× the trade cap**, not the "500 → ~4,000" single change §25 described. That approach is
withdrawn. Instead `WindowAccumulator` evaluates **incrementally**, holding the opening snapshot
plus running tallies — so **no retention constant changes at all**. A test asserts the
accumulator holds no list, i.e. that it is O(1) in window length.

### Equivalence — the incremental path is not a second implementation

`WindowAccumulator(...).finish()` was compared field-by-field against `evaluate_window(...)` on
all three real windows: **no differences** in outcome, one-sided interval, queue evidence,
refusal census, selected action or any policy net. Parametrised tests cover NEITHER, one-sided
and BOTH.

### Rollover — one clock, not two

Scoring fires on the recorder's **own** ladder allowlist: `rollover(keep=ladder_keep)` runs
immediately before the existing `drop_stale_ladders(mstate, ladder_keep)`, so a window is scored
exactly when the recorder stops retaining its rungs. No second schedule to drift.

### Restart recovery — fails closed

Window state is in memory. An accumulator whose first book arrives more than `open_grace_s`
(30s) after the window start cannot know the quote or the queues ahead, so `finish()` returns
`MISSED_WINDOW_OPEN` with **no `net` and no `outcome`**. A window a restart interrupted is absent
from the results, never present and wrong. Out-of-order events are dropped and counted
(`out_of_order_dropped`), never reordered by guess.

### Idempotency — verified across a simulated restart

The store is read at construction; a slug already present is never scored again. Dry-run:
after resetting the module-level log (a redeploy), `SLUG in log.done` is `True` and the rescore
attempt returns `[]` with the store still holding one line. Store default is the recorder's
**mounted volume** (`/state/pmus-ws/l5_shadow_policy.jsonl`, volume
`18bd5702-2248-4572-9966-88a6037b95fb`), so records survive restart and redeploy. An unwritable
store increments `store_errors` and does not crash the recorder.

### End-to-end dry-run through the patched recorder

The patch was applied to a throwaway copy and driven with 2030z's real frames in the venue's own
WS schema. Result, identical to both other paths:

```
PMUS_SHADOW_POLICY {"rows":1,...,"outcome":"BOTH","one_sided_s":81.002,
  "selected":"HOLD","decisions_evaluated":572,
  "net_usd":{"HOLD":"0.037500","REPRICE":"0.037500","CROSS":"0.037500","BLIND":"0.037500"}}
```

With the flag unset: `shadow_policy_log()` returns `None`, no state key is added, and ingestion
is unchanged. **Off is byte-identical to today.**

## 3. The crossing comparison

- **Executable depth.** `completion_decision` receives the displayed size at the rung we would
  actually cross into — the ask rung for a deficient UP leg, the bid rung for a deficient DOWN leg
  (own-outcome price `1 - bid`) — and clips the size to it. `None` depth is refused
  `NO_DISPLAYED_DEPTH`, never treated as unlimited.
- **Fees.** One model, `l5_two_sided_probe.fee`: `MAKER_THETA` on a resting leg, `TAKER_THETA` on
  a crossing one. Each policy's record carries its `fee_model`; a test requires all four identical
  and that the evaluator declares no coefficient of its own.
- **Value of the resting quote.** Three bars in `completion_vs_resting`: the fee-safe pair
  ceiling, resting-price-is-better, and cross-costs-more-after-fee. The REPRICE branch prices the
  queue cost explicitly — it re-joins at the new rung and models the fill from that timestamp, so
  a reprice to a price that is not a visible rung never fills.

**One honest limit.** Each *decision* uses only information available at its timestamp, but the
*scoring* credits HOLD with the resting fill that actually occurred later. That is the correct way
to score a counterfactual after the fact, and it makes HOLD's net optimistic relative to a
decision-time expectation. Read the CROSS−HOLD deltas as "what the cross gave up against a
resting quote that did fill", not as a forecast.

## 4. Cost

Measured on 2030z (5,991 books + 3,750 prints):

| | measured |
|---|---|
| accumulator footprint | **368 KB** per live window (tracemalloc delta, opening snapshot included) |
| CPU | **0.27 s** per window total, **0.028 ms/event** |
| emitted log line | **474 bytes** per completed window |
| stored record | **2,552 bytes** per window |

At two live slugs (`PMUS_WS_LADDER_ROLLING_AHEAD=1`): **<1 MB resident**, against the recorder's
current 0.175 GB used of a **32 GB** limit (6h max 0.876 GB). CPU currently 2–9%. Log cost
~**46 KB/day** at four windows/hour; volume cost ~**240 KB/day**.

## 5. Safety boundary

| | |
|---|---|
| service | `pmus-ws-evidence-recorder` (`71b6ea99-…`) only |
| money-capable service | `l5-pmus-live` (`87f1834e-…`) — **not touched**; its `watchPatterns` is `["deploy/l5_pmus_live/DEPLOY_TRIGGER"]`, so a recorder-side commit cannot reach it |
| write verbs | the recorder has **no POST, no order, no cancel** path; verified by grep. Its only outbound are signed GETs (`http_get_json`) and a websocket whose every frame passes `guard_outbound` against a key allowlist |
| credentials | **correction to §25**: the recorder *does* hold `PMUS_API_KEY_ID` / `PMUS_API_SECRET_KEY`. §25's "holds no credentials" was wrong. The boundary is the absence of any write verb and any order path, not the absence of credentials. The patch adds no network call and no new credential use |
| default | `L5_SHADOW_POLICY_EVAL` unset ⇒ feature absent; `"1"` or any value but `YES` also reads as off |
| failure mode | every hook is wrapped; a build failure yields `None`, an ingestion error increments `errors`, an unwritable store increments `store_errors`. None can stop the capture |
| HALT / SHADOW_ONLY / risk limits | untouched. The patch is in a different service and reads no gate |

**Why the patch is a `.diff` and not a commit.** The recorder's `watchPatterns` includes
`src/pmus_ws_evidence_recorder.py` on **this very branch** (`claude/…-epkz1i`), so committing the
edit *is* the deploy. `deploy/shadow_policy/**`, `src/l5_shadow_policy_eval.py` and
`scripts/l5_shadow_policy_run.py` are **not** watched, which is why all the logic and tests ship
now with nothing deployed.

## 6. Deployment steps, on approval

1. Set on `pmus-ws-evidence-recorder` only: `L5_SHADOW_POLICY_EVAL=YES`,
   `L5_TS_EXPERIMENT_ID=shadow-policy-eval`, `L5_TS_MARKET=cpc-btc-updown-15m-*`
   (`L5_SHADOW_POLICY_STORE` defaults to the mounted volume).
   *Setting the variables alone changes nothing until step 2 — the code is not there yet.*
2. `patch -p1 < deploy/shadow_policy/RECORDER_PATCH.diff`, run the gate, commit. The commit
   triggers exactly one recorder redeploy.
3. Verify within two windows: one `PMUS_SHADOW_POLICY` line per completed window, recorder memory
   and the ladder/trade emitters unchanged, `store_errors: 0`.
4. Rollback: unset `L5_SHADOW_POLICY_EVAL` (instant, no redeploy needed — the hooks go inert), or
   revert the commit.

**Recommendation:** approve. The cost is <1 MB and 0.03 ms/event on a service at 0.5% of its
memory limit; the alternative is that consecutive settlement-adjusted results keep depending on
me pulling logs by hand.
