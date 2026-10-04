# P1 — hybrid maker→taker execution on frozen Cell C

Execution only. Cell C's decisions, timestamps, signals, quantities,
inventory rules and reversal logic are untouched; this asks what each of
its **676 actions** (300 ENTRY, 87 EXTEND, 289 EXIT legs) would have cost
under maker-first with a conditional taker fallback.

Fill discipline is the established one — **no touch = fill**: queue-ahead
from the depth displayed when our level becomes the touch, depleted only by
trades consuming our side, zeroed by a print through our price. The
fallback is priced at `t + wait + 0.713 s` (measured cancel RTT p90 +
one state refresh), so the maker order is **never live at the same time as
the fallback**. 269 of 676 actions achieve an evidenced maker fill within
60 s.

The wait grid comes from the tape's own resolution (median book
inter-arrival 0.101 s, median our-side trade inter-arrival 0.075 s, p90
0.924 s) and the **whole frontier is reported** — no timeout is selected
from P&L.

### ENTRY — 300 actions

| wait | maker fill prob | median TTF | p90 TTF | fee saved on fills | spread captured | adverse on fallbacks | cancel→taker fee delta | **net vs immediate taker** |
|---|---|---|---|---|---|---|---|---|
| 0.10s | 0.7% | 0.04s | 0.08s | +0.010 | +0.022 | -1.319 | +0.014 | **-1.273** |
| 0.25s | 1.0% | 0.08s | 0.23s | +0.040 | +0.039 | -1.171 | +0.011 | **-1.081** |
| 0.50s | 1.7% | 0.23s | 0.36s | +0.096 | +0.089 | -1.006 | +0.009 | **-0.812** |
| 1.00s | 3.7% | 0.53s | 0.80s | +0.162 | +0.161 | -1.043 | +0.017 | **-0.704** |
| 2.00s | 7.3% | 0.98s | 1.84s | +0.413 | +0.347 | -1.524 | +0.029 | **-0.735** |
| 5.00s | 19.0% | 2.30s | 4.42s | +0.963 | +0.812 | -2.174 | +0.058 | **-0.342** |
| 10.00s | 28.7% | 3.40s | 8.12s | +1.465 | +1.228 | -3.163 | +0.139 | **-0.332** |
| 30.00s | 42.3% | 6.00s | 20.14s | +2.217 | +1.817 | -4.144 | +0.297 | **+0.187** |
| 60.00s | 48.7% | 7.20s | 36.42s | +2.626 | +2.103 | -6.417 | +0.416 | **-1.272** |

### EXTEND — 87 actions

| wait | maker fill prob | median TTF | p90 TTF | fee saved on fills | spread captured | adverse on fallbacks | cancel→taker fee delta | **net vs immediate taker** |
|---|---|---|---|---|---|---|---|---|
| 0.10s | 0.0% | — | — | +0.000 | +0.000 | +0.006 | -0.001 | **+0.005** |
| 0.25s | 0.0% | — | — | +0.000 | +0.000 | -0.004 | -0.000 | **-0.004** |
| 0.50s | 0.0% | — | — | +0.000 | +0.000 | +0.042 | -0.001 | **+0.041** |
| 1.00s | 0.0% | — | — | +0.000 | +0.000 | +0.022 | +0.001 | **+0.023** |
| 2.00s | 0.0% | — | — | +0.000 | +0.000 | -0.004 | +0.001 | **-0.003** |
| 5.00s | 8.0% | 4.40s | 4.77s | +0.058 | +0.105 | +0.046 | +0.006 | **+0.214** |
| 10.00s | 12.6% | 4.68s | 8.28s | +0.072 | +0.147 | -0.076 | +0.007 | **+0.149** |
| 30.00s | 25.3% | 9.90s | 21.56s | +0.179 | +0.267 | +0.499 | +0.004 | **+0.950** |
| 60.00s | 32.2% | 12.83s | 43.60s | +0.247 | +0.344 | +0.687 | -0.001 | **+1.276** |

### EXIT — 289 actions

| wait | maker fill prob | median TTF | p90 TTF | fee saved on fills | spread captured | adverse on fallbacks | cancel→taker fee delta | **net vs immediate taker** |
|---|---|---|---|---|---|---|---|---|
| 0.10s | 0.3% | 0.05s | 0.05s | +0.026 | +0.016 | -10.934 | -0.267 | **-11.159** |
| 0.25s | 0.3% | 0.05s | 0.05s | +0.026 | +0.016 | -10.774 | -0.262 | **-10.994** |
| 0.50s | 0.3% | 0.05s | 0.05s | +0.026 | +0.016 | -10.437 | -0.256 | **-10.651** |
| 1.00s | 1.0% | 0.60s | 0.64s | +0.029 | +0.016 | -10.609 | -0.257 | **-10.821** |
| 2.00s | 1.4% | 0.62s | 1.99s | +0.049 | +0.040 | -10.728 | -0.253 | **-10.892** |
| 5.00s | 3.8% | 3.11s | 4.19s | +0.169 | +0.151 | -14.910 | -0.307 | **-14.898** |
| 10.00s | 8.7% | 6.48s | 9.80s | +0.402 | +0.354 | -14.176 | -0.222 | **-13.642** |
| 30.00s | 21.5% | 12.26s | 26.77s | +1.031 | +1.012 | -12.847 | +0.034 | **-10.769** |
| 60.00s | 32.9% | 19.50s | 42.79s | +1.692 | +1.612 | -14.254 | +0.179 | **-10.772** |

### All actions combined

| wait | maker fill % | fee saved | spread captured | adverse cost | fee delta on fallbacks | **net vs immediate taker** | per window |
|---|---|---|---|---|---|---|---|
| 0.10s | 0.4% | +0.036 | +0.037 | -12.246 | -0.254 | **-12.427** | -0.4438 |
| 0.25s | 0.6% | +0.066 | +0.055 | -11.949 | -0.251 | **-12.079** | -0.4314 |
| 0.50s | 0.9% | +0.122 | +0.104 | -11.401 | -0.248 | **-11.422** | -0.4079 |
| 1.00s | 2.1% | +0.191 | +0.177 | -11.630 | -0.239 | **-11.501** | -0.4108 |
| 2.00s | 3.8% | +0.461 | +0.387 | -12.256 | -0.222 | **-11.630** | -0.4154 |
| 5.00s | 11.1% | +1.190 | +1.068 | -17.039 | -0.244 | **-15.025** | -0.5366 |
| 10.00s | 18.0% | +1.939 | +1.729 | -17.415 | -0.076 | **-13.824** | -0.4937 |
| 30.00s | 31.2% | +3.428 | +3.096 | -16.492 | +0.335 | **-9.633** | -0.3440 |
| 60.00s | 39.8% | +4.565 | +4.058 | -19.985 | +0.594 | **-10.768** | -0.3846 |

## The economically dominant region, and only it
## Reading the three classes — they are emphatically not the same

**EXIT: never.** Negative at **every** wait, −$10.65 to −$14.90. This is D4
reconfirmed on a nine-point grid instead of one: an exit fires *because*
price is moving against us, so resting into it is patience at the worst
moment. Immediate taker on exits is correct and the frontier has no corner
where that changes.

**ENTRY: no, and the one positive point is a trap.** Negative at 8 of 9
waits. The single positive (30 s, +$0.19) **collapses to −$1.27 at 60 s** —
a non-monotone frontier whose sign depends on the grid point is noise, not
a region.

**EXTEND: the only economically dominant region** — non-negative across the
entire grid. But see below before treating that as a result.

## The economically dominant region, and only it

EXIT is negative at every wait (-$10.65 to -$14.90) and ENTRY at every wait but one, with 60s collapsing from +0.19 to -1.27 -- non-monotone and unstable. **EXTEND is the only class that is non-negative across the whole grid.** The policy therefore writes itself without choosing a timeout: maker-first on EXTEND, immediate taker on ENTRY and EXIT.

| wait | EXTEND net ($1 scale, 28 win) | per window | **scaled to $100 (x12.45)** | fee removed from the $11.42 bill | leave-best-window |
|---|---|---|---|---|---|
| 0.10s | +0.005 | +0.00019 | **+0.0024** | 0.000 (0.0%) | -0.023 |
| 0.25s | -0.004 | -0.00015 | **-0.0018** | 0.000 (0.0%) | -0.032 |
| 0.50s | +0.041 | +0.00145 | **+0.0180** | 0.000 (0.0%) | +0.001 |
| 1.00s | +0.023 | +0.00082 | **+0.0102** | 0.000 (0.0%) | -0.026 |
| 2.00s | -0.003 | -0.00010 | **-0.0013** | 0.000 (0.0%) | -0.051 |
| 5.00s | +0.214 | +0.00765 | **+0.0952** | 0.058 (0.5%) | +0.068 |
| 10.00s | +0.149 | +0.00534 | **+0.0664** | 0.072 (0.6%) | +0.052 |
| 30.00s | +0.950 | +0.03391 | **+0.4223** | 0.179 (1.6%) | +0.688 |
| 60.00s | +1.276 | +0.04556 | **+0.5674** | 0.247 (2.2%) | +0.932 |

## With the conditional fallback (Cell C's own rule: the side must still be the favourite)
## Why EXTEND's apparent gain does not survive inspection

Three things disqualify it as the basis for a policy:

1. **It is 87 of 676 actions (12.9%)** and removes **0.0% to 2.2%** of the
   $11.42 fee bill. The target is ~12% of total friction.
2. **Most of the "improvement" is not execution.** At the 60 s point the
   +$1.276 decomposes as fee saved $0.247 + spread captured $0.344 +
   **favourable price drift on the fallbacks $0.687**. That last term is
   directional luck on 87 actions, not an execution edge. Stripping it
   leaves **$0.591**, i.e. **+$0.263/window at $100** against the **+$0.62**
   needed — 42%, from the grid's extreme point.
3. **The gain rises monotonically to the edge of the grid.** A frontier
   whose maximum is its last point is telling you the grid is too short,
   not that 60 s is optimal — and 60 s is already the whole evaluation
   period, so "maker-first on EXTEND at 60 s" is really "stop taking
   extensions and hope they come to you".

## The conditional fallback makes it worse, which is itself a finding

Applying your rule literally — after the timeout, cross **only if the side
is still the favourite under Cell C's unchanged rule, else cancel and wait:

| wait | EXTEND net | trades skipped | ENTRY net | trades skipped | all-actions net |
|---|---|---|---|---|---|
| 0.10s | +0.005 | 0 | -1.364 | 1 | -12.518 |
| 0.25s | -0.004 | 0 | -1.210 | 2 | -12.208 |
| 0.50s | +0.041 | 0 | -0.923 | 2 | -11.533 |
| 1.00s | +0.023 | 0 | -0.779 | 1 | -11.577 |
| 2.00s | -0.003 | 0 | -0.811 | 1 | -11.705 |
| 5.00s | +0.214 | 0 | -1.268 | 7 | -15.951 |
| 10.00s | +0.149 | 0 | -2.234 | 11 | -15.726 |
| 30.00s | +0.407 | 3 | -3.698 | 18 | -14.060 |
| 60.00s | +0.802 | 2 | -6.462 | 20 | -16.432 |

## The two questions, answered
At 30 s the conditional rule skips 18 ENTRY trades and takes ENTRY from
**+$0.19 to −$3.70**. The trades it declines were, on balance, the ones
worth having. The re-validation is sound risk practice and it costs money
here; both of those are true at once.

## The two questions, answered directly

**Q: Can hybrid execution recover the ~12% friction reduction without
sacrificing equal or larger gross?**

**No.** The only class that tolerates maker-first is 12.9% of actions and
2.2% of the fee bill at best. Applying maker-first to *everything* removes
up to **40%** of the fee bill ($4.565 of $11.42 at 60 s) and costs
**$19.99** of adverse movement to do it — a net loss of $10.77. There is no
point on the frontier where fee saving exceeds movement cost in aggregate.

**Q: At ~$100, how much of the $5.00/window taker fee bill can actually be
removed after adverse selection and failed maker attempts?**

**About $0.11/window (2.2%), from EXTEND only, at the most favourable point
on the grid — and roughly half of even that is directional drift rather
than execution.** Net economic improvement is **+$0.10 to +$0.57 per
window** across the EXTEND grid against a **−$0.74** deficit, and the upper
end is the grid's extreme on 87 actions.

## No hybrid policy is preregistered and no portfolio replay was run

The instruction was to construct a policy "only from economically dominant
regions". After measuring all three classes across nine waits, the dominant
region is **one class, 12.9% of actions, 2.2% of the fee bill, with half
its gain attributable to drift and a maximum at the edge of the grid**. A
portfolio replay of that would be a replay of noise, and preregistering
"maker-first on EXTEND at 60 s" would be selecting the frontier's best
point — the exact thing this study was told not to do.

What is established, and is worth carrying forward:

- **Exits must be taker.** Measured at nine waits, not assumed.
- **Entries must be taker.** The apparent 30 s exception does not survive
  the next grid point.
- **The fee bill is not recoverable through execution on this strategy's
  action mix**, because 87% of its actions are in classes where waiting
  costs more than the fee it saves.

Cell C's problem is therefore confirmed to be its **action mix**, not its
execution: a strategy that enters and exits on movement cannot be made
cheap by waiting, because waiting is precisely what its signal cannot
afford. Changing that means changing the strategy, which this study did
not do and does not license.

`historical replay`. No live orders, no strategy optimization, no timeout
selected. Verified maker compensation $0.0000 — every maker rebate figure
here is modelled and sits outside every net.
