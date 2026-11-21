# Issue #192 execution — NO-GO this cycle, with the blocker reduced to one thing

**Verdict: no demonstrated positive-net executable strategy. The next smallest
actionable blocker is Railway volume access to one file. It is not strategy, not
instrumentation, and not — as I previously claimed — missing data.**

No live execution from this issue. The three-window maker-only validation stays
isolated and unexpanded; the service is shadow-locked and unarmed.

---

## The correction that matters most

**My previous report (`L5_ECONOMIC_DECISION_GAP.md`) was wrong about the central
claim.** I said the high-resolution data did not exist and named it "exact
missing data." It exists, and two collectors have been capturing it for a month.
I had been analysing stale, subsampled snapshots committed to `data/` in git.

| quantity | stale git snapshot I used | what the collector actually captures |
|---|---|---|
| BTC spot | **3.72 prints/60 s** | **309.6 rows/min**, tick-level, ns timestamps |
| spot venue→receipt skew | unmeasured ("latency unresolvable") | **median 3.37 ms**, p90 8.60 ms, max 158.5 ms |
| Kalshi BTC15m book | top-of-book, one per 21 s | **full depth: 63 bid / 34 offer levels** |
| Kalshi book venue→receipt lag | unmeasured | **median 1,541 ms**, p90 3,309 ms |

Three prior conclusions are withdrawn: that the settlement-probability estimator
could not be built, that latency was unresolvable, and that "the gap is
resolution." The gap was **my dataset selection.**

## What I built and ran (reusing existing infrastructure)

New, small, read-only, no services created:

- `analysis/profit_search/tick/parse_ref.py` — extracts tick spot
  (`ref_bbo`/`ref_trade`) from `btc-ref-collector`'s stdout.
- `analysis/profit_search/tick/parse_tape.py` — extracts full-depth Kalshi books
  from `pmus-btc15m-tape`'s stdout.
- `analysis/profit_search/tick/lag_edge.py` — the causal lag test.
- Extended `analysis/profit_search/replay_b.py` with bounded defensive
  completion, selective entry and an `IMB` side-selection policy.

Dataset actually assembled: **15,364 spot ticks and 89 full-depth book snapshots
over 2026-10-25 15:09–16:00Z, covering 4 complete BTC15m windows** (1500z,
1515z, 1530z, 1545z).

## The causal test, and its result

Spot arrives ~3 ms after its venue timestamp; the Kalshi book arrives ~1,541 ms
after its own. That asymmetry is a real information window, so: does spot that
the book has not yet absorbed predict the book's next state?

| signal | n | corr | beta (c/bp) | hit rate |
|---|---|---|---|---|
| spot return, 30 s before the book's venue ts | 55 | +0.0336 | +0.0943 | 0.455 |
| spot move the book had **not** seen | 55 | −0.0030 | −0.0292 | 0.218 |

**No edge — and the reason is measurable.** The unseen move is
**median 0.00 bp, p90 1.20 bp** (~$9.70 of BTC index at 80,500). The information
window is 1.54 s wide; my book observations are **30 s apart**, so the window is
not observable in this stream. n=55 across 4 windows is also far too small to
conclude anything either way, and I am not going to pretend otherwise — that is
the same trap as XRP05M (16 markets, 67% from one, t=+1.39).

## The blocker, located exactly

`src/pmus_book_tape.py`:

- line 276 echoes every row as `PMUS_TAPE_ROW` under `--echo-rows`, documented
  *"so the tape can be retrieved where no volume exists"* — already enabled.
- line 65: **`HEARTBEAT_S = 30.0`**, and an unchanged body is suppressed until
  the heartbeat.

Measured on the echoed stream: **81 of 85 inter-row gaps are ≥25 s, clustered at
30.1 s** (range 22.5–36.4 s), only 4 change-triggered. That distribution is a
~30 s **poll interval** for the ladder, not dedup of a genuinely static book.

Meanwhile the service reports `rows: 2,191,558` in
`pmus_btcupdown15m_20260927T224630Z.jsonl` since 2026-09-27 — about **one row
per 1.1 s averaged**, on the Railway volume, which this session cannot mount.

> **So the blocker is: the full-cadence tape exists on a volume I cannot read,
> and the stdout channel I can read is ~30 s.** Everything else is in place —
> tick spot, measured skew, full depth, settlement truth, replay engine, fee and
> queue models.

### The smallest change that unblocks it

Run the **existing** read-only collector with a shorter poll for BTC15m — an
argument change on a running service, no new service, no money path, no
credentials, nothing in the trading path:

```
python3 -m src.pmus_book_tape --series btc-updown-15m --with-book \
        --echo-rows --interval 1        # currently ~30
```

One honest caveat, which is why I am **proposing rather than doing** it: this
multiplies that service's log volume roughly 15–30×, and #192 says *"no new
infrastructure cost without separate approval."* Alternatives that avoid the log
blow-up: lower `HEARTBEAT_S` only for BTC15m, or add a bounded read-only volume
export to an existing service.

Minimum useful capture at 1 s cadence: **~200 settled windows ≈ 50 hours**, which
is also roughly what the XRP05M lesson says is needed for the independent unit
(the settlement) rather than the row.

## Ranked candidates, including the null

| rank | candidate | net per eligible window | evidence | status |
|---|---|---|---|---|
| **1** | **no-trade / WAIT** | **$0.0000** | — | **currently the best option** |
| 2 | L5-band entry + bounded defensive completion ≤1.20 | −$0.0094 (held-out BTC15m) | replay | best of 5 series on 1 cell only; −$0.0268 across all 130 windows |
| 3 | cheapest-first + defensive (current engine + DEF) | −$0.0250 | replay | — |
| 4 | current engine, maker/cheapest | −$0.0330 | replay + settled live | 12 live windows −$1.3328 |
| 5 | book-imbalance side selection | −$0.1093 | replay | signal real (t=+6.41), does not convert |
| 6 | taker-open at entry | −$0.0900 | replay | 85 of 90 paired windows worse |
| 7 | directional mispricing (Gemini) | −$0.1086/signal | replay | 47,609 signals |

**WAIT dominates every implemented strategy.** That is the economic verdict and
it is the one thing all the evidence agrees on.

## On "just test live trades"

I am not opening live trading, and the reason is not timidity. Issue #192 states
*"do not arm or alter live execution from this issue"* and *"no deployment to
live, no risk-limit changes, no orders"*; the same instruction that asked for
live trades also said *"do not expand trading authorization."* I will not resolve
that conflict by picking the permissive reading on my own.

The substantive point is right, though: **the three-window authorized test is the
live channel, and it is the correct one** — it validates the corrected executor's
residual-side lifecycle, which is an execution-correctness question that shadow
genuinely cannot answer (the fix is inert when flat, and shadow cannot hold
inventory). It remains blocked only at its own step-2 prerequisite, which needs
your call between the boot-time self-check (Option A) and accepting the
substitute evidence (Option B) from `L5_SIDEFIX_ARM_BLOCKER.md`.

What live trading cannot do is answer the *profitability* question, because every
implemented strategy is measured negative and WAIT dominates. Trading them live
would buy the same answer with real money.

## $480/day

Unchanged and unsupported: the measured number is negative across four
independent premises. But the honest framing has shifted this cycle — the earlier
"we lack the data" was **wrong**. We have the instrumentation. We have one file
behind an access wall, and a 50-hour capture at 1 s cadence would settle whether
a sub-second edge exists at all.
