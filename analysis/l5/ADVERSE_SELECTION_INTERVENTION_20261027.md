# Adverse selection on first-leg maker fills: the cause, and the one lever

Status: **analysis complete, one intervention identified, nothing enabled.**
The intervention is an existing, tested, fail-closed mechanism that is
disabled by default. Enabling it needs owner authorization.

Corpus: the ten preserved BTC15m windows
(`analysis/l5/residual_exit/preserved/*.ndjson`, 2026-10-24 0100z–0230z and
2026-10-26 2100z–2130z), eight with venue settlement records, plus the
13-untouched-window result in `analysis/l5/PMUS_ENTRY_RULE_RESULT.md`.

## 1. Settled ground truth

| window | matched | matched P&L | residual | residual P&L | **settled** |
|---|---|---|---|---|---|
| 0100z | 1.04 | +0.0416 | — | — | **+0.0416** |
| 0115z | 0 | 0 | 0.96 DOWN | **−0.2304** | **−0.2304** |
| 0130z | 1.27 | +0.1905 | — | — | **+0.1905** |
| 0145z | 0 | 0 | 1.03 DOWN | **−0.3605** | **−0.3605** |
| 0200z | 1.22 | +0.0732 | — | — | **+0.0732** |
| 0215z | 0 | 0 | 1.25 UP | **+0.8625** | **+0.8625** |
| 0230z | 1.16 | +0.0812 | — | — | **+0.0812** |
| 2100z | 1.72 | +0.2660 | 1.77 DOWN | **−0.4166** | **−0.1506** |
| | | **+0.6525** | | **−0.1450** | **+0.5075** |

Two facts organise everything else:

* **The matched book never lost.** Five windows completed pairs; all five
  were positive, +0.0416 to +0.2660, mean **+$0.0816/window**. Independently
  measured on 13 untouched windows: pair net **+$0.0674/window, 95% CI
  [+0.0149, +0.1295]** — a real, CI-positive edge.
* **The residual is a lottery.** −0.2304, −0.3605, +0.8625, −0.4166: mean
  −0.036, range **1.28**. It is ~6x the pair edge in noise terms and it is
  what drives the $5 daily-loss halt.

## 2. The mechanism, from the fill records

Every live order with a presubmit record, 18 of them:

| window | first leg | filled | complement | filled | outcome |
|---|---|---|---|---|---|
| 0100z | DOWN 0.28 | no | DOWN 0.37 → UP 0.59 | both | pair |
| 0115z | DOWN 0.24 | **yes** | UP 0.70 | **no** | stranded |
| 0130z | UP 0.29 | yes | DOWN 0.56 | yes | pair |
| 0145z | DOWN 0.35 | **yes** | UP 0.61 | **no** | stranded |
| 0200z | UP 0.35 | yes | DOWN 0.59 | yes | pair |
| 0215z | UP 0.31 | **yes** | DOWN 0.63 | **no** | stranded |
| 0230z | UP 0.42 | yes | DOWN 0.51 | yes | pair |
| 2115z | DOWN 0.17 | **yes** | — | — | stranded |
| 2130z | UP 0.43 | no | DOWN 0.52 | yes | stranded |

**First legs fill; complements are the coin flip.** 7 of 9 first legs
filled, 5 of 8 complements. Every complement was quoted exactly 2 ticks
behind its touch (`L5_TS_DEPTH_TICKS=2`), so quote depth cannot explain
filled vs unfilled. `queue_ahead` does not separate them either: filled
complements sat at 1482/1826/4422/4850/7779, unfilled at 5477/6357/8717 —
overlapping.

What does explain it is in the engine's own comment:

> With one live-order slot the complement can never rest beside the first
> leg — it is refused `MAX_LIVE_ORDERS` until that leg leaves the book — so
> it is **always** placed later, against a book that has moved.

A resting maker buy `k` ticks behind the touch fills only when the price
moves `k` ticks against us, and that move is exactly what makes the
complement dearer. 0115z: filled DOWN at 0.24, complement then needed
≤0.72 and the book had gone to 0.79/0.80. So leg two is always quoted
**after** the adverse move **and** at the back of the queue. That is the
adverse selection, and it is structural, not a pricing error.

## 3. Three avenues that are closed — do not reopen

**Entry-price selection cannot work, and was already tested out of sample.**
`PMUS_ENTRY_RULE_RESULT.md`: the pre-registered floor of 0.45 (frozen at
`4c1aa98f` before any untouched window was read) was **rejected** on 13
untouched windows. It gave up **$0.2155** of completed-pair profit to buy a
zero-EV change in residual composition; 3 of its 4 strands won at mean own
cost 0.80, against 1 of 6 for the control at 0.35. The deployed 0.20 floor
was not beaten. The reason is analytic, not statistical: a leg held at own
cost `c` settles 1 with probability `c` at fair pricing, so
`E[pnl] = c(1−c) + (1−c)(−c) = 0` **for every `c`**. A floor changes the
residual's variance composition and nothing else.

My own first pass through the preserved tapes found an apparent separation
at own 0.25 (every leg opened below 0.25 stranded, 4 of 4; nothing below
0.29 ever paired). **That is the same threshold fitting, on a subset of the
same selection corpus, and it is not a finding.** Recording it here so it
is not rediscovered.

**Exiting a stranded leg is worse than holding, at every timing tested.**
Exit-at-touch value, seconds after the first-leg fill:

| window | basis | 30s | 60s | 120s | 180s | 300s | 600s |
|---|---|---|---|---|---|---|---|
| 0115z | 0.24 | 0.20 | 0.24 | 0.15 | 0.09 | 0.27 | 0.24 |
| 0145z | 0.35 | — | 0.29 | 0.34 | 0.34 | 0.19 | 0.03 |
| 0215z | 0.31 | 0.28 | 0.34 | 0.31 | 0.17 | 0.16 | 0.02 |

Net of the three stranded windows, holding to settlement returned
**+0.2716**. Exiting at the touch after a dwell returned **−0.44 (150s),
−0.41 (180s), −0.37 (240s), −0.44 (300s), −0.79 (420s)**; exiting
immediately returned **−0.0656**. Every arm is worse. The current
`L5_DEFENSIVE_COMPLETION=disabled` default is **correct** and should stay.
(2100z's own residual samples agree: the best available exit turned −0.4166
into −0.3281, recovering 21% of one loss.)

**Completion-policy variants were already tested and lost.** On PMUS no
completion policy beat plain `JOIN`, every policy's per-window CI spanned
zero, and residual *reduction* was worse (−$3.05 vs −$2.53).

## 4. The intervention: rest both legs of the round at once

`L5_TS_TWO_ORDER_PAIR` — already implemented, already tested, **disabled by
default**, `src/l5_two_sided_probe.py:second_leg_slot_refusal`.

It grants a second maker slot to exactly one kind of order: the **opposite
side of the same round in the same market** as the one order already live.
Eight named fail-closed refusals (`TWO_ORDER_PAIR_DISABLED`,
`NO_FIRST_LEG_RESTING`, `PAIR_ALREADY_RESTING`,
`RESTING_ORDER_IS_ANOTHER_MARKET`, `RESTING_ORDER_IS_THE_SAME_SIDE`,
`RESTING_ORDER_IS_ANOTHER_ROUND`, `THIS_SIDE_ALREADY_CLAIMED`, plus
unreadable-state refusals). It is explicitly **not** `max_live_orders = 2`:
it cannot permit two independent first legs, which is the 1215z failure the
`STRATEGY_EXCLUSIVE` gates exist to stop. A same-side EXTEND cannot use it.

Why this is the right lever, and why it is nearly free:

* It attacks the measured cause directly. Both quotes rest from the start,
  straddling the market, so the pair is struck at the **planned ~0.95** the
  moment the book oscillates through both — instead of leg two being
  quoted after the adverse move, at the back of the queue.
* **The capital is already reserved.** `probe_plan` sizes every round at
  `oblig_per_contract = D(1) + up_px` — a dollar a share for the resting
  DOWN short plus `up_px` for the resting UP buy. The planner has always
  charged for *two* simultaneously resting legs and bound the size
  accordingly. The engine pays that cost and then rests only one order.
  Enabling the slot collects a benefit already paid for; it changes no
  cash figure, no envelope, no `round_allocations`.
* It raises no risk bound. Deployed `L5_MAX_LIVE_ORDERS` stays 1 as the
  base; the slot lifts the ceiling by exactly one for one qualifying
  order. Every cash, exposure, residual, per-market and loss gate still
  prices each resting order as if it fills in full.
* It cannot change residual EV (zero either way) and it does not increase
  the chance of being stranded: if both rest and only one fills, the state
  is exactly today's; if both fill, the round is matched.

**The trade-off, stated.** Today's complement is adaptive but late: it is
repriced against the live book each tick (`DERIVED_FROM_CURRENT_BOOK`).
Resting both legs up front is early but committed: leg two cannot be
repriced while it rests. Early-and-committed is the better side of that
trade only if the planned pair is reachable — which the 0.96 cap already
guarantees at placement.

### Estimated net economic impact — a bound, not a point

The mechanism's entire effect is on the probability that the **second** leg
fills. It cannot change residual EV and cannot change the capital charge.

* **Floor: $0/window.** If no additional second legs fill, nothing changes.
* **Ceiling: ≈ +$0.036/window.** Three of eight settled windows completed
  zero pairs. If every one of them instead paired, the pair term gains
  3 × $0.0966 ≈ **+$0.29 over 8 windows**.
* Against the measured base of **+$0.0634/window settled**, that is roughly
  a **+57% improvement at the ceiling** — and it would also remove three
  ±$0.5 residual bets from the distribution, which matters more for the $5
  halt than the mean does.

**I cannot narrow this from the tapes, because no recorded window ever ran
with two orders resting.** Any number inside that range is a hypothesis.
Measuring it is what the validation below is for. No fill, no rebate and no
profit is claimed here.

## 5. Bounded validation plan

Not authorized. Proposed only, and it needs a separate safety review.

**Precondition.** Promote `a1b11967` and set
`PMUS_SETTLEMENT_SWEEP_CAP=4` first. Entry currently lands ~100s late
(measured +92.4s / +103.6s / +102.5s of pre-preflight tick time across
three windows); without that fix this experiment measures the delay rather
than the quoting change.

**Scope.** One market, BTC15m. **Stage 1: 4 windows shadow** on the
existing shadow-locked executor with `L5_TS_TWO_ORDER_PAIR=YES`, which
submits nothing — it records `PAIR_SECOND_SLOT` grants and the plans that
would have rested, establishing how often the slot is granted and what the
pair prices would have been. **Stage 2, only if stage 1 shows grants on
≥3 of 4 windows: 4 windows live**, unchanged `$20` exposure ceiling, `$5`
daily-loss halt, `$12` cash envelope, `round_allocations` unchanged,
maker-only, `L5_MAX_LIVE_ORDERS` base 1, pair cap 0.96, entry floor at its
deployed 0.20.

**Primary endpoint.** Settled net P&L per window from
`retire_settled_market` on a venue-terminal state with a binary
`settlementPx`. Settlement-only labelling; no mark-to-market, no estimated
rebate.

**Pre-registered secondaries.** Completed pairs per window (base: 5 of 8
windows, 1.98 matched contracts); second-leg fill rate (base 5/8 = 63%);
windows with zero pairs (base 3/8); realised pair cost versus the planned
0.95; `queue_ahead` at second-leg placement (base 1482–8717).

**Stop rules.** Halt on the first window with settled net < −$1.25; on any
`L5_LIVE_ERROR` in the money path; on any `PAIR_SECOND_SLOT` grant whose
refusal reason was not `None`; on two resting orders ever appearing in
different markets or on the same side; on cumulative −$5.

**Decision rule.** 4 clean live windows with settled net ≥ +$0.10/window
**and** second-leg fill rate above 63% justifies 8 more. A fill rate at or
below 63% rejects the intervention regardless of P&L, because P&L over 4
windows cannot distinguish the mechanism from the residual lottery — which
is the error the frozen entry rule made and the reason it was withdrawn.
