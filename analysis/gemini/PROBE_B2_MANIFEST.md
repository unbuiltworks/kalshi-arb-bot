# PROBE B2 — frozen bounded manifest, awaiting owner authorization

**Status: PREPARED, NOT AUTHORIZED, NOT ARMED. No order has been placed and
none can be.** `static_readiness` verdict is `FAIL` and `mutations_sent` is 0.
This document is the whole of what approval would cover. Nothing outside it is
proposed.

## 1. Why B2 exists at all

Probe B (`OWNER-2026-09-23-GEMINI-Q1-AB-2`) ran four BTC5m windows and produced
six fills, one matched pair at 0.62 and a settlement-implied −$0.33. It was
reclassified **INVALID FOR THE MAKER_IMPROVE HYPOTHESIS**: neither
`WC.Driver(...)` construction site passed `placement`, so the driver ran its
default placement while the spec declared `maker_improve`. B remains valid only
as a frozen BTC5m control.

B2 is the *same experiment*, run on a chain that actually delivers the declared
placement. It is not a bigger experiment, a different strategy, or a new
hypothesis.

## 2. What changed since B, and what did not

| | Probe B | Probe B2 |
|---|---|---|
| spec_id | `btc5m_q1_improve` | `btc5m_q1_improve` (same) |
| **spec_sha** | `bfe7ba23199c544d30f25cd1c730d92e62d0bea534274b1c00feff961242a650` | **identical** |
| placement declared | `maker_improve` | `maker_improve` |
| placement **delivered to Driver** | **no** (defect) | yes, and verified off the driver after construction |
| windows / q / CAP | 4 / 1 / CAP2 | 4 / 1 / CAP2 (unchanged) |
| envelope | `live_q1` | `live_q1` (unchanged) |
| cohort chain | `…AB-B-W01..W04` (spent) | `…B2-W01..W04` (fresh, 12 each) |

The strategy is byte-identical: `spec_sha` did not move. `live_cohort` is
metadata and sits outside the fingerprint by design, so repointing the budget
chain does not change the experiment. Swapping `placement` *does* move
`spec_sha` — that was proven in the P0 chain proof.

**Nothing was added.** No `COMPLEMENT_SEEK`, no extra maintenance budget, no
crossing, no pair-ceiling relaxation, no wider ladder, no directional selection,
no technical indicators, no persistence change.

## 3. Identity to be authorized

| field | value |
|---|---|
| proposed authorization id | `OWNER-2026-09-23-GEMINI-Q1-B2-1` |
| spec_id | `btc5m_q1_improve` |
| **spec_sha** | `bfe7ba23199c544d30f25cd1c730d92e62d0bea534274b1c00feff961242a650` |
| **executable_sha** (`ws_continuous.py` bounds) | `13cd59840138c3b2d98dabab98809ff3f628328b9d835686cb9021bf04e0a83a` |
| **bundle_sha** (15 files, ordering included) | `7c747900d2562c61407792ae6517a3a35af4a17d16e4af6be4015cc2900b19a0` |
| **driver envelope_sha** (all declared envelopes + live order-path bounds) | `be8b544b35929149c9ecea41e6a2461824426a59debb3a6e5d4e275f9250445d` |
| active envelope sha (`GEMINI_L5_LIVE_ENVELOPE_Q1_V1` alone) | `4e2818d53735999cda6c3de439f4a9e87a3cb2a98f9e7c10c2af125e145ed6b5` |
| symbol family | `GEMI-BTC05M` |
| cohort chain | `COHORT-2026-09-23-BTC5M-B2-W01` … `-W04` |
| latch counterfactual | `latch_path_exists=false`, `would=CONSUME_FRESH`, `reaches_mutation_boundary=true` |

`AB-1` and `AB-2` are both spent; their latches stay on disk untouched. A fresh
id and a fresh expiry are required — an expired or reused id arms nothing.

## 4. Exact maximum cash exposure

Per window, enforced on the order path by `GEMINI_L5_LIVE_ENVELOPE_Q1_V1`:

| bound | value |
|---|---|
| `action_size` | 1 contract |
| `max_actions` | 12 (a cancel and a reprice each charge a slot) |
| `max_total_spend_usd` | **$2.50** |
| `max_committed_usd` (filled + resting + the frame being sent) | **$2.50** |
| `max_residual_usd` | $2.00 |
| `max_yes_contracts` / `max_no_contracts` | 2 / 2 |
| `max_same_side_run` | 2 |
| `max_working_orders` | 2 |
| `max_concurrent_markets` | 1 |

Fees are additive and are **not** inside the spend cap. Gemini maker fee is
`ceil_cent(0.0175 × C × P × (1−P))`; at `C=1` its maximum over all prices is
`ceil_cent($0.004375) = $0.01` per fill, and at most four fills can occur in a
window (2 YES + 2 NO).

```
per-window worst case cash out   = $2.50 spend + $0.04 fees = $2.54
four windows                     = $10.16
```

**Maximum cash at risk across the whole of Probe B2: $10.16.**

That is the *structural ceiling* — the most the order path can spend even if
every bound is ridden to its limit and every contract settles worthless. It is
not a forecast. Probe B, the same shape, actually moved six fills and a
settlement-implied −$0.33.

The `--max-loss-usd 10.00` stop is a **second, independent** brake, not the
ceiling: it sums SETTLED windows only, so a window already in flight can finish
before it trips. The binding number is $10.16, and it is bounded by the
envelope rather than by the stop.

## 5. Stop conditions

The run halts, places nothing further, and writes its artifact when **any** of:

1. `cumulative_economic_loss_usd ≥ $10.00` over SETTLED windows
   (`--max-loss-usd 10.00`). The terminal settlement sweep now runs after the
   loop, so a loss that settles at the break is counted rather than skipped —
   this was the defect fixed and pinned by `tests/test_cohort_settlement_stop.py`
   (5 of its 7 assertions fail before the fix).
2. drawdown ≥ $5.00 (`--max-drawdown-usd 5.00`).
3. the four cohort ledgers are exhausted (48 actions total, 12 per window). No
   rollover and no automatic successor.
4. any envelope bound would be breached by the next frame — refused by name.
5. the spec's declared `placement` is not present on the constructed driver →
   `REFUSED_PLACEMENT_NOT_DELIVERED`, before any mutation.
6. the authorization id, `executable_sha`, `bundle_sha` or `envelope_sha`
   differs from the authorized value → refused at the five-layer gate.
7. the AB-2/B2 latch is already consumed → `STOPPED_NO_LATCH`, zero orders.
8. the authorization expiry has passed, or `GEMINI_CANARY_LIVE` is not `YES`.
9. unreadable book, or a position list that cannot be read — an unread account
   is never treated as a flat one.

There is **no automatic relaunch** after completion or crash. A second run needs
a second authorization.

## 5b. One further repair, found by the proof itself

Running the end-to-end proof (`analysis/gemini/audit/p23_chain_proof.py`)
surfaced a second route to Probe B's exact outcome, which the P0 delivery fix
could never have caught.

`ws_continuous` branches on `self._placement == "maker_improve"`. Every other
string — including a typo of that one — falls through to the default. A spec
declaring `"maker_improvee"` would hash differently, be reviewed and authorized
under its own identity, be delivered faithfully to the driver, **pass the
post-construction delivery check** (the driver received exactly what the spec
declared), and run the frozen control while reporting the experiment.

The existing `PLACEMENTS` guard lived only in `_declare()`, which sees the
module's own specs and nothing built directly. It is now enforced in
`__post_init__`, on the type, so an unimplemented mode is a construction error
before any hash exists to approve. `tests/test_placement_modes_closed.py` pins
both halves, including an AST anti-drift check that the declared set equals the
modes `ws_continuous` actually compares against.

**This did not move `spec_sha`** (`bfe7ba23…` is unchanged — the experiment is
identical), nor `executable_sha`, nor the driver `envelope_sha`. It moved
`bundle_sha` only, because `live_probe_spec.py` is a bundle member. The value in
§3 is the post-repair one.

## 6. The one question B2 answers

> Does `maker_only_improve` — posting at `bid + 1 tick` instead of at the bid,
> and standing down entirely when that would cross the ask — change fill rate,
> fill price, or pair economics against the frozen BTC5m control?

Probe B is the control (same spec, default placement, 4 windows, 6 fills, one
pair at 0.62, settlement-implied −$0.33). B2 is the treatment. Six windows
cannot rank two arms statistically and this manifest does not claim they can;
B2 establishes only that the transformation **reached the wire** and what it
did to the prints it produced. `_record_placement()` now writes
`native_price → submitted_price`, `improved`, `placement`, book, inventory and
budget state for every action, flushed at the top of `_drain_events` so a
session with no event queue still persists its rows.

## 7. Exact invocation

```
python -m src.gemini_execution.run_continuous \
  --auth-id OWNER-2026-09-23-GEMINI-Q1-B2-1 \
  --family btc5m \
  --envelope live_q1 \
  --entry-tte-band 240:280 \
  --ledger analysis/gemini/continuous_ledger.jsonl \
  --out  analysis/gemini/cohort_run_B2_{utc}.json \
  --wire analysis/gemini/cohort_wire_B2_{utc}.json \
  --cohort COHORT-2026-09-23-BTC5M-B2-W01 \
  --max-loss-usd 10.00 --max-drawdown-usd 5.00
```

`placement`, `quote_band` and `action_size` **cannot** travel on argv
(`ARGV_CANNOT_CARRY`). They reach execution only via `probe_spec=`, and
`placement` is then re-read off the constructed driver and compared. This is the
defect that invalidated Probe B, and it is now structurally impossible to repeat
silently.

## 8. What approval must supply

Three constants and two environment values, none of which this session can
create:

1. `arming.EXPECTED_AUTH_ID = "OWNER-2026-09-23-GEMINI-Q1-B2-1"`
2. `bundle.AUTHORIZED_BUNDLE = "7c747900…900b19a0"`
3. `ws_continuous.REQUIRED_AUTH_ID` / `AUTHORIZED_SHA` / `AUTHORIZED_ENVELOPE`
4. `GEMINI_CANARY_LIVE=YES`, `GEMINI_CANARY_AUTH=<the id>`,
   `GEMINI_CANARY_AUTH_EXPIRES=<a fresh UTC instant>`
5. a run host that holds the Gemini credentials — this container does not, so
   the credentialed half of readiness (`terms`, `positions_readable`) has never
   passed here and must pass there before the first mutation.

All three constants are currently the disarmed sentinels. Installing them does
not move `executable_sha`, because `_AUTH_LINES` blanks single-line
authorization assignments before hashing — that property is what lets the owner
approve a hash and then install the grant against it.

## 9. Readiness as it stands right now

`verdict: FAIL`, and every reason is one of the two expected classes:

- **no authorization installed** (4 reasons) — correct; this is what approval fixes.
- **no credentials in this container** (2 reasons) — correct; this is what the
  run host fixes.

Everything else passes: `spec ok`, `family_agrees ok`, `cohort_chain ok`
(4 ledgers, 48 actions, all `predecessor_intact`), `cohort ok`, `latch ok`.

---

**This manifest requires only explicit owner approval. It does not authorize
itself, and no part of it has been executed.**
