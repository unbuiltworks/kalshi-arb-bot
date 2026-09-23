# Technical debt — open, deliberately unfixed, with the reason

Items here were found while doing something else, are NOT blocking that work,
and were left alone on purpose. Each says what is wrong, what it would break
today, and what has to be true before anyone touches it. A defect that is
merely written down is not fixed; this file exists so it cannot be quietly
forgotten either.

---

## TD-1 · PMUS sports tick: `TICK = 0.005` vs the venue's `0.01`

**Found** 2026-09-23, closing the crypto tick defect.

**What is wrong.** `src/pmus_q1_executor.TICK` is `D("0.005")` and was applied
unconditionally by `complement_bound`. But `market_micro_spec` in
`src/pmus_micro_executor.py` already documents, from a live incident, that

> PMUS sports serve `orderPriceMinTickSize=0.01` … so a hardcoded equality
> check REFUSED EVERY SPORTS MARKET with `MICRO_TICK_UNSUPPORTED:0.01` — the
> live path could not have placed a sports order at all.

So `0.005` is not the sports grid either. It is not the crypto grid (BTC15m is
`0.01`). It appears to be a constant that matches nothing the venue currently
serves.

**Current state.** The crypto half is closed: `complement_bound` now takes a
`tick`, `tick_for_market()` reads the venue's authoritative field, and
`PMUSAdapter.complement_price` passes through the grid it is handed. Omitting
`tick` still uses `TICK`, so **every existing sports caller is byte-identical**.

**Why it was NOT changed.** Changing the default changes a live execution
bound on the sports lane, which is a strategy-behaviour change in a lane that
is currently running. The owner froze PMUS strategy behaviour on 2026-09-23
and directed this to be recorded rather than worked on.

**Consequence of leaving it.** A complement bound computed on a 0.005 grid for
a market whose grid is 0.01 can name a price the venue will not accept. On the
sports path that price is then re-rounded downstream, so the observed effect is
a *slightly more conservative* bound rather than a rejected order — which is
why this is debt and not an incident.

**Before touching it.** Establish, from live sports market metadata rather than
from either constant, what `orderPriceMinTickSize` the sports markets we
actually quote serve; then change the default and the bound together, with a
regression run over the sports lane. Do not change it as a side effect of
crypto work.

---

## TD-2 · R1 probe image carries `pmus_q1_executor.py` it never imports

**Found** 2026-09-23, adding `src/pmus_fees.py`.

`deploy/l5_r1_probe/Dockerfile` COPYs `src/pmus_q1_executor.py`, but the R1
entrypoint (`src.pmus_l5_continuous_session`) does not import it at load time —
verified by walking the import closure. The copy set was extended to 39 files
to carry `pmus_fees.py` with it, because a copied module whose import cannot
resolve is a crash waiting for the first thing that imports it.

**Open question.** Whether `pmus_q1_executor.py` belongs in that image at all.
Removing it would shrink the reviewed copy set, but the copy set is *pinned*
and the R1 lineage gate is already failing on this branch for unrelated
reasons, so nothing should move until that gate is green and someone owns the
re-review.
