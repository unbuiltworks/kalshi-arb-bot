# Data-quality note — crossed books in the Zenodo historical L2 archive

**Permanent provenance record.** Raw measurements in `CROSSED_BOOKS.json`,
produced by `analysis/l5/data_quality/crossed_books.py`.

**No frozen study is retroactively changed by this note.** Every completed
result stands as published, at its own commit, with its own protocol. This
document records the defect and inventories which findings are exposed to
it.

## The defect

`best_ask < best_bid` on a single token within a single second — impossible
in a real order book. Wherever it appears, the archive's **ask side** is
wrong at that instant.

**Cause (hypothesis):** the event-driven delta capture dropped an ask-side
update while the bid moved past it, leaving a stale low ask. The bid side
shows no inversion by construction, and the defect is **ask-side only**.

**Discovered** 2026-10-06 during the running pair-cost confirmation run
(`PAIRCOST_FINDINGS.md` @ `1498a293`), where it surfaced as an
arithmetically impossible `ask_sum < bid_sum` in two windows. It was not
detected by any earlier study, including the L2 resolution audit, because
no earlier study compared an ask-derived quantity against a bid-derived one
within the same second.

## Extent

| | BTC15m | BTC5m |
| --- | --- | --- |
| windows | 12 | 23 |
| windows with any crossing | **10 / 12** | **18 / 23** |
| pooled crossed seconds | 777 / 9,607 | 1,261 / 4,870 |
| **pooled crossed share** | **8.09 %** | **25.89 %** |
| inversion (bid − ask) median | 0.03 | **0.18** |
| inversion p90 / max | 0.33 / 0.39 | **0.63 / 0.86** |

**BTC5m is severely affected.** Worst windows:

| BTC15m window | crossed | worst example |
| --- | --- | --- |
| …307300 | **32.7 %** | up bid 0.750 / ask 0.570 |
| …311800 | **28.9 %** | down bid 0.950 / ask 0.560 |
| …290200 | 11.4 % | down bid 0.450 / ask 0.150 |

| BTC5m window | crossed | worst example |
| --- | --- | --- |
| …349900 | **85.6 %** | up bid 0.999 / ask 0.340 |
| …283600 | **77.5 %** | down bid 0.520 / ask 0.040 |
| …312400 | **74.4 %** | up bid 0.600 / ask 0.001 |

A 5m window with 85.6 % of its seconds crossed has effectively **no usable
ask side at all**.

## Direction of the bias

The stale quote is a **low ask**. So wherever asks are used:

- **ask levels are understated**, therefore
- **spreads are understated**, **mid prices are biased low**, and
- **anything that looks cheaper when the ask is lower looks more
  favourable than reality.**

This direction matters for the inventory below: for several findings the
bias runs *against* the published conclusion, which strengthens rather than
threatens it.

## Impact inventory — bid-derived vs ask-derived

### Not exposed (bid-, fill-price- or share-derived only)

| finding | why unexposed |
| --- | --- |
| **80-share clip lattice**, 100-share ceiling, clip exhaustion (113/613 summing to 80.00) | share quantities only |
| **Ladder geometry §5** — offsets below touch, level spacing, 3+ simultaneous levels | `best_bid` only |
| **§3 withdrawal** — TTE −0.82, inventory +0.74, clip exhaustion +0.48 | clock/inventory; the one ask-derived covariate (`spread`) was **negligible at −0.08** and carries nothing |
| **§7 transaction templates**, collapse ratios 1.10 / 2.83, maker/taker mix | chain data only |
| **§1 opening timing** (6–10 s), **§8 generic-vs-specific** percentiles | block timestamps and all-trader prints |
| **Allocation terminal finding** — w_up 0.481 (IQR 0.036), matched fraction 0.955 | **share** allocation, not prices |
| **Completion T1** — EXTEND 0.37 vs COMPRESS 0.61, delta −0.387 | **actual fill prices** |
| **Completion T2 / T3** — FIFO pair costs, ceiling tests | actual fill prices |
| **Pair-cost maker view**, F6 cumulative-vs-marginal, CF5 permutation | `best_bid` only |
| **Window selection** (`select_window.py`) | gates on **row presence**, not ask validity — the cohort would be identical |
| S1/S2 next-fill and inventory nulls (lift 1.001, E2 False) | bid/fill-based features |

### Exposed, with the bias direction

| finding | exposure | assessment |
| --- | --- | --- |
| **Mint opportunity ceiling ≤27 % of the fill gap** (S2 §3) | uses **both tokens' asks** to test whether price + complement ask ≤ \$1.00 | **Bias favours the published conclusion.** A stale-low ask makes mint look *more* feasible, so the true ceiling is **≤27 %, possibly lower**. The conclusion "mint access needs a pricing change, not just representation" is **strengthened**. |
| **Pair-cost taker view** (9/12 pass) | time-weighted **ask** sum | Flagged in `PAIRCOST_FINDINGS.md` §5 at the time. Bias understates ask sums → understates the taker edge → **cannot have manufactured the pass**. The REFUTED verdict rests on the maker view, F6 and CF5, none of which use asks. |
| **Allocation A1 / A4** — probability bins, split-law targets | `p_up_mid = (bid+ask)/2` | Mid is biased low where crossed. **All four allocation bars FAILED**, so no published positive claim depends on it. The surviving terminal finding is share-based. |
| **Completion `marginal_eventual_pair_cost`** | `walk_ask` on the complement ladder | That variable **failed its bar** (delta −0.056). No conclusion rests on it. |
| **`events.py` ask features** — `ticks_below_ask`, `comp_ask`, `own_spread` | recorded covariates | Present in the event records; none is load-bearing for a reported microstructure rule. Any future use of these columns must read this note first. |
| **All BTC5m ask-derived quantities** | 25.89 % crossed, median inversion 0.18 | **Treat every BTC5m ask-derived number as unreliable.** BTC5m was identification-only and barred from PMUS rules, which limits the damage, but no 5m ask quantity should be cited. |

## Standing rules going forward

1. **Any new study using asks must report its crossed-book exposure** —
   per-window share and the bias direction — alongside its result.
2. **Prefer bid-derived and fill-price-derived quantities.** For a maker
   strategy the bid side is both the honest comparison and the clean data.
3. **Do not cite any BTC5m ask-derived figure.**
4. **Two views of the same quantity must share common support.** The
   defect surfaced only because a bid-derived and an ask-derived mean were
   compared; computing each over its own available subsample hid it. This
   is now a standing requirement.
5. **Do not repair the archive in place.** It is checksum-verified source
   data. Any filtering belongs in the consuming study, declared in its
   protocol.

## What this does not change

No frozen verdict moves. Specifically: RUNNING PAIR-COST CONTROL remains
**REFUTED**; the allocation bars remain failed with a share-based terminal
finding; completion remains T1-pass / T2-fail / T3-fail; and the
mint-opportunity conclusion is strengthened. S3 is unaffected — it consumes
**live PMUS** data, not this archive.
