# The 34-second cadence: traced, classified, and one proposal

Reproduce with `analysis/l5/campaign9/order_slot_coverage.py`,
`window_index_stationarity.py`, and the C7 scripts named below. No live
capital was used and no trading behaviour was changed.

## 1. The order lifecycle, as the code actually runs it

| stage | where | period |
|---|---|---|
| main loop | `l5_pmus_live_runner.py:7210`, `L5_LIVE_POLL_S` | **3.0 s** |
| L5 activity feed read | `Runner.poll`, `:2354` — called every tick, **no cadence gate** | 3.0 s |
| new BTC15m action admitted | `ingest:admitted`, `:2448` — most rows are `ingest:dup` | **~14 s** measured |
| completion re-decision | `maybe_ts_completion`, gated by `TS_COMPLETION_EVERY_S` | 10.0 s |
| order-state poll | `poll_orders` under `ORDER_POLL_S`, `:217` | 6.0 s |
| entry window | `ENTRY_CUTOFF_S` / `COMPLETE_CUTOFF_S`, `l5_two_sided_probe.py:691,692` | T+600 / T+720 |
| order age deadline | `ORDER_TTL_S`, `:726` | 120 s |
| book freshness required | `MAX_BOOK_AGE_S`, `:604` | 2.0 s |

**There is no sleep, no cool-down and no inter-order timer anywhere in this
path.** `venue_cooling()` exists but is a ~3 s rate-limiter back-off, an
order of magnitude too small and not triggered in these windows.

## 2. What the decision tape shows

`data/pmus_decision_tape/pmus_decision_states_20260927.jsonl`, the
`L5_LIVE_DECISION` record — one row per engine decision, carrying the
authenticated-WS book at that instant.

| slice | rows | span | median inter-decision gap | gaps > 30 s | share of span inside gaps > 15 s |
|---|---|---|---|---|---|
| 1630z | 79 | 595 s | **0.00 s** | **17** | **99 %** |
| 1615z | 236 | 87 s | **0.00 s** | 1 | 100 % |

The engine decides in **bursts separated by silences**. A median gap of zero
with 99 % of the span inside gaps longer than 15 s is not a slow loop — the
loop is running at 3 s throughout. It is a loop that has nothing to decide.

That matches the admitted-action rate: the engine evaluates when a **new L5
action** is admitted, roughly every 14 s, not when the book moves. The ~34 s
submission cadence is approximately two admitted actions, one of which
survives the gates.

**This supersedes the serial-latency hypothesis I recorded in
`MATCHED_VOLUME_BOTTLENECK.md` §5** (3 s loop + 6 s order poll + 14 s action
+ 11 s dwell ≈ 34 s). The arithmetic fit, and it was wrong about the
mechanism: the stages are not a serial chain the slot waits through. The
engine is idle because no decision has arrived, and lowering `ORDER_POLL_S`
or `L5_LIVE_POLL_S` would therefore buy **nothing**. That was the change I
was about to propose; the trace killed it.

## 3. The four-way classification

> unnecessary waiting / lack of qualifying decisions / risk gates / venue behaviour

**Lack of qualifying decisions, with the risk gates correctly refusing most
of what does arrive. Not unnecessary waiting, and not the venue.**

- **Not waiting.** The loop and the feed poll run at 3 s continuously. No
  timer gates submission.
- **Decision arrival dominates.** Evaluations are driven by L5's action
  stream (~14 s), so between actions the engine does not look at the book
  for a new opening. This is the replication design, not a defect: firing
  on our own book instead of L5's actions would stop being L5 replication.
- **The gates refuse the rest, correctly.** C7 measured the quietest gap in
  C6 — 297 s of the 600 s entry period in 1630z. The engine evaluated **17
  times** in it and was refused **17 times** by
  `PAIR_COST_CEILING:PAIR_COST_ABOVE_CEILING`. Every one of those
  completions was a guaranteed loss of **1 to 19 cents a share**. The engine
  waited, UP fell back, and at T+484 it completed at **0.85 for +$0.2895**.
  Filling that gap would have manufactured losses.
- **Not the venue.** Submissions that did go out had `stream_age_s` of
  0.007–0.147 s and were acknowledged promptly.

Two campaigns agree on the number: **12 % of clock** (C8, six windows) and
**13 % of the entry period** (C6, three windows, measured independently in
`campaign7/THROUGHPUT_FINDINGS.md`).

## 4. There is no safe per-window code or configuration change

Checked and rejected, each for a stated reason:

| candidate | verdict |
|---|---|
| lower `ORDER_POLL_S` / `L5_LIVE_POLL_S` | **No effect.** The loop is not what the slot waits on (§2). |
| longer completion dwell | **Already tested, NOT SUPPORTED.** Break-even completion pair cost 0.9532 vs achieved 0.9530. |
| quote completions dearer to win the race | **Negative at every price.** −$0.0877 at 0.96, −$0.6005 at 1.00. |
| relax the pair-cost ceiling | **Manufactures the guaranteed losses of §3.** |
| second concurrent order | **No throughput case.** The slot is idle ~87 % of eligible time; the 23 `MAX_LIVE_ORDERS` refusals in C6 are instantaneous contention, not saturation. There is a separate *variance* argument for locking both legs at the plan pair, but it would have cost C6 about $1 of realised edge and needs its own validation. |
| fire openings on our own book | **Strategy change.** Out of scope and not proposed. |

## 5. The one proposed change

> **Raise the authorized window count per campaign from 8 to 16. Change
> nothing else.**

No code change. No configuration change to any limit. The deployed gate
ceiling already reports `max_windows: 20`, so this is a number in the
authorization document, not an edit to the engine.

**Why this and not something per-window.** Per-window throughput has no safe
headroom — §3 and §4. Window count is the only remaining lever that adds
completed matched volume without touching a gate, a price, exposure,
concurrency, capital or the strategy.

**Expected effect.** Matched surplus is **+$0.1933 per window** (17 settled
windows, sd 0.2400, t=3.320). At 16 windows that is ≈ **+$3.09 matched per
campaign** against ≈ +$1.55 at 8, holding everything else constant. It does
**not** approach $5/window and does not change that target's reachability —
it raises campaign throughput, not the per-window rate.

**Maximum loss is unchanged.** The $5 daily realized-loss halt and the $20
cumulative halt bound the day regardless of how many windows run. Per-window
risk is identical: same $20 exposure, $12 cash envelope, one resting order,
maker-only, same ceilings, same completion rescue.

### The offline test, run

The lever only works if the Nth window earns what the 1st does. Run
`window_index_stationarity.py` over 15 windows of C6/C7/C8 tapes, matched
surplus only (residual is t=0.225 and would swamp the signal):

| | n | mean matched | total |
|---|---|---|---|
| windows 1–3 of each campaign | 9 | **+$0.2326** | +$2.0933 |
| windows 4 onward | 6 | **+$0.1915** | +$1.1488 |

**No material dilution.** Later windows earn 82 % of earlier ones, well
inside a sample this size. That rules out a large index effect; it does not
establish stationarity.

**Honest limits of this test.** 15 windows across 3 campaigns on 2 calendar
days, all between 16:00z and 03:15z. It says nothing about hours these
campaigns never traded, and 16 windows is 4 hours of clock, which reaches
into exactly those hours. The FIFO pairing here totals **+$3.2421** matched
against the settled ledger's **+$3.2856** over 17 windows — the 15 covered
windows and the reconstruction's partial-fill handling account for the
difference, and the ledger remains authoritative.

**What would falsify it:** the first 16-window campaign showing windows 9–16
materially below windows 1–8. That is a measurement the campaign itself
produces, at no extra risk, because the daily halt bounds the downside
either way.

This requires owner authorization. Nothing here is deployed or armed.
