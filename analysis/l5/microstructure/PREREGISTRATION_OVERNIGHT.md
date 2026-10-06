# Preregistration — overnight BTC15m / PMUS translation study

**Written and committed BEFORE any new result is computed.** Everything below
is fixed at this commit. Completed work stays frozen: `FROZEN.md`,
`PREREGISTRATION_NEXTACTION.md` + `NEXTACTION_FINDINGS.md`, and
`PREREGISTRATION_OPENING15.md` + `OPENING15_FINDINGS.md` are **inputs**, not
things this study may revise.

Research only. No S1 change (`441c375e…b6fe`). No S3. No orders. No change to
authorization or risk controls. Zero capital.

## 0. Populations

| population | role | n |
|---|---|---|
| **BTC15m** | the deployment-relevant horizon | 3,166 fills / 12 windows |
| **BTC5m** | **identification only** | 373 fills / 23 windows |

**No 5m-only relationship may become a PMUS rule.** A rule qualifies for PMUS
consideration only if it is demonstrated **on BTC15m**.

Discovery vs confirmation: the BTC15m discovery window
`btc-updown-15m-1787300100` remains flagged; where an endpoint permits
leave-one-window-out it is used instead, and the fold is the unit.

## 1. Endpoints — fixed now

**E1 OPENING (§1).** Subdivisions `OPENING-A` 0–30 s and `OPENING-B` 30–60 s
**only**. Boundaries are not optimized. The prior 0–60 s result (lift 1.158,
0.530 bits, n=136) is **discovery evidence, not validation**, and
`OPENING15_FINDINGS.md` already reports the preregistered subdivision test;
this study adds the full-L2 window-by-window reconstruction, not a new cut.

**E2 FUTURE INVENTORY STATE (§2) — the critical new endpoint.** At each causal
state T, predict L5 state at **T+10 s, T+30 s, T+60 s**:
UP acquired, DOWN acquired, matched, directional residual, signed imbalance,
and the range of acquisition prices where supported.
Baselines: **persistence / no-change** (state at T carried forward) and a
**frozen-rate** baseline (training-fold median acquisition rate per second
applied forward). Scored by **MAE and median absolute error in shares**, and
by **R² against the persistence baseline** where defined.
**E2 does not replace E1.** Both are reported.

**E3 WITHDRAWAL (§3).** Causal states where L5 could plausibly continue but
does not. Continuation vs apparent withdrawal compared on the preregistered
variables listed in §3 below.

**E4 UNDERLYING BTC (§4).** Proxy-conditioned prediction, see §4.

**E5 LADDER GEOMETRY (§5)**, **E6 CLIP CONSUMPTION (§6)**,
**E7 TRANSACTION TEMPLATES (§7)**, **E8 L5-vs-GENERIC (§8)**,
**E9 80-STABILITY (§9)** — descriptive characterisations with the variables
named in each section; no hypothesis test is claimed for these beyond the
stated comparisons.

## 2. Features — complete list, all strictly causal

Read at or before the decision instant; never after.

Book: both outcomes' full L2 ladder, BBO, spread, depth at touch, total depth,
level count, and touch movement over 5/10/30 s.
L5 state: UP/DOWN acquired, matched, residual, signed imbalance, previous
action and its type, elapsed time since it, TTE, seconds since open.
Clip state: inferred clip identity, fraction of 80 consumed, remaining-to-80,
partial-consumption count.
Transaction class: par-pair count so far, whether the previous action was a
par pair.
Proxy (§4, labelled): BTC spot return/velocity/realized volatility/direction
over 30/60/300 s.

**No feature may be added after a result is observed.**

## 3. §3 withdrawal variables — fixed now

inventory · residual · imbalance · matched · price · TTE · spread · depth ·
short-horizon realized volatility · recent clip consumption fraction ·
elapsed time since last consumption · inferred existing exposed liquidity
(depth at L5's last price). Comparison by Cliff's delta with n, plus
per-window direction agreement.

## 4. §4 — what is and is not available

**The exact settlement index is UNAVAILABLE.** The Zenodo archive carries no
strike, no reference price and no settlement index (its columns are
market/token/outcome, the ladders, `best_bid`/`best_ask`, `crossed`,
`captured_at`, `market_end_at`, `seconds_to_close`), and no local series covers
2026-08-21…24.

A **BTC spot 1-minute series from a different venue** is fetched and is
labelled **`PROXY_NOT_SETTLEMENT_INDEX`** everywhere it appears. It supports
**return, velocity, realized volatility, direction**. It **cannot** support
distance-from-strike, and **no strike-distance feature will be derived**. A
settlement-probability proxy taken from the prediction-market book itself is
**excluded as circular**.

If the fetch fails, §4 is reported as **NOT TESTED — DATA UNAVAILABLE**, and
no substitute is used.

## 5. Minimum sample requirements

* **n ≥ 50** scored decisions for any lift or effect to count as a finding;
* below 50 → reported **UNDERPOWERED**, cannot support advancement;
* per-window results are always shown alongside aggregates, so no aggregate
  rests on one or two windows;
* for E2, **n ≥ 100** state-horizon pairs per horizon.

## 6. Baselines

Persistence / no-change · majority class · training-fold median rate ·
current touch (for price). Lift is reported over the **best** baseline.

## 7. Advancement / rejection criteria — fixed now

| outcome | requirement |
|---|---|
| **E2 counts as a positive result** | future-inventory MAE beats the **best** baseline by **≥20%** at **two of three** horizons, with n ≥ 100 each, and the improvement reproduces in **≥8 of 12** BTC15m windows |
| **E1 counts as validated** | an out-of-sample lift ≥1.10 at n ≥ 50 in a preregistered subdivision — already **failed** in `OPENING15_FINDINGS.md`; this study does not re-test it |
| **a mechanism may be proposed for PMUS** | demonstrated on **BTC15m**, surviving leave-one-window-out or an equivalent out-of-sample check, **and** classified NATIVE or APPROXIMABLE in the §10 matrix |
| **STOP** | no endpoint meets its bar and no mechanism is both BTC15m-demonstrated and PMUS-executable |

## 8. Final recommendation

Exactly one of **KEEP RESEARCHING / BUILD HISTORICAL S3 / BUILD REALTIME
SHADOW S3 / STOP THIS DEPLOYMENT PATH**, with the evidence that forces it.
**S3 will not be recommended merely because it is the next named stage**; it
requires a mechanism meeting the §7 PMUS bar.

## 9. Reporting discipline

Null and failed results are reported with the same prominence as positive
ones. Descriptive follow-ups are labelled **DESCRIPTIVE (not preregistered as
a test)** wherever they appear. Raw artifacts, provenance, preregistration
commit IDs and fingerprints are preserved and pushed.
