# pmus_flow write gap, 2026-09-21 03:55:13Z - 06:07:17Z

Service `l5-live-habitat` (`db9e951c-98c1-459c-aae5-fe7adc6e147e`), deployment
`62a54463-b441-4f3a-a723-9545e6f368c9`, Railway project
`50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`.

**The service was not restarted or redeployed for this incident.** It recovered
on its own. This record exists so the gap is preserved and marked rather than
silently averaged into the prospective sample.

---

## 1. Correction of my earlier characterisation

At the :50 durable-service watch and the :28 accounting watch I reported this to
the owner as a **writer stall** and an **observability defect in the writer**.

**That was wrong, and it is withdrawn.** The `pmus_flow` thread never stalled.
It ran its normal 8 s sweep for the entire window and wrote nothing because its
own selector legitimately selected zero markets. The real defect is upstream of
the writer and is described in section 4; a second, smaller defect is that the
heartbeat cannot tell those two situations apart (section 6).

I am recording the correction rather than quietly replacing the earlier text,
because the earlier text was used in two watch reports.

---

## 2. The gap, exactly

| quantity | value |
|---|---|
| last row before the gap | `2026-09-21T03:55:13Z` |
| first row after the gap | `2026-09-21T06:07:17Z` |
| duration | **2 h 12 m 04 s** |
| `rows_written.pmus_flow` throughout | **1,220,964** (frozen, exact) |
| `file_bytes.pmus_flow` throughout | **516,596,932** (frozen, exact) |
| `pmus_markets` throughout | 300 (unchanged) |
| sibling streams | `pmus_books`, `intl_books`, `intl_flow` all advanced normally |

In local time the window is **23:55 ET Sunday -> 02:07 ET Monday**. 2026-09-21
is a Monday; Sunday Night Football had just ended.

---

## 3. What the process was actually doing

`pmus_flow_loop` selects the markets it polls like this
(`src/l5_live_habitat_service.py`):

```python
livemap = state.get("pmus_live") or {}
sel = [
    r for r in (state.get("pmus") or [])
    if livemap.get(r["event"], (False,))[0]
    or (r.get("stratum") == "nfl_pre_control" and sweep % 4 == 0)
]
```

Both disjuncts were false for every discovered market for 2 h 12 m, so `sel` was
empty, so the ten workers had nothing to fetch and `write()` was never called.
The loop itself was healthy: it kept its 8 s cadence and its supervisor logged
no restart.

Evidence the process was alive: `uptime_s` in the heartbeat advanced uniformly
by 60 s per beat across the whole window. The heartbeat *log timestamps* bunch
at 06:03:07Z-06:07:20Z, which looks like a stall and is what misled me; it is
Railway log-shipping delay on the collector side, contradicted by the
monotonic `uptime_s` in the payloads themselves.

---

## 4. Root cause: the liveness map covers 50 of the 266 leagues discovery samples

`state["pmus_live"]` and `state["pmus"]` are built in the same discovery cycle
but from **two different league lists**:

| pass | league source | leagues returned |
|---|---|---|
| `pmus_discover()` -> `state["pmus"]` | `/v2/sports`, recursive `collect_leagues` | **266** |
| liveness map -> `state["pmus_live"]` | `/v2/leagues` -> `["leagues"]` | **50** |

Measured directly (`scratchpad/livemap_probe.py`, read-only, 2026-09-21T06:4xZ):
the 50 are a strict subset of the 266 — `B-only` is empty, `A-only` has 216
entries including `npb`, `kbo`, `wta`, `itfme`, `itfwo`, `setkameua`,
`setkamecz`, `dota2`, `r6`, `ow`, `khl`, `cfb` and the rest of the overnight
slate.

An event in one of the 216 uncovered leagues is simply **absent** from the map,
and the selector reads absence as not-live:
`livemap.get(r["event"], (False,))[0]`.

### The smoking gun in the logs

`pmus_diag_stratum` counts rows carrying the `p10_25_diagnostic` stratum, and
`pmus_discover()` only assigns that stratum when it sees `is_live` true:

```python
if is_live and DIAG_LO <= mid <= DIAG_HI:
    diag.append({**row, "stratum": "p10_25_diagnostic"})
```

Throughout the gap the discovery heartbeat reports, in the **same cycle**:

```
03:54:43Z  pmus_live_events: 0   pmus_diag_stratum: 10
05:05:23Z  pmus_live_events: 0   pmus_diag_stratum: 17
05:44:38Z  pmus_live_events: 0   pmus_diag_stratum: 11
06:02:27Z  pmus_live_events: 0   pmus_diag_stratum: 4
06:07:17Z  pmus_live_events: 1   pmus_diag_stratum: 3     <- flow resumes here
```

`pmus_diag_stratum > 0` means the discover pass saw live events. `
pmus_live_events == 0` means the liveness pass, in the same cycle, saw none.
The two passes read the same `eventState.live` field from the same endpoint —
they disagreed only about **which leagues to ask**.

### Independent confirmation, live

`scratchpad/live_coverage.py` enumerated live events under both paths at
2026-09-21T06:5xZ:

```
LIVE events visible to the liveness map:     0 in 0 leagues
LIVE events INVISIBLE to the liveness map:  24 in 7 leagues
   itfme 3, itfwo 9, npb 3, setkamecz 1, setkameua 4, wta 3, wtadb 1
invisible_share = 1.000
```

Every live event on the venue at that moment was invisible to the map. The
condition that produced the gap was still in force when it was diagnosed, which
is why the gap is a recurring overnight pattern and not a one-off.

The two endpoints also do not share one slug vocabulary: `/v2/leagues` serves
`itfm`/`itfw` while `/v2/sports` also serves `itfme`/`itfwo`. This is noted, not
relied upon — the subset relation alone is sufficient.

---

## 5. The `nfl_pre_control` residual, resolved

The second disjunct polls today's NFL pregame control every fourth sweep
regardless of liveness, so in principle it should have written something. It
wrote nothing because **there were no `nfl_pre_control` rows to select.**

That stratum requires `kickoff_within_hours(kickoff, 12.0)`. Queried directly
from `/v2/leagues/nfl/events?limit=500` at 2026-09-21T06:46Z:

```
h_to_kick= +17.48  2026-09-22T00:15:00Z  nfl-nyg-lar-2026-09-21   (MNF)
h_to_kick= +89.48  2026-09-25T00:15:00Z  nfl-atl-gb-2026-09-24
h_to_kick=+154.23  2026-09-27T17:00:00Z  ... (Week 4 Sunday)
n_events_total 33   n_within_+12h 0
```

The next kickoff was Monday Night Football at +17.5 h (≈ +20.3 h at the start
of the gap) — outside the 12 h window for the entire incident. Sunday's slate
had already kicked off and ended, so it was in the past, not the future window.
Zero qualifying rows, nothing for the every-fourth-sweep branch to select.

This is correct behaviour of the stratum rule, not a second bug. The stratum I
saw in the heartbeat (`pmus_diag_stratum`) is a *different* stratum that the
flow selector never consults; it is evidence **of** the defect in section 4, not
a residual.

---

## 6. The genuine observability defect

`heartbeat` emits `rows_written` and `file_bytes` but **no per-stream selection
size**. "Nothing was selected to poll" and "the writer is stuck" therefore
produce an identical heartbeat: a frozen counter. That ambiguity is what made me
report a stall.

---

## 7. Data integrity

- No rows were lost. The gap is an absence of observations, not corruption.
- `pmus_books` continued for all 300 discovered markets throughout, because
  `pmus_books_loop` does not consult the liveness map. Book coverage over the
  window is intact; only the flow/volume stream is missing.
- Post-recovery `pmus_flow` runs at ~7.5 rows/min against ~70 rows/min during
  the NFL slate. That is market-driven (one live event vs dozens), not a
  degraded writer.

**Any analysis using `pmus_flow` must treat 03:55:13Z-06:07:17Z as censored,
never as zero flow.** This is the same right-censoring discipline applied to the
NFL habitat reach figures.

The window is marked **in the stream**, not only here, so an analysis that never
opens `analysis/` still cannot read it as zero. On start the service appends to
`pmus_flow.jsonl`:

```json
{"_marker": "PRESERVED_INCIDENT",
 "incident_id": "PMUS_FLOW_LIVEMAP_COVERAGE_20260921",
 "window_start_utc": "2026-09-21T03:55:13Z",
 "window_end_utc": "2026-09-21T06:07:17Z", "duration_s": 7924,
 "rows_written_frozen_at": 1220964, "file_bytes_frozen_at": 516596932,
 "not_a_writer_freeze": true,
 "treatment": "CENSORED -- absence of observations, never zero flow"}
```

It is re-written on every start; these are append-only observations and
`incident_id` makes duplicates collapsible.

---

## 8. Fix

Applied in the same change that deploys the prospective recorder, so this
incident does not on its own cause a restart or redeploy:

1. `pmus_discover()` now returns `(selection, livemap)`. The map is built from
   the events it **already fetched**, so the two can no longer disagree, and the
   duplicate `/v2/leagues` + 50×`/events` pass is deleted. This strictly
   *reduces* load on the venue and removes code rather than adding it.
2. A partially-built map can no longer silently replace a complete one: the map
   is now published only alongside the selection it was derived from.
3. The heartbeat gains `selected` (per-stream selection size) and
   `pmus_flow_selected`, so an empty selection is distinguishable from a stuck
   writer without reading the discovery lines.

Risk to the healthy collector: the change removes one network pass and one
independent source of truth. `pmus_books_loop`, `intl_books_loop` and
`intl_flow_loop` do not read `state["pmus_live"]` and are untouched.
