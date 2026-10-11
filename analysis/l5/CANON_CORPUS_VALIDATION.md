# Section 1 — L5 CORPUS VALIDATION

Artifacts: `scripts/l5_corpus_validation.py`,
`analysis/l5/CANON_CORPUS_VALIDATION.json`. Read-only, no network.
Nothing below is realized profit for our account — this is another
trader's public activity.

## The identity / provenance rule, stated explicitly

> **A record is admitted iff the envelope's `params.user` is L5's address
> AND the record carries no CONTRADICTING `name`/`proxyWallet`. The `name`
> field is NEVER used as a positive filter.**

This is required, not stylistic: the `/activity` pages omit the profile
block entirely, so `x["name"] == "l5Zn1bWoM8eTsK"` discards **706,500 of
784,500 records (90%)**. That is the defect this rebuild exists to fix.

Provenance is clean: **all 1,570 envelopes carry `params.user` =
L5's address**, with no mismatches.

| file | envelopes | records | `name` present |
|---|---|---|---|
| `activity.jsonl` | 157 (156 × status 200, 1 × status 400) | 78,000 | **yes, all 78,000 match L5** |
| `activity_gap.jsonl.gz` | 1,413 (all status 200) | 706,500 | **absent on all 706,500** |

**Contradicting names: 0.** No record was disqualified on identity.

### Corroboration, and its honest limit

Because `activity.jsonl` *does* carry names and overlaps the nameless file
in time, the two can be intersected on `transactionHash` to test identity
against an independent signal rather than trusting provenance alone:

* transactions in the named file: 70,410
* transactions in the nameless file: 676,750
* **shared: 3,175**

So corroboration exists but is **thin — about 0.5% of the nameless
transactions**. On the shared set an independent identity signal confirms
L5; for the remaining 99.5%, **provenance is the only evidence**. That is
a real limitation and is stated rather than glossed.

## THE PRESCRIBED DEDUPE KEY IS DEFECTIVE — measured, then replaced

`(transactionHash, asset, timestamp)` cannot work here, because **the
`/activity` pages carry no `asset` field at all** (only the 78,000 named
records have one). It therefore degenerates to `(txh, None, timestamp)`
for 90% of the corpus. Measured over all 784,500 admitted records:

| key | unique | exact dupes | **collisions with DIFFERING fields** |
|---|---|---|---|
| `(txh, asset, ts)` — as prescribed | 747,160 | 4,283 | **33,057** |
| `(txh, ts)` | 743,985 | 7,458 | 33,057 |
| `(txh, conditionId, outcomeIndex, ts)` | 744,442 | 7,520 | 32,538 |
| **`(txh, cid, outcomeIndex, ts, price, size, type)`** | **776,749** | 7,751 | **0** |

A "collision with differing fields" is silent data loss: two records whose
price/size/side/outcome/type differ, collapsed into one.

**Redemptions are worst hit.** A single `transactionHash` carries many
distinct `REDEEM` records with an empty `asset` and an identical
timestamp but different sizes — e.g. one hash bearing 295.81, 207.47,
197.95 and 194.21. Under the prescribed key only the first survives, so
**redemption/settlement cashflow would be materially understated** —
precisely the quantity the incentive question turns on.

**The full key is used throughout and has zero collisions.**

## Corrected corpus scale

| quantity | value |
|---|---|
| admitted records | 784,500 |
| unique after dedupe | **776,749** |
| exact duplicates dropped | 7,751 |
| **TRADE fills** | **760,817** |
| **distinct TRADE event slugs** | **17,003** |
| REDEEM | 15,971 |
| MAKER_REBATE / TAKER_REBATE / REWARD | **111 / 73 / 20** |

This supersedes the previously circulated "754,234 fills / 17,855 events",
which was computed on the defective key.

## Span — these files do NOT reach September

**2026-03-19 10:09:37Z → 2026-08-31 23:17:16Z.**

The earlier claim of coverage "through 2026-09-25" conflated this
canonical corpus with a separate September harvest under
`data/l5/raw/sep/`. **Any September conclusion must be attributed to that
harvest, not to this corpus**, and it remains thin (~2.6 days, not from
the canonical collector, with verified holes).

## Field-level validation

| check | result |
|---|---|
| price outside [0,1] or unparseable (TRADE) | **0** |
| quantity ≤ 0 or unparseable (TRADE) | **0** |
| timestamp invalid | **0** |
| market id shape | 746,956 × 66-char hex `0x…`; **204 NON_HEX** |
| side classification | **BUY 731,658 / SELL 2 / empty 15,932** |
| missing `side` | 15,932 (REDEEM-type records, not fills) |
| missing `outcome` | 204 |
| missing `conditionId` | 203 |
| missing `eventSlug` | 3,448 |
| window duration | 15m 671,572 / 5m 72,287 / **UNRESOLVED 3,448** |
| asset | BTC 725,729 / ETH 18,130 / **UNRESOLVED 3,448** |
| malformed envelopes | 1 (`payload` not a list, the status-400 row) |

**Nothing is inferred.** Every unresolved record is counted and attributed
to the field it lacks; no missing field is imputed anywhere.

### What the field validation establishes

* **L5 is buy-only on the full corpus**: 2 SELLs in 760,817 fills. This
  does **not** contradict the documented ~9s "side switch" mechanic, which
  is L5 alternating *which outcome it buys* (i.e. COMPLETE). Those are
  distinct claims and both hold.
* **15m dominates 5m** 671,572 to 72,287 across the whole corpus.
* **ETH is a real but minor share** (18,130 of 743,859 resolved).
* The 3,448 slug-less records are a **single failure mode** (empty
  `eventSlug`), so duration and asset are unresolvable for exactly that
  set and no other.

## Caveats carried forward

1. Identity for 99.5% of records rests on provenance alone; cross-file
   corroboration covers 3,175 transactions.
2. The rebate/reward populations are **tiny — 111 + 73 + 20 records**. Any
   per-contract incentive conclusion drawn from them is thin evidence and
   must say so.
3. These files end 2026-08-31. September is a separate, thinner source.
