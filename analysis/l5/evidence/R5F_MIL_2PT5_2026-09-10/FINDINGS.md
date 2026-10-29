# R5F halt: MIL -2.5, 2026-09-10

Owner declared a SAFETY EVENT and halted the campaign. Probe disarmed at
00:47:10Z (deployment 5ca186c9, `"mode":"SHADOW"`). Position left to settle;
NOT dumped. Raw rows in `raw_2ee4d1c6_mlb_rows.json`, per-order table in
`reconstruction.json`.

Market: `asc-mlb-chc-mil-2026-09-09-pos-2pt5`. Commit `53bc2808`, rung R5F,
band `0.25-0.75` (confirmed live in `session_end.spring_gates`).

## 1. The COMPLETE re-quote DID fire. An earlier read of mine said it did not.

That earlier statement was drawn from the first 2 of 6 sessions and was WRONG.
Across all 6 sessions, 41 complement orders:

| end_reason | n |
|---|---|
| `deadline` | 37 |
| `repriced:touch_moved:0.835` | 1 |
| `repriced:touch_moved:0.880` | 1 |
| `repriced:touch_moved:0.920` | 1 |
| `filled_on_cancel` | 1 |

Rest times: min 3.24s, median 6.40s, max 12.08s. So the "5s rest starves the
re-quote" hypothesis I floated is ALSO wrong -- rests routinely exceed 5s and
the mechanism fired. Ladder outcome (4)/(5) was reached, not (2).

## 2. What the mechanism actually did

```
00:42:53  spring_complete_5  NO px 0.875 rest 3.24s -> repriced:touch_moved:0.880
00:42:53  spring_complete_7  NO px 0.880 rest 6.48s -> filled_on_cancel   FILLED 3.63
00:44:13  spring_complete_3  NO px 0.830 rest 5.30s -> repriced:touch_moved:0.835
00:44:13  spring_complete_6  NO px 0.835 rest 5.65s -> repriced:touch_moved:0.920
```

It followed the touch up and it filled. Increment: 3.63 contracts for $3.1944
= **0.8800**. Against a first leg averaging 0.3612, that is a completed pair at
**1.2412** -- 24 cents above par, on a claim that pays $1.00.

**Dynamic COMPLETE works mechanically and converts stranded residual into
locked losses.** That is the finding. It required real orders to see.

## 3. Inventory and cash

```
00:39:23  up 20.34  dn 12.43  matched 12.43  residual 7.91  spent $18.3975
00:42:53  up 20.34  dn 16.06  matched 16.06  residual 4.28  spent $21.5919
```

Residual fell 7.91 -> 4.28. Spend rose to **$21.59 = 2.88x the stated $7.50
per-market cap**. The cap bound the FIRST LEG only (20.34 x 0.3612 = $7.34 of
$7.50, correctly). The complement side is unbounded in price and therefore
unbounded in cash.

## 4. Settlement arithmetic (nothing settled; venue numbers pending)

Total paid **$21.5919**. Exactly one side pays $1.00 per contract.

| outcome | payout | P&L |
|---|---|---|
| UP (YES) wins | 20.34 x $1 = $20.34 | **-$1.25** |
| DN (NO) wins | 16.06 x $1 = $16.06 | **-$5.53** |

**The position now loses in every outcome.** Before the 0.880 completion it was
-$5.97 / +$1.94. Completing above par removed the winning branch. It did narrow
the range (variance down), which is policy-consistent with "do not assume
residual has predictive skill" -- but paying 1.2412 for a $1.00 claim is not
acceptable completion under any definition we hold.

## 5. Root cause, which is one defect and not two

R5F sets `pair_cost_max: None` and `complement_pricing: "l5_touch"`. The
session reports `pair_target: 0.98`, but complements went out at 0.83-0.92, so
that target does NOT bind the complement under `l5_touch`. With no par ceiling
on the complement:

- the price is unbounded -> pairs complete above $1.00 (defect 2);
- the cash is therefore unbounded -> $21.59 against a $7.50 "cap" (defect 1).

The owner's two named fixes share this cause. The re-quote is not the defect;
the re-quote is what made an already-unbounded complement chase faster.

## 6. What held

- First-leg cap: $7.34 of $7.50. Bound exactly as designed.
- Maker/post-only: every order maker, no taker.
- Concurrency 1, one outstanding order.
- Band 0.25-0.75 live and correct.
- Daily loss stop never reached (-$5.53 worst case vs -$15.00), and it could
  not have fired anyway: it reads SETTLED realized, and nothing has settled.
  A stop that cannot see unsettled exposure did not protect this position and
  was never going to.

## 7. Explicitly NOT proposed

Raising the cap to accommodate what happened. The owner's instruction stands:
the system respects the risk budget; the budget is not redefined after being
exceeded.
