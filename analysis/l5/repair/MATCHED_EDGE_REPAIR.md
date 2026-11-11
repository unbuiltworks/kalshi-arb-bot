# L5 matched-edge / residual-repair replay

`analysis/l5/repair/replay.py` · corrected `usdcSize` economics · corrected
redemption population (a missing REDEEM row on a long-settled window is scored
as the loss it is) · all superseded frontier results excluded.

## The construction, and why it cannot cheat

The clone walks L5's own fill sequence in order and **may only ever refuse a
fill**. It never takes a fill L5 did not take, never reorders, never assumes a
complement arrives, and never prices a leg at anything but the price L5 actually
paid at that instant. Refusing is free; completing is not — so a policy that
"waits for a cheap pair" only gets one if L5's tape later supplies a complement
fill at that price, and otherwise it is left holding the residual it declined to
repair. Eight tests pin that asymmetry, including the one that matters most:
a pair ceiling that refuses the only complement **strands** the inventory.

Every policy is a rule about inventory that already exists. Nothing here
forecasts direction or selects windows.

## Primary question: **yes, and the margin is large**

BTC15m, TEST (out of sample, 1,641 windows, observed fees):

| policy | matched | residual | net | capital | bp/$ | max DD | worst window |
|---|---|---|---|---|---|---|---|
| **1 control (exact L5)** | +$89,028 | **−$90,452** | **−$1,424** | $3,232,181 | **−4** | **−$8,497** | −$290 |
| 2 residual cap 20 | +$5,284 | −$1,551 | +$3,733 | $491,907 | **+76** | −$113 | −$29 |
| **2 residual cap 50** | +$11,826 | −$3,308 | **+$8,518** | $1,528,344 | **+56** | **−$154** | −$78 |
| 2 residual cap 100 | +$18,290 | −$9,796 | +$8,494 | $2,357,801 | +36 | −$399 | −$150 |
| 3 complement-only ≤1.00 | +$2,858 | −$2,603 | +$255 | $45,509 | +56 | −$152 | −$42 |
| 4 complement-only ≤0.98 | +$3,644 | −$3,207 | +$437 | $43,457 | +100 | −$191 | −$43 |
| 5 complement-only ≤0.96 | +$4,289 | −$3,917 | +$372 | $41,871 | +89 | −$185 | −$47 |
| 6 relax ceiling with TTE | +$128,129 | −$139,273 | **−$11,143** | $2,375,110 | −47 | **−$17,762** | −$2,864 |
| 7 no new inventory TTE<300 | +$16,109 | −$11,920 | +$4,189 | $2,239,369 | +19 | −$1,850 | −$226 |
| 8 cap50 × ≤1.00 | +$17,906 | −$13,057 | +$4,849 | $999,562 | +49 | −$389 | −$54 |
| 8 cap50 × relax × t600 | +$10,795 | −$9,364 | +$1,431 | $223,647 | +64 | −$366 | −$47 |

TRAIN → TEST for the cap family is **+96 → +76**, **+84 → +56**, **+78 → +36**
bp/$ — same ordering, same sign, degradation but no inversion. The control is
**+62 → −4**. The caps are the only family that survives the split.

**A plain residual cap turns L5's own sequence from −4 bp to +56 bp out of
sample and cuts max drawdown from −$8,497 to −$154.** It does so without any
forecast, without selecting windows, and while refusing only leading-side fills.

Two families fail, and both fail for reasons worth keeping:

* **complement-only** policies deploy almost no capital ($42–46k of $3.2M) and
  carry a p05 of −10,000 bp — total-loss windows — because refusing an expensive
  complement strands the leg it was supposed to repair.
* **relaxing the ceiling as expiry nears (policy 6) is the worst thing tested**,
  −47 bp and −$17,762 drawdown, because without a cap it lets inventory balloon
  and then repairs it at any price.

## The mechanism: leakage falls faster than edge

| cap | % of matched kept | % of leakage kept | leverage | net | bp/$ |
|---|---|---|---|---|---|
| 5 | 1.8% | 1.0% | 1.87× | +$729 | +62 |
| 10 | 3.4% | 1.4% | 2.44× | +$1,784 | +75 |
| 20 | 5.9% | 1.7% | 3.46× | +$3,733 | **+76** |
| **35** | 8.3% | 2.1% | **3.98×** | +$5,532 | +56 |
| 50 | 13.3% | 3.7% | 3.63× | +$8,518 | +56 |
| 75 | 17.3% | 6.9% | 2.52× | **+$9,205** | +45 |
| 100 | 20.5% | 10.8% | 1.90× | +$8,494 | +36 |
| 200 | 33.1% | 27.1% | 1.22× | +$4,960 | +17 |
| 500 | 76.9% | 75.4% | 1.02× | +$268 | +1 |
| none | 100% | 100% | 1.00× | −$1,424 | −4 |

The leverage ratio peaks at **cap 35 (3.98×)**, absolute net at cap 75, bp/$ at
cap 20. There is a real frontier here rather than a single point, and it decays
monotonically to 1.00× — at which point you are L5 and you lose money.

## Secondary question: how much temporary residual is actually required?

State-level, BTC15m TEST. Every point at which inventory reaches a residual
bucket, scored by what followed **within the same window**:

| temporary residual | states | P(repair to ≤ half) | final effective pair (p50) | stranded (p50) | net mean |
|---|---|---|---|---|---|
| 0–5 | 1,407 | 46.4% | 0.9722 | 234.1 | −$0.53 |
| 5–10 | 1,460 | 77.2% | 0.9720 | 232.8 | +$1.02 |
| 10–20 | 1,539 | 88.6% | 0.9723 | 235.6 | +$0.07 |
| **20–50** | 1,619 | **93.8%** | 0.9723 | 241.1 | −$1.16 |
| 50–100 | 1,640 | 95.1% | 0.9720 | 243.9 | −$0.79 |
| 100–200 | 1,641 | 95.1% | 0.9720 | 243.9 | −$0.87 |
| 200–500 | 1,608 | 90.0% | 0.9715 | 248.8 | −$0.82 |
| **500+** | 526 | **37.5%** | 0.9612 | 565.1 | **−$47.90** |

**Repair probability saturates at ~94–95% by a residual of 20–50 and buys
nothing after that.** The attained pair is flat at ≈0.972 across every bucket —
carrying more inventory does **not** buy a cheaper completion. Above 500 the
repair rate collapses to 37.5% and the mean state is −$47.90.

So the honest answer to "how much must be tolerated": **roughly 20–50 shares.**
That is where the repair probability is already saturated, and every share
beyond it purchases tail risk at no improvement in the pair.

## The fee counterfactual — the mechanism is portable, conditional on economics

Net $ / bp per $, TEST (OOS). **B is an economic transfer test only and asserts
nothing about executability on PMUS.**

| cohort · policy | A observed | B PMUS maker | C fee-free | D all fee-bearing |
|---|---|---|---|---|
| BTC15m control | −1,424 / **−4** | 17,654 / **+55** | 3,791 / +12 | −73,841 / **−223** |
| BTC15m cap 50 | 8,518 / +56 | 17,291 / **+114** | 10,846 / +71 | −25,244 / −162 |
| BTC15m cap 100 | 8,494 / +36 | 22,124 / +94 | 12,179 / +52 | −43,510 / −181 |
| BTC5m control | −2,314 / −63 | 6,840 / **+191** | 5,453 / +151 | −2,314 / −63 |
| BTC5m cap50 × ≤1.00 | −707 / −60 | 2,755 / **+231** | 2,273 / +190 | −707 / −60 |
| ETH5m control | 44 / +15 | 810 / **+280** | 694 / +239 | 44 / +15 |
| ETH5m cap50 × ≤1.00 | 359 / +210 | 803 / **+475** | 735 / +433 | 359 / +210 |

(BTC5m and ETH5m are identical under A and D because every one of their fills
already bears the fee.)

**Under PMUS maker economics every cohort and every policy is strongly positive
out of sample** — +55 to +475 bp. Under the L5 fee on every fill, BTC15m is
−162 to −223 bp. The behavioural mechanism produces real matched economics; the
fee regime decides whether any of it survives.

Note also that on BTC5m under observed fees the residual cap does **not** help
(−63 → −82 bp): BTC5m's problem is the fee, not the residual. The cap is the
right tool only where the fee has already been survived.

## What is not claimed

* No policy here is proposed for deployment, and nothing was deployed.
* Scenario B does not assert these fills are executable on PMUS.
* L5's incentive credits are not attributable per window (the venue publishes no
  per-window reward row for another account) and are never netted in. Spread,
  depth, queue position and the intra-window book remain unavailable.
* The residual cap is measured in **shares of L5's own sequence**, not in our
  behavioural units, and is not a live risk parameter.
* The live probe stays stopped and the −$30.58 stop stays as it fired.
