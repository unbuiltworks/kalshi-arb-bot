# Cap verdict: telemetry/rollup only — raw persisted rows are COMPLETE

## The distinction you asked for, resolved

**Raw persistence: COMPLETE and uncapped.** The recorder's own contract, line 6
of `src/pmus_ws_evidence_recorder.py`: it *"appends every received message
verbatim to daily JSONL under a persistent root"*. Written by `append(root, row)`
to `/state/pmus-ws/raw/pmus_ws_YYYYMMDD_HH.jsonl` (hourly parts), on the Railway
volume `18bd5702…` mounted at `/state/pmus-ws`, with a **12 GB** retention budget
that drops whole old parts rather than truncating rows. No per-market cap exists
on that path.

**The 50 / 200 `cap_per_market` values are on the stdout ROLLUP docs only** —
`PMUS_BOOK_ROWS` and `PMUS_TRADE_ROWS` — via `fresh[-cap:]` in the two emitters
(`book_log_cap()` ceiling 400, `trades_log_cap()` ceiling 200). They are a
telemetry view of a complete underlying tape.

**So: proceed. No recorder persistence change is required, and I have made none.**

## But my access channel is the capped view, and it is capped unevenly

Measured directly from two consecutive emissions 9.99s apart:

| stream | events in the interval | emitted | coverage |
|---|---|---|---|
| book states | `book_updates` 1468 → 1566 = **98** | 50 | **51%** |
| trade prints | ~36 (3.55/s) | cap 200 | **100%** |

**The trade tape reaching me is complete**, and every print carries px, qty,
venue `trade_time`, taker direction, and `bid_at_trade`/`ask_at_trade` stamped at
the instant. Since fills in a causal simulator come from *prints*, the critical
input is intact. Book states arrive at ~51%, which is still **66× denser** than
the 50-per-window tape every prior analysis used.

## Decision, and the one thing I will not do unilaterally

I am proceeding on the complete print tape plus ~51% book coverage. Queue-ahead
initialisation is the only step that wants denser book state, and if it proves
to bind I will say so with evidence rather than pre-emptively touching the
collector.

Raising `PMUS_WS_BOOK_LOG` from 50 to 400 (the code's own ceiling, comfortably
above 98/interval) would make the emitted view complete. It is a telemetry-only
change and it changes no persistence. **I am not doing it without a decision,
because it restarts our only live data source:** the service carries no commit
pin, tracks `feature/l5-transfer-backtest` with watch patterns, and has been
running since 2026-09-26 on `4b5b0e84`. A variable change triggers a rebuild at
that branch's *current* HEAD, which has had four days to move. Restarting a
healthy collector onto an unreviewed commit to improve a telemetry cap is a
worse trade than proceeding at 51% book coverage.
