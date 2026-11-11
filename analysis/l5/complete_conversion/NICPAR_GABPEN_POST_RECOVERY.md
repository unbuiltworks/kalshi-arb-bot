# Maintenance audit close + post-recovery COMPLETE forensics
## `aec-itfme-nicpar-gabpen-2026-09-14`, 11:51:53Z -> 12:06:53Z

Read-only. No production, risk, cap, band, retry, shell or execution-mode
change was made or is proposed here.

---

# PART 1 — MAINTENANCE AUDIT, CLOSED

## 1.1 TWO CORRECTIONS TO MY OWN PRIOR REPORT

**(a) "Unchanged realized P&L proves nothing filled" was invalid and is
WITHDRAWN.** `daily_realized_pnl_usd` moves only on venue-confirmed
*settlement*. A fill during the blind window on an unsettled market would
leave it untouched. It was never evidence about fills and it is struck from
the audit. The figure itself is accurate (-15.3350 at 09:15:02Z and after);
the inference drawn from it was not.

**(b) THE ORDER COUNT WAS WRONG. It is FOURTEEN, not ten.** The per-market
figures carried through this incident were right and their total was not:

```
hubmak-kuzdmy   4  +  alemon-mardes  2  +  matvav-filcal  4  +  janwyg-artlab  4  =  14
```

Counted directly from the `fill_reconciliation.reconciled.still_open` id lists
in each session_end row. "Ten" has been repeated since the halt; it is an
arithmetic error of mine, and every downstream statement of it is corrected.

## 1.2 THE AUTHORITATIVE PER-ORDER ARTIFACT

Every one of the fourteen carries a venue-authored terminal state in its own
`end_reason`:

```
rejected:ORDER_REJECTED_ZERO_FILL:<order_id>:ORDER_STATE_REJECTED:
        fields=action=ORDER_ACTION_BUY;avgPx=0.0000;cashOrderQty=0.00
```

`ORDER_STATE_REJECTED` is terminal — a rejected order never rests, so it
cannot fill later — and the venue states `avgPx=0.0000` and
`cashOrderQty=0.00` for each. This is per-order venue evidence, not our
inference, and it is the artifact the audit now rests on.

| market | session_end | order ids | rest_s |
|---|---|---|---|
| `aec-setkameua-hubmak-kuzdmy` | 09:19:33 | CFZZAM1G2TMQ, CFZZAM2D8TMQ, CFZYXAADWTMM, CFZZ5ZQ5PTN0 | 0.87-1.11 |
| `aec-itfme-alemon-mardes` | 09:20:29 | CFZZAMMC2TMQ, CFZZ29KY0TN1 | 0.35-0.90 |
| `aec-itfme-matvav-filcal` | 09:23:31 | CG0112Q60TN5, CG00RVA8ATN0, CG00Z01E8TMY, CG00FHMQ4TN2 | 0.87-0.94 |
| `aec-itfme-janwyg-artlab` | 09:24:33 | CG0108JYTTMP, CG0114JWTTN3, CG018ATT6TMY, CG0108Q42TMP | 0.09-1.01 |

Two supporting facts, neither load-bearing on its own: the recovery sweep at
11:36:22Z read the account authenticated and returned `seen 0, cancelled [],
foreign [], errors []`; and the durable ledger recorded zero fills on all four.

## 1.3 WHY THE ENGINE STILL SAID `still_open`

The reconciler marked all fourteen `unreadable` AND `still_open` with
`open_orders_block_flat_claim: true`. That is not a contradiction: the
rejection was the venue's synchronous response at submit time, and the
reconciler's confirming GET then failed with 503, so it fell back to the
conservative assumption. **The engine was more cautious than its own evidence
warranted — which is the correct direction to fail.**

## 1.4 VERDICT — ORDER-LEVEL RESOLVED, POSITION-LEVEL NOT FETCHED

| layer | status |
|---|---|
| per-order final status | **RESOLVED** — all 14 venue-rejected, zero fill, terminal |
| open/orphan orders | **RESOLVED** — authenticated account-wide sweep, zero |
| durable ledger | **AGREES** — zero fills, zero inventory on all four markets |
| per-market POSITION read | **NOT PERFORMED** |
| per-market TRADE-ACTIVITY walk | **NOT PERFORMED** |

The four markets were never re-selected after recovery, so no
`settlement`/`activity_walk` row exists for them. Post-recovery they appear
only inside `fidelity_gate` per-market decision tallies and, for
`janwyg-artlab`, as one of three recorder-tape liveness probes in
`recovery_gate_waiting` — neither is position evidence.

**Therefore: `RECOVERY_FLAT_ORDER_CONFIRMED`, not bare `RECOVERY_FLAT`.**
Authenticated order evidence plus the durable ledger establish it at the
order layer. Position and trade-activity evidence for those four slugs was
never fetched and is not claimed. Anyone wanting the stronger label needs a
per-market authenticated position read, which no artifact in hand supplies.

## 1.5 THE REJECTION SIGNATURE CAME BACK AFTER RECOVERY

The same `ORDER_REJECTED_ZERO_FILL` signature reappeared at **12:06:53Z**, ~30
minutes after the venue recovered, on three consecutive COMPLETE orders. The
engine's own label:

```
rejection_class          implementation bug candidate:rejected_at_non_crossing_price
                         (third also: ;authenticated_book_crossed)
rejection_margin_ticks   5.00
fresh_opp_ask            0.5700       our px 0.52
venue_market             MARKET_STATUS_OPEN, active true, closed false
infra_rejections         0
complete_reject_streak   1 -> 2 -> 3        complete_reject_rate 1/2 -> 2/3 -> 3/4
```

Five ticks from crossing, market open, not an infra rejection. **This is the
same failure mode that preceded the outage, still live.** It is the single
largest bucket in the decomposition below.

---

# PART 2 — ACTION-BY-ACTION, POST-RECOVERY

25 maker orders across six sessions, all `tte>10m`, band 0.25-0.75, pair
ceiling 1.00, concurrency 1, maker-only, `rest_policy: model`, tick 0.01
(`declared_by_venue`).

```
sess_end stage              rel      side   px    qty   fill  rest_s  end_reason
11:53:42 spring_open_1      open     YES   0.41   2.43  2.43    6.8   filled_on_cancel
11:53:42 spring_complete_2  complete NO    0.53   2.43     0   15.9   repriced:touch_moved:0.56
11:53:42 spring_complete_3  complete NO    0.56   2.38  2.38   28.7   filled
11:53:42 spring_open_4      open     YES   0.42   2.38     0   31.4   deadline
11:53:42 spring_complete_5  complete NO    0.54   0.05  0.05   10.4   filled
11:56:44 spring_open_1      open     YES   0.40   2.50  1.44    5.7   filled_on_cancel   (PARTIAL)
11:56:44 spring_extend_2    extend   YES   0.40   2.50     0   25.1   repriced:touch_moved:0.44
11:56:44 spring_complete_3  complete NO    0.52   1.44     0   31.4   deadline
11:56:44 spring_complete_4  complete NO    0.52   1.44     0   31.2   deadline
11:59:02 spring_extend_1    extend   YES   0.44   2.27     0   26.5   repriced:touch_moved:0.48
11:59:02 spring_complete_2  complete NO    0.49   1.44     0   32.0   deadline
11:59:02 spring_complete_3  complete NO    0.49   1.44  1.44   18.3   filled
11:59:02 spring_open_4      open     YES   0.45   2.22  2.22   30.1   filled
12:02:16 spring_complete_1  complete NO    0.45   2.04  2.04    3.6   filled
12:02:16 spring_open_2      open     YES   0.49   2.04  2.04   24.9   filled
12:02:16 spring_extend_3    extend   YES   0.49   2.04  2.04   13.1   filled
12:02:16 spring_extend_4    extend   YES   0.49   2.04  2.04   21.4   filled
12:04:41 spring_complete_1  complete NO    0.54   2.32     0    1.2   repriced:touch_moved:0.56
12:04:41 spring_extend_2    extend   YES   0.43   2.32     0   32.0   deadline
12:04:41 spring_extend_3    extend   YES   0.43   2.32  2.32   20.6   filled
12:04:41 spring_complete_4  complete NO    0.54   2.32     0    1.8   repriced:touch_moved:0.56
12:06:53 spring_complete_1  complete NO    0.52   2.17  2.17   27.7   filled
12:06:53 spring_complete_2  complete NO    0.52   2.32     0    0.4   REJECTED
12:06:53 spring_complete_3  complete NO    0.52   2.32     0    0.9   REJECTED
12:06:53 spring_complete_4  complete NO    0.52   2.32     0    0.4   REJECTED
```

## 2.1 COMPLETE attempt features

`touch` = the order's own recorded `l5_touch_px`; `dTk` = ticks our price sat
below it; `minOpp` = closest complement ask seen while resting (`rest_book`);
`polls` = book polls while resting.

```
sess_end stage              px    qty  fill  rest_s touch dTk  minOpp polls  mode
11:53:42 spring_complete_2  0.53  2.43    0   15.9  0.53   0   0.5800   14   M6_MARKET_MOVED_PAST_US
11:53:42 spring_complete_3  0.56  2.38 2.38   28.7  0.56   0   0.5800   26   FILLED
11:53:42 spring_complete_5  0.54  0.05 0.05   10.4  0.54   0   0.5800    9   FILLED
11:56:44 spring_complete_3  0.52  1.44    0   31.4  0.52   0   0.5600   28   M1_COMPLEMENT_NEVER_REACHED
11:56:44 spring_complete_4  0.52  1.44    0   31.2  0.52   0   0.6000   27   M1_COMPLEMENT_NEVER_REACHED
11:59:02 spring_complete_2  0.49  1.44    0   32.0  0.49   0   0.5200   27   M1_COMPLEMENT_NEVER_REACHED
11:59:02 spring_complete_3  0.49  1.44 1.44   18.3  0.49   0   0.5500   15   FILLED
12:02:16 spring_complete_1  0.45  2.04 2.04    3.6  0.45   0   0.5100    3   FILLED
12:04:41 spring_complete_1  0.54  2.32    0    1.2  0.56   2   0.5700    1   M7_UNKNOWN
12:04:41 spring_complete_4  0.54  2.32    0    1.8  0.56   2   0.5700    1   M7_UNKNOWN
12:06:53 spring_complete_1  0.52  2.17 2.17   27.7  0.52   0   0.5400   24   FILLED
12:06:53 spring_complete_2  0.52  2.32    0    0.4  0.52   0      -      0   R0_REJECTED_BEFORE_RESTING
12:06:53 spring_complete_3  0.52  2.32    0    0.9  0.52   0      -      0   R0_REJECTED_BEFORE_RESTING
12:06:53 spring_complete_4  0.52  2.32    0    0.4  0.52   0      -      0   R0_REJECTED_BEFORE_RESTING
```

`complement_reached_our_price` is **False on every single attempt** — in no
case did the observed complement ask trade down to the price we posted.

**Required complement qty** is the `qty` column (each COMPLETE requests
exactly the outstanding residual). **Held qty** at each attempt is the running
`up` inventory in §2.2. **Depth and quote age**: `executable_depth_status` was
`STALE` on 5 of the 6 sampled evaluations and `OBSERVED` once (653.33 at
11:59:02Z); `quote_age_s` ran 3.99-18.17 s against a `max_quote_age_s` of 5.0,
so most depth readings are older than their own freshness bound. **Fill
latency per attempt is NOT recorded** — only `rest_s` to the terminal event —
so latency is reported as rest-to-fill, not as time-to-first-execution.

## 2.2 HOW 2.43 MATCHED AT 0.96959 HAPPENED, THEN $3.8963 UNMATCHED

| session end | up | dn | matched | residual | avg_up | avg_dn | pair | unmatched $ |
|---|---|---|---|---|---|---|---|---|
| 11:53:42 | 2.43 | 2.43 | 2.43 | 0.00 | 0.4100 | 0.55959 | **0.96959** | 0.0000 |
| 11:56:44 | 1.44 | 0 | 0 | 1.44 | 0.4000 | — | — | 0.5760 |
| 11:59:02 | 6.09 | 3.87 | 3.87 | 2.22 | 0.42222 | 0.53370 | 0.95591 | 0.9373 |
| 12:02:16 | 12.21 | 5.91 | 5.91 | 6.30 | 0.45619 | 0.50481 | 0.96100 | 2.8740 |
| 12:04:41 | 14.53 | 5.91 | 5.91 | 8.62 | 0.45201 | 0.50481 | 0.95682 | **3.8963** |
| 12:06:53 | 14.53 | 8.08 | 8.08 | 6.45 | 0.45201 | 0.50889 | 0.96090 | 2.9155 |

**The 0.96959 pair.** One OPEN joined the touch and filled 2.43 @ 0.41 in
6.8 s. The complement then took three attempts: the first was cancelled when
our own touch rose 0.53 -> 0.56 (M6), the second filled 2.38 at complement
0.56 after 28.7 s, and a third mopped the 0.05 remainder at 0.54.
`(2.38x0.56 + 0.05x0.54)/2.43 = 0.55959`, and `2.43x0.41 + 1.3598 = 2.3561`
— the reported `spent`. Pair `0.41 + 0.55959 = 0.96959`. The upward move
during completion is what made it cheap; the session ended exactly matched.

**The $3.8963 unmatched.** The imbalance is a *rate* asymmetry, not a pricing
failure — the pair cost never deteriorated, staying in 0.9559-0.9696
throughout. Over the five sessions after the first:

- **UP side (OPEN/EXTEND): 7 of 11 filled.** Every one joined the touch
  (`touch_join: true` on all 11).
- **DOWN side (COMPLETE): 5 of 14 filled**, and only 2 of those 5 in the
  three sessions where residual grew.
- The decisive session is 12:02:16: **one** COMPLETE filled (2.04) against
  **three** UP fills (2.04 x 3). Residual 2.22 -> 6.30 in one session.
- 12:04:41 then filled one more EXTEND (2.32) and zero COMPLETEs — both
  COMPLETE attempts were killed at 1.2 s and 1.8 s. Residual 6.30 -> 8.62,
  unmatched $3.8963.
- 12:06:53 finally filled 2.17 of complement, pulling residual back to 6.45,
  and then the venue rejected the next three.

`avg_up` drifted 0.4100 -> 0.4562 and `avg_dn` 0.55959 -> 0.50481: the market
moved up under us, so each new UP leg cost more and each complement cost less.
Those two moves offset, which is why the pair held near 0.96 while the
*quantity* imbalance grew.

**NO CAP WAS BREACHED.** `residual_cap_units: 6` is enforced as
`abs(behavioral_residual()) >= 6`, and `behavioral_residual() = residual/unit`
— **action units, not contracts**. With `unit` = 2.43 (first fill), the 8.62-
contract peak is ~3.5 action units against a cap of 6. The cap also blocks
only further same-side EXTEND; COMPLETE stays permitted and nothing is
flattened, which is the designed L5-consistent behaviour. Session notional
cap 30.00 against `prior_cumulative_notional` 6.5677 — also not binding.

One accounting artifact, not a contradiction: at 11:56:44 `venue_vs_ledger`
read `venue 6.30 vs ledger 1.44, agrees false`. That session's ledger was
session-local (1.44) while the venue reported the market cumulative
(4.86 + 1.44 = 6.30). After the position was adopted into held state at
11:56:45 every later session reconciled cumulatively and agreed exactly:
9.96, 18.12, 20.44, 22.61 — `delta 0.0000, agrees true` each time.

## 2.3 RANKED COMPLETE FAILURE DECOMPOSITION

Nine failed COMPLETE attempts of fourteen:

| rank | mode | n | share | evidence |
|---|---|---|---|---|
| 1= | **R0_REJECTED_BEFORE_RESTING** | 3 | 33.3% | venue rejected at 5 ticks from crossing, market open, `infra_rejections 0`; three identical resubmits inside 1.7 s |
| 1= | **M1_COMPLEMENT_NEVER_REACHED_OUR_PRICE** | 3 | 33.3% | rested 31.2-32.0 s over 27-28 polls; `minOpp` 0.56/0.60/0.52 against our 0.52/0.52/0.49 |
| 3 | **M7_UNKNOWN_INSUFFICIENT_EVIDENCE** | 2 | 22.2% | cancelled at 1.2 s and 1.8 s, **1 poll each**, the only two attempts priced *below* touch (2 ticks) |
| 4 | **M6_MARKET_MOVED_PAST_US** | 1 | 11.1% | our touch rose 0.53 -> 0.56 during a 15.9 s rest, 14 polls |

Not observed in this sequence: M2 (reached-but-no-fill), M4 (size exceeded
flow), M5 (repeat unchanged book) — though see the caveat on R0 below, which
*is* a repeat-unchanged-price pattern that the taxonomy routes to R0 because
the rejection precedes resting.

**The two M7s are M3-shaped and the taxonomy is right to withhold M3.** With a
single poll there is no evidence the complement would ever have reached our
price, so "we cancelled before it could fill" cannot be asserted — only that
we cancelled before we could know. That is exactly the distinction M3-above-M1
was frozen to preserve.

## 2.4 AGAINST THE LARGER CORPUS AND L5 CADENCE

| measure | this sequence | settled corpus | whole cohort |
|---|---|---|---|
| COMPLETE fill rate | **5/14 = 35.7%** | 8/269 = 3.0% | 17/477 = 3.6% |
| attempts priced AT touch | 12/14 = 85.7% | — | — |
| at-touch fill rate | 5/12 = 41.7% (5/9 = 55.6% excluding venue rejections) | 19.7% | — |
| attempts priced BELOW touch | 2/14 = 14.3% | 47.4% of sessions | — |
| below-touch fill rate | **0/2**, both dead in <2 s at 1 poll | 0.74%, 134/135 single-poll | — |

**This sequence is an order of magnitude better than the corpus, and the
reason is visible in one column.** The pair ran at ~0.96 against a 1.00
ceiling, so the ceiling almost never forced our price below the touch; we
could post *at* the touch 12 times out of 14. The two occasions the ceiling
did bite reproduce the corpus pathology exactly — priced below touch, one
poll, dead in under two seconds, zero fills. The earlier finding that
below-touch pricing produces deterministic single-poll churn is reproduced
here at n=2, in a sequence where everything else went right.

**L5 cadence comparison, with its limit stated.** L5's public fills give
consecutive order-episode start-to-start gaps of median 3 s (extend, n=52542)
and 4 s (complete, n=52015), p90 25-26 s. Our 25 orders spanned 11:51:53 ->
12:06:53 = ~900 s, roughly 36 s per action — about an order of magnitude
slower than L5's episode cadence. **No claim is made about L5's resting
durations, queue positions, cancel behaviour or maker/taker status: public
fills cannot establish any of them and they remain UNKNOWN.** Our `rest_s`
(median 13.2 s for COMPLETE) and L5's episode gaps are different quantities
and are not compared as if they were the same.

## 2.5 ONE HIGHEST-EVIDENCE MODIFICATION TO SHADOW-TEST — NOT IMPLEMENTED

> **On a `rejected_at_non_crossing_price` COMPLETE rejection, do not resubmit
> the same price. Require one fresh authenticated book read, and a changed
> price or changed book, before the next COMPLETE attempt on that market.**

Why this one:

- It is the **largest failure bucket** (3 of 9, tied 33.3%) and the only one
  the engine itself already flags as a defect candidate rather than market
  behaviour.
- The pattern is a **retry storm**: three identical 0.52 submissions in 1.7 s,
  reject streak 1 -> 2 -> 3, rate 1/2 -> 2/3 -> 3/4, and
  `authenticated_book_crossed` only detected on the **third**. A single
  fresh read after the first rejection is what the current path skips.
- It is **not** the parked change. CEILING_PINNED_PERSIST (suppressing the
  `touch_moved` cancel) stays PARKED and is not proposed; that would target
  the M7 bucket, which is smaller (2 of 9) and whose counterfactual is
  explicitly unknowable at one poll.
- It touches **only** post-rejection retry behaviour — no price, size, band,
  cap, ceiling, admission, concurrency, settlement horizon, post-only status
  or maker-only status changes, and every risk/safety cancel still fires
  immediately.

Evidence it does NOT yet have: n=3, one market, one 1.7 s window, and the
rejections may be venue-side transients tied to an incompletely recovered
venue rather than anything we control. **This is a shadow-test candidate, not
a fix, and no improvement in fill conversion should be attributed to it until
filled/settled evidence demonstrates one.**

---

# PART 3 — COHORT AND SETTLEMENT, SEPARATE AND UNCHANGED

Ledger D: ten venue-confirmed settlements, **net -8.1284**, 16.95 of 92.28
matched (18.4%), 269 COMPLETE attempts / 8 fills (3.0%). No settlement has
occurred since. `aec-itfwo-honwon-yanhou-2026-09-14` remains
`SETTLED_REALIZED_UNCONFIRMED`.

Serial <=0.97 cohort: **SUPPORTED stays at 3** (andfen-ryaseg 1,
honwon-yanhou 2). `nicpar-gabpen` enters as `books 5, ord 5, priced 0,
<=.97 0, SUPP 0`. Its live maker pair cost of 0.96959 is **not** a fourth
SUPPORTED row — the prospective-cross arm and the realised maker pair are
different measurements and are not conflated.

**The nicpar-gabpen matched economics remain UNREALIZED.** Market
`MARKET_STATE_OPEN`, `public_resolved: false`, `PENDING_SETTLEMENT`,
`realized_confirmed: false`, `realized_usd: null`. Five of fifteen cohort
markets await venue-confirmed settlement: andfen-ryaseg, nicpar-gabpen,
honwon-yanhou, cribuc-biaand, kanpet-zijcui.
