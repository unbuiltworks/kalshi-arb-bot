# Adverse selection on filled opening legs — measured

**2026-10-24.** No policy change, no rearm, no capital change, no new
infrastructure. The expired campaign stays expired.

The reconciliation and the superseded-analysis warnings in
`CRITICAL_REVIEW_RECONCILIATION_20261024.md` stand and are preserved.

---

## 1. Cashflow completeness of +$345,525.64

Matching the generating artefact to the cent establishes **reproducibility**.
Five independent checks on **completeness**:

| check | result |
|---|---|
| **Row types** | Only `TRADE`, `REDEEM`, `MAKER_REBATE`, `TAKER_REBATE`, `REWARD` exist in the whole corpus. **No MERGE / SPLIT / CONVERSION type appears**, so no merge cashflow is being silently dropped by type. |
| **Missing buy data** | **0 orphan REDEEM rows** — every redemption's window has trade rows. The 101 gaps >6h between traded windows are genuine inactivity, not fetch holes. Participation is **34.9%** of available windows (5,546 of 15,889). |
| **Unredeemed matched inventory** | **304 of 5,533 resolved windows have `redeem_in < matched pairs`**, total shortfall **3,279.14 pairs**. A matched pair redeems $1 regardless of outcome, so this is **≈ +$3,279 of value earned but never collected** — 0.056% of pairs. |
| **Outstanding inventory** | `positions_all.jsonl`: 17 btc15m positions, 7,014.12 shares, `currentValue` **$2,880.73**, not in realized cashflow. `positions_redeemable.jsonl`: 13 btc15m at value $0.00 — these are the 13 unresolved windows. |
| **Excluded cashflows** | Other series net **−$733.48** (trade $1,228,485 vs redeem $1,227,752) — correctly outside a btc15m figure. |

### The incentive finding, which corrects my own earlier claim

**All 204 incentive rows carry an EMPTY slug**, totalling **$71,581.68**
(MAKER_REBATE $35,258.26, REWARD $28,797.69, TAKER_REBATE $7,525.73). The
artefact excludes them because an empty string does not *contain*
"btc-updown-15m" — **not** because they belong to another market.

**Retraction.** I previously stated "zero incentives in this series, so L5's
edge is pure pair economics." That is not established. The correct statement
is that **$71,581.68 of incentives are account-level and unattributable**.
btc15m is 83.2% of his buy volume, so a pro-rata share would be ≈ $59,556.

### Reconciled completeness ladder

| basis | figure |
|---|---|
| **Realized cashflow, btc15m only (authoritative, reproducible)** | **+$345,525.64** |
| + unredeemed matched pairs at their $1 floor | +$348,804.78 |
| + outstanding inventory at fetch (`currentValue`) | **+$351,685.51** |
| + pro-rata share of unattributable account incentives *(attribution unproven)* | ≈ +$411,241 |

**Verdict: the figure is reproducible, internally consistent, and free of
missing buys or unknown cashflow types — but it is NOT cashflow-complete.** It
understates by ≈$6,160 of uncollected and outstanding value, and sits inside a
±$71,582 attribution uncertainty from account-level incentives. For
decision-making the authoritative realized figure remains +$345,525.64, with
those bounds stated.

---

## 2. Conditional opening-leg edge — measured on 956 modelled fills

Source: the deployed shadow controller, queue-aware, on **live** authenticated
books. Counters since 2026-10-19T19:55:10Z, **475 windows observed**,
`controller_errors: 0`, `counters_cover_all_settled_windows: true`.

| | |
|---|---|
| quotes placed | 1,322 |
| filled | 956 |
| unfilled at expiry | 304 |
| **fill rate** | **0.7587** |
| **adverse fills** | **876 → adverse_rate 0.9163** |
| `adverse_usd` (net wire move against us, all fills) | **$79.0200** |
| **per filled contract** | **−$0.0827** |
| **swept fills** | **937 of 956 = 98.0%** |

`adverse_selection` is defined as the wire move from placement to window close
**against our own outcome**, so for a binary that settles at 0 or 1 it is
approximately the negative of our per-contract P&L.

> **Estimated conditional opening-leg edge: ≈ −$0.083 per filled contract,
> with 91.63% of fills adverse.**

This is *conditional on plausible maker execution*, not on submission: the
denominator is fills, and 304 submitted orders that never filled are excluded
from it.

**Submitted / touched / plausibly filled / venue-confirmed**, kept distinct:

| class | count | basis |
|---|---|---|
| submitted (modelled) | 1,322 | quotes placed |
| plausibly filled (queue-aware) | 956 | 937 by sweep, 19 otherwise |
| unfilled at expiry | 304 | never reached |
| **venue-confirmed filled** | **6** | Stage 1 only — 5 opening legs + 1 complement |

---

## 3. Expected net P&L including completion and residual settlement

| | |
|---|---|
| pair_net (completion economics) | **+$29.9396** |
| residual_net (settlement of unmatched) | **−$85.2296** |
| **net** | **−$55.29** |
| net per settled window | **−$0.1523** |
| capital deployed | $292.29 |
| **capital efficiency** | **−18.92%** |
| **max drawdown** | **$57.01** |
| turnover | 1,322 quotes / 475 windows = **2.8 per window** |
| opportunity cost | 99,293 justified refusals, **0 possible misses** |

**Completion is profitable; residual settlement destroys it, 2.85 : 1.**

### Against WAIT, and against one inventory-aware alternative

| policy | net | capital | drawdown |
|---|---|---|---|
| **WAIT** | **$0.0000** | 0 | 0 |
| baseline (current) | **−$52.25** | $287.25 | $54.51 |

**WAIT dominates the current policy by +$52.25 over 355 windows.**

The inventory-aware alternative is read directly off the stratification below:
stop at 2 residual units instead of 3.

---

## 4. Stratification — where the loss actually lives

**By residual at close:**

| \|residual\| | n | net | pair | residual | **net/window** |
|---|---|---|---|---|---|
| 0 | 29 | +$3.89 | +3.89 | 0.00 | **+0.1341** |
| 1–2 | 159 | +$51.65 | +18.07 | **+33.58** | **+0.3248** |
| **3+** | **167** | **−$107.79** | +7.98 | **−115.77** | **−0.6454** |

**By matched units:**

| matched | n | net | net/window |
|---|---|---|---|
| 0 | 299 | −$94.13 | −0.3148 |
| 1 | 29 | +$15.90 | +0.5483 |
| 2+ | 27 | +$25.98 | +0.9622 |

Two findings, both sharp:

1. **Carrying residual is not the problem — accumulating it is.** Windows
   ending with 1–2 residual units are *profitable*, and their residual
   component is **positive** (+$33.58). Only at **3+** does it collapse
   (−$115.77). The losing stratum is exactly the windows that reach the
   deployed residual cap of 3 — and `residual_cap:3>=3` fired 12,051 times.
2. **Completion is the discriminator.** Any matched pair makes a window
   profitable; 299 of 355 windows match nothing and carry −$94.13.

### Chronological out-of-sample

| | n | net | per window | pair | residual | capital eff. |
|---|---|---|---|---|---|---|
| In-sample (first 70%) | 248 | −$44.71 | −0.1803 | +20.84 | −65.55 | −21.84% |
| **Out-of-sample (last 30%)** | 107 | −$7.54 | **−0.0705** | +9.10 | −16.64 | **−9.13%** |

**Negative in both halves.** The edge is negative out-of-sample, less severely
so. This is the robustness check that Stage 1's five windows could not provide.

---

## 5. Uncertainty and queue sensitivity

**The pessimistic queue assumption is already the binding one.** 937 of 956
fills (**98.0%**) came from a *sweep* — a print consuming the entire level,
which is the only way the model fills an unknown queue position. So the
measured edge is **not** an artifact of optimistic queue modelling: under the
most pessimistic assumption (never fill unless swept) 98% of these fills
survive, and the measured edge barely moves.

Remaining uncertainties, named:

- **Modelled, not venue-confirmed.** 956 modelled fills against 6
  venue-confirmed. Per the owner's instruction this is not a promotion basis.
- **Per-scenario sensitivity is not in hand.** The controller runs three fill
  scenarios and four placement arms; the 81KB heartbeat truncation surfaced one
  aggregate block. Comparing AT_TOUCH against ONE_TICK_BEHIND needs the other
  blocks and is not yet read.
- **`adverse_usd` is a wire-move proxy** for P&L, not booked P&L.
- **5 days / 475 windows**, one asset, one volatility regime.

**On the 21.2% figure:** it says only that Stage 1's particular four-loss draw
was not *individually* significant. It does **not** establish fair pricing, and
this measurement contradicts fair pricing — 91.63% adverse over 956 fills and
negative capital efficiency in both out-of-sample halves. The two are
consistent: the policy has negative edge, and Stage 1 was a bad draw from a
negatively-skewed distribution.

---

## 6. Which bottleneck?

> **Adverse selection — realised through imbalance accumulation, not through
> completion latency and not through insufficient volume.**

- **Adverse selection: confirmed and quantified.** 91.63% of fills adverse,
  −$0.083 per filled contract, 98% of fills by sweep. We fill when the market
  runs through our level.
- **Completion latency: not the binding constraint.** `possible_misses: 0` —
  the controller never identified an opportunity it should have taken and
  missed. The largest refusal bucket is `already_resting` (66,044), i.e. the
  one-order limit, not slow evaluation.
- **Insufficient executable volume: rejected, and it would make things
  worse.** Fill rate is already 75.87%. At a 2.85:1 residual-to-pair loss
  ratio, more volume scales the loss faster than the gain. L5's 110 legs/window
  is not a remedy we can borrow while each fill carries −$0.083.

The decisive detail is the stratification: adverse selection is *survivable*
at 1–2 residual units (+$0.3248/window, positive residual component) and
*fatal* at 3+ (−$0.6454/window). The engine's EXTEND behaviour walks inventory
from the survivable regime into the fatal one.

---

## 7. One concrete next action — no new infrastructure

> **Add one shadow arm with the absolute residual cap at 2 instead of 3,
> beside the unchanged baseline, and compare over the same windows.**

- Uses the existing `ShadowController`, which already takes
  `max_abs_residual` and already runs four arms over three fill scenarios. No
  new service, no new branch, no capital, no live change.
- It is the direct test of the one sharp finding: the 3+ stratum holds
  −$107.79 of the −$52.25 net while the 1–2 stratum is positive.
- **Crude expected effect, stated as crude:** the 3+ stratum loses ≈$0.23 per
  residual unit. Removing roughly one unit per window from 167 windows is
  ≈ +$38, which would move −$52.25 toward ≈ −$14. **Still negative** — this is
  a loss-reduction test, not a path to profit, and it must not be promoted as
  one.
- Pre-registered read: compare net, net/window, residual_net, pair_net,
  capital efficiency, max drawdown and adverse_rate between the two arms over
  ≥200 common windows, chronologically split.

**Authorization: not required for a shadow arm, and not sought for anything
else.** On present evidence the live policy should remain unarmed: WAIT
dominates it by +$52.25 over 355 windows, out-of-sample included.
