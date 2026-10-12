# Why completion succeeded in aleher-tundur and failed in petbru-alecan

Existing evidence only: `analysis/l5/r4_orders.jsonl` (64 orders, per-order
outcome / rest / end_reason), `analysis/l5/r4_fidelity_decomposition.json`,
`analysis/l5/r4_decisions.jsonl`, the already-committed
`analysis/l5/complete_no_fill_forensics.json` (n=868 completes, with book
extrema), and the engine source. Nothing re-run, no threshold fitted, no new
code, no orders.

## The answer in one line

**Completion did not fail on mechanics. It failed because the entry price
decided whether a profitable completion was ever reachable, and in
petbru-alecan it was not.**

The engine quoted its completion **at its own pair ceiling** — a pair cost of
exactly **$1.00, par** — eighteen times over forty-five minutes, at a price
that never once changed, and nothing filled.

## The two sequences, from the order tape

### petbru-alecan: 19 orders, 1 fill

| # | time | rel | side | px | qty | rest | fill | end_reason |
|---|---|---|---|---|---|---|---|---|
| 1 | 17:45:46 | open | YES | **0.38** | 5.24 | 13.1s | **5.24** | filled |
| 2 | 17:48:46 | complete | NO | 0.62 | 5.00 | 31.1s | 0 | deadline |
| 3 | 17:53:57 | complete | NO | 0.62 | 5.00 | 31.7s | 0 | deadline |
| 4 | 17:53:57 | complete | NO | 0.61 | 5.24 | 31.4s | 0 | deadline |
| 5–19 | …to 18:33:06 | complete | NO | **0.62 ×15** | 4.92→3.31 | ~31s each | **0** | deadline ×15 |

Price variance across 18 completion attempts spanning 45 minutes: **zero.**
Seventeen at 0.62, one at 0.61. Every one died on the ~31 s deadline. No
rejection, and **no safety refusal ever blocked a COMPLETE here.**

### aleher-tundur: 16 orders, 5 fills

| # | rel | side | px | qty | fill | pair cost | end_reason |
|---|---|---|---|---|---|---|---|
| 3 | open | YES | 0.47 | 5.31 | 5.31 | — | filled |
| 4 | complete | NO | **0.51** | 5.20 | 5.20 | **0.98** | filled |
| 5 | complete | NO | 0.50 | 0.11 | 0.11 | 0.97 | filled |
| 6,7 | open | NO | 0.48/0.49 | — | 0 | — | **repriced:touch_moved** |
| 8 | open | YES | 0.49 | 5.10 | 5.10 | — | filled |
| 9 | complete | NO | **0.22** | 3.24 | **3.24** | **~0.71** | filled |
| 10–16 | complete | NO | 0.22/0.21 | 1.86 | 0 | — | **rejected: ORDER_REJECTED_ZERO_FILL ×7** |

The matched average of **0.8798** is produced almost entirely by **order 9**:
NO at **$0.22** against YES at ~0.49, a 29-cent pair margin. That is the market
handing us a cheap complement, not the engine engineering a fill.

## Why 0.62, and why it never moved

Recovered from the engine's own decision record for petbru-alecan:

    chosen_px          0.61
    spring_bound_px    0.62
    hard_bound_px      0.620
    pair_cost_max      1.00      <- the ACTIVE rung's hard ceiling
    spring_pair_target 1.00
    observation_cap    0.98
    regime             spring_target

`spring_bound_px = pair_cost_max − avg_cost = 1.00 − 0.38 = 0.62`. The engine
was quoting **exactly at its ceiling**, deliberately (`regime:
spring_target`), for a pair at **par**.

Two consequences, and both are the failure:

1. **A bid at par offers nothing.** YES was 0.38, so NO's fair value was ~0.62.
   We bid fair value, as a maker, with no concession and no margin. There was no
   reason for anyone to cross to us, and nobody did — 0 of 18.
2. **The ceiling forbade following the price.** The committed n=868 forensics
   records petbru-alecan as the **#2 market for missed reprices, 14 of them** —
   i.e. in 14 of its 18 unfilled completes *L5's own touch moved ABOVE our
   resting price*. Above 0.62 is above par. So the only prices that would have
   filled were prices the pair ceiling correctly refused. **The refusal was
   right; the position was already uncompletable at non-negative pair cost.**

Combined with `requote_relations()` returning `("open","extend")` — COMPLETE is
excluded from the touch-moved re-quote unless an explicit flag is armed; the
forensics measured **0 of 868 COMPLETEs** ever exiting that way against 36 OPEN
and 35 EXTEND — the engine simply let each order die and reposted the identical
par price.

## The per-attempt questions, answered and un-answered

| question | petbru-alecan | aleher-tundur |
|---|---|---|
| opposite side at an executable price? | **No.** 14 of 18 had the touch above our 0.62 ceiling | **Yes**, twice decisively: 0.51 and 0.22 |
| displayed depth sufficient? | **UNANSWERABLE** — see below | **UNANSWERABLE** |
| did the engine submit? | Yes, all 18 | Yes, all 10 completes |
| rested / rejected / safety-refused? | **18/18 rested ~31 s then `deadline`.** 0 rejects, **0 safety refusals on the COMPLETE path** | 5 filled, **7 rejected `ORDER_REJECTED_ZERO_FILL`** on the last 1.86 |
| price & inventory after? | price **constant 0.62**; qty decayed 5.00→3.31; residual stuck at 5.24 YES throughout | inventory 5.31 YES → +5.2 NO matched → … → 1.86 YES residual |
| settlement-adjusted outcome | **−$1.9912**, the entire basis lost | **+$1.9955** |

Fee-adjusted: **$0 adjustment** — all fills were maker and PMUS makers pay no
fee. Rebate accrual $0.0154 / $0.0558, unconfirmed, not netted.

**Depth is permanently unanswerable for this sample.** The forensics' `rest_book`
recorded only extrema — `min_opp_ask_during_rest` and `max_touch_during_rest` —
and **never size**. No depth was captured per order, and the Railway log dumps
the forensics reads (23 files) are no longer on disk, so it cannot be
re-derived. That is a recording gap, stated rather than estimated.

## Against L5's actual behavior — and we did the opposite on both legs

This is the part that matters most, and it cuts against "pair it immediately".

In petbru-alecan, **L5's own next action was EXTEND, eighteen times.** The
decomposition records that market's 18 `unmatched_usd_cap` refusals with
`l5_relation_at_refusal.sampled = {extend: 18}`.

So at the same 18 decision points:

- **L5 wanted to EXTEND** the 0.38 YES — build the position further.
- **The cap refused** every one.
- **We instead chose COMPLETE** 18 times, at par, and filled none.

We refused to do what L5 did, and did something L5 was not doing. Insisting on
immediate pairing at par was *our* behavior, not his, and it is what left 5.24
shares naked into a settlement that paid the other side. Whether L5's extends
would have been better is **not** established — the held-to-settlement
counterfactual for the suppressed extends across the sample is **−$90.78** — but
the record does not support treating immediate completion as the safe default.

## The smallest justified change

**It is not arming the existing flag, and it is not moving a threshold.**

`PMUS_SPRING_COMPLETE_REQUOTE` already exists (`DEFAULT_COMPLETE_REQUOTE =
False`, shipped dark, documented). It is the obvious candidate and the
forensics' headline supports it — 22.3% of unfilled completes had a reprice the
book would have met. **But it would not have rescued petbru-alecan**, because
there the touch moved *above par* and the pair ceiling would have refused the
re-quote anyway. The existing forensics does not check that: it measures whether
the touch moved and whether the book reached it, and **never whether the pair
cost at the new touch was inside the ceiling.**

So the headline 22.3% mixes two populations that call for opposite actions:

- *reachable and affordable* → arming the flag captures it;
- *reachable but above the ceiling* → the flag is useless and the real problem
  is the entry.

**The smallest justified change is the one predicate that splits them:** in the
existing `src/l5_complete_no_fill_forensics.py`, partition the 176 / 106
"reprice existed" counts by whether

    avg_cost + max_touch_during_rest <= pair_cost_max

One predicate, one existing script, no engine change, no new service, no
threshold. It is the measurement that decides whether arming the flag is
justified at all — and on petbru-alecan's evidence the answer may well be no.

It needs the Railway log dumps the script reads, which are not on disk, so it
runs either against a re-harvest or against the next tape forward.

### Separately, for the owner, not a change I will make

The active rung sets `pair_cost_max = 1.00`. Quoting COMPLETE at par earns zero
gross pair margin and relies on the maker rebate — the engine's own comment says
the measured rebate "pays for a pair up to ~1.003" — and **that rebate is
unconfirmed in our accounting.** petbru-alecan is what par looks like when it
fails: no concession, no fill, full basis lost. The `observation_cap` is 0.98.
Changing the rung's ceiling is an owner decision (`OWNER 5567269295 (R4)`) and
is deliberately not proposed as a fitted number here.

## Two measurement defects found on the way

1. **`touch_join` is a tautology.** `pmus_spring_l5_session.py:2729` sets
   `"touch_join": relation_ in ("open", "extend")`. It is not measured against
   the book at all, so it carries no information about whether an order joined
   the touch — `l5_entry_quality.py` already warns "not a feature, an arithmetic
   fact". Worse, `l5_fidelity_gate.py:207–217` computes a `join_rate` from it and
   PASS/FAILs against `touch_join_rate_min`. **That gate is measuring the share
   of orders that are not COMPLETE**, and can never fail for the reason it
   claims to test. It gives false assurance about placement quality.
2. **No per-order depth was ever recorded**, so sufficiency of displayed size
   cannot be evaluated for any of the 868 completes.

## Status

Executor disarmed, acquisition flag off, `PMUS_SPRING_COMPLETE_REQUOTE` off,
engine unmodified, no orders, no new infrastructure.
