# What this archive cannot reveal

Stated first, because every rule in `MICROSTRUCTURE_FINDINGS.md` is bounded by
it. Nothing below is a defect in the reconstruction; these are limits of public
historical data.

## 1. Unfilled and cancelled orders are invisible in principle

The archive records **executions** (from the chain) and **anonymous depth**
(from the L2 capture). It never records:

* an L5 order that was placed and **never filled**;
* an L5 order that was **cancelled**;
* **which** resting size at a price was his;
* his **queue position** at any level.

So every statement about "where he placed liquidity" is an inference from
where he was *executed*, and is therefore a **lower bound on the levels he
exposed and an unknown fraction of the size he exposed**. The latent-ladder
estimate (Q4) is explicitly a minimum.

**Absence of size at a price is not proof he had no order there**, and
presence of size at a price is not proof he did. `size_at_*` fields are depth
at price levels and are never attributed to anyone.

## 2. Quantity is the counterparty's take, not his order size

Observed fill quantities are highly fragmented — **1,173 distinct values among
2,768 BTC15m confirmation fills**, only 45% integers, 17% round lots. Same-block
same-price groups sum to 180, 160, 157, 152.29 — not clean lots. The maximum
observed anywhere is exactly **100.0** (8 fills), and the opening study shows a
second cluster at **75–80**.

This means **what the data calls "quantity" is the size of the counterparty's
aggression against his resting interest, not the size he exposed.** That alone
explains why Q2 finds quantity nearly unpredictable, and it is why no rule
below claims to predict order size.

## 3. Timestamp precision is one second, and L5's timing is block-quantised

* The L2 column is `timestamp[us]` but **every microsecond is zero**, on both
  archives, and the GCD of inter-observation gaps is 1 s. Whole seconds is the
  archive's native resolution — not a bucketing choice.
* L5's fills are timed by **block timestamps**. Polygon blocks are ~2 s apart,
  so every fill in a block shares one second and the true instant lies
  somewhere inside that block's interval. Each event row carries `block`,
  `l5_fills_sharing_this_block` and the measured intervals to the neighbouring
  blocks so the ambiguity is visible.
* `log_index` gives a deterministic **order** within a block but no finer
  **time**. Fills in one block are therefore ordered but not separately timed.
* **No sub-second state was manufactured anywhere.** Where the archive has no
  observation for a second, the event stream has no event there.

## 4. Book staleness at the moment of a fill

A fill is matched to the most recent observation at or before it. Because the
capture is event-driven, an old row is the *current* state rather than stale
data — but it is carried at most **60 s**, the capture's own REST resync
interval, beyond which the row is refused rather than trusted. Every event row
carries `obs_age_s_own` and `obs_age_s_comp`.

The feed runs ~24k events/s with **no sequence numbers**, so a dropped delta is
undetectable from the stream. Every offset here carries up to one resync
interval of possible drift. This is the dataset's own stated bound.

## 5. Coverage gaps, reported rather than patched

| | BTC15m | BTC5m |
|---|---|---|
| L5 fills reconstructed | 3,166 / 3,166 | 373 / 373 |
| chain chunk holes | **0** | **0** |
| fills with no block timestamp | **0** | **0** |
| `UNRESOLVED_NO_BOOK` (no book within the carry bound) | 15 (0.5%) | **64 (17.2%)** |
| L2 gaps > 60 s | 8 of 37,914 | 22 of 25,419 |
| worst gap | 226 s | 223 s |

The BTC5m `UNRESOLVED_NO_BOOK` rate is **34× the BTC15m rate** and is a real
limitation on the 5m conclusions: in 17% of 5m fills there is no book inside
the carry bound to classify against, so those fills carry no mechanism.

## 6. Provenance is not equally strong on the two archives

* `updown_15m.parquet` — sha256 `d86191506e…`, matching a value recorded in
  this repository **before any acquisition workflow existed**.
* `updown_5m.parquet` — **no prior hash existed.** Verified *structurally*
  against figures prior analysis recorded (167,941 rows, 471 markets,
  captured_at range) — all matched exactly — and the sha256 obtained
  (`166822b2…`) is now recorded for future runs. **A structural match is
  weaker than a cryptographic one.**

## 7. What "mechanism" does and does not assert

`MINT_CONSISTENT` means a complete-set mint **could** have funded the fill at
the complement's posted bid (p + best_bid(complement) ≥ 1.00). It does **not**
assert that is what happened, and no order in this dataset is attributed to any
trader. `RESTING_BID_CONSISTENT` means only that the price was not above the
prevailing touch — it does not prove an order rested there.

## 8. Sample sizes and where they bite

* BTC5m has **373 fills across 23 windows** against BTC15m's 3,166 across 12.
  Most 5m windows carry fewer than 20 fills per mechanism, so the per-window
  reproducibility test is **underpowered on 5m** and its "1/1" and "1/2"
  agreement counts should not be read as confirmation.
* The 15m control sample is small (1,657 observations) for a substantive
  reason: L5 is so continuously active that few seconds in those windows are
  5 s clear of one of his fills.
* The opening study rests on 12 (15m) and 9 (5m) first-fill observations. The
  timing result is tight enough to survive that; the **quantity** results at
  the open are not, and are labelled accordingly.

## 9. Not covered at all

Settlement cash appears only as a labelled diagnostic. Fees, gas, rebates,
his capital base, his other markets, his orders on venues outside this
archive, and any off-chain intent are all outside what this data can see.
