# P8-A/B/C — the extracted pair rule, WAIT recomputed, and the corrections made canonical

**Date:** 2026-09-22 · **Data:** Gemini public tape 2026-09-16..22, corrected
· **Live orders placed: none. Nothing armed. No rebate term anywhere below.**

Split, unchanged and not re-drawn: **DEV 09-16..18**, **VALIDATION 09-19..20**.
09-21..22 is *previously exposed* and was **not read for this round**. 09-23
onward is the sealed forward set and was not read either.

**Reproduce:**

```
python3 analysis/gemini/p8ab_run.py  analysis/gemini/p7_bulk_tape.json
python3 analysis/gemini/p8b_wait.py  analysis/gemini/p7_bulk_tape.json
```

Both read the raw pull committed at `p7_bulk_tape.json` and apply the
corrections on load, so every number below comes from the rows the venue
actually returned.

---

## Answer first

**No. There is no pre-rebate BTC5m candidate worth carrying into the 09-23
forward test.**

The extracted rule's DEV optimum (+$7.62 over 132 markets, ROI 8.1%, at the
capture=1.00 upper bound) **flips to −$2.13 on validation**, and on DEV alone
it already flips sign as the capture assumption moves (+7.62 → −0.72 → +0.98 →
+1.52 across capture 1.00/0.50/0.33/0.25). A result whose sign depends on the
participation assumption is not a mechanism.

The round is not empty, though. It produced three findings that are stable,
and they point the next round somewhere other than threshold tuning:

1. **The loss is in acquisition, not completion.** Given a leg is already
   held, holding it beat completing it at the prices complements were actually
   offered (BTC5m DEV: 0.704 vs 0.598 per decision, *even for the completions
   the frozen ceiling accepts*). The purchase was the error, not the failure
   to unwind it.
2. **About half the residual is unreachable by any completion policy.** 53 of
   115 BTC5m residual markets — 94 of 213 unpaired units, −$7.79 — never saw a
   single complement offered while we held. No WAIT rule, no ceiling, no
   deadline can touch those.
3. **`ev_policy.decide` has a side defect** (below). Every P7/P8 EV arm was
   scored with an acquisition test that read the wrong side of the book.

---

## P8-C — the corrections, made canonical

`src/gemini_execution/tape_normalize.py` (`GEMINI_TAPE_NORMALIZE_V1`) now owns
both fixes; `analysis/gemini/p8_dataset.py` is the only supported way to load
tape and applies them on load. The ad-hoc copy in `p8_make_clean.py` is gone —
it calls the module. Regression tests: `tests/test_tape_normalize.py` (16).

| | rule | guard |
|---|---|---|
| duration | derived from the contract's own `effectiveDate`→`expiryDate` (or a recorded `life_min`), never from the ticker | a lifetime in no band **quarantines** the contract rather than guessing a lane |
| mirrored twins | same second + prices summing to exactly 1 + equal quantity → one execution; the row continuing the contract's price series survives | either condition alone is *not* a twin, and is tested for |

**The module reproduces the earlier ad-hoc correction exactly** — same 3,078
contracts, same 23,379 prints, print for print. So P8's corrected arm table
stands as published.

**Auditability.** `normalize()` keeps `prints_raw` and lists every dropped row
with the row that replaced it; `restore()` reconstructs the input exactly
(tested). Raw pulls on disk are never rewritten.

### Re-running the conclusions whose denominator used the contaminated tape

| claim | corrected |
|---|---|
| "11.4% of prints are mirrored twins" | **1,291 echo rows = 5.2%** of the 24,670 rows on studied contracts (≈10.5% of rows *participate* in a twin pair; half of each pair is the echo). 5.3% across everything pulled. Quantity 6.5%. |
| P7 arm table (P8-corrected) | unchanged — it was already computed on this exact corrected set |
| P4 habitat / P6 duration screening | **not contaminated by the duration defect** — both collectors recorded `life_min` per contract and every row is consistent with its lane (355/355 5m, 116/116 15m, 258/258 1h). Their trade counts and notionals are inflated by the twin defect by ~5–7%, which moves no ranking: **no contract in the corpus was two-sided only because of twins** (1,771 before, 1,771 after). |

---

## P8-A — the extracted pair rule as an independent policy

`src/gemini_execution/pair_policy.py` (`GEMINI_PAIR_POLICY_V1`). No `p_win`,
no probability model, no import of `ev_policy` — asserted by test.

```
ACQUIRE a leg at p   iff   p + q_hat(complement)  <=  acq_max
COMPLETE a leg at a  iff   a + p                  <=  comp_max
```

`q_hat` is the mean price at which the **complement** side has printed so far
in this contract. **When the complement has never printed, the answer is SKIP**
— no fallback, because any fallback assumes a complement at a price nobody has
paid, which is precisely how a cheap first leg becomes a directional bet.

### DEV response surface, BTC5m, capture 1.00 (upper bound)

| acq \ comp | 0.88 | 0.92 | 0.98 | 1.12 | 1.30 |
|---|---|---|---|---|---|
| 0.85 | +3.98 | +6.17 | +3.91 | | |
| 0.88 | +6.01 | **+7.92** | +6.12 | | |
| 0.90 | +7.18 | **+7.62** | +5.95 | +4.36 | +3.57 |
| 0.93 | | | +4.52 | +2.52 | +1.07 |
| 0.95 | | | +3.44 | +1.77 | +0.64 |
| 1.00 | | | −6.61 | −4.47 | −6.12 |

Two readings, both interpretable:

* **The acquisition filter is doing real work.** Removing it (acq 1.00) turns
  DEV from +$7.62 to −$6.61.
* **A *higher* completion ceiling is worse, monotonically.** Completing at
  expensive pair costs destroys more value than the residual it removes. This
  is the opposite of what the imported `p_win` curve prescribed (pair limits of
  1.16–1.25 for cheap legs) and it is consistent with that curve being
  falsified on Gemini.

**The optimum is interior, not a corner** — the grid was extended past its
first boundary (acq down to 0.75, comp down to 0.88) precisely to check that,
and the surface falls away on both sides. It is also **flat**: everything in
acq 0.85–0.90 × comp 0.88–1.02 sits between +$3.4 and +$7.9. Per market that
is $0.058 at the very best cell.

**BTC15m is negative across the entire interior** (−$1.0 to −$4.9). Only the
no-filter corner (acq 1.00) is positive, which is the corner that is negative
on 5m. Consistent with P6/P7: BTC15m is not a lane.

### Frozen candidates → validation, one evaluation

Frozen on DEV before validation was scored: **C1 = BTC5m acq 0.90 / comp
0.92**, C2 = BTC5m acq 0.88 / comp 0.92, C3 = BTC15m acq 0.90 / comp 0.98.

| candidate | split | cap 1.00 | cap 0.50 | cap 0.33 |
|---|---|---|---|---|
| C1 BTC5m | dev | **+7.62** | −0.72 | +0.98 |
| C1 BTC5m | **val** | **−2.13** | −4.39 | +0.70 |
| C2 BTC5m | dev | +7.92 | −0.77 | +1.98 |
| C2 BTC5m | **val** | **+0.14** | −2.79 | +2.30 |
| C3 BTC15m | dev | −2.01 | −2.67 | +2.66 |
| C3 BTC15m | **val** | +0.44 | −5.08 | −4.02 |

C1 validation detail (capture 1.00): 68 markets, 195 fills, $54.13 turnover,
matched +$9.18, residual −$11.31, **net −$2.13**, ROI −3.9%, pair completion
0.30, terminal residual 137 units, maxDD $6.36, worst market −$1.27, worst day
−$3.67.

**Nothing here is a candidate.** The sign is not stable across the split and
is not stable across capture within a single split.

### The part that had to be checked: the cheap leg that never gets its complement

BTC5m, DEV, C1, capture 1.00 — every market grouped by what its **unpaired**
leg cost:

| residual leg | mkts | qty | cost | residual $ | return on cost | dominant won |
|---|---|---|---|---|---|---|
| fully matched | 135 | 0 | — | — | — (net **+$5.56**) | — |
| <0.20 | 43 | 82 | 10.59 | −3.59 | **−33.9%** | 4/43 |
| 0.20–0.35 | 52 | 93 | 24.42 | −14.43 | **−59.1%** | 8/52 |
| 0.35–0.50 | 20 | 38 | 14.76 | −0.76 | −5.1% | 9/20 |
| **all residual** | **115** | **213** | **49.78** | **−18.78** | **−37.7%** | |

So the hazard is real and it is the dominant term: the rule generates $26.40 of
matched P&L and hands back $18.78 of it through legs that never paired. **The
entire net of the clean half of the book is $5.56 over 135 markets**, and the
residual half is a coin flip around zero with a fat negative tail.

BTC15m is worse and more explicit: its `<0.20` residual bucket won **0 of 39**
markets and returned **−100.0% of cost**.

**A floor on what a leg may cost does not rescue it.** Added as a third,
interpretable axis *after* seeing the table above (so: weaker evidence, DEV
only): min leg 0.20 → +$5.14, 0.25 → −$3.50, 0.30 → −$1.50, 0.35 → +$0.49,
0.40 → +$0.52. Non-monotone, sign-unstable, strictly worse than no floor. The
cheap legs are not separable from the profitable ones by their price.

---

## P8-B — WAIT, recomputed from scratch

Previous WAIT statistics are **discarded**; these are computed on the corrected
and deduplicated set only. A "decision moment" is every instant at which we
held an unpaired leg and the tape offered its complement. BTC5m DEV: 173
moments. BTC15m DEV: 245.

### 1. Does "the complement gets cheaper later" survive?

| | BTC5m | BTC15m |
|---|---|---|
| any later complement print exists at all | 112/173 (64.7%) | 176/245 (71.8%) |
| a **strictly cheaper** one exists (hindsight) | 67/173 (**38.7%**) | 110/245 (**44.9%**) |
| hindsight best improvement, median | 0.220 | 0.250 |

It survives, but as a minority event, and **35% of the time there is no later
complement at all** — waiting there is a decision to settle directional.

### 2/3. Reachability, then fill, then the full cost of failing

A passive order pre-committed at `q − δ` (no hindsight minimum is used as a
target). If the target is never reached, **WAIT pays the settlement outcome**,
win or lose. Values are summed over all 173 BTC5m DEV decisions:

| δ | capture | reached | filled | **WAIT** | **COMPLETE NOW** | **HOLD** |
|---|---|---|---|---|---|---|
| 0.01 | 1.00 | 67/173 | 67/173 | 62.88 | 59.17 | **78.00** |
| 0.02 | 1.00 | 64/173 | 64/173 | 64.89 | 59.17 | **78.00** |
| 0.02 | 0.33 | 64/173 | 31/173 | 68.89 | 59.17 | **78.00** |
| 0.05 | 1.00 | 50/173 | 50/173 | 65.64 | 59.17 | **78.00** |
| 0.05 | 0.33 | 50/173 | 25/173 | 70.53 | 59.17 | **78.00** |

**WAIT beats COMPLETE NOW at every δ and every capture — and loses to HOLD at
every δ and every capture.** The ranking is HOLD > WAIT > COMPLETE NOW and it
does not depend on the tuning.

Why: at a decision moment the complement is **expensive** (BTC5m mean 0.658,
median 0.730) while the held leg is **cheap** (mean cost 0.260) and settles in
the money **45.1%** of the time. Paying 0.73 for a 0.45 outcome is a bad trade
whatever you call the policy.

This holds even inside the frozen ceiling, which is the uncomfortable part:

| BTC5m DEV | n | COMPLETE, per decision | HOLD, per decision |
|---|---|---|---|
| offers the 0.92 ceiling **accepts** | 71 | 0.598 | **0.704** |
| offers it **refuses** | 102 | 0.164 | **0.275** |

BTC15m is the one place completion pays, mildly: accepted offers 0.521 vs
0.471. It is also the lane that is negative overall.

**Two caveats stated rather than buried.** (a) These are per-*decision*
comparisons; one leg held across several offers is counted at each, so the
columns are not a portfolio P&L — the market-level confirmation is the DEV
surface above, where lower completion ceilings scored better. (b) "HOLD beats
completing" ignores sunk cost, correctly, and says nothing good about having
bought the leg: P8-A shows that purchase returned −37.7% on cost.

### 4. The half that no completion policy can reach

BTC5m DEV, C1, markets that ended holding a residual, by whether a complement
was **ever** offered while we held:

| | mkts | residual qty | residual $ |
|---|---|---|---|
| **no complement ever offered** | **53** | **94** | **−7.79** |
| 1–2 offers | 42 | 77 | −4.92 |
| 3+ offers | 20 | 42 | −6.07 |

46% of residual markets and 44% of residual units were never completable at any
price. **WAIT is not the missing piece, and neither is any other completion
rule.** The reachable half is the half where holding already won.

---

## Defect found in `ev_policy` (documented, deliberately not fixed)

`decide()` calls `acquire_value(price, state, offered_side)` — the parameter is
named `need_side` and receives the side being **offered** rather than the
complement. So `p_complete` and `q_hat` describe the wrong side of the book and
the acquisition test degenerates from "price + expected complement" into
roughly "twice this price".

Left in place on purpose: every P7/P8 EV arm was scored with this behaviour and
silently changing it would make those results unreproducible. It is recorded in
the module docstring, `pair_policy` states the corrected arithmetic, and
`ev_replay(..., qhat_side="same")` reproduces the legacy behaviour for
comparison.

---

## What this round changes about the programme

* Threshold tuning on acquisition/completion is **finished** as a line of work.
  The surface has been mapped, its optimum is interior, flat, worth ~$0.06 per
  market at an upper bound, and does not survive the split.
* The binding constraint is **which legs get opened**, and the discriminator is
  not the leg's price — a floor on price makes it worse.
* The venue-side constraint is **one-sidedness**: nearly half of our unpaired
  inventory sits in markets that never offered a complement at all. That is the
  participation limit P3 measured, arriving in the P&L.

## Held, unchanged

No live orders. Nothing armed. No rebate term entered model selection. No
Gemini probability refit. No hourly candidate. 09-23+ collector untouched and
unread.
