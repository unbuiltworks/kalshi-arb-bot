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

---

## TD-3 · The session container cannot host continuous collection

**Found** 2026-09-23, after the local L5→PMUS observer and PMUS book tape died
twice without leaving an error.

**What is actually happening.** Neither process crashed. Both times the logs
stop mid-stream, with no traceback and no non-zero exit, and both times every
descendant died simultaneously — including a `setsid`/`disown` supervisor shell
whose only job was to restart them. `/proc/uptime` read **50.5 seconds** while
the observer's own tape showed hours of prior work, and the session process is
launched with `--session-mode resume`. The microVM is suspended between turns
and resumed fresh: PID 1 is new, so nothing started in a previous turn exists.

Measured interruptions: 16:57:46Z–17:29:40Z (~32 min) and 17:43:xx–17:45:30Z.

**Why no local fix works.** `nohup`, `setsid`, `disown` and a restart-loop
supervisor all defend against signals and parent exit. None of them survives
the destruction of the whole VM. There is no local process that can be made
durable, so this is not worth further engineering.

**What it does NOT damage.** The observer persists its action tape to disk and
re-hydrates from it on boot (824 actions at the 17:45 restart, 631 at 17:29),
and re-ingested actions are marked `backlog` rather than `realtime`. So:

- no L5 action is lost;
- realtime latency and copyability statistics are **not** contaminated by the
  gap — the re-read actions land as `BACKLOG_WINDOW_CLOSED`, which is exactly
  what that separation was built for.

**What it does damage.** A window whose L5 actions arrive during a suspension
can never be a *realtime* window, because the PMUS state at receipt was never
observed. Windows **17:00** and **17:15** are backlog-only for this reason and
cannot count toward the five-active-window sample, despite the authenticated
trade tape for them existing on Railway.

**Consequence for the programme.** Continuous collection belongs on Railway,
where `pmus-ws-evidence-recorder` and `l5-live-habitat` already run 24/7 and
were unaffected by both suspensions. The local observer should be treated as an
*interactive analysis tool over the durable tape*, not as a collector. The
piece that has no Railway equivalent today is the observer's alignment of an L5
action to the PMUS book **at receipt time** plus its copyability class; until
that moves, the realtime sample accrues only while a turn is active.

**Before touching it.** Moving the observer to Railway is a deployment change,
not a strategy change: the band, controller, persistence and interaction
classifier stay frozen. It needs owner sign-off because it adds a service, and
it must stay read-only and mutation-incapable like the recorders it joins.
