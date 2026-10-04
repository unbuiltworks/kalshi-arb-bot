# 1. D0's gross edge is robust. 2. D2 maker-first: the fills are the *bad* actions, and the saving almost pays for it anyway

Historical replay, 28 untouched windows, $1 scale. D0's decision policy is
frozen and untouched. No threshold search, no trail change, no scaling, no
live orders.

## Part 1 — the fork test: D0 gross before fees, by window

| statistic | value |
|---|---|
| total | **+$3.8959** |
| mean per window | **+$0.1391** |
| **median per window** | **+$0.1132** |
| positive windows | **23 of 28 (82.1%)** |
| p25 / p50 / p75 | +$0.0427 / +$0.1132 / +$0.2121 |
| worst window | **−$0.1584** |
| best window | +$0.4963 |
| **top-1 contribution** | **12.7%** of total |
| **top-3 contribution** | **36.5%** of total |
| **leave-top-1** | **+$0.1259** per window |
| **leave-top-3** | **+$0.0990** per window |
| fees, mean / median | $0.1455 / $0.1361 |

**This is not lottery-shaped.** 82% of windows are gross-positive, the median
is positive, the worst window loses 16 cents, and removing the three best
windows still leaves +$0.099 per window. Compare B, whose single best window
was 130% of its entire total.

So the fork resolves in favour of the execution hypothesis: **D0's gross
trading behaviour is a real, broadly distributed edge of about 13.9 cents a
window, and a mean fee bill of 14.6 cents is what makes it negative.** The
mean fee exceeds the mean gross by 0.64 cents — the whole deficit.

## Part 2 — D2_MAKER_FIRST, and the matched counterfactual

| | **D0** (taker, control) | **D2** (maker-first, skip if unfilled) | **D0 on D2's captured actions** |
|---|---|---|---|
| intended D0 actions | 195 | 195 | 195 |
| **defensible fills** | 195 | **102** | 102 |
| missed actions | 0 | **93** | — |
| **maker capture rate** | — | **52.3%** | — |
| gross before fees | **+3.8959** | +1.1117 | **−0.1500** |
| fees | −4.0741 | **−1.1281** | −2.2892 |
| maker rebate, MODELLED (excluded from net) | 0.0000 | 0.2194 | 0.0000 |
| **net** | −0.1781 | **−0.0164** | −2.4392 |
| mean net/window | −0.0064 | **−0.0006** | −0.0871 |
| **median net/window** | −0.0156 | **+0.0177** | −0.0469 |
| positive windows | 13/28 | **16/28** | 3/28 |
| leave-best-1 /window | −0.0193 | −0.0215 | −0.0921 |
| leave-best-3 /window | −0.0448 | −0.0354 | −0.1009 |
| worst window | −0.4613 | **−0.8663** | −0.3567 |
| peak capital, worst window | 3.50 | **2.12** | 3.21 |
| max residual / max drawdown | 3.6 sh / 0.61 | 2.5 sh / 0.89 | 3.6 sh / 0.36 |
| exits / turnover | 141 / 3.87× | 86 / 3.01× | 88 / 2.89× |

The decomposition identity reconciles to **0.000000** in all 28 windows for
all three.

### Your adverse-selection prediction is confirmed, and it is large

**On exactly the 102 actions a passive order won, D0's taker execution would
have produced gross of −$0.1500.** The full 195 actions produce +$3.8959. So
the **93 missed actions carry +$4.0459 of gross — essentially the entire
edge — and the captured actions carry none of it.**

That is the "60 → our 59 fills → 55 → 48" selection you described, measured:
the passive order wins precisely when the favorite is about to stop being
favored, and misses when the move is real.

### And the execution saving nearly pays for it

On those same 102 actions:

| | |
|---|---|
| price improvement, 110 fills / 126.5 shares | **+$1.3275** (mean 1.04¢/share) |
| taker fee avoided | **+$1.1636** |
| **total execution saving** | **+$2.4911** |
| modelled maker rebate, excluded from net | $0.2194 |

That saving turns a −$2.4392 subset into −$0.0164. **Execution is worth
+$2.42 on the captured actions; selection costs −$4.05 of gross.** The two
nearly cancel, which is why D2 lands within 1.6 cents of break-even across
28 windows.

### Where D2 stands against the robustness bar

**D2 is the best result in this lane and it still does not pass.**

- first **positive median** (+$0.0177) and most positive windows (16/28) of
  anything tested;
- mean per window −$0.0006 — ten times closer to zero than D0;
- but **leave-best-1 (−$0.0215) and leave-best-3 (−$0.0354) are negative**,
  so by the standard you set it is not robust;
- and its **worst window is worse** than D0's (−$0.8663 vs −$0.4613): fewer,
  larger adverse fills concentrate risk even as capital falls to $2.12.

It is also materially *cheaper*: 2.12 peak capital against 3.50, 86 exits
against 141, turnover 3.01× against 3.87×.

## What this says about the next step

The two effects are now separately measured, and they point at exactly the
hybrid you described:

- maker execution is worth **~2.4¢ per captured share** in price plus fee;
- passive fills select the actions worth **−$0.15** of the +$3.90 gross.

**D3 — maker-first, bounded wait, then taker only if the D0 signal is still
valid — is the indicated experiment**, because it is the only form that can
pay the cheap price on the 102 while still paying the crossed price for the
+$4.05 of gross sitting in the 93. I have **not** run it: its wait must be
derived from measured execution latency (we have cancel RTT min 0.187 s /
p50 0.266 s / p90 0.612 s from 4,567 in-repo samples) and preregistered on
its own, not picked from a ladder of 100 ms / 500 ms / 1 s / 2 s by which
one backtests best.

Turnover reduction — resting some exits instead of crossing — remains the
second lever and stays uncombined, as you said.

Nothing here is scaled, and the $200 ceiling stays unused: there is still no
positive process to scale. Verified compensation remains **$0.0000**;
every maker rebate figure above is modelled and sits outside net.
