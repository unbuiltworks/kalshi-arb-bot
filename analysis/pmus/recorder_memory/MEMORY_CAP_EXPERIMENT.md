# Recorder memory: the smallest cap experiment — **DESIGN ONLY, NOT DEPLOYED**

**Recommendation up front: do not lower `PMUS_WS_STATE_MAX_MARKETS`.** The
measurement that motivated the experiment does not say what it was read to
say, and the cap is the wrong lever for the number in question. A smaller,
strictly better experiment is designed below in its place. Nothing here was
deployed, no variable was set, no service was restarted, and the recorder is
still serving deployment `32ac7295` (commit `b1efec2a`, up since
2026-09-13T19:39Z).

---

## 0. The correction that comes first

**My prior diagnosis in `faaafa51` was wrong, and its error was to treat two
different quantities as one.**

`pmus_ws_evidence_recorder.rss_gb()` reports
`resource.getrusage(RUSAGE_SELF).ru_maxrss` — the **Python process
high-water mark**. Railway's `MEMORY_USAGE_GB` reports the **container
total**, which under cgroup v2 includes page cache. Read side by side on the
same deployment:

| when | uptime | process peak RSS (`ru_maxrss`) | container `MEMORY_USAGE_GB` |
|---|---|---:|---:|
| 2026-09-13T19:49:21Z | ~10 min | **0.603 GB** | ~0.599 GB |
| 2026-09-14T02:05:04Z | ~6 h 26 m | **0.652 GB** | **7.081 GB** |
| growth | 6 h 16 m | **+49 MB** (+8%) | **+6.48 GB** (+1082%) |
| rate | | **7.8 MB/h** | **1.03 GB/h** |

`ru_maxrss` never decreases. So over the whole observation the Python heap's
**all-time peak never exceeded 0.652 GB**, while the container grew by 6.48
GB. The container figure grew ~132× faster than the process high-water mark.
**The growth is not the Python heap, and therefore not `mstate`.**

`faaafa51` read the container series (`0.599 → 6.45 GB`) and attributed it to
"the 1500-market COLD state ... ~3.75M small dicts, ~2 GB of live objects".
That attribution is not supported. The process's own high-water mark refutes
it.

### What the 6.48 GB actually is

The recorder writes a raw tape continuously and sweeps it to a 12 GB budget.
From its own `raw_retention` rows on this deployment:

| window | parts deleted | bytes freed |
|---|---:|---:|
| 23:18:11Z → 01:49:55Z (2 h 31 m 44 s) | 9 hourly parts | **4,592 MB** |

`kept_bytes` stays pinned at 12.30–12.51 GB against a 12,884,901,888-byte
budget, so in steady state **bytes written ≈ bytes deleted ≈ 1.82 GB/h**, and
`DISK_USAGE_GB` is flat at 27.78 GB (min 27.50, max 27.90 over 7 h). Every
written byte passes through page cache, which the kernel does not reclaim
until there is pressure — and there is none at 7.08 GB against a 32 GB limit.

Container growth of 1.03 GB/h against a write rate of 1.82 GB/h is exactly
the shape of page cache filling toward the ~12.3 GB resident tape and then
plateauing. It is reclaimable, it is not a leak, and **no cap value changes
it.**

---

## 1. Why `prune_state` is already doing its job

Live `state_prune` telemetry, every ~70 s, 13 consecutive samples
(01:51:14Z → 02:05:22Z):

```
kept 1500   evicted_idle 0   evicted_over_cap 24-82   protected 160
max_idle_s 7200.0   max_markets 1500
```

- `evicted_idle: 0` in **every** sample — nothing reaches the 2 h idle rule.
- `protected: 160` in every sample — all seats present.
- Eviction is driven **entirely** by the cap: 52.2 markets per 70.7 s cycle,
  **0.738/s = 2,657/h**, against 1,340 non-protected slots.

The dict is saturated and thrashing at its bound. That is the bound working,
not failing.

---

## 2. Seat protection is structural, not a tuning parameter

`prune_state` skips protected markets before they ever enter the eviction
candidate list:

```python
for slug, st in mstate.items():
    if slug in keep:
        continue
    ...
```

A seat cannot appear in `evicted_idle` or in the over-cap `remaining` list at
any cap. **All 160 seats are safe at 1500, 1000, 750 and 500 alike** — by
construction, and it needs no experiment to establish. So "protect all 160
seats" is not the constraint the experiment trades against.

The thing a lower cap actually destroys is **admission evidence for unseated
markets**. `prune_state`'s own docstring says so:

> *"Admission reads trades_1h / two_sided_trades off this state, so evicting a
> market throws away the evidence that makes it admissible."*

---

## 3. The evidence-loss metric, measured — and it already fails at 1500

Residency of an unseated market, by Little's law on the observed eviction
rate (steady state: evictions = arrivals, so the rate is cap-invariant and
residency scales with slot count):

| `MAX_MARKETS` | seats | unseated slots | mean residency | fraction of the 3600 s `trades_1h` window | heap saved (measured) |
|---:|---:|---:|---:|---:|---:|
| **1500 (current)** | 160 | 1340 | **30.3 min** | **50.4%** | — |
| 1000 | 160 | 840 | 19.0 min | 31.6% | 299 MB |
| 750 | 160 | 590 | 13.3 min | 22.2% | 434 MB |
| 500 | 160 | 340 | 7.7 min | 12.8% | 569 MB |

**At the current cap the mean unseated market already survives ~30 minutes —
half of the hour `trades_1h` needs.** Every proposed lower value makes a
pipeline that is already evidence-starved worse, and the promotion path from
COLD observation to seat is the only way the 160 seats get better occupants.

Against the number that prompted the concern, the saving is derisory: 299 /
434 / 569 MB against a **7,081 MB** container figure — **4.2% / 6.1% / 8.0%**
— and none of it touches the page cache that is 91% of that figure.

**Trading half the remaining admission evidence for 4–8% of the wrong number
is a bad trade. That is the recommendation.**

---

## 4. The heap model the numbers come from

`analysis/pmus/recorder_memory/heap_model.py` allocates the recorder's own
per-market shape locally (no import of the recorder, no socket, no
environment, no Railway contact) and measures `VmRSS` — *current* resident,
not `ru_maxrss`, because a high-water mark cannot measure a structure being
freed.

Heap is linear in market count, at ~548–571 KB per fully-populated market:

| markets | bbo entries | trades | heap | KB/market | saved vs 1500 |
|---:|---:|---:|---:|---:|---:|
| 1500 | 2000 | 500 | 836.1 MB | 570.8 | — |
| 1000 | 2000 | 500 | 537.1 MB | 550.0 | 299.0 MB |
| 750 | 2000 | 500 | 401.7 MB | 548.5 | 434.4 MB |
| 500 | 2000 | 500 | 267.3 MB | 547.4 | 568.8 MB |

**And it is equally linear in retained history depth — where the cap costs
market coverage and depth costs nothing:**

| markets | bbo entries | trades | heap | KB/market | saved vs 2000 entries |
|---:|---:|---:|---:|---:|---:|
| 1500 | 2000 | 500 | 815.7 MB | 556.9 | — |
| **1500** | **400** | **100** | **160.3 MB** | **109.4** | **675.8 MB** |
| 1500 | 100 | 25 | 39.7 MB | 27.1 | 796.4 MB |

Trimming history depth at cap 1500 saves **more heap than dropping the cap to
500** — 676 MB against 569 MB — while keeping all 1500 markets under
observation.

(Caveat, stated not hidden: synthetic entries are uniform. Real markets vary
in history depth, so these are per-entry slopes, not a forecast of the live
process. §5 turns that into the one measurement the experiment must take.)

---

## 5. THE EXPERIMENT I RECOMMEND INSTEAD — and it is smaller

The retention bound and its consumers are mismatched in *kind*, not in size.
Complete consumer census of `bbo_history`:

| consumer | what it reads | window it needs |
|---|---|---|
| `pmus_ws_evidence_recorder.py:234` (producer) | `del s["bbo_history"][:-2000]` | **count-based, 2000** |
| `pmus_ws_evidence_recorder.py:269` `ws_gate` | filtered list | `window_s + SLOT_S` = 3600 + 300 = **3900 s** |
| `pmus_spring_shadow_replay.py:177` `summarize_state` | filtered list | `WINDOW_S + SLOT_S` = **3900 s** |
| `pmus_priority_queue.py:255` `book_rate` | count in window | `BOOK_RATE_WINDOW_S` = **600 s** |
| `pmus_priority_queue.py:130` `last_authenticated_t` | `hist[-1]` only | **newest entry** |
| `pmus_ws_evidence_recorder.py:591` (status row) | `hist[-1]` only | **newest entry** |
| `pmus_ws_evidence_recorder_enriched.py:73` | `max(t)` over trades+bbo | **newest entry** |
| `pmus_ws_evidence_recorder.py:426` `snapshot_state` | **full shallow copy each status cycle** | none |

Every caller of `ws_gate` and `summarize_state` passes the default
`window_s = 3600.0` (`recorder.py:359`, `enriched.py:97`, `enriched.py:253`,
`pmus_spring_evidence_transfer.py:90`) — no caller widens it. **So the widest
window any consumer reads is 3,900 s, and retention is count-based at 2,000.**
For the quiet COLD markets that are 89% of the dict, most retained entries are
older than anything ever looks at; for a hot market, 2,000 entries may not even
span 3,900 s.

### The change to test

```python
del s["bbo_history"][:-2000]                    # existing count bound, KEPT
# plus, additionally:
cut = now - BBO_MAX_AGE_S                       # BBO_MAX_AGE_S = 3900.0
i = bisect_left(KeyView(s["bbo_history"], "t"), cut)
if i:
    del s["bbo_history"][:i]
```

`min(count 2000, age 3900 s)`. Keeping the existing count bound means the
change **can only reduce and never increase** any market's retained history —
including the hot-market case where 2,000 entries span less than 3,900 s.

### Why it is the smaller experiment

- **Zero evidence loss by construction.** It removes only entries strictly
  older than the widest window any consumer reads. Every consumer's answer is
  bit-identical.
- **No market coverage lost.** All 1500 stay under observation; the 30.3 min
  residency does not shrink.
- **Seats untouched** — same structural protection, unchanged.
- **It also cuts the churn term.** `snapshot_state` shallow-copies every
  market's history list on every status cycle; shorter lists make that copy
  proportionally cheaper.
- It is testable **offline**, against the existing suite, with no deployment.

### The one measurement that must come first

Everything above rests on one unmeasured quantity: **the distribution of
`bbo_history` length per market at steady state**, i.e. how many entries each
market actually holds and how many of those fall inside 3,900 s.

- **Source:** `ws_book_updates` and `ws_last_update_age_s`, already served per
  market in the recorder's status document. Read them from the `/status`
  endpoint over the internal network (the probe already does exactly this via
  `http://pmus-ws-evidence-recorder.railway.internal:8080/status`) — **not**
  from the log stream, where the row truncates at 81,920 bytes and the tail
  markets are lost.
- **Decision rule, pre-registered:**
  - if the p50 market holds **< 2000** entries spanning **> 3900 s** → the age
    bound is a strict win; implement and test it, deploy at its own boundary.
  - if the p50 market **saturates** the 2000-entry bound inside 3900 s → the
    count bound is already tighter than the age bound for typical markets, the
    saving is small, and **nothing should change at all**.
  - either way, **no cap change is proposed.**

---

## 6. If the cap experiment is run anyway — how to run it so it cannot mislead

Should the owner want the 1500/1000/750/500 comparison regardless, these are
the conditions that keep it honest. It is still not recommended.

1. **One variable.** `PMUS_WS_STATE_MAX_MARKETS` only. Seat count (160), watch
   limit, `PMUS_WS_STATE_MAX_IDLE_S` (7200), tape budget and discovery
   cadence all held.
2. **Record both memory series, separately, never summed:** the recorder's own
   `rss_gb()` (`ru_maxrss`, process high-water) *and* Railway
   `MEMORY_USAGE_GB` (container, includes page cache). Conflating them is the
   error in §0 and the experiment must be incapable of repeating it. Add
   `DISK_USAGE_GB` so the tape write rate is visible alongside.
3. **Only `ru_maxrss` may be attributed to the cap.** A container-memory move
   is not evidence about `mstate`.
4. **Steady state before reading.** ≥2 h per arm, and `state_prune` must show
   `kept == max_markets` with `evicted_idle == 0` before any reading counts —
   a filling dict reads low for reasons that have nothing to do with the cap.
5. **Primary outcome is evidence, not memory:** count of distinct unseated
   markets that reach `trades_1h > 0` with `two_sided_trades == true`, and the
   count of seat promotions, per arm per hour. Memory is the cost side.
6. **Seat assertion every cycle:** `protected == 160` and all 160 slugs
   present. Abort the arm if either fails.
7. **Abort conditions:** `fresh_subscribed < 160/160`, any
   `evicted_idle > 0` on a seated slug, `conn != connected`, or a
   `raw_retention` error.
8. **Ordering:** descending (1500 → 1000 → 750 → 500), returning to 1500
   between arms, so a degradation is observed against a restored baseline
   rather than confounded with time of day and venue slate.
9. **Sequencing:** the recorder is read-only infrastructure, but it is still a
   live service. One arm per deployment boundary, each under the ≤60 s action
   window in `deploy/pmus_spring_l5/DEPLOY_POLICY.md`, re-reading and
   re-authorising if the transport stalls — which is the rule the 00:28:08Z
   cutover broke.

---

## 7. What is NOT proposed

No cap change. No seat-count change. No watch-limit change. No tape-budget
change. No restart. No deployment. No change to `pmus-spring-l5-probe`, which
stays on `75edc4bd`. `PMUS_L5_MULTI_SHADOW` stays disabled and
`src/pmus_universe_cross_shadow.py` stays undeployed.

Reproduce: `python3 analysis/pmus/recorder_memory/heap_model.py`
and `python3 analysis/pmus/recorder_memory/residency.py`
