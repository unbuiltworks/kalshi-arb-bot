# P8 audit — independent recomputation, assuming the conclusions may be wrong

**Date:** 2026-09-22 · **No live orders. Nothing armed.**

`analysis/gemini/p8_audit.py` imports **nothing** from `src.gemini_execution`.
The tape walk, fill rule, inventory, settlement, twin test and survivor rule
are written again from the raw records, so agreement is evidence and
disagreement is a finding. Every figure is also computed **with and without**
the mirrored-twin deduplication, because the audit's first question is whether
that deduplication should exist.

```
python3 analysis/gemini/p8_audit.py analysis/gemini/p7_bulk_tape.json
```

---

## Headline

The **arithmetic reproduces exactly**. Two *interpretations* do not survive,
one **claim is falsified**, and one **count is wrong**.

| # | P8 claim | reproduced? | original | audit | provenance | verdict |
|---|---|---|---|---|---|---|
| 1 | mirrored twins are one execution reported twice | — | "11.4% of prints… same-second complement at 1−p" | 1,474 of 1,476 candidate pairs are **same-side** (buy/buy or sell/sell); 7 of 7 re-pulled pairs carry **distinct `tid`s** | independent scan + live `/v1/trades` re-pull carrying `tid`/`timestampms` | **FALSIFIED as stated** |
| 1b | the survivor heuristic is neutral | — | "keep the row that continues the price series" | keeps rows a mean **0.055** from the previous price and deletes rows **0.385** away — it smooths by construction, deleting a mean excursion of **0.330** | independent scan | **NOT VERIFIED** |
| 2 | BTC5m C1 **DEV** matched +26.40 / residual −18.78 | yes | +26.40 / −18.78, net +7.62 | **+26.40 / −18.78, net +7.62** | independent walk | **VERIFIED** |
| 2b | BTC5m C1 **VAL** matched +9.18 / residual −11.31 = −2.13 | yes | +9.18 / −11.31 = −2.13 | **+9.18 / −11.31 = −2.13** | independent walk | **VERIFIED** |
| 2c | turnover, fills, residual qty, maxDD | yes | 94.38 / 355 / 213 / 7.54 (dev); 54.13 / 195 / 137 / 6.36 (val) | identical in every field | independent walk | **VERIFIED** |
| 2d | "fully matched markets: 135, net +$5.56" | partly | 135 markets | **17** markets, net **+$5.56**. 135 = 17 traded-and-matched **+ 118 never traded** | independent walk | **FALSIFIED (count); dollar value VERIFIED** |
| 3 | 53/115 residual markets and 94/213 units never had a complement **available** | arithmetic yes, wording no | 53 / 94 / −$7.79 | **53 / 94 / −$7.79** for *no complement PRINT*; a complement **QUOTE** was available in **217 of 217** readable BTC5m book cycles | independent walk + recorded book cycles | **SUPPORTED WITH CAVEAT — the word "available" is wrong** |
| 4 | capture sensitivity shows the sign is unstable | no | −0.72 / +0.98 / +1.52 at 0.50 / 0.33 / 0.25 (dev) | those are **one arbitrary sample**. Bernoulli draws ×12: dev@0.50 mean **+4.41**, range [−0.61, +8.92], 10 of 12 positive | independent Bernoulli reimplementation | **NOT VERIFIED — the evidence was an artifact** |
| 5 | HOLD > WAIT > COMPLETE NOW | partly | per-decision, BTC5m DEV | at **market level**: COMPLETE NOW is worst in **4 of 4** cells; HOLD beats WAIT in only **2 of 4** | independent market-level walk | **SUPPORTED WITH CAVEAT** |
| 6 | the `q_hat` orientation defect is real | yes | V1 passes `offered_side` into `need_side` | reproduced and pinned by test; corrected only in `ev_policy_v2` | unit tests on an extreme asymmetric book | **VERIFIED** |
| — | duration must come from declared life, not ticker shape | yes | 3,078 kept / 103 quarantined | **3,078 kept / 103 quarantined**, same lane counts | independent classifier | **VERIFIED** |

---

## 1. The mirrored-twin interpretation is falsified

P8 inferred "one execution reported from both sides of the ladder" from the
rows' **shape** alone — same second, prices summing to 1, equal quantity. Two
things the shape cannot fake now contradict it.

**Aggressor side.** The mirror story predicts one `buy` and one `sell` — the
same match seen from each side. The corpus says otherwise:

```
  ('buy','buy')  1463      ('sell','sell')  11
  ('sell','buy')    1      ('buy','sell')    1
```

**1,474 of 1,476 pairs are the same aggressor side** — 99.9%. Two `buy` prints
are not one execution seen twice.

**Trade ids.** `GET /v1/trades` returns `tid` and `timestampms`; **our
collector stored neither** (it kept only `timestamp`, `type`, `price`,
`amount`), which is why the original claim could only ever be an inference.
Seven corpus contracts from 09-21/22 are still inside the venue's ~7-day
retention and were re-pulled with full fields:

```
  GEMI-BTC2609212100-HI85000
    A tid 1893456011875179  ms ...979073  0.5  1000  sell
    B tid 1893456011875177  ms ...979055  0.5  1000  sell
```

**7 of 7 carry distinct `tid`s**, 5 of 7 differ in milliseconds, and all 7 are
same-side. By the venue's own identifiers these are **separate executions**.

They are also all at **0.50**, where `p + (1−p) = 1` holds trivially for any
two same-priced trades — the rule fires on a coincidence, not a mirror. Across
the whole corpus 50 pairs are of that degenerate kind.

**And the survivor rule is circular.** It keeps whichever row is nearer the
previous price, so it *enforces* the continuity it claims to detect: kept rows
sit a mean 0.055 from the previous price, deleted rows 0.385. It systematically
deletes prints near the **mirror** of the current price — which is exactly what
a complement looks like.

### What that costs

| | DEV matched | DEV residual | DEV net | VAL net |
|---|---|---|---|---|
| deduped (as published) | +26.40 | −18.78 | **+7.62** | −2.13 |
| raw, no dedupe | +29.98 | −18.69 | **+11.29** | −2.13 |

So the deduplication moves DEV net by 48% and leaves validation untouched.

**But it does not create the orphan problem**, which was the worry:

| lane / split | complement ever (deduped → raw) | pairs ≤1.00 (deduped → raw) |
|---|---|---|
| 5m dev | 59.9% → 58.4% | 44.9% → **42.4%** |
| 5m val | 53.8% → 54.1% | 35.9% → 35.8% |
| 15m dev | 71.4% → 69.9% | 46.3% → 45.8% |
| 15m val | 76.2% → 76.2% | 46.3% → 46.3% |

The structural finding is robust to the correction; the P&L is not.

**Recommendation:** the dedupe should be **withdrawn** until it can be
re-established from `tid`, and the collectors should store `tid` and
`timestampms` from now on. The duration correction is unaffected and stands.

---

## 3. "No complement available" is the wrong word

The arithmetic reproduces exactly: **53 of 115** residual markets, **94 of 213**
units, **−$7.79**. What it counts is *no complement **PRINT** on the needed side
while we held*.

That is not the same as no complement being **offered**. The recorded
COHORT-2 book cycles settle it:

| lane | readable cycles | both sides quoted | median YES spread |
|---|---|---|---|
| BTC5m | 217 | **217 (100.0%)** | **0.280** (p10 0.11, p90 0.33) |
| BTC15m | 46 | **46 (100.0%)** | 0.060 |

A complement was **quotable at every single readable cycle**. What was missing
was an *aggressor print on the needed side* — a maker-fill opportunity at our
price. Completing as a **taker** was always possible; at a 0.28 spread it costs
`1 + spread ≈ 1.28` per pair, so it was available and uneconomic, which is a
different statement from unavailable.

**This also corrects a P9 claim of my own.** P9 said achieved pair margins of
0.28–0.36 were "ten times the quoted spread of 0.01–0.04". That spread came
from an all-families snapshot dominated by long-dated markets. **BTC5m's own
book spread is 0.28 median**, so the achieved margins are consistent with
spread capture and are *not* prima facie evidence of directional selection. The
identity (`pair margin = p2 − p1`) still holds; the inference drawn from it was
overstated and is withdrawn.

P9-F's conclusion survives the correction — re-run at the lane's real spread,
a two-sided quote is still negative everywhere (BTC5m DEV −$0.047/quote at
s=0.28, −$0.037 at 0.33; VAL −$0.127 at 0.28) — but it was previously only
tested up to s=0.20, below the actual spread, and that gap was mine.

---

## 4. The capture model is an artifact

`capture` is implemented as "take every Nth qualifying print"
(`seen % int(1/capture) == 0`). That is deterministic and **phase-locked**: at
capture 0.50 the 1st, 3rd, 5th qualifying print of *every* market is always
missed. It is one arbitrary sample, not an expectation.

Replacing it with an independent Bernoulli draw per print (12 seeds):

| split | capture | every-Nth (published) | Bernoulli mean | range | sign |
|---|---|---|---|---|---|
| dev | 0.50 | **−0.72** | **+4.41** | [−0.61, +8.92] | 10+ / 2− |
| dev | 0.33 | +0.98 | +3.93 | [−1.75, +9.71] | 9+ / 3− |
| dev | 0.25 | +1.52 | +3.44 | [−2.07, +7.81] | 9+ / 3− |
| val | 0.50 | −4.39 | −0.33 | [−5.49, +3.05] | 5+ / 7− |
| val | 0.33 | **+0.70** | −0.41 | [−4.89, +2.76] | 7+ / 5− |
| val | 0.25 | −1.56 | +0.04 | [−4.77, +4.36] | 7+ / 5− |

The published capture figures sit far from the mean of their own distribution
and sometimes on the other side of zero. **Every "the sign flips with capture"
statement in P7, P8 and P9 rests on an artifact.** The underlying conclusion —
no stable edge — survives, and the fairer reading is *DEV positive, validation
about zero*. Only the capture-1.00 column is thinning-free and directly
comparable.

Two things the audit confirms are sound: market state is updated from **every**
print regardless of thinning, and settlement/inventory accounting is untouched
by it. The defect is the sampler, not the accounting.

---

## 5. WAIT re-audited at market level

Each market walked once, inventory respected, contributing once. Same
acquisition rule throughout; only the completion ceiling varies.

| lane | split | HOLD (never) | WAIT ≤0.92 | WAIT ≤1.00 | COMPLETE NOW |
|---|---|---|---|---|---|
| 5m | dev | **+21.45** | +7.62 | +6.12 | +3.41 |
| 5m | val | −3.18 | **−2.13** | −4.46 | −5.60 |
| 15m | dev | −3.75 | −4.28 | **−2.81** | −5.46 |
| 15m | val | **+2.85** | −0.35 | −2.68 | −8.93 |

**COMPLETE NOW is worst in 4 of 4 cells** — that part of P8-B is verified and
robust. **HOLD beats WAIT in only 2 of 4** — the full ordering is not. And
BTC5m DEV's HOLD result (+$21.45, entirely residual on 243 unpaired units)
becomes −$3.18 on validation, which is what a coin flip looks like.

---

## 6. The orientation defect, corrected in a new version only

V1 is untouched. `ev_policy_v2` changes one lookup and imports everything else
from V1, so the two can differ only in orientation. The audit added tests on a
deliberately extreme book — YES printing at 0.10 throughout, NO at 0.88 — where
a reversal changes the **action**, not a decimal:

```
  V2 (correct):  0.10 + 0.88 = 0.98 projected pair  -> a poor acquisition
  V1 (defect) :  0.10 + 0.10 = 0.20 projected pair  -> looks superb
```

plus the mirrored case from the NO side, and a `p_complete` orientation test
(9 NO prints to 1 YES print must give a YES buyer 9/10, not 1/10).

---

## What P9 may now use as input

* **May use:** the C1 DEV/VAL accounting, the residual-band decomposition, the
  53/94/−$7.79 count **read as "no complement print"**, the duration
  correction, the orientation defect.
* **May not use without restating:** anything resting on capture < 1.00;
  anything describing a complement as "unavailable" or "not offered"; the
  "135 fully matched markets" count; the claim that achieved margins exceed
  the venue's spread.
* **Withdrawn pending `tid`-based re-derivation:** the mirrored-twin
  deduplication, and every P&L figure computed on the deduped tape. Base-rate
  and completion-availability findings are robust to it.
