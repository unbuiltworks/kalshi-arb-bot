# Probe 07, decomposed — and the one lever that changes the sign

Probe 07 proved the mechanism: nine maker orders, nine rests, nine fills, one
negative commission. It did not prove an expectancy; it lost $0.77180. This
file asks where that went and which single change would have kept it.

Sources, all authoritative and all GET-only:

* `probe07_orders.json` — the nine venue order rows, re-read from
  `/v1/order/{id}`, recovered from deployment `ce3238b7` logs.
* `probe07_actions.jsonl` — all 54 `L5_ACTION_ROW` lines from deployment
  `32970599`, the recover-only read of the Probe 07 journal.
* `data/l5/raw/sep/l5_sep23_25_daily.json` — L5's own 22,512 fills over
  Sep 23–25, harvested from the venue data API.

## 1. The unit of account, and why one ledger can hold both venues

PMUS wires a `BUY_SHORT` as a SELL of YES at a wire price `p`. That is the
same thing as buying NO at `1 − p`:

    p·q credited now  −  1·q owed at settlement   ==   −(1 − p)·q

so both are written here as **buy outcome X at X's own price**. The identity
is exact, not an approximation, and it is what lets a Probe 07 order and an
L5 Polymarket trade run through the same inventory walk.

**A correction to the previous accounting file.** `PROBE_07_FINAL_ACCOUNTING.md`
split execution as `long −2.0480 / short +4.0762`. From the venue rows the
legs are `long −1.7361 / short +3.7643`. Both splits net to the published
`+2.0282`, and the published total and balance delta were right; the per-leg
split was inflated by 0.3119 on each side and is corrected here.

## 2. Chronological decomposition — closes exactly

Fill-time order, FIFO pairing. A matched pair is one UP share and one DOWN
share; it redeems exactly $1 whatever happens, so its P&L is locked when the
second leg fills and is known without knowing the outcome.

| # | fill utc | side | px | qty | matched | pair cost | locked | residual |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | 19:47:04 | DOWN | 0.2500 | 0.29 | — | — | — | −0.29 |
| 2 | 19:47:22 | UP | 0.7100 | 1.71 | 0.29 | 0.9600 | +0.0116 | +1.42 |
| 3 | 19:47:22 | UP | 0.7100 | 0.54 | — | — | — | +1.96 |
| 4 | 19:49:55 | DOWN | 0.3300 | 0.22 | 0.22 | 1.0400 | −0.0088 | +1.74 |
| 5 | 19:50:09 | DOWN | 0.3300 | 0.22 | 0.22 | 1.0400 | −0.0088 | +1.52 |
| 6 | 19:50:10 | DOWN | 0.3100 | 0.05 | 0.05 | 1.0200 | −0.0010 | +1.47 |
| 7 | 19:50:15 | UP | 0.6300 | 0.22 | — | — | — | +1.69 |
| 8 | 19:50:45 | DOWN | 0.3400 | 2.25 | 1.69 | 1.05/1.05/0.97 | −0.0669 | −0.56 |
| 9 | 19:50:56 | DOWN | 0.2300 | 2.25 | — | — | — | −2.81 |

| | |
|---|---:|
| matched qty | **2.47** |
| matched acquisition cost | **2.5439** |
| matched redemption | **2.4700** |
| **matched trading P&L** | **−0.0739** |
| average pair cost | **1.029919** |
| maker rebates | **+0.0100** |
| residual qty | **2.81 DOWN** |
| residual cost | 0.7079 |
| **residual settlement P&L** | **−0.7079** |
| **TOTAL REALIZED** | **−0.771800** |

`−0.0739 + −0.7079 + 0.0100 = −0.771800`, equal to the balance delta
`63.02925 → 62.25745`. An independent check with no pairing convention at
all — `redemption 2.47 − spend 3.2518 + rebates 0.0100` — gives the same
number.

**The loss is not all residual, and the directive was right to demand the
arithmetic.** Residual is 91.7% of it, but the matched book *lost* 7.39
cents: it was acquired at **1.0299 a pair** and redeems at 1.00. Under the
average-cost convention instead of FIFO the split moves to matched +0.0249 /
residual −0.8066 and the total is unchanged, so the honest statement is
that **the matched book was approximately break-even (±8¢) and the residual
lost 71–81¢**. Matched-pair production at par earns nothing; it is the
residual that has all the variance.

The operating model's spring discipline prefers pair completion around
`<= 0.96`. Probe 07 paid 1.0299 and nothing in the stack noticed, because
no gate was ever wired to that number.

## 3. Treatments on Probe 07's own stream, settlement-blind, one at a time

Thresholds come from inventory geometry — the cap is `k ×` the largest clip
the stream has **already** shown, the age limit is a fraction of the 900s
window — never from a sweep over settled results. `k < 1` is degenerate: a
cap below one clip cannot open any position at all.

| policy | took | supp | matched | match P&L | resid | resid P&L | TOTAL |
|---|---:|---:|---:|---:|---:|---:|---:|
| NATIVE | 9 | 0 | 2.47 | −0.0739 | 2.81 | −0.7079 | **−0.7718** |
| RESIDUAL_CAP k=1 | 7 | 2 | 1.93 | −0.0469 | 1.10 | −0.3740 | −0.4109 |
| RESIDUAL_CAP k=1.5/2/3 | 9 | 0 | 2.47 | −0.0739 | 2.81 | −0.7079 | −0.7718 |
| RESIDUAL_AGE 60s / 120s | 7 | 2 | 2.25 | −0.0805 | 0.78 | −0.2652 | −0.3357 |
| RESIDUAL_AGE 300s | 9 | 0 | 2.47 | −0.0739 | 2.81 | −0.7079 | −0.7718 |
| COMPLEMENT_REPAIR | 6 | 3 | 1.71 | −0.0535 | 1.32 | −0.4488 | −0.4923 |

The whole fill sequence spans 232 seconds, which is why the 300s age limit
never binds. **Every arm that binds still loses.** One window of nine fills
can show a mechanism; it cannot show an expectancy, and nothing in this
table should be read as one.

## 4. Which windows are usable, and which are not

`observer_rows_all_windows.jsonl` holds 569 clean rows over 12 BTC15m
windows, 217 of them `MAKER_COPYABLE`. It contains **zero fills** —
BOOK_REACHED and TRADE_FLOW are not fills and were not treated as any. Every
treatment here is a function of realized inventory, so with no fills the
inventory never moves, no policy ever binds, and those windows **cannot
evaluate these treatments at all**. They remain what they were: the evidence
for concurrency and obstruction. Saying so is the finding; inventing a fill
model to fill the gap would not be.

The usable set is therefore Probe 07's nine real fills and L5's own 22,512.
The out-of-sample work is in `analysis/l5/TREATMENT_OOS.md`.

## 5. The two execution fixes, replayed — mechanics only

Ten actions were lost to two defects that had nothing to do with what to
trade. Seq 20 carried `l5_price 0.2600000021`, wired `0.7399999979`, the
venue echoed `0.73` and the exact-echo gate refused; every one of the nine
`ORDER_STATE_UNCERTAIN` refusals came **after** it.

| fix | submittable | blocked |
|---|---:|---|
| tick quantization alone | **10 / 10** | — |
| terminal-known-no-order alone | 9 / 10 | seq 20 still off-tick |
| both | **10 / 10** | — |

All ten are `our_mode MAKER` and all ten were already identity-admitted.
Worst-case exposure with every recovered order live is **1.8328** against
the $5 cap, so the risk envelope was never the constraint. Slot pressure has
two bounds and the truth is between them: if no order ever terminated, 19
slots would be wanted against `max_live_orders 8` and the last few would
**wait, not be lost**; over the real insert→lastTransact intervals peak
concurrency is 4 and the limit does not bind.

**Not claimed: that any recovered order would have filled.** A maker order
rests. Probe 07 has no evidence for the fill rate of orders it never placed,
and this mechanical result is kept out of the strategy results entirely.
