# Pre-fill toxicity decomposition — is adverse selection avoidable before a fill?

2026-10-02. No orders. No threshold optimization. Prospective collectors running.

## 1. The fix, the test, and the rerun

Only the confirmed double-count was changed. `simulate()` closes residual at
the opposite touch — which already pays the crossing — and then subtracted
another half-spread. `taker_cleanup_cost` is now a **diagnostic that is never
netted**; nothing else was touched.

`tests/test_gemini_twosided_cleanup.py` — 3 tests, all passing — pins the
invariant and two fill-discipline rules that must not regress:

- closing a long at the bid yields **zero** net on a flat round trip, not −$0.25;
- a trade **smaller than queue-ahead fills nothing**;
- an **unknown-side trade fills nothing** and is counted.

Rerun on the identical historical sample. Pre-fix output preserved at
`analysis/gemini/TWOSIDED_SHADOW.PREFIX.json`.

| | pre-fix | post-fix |
|---|---|---|
| defensible fills (rows) | 300/1390 | **300/1390 — unchanged** |
| net before compensation | −$284.18 | **−$99.19** |
| modeled rebate | +$17.92 | +$17.92 |
| net after modeled comp | −$266.26 | **−$81.27** |
| net per matched share | −$0.3157 | **−$0.0964** |
| positive windows | 31/98 | **57/98** |

Fill counts are identical, confirming the fix touched accounting only.

## 2. Central metric — cost of producing $1 of maker compensation

    modeled maker compensation      $17.92
    net trading cost before comp    $99.19
    ------------------------------------------
    compensation / cost ratio       0.1807
    COST PER $1 OF COMPENSATION     $5.54

**It costs $5.54 of trading loss to earn $1 of modeled maker rebate.** For
reference the Gemini program pays ~1.029% of maker notional, so this says
adverse selection on that notional runs roughly 5.5× the rebate rate.

## 3. Overall adverse selection is weaker than the per-window table implied

Over 75 instrumented fills (q=25, T−90), per contract:

| horizon | mean | SE | sigma |
|---|---|---|---|
| 1s | −0.0101 | 0.0108 | 0.9 |
| 5s | **−0.0261** | 0.0124 | **2.1** |
| 15s | −0.0045 | 0.0121 | 0.4 |

Only the 5-second horizon is significant, and marginally. **The honest
statement is that mean adverse selection is small and only clearly present at
5s** — my earlier per-size table read as a stronger claim than the markouts
support.

## 4. Pre-fill buckets

Every key is computed from state **strictly before** the fill; the markout is
the outcome and never feeds a key. **36 buckets × 3 horizons = 108 cells**, so
individual 2–3σ results are expected by chance and none is treated as
established.

### The strongest discriminator is the synchronized underlying move

| underlying 300s return | n | mo1s | mo5s |
|---|---|---|---|
| < −10 bp | 5 | +0.0060 ± 0.0186 | −0.0060 ± 0.0150 |
| **−10 to 0 bp** | **32** | **−0.0413 ± 0.0135 (3.1σ)** | **−0.0706 ± 0.0197 (3.6σ)** |
| 0 to +10 bp | 37 | +0.0161 ± 0.0173 | +0.0117 ± 0.0152 |
| ≥ +10 bp | 1 | — | — |

Fills taken while BTC had drifted *mildly down* over the prior five minutes are
sharply toxic; fills taken on a mildly rising tape are not. This is the one
feature that is both pre-observable and strongly separating, and it exists
only because the Coinbase 1-minute candles already in the repo cover this
period — otherwise it would have been UNKNOWN.

### Other buckets, in order of apparent separation

| feature | toxic side | benign side |
|---|---|---|
| net trade flow 30s | **[0,100): −0.0915 ± 0.0320 (2.9σ)** | ≥100: +0.0183 ± 0.0181 |
| contract mid move 30s | **< −0.02: −0.0356 ± 0.0162 (2.2σ)** | ≥ +0.02: +0.0033 ± 0.0221 |
| spread | < 0.02: −0.0900 (n=2) | **≥ 0.20: +0.0288 ± 0.0226, n=27** |
| inventory before | |inv| 10–25: −0.0725 ± 0.0316 | ≥25: +0.0128 ± 0.0186 |
| inventory-reducing | **True: −0.0455 ± 0.0263 (1.7σ)** | False: −0.0037 ± 0.0118 |
| first fill in window | True: −0.0177 ± 0.0199 | False: −0.0052 ± 0.0126 |
| side | buy −0.0123 ± 0.0118 | sell −0.0063 ± 0.0215 |
| queue ahead | <1: −0.0142 ± 0.0113 | 100–500: +0.0362 ± 0.0442 |
| time to expiry | ≥240s: −0.0172 ± 0.0094 | <120s: +0.0341 ± 0.0343 |

Two of these are worth flagging as *mechanically* coherent rather than merely
statistical: **wide spreads are the benign bucket, not the toxic one** (the
opposite of my earlier suspicion that a 13¢ book implies informed crossers),
and **inventory-reducing fills are more toxic than inventory-increasing ones** —
you get the fill that flattens you precisely when the market is about to keep
going against the position you were trying to shed.

## 5. Answer to the objective

**Adverse selection here is state-dependent, not uniform — but not yet
demonstrably avoidable.** Three pre-observable features (underlying drift,
recent flow, recent contract move) separate toxic from benign fills at 2–3.6σ
on 75 fills across 108 cells. That is suggestive and mechanically sensible; it
is not an established edge, and the sample cannot support one. No thresholds
were fitted and none is proposed.

What would settle it: the same decomposition on an out-of-sample tape, with the
buckets fixed in advance. The prospective collectors are accumulating it.

## 6. The 65% exclusion — characterized, nothing imputed

182 of 280 BTC rows are unscorable. The exact reason for every one:

| reason | n |
|---|---|
| **NO_DEPTH_TO_CLOSE and residual remains** — at withdrawal the opposite touch had insufficient size, so closure cannot be proven | **168** |
| residual remains after a partial close | 14 |

No settlement or closure is imputed for any of them.

Pre-existing observables, included vs excluded:

| | included (98) | excluded (182) |
|---|---|---|
| **one-sided (a leg never filled)** | **54 (55%)** | **141 (77%)** |
| median buy qty | 5.00 | 3.88 |
| median sell qty | 10.00 | 9.00 |
| median matched qty | **0.00** | **0.00** |
| median venue volume in window | 680.20 | 832.47 |

**Exclusion is driven by one-sidedness, not by illiquidity** — excluded windows
actually traded *more* venue volume (832 vs 680). The selection is therefore
toward windows where a second leg happened to trade, which biases the included
sample **optimistically** on spread capture.

And the line that matters most: **median matched quantity is 0.00 in BOTH
groups.** The two-sided round trip is the exception, not the mechanism. Even
among "interpretable" rows, 55% never filled both legs, so the +$132.57 of
captured spread comes from a minority of windows while the one-sided
directional exposure is carried by all of them.

That, rather than the markouts, is the structural problem with the hypothesis
on this sample: continuous two-sided quoting mostly produced **one-sided
inventory**, which is the same failure mode as the 88 historical all-BUY fills,
arrived at from the opposite direction.
