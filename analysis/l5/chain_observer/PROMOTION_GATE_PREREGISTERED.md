# Chain-observer promotion gate — PREREGISTERED 2026-09-15T15:4xZ

**Written before the observations exist.** Every threshold below is a number,
not an adjective, and it is fixed as of this commit. Choosing a threshold after
seeing the distribution is the failure this document exists to prevent, so a
later change to any number here must be a new commit that says what it changed
and why — never a quiet edit.

At the time of writing: **11 settled-window passes, 10 clean**, and the joined
transaction count under the current (post-block-boundary-fix) code is **well
under 500**. Nothing below is currently satisfied.

**Scope of promotion, if it passes:** the chain becomes the **shadow
observation clock** and nothing else. It does not enable live trading, it does
not change FIDELITY v0.2, and it does not alter any risk bound. Promotion is a
change to *when we learn what L5 did* — not to what we do about it.

---

## G-C1 · Sample size

| | |
|---|---|
| metric | joined L5 transactions (present in BOTH chain and feed) counted only from passes run by code at or after commit `47cb38e0` (the block-boundary fix) |
| threshold | **≥ 500** |
| why this number | the boundary bug produced exactly one false `feed_only` in 11 passes; at ~150 joined transactions per pass, 500 spans multiple hours, several L5 activity regimes and at least one quiet period |

Transactions observed under the pre-fix code are **excluded**, because the
window they were compared over was wrong.

### Amendment 2026-09-15 (enforcement mechanism; the bar is raised, not lowered)

"At or after commit `47cb38e0`" was not enforceable: a commit hash cannot be
compared without git, which the deployed image need not carry, so nothing
actually excluded pre-fix rows. It showed: rows reading **240 @ 0.35** for a
fill the venue reported as **80 @ 0.05** — written before chain legs were
grouped by L5's role — sat in the same file as corrected ones and failed G-C4
on superseded code.

The enforceable form is the validator's **`EPOCH`**, stamped on every row it
writes. The gate counts only rows at or above **`QUALIFYING_EPOCH = 3`** and
reports how many it discarded. An unstamped row predates the stamp and is
excluded rather than assumed current. `EPOCH` is bumped whenever a change makes
older rows non-comparable; the commit is still recorded per row, for
provenance.

Epoch 3 is **later** than `47cb38e0`, so this tightens G-C1 rather than
relaxing it: the ≥ 500 sample restarts from the grouped-attribution code. The
threshold of 500 is unchanged.

## G-C2 · No unexplained misses

| | |
|---|---|
| metric | `feed_only` transactions — the venue published a fill the chain did not carry |
| threshold | **0 unexplained** |
| explained means | a named, reproducible cause with evidence, e.g. the tx lies outside the queried block range (boundary), or the log carries no L5 topic at any scanned position. "Probably a blip" is not an explanation |
| why zero | a missed fill is indistinguishable from L5 not trading. One unexplained miss makes every subsequent absence ambiguous |

## G-C3 · Every `chain_only` case classified

| | |
|---|---|
| metric | `chain_only` transactions assigned to a named category |
| threshold | **100 % classified**, 0 unclassified |
| permitted categories | `publication_lag` (the feed published it later — verified by re-checking), `window_edge` (published outside the compared window — see the amendment below), `non_trade_transfer` (an L5-bearing log that is not a fill), `other_market` (outside the cohort), `unresolved` |
| additional threshold | **`unresolved` must be 0** |
| why | `chain_only` is expected and benign *as publication lag*. Any other cause means the chain is reporting something the venue does not, and a faster feed that invents fills is worse than a slow one |

### Amendment 2026-09-15 (categories only; no threshold changed)

Building the classifier exposed a cause the original list did not name. The
block range queried is a **superset** of the time window compared, because the
bounds are floored and ceiled onto block boundaries. A transaction in an edge
block can therefore carry a venue timestamp just outside the window, and it
appears as `chain_only` while being fully published.

`window_edge` is added as a permitted category for exactly that case, and it
requires the same proof as `publication_lag`: the transaction must be **found
in the feed**, with the measured distance outside the window recorded.

**No threshold moved.** 100 % classified and `unresolved` = 0 stand as written.
This amendment names a cause; it does not excuse one. It is recorded here
rather than applied quietly, per the rule at the top of this document.

## G-C4 · Attribution agreement

Joining on `transactionHash` proves the two sources saw the same transaction.
It does **not** prove they agree about what happened in it. For every joined
transaction the chain-derived and feed-reported values must match:

| field | threshold |
|---|---|
| market / condition id | **100 %** exact |
| outcome (Up / Down) | **100 %** exact |
| price | **≥ 99.5 %** exact to 4 dp, and **0 unexplained** mismatches |
| size | **≥ 99.5 %** exact to 6 dp, and **0 unexplained** mismatches |

"Explained" for price/size means a documented representation difference (e.g.
integer-scaled chain units versus the feed's float) with the conversion shown
and applied. An unexplained mismatch of any size fails the gate: if the two
sources disagree about the size of a fill, the inventory the engine would build
is wrong regardless of how early it learned.

**This requires work that does not exist yet:** the validator currently joins
on hash only and decodes no log payloads. Building that decode is part of
meeting the gate.

## G-C5 · Reorg and finality, handled explicitly

| | |
|---|---|
| confirmation depth before a chain fill counts as FINAL | **≥ 64 blocks** (~2 min on Polygon) |
| behaviour under a reorg | a transaction that disappears from the canonical chain below that depth must be **retracted with a marker**, never silently dropped or silently kept |
| test | a simulated reorg (a block range re-scanned to different content) produces a `chain_retraction` record and removes the fill from counted evidence |
| threshold | the test exists and passes; **0 counted transactions below the confirmation depth** |
| why | an unconfirmed chain fill acted on as real is exactly the "faster but wrong" failure. Depth 64 is chosen as a conservative multiple of Polygon's practical reorg depth, not tuned |

## G-C6 · An RPC failure is UNKNOWN, never NO ACTION

| | |
|---|---|
| metric | passes in which an RPC error produced an empty result reported as fact |
| threshold | **0** |
| status | **already implemented and tested** (`failed_pass()`; `chain_fills_checked` returns `(ok, fills)` and never conflates). Two regression tests |
| continuing requirement | `consecutive_errors` reported on every pass; a pass with `ok: false` contributes nothing to G-C1 and carries no agreement figures at all |

## G-C7 · Coverage gaps excluded

| | |
|---|---|
| rule | a transaction counts toward G-C1 only if `coverage.observed("chain_validate", a, b)` is True for the window it fell in |
| threshold | **0 counted transactions from an unobserved interval** |
| why | the supervisor records its own downtime; evidence gathered while we were not watching is not evidence |

## G-C8 · Provenance retained

| | |
|---|---|
| metric | counted transactions retaining raw `transactionHash`, `blockNumber`, `logIndex`, contract address and topic position |
| threshold | **100 %**, durably on the volume |
| why | a promotion argued from aggregates nobody can re-derive is not auditable. Every number in the gate report must be reconstructible from the retained rows |

---

## Decision rule

**Promote only when G-C1 … G-C8 are ALL satisfied simultaneously, in a single
report, over one continuous qualifying sample.** No partial credit, no "close
enough on one", no combining a good attribution run with a different good
coverage run.

On promotion: `observer.PROMOTED` flips to True by a human, and the chain
becomes the shadow observation clock. **Live trading is not enabled as a side
effect and requires its own separate authorization.**

If any gate fails, the report says which, with the number, and the observer
stays unpromoted.
