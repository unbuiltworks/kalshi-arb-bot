# P8 — What actually drives the edge, and two dataset defects that change P7

> ## PROVISIONAL — pending P10 decoder validation (2026-09-22)
>
> Every result in this document that depends on assigning a trade to a SIDE is
> **provisional**. P9-D found that a contract's `/v1/trades` feed shows 20-56%
> exact mirrors among same-second consecutive prints, which a single price
> series cannot produce, so the mapping "a sell print at p made YES buyable at
> p; a buy print at b made NO buyable at 1-b" is not established.
>
> The venue's own documentation defines `type` as the aggressor side -- "buy
> means that an ask was removed from the book by an incoming buy order" -- but
> that is the SPOT trade schema, and the prediction-markets order API carries a
> second dimension (`outcome`: yes/no) that the trades feed does not report. A
> contract's YES and NO quotes are exact mirrors of one another
> (`buy.yes + sell.no = 1`), so a NO-side execution and a YES-side execution
> are indistinguishable on price alone.
>
> Affected: complement availability, pair cost, matched and residual P&L,
> maker-fill simulations, markout, and every arm table. Unaffected: prints per
> minute, dollars per minute, contract lifetimes, trading-day counts, quoted
> spreads, and the settlement outcomes themselves.
>
> **No claim here may be treated as conservative, safe, or directional in its
> bias until P10 establishes the decoder.** An earlier note in this programme
> argued a decoding error would only flatter a positive result; that argument
> is withdrawn as unproven.



**Status: OFFLINE ONLY. No live orders, nothing armed, rebates still exactly $0.**

---

## 1. Two defects in the P7 dataset, found and corrected

### 1a. The "1h" lane was 18% contaminated

`bulk_pull` classified duration from the TICKER shape (`BTC{ymd}{hh}00`), but
that shape is shared by multi-day strike markets. Of 1,854 contracts labelled
1h, **103 were not hourly at all** (median life 60 min, max **10,770 min** =
7.5 days), contributing **3,216 of 17,414 prints — 18% of the lane**.

Corrected by classifying on ACTUAL life (`effectiveDate` -> `expiryDate`)
exactly as P6 did. 5m and 15m were clean: 100% of their contracts had the
right life.

### 1b. Mirrored twins — the same trade reported on both sides

The bulk `/v1/trades?limit_trades=500` feed returns, for some matches, **both
the `p` and the `1-p` row at the same second**, usually with identical
quantity:

```
GEMI-BTC15M2609172330-UP     t-33s  buy @ 0.96      t-33s  buy @ 0.04
                             t-32s  buy @ 0.96      t-32s  buy @ 0.04
```

**11.4% of prints have a same-second complement at `1-p`, and 66% of those
share the quantity.** Treating them as two independent buyable units
manufactures a complement out of the data format. Deduplicated by keeping the
row that continues the contract's own price series (1,291 twins dropped).

> **Precision note added 2026-09-22 (P8-C).** Stated as a share of rows, the
> 1,291 dropped echoes are **5.2%** of the 24,670 rows on studied contracts;
> ~10.5% of rows *participate* in a twin pair and half of each pair is the
> echo. The 11.4% figure above is the participating-row count, not the removed
> count. The dataset itself is unchanged — the canonical
> `tape_normalize` module reproduces this correction print for print.

**Notably COHORT-2's per-cycle tape shows 0% of this** — it is specific to the
bulk endpoint, which is why the earlier 56-fill calibration did not catch it.

---

## 2. Corrected arms — net P&L, pre-rebate

capture 1.00 / 0.50 / 0.33 / 0.25. **OOS is 09-21..22 and is now
PREVIOUSLY EXPOSED, not pristine.**

| arm | dev | val | **OOS (exposed)** |
|---|---|---|---|
| 1. frozen L5 | −75.01 / −51.26 / −27.21 / −34.54 | −20.60 / −12.20 / −9.38 / −8.99 | −3.33 / +5.79 / −6.55 / +2.68 |
| **2. BTC5m EV** | +17.24 / +3.51 / +8.19 / +3.92 | +0.67 / +3.05 / +0.55 / **−1.13** | **+12.73 / +13.20 / +5.32 / +4.97** |
| 3. BTC15m EV | +3.94 / +5.11 / +11.78 / −0.53 | +6.89 / +6.10 / +5.05 / +5.02 | **−4.59 / −0.66 / −4.92 / −2.78** |
| 4. BTC1h nearest-money | −3.78 / −5.97 / −8.99 / −0.57 | −7.86 / −5.44 / −3.24 / +0.61 | +1.37 / +1.50 / +0.59 / +1.22 |
| 5. combined | +17.40 / +2.65 / +10.98 / +2.82 | **−0.30** / +3.71 / +2.36 / +4.50 | +9.51 / +14.04 / +0.99 / +3.41 |

**The corrections cut the headline numbers roughly in half** (combined dev
+39.68 -> +17.40) and flipped combined validation at capture 1.00 (+1.99 ->
−0.30). The qualitative conclusions survive:

- **BTC5m EV** is positive in 11 of 12 cells and remains the strongest single
  candidate.
- **BTC15m EV is negative in all four OOS cells** — the rejection stands.
- **BTC1h is positive in all four OOS cells but negative across dev and val.**
  A lane that only works on the exposed holdout is not a signal; treat it as
  noise, not a component candidate, until the forward set says otherwise.
- **frozen L5** remains negative almost everywhere.

---

## 3. (A) The edge is pair-cost arithmetic, not the probability model

Every EV decision on dev was reconstructed with its inputs (9,614 decisions).

**What the rule actually does — it buys CHEAP and refuses DEAR:**

| decision | n | accepted | accepted median unit | rejected median unit |
|---|---|---|---|---|
| OPEN | 2,952 | 26.9% | **0.320** | 0.600 |
| EXTEND | 5,384 | 52.1% | **0.260** | 0.630 |
| COMPLETE | 1,278 | 90.6% | pair cost **0.980** | pair cost **1.242** |

**Simple filters reproduce it almost entirely:**

| rule | agreement with the EV policy |
|---|---|
| acquisition: projected pair (unit + q̂) ≤ 0.95 | **89.9%** |
| acquisition: sign of (1 − p − q̂) alone | **89.4%** |
| completion: pair cost ≤ 1.20 | **96.0%** |
| acquisition: any flat unit-price threshold | ≤ 43% (worse than "always accept") |

**So the mechanism is a pair-cost filter, and it is calibration-free.** The
`p_win` term — the part built on the falsified L5 prior — changes roughly 10%
of acquisitions and 4% of completions. That is why the arms were positive
despite the prior being wrong: **the profit does not come from the probability
model, it comes from only buying legs that project into a cheap pair.**

This also means a flat unit-price threshold cannot substitute for it: the rule
is an *interaction* between the offered price and the expected complement
price, which varies per market.

---

## 4. (B) The Gemini calibration is NOT shipped, and why

A dev-only empirical curve was fitted (20 buckets, 5c wide, already monotone
without isotonic adjustment). But it does not survive its own sanity check.

**A leg priced above $0.90 with under 60 seconds to expiry should win almost
always. It wins 82.3%** (n=96, after both corrections above):

| TTE | <0.35 | 0.35-0.65 | 0.65-0.90 | >0.90 |
|---|---|---|---|---|
| >900s | 18.1% | 47.4% | 76.4% | 90.3% |
| 300-900s | 26.0% | 46.8% | 69.3% | 90.9% |
| 60-300s | 24.5% | 44.5% | 68.1% | 82.5% |
| **<60s** | 14.0% | 45.3% | 69.4% | **82.3%** |

Inspecting cases directly, some are genuine: `GEMI-BTC15M2609172330-UP` traded
0.96 with 33 seconds left and settled at 76,428.67 against a strike of
76,435.38 — **$6.71 apart, 0.009%**. A stale 0.96 quote on a genuine coin flip.
So part of this is real thin-market inefficiency rather than a data error.

But I could not separate "real inefficiency" from "residual artifact", and
**a probability model whose extreme buckets cannot be validated should not
become the production model.** Combined with section 3 — where `p_win` moves
only ~10% of decisions anyway — recalibrating is both low-value and currently
unsafe. **Status: UNRESOLVED, not shipped.**

---

## 5. (F) The forward test is now collecting

`analysis/gemini/p8_forward_collect.py` + routine
`trig_01M73pfKyf7Ng5qW3gXZJBeh`, daily at 01:20Z. It writes
`analysis/gemini/forward/<day>.json` from public endpoints only.

**It refuses any day at or before 2026-09-22** (`LAST_EXPOSED`), so
already-inspected data cannot silently enter the holdout — verified by running
it against 09-22 and getting a refusal. The routine's prompt forbids analysing
or scoring the forward set.

**09-21..22 is labelled PREVIOUSLY EXPOSED from here on.** It has been
inspected in P7 and again in this round.

---

## 6. What I did not do, and why

- **(C) WAIT was not built.** It depends on a complement-availability estimate
  drawn from the same tape whose duplication defect I just corrected, and the
  P7 WAIT diagnostic (39-65% cheaper later) was computed on the *contaminated*
  data. That diagnostic must be recomputed on the corrected dataset before a
  rule is built on it; building now would encode the artifact.
- **(B) the calibrated model was not shipped** — section 4.
- **(E) the five-arm comparison including the new variants** is partially done:
  arms 1, 3, 5 in the owner's list (imported-L5 EV, and the duration
  selector) are in section 2. Arm 2 (simple extracted filters) is
  characterised in section 3 but not yet run end-to-end as a P&L arm, and
  arm 4 (WAIT + Gemini EV) is blocked on C.

The honest position: **this round was mostly error-correction, and it found
enough to invalidate part of P7.** Two defects changed the headline numbers by
roughly half. The decomposition in section 3 is the durable result — it says
the strategy is a pair-cost filter, which is a much simpler and more testable
object than the EV machinery built around it.

**No rebate term. No live orders. Nothing armed.**
