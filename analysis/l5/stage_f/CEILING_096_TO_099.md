# Does raising the completion ceiling toward 0.99 improve net profitability?

**Date:** 2026-11-01
**Direct answer: NO.** At our actual size the change is worth a **maximum of
+$0.0008 across the whole seven-window campaign — $0.0001 per window** — it
touches 2 of 7 windows, every instance requires **crossing**, and at a pair
cost of 0.99 the matched margin (1¢/contract) is **smaller than our observed
taker fee (≈2¢/contract)**, so a 0.99 completion is loss-making by
construction.

**No L5 price appears anywhere in this document.** Every figure is a
venue-confirmed PMUS cost basis or a PMUS-quoted complement price from our own
executor's records.

---

## 1. The five-configuration comparison cannot be run (stated, not worked around)

The requested matrix — completed pairs, stranded legs, matched P&L, residual
P&L, settled net and **drawdown**, across 0.96/0.96, 0.96/0.97, 0.96/0.98,
0.96/0.99 and 0.99/0.99 on identical historical PMUS windows — requires a
**per-window two-sided PMUS book time series**. Here is every PMUS BTC15m book
recording in the repository:

| source | lines | two-sided book rows | **distinct BTC15m windows** | what it is |
|---|---|---|---|---|
| `data/pmus_btc15m_tape/pmus_btc15m_tape.jsonl` | 363 | 42 | **2** (2026-09-27 1815z, 1830z) | BBO only; `ask` is null on 88% of rows |
| `data/pmus_decision_tape/pmus_decision_states_20260927.jsonl` | 315 | 315 | **2** (2026-09-27 1615z, 1630z) | BBO only, and only at our own decision instants |
| `data/l5_strand_replay/residual_books_20261025.jsonl` | 290 | 276 | **5** (2026-10-25 0030z–0145z) | full 8-level ladders, but **sampled only while we already held a residual with an order resting** |
| Railway executor logs, campaign | 501 entries | 194 decisions | **7** (2026-11-01 1330z–1500z) | BBO per decision + `displayed_depth` at each completion price |
| `data/transfer_backtest/pmus_live_sample.jsonl` | 232 | 232 | **0** | `aec-mlb-*` sports slugs, wrong series |
| `data/policy_d_port_ext/*.jsonl` | 90,151 | many | **0** | Kalshi `KXSOL15M`/`KXXRP15M` and `BTC05M`, wrong venue and series |

**At most 16 distinct windows, across three disjoint days, with three
incompatible shapes.** Specifically missing:

1. **No continuous book series for any window.** The 09-27 tapes carry BBO with
   a null ask on most rows, so a complement price is unavailable at most
   instants — I cannot price a completion that the engine did not already
   price itself.
2. **The 10-25 ladders are conditioned on the outcome being studied.** They
   were recorded *because* a residual existed and an order was resting. Using
   them to compare ceilings would select windows on the dependent variable.
3. **No depth beyond the touch on 09-27 or 11-01.** `displayed_depth` exists
   only at the single price the engine evaluated.
4. **Drawdown is not computable.** Four windows carry a residual. A drawdown
   path over 4 observations is not a statistic, and I will not report one.

So I am not producing the five-row table. Inventing the missing book states is
the only way to fill it, and that is excluded.

## 2. The strongest defensible result: the ceiling never bound

What the campaign *does* support exactly. For each of the 157 completion
evaluations that carried a priced complement, the pair cost is our **venue-
confirmed cost basis** on the leg we held plus the **PMUS-quoted** complement:

| window | n | our held cost | min complement | pair min | pair p50 | pair max |
|---|---|---|---|---|---|---|
| **1415z** | 24 | 0.4400 | 0.2300 | **0.6700** | 0.7800 | **0.9400** |
| 1430z | 53 | 0.4700 | 0.3900 | 0.8600 | 0.9700 | 1.2400 |
| 1445z | 52 | 0.4800 | 0.2900 | 0.7700 | 1.1200 | 1.3100 |
| **1500z** | 28 | 0.5000 | 0.5200 | **1.0400** | 1.2623 | 1.3723 |

**Window 1415z is entirely below 0.96** — pair 0.67 to 0.94 across all 24
evaluations. The 0.96 ceiling **never bound there at all**; the refusal was
`OWNER_AUTHORIZATION_ABSENT` on 191 of 191 evaluations, i.e. maker-only.
**Window 1500z is entirely above 0.99** — no ceiling in the requested range
admits a single evaluation.

| completion ceiling | evaluations admitted | share | newly admitted |
|---|---|---|---|
| **0.96** | 56 | 35.7% | — |
| 0.97 | 61 | 38.9% | +5 |
| 0.98 | 66 | 42.0% | +5 |
| **0.99** | 67 | 42.7% | +1 |
| above 0.99 | — | **57.3% never admitted** | — |

Raising 0.96 → 0.99 newly admits **11 of 157 evaluations (7.0%)**, in **2 of 7
windows**.

## 3. What those 11 are worth

| window | time | held | comp | pair | qty | depth | margin/ctr | **net incl. fee** |
|---|---|---|---|---|---|---|---|---|
| 1430z | 14:30:56 | 0.4700 | 0.5000 | 0.9700 | 2.02 | 224.0 | +0.0300 | **+0.0206** |
| 1430z | 14:31:38 | 0.4700 | 0.5100 | 0.9800 | 2.02 | 151.1 | +0.0200 | +0.0004 |
| 1430z | ×6 more at 0.97–0.98 | | | | | | | +0.0004 … +0.0206 |
| 1445z | 14:46:08 | 0.4800 | 0.5100 | **0.9900** | 2.02 | 113.0 | +0.0100 | **−0.0198** |

The 11 are repeated 10–15s evaluations of the **same two residuals**, so at
most **one per window** could ever be acted on. Taking the best single
opportunity in each:

| window | best pair | net |
|---|---|---|
| 1430z | 0.9700 | **+$0.0206** |
| 1445z | 0.9900 | **−$0.0198** |
| **total** | | **+$0.0008** |
| **per window over the 7-window campaign** | | **+$0.0001** |

**One hundredth of a cent per window.** And every one is `mode: TAKER`, so
realizing it requires crossing — which `STRANDED_LEG_CODE_AUDIT.md` measured
at **EV −$0.03 per completion** (fee plus half-spread), a cost an order of
magnitude larger than the entire benefit.

## 4. Why 0.99 is the wrong direction, analytically

The 1445z row is the general case, not an unlucky draw. Our observed taker fee
is **$0.04 on 2.02 contracts ≈ 2.0¢/contract**. A crossed completion at pair
cost `P` nets `(1 − P) − 0.0198` per contract, so:

> **break-even pair cost for a crossed completion ≈ 0.980**

A ceiling of **0.99 admits trades that lose money by construction** — 1¢ of
matched margin against 2¢ of fee. A ceiling of 0.98 sits exactly at
break-even. **Only 0.96 and 0.97 are strictly inside the profitable region**,
and 0.96 — the deployed value — is the only one with real margin.

For a **maker** completion the question does not arise: the required
complement price is through the book (`LEG_WOULD_CROSS` on 168 of 194
decisions), so `post_only` rejects it and no ceiling is consulted.

## 5. On the 0.99/0.99 configuration

I cannot report it, and the reason is worth stating. The **opening** ceiling
governs the two-sided quote before either leg rests, where the pair identity
is `1 − spread − 2·depth·tick` ≈ 0.95 at a 1-tick spread. Moving the opening
ceiling to 0.99 changes which *quotes* are placed, which changes which legs
*fill*, which changes the entire subsequent inventory path. That is a
counterfactual fill sequence, and I have no PMUS book series to generate one
from — it is precisely the "invent fills" route that is excluded.

What can be said without inventing anything: `TRAJECTORY_TRANSFER_VERDICT.md`
established that raising the opening ceiling to admit a ~0.99 entry admits the
origin of L5's accumulation path, and the sample where that path was measured
ended **net negative**. That is an argument against 0.99/0.99, not evidence
for the configuration's P&L, and I am labelling it as such.

## 6. Direct answer

> **No. Increasing the completion ceiling toward 0.99 does not improve
> expected net profitability at our actual size.**

Three independent reasons, each sufficient:

1. **It barely binds.** 7.0% of evaluations newly admitted, in 2 of 7 windows.
   In the window where completion was most attractive (1415z) the pair was
   already 0.67–0.94 and the ceiling was never the gate — **maker-only was**.
2. **The admitted band is worth $0.0001/window** at best, against crossing
   costs of $0.03 per completion.
3. **Above ≈0.980 a crossed completion is loss-making by construction**, since
   the matched margin falls below our 2¢/contract taker fee. 0.99 is past
   break-even; 0.96 is the only tested value with real margin.

**Recommendation: keep 0.96/0.96. No change.** The binding constraint on
completion is not the ceiling and never was — it is that the economically
required complement price sits on the far side of the book, which is a
maker-only consequence, not a parameter.

## 7. What would be needed to run the real comparison

Stated so it can be costed, and **not** as a request:

- a continuous two-sided PMUS BTC15m book series — both sides, ≥1 level,
  ≥1 Hz — across **≥100 contiguous windows**, recorded **independently of our
  own order state** so no window is selected on the outcome;
- depth at ≥3 levels per side, to price a completion larger than the touch;
- settlement outcome per window (already available);
- our own fee schedule per fill (already available).

Only the first two are missing. `l5-pmus-observer` is the service that would
record them, and it is currently **writing nothing** — its volume is full
(`CONTINUOUS_OPERATION.md` §6b), which is the same blocker reported there and
still unresolved. **Fixing that collector is a prerequisite for ever answering
this question properly, and it needs no new infrastructure.**

## 8. On the 21–50 fill band

Confirmed closed, by both tests you set: the attribution reconciles only to
**±10%** (+$18,074 implied against +$16,436 canonical, a $1,638 gap from the
98.10%-accurate winner inference), and **t = 2.68 against the 2.86** required
by Bonferroni over the 12 ex-ante cohorts tested. **Not established**, and I
am not building on it.

No deployment, no arming, no risk change, no new infrastructure.
