# The smallest bounded continuous-live envelope — proposal

**Item 6 of the owner's priority order.** Item 7 is STOP, and this document
ends there: nothing continuous and live runs without the statement at the
bottom, signed by the owner.

---

## 1. What this run is for, stated before the numbers

**It is not for profit, and it must not be judged on P&L.** Two separate
bodies of evidence say so plainly:

- **The adapted-policy failure decomposition** (`L5_STATUS.md`):
  `FAILURE_MODE = BROAD_LEAKAGE`. The directional residual's sign is set
  entirely by whether the leftover side won — 38 of 38 settled windows — and
  that bet wins **0.3421** of the time. Across 17 counterfactual probes,
  **none** raises the residual win rate to 0.50. The side a resting maker
  accumulates is the side that is becoming wrong.
- **This venue's habitat** (`SEGMENTED_HABITAT_1.md` §4): **87.9%** of
  measured adjacent-cycle pairs saw **zero** public prints, with one contract
  going 198.5 s without a single one.

A run whose purpose were profit would be refused by its own evidence base.

**It is for two specific, currently unobtainable facts:**

| # | the fact | why it needs live orders | done when |
|---|---|---|---|
| **E1** | the **first statable per-segment fill rate** | shadow places nothing, so it has no fills and can never produce one | **10 fills with their decision state recorded** (`segments.MIN_FILLS_FOR_A_RATE`). Today that count is **1** — only C8B's fill has a book behind it; C4, C5 and C6 predate book-state capture |
| **E2** | the **first maker rebate credit attributable to a known window** | `$0.51` of credits are confirmed on `/v1/transfers` but carry **no** order id, symbol or fill linkage (`REBATE_ECONOMICS_1.md`); only running maker volume in a window we fully instrument can test whether a credit follows it | a credit posts whose timing can be tested against a window whose maker volume we recorded |

At ~0.83 fills per order-placing window (5 fills across the 6 windows that
placed an order — C2, C4, C5, C6, C7, C8B), **12 windows is the smallest run
that plausibly reaches E1**, since every fill it produces will carry the full
decision state that only C8B's does today.

That is an extrapolation from 6 windows, not a forecast. If the true rate is
half of it, the run ends at its bounds with 5 or 6 fills and E1 stays
unanswered — which is a result, and not a reason to extend the budget. The
budget is sized by the evidence it must produce, never by an expected return.

**If E1 and E2 both come back negative, that is a result.** It would say this
venue does not support the L5 policy's execution assumptions, which is a fact
about **model B** and is never a reason to alter **model A**.

---

## 2. The envelope

### Per window — the CONTINUOUS-8 envelope, byte-for-byte unchanged

`envelope_sha b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17`
— unmoved across every window to date.

| bound | value |
|---|---|
| order size | **0.01** |
| spend per window | **$0.15** |
| max actions (cohort) | **12** |
| residual exposure | **0.04** units |
| inventory imbalance | **0.04** units |
| per leg | **0.10** units |
| concurrent markets | **1** |
| no new OPEN/EXTEND after | **TTE 60 s** |
| withdraw all at | **TTE 30 s** |
| execution | **maker-only (MOC)** — no taker cross, no SELL, no IOC/FOK, no forced flatten |
| pair-cost ceiling | **none** |

### Per run — what continuous operation adds

| bound | value | does it actually bind? |
|---|---|---|
| `max_windows` | **12** | **yes** |
| `max_total_spend_usd` | **$1.00** | **yes — this is the real control** |
| `max_run_seconds` | **7200** | yes |
| `max_concurrent_markets` | **1** | yes |
| `daily_realized_loss_stop_usd` | −$0.25 | **NO — see below** |

### The loss stop does not protect anything today, and I will not present it as if it did

`RunState.realized_usd` moves **only on a venue-confirmed cash movement**.
**No attributable cashflow has ever been observed on this venue** — Gemini
REALIZED is `$0.00` across every window to date, and every settlement so far
is labelled EXPECTED. A stop keyed to a quantity that never moves **cannot
fire**.

So the honest statement of the financial bound is:

> **Maximum capital at risk is `max_total_spend_usd` = $1.00.** The worst case
> is every cent unmatched and settling to zero: **−$1.00**. The −$0.25
> realized-loss stop is a placeholder that will not act, and it should not be
> read as a second line of defence.

I am **not** proposing to replace it with a spend-based or mark-based stop in
this run. A mark-based stop would make an unrealized number load-bearing,
which is exactly the collapse this programme refuses everywhere else. The
$1.00 cap is a real bound and it is enough at this size.

---

## 3. One addition to the window, and it is read-only

**A bounded post-fill observation phase.** Four of five fills have **no
post-fill book at all**, and C5's filled 57 s after its window had stopped
reading. Adverse movement after a fill — a dimension the owner named — is
therefore measurable on exactly one fill in the whole programme.

Proposed, for approval rather than built:

- it begins **only after** terminal cleanup has proven the book clear —
  the same positive evidence `rollover_safe` already demands;
- with nothing resting, **nothing can fill during it**;
- it is **public reads only** (presented book + `/v1/trades/{symbol}`), on the
  same cadence, for **≤ 90 s or until TTE 0**, whichever comes first;
- it consumes **no** cohort slot and **no** spend, and it cannot place,
  cancel, or reach a session;
- it runs **only** in a window that actually filled.

This is the one change that makes E1's segmentation answerable next time
rather than the time after.

**Already closed and in the tree:** the public print tape is now recorded
beside every live decision (`book_state.flow`), proven byte-equivalent against
an unobserved run including when the reader raises. `envelope_sha` and
`executable_sha` are unmoved; `bundle_sha` moves to
`82ad2166fec84ab4ba368eee9faa69c41cbeb54f1563b783409576681d00a6fe`.

---

## 4. Cohorts — the run may not mint its own budget

**One cohort per window, all created before the run, by an explicit act.**

- `COHORT-2026-09-19-K01` … `K12`, budget **12** each — the frozen
  `CP.MAX_ACTIONS`, not a new number;
- each chained to its immediate predecessor's ledger sha256, preserving the
  existing single-hop `verify_predecessor` semantics;
- **C5 (3/6), C6 (6/6), C7 (6/6), C8 (0/6) and C8B (11/12) are not touched,
  not reused and not rewritten**, and the historical ledger stays
  byte-for-byte at `89cf9de6d949cf88`;
- if a window would need a thirteenth slot, it **stops**. It does not roll
  into the next cohort.

Nothing in `src/gemini_execution` calls `cohort.create`, and
`test_nothing_in_the_package_creates_a_cohort_automatically` pins that by AST
scan. A system that can mint its own mutation budget has no budget.

---

## 5. Every way this run stops

All eight failure states are terminal for the **run**, not just the window.
There is no retry loop, no "assume flat", no sleep-and-hope:

`ENTRY_BAND_UNREACHABLE` · `PREFLIGHT_DISAGREES` · `STATE_UNREADABLE` ·
`EXECUTION_CONTRADICTION` · `UNKNOWN_TERMINAL` · `ORDERS_LIVE_AFTER_STOP` ·
`RISK_BUDGET_EXHAUSTED` · `DAILY_LOSS_STOP`

The rollover gate is the load-bearing one: a window may not roll forward
without **positive** evidence that the previous one is finished — a readable
open-order list with none of our orders in it, and a readable canonical
execution view. C5 is why: it stopped its controller, left two orders
resting, and one filled 57 s later. A continuous system that rolls on
anything less repeats that on a loop.

`RiskBudget` is a frozen dataclass whose only public method is `labels`.
**Bounds tighten or stop; nothing can self-increase.**

---

## 6. What this run will NOT establish, said now rather than after

- **Not profitability.** $1.00 of maker orders at 0.01 cannot establish edge
  in either direction, and the residual decomposition says the direction to
  expect is negative.
- **Not a rebate rate for our own fills.** Gemini's credit rows carry no fill
  linkage. The best this can do is test whether a credit *follows* a window
  whose maker volume we recorded. Fill-level attribution stays refused.
- **Not L5 fidelity.** Fidelity is measured against L5's own actions, not
  against Gemini outcomes. A poor result here is evidence about model B only.
- **Not concurrency, size or aggressiveness.** All frozen. The promotion order
  stays PATIENCE → FREQUENCY → SIZE → CONCURRENCY, and this run is frequency.
- **Not a stable refusal rate.** Book refusals were 2.3% in one soak and 23.0%
  in the next, 90 minutes apart. Usable cycles per window are not plannable
  from a constant, and the run must survive either.

---

## 7. Pre-live checklist

| # | item | state |
|---|---|---|
| 1 | one canonical authoritative fill view, fails closed on contradiction | **DONE** — `executions.py`, bundle member, 26 tests |
| 2 | exact C8B replay pinned as a test | **DONE** — read 13 carries `yes 0.01 / residual +0.01 / spend 0.0031 / n_fills 1` |
| 3 | rebate reader over authenticated surfaces, no scraper | **DONE** — `/v1/transfers` credits, `/v1/tradevolume` denominator, cross-checked by `/v1/mytrades` |
| 4 | continuous production-lifecycle shadow soak | **DONE** — 2 soaks, 8/8 windows, 173 cycles, 0 mutations |
| 5 | fill conversion + habitat + rebate economics, segmented | **DONE** — `SEGMENTED_HABITAT_1.md` |
| 6 | shadow proven to have no reachable order path | **DONE** — property of what it calls, not a flag |
| 7 | run-level budget cannot self-increase | **DONE** — frozen dataclass, public surface pinned to `["labels"]` |
| 8 | cohorts cannot be created by the run | **DONE** — AST scan test |
| 9 | rollover gate demands positive evidence | **DONE** — `rollover_safe`, 23 tests |
| 10 | public print tape recorded beside every live decision | **DONE** — behaviour-neutrality proven both ways |
| 11 | 12 cohorts pre-created and chained | **NOT DONE — deliberately.** They are created only after authorization, so an unapproved run has no budget to spend |
| 12 | bounded post-fill observation phase | **NOT BUILT — proposed in §3 for approval** |

Suite: **6540 passed, 27 skipped, 1 xfailed.**

---

## 8. Proposed authorization statement

> I authorize ONE bounded continuous live Gemini BTC5m L5 run using
> authorization ID `OWNER-2026-09-19-GEMINI-CONTINUOUS-LIVE-1`, executable SHA
> `50996ee0f7d36126273299919a9be8bb8d4cf6135a1a0bd63f427cbee37659bd`, the
> frozen envelope SHA
> `b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17`, bundle
> SHA `82ad2166fec84ab4ba368eee9faa69c41cbeb54f1563b783409576681d00a6fe`, and
> mutation cohorts `COHORT-2026-09-19-K01` through `K12`, each with the frozen
> bounded budget of 12 slots, created before the run and chained to their
> immediate predecessor. The run is bounded by `max_windows 12`,
> `max_total_spend_usd $1.00`, `max_run_seconds 7200` and
> `max_concurrent_markets 1`. Maximum capital at risk is $1.00 and I accept
> that the −$0.25 realized-loss stop cannot fire on this venue.
>
> Its sole purpose is evidence: to reach a statable per-segment fill rate and
> to test whether a maker rebate credit follows a window whose maker volume
> was recorded. It is not authorized to pursue profit and its result must not
> be read as evidence about L5 fidelity.
>
> The per-window envelope is unchanged: size 0.01, spend $0.15, residual 0.04,
> imbalance 0.04, per leg 0.10, maker-only MOC, no taker crossing, no SELL, no
> IOC/FOK, no forced flatten, no pair-cost ceiling. A bounded read-only
> post-fill observation phase of at most 90 seconds may run AFTER terminal
> cleanup has proven the book clear; it may place nothing, cancel nothing and
> consume no slot.
>
> Fail closed on unreadable or ambiguous authoritative state, on an
> unexplained disappearance, on a disagreement between execution sources, or
> on an actual risk-envelope violation. Every failure state ends the RUN.
> Roll forward to another window only on positive authoritative evidence that
> the previous one is finished.
>
> No second run, automatic re-arm, budget reset, cohort creation, scaling,
> concurrency increase, size increase, SELL, IOC/FOK, taker crossing, forced
> flatten, strategy or controller change, or risk-envelope change without new
> approval. C5 stays 3/6, C6 6/6, C7 6/6, C8 0/6 and C8B 11/12; none is
> reused and the historical ledger stays byte-for-byte at `89cf9de6d949cf88`.

**If the post-fill observation phase in §3 is approved**, building it changes
`run_continuous.py`, which moves `bundle_sha` — and the statement above would
then need the new bundle SHA. `executable_sha` and `envelope_sha` do not move:
the phase places nothing and touches no bound. Say which you want and I will
report the resulting hashes before anything is armed.

**No continuous live run will start without that statement.** Until then the
system stays in SHADOW, where it is now.

---

## 9. STOP

Awaiting authorization before any continuous real-money execution.
