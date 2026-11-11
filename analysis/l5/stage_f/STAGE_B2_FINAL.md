# Stage B2 — final reconciliation (2026-10-31), campaign halted on a defect

Campaign `camp1.camp-BpS2…`, document `f721aefc…`, scope digest `7bdec681…`.
Authorized 8 windows 1300z–1445z; **7 reached, 7 traded, 1 lost before arming**.
Halted mid-1445z on a confirmed safety-control defect. **Currently disarmed.**

## Priority 1 — order, fill and obligation reconciliation

**No outstanding live orders or obligations.** Independent `l5-halt-watchdog`
at 15:02:21: `open_orders 0`, `still_open []`, `unverified []`, `not_ours []`,
`cancel_enabled true`, `read_error null`. Every window record reads
`retired: true` with `committed_released` equal to `cost_total`.

### 1445z, the halted window

| | |
|---|---|
| fill | **DOWN 2.02 @ 0.53 wire (own 0.47)**, 14:46:11, commission 0.0000 |
| completion | none — engine disarmed at 14:51:22 before one was placed |
| settled | matched 0, residual 2.02 DOWN, `residual_redeem 2.020000` |
| **realized** | **+$1.070600** — `yes_value 0.0000`, the stranded DOWN leg won |
| cancelled / rejected | 0 / 0 |
| venue open orders after | **0** |

**Order-count anomaly, flagged not explained.** 1445z reports
`submitted: 70, acknowledged: 1, terminal: 1, filled_orders: 1, cancelled: 0,
rejected: 0`, `maker_orders: 70`. Every prior window had
submitted == acknowledged (10/10, 5/5, 9/9, 12/12). Only **one** order reached
the venue, so there is no exposure consequence and the watchdog confirms zero
open orders — but 69 counted submissions that were never acknowledged is a
reconciliation discrepancy. Most likely the post-disarm refusal path
incrementing the submitted counter; **not verified**, and it also inflated
`rebate_estimate_usd` to 0.3521 (excluded from realized, as always).

## Priority 2 — root cause, narrowed by reproduction

`tests/test_l5_residual_cap_1415z_regression.py`, **10 tests, all passing**,
drives the **real `InventoryStore`** with the five real fills:

```
resid_before 2.17  (real store: UP 5.96 - DOWN 3.79)
max_clip     2.17  (real store)
b_abs 2.17 -> exempt_open False      a_abs 4.25 -> increases True
cap min(2 * 2.17, 3) = 3             binds TRUE -> must refuse
```

**The predicate is correct. The store functions are correct. The configuration
was correct** (heartbeat echoed `k=2`; the policy is OFF only when
`PMUS_RESIDUAL_CAP_K` is unset). Given these inputs the gate refuses.

So the defect is **not** skipped-logic, **not** stale inventory across ticks
(`resid_before` is read fresh at line 7116, eleven lines before the gate), and
**not** a sign error (`residual` and `outstanding_residual` are both
UP-positive). It is **reachability, or the state handed to the gate at
evaluation time**.

Two corrections to my own earlier analysis, both found by doing this properly:

- **RETRACTED:** I cited "zero `RESIDUAL_CAP` notes in the campaign" as
  evidence the gate was skipped. It is not evidence. `INVENTORY_CLASS` notes,
  emitted unconditionally at the same call site, are **also** absent — so
  `prov.note(...)` is simply **not log-visible in this deployment**.
- **The gate is sound on overshoot**, contrary to my hypothesis that a
  "reducing" order overshooting past flat would slip through. `increases`
  compares MAGNITUDES: DOWN 6.00 against UP 2.17 lands at |3.83| > |2.17| and
  binds. My test asserted the opposite; the assertion was wrong, not the code.

**The unobservability is the blocking defect.** The gate's comment says its
treatment is "read off the record rather than inferred from what happened
next" — and it cannot be. Until that is fixed, the next incident is equally
undebuggable, which is why it is prerequisite #1.

## Priority 3 — effective HALT without a redeploy

**The mechanism already exists and is prompt.** `submission_gate` calls
`halt_gate.refuse_reason()`, which "just read the latch **over HTTP**", every
tick. Evidence it is polled live: at the 13:02:01.89 heartbeat the latch's
`record_t` was `13:01:56.99` — read ~5 s earlier. A HALT written to that store
refuses submissions within one tick, with `halt_latch: "HALT"` plus a sha256
digest as observable acknowledgement, and it fails closed (`UNREADABLE` is
treated as BLOCKED). Cancellation is never gated by it.

**What is missing is operator reach, not capability.** The store sits behind
`L5_MANDATE_STORE_URL` / `L5_HALT_URL` on an internal Railway private domain,
unreachable from this session; `halted()` reads `L5_LIVE_HALT`, an env var, so
that route needs a redeploy. **This is exactly what cost 5m53s today:**
clearing `PMUS_Q1_ARM` took effect only at the 14:51:22 restart, and 1445z
armed at 14:45:12 — **52 s after the write** — and traded in between.

Closing it requires a reachable trigger for the existing latch (no new
service: the `l5-halt-watchdog` already reaches the store and already holds
`cancel_enabled: true`). **Not implemented here** — it needs either network
reach or a deliberate decision about who may write the latch, and the
instruction forbids new infrastructure.

## Priority 4 — complete ledger

| window | matched | residual | realized | note |
|---|---|---|---|---|
| 1300z | — | — | — | **not armed**: `WINDOW_BOUNDS_UNREADABLE`, no window spent, 0 orders |
| 1315z | +0.143617 | +1.068483 | +1.212100 | 3 pairs; 1.98 DOWN strand **won** |
| 1330z | 0 | +1.025100 | +1.025100 | 0 pairs; 2.01 UP strand **won** |
| 1345z | +0.059100 | 0 | +0.059100 | 1 pair @0.9700; **zero residual** |
| 1400z | +0.015800 | 0 | +0.015800 | 2 pairs; **zero residual** |
| 1415z | +0.170984 | −1.188784 | −1.017800 | 2 pairs; 4.25 UP strand **lost** — the defect |
| 1430z | 0 | −0.949400 | −0.949400 | 0 pairs; 2.02 DOWN strand **lost** |
| 1445z | 0 | +1.070600 | +1.070600 | 0 pairs; halted mid-window; strand **won** |
| **TOTAL** | **+0.389501** | **+1.025999** | **+1.415500** | |

**Ledger tie-out, exact:** −10.24460000 → **−8.82910000**, delta
**+1.41550000**, matching the heartbeat's `realized_today_after`.

**72.5% of the result is directional.** Matched is **+$0.3895** of
**+$1.4155**. Five of seven traded windows stranded (71%); three strands won
(+$3.1642), two lost (−$2.1382). The residual line is positive only because
3 of 5 coin flips landed our way — it was +$2.09 at peak, −$0.04 after two
losses, and +$1.03 after the last one won. That volatility across seven
windows is the finding.

## Priority 4 — pre-registered test

Statistic: edge = `1.00 − pair_cost`, one observation per settled pair.

| set | n | mean edge/contract |
|---|---|---|
| Stage B1 | 8 | +0.01250 |
| Stage B2 | 8 | +0.01875 |
| **pooled** | **16** | **+0.01562** (stdev 0.03425) |

- t = 1.825, one-sided **p = 0.0340**, GO threshold +0.01408 → **threshold met**
- **drop-best-pair: n=15, mean +0.01133 → SURVIVES** (positive), though on its
  own it is t = 1.431, p = 0.0762 — positive but not significant alone

### GO IS INELIGIBLE FOR PROMOTION

Three independent reasons, any one sufficient:

1. **An unresolved safety-control defect.** A matched edge measured on a
   campaign whose residual control was not enforcing cannot authorize scaling.
2. **The sample is not the pre-registered one.** 8 Stage B2 pairs, not the
   ~21 projected: 1300z never armed, 1445z was halted mid-window, and 3 of 7
   traded windows produced no pairs at all.
3. **Interim looks were taken.** The statistic crossed at n=12, uncrossed at
   n=14 and recrossed at n=16. The nominal p is not corrected for that
   sequence of looks.

The direction is consistent across two campaigns and the drop-best-pair check
survives. That is worth something, and it is not a GO.

## What remains before another live authorization

1. Make `RESIDUAL_CAP` and `INVENTORY_CLASS` decisions reach the logs. Nothing
   else can be diagnosed until they do.
2. Then identify which submitter bypassed the gate. Audit candidates: the
   round-4 opening leg while round 3 held unmatched inventory after its DOWN
   leg was claimed and cancelled; `acc_recover` cancel-and-replace; Route B
   `book_completion`; `l5_defensive_completion`.
3. Give the existing HTTP halt latch a reachable trigger; re-verify prompt
   blocking and independent cancellation with observable acknowledgement.
4. Explain the 1445z `submitted 70 / acknowledged 1` discrepancy.
5. Have the preflight state which execution paths actually evaluate each
   control it reports.
6. Re-verify on recorded books, then a fresh bounded authorization — at the
   same limits. No capital increase, no crossing, no new services.
