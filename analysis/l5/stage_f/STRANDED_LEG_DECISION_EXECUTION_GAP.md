# The stranded-leg gap: 145 COMPLETE decisions, zero orders

**Date:** 2026-11-02 · Campaign 2026-11-01 13:30–15:15Z · Deployed SHA `4d52345d`

**Headline: the executor decided to complete 145 times and never placed one
order.** 109 of those decisions were at a pair cost **inside** the 0.96
ceiling (median 0.9200, minimum 0.6600), across **5 of 7 windows**. The
refusal that stopped them is not recorded against the decision, so no missed
completion can be attributed to a cause. That is the defect, and it is fixed
here as observability — not as a behaviour change, for a reason given in §5.

---

## 1. CONTINUOUS INVENTORY — tracking worked; execution did not

**Inventory update: correct.** Each `L5_BOOK_COMPLETION_TRIGGER` carries
`held_side`, `need`, the correct complement `side`, and a `pair_own_cost`
recomputed as the book moved. 1430z, holding DOWN 2.02 @ 0.47:

```
T+327 0.9400   T+348 0.9700   T+362 0.9900   T+369 0.9500   T+389 0.9300
T+404 0.8900   T+411 0.8700   T+432 0.8700   T+439 0.9000   T+476 0.9900
```

That is continuous re-evaluation adapting to the book, exactly as asked.

**Quoting the underweight side: decided, never executed.**

| | |
|---|---|
| `L5_BOOK_COMPLETION_TRIGGER` events | **145** |
| of which `action: COMPLETE, ok: true` | **145 (all of them)** |
| `pair_own_cost` ≤ 0.96 | **109** |
| median / minimum pair cost | **0.9200 / 0.6600** |
| `L5_LIVE_LIFECYCLE` records for 1430z | **0** |
| `still_fillable_orders` in all 191 completion evaluations | **0** |

**Missed economically viable completions: 109.** Not one became an order.

| window | COMPLETE | min pair | best `locked_pnl_usd` | T+ range | realized |
|---|---|---|---|---|---|
| 1400z | 6 | 0.9748 | +$0.0526 | 47–553 | +1.2127 |
| **1415z** | **58** | **0.6600** | **+$0.7072** | 180–599 | +1.1648 |
| **1430z** | **58** | **0.8700** | **+$0.2626** | 49–476 | **−0.9494** |
| **1445z** | **14** | **0.7600** | **+$0.4848** | 72–455 | **−0.9696** |
| 1500z | 9 | 0.9719 | +$0.0561 | 73–413 | −0.8188 |
| | | | **Σ best = +$1.5633** | | **campaign −$0.0755** |

## 2. COMPLETION REPRICING — there was nothing to reprice

Repricing cannot be the fix. In 1430z **no completion order ever rested**:
zero lifecycle records, and `still_fillable_orders: 0` on every evaluation.
The requote machinery did fire — `IMPROVEMENT_BELOW_MIN_TICKS`,
`ALREADY_NEAR_TOUCH`, `NO_IMPROVEMENT`, `ORDERS_UNCERTAIN` — but with no live
completion order to improve, those refusals are about something else.

**So "better maker repricing" is the wrong lever. The failure is upstream of
any resting order: the completion was never placed at all.**

## 3. RESCUE ECONOMICS — already implemented correctly, no change needed

The engine already performs exactly the comparison asked for, using only
decision-time information:

- **`PAIR_BEATS_CARRY` (104 decisions)** — completing beats carrying the
  residual, measured against `breakeven_pair: 0.9532` with
  `locked_pnl_usd` stated;
- **`RISK_REDUCING_COMPLETION` (41)** — the weaker case above breakeven,
  flagged `within_preferred_target: false`;
- no settlement outcome enters either, so no hindsight selection.

**No change proposed here.** Taker crossing is not enabled, not used in any
figure above, and not proposed.

## 4. FIRST-LEG PREVENTION — no separation in what we record

LIVE first legs only (39 pre-submits, `coid` prefix `l5f-`):

| | median runway | median queue-ahead | spread | entry T+ | own px |
|---|---|---|---|---|---|
| windows that stranded (n=4) | **576s** | 1,838 | 1 tick | 19–28 | 0.45–0.47 |
| windows that completed (n=3) | **578s** | 1,490 | 1 tick | 21–292 | 0.39–0.46 |

Runway, queue-ahead, spread, entry time and entry price are **effectively
identical**. At entry, stranded and completed windows look the same.

**Opposite-side depth cannot be tested.** The pre-submit `book` carries only
`bid`, `ask` and `spread` — **0 of 39 records carry depth fields**. The
`queue_ahead` figure is for *our* side, not the complement's. That is a
measurement gap, and I am reporting it as one rather than substituting a proxy.

## 5. THE IMPROVEMENT

### Exact failure mechanism

`src/l5_pmus_live_runner.py:6096–6134`. The decision is emitted **before** the
attempt, and the attempt's result is thrown away:

```python
emit("L5_BOOK_COMPLETION_TRIGGER", {... "decision": rec ...})  # ok:true, locked 0.2626
...
self.executor.handle(None, book, market=..., controller={...})  # result DISCARDED
else:
    self.handle(row, now)                                       # result DISCARDED
return rec                                                      # returns the DECISION
```

The refusal lands only in a shared `self.stats` counter. The heartbeat shows
four live candidates during 1430z, by recency:

| refusal | age at 14:44:51 |
|---|---|
| `book_completion:ECONOMICS:COMPLEMENT_DEPTH_INSUFFICIENT` | **3.0s** |
| `book_completion:ALREADY_RESTING_AT_OR_BETTER` | 6.0s |
| `SAFETY_OVERLAY:MAX_LIVE_ORDERS` | **7.0s** |
| `book_completion:ORDER_ALREADY_RESTING` | 7.0s |

**None of them is attributable to any decision.** An aggregate age cannot be
joined to a `key`.

### Expected economic impact and uncertainty

- **Nominal prize: $1.5633** — the best `locked_pnl_usd` per window, against a
  campaign that realized **−$0.0755**.
- **Executable fraction: UNKNOWN, and I am not claiming it.** The freshest
  economics refusal is `COMPLEMENT_DEPTH_INSUFFICIENT`, which means some or
  all of those pair costs may be top-of-book prices without the depth to fill
  `need: 2.02`. A `locked_pnl_usd` computed before a depth check is not an
  executable profit.
- **Direct impact of this change: $0.** It is observability.

### Why not a behaviour change now

The two leading mechanisms demand **opposite** fixes:

| if the blocker is | the fix is |
|---|---|
| `COMPLEMENT_DEPTH_INSUFFICIENT` | size the completion **down** to displayed depth (partial completion) |
| `MAX_LIVE_ORDERS` / `ORDER_ALREADY_RESTING` | **withdraw** the stale opposing quote to free a slot |

Shipping either blind risks withdrawing a good opening quote to chase a
completion that could never have filled. **The highest-impact change is the
one that tells them apart**, because it converts a $1.5633 question into a
measured answer at zero economic risk.

### Code path changed

- `src/l5_pmus_live_runner.py` — capture both placement results, emit
  `L5_BOOK_COMPLETION_RESULT` paired to the decision's `key`, plus a
  `_bc_capture` static normaliser that reports `captured: False` rather than
  guessing on an unreadable result.
- **Nothing else.** No price, size, limit, gate, ceiling or cancellation path
  is touched. A refusal can never be reported as a submission.

### Regression tests

`tests/test_l5_book_completion_result.py`, **14 tests**: `None` is a refusal
not a submission; a named refusal is carried verbatim; **all four candidate
refusals survive capture**; an explicit submission records its order ref; a
refusal beats an `ok` flag; an unreadable result is `captured: False` and
never a verdict; the capture is AST-verified to call no `submit`/`cancel`/
`place`/`handle` and to take no `self`; both placement branches are captured
(`_bc_capture(` appears exactly twice); no bare placement call remains; and
the trigger still precedes the result.

**Full suite: 2,834 passed, 0 failed** across the 96 suites touching the
runner. Two existing tests failed on the first run and both are fixed without
weakening either guard:
- `test_provenance_is_not_misrepresented` read `seen[-1]`, silently depending
  on the trigger being the last event. It now selects by label and
  **additionally** asserts the result follows the trigger.
- `test_it_cancels_nothing_and_reconciles_nothing` scans this method's source
  **text** for the verb. My explanatory comment contained it; I reworded the
  comment rather than relax the scan, and noted in the source why that wording
  is deliberate.

### Validatable in the next bounded live campaign

**Yes, decisively.** One window that ends with unmatched inventory produces
the attribution — the campaign does not need to be profitable, or even to
complete a pair, for the measurement to land. Four windows will give several
hundred paired decisions on current evidence.

## 6. Status

Not deployed. The executor runs `4d52345d`; this is on
`claude/compassionate-allen-wic64u`. Deploying it needs the executor's
`DEPLOY_TRIGGER` and a deployment-parity check, and arming still needs the
fresh `L5_CAMPAIGN_APPROVAL` only you can install.

No risk limit changed, no taker crossing enabled, nothing armed, no new
service, $0 cost.
