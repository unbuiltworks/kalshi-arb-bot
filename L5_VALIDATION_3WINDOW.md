# 3-window maker-only validation — prepared, NOT armed

**Question it answers:** can a controller-generated opening fill now produce
a **venue-confirmed maker complement** and a **positive settled pair**?

Nothing is armed. No campaign exists. No limit, cap or strategy parameter is
changed: pair cap **0.96**, crossing **disabled**, Route B's tolerance
**unchanged**.

---

## 1. Live state at preparation (deployment `ac6e6da5`, SHA `38342446`)

| | |
|---|---|
| preflight | `ok: false` — `fail: [owner_arm_token, absolute_deadline, campaign_approval]` |
| transport | `armed: false` |
| `live_orders` | **0** |
| `pending_terminal` | **[]** |
| `quarantined` | **[]** |
| `committed_usd` | **{}** — empty, so every filled market has been retired |
| day realized (2026-10-30) | **−$0.72580000** |
| all-time realized | **−$10.31700000** |

**No outstanding orders and no unreconciled positions.** The
`residual_net ≈ −$236` lines in the heartbeat are shadow-variant
simulations, not our ledger — impossible under the $20 live cap.

## 2. Completion flags, confirmed

| flag | state | source |
|---|---|---|
| `L5_BOOK_COMPLETION` | **ON** | `book_completion:{DECIDE_COOLDOWN,NO_ACTION_YET,NO_INVENTORY}` all incrementing; only reachable past `BC.enabled()` |
| `L5_CONTROLLER_OPEN` | **ON** | `controller_open_enabled: true` |
| `ts_two_order_pair_enabled` | **false** | `TS.two_order_pair_enabled()` |
| `defensive_execution_enabled` | **false** | `DEF.enabled()` |
| `maker_only_forbids_defensive_crossing` | **true** | `AUTH.EXECUTION_MODE` vs `DEF.enabled()` |
| `maker_pair_ceiling` | **0.96** | `TS.max_pair_cost()` |
| `BREAKEVEN_PAIR` (Route B target) | **0.9532** | `TS.BREAKEVEN_PAIR`, unchanged |
| `max_live_orders` | **1** | `R.max_live_orders()` |
| `max_worst_case_deficit_usd` | **3.00** | `VG.parse_deficit_bound` |
| `window_strategy` | **TWO_SIDED** | env |
| `series_allowlist` | `cpc-btc-updown-15m-` | code constant |

So the corrected route is **live and deciding now, disarmed** — it evaluates
and records while the transport refuses every submission.

`L5_EFFECTIVE_RUNTIME_CONFIG` did not report `BC.enabled()`; `b703237d` adds
`book_completion_enabled` and `book_completion_breakeven_pair` as observed
rows. **It should ride the validation's deploy, not justify its own.**

## 3. Must be confirmed before arming

| item | current | required |
|---|---|---|
| campaign approval | `approval_stale` | fresh, series-covering, `max_windows: 3` |
| `L5_LIVE_AUTH_NOT_AFTER` | EXPIRED (−34363 s) | in the future, covering all 3 windows |
| Q1 handshake | absent (`PMUS_Q1_LIVE=NO`) | present — **Option B now requires it alongside the approval** |
| runtime config | `PASS: false`, 8 rows `NO_APPROVAL_IN_FORCE` | `PASS: true` |
| deployed SHA | `38342446` | carries `b703237d` so the flags self-report |

The 8 currently-failing graded rows are all `required: NO_APPROVAL_IN_FORCE`
— they are the *absence of an approval*, not misconfiguration, and they clear
when a fresh approval is installed.

## 4. Configuration

| | |
|---|---|
| markets | 1, `cpc-btc-updown-15m-` |
| windows | 3 |
| execution | `MAKER_ONLY`, post-only, crossing disabled |
| total exposure | **$20** |
| per-market worst-case deficit | **$3.00** |
| daily realized-loss halt | **$5** |
| resting orders | 1 |
| pair cap | **0.96** |

## 5. Per-window report (the required form)

For each of the 3 windows:

| field | definition — and what will NOT be accepted as it |
|---|---|
| **opening fill** | venue `cumQuantity` > 0 on the controller's opening leg: side, own price, wire price, qty, `commission`, venue order id, cash delta. *Not* an ACK, not a resting order |
| **complement quote** | the own price `complement_order` priced, the touch at that instant, depth behind the touch, the pair cost it implied, and `judged_as`. **A quote is not a submission** |
| **complement submission** | a venue-**ACKed** post-only order with a venue order id. **A priced quote that was never sent is not a submission** |
| **actual fills** | venue `cumQuantity` > 0 on the complement, with `avgPx` and `commission`. **Executable, reachable and resting are none of them a fill** |
| **pair cost** | effective, from the ledger's realised basis: `(leg_cost[UP] + leg_cost[DOWN]) / min(up_qty, dn_qty)` |
| **remaining inventory** | `|up_qty − dn_qty|` at window close, with its cost basis and whether that basis is observed or a `$1/share` placeholder |
| **settled net P&L** | from `retire_settled_market` against an authoritative binary settlement only. Not mark-to-market, not modelled pair profit, not an estimated rebate |

## 6. Structural failure vs ordinary maker non-fill

**This is the distinction 0230z got wrong.** That window submitted **zero**
complements, so it was never a maker non-fill — it was structural, and
calling it "the complement didn't fill" would have hidden the defect for
another campaign.

**A — STRUCTURAL COMPLETION FAILURE (a defect; stop the validation)**
The route could not even try. Signatures:

* `book_completion:NO_ACTION_YET` incrementing **while inventory is held** —
  the coupling defect is back;
* `book_completion:REENTERED:CONTROLLER` **0** while inventory is held;
* `COMPLEMENT_PAIR_ABOVE_CAP` with **`completion_ceiling: null`** — the cost
  basis was destroyed, which is the 0230z cause exactly;
* `book_completion:LEDGER_UNREADABLE:*`;
* `CONTROLLER_INTENT:CONTROLLER_NO_PROBE_LEG`;
* any admissible economics with no submission attempted.

**B — ECONOMIC REFUSAL (working as designed; not a failure and not a
non-fill)**
The route tried and declined on price or state, with a readable ceiling:

* `COMPLEMENT_PAIR_ABOVE_CAP` with a **numeric** `completion_ceiling`;
* Route B declining against the 0.9532 target;
* `COMPLEMENT_DEPTH_INSUFFICIENT`, `COMPLEMENT_WOULD_CROSS`,
  `PRICE_OUT_OF_BAND`, `NOTHING_TO_MATCH_ON_GRID`;
* `ORDER_ALREADY_RESTING`, `DECIDE_COOLDOWN`, `PAST_ENTRY_CUTOFF`.

**C — ORDINARY MAKER NON-FILL (the measurement we actually want)**
Only countable when **all** of these hold:

* the complement was **venue-ACKed** with a venue order id;
* it **rested** at a recorded own price, with the depth ahead recorded;
* `cumQuantity` stayed **0** until the cancel margin;
* it was cancelled by exact owned id and the cancel was **confirmed
  terminal**.

**C is the first real joint-maker-fill datum on this venue**, and replay
cannot supply it: PMUS publishes no print endpoint (`/trades`, `/history`,
`/ohlc`, `/candles`, `/price-history` all 404), only a cumulative
`sharesTraded` lower bound. A window of C is a **valid result**, not a
failure.

## 7. Outcomes

* **Success:** ≥1 venue-confirmed **matched pair** on controller-opened
  inventory, **settled**, with its effective pair cost. The programme has
  never produced one.
* **Informative:** complements submitted, none filled (**C**) — the datum
  replay cannot give.
* **Stop immediately:** any **A**; any automatic halt; any ambiguous
  submission; any unreconciled position at window close.

The objective is **repeatable positive settled pair economics** — not another
successful order submission.
