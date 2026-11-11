# Preserved raw evidence for the four settled PMUS BTC15m windows

**Why this directory exists.** `data/pmus_auth_trades/` and
`data/l5_pmus_realtime/` are gitignored, and the session container that held
them is suspended and resumed between turns and destroyed when the session
ends (TD-3). The four windows below are the entire authenticated sample the
Phase 1 decision rests on, and three of the four markets have already expired,
so the evidence is not re-derivable from the venue. It is preserved here, in
the tracked tree, with its provenance intact.

## `auth_trades/*.jsonl`

Authenticated PMUS trade documents exactly as the recorder emitted them: 87
`PMUS_TRADE_ROWS` documents, 880 rows, four markets. Nothing is normalised on
the way in and no row is edited.

Each row carries `t` (our receipt epoch), `px`, `qty`, `trade_time` (venue
time), `bid_at_trade` / `ask_at_trade` (the AUTHENTICATED top of book at the
instant of the trade) and `dir` (taker direction).

`bid_at_trade` / `ask_at_trade` are what made the aggressor provable. Against
a cached public book the mapping tested 46.2% forward / 35.3% inverted, which
was the wrong book rather than a wrong hypothesis; stamped at ingestion it is
546/546 consistent. Do not re-derive or loosen that mapping, and do not
retro-fit rows recorded before stamping against a cached book.

The 2026-09-23T17:49:35Z file was recovered from the session transcript rather
than re-fetched, because the transcript stores tool results verbatim; four
synthetic test fixtures present in the same transcript were rejected by slug
pattern. The aggressor proof rising from 473/473 to 546/546 with zero
inconsistencies is the check on that recovery.

## `observer_rows_settled_windows.jsonl`

The realtime observer's rows for the same four windows, 267 of them: 16:15
(49), 16:30 (91), 16:45 (50), 17:30 (77). Each carries three clocks, the PMUS
snapshot taken at RECEIPT, the copyability class, the safety overlay on its
own axis, and `no_backdating_ok`.

The full tape is not copied here — it is 3.3MB and continues on the Railway
volume `/state/l5-pmus-obs`. Only the windows behind the decision are kept.

## Settlement

| window | market                                   | settled |
|--------|------------------------------------------|---------|
| 16:15  | cpc-btc-updown-15m-2026-09-23-1615z      | yes 0.0000 (DOWN) |
| 16:30  | cpc-btc-updown-15m-2026-09-23-1630z      | yes 0.0000 (DOWN) |
| 16:45  | cpc-btc-updown-15m-2026-09-23-1645z      | yes 0.0000 (DOWN) |
| 17:30  | cpc-btc-updown-15m-2026-09-23-1730z      | yes 1.0000 (UP)   |

Nothing in this directory is a fill of ours. No order existed on either venue,
queue position is not implied anywhere, and nothing here is submitted, filled,
settled or realized.
