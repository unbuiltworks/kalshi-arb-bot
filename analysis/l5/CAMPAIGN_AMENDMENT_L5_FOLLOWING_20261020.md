# Campaign Amendment 1 — L5_FOLLOWING for the remaining three windows

**Status: AWAITING OWNER AUTHORIZATION. Not applied.**

Amends campaign `6b89ebf988f92edd289875c6c8d6ec8de4c3bc75ab936b3030b35021672a0056`
(issued 2026-10-20T22:19:16Z, expires 2026-10-21T02:19:16Z).
Prepared 2026-10-20, on deployed commit `f6b67aa2`.

---

## 1. What this amendment changes

**One selector variable, and the reversal of two I set while diagnosing.**

| Variable | Now | After | Why |
|---|---|---|---|
| `L5_WINDOW_STRATEGY` | `TWO_SIDED` | `L5_FOLLOWING` | names the L5-following path as owner of each window |
| `L5_TS_MARKET` | `cpc-btc-updown-15m-*` | `` (empty) | makes the two-sided engine inert, so exactly one path is live |
| `L5_TS_EXPERIMENT_ID` | `l5-ts-campaign-20261020T2219z` | unchanged | harmless once `L5_TS_MARKET` is empty; `TS.active()` needs both |

Nothing else. No limit, ceiling, budget, document, or ledger is touched.

I set the two `L5_TS_*` values at 22:48Z to clear the 2230z deadlock while the
campaign was still authorized for TWO_SIDED. They are listed here for reversal
because under `L5_FOLLOWING` an active two-sided engine would plan a pair and
then be refused at `STRATEGY_EXCLUSIVE_L5_FOLLOWING`
(`src/l5_live_execution.py:5257`) — wasted work, and two strategies reading one
cash envelope is the 1215z starvation the exclusivity gate exists to prevent.

## 2. Why — the evidence that changed the choice

The only completed, settled, economically valid live pair this programme has
produced came from **L5_FOLLOWING**, not TWO_SIDED:

> UP 2.46 at own 0.90 and DOWN 2.46 at own 0.06 — pair cost exactly 0.96,
> redeemed 2.46 for **+$0.0984 settled**.

TWO_SIDED has never produced a completed live pair.

The 2230z window under TWO_SIDED produced **37 refusals and zero orders**:
`order.submitted: false`, `order.reason: STRATEGY_EXCLUSIVE_TWO_SIDED`,
`venue_order_id: null`, and `two_sided: null` in all 55 decision records — the
two-sided engine never ran, because `TS.active()` requires both
`L5_TS_EXPERIMENT_ID` and a matching `L5_TS_MARKET`
(`src/l5_two_sided_probe.py:245`) and the pin held a stale single slug.

## 3. Execution behaviour under L5_FOLLOWING

**Maker-only, resting at L5's own price. No taker leg, ever.**

`src/l5_pmus_live_runner.py:868` constructs the live executor with
`maker_treatment=True, maker_only=True` — hardcoded, no variable involved.

- `maker_only=True` — **no taker fallback at all.** A crossing order is never
  placed. This satisfies the campaign's maker-first restriction and the
  standing prohibition on automatic late taker behaviour copied from mature
  August L5.
- `maker_treatment=True` — an action the frozen rule classifies `MAKER` and
  then refuses `STRICT_NOT_EVALUABLE` is **rested at L5's own price** rather
  than dropped (`src/l5_live_execution.py:3799`, logic at `4511`). That refusal
  concerns replay evidence — whether a hypothetical resting order *would* have
  filled — which live execution does not need to prove: the order is placed and
  the venue answers.
- The price is floored to the venue tick, never rounded up, because up is the
  aggressive direction for a buy (`quantize_price_non_aggressive`). A price at
  or below the minimum tick refuses `PRICE_BELOW_MIN_TICK`.

**This is why the amendment will actually produce orders.** All four conditions
the maker treatment requires were already satisfied in every 2230z record:
`maker_treatment` true, `mode: MAKER` (38 of 55), `reason:
STRICT_NOT_EVALUABLE` (all 38), and `l5_price` present (0.45–0.53). The *only*
thing stopping a submission was the selector.

Sequence per window: maker OPEN small → reassess → optional same-side EXTEND →
reassess → patient fee-safe COMPLETE when price improves → settle → audit.

## 4. Every limit still enforced — verified against the gate chain

The selector sits in the **middle** of the submit chain. It chooses which
strategy may submit; it bypasses nothing. Order of gates in
`src/l5_live_execution.py`:

| # | Gate | Line | Relative to selector |
|---|---|---|---|
| 1 | `SAFETY_OVERLAY: RESIDUAL_ABOVE_CAP` | 5109 | **upstream** |
| 2 | **`PAIR_COST_CEILING`** | 5201 | **upstream** |
| 3 | *strategy selector* | 5246 / 5257 | — |
| 4 | `BALANCING_WOULD_OVERSHOOT` / `BALANCING_DOES_NOT_REDUCE` | 5272 / 5291 | downstream |
| 5 | **`SUBMISSION_CLAIM`** — three class budgets, flat backstop, completion reserve | 5358 | downstream |
| 6 | `SAFETY_OVERLAY` — cash envelope, outstanding-order count | 5387 / 5448 | downstream |
| 7 | `SUBMIT_OUTCOME_UNKNOWN` | 5465 | downstream |
| 8 | `ACK_WITHOUT_VENUE_ID` | 5490 | downstream |
| 9 | `ACK_NOT_PERSISTED` | 5513 | downstream |

Confirmation of each approved limit:

| Limit | Enforced | How |
|---|---|---|
| **$5 total exposure** | YES | cash envelope reserved before the wire; `risk_gate.max_usd_per_market = "5"`; gate 6 |
| **$5 daily realized-loss halt** | YES | `day_realized.halt_usd = "5"`, reads `utc_day_realized_usd` from settlement rows only; currently `would_halt: false`, room **$5.0984** |
| **$0.96 combined pair ceiling** | YES | gate 2, **upstream of the selector** — applies to both strategies identically |
| **1 outstanding order** | YES | `L5_MAX_LIVE_ORDERS`; gate 6 |
| **OPEN 8 / COMPLETE 12 / REPLACE 16** | YES | gate 5, `AUTH.claim_submission(..., klass=...)` — durable single-use tickets |
| **28 total backstop** | YES | gate 5; does **not** bind a risk-reducing order, so it cannot strand inventory |
| **Completion reserve (2 per obligation)** | YES | gate 5; refuses a new OPEN unless capacity to close it is reserved |
| **3 remaining windows** | YES | campaign ledger, durable; `windows_spent: 1`, `windows_remaining: 3` |
| **Campaign expiry 02:19:16Z** | YES | `L5_LIVE_AUTH_NOT_AFTER`, unchanged |
| **BTC15m series only** | YES | `L5_LIVE_AUTH_MARKET_SERIES = cpc-btc-updown-15m-`, unchanged |
| **Shadow lock / HALT latch / watchdog** | YES | unchanged; latch CLEAR/PRESENT, watchdog sweeping 20s, `cancel_enabled: true` |
| Fail-closed on ambiguity | YES | gates 7–9: register before resting; unknown outcome, missing venue id, or unpersisted ack all refuse |

## 5. Preserved — explicitly not reset

- The campaign document and its nonce — **unchanged**, same SHA.
- **2230z stays consumed.** `windows_spent: 1`, `windows_remaining: 3`.
- All submission budgets stay at **0 spent** and are **not** reset; they key on
  `L5_EXPERIMENT_ID` (`l5-campaign-20261020T2219z`), which is unchanged.
- Inventory, realized P&L, `retired_markets`, account-wide exposure and the
  cumulative loss gates key on `<L5_LIVE_ROOT>/inventory.json`, not on any
  variable in this amendment.
- Today's realized **+$0.0984**; all-time **−$8.2688**; `unattributed_usd: 0`;
  `undated_settlements: []`.
- The `UNPROVEN_LEGACY_ATTRIBUTION` cost-basis gap stays carried as unknown.
  Not zeroed, not erased.

## 6. Not included in this amendment

No new strategy. No new service, volume, or infrastructure. No change to
capital, risk, or loss limits. No change to the pair ceiling. No additional
trading permission beyond the one already granted for this campaign. No reset
of any safety counter or latch.

## 7. Verification after authorization, before any order is trusted

1. Deploy; confirm `shadow_lock: false`, `blocked: false`, `reason: OPEN`.
2. Confirm `campaign.approval_state: approved`, same document SHA,
   `windows_remaining: 3` — **not** 4.
3. Confirm class budgets still read 0 spent against
   `l5-campaign-20261020T2219z`.
4. Confirm `L5_BUDGET_SELFCHECK.all_passed: true`, `missing_checks: []`.
5. First window: report `order.submitted`, `venue_order_id`, `order.state`, and
   any refusal **by name**.
6. Report real orders and fills **separately from shadow activity**.
   `L5_LIVE_DECISION` is the money path. `L5_SHADOW_DECISION` /
   `shadow_controller` (`provenance: CONTROLLER_GENERATED`) is model output and
   is never counted as a fill.
7. Labels stay strict: submitted → accepted → filled → settled → realized.
   Only exchange-confirmed fills count. Only settlement is realized.

---

## Authorization

Approving this amendment authorizes exactly one change: the window-owner
selector moves from `TWO_SIDED` to `L5_FOLLOWING`, and the two-sided engine is
made inert, for the **three remaining windows** of campaign `6b89ebf9…` under
**every limit already approved and listed in section 4**.
