# P1 at n=12 — two leakage channels, and PROFIT_LOCK_5

The frozen n=25 arm is **byte-identical and unchanged** — verified by regenerating
its ledger and diffing (`per_window` and `rolling` both identical). Every
challenger is a default-OFF parameter.

**Headline: PROFIT_LOCK_5 takes q=50 from +0.2132 to +4.0227 modeled net/window
with NO increase in peak directional exposure (100 contracts, both arms) — a 19×
improvement that still falls $0.98 short of the $5 target.** COMPLEMENT_BIAS,
though independently supported by L5's corpus, does **not** improve PMUS economics
and is recorded as a falsified transfer.

## 1. The two channels are independent, and one is invisible to P0

2245z ended **perfectly flat at 150/150 with zero terminal residual and still lost
−$10.65**. Splitting the 12 windows by whether a $5 floor ever became executable
shows why that is not an anomaly:

| q=50, per window | EC gross | **PASSIVE** | matched | fees | **residual** | trading net | terminal resid |
|---|---|---|---|---|---|---|---|
| **4 never-locked** (1915z, 1930z, 2045z, 2245z) | +20.5909 | **−25.5181** | −4.9272 | 0.7373 | **0.0000** | **−5.6645** | **0 in all four** |
| 8 locked | +16.9933 | −4.1209 | +12.8724 | 2.1965 | −8.7562 | +1.9196 | 3 of 8 |

**Channel A (passive / oscillator) is fatal on its own.** In a third of windows it
destroys more than the whole EC edge — passive −25.52 against EC +20.59 — with
**residual P&L exactly zero**. Residual-frequency statistics cannot see these
windows at all, which is precisely why 2245z matters.

**Channel B (directional / residual)** is concentrated in the locked group
(−8.7562/window) and is entirely removable: PROFIT_LOCK_5 drives residual P&L to
**0.0000** at both clips.

### What 2245z actually did
Bought UP at **0.44, then 0.77, 0.76, 0.89, 0.98** as UP climbed, while buying DOWN
at 0.52, 0.39, 0.19. Five same-side adds into a rising held side. Our two-sided
quoting is unconditional, so in a trending market its resting bid on the rising
side is repeatedly taken at ever worse prices. No residual is created — the book
stays balanced — and the loss lands entirely in matched pair cost.

## 2. The missing state variable, verified independently

Rebuilt from `data/l5/raw/activity.jsonl` (66,949 BTC15m states), **not** read from
the aggregate module, so this is an independent reconstruction:

| held-side move since basis | n | P(EXTEND same side) | P(COMPLETE complement) |
|---|---|---|---|
| ≤ −10c | 3,938 | **0.588** | 0.412 |
| −10..−5c | 5,137 | **0.621** | 0.379 |
| −5..−2c | 7,460 | **0.621** | 0.379 |
| −2..0c | 11,878 | **0.589** | 0.411 |
| 0..+2c | 16,857 | 0.494 | 0.506 |
| +2..+5c | 10,012 | 0.387 | **0.613** |
| +5..+10c | 5,989 | 0.377 | **0.623** |
| ≥ +10c | 4,441 | 0.409 | **0.591** |

This reproduces the canonical `RESPONSE_CURVE` in sign and magnitude from raw data.
**The state variable our translation lacks is the signed move of the held side
since it was acquired**: after a rise L5 goes complement ~60%, after a fall he adds
to the same side ~60%. It is inventory-conditioned and symmetric — not a
directional forecast.

**But implementing it does not help.** `complement_bias_c = 0.02` suppresses
same-side adds while the held side is ≥2c above basis:

| q=50 per window | frozen | **COMPLEMENT_BIAS 2c** |
|---|---|---|
| SHADOW TRADING NET | −0.6084 | **−0.7871** |
| SHADOW MODELED NET | +0.2132 | **+0.0346** |

Supported by L5's data, and it still fails the second half of the test. Recorded as
a falsified transfer; not retried and not tuned.

## 3. PROFIT_LOCK_5

`settlement_independent_floor()`:

    floor = min(q_up, q_down) − cash_paid − fees + locked_completion_margin

Whichever way the market settles the payout is at least `min(q_up, q_down)`, so
this is attainable **by doing nothing and holding to settlement**. Resting orders
are charged at their own price with no credit to `min()`, which can only understate
the floor. **No settlement outcome, future price, future fill, or ex-post maximum
is consulted anywhere.** At LOCK: resting acquisitions are cancelled, no further
OPEN/ACQUIRE/EXTEND, and a floor-securing completion is executed **only when it is
what lifts the floor over the line** — priced on the contemporaneous ladder,
depth-limited. If nothing executable reaches the threshold, it is not a lock.

### A vs B — the distinction the exercise turns on

| | q=25 | q=50 |
|---|---|---|
| windows where **mark-to-market** peaked ≥$5 | 8 | 10 |
| windows where the **executable floor** peaked ≥$5 | 7 | 8 |
| **A-only: marked $5 that was never lockable** | **1 (1945z: MTM 5.6250 vs floor 4.8267)** | 0 |

Only **B** is counted anywhere in the results below. At q=50 the MTM–floor gap is
small (0.25–1.6), so a mark-based reading would have been nearly right there and
wrong at q=25 — which is exactly why the floor, not the mark, is the trigger.

### Per window, q=50

| window | lock? | first lock | TTE | inv at lock | via | guaranteed at lock | frozen net | LOCK5 net | delta |
|---|---|---|---|---|---|---|---|---|---|
| 1830z | YES | 18:36:35 | 504.8s | 100/100 | COMPLETE | +5.6326 | −24.3793 | +5.6326 | **+30.0120** |
| 1845z | YES | 18:54:38 | 321.0s | 100/100 | COMPLETE | +5.1496 | −3.5383 | +5.1496 | +8.6879 |
| 1900z | YES | 19:13:07 | 112.6s | 129.52/129.52 | COMPLETE | +5.8911 | +23.9640 | +5.8911 | −18.0729 |
| 1915z | no | — | — | — | — | — | −4.3004 | −4.3004 | 0 |
| 1930z | no | — | — | — | — | — | −0.0688 | −0.0688 | 0 |
| 1945z | YES | 19:51:41 | 498.4s | 50/50 | HOLD | +9.6535 | −4.1930 | +9.6535 | +13.8465 |
| 2000z | YES | 20:08:38 | 381.5s | 0/0 | HOLD | +8.9049 | +10.7714 | +8.9049 | −1.8666 |
| 2030z | YES | 20:40:21 | 278.8s | 0/0 | HOLD | +8.1848 | −30.8152 | +8.1848 | **+39.0000** |
| 2045z | no | — | — | — | — | — | −7.6362 | −7.6362 | 0 |
| 2115z | YES | 21:19:55 | 604.1s | 50/50 | HOLD | +6.9699 | +26.3065 | +6.9699 | −19.3366 |
| 2215z | YES | 22:21:17 | 522.4s | 0/0 | HOLD | +10.6838 | +17.2408 | +10.6838 | −6.5569 |
| 2245z | no | — | — | — | — | — | −10.6526 | −10.6526 | 0 |

**In every locked window the final net equals the floor to the cent** — the lock is
genuinely settlement-independent rather than a hopeful hold. **2245z never locks at
either clip** (peak floor 2.00 at q=50): PROFIT_LOCK cannot address the
terminal-flat losers, because their floor never approaches $5. Those four windows
are pure channel A.

### Aggregate waterfall

| per window | q=25 frozen | q=25 LOCK5 | q=50 frozen | **q=50 LOCK5** |
|---|---|---|---|---|
| EC matched gross | +9.3373 | +6.6320 | +18.1925 | +12.0322 |
| PASSIVE matched gross | −5.6904 | −4.8222 | −11.2533 | −7.5988 |
| **TOTAL matched gross** | +3.6469 | +1.8098 | +6.9392 | +4.4334 |
| fees | 0.8771 | 0.7011 | 1.7101 | 1.2324 |
| residual P&L | −2.9188 | **0.0000** | −5.8375 | **0.0000** |
| **SHADOW TRADING NET** | −0.1490 | **+1.1087** | −0.6084 | **+3.2010** |
| expected maker rebate | +0.4133 | +0.4133 | +0.8217 | +0.8217 |
| **SHADOW MODELED NET** | +0.2644 | **+1.5220** | +0.2132 | **+4.0227** |
| max peak directional (contracts) | 50 | **50** | 100 | **100** |
| lock hit rate | — | 7 of 12 | — | 8 of 12 |
| $/locked window | — | +5.6885 | — | +7.6338 |
| worst window | −15.3540 | −15.3540 | −30.8152 | −10.6526 |
| mean matched throughput | — | see JSON | — | see JSON |

**The exposure condition is met exactly: max peak directional is unchanged at both
clips.** The lock buys its improvement by stopping, not by taking more risk.

### Diagnostic thresholds — NOT selected from

| threshold | q=50 hits | q=50 net/window | q=25 hits | q=25 net/window |
|---|---|---|---|---|
| $2 | 10 of 12 | +4.3768 | 8 of 12 | +1.6691 |
| $3 | 8 of 12 | +3.0874 | 8 of 12 | +2.3833 |
| $4 | 8 of 12 | +3.2154 | 8 of 12 | +2.6524 |
| **$5 (preregistered)** | **8 of 12** | **+4.0227** | **7 of 12** | **+1.5220** |
| $6 | 8 of 12 | +4.9065 | 6 of 12 | +1.7656 |
| $7.50 | 8 of 12 | +5.2935 | 4 of 12 | +0.6227 |
| $10 | 7 of 12 | +3.9340 | 2 of 12 | +0.2674 |

$7.50 clears $5 at q=50 and **$5 remains the preregistered challenger**; nothing is
selected from these twelve windows, and the non-monotonicity across thresholds at
n=12 is itself a warning against reading any of them as a choice.

## 4. Verdict

* **≥$5/window: NOT reached.** The preregistered PROFIT_LOCK_5 delivers
  **+4.0227** modeled net at q=50 — $0.98 short — and **+1.5220** at q=25.
* **Channel B is solvable**: residual P&L goes to exactly 0.0000.
* **Channel A is the residue and is not solved.** It remains −7.5988/window under
  the lock and is the entire loss in the 4 never-locked windows.
* q=50 remains **9.42× modeled extrapolation** on the 5.31-contract largest live
  fill, and the $0.8217 rebate is **EXPECTED, never credited**. On trading alone
  the lock gives **+3.2010**.
* **P0 is unaffected and continues unchanged to n=25**, at which point these arms
  re-run automatically on the larger sample.
