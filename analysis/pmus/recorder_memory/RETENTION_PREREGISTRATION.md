# The 3900-second retention decision — pre-registered, measured, and **DECLINED**

**Outcome: do not add the age bound. The measurement falsified my own
proposal.** Nothing was implemented, nothing deployed, no variable set, no
restart. `PMUS_WS_STATE_MAX_MARKETS` stays at **1500** per the accepted
verdict.

---

## 1. The pre-registration, written before the measurement was read

From `MEMORY_CAP_EXPERIMENT.md` §5, verbatim:

> **Decision rule, pre-registered:**
> - if the p50 market holds **< 2000** entries spanning **> 3900 s** → the age
>   bound is a strict win; implement and test it, deploy at its own boundary.
> - if the p50 market **saturates** the 2000-entry bound inside 3900 s → the
>   count bound is already tighter than the age bound for typical markets, the
>   saving is small, and **nothing should change at all**.
> - either way, **no cap change is proposed.**

The arithmetic that makes it decidable: retention is count-based
(`del s["bbo_history"][:-2000]`) and the widest window any consumer reads is
`window_s + SLOT_S = 3600 + 300 = 3900 s`. An age bound frees memory only
where 2000 entries span **more** than 3900 s, i.e. where the market's
book-update rate is below **0.513 updates/s**.

## 2. The measurement

Source: `PMUS_WS_XCHECK` rows from the running recorder deployment
`32ac7295`, 01:30Z–02:36Z on 2026-09-14. These carry per-market
`ws_book_updates` and, unlike the status document's copy in the log stream,
do **not** truncate at 81,920 bytes. Differencing one market's counter across
two rows gives its update rate directly. 333 XCHECK rows, 20 distinct
markets, 17 with ≥60 s of separation.

| | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|
| book updates / s | 2.31 | **2.86** | 3.63 | 4.55 |
| seconds 2000 entries span | 551 | **699** | 866 | — |

**The p50 market burns through its 2000-entry history in 699 seconds — 11.6
minutes — against a 3900-second consumer window.** All 17 have lifetime
counters ≥3201, so every one is saturated at the count bound. In **16 of 17**
the count bound is already strictly tighter than the proposed age bound.

## 3. Verdict

**The second branch of the rule fires: change nothing.**

The age bound would be a no-op for the markets measured. The 676 MB saving
estimated in `MEMORY_CAP_EXPERIMENT.md` §4 assumed markets retain far more
history than 3900 s of it; at ~2.9 updates/s they retain 11.6 minutes. That
estimate was wrong and this measurement replaces it.

An independent check agrees. The heap model measures ~571 KB per market at a
full 2000-entry history; 1500 saturated markets is ~836 MB, and the recorder's
own `ru_maxrss` reads **0.652 GB**. The observed heap is what a fully
saturated count bound predicts. **There is no slack for an age bound to
recover.**

## 4. What this measurement does NOT establish — stated because it decides the scope

`PMUS_WS_XCHECK` cross-checks the keyless gateway against the authenticated
socket for markets the recorder is **subscribed** to. It samples the ~160
**seated** markets and never the ~1,340 unseated **COLD** entries, which are
89% of `mstate` and therefore most of the memory.

- Seated markets: **measured**, age bound is a no-op.
- Cold markets: **UNKNOWN.** There is no read-only path to their counters —
  the status document holds them but its log copy truncates, and the
  recorder has no public domain from this session.

A cold market subscribed only during a rotation slice could plausibly hold
few entries over a long span, where an age bound would help. That is a
hypothesis, not a finding, and it is not promoted. Resolving it needs a
reader for the untruncated status document, which is a change to a live
service and is **not** proposed here.

## 5. Standing position

| item | state |
|---|---|
| `PMUS_WS_STATE_MAX_MARKETS` | **1500, unchanged** |
| `PMUS_WS_STATE_MAX_IDLE_S` | 7200, unchanged |
| `bbo_history` count bound | 2000, unchanged |
| age bound at 3900 s | **declined on measurement** |
| container memory ~7.1 GB | page cache from a ~1.8 GB/h tape write against a 12 GB budget; reclaimable, not the heap |
| process heap | 0.652 GB peak, bounded, behaving as designed |

Reproduce: `python3 analysis/pmus/recorder_memory/bbo_history_rate.py <railway-log-dump.json>`
