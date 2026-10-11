# Favorite-inventory lane — VALIDATION GATE

Frozen code `51fe6efd` unmodified. **The prospective OOS cohort
(`data/gemini_infoedge`) has NOT been read and remains sealed.** No live
orders. The closed passive-maker conclusion at `115de70b` is untouched.

# GATE: **A — no BTC-state predictive signal survives validation**

...with one honest qualification that matters, stated in §1: the
**settlement** arm of the BTC-state test was *underpowered*, while the
**repricing/exit** arm was well-powered and clearly negative. So A is reached
on strong evidence for D and on weak-but-unfavourable evidence for C.

**Per the stated rule — "do not open OOS unless at least C or D survives
VALIDATION" — the OOS data stays unread.**

---

## 1. THE BTC-STATE SETTLEMENT TEST WAS UNDERPOWERED — AND WHY

| BTC05M markets in `ref_lag` | 776 |
|---|---|
| with `pbook` | 179 |
| with settlement | 465 |
| **book + settlement** (baseline test used this) | **176** |
| **book + settlement + strike** (BTC-state test needs this) | **38** |
| …of which enough trailing spot for `dist_vol` | **28** (18 DEV / 10 VAL) |

**The strike — not the BTC feed — is the binding constraint.** The recorder
ran in bounded ~3 h sessions, so settlement rows arrive for many past markets
whose `window` row we never held, and vice versa; the three-way intersection
is small.

With 18 DEV windows a settlement frequency carries a window-clustered
**se ≈ 0.118**, so **separations below ~0.24 are undetectable**. The
"75% vs 45%" conditional separation you asked about is a 0.30 effect — right
at the edge of what this cohort could see even if it existed.

**Q1, is the market even calibrated, shows the noise directly:**

| mid bucket | DEV settle freq (±se) | VAL settle freq (±se) |
|---|---|---|
| 0.35–0.45 | 0.533 ± 0.207 | **0.121 ± 0.224** |
| 0.45–0.55 | 0.768 ± 0.160 | 0.689 ± 0.281 |
| 0.55–0.65 | 0.613 ± 0.196 | 0.579 ± 0.224 |
| 0.65–0.80 | 0.850 ± 0.160 | **0.153 ± 0.246** |

The 0.65–0.80 bucket settles **85%** in DEV and **15%** in VALIDATION. Those
cannot both be true; at 5–8 effective windows per cell this is noise, not
structure. **Calibration itself is not measurable on this cohort**, so
conditional separation on top of it certainly is not.

**Q2/Q3 replication: NONE.** No state separation significant in DEV kept its
sign and magnitude in VALIDATION. Several DEV separations looked large
(`dist_vol` −0.43, −0.37, −0.47 across mid buckets) but rest on 2–5 windows
per cell and do not replicate.

**I am therefore NOT claiming "BTC state does not predict settlement."** I am
claiming it **cannot be established on the available historical data**, and
that nothing survived the validation split. That distinction is the finding.

## 2. THE REPRICING / EXIT TEST WAS WELL-POWERED AND IS CLEARLY NEGATIVE

This arm needs no strike, so it runs on the full **176 windows / ~8–10 k
observations** per split. This is where the statistical power actually is.

**Forward favourable mid movement vs the contemporaneous spread:**

| horizon | DEV: fav move p50 / p90 | **P(move > spread)** | VAL: p50 / p90 | **P(move > spread)** |
|---|---|---|---|---|
| 1 s | +0.000 / +0.065 | **0.2%** | +0.000 / +0.060 | **0.1%** |
| 5 s | +0.005 / +0.090 | **0.4%** | +0.005 / +0.095 | **0.5%** |
| 10 s | +0.005 / +0.110 | **0.5%** | +0.005 / +0.130 | **1.3%** |
| 30 s | +0.015 / +0.155 | **2.0%** | +0.005 / +0.175 | **4.6%** |
| 60 s | +0.015 / +0.185 | **3.4%** | −0.045 / +0.185 | **7.4%** |

**The contract does reprice — it just almost never reprices by more than the
spread you must cross twice.** Taker round trip (buy ask → sell bid):

| horizon | DEV p50 / mean / **P(>0)** | VAL p50 / mean / **P(>0)** |
|---|---|---|
| 1 s | −0.240 / −0.243 / **0.2%** | −0.200 / −0.203 / **0.4%** |
| 10 s | −0.220 / −0.246 / **2.7%** | −0.190 / −0.203 / **4.0%** |
| 30 s | −0.220 / −0.252 / **7.7%** | −0.200 / −0.231 / **8.4%** |
| 60 s | −0.230 / −0.265 / **10.7%** | −0.270 / −0.298 / **10.1%** |

Totals −$1,800 to −$2,550 per split. **Latency is irrelevant**: 0.25 s vs
1.0 s changes the mean by under a cent. The binding constraint is the spread,
not speed.

**Q4 rank correlations do not replicate either.** DEV correlations are all
small (|ρ| ≤ 0.25); VALIDATION ones are larger but **mostly sign-flipped**
(`dist` +0.026 → **−0.540**; `frac_above` −0.071 → **−0.660**;
`sigma_per_sqrt_s` +0.162 → **+0.568**). With 10 VAL windows a few trending
windows dominate the ranks. Only two cells kept a sign, both weak. **This is
the signature of no stable signal, not of a signal we mis-measured.**

## 3. CONTINUOUS INVENTORY — THE MECHANISM WORKS, THE FREQUENCY DOES NOT

**Favorite flips are bimodal, and the median window never flips at all:**

| | DEV | VALIDATION |
|---|---|---|
| flips/window p50 | **0** | **0** |
| flips/window p90 / max | 17.6 / 61 | 9.0 / 67 |
| **zero-flip windows** | **66/105 (63%)** | **51/71 (72%)** |

So "accumulate the favorite, switch when it flips" has **no flip to act on in
roughly two-thirds of windows**.

**Maker/maker matched inventory — the only matched construction with positive
economics (cost 1 − spread):**

| | DEV | VALIDATION |
|---|---|---|
| windows with a defensible maker fill on **both** sides | **7/105 (6.7%)** | **1/71 (1.4%)** |
| matched contracts obtained | 54.75 | 10 |
| **net P&L after fees and rebates** | **+$5.85** | **+$1.00** |
| contracts needed for +$5 (at p50 spread) | 19.2 (38.5 fills) | 22.7 (45.5 fills) |

**This is the one genuinely positive result in the lane, and it is real but
negligible.** When both legs fill as maker the economics are exactly as the
identity promises — positive. But it happens in 1.4–6.7% of windows and
yields **+$0.056/window (DEV) and +$0.014/window (VAL)** averaged over all
windows. At 288 windows/day that is **$16/day and $4/day of *gross matched*
economics**, before any failed-window directional losses, and the frequency
**falls 5× from DEV to VALIDATION**.

Against the +$5/window target it is short by **~90×**.

## 4. WHY EACH BRANCH FAILS — ONE SENTENCE EACH

| branch | verdict | mechanism |
|---|---|---|
| favorite-only directional (A/B) | rejected earlier at `51fe6efd` | spread handicap (break-even 0.60–0.64 vs ~50% win rate); maker fills adversely selected |
| **BTC-state settlement (C)** | **not established** | strike present in only 38/776 windows → se 0.118, nothing replicates |
| **exit / repricing (D)** | **rejected** | mid moves > spread in 0.1–7.4% of cases; taker round trip P(>0) ≤ 10.9% |
| **continuous inventory (E)** | **no surviving edge to improve** | median window has zero flips; maker/maker both-sides fills in 1.4–6.7% of windows |
| taker→taker matching (F) | rejected by identity | pair cost = 1 + spread, enforced in code |

## 5. CONSEQUENCE FOR THE OOS DATA

**Nothing survived VALIDATION, so the OOS cohort stays unread**, exactly as
the gate rule requires. It is preserved sealed.

**One thing worth your decision, stated without pushing it:** the BTC-state
settlement question is the only one this lane did **not** actually answer, and
`data/gemini_infoedge` is precisely the dataset that could — it carries the
**strike**, a **high-frequency Coinbase BTC reference (~4.7 trades/s vs the
1.26 s gaps that crippled `ref_lag`)**, and settlement, on the **current**
`LS-LRRR-XBTUSD` regime. Answering it properly needs on the order of
**several hundred windows with strike** to get the clustered se near 0.03,
i.e. roughly **2–4 days** of continuous collection, not the ~0.3 days now held.

That would be a **properly-powered first test**, not an OOS read of a frozen
rule — the frozen rule has already failed on the arms that could be measured.
Reading the sealed cohort now would waste it on an underpowered question.

**My recommendation:** keep the collector running if the BTC-state question
still interests you, and treat the accumulated data as the DEV cohort of a
new, adequately-powered test rather than as OOS for this one. If it does not,
stop the collector and this lane closes at gate **A**.

## 6. PRESERVED

- Prior conclusions at `115de70b` and the `300/1,390` resolution: untouched.
- Baseline findings at `51fe6efd`: untouched; this document only adds.
- All reported cells include the losing and non-replicating ones.
- Invariants (29 checks) still pass; taker→taker remains a code-enforced
  negative control.
- No parameter was selected by P&L; the DEV tertile cut points were reused
  verbatim on VALIDATION.
