# pmus-btc15m-tape — READ-ONLY PMUS BTC15m book + tape collector

Railway service `1f0c6aaf-657c-4c82-bb0d-592250c089b4`, project
`50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`, env `f3642d27-26dc-4e21-9170-bc76290c076f`.

## Why it exists

The open completion-side question — *when residual exists, are there subsequent
executable states where completing it beats carrying it* — is a question about
**which moments** to act on. Answering it needs the book, with depth, at every
state.

We do not have that. The L5 Polymarket corpus (2,216 windows) carries **no book
at all**. Our own PMUS book evidence survived for two windows before Railway
reclaimed the deployment that held it: **1515z and 1530z are gone permanently**.
What was rescued is committed at `data/pmus_decision_tape/`.

So this records the market side continuously, *before* the question is asked.

## What it records

Per slug, every 2 s:

- `kind: "bbo"` — best bid/ask, `bid_shares`/`ask_shares`, `bid_depth`/
  `ask_depth`, `last`, `shares_traded`, `state`, `one_sided`, `two_sided`
- `kind: "book"` — the **full ladder both sides** (`bids`, `offers` as
  `[price, qty]`), which is what a depth-aware completion study needs
- `shares_traded_delta_lower_bound` — the delta of the cumulative
  `sharesTraded` counter between consecutive observations

Plus `tape_start` and `reslate` provenance rows. The series universe re-slates
every 45 s, so each new 15-minute market is picked up on its own clock.

## What it must never be read as

**It records the BOOK, never a fill.** PMUS serves no public trade-print
endpoint — `/trades`, `/history`, `/ohlc`, `/candles` and `/price-history` all
404 — so `shares_traded_delta_lower_bound` is the only print evidence the venue
exposes, and it is a **lower bound on traded volume**, never a trade count and
never a fill.

A tape that let us assume fills would be worse than no tape, because it would
make every candidate look good.

## Safety

Read-only and unauthenticated **by construction**: public gateway endpoints
only, no credentials, no account state, no signing path, no order verb. The
image copies exactly one stdlib-only module. It cannot touch the live executor
and it moves no money. Safe to run 24/7 and meant to.

## KNOWN GAP — persistence

The start command writes to `/state/pmus-btc15m`, but **no volume is attached**:
the project is at Railway's limit of 20 volumes per project, and every existing
volume is mounted on a live service holding someone's evidence. Freeing one
means deleting data, which is the owner's call, not an agent's.

**Until a volume is mounted at `/state`, the tape is container-local and is lost
on restart.** Collection still runs and rows still flush every cycle, so a live
window is readable; it simply does not survive a redeploy or a crash-restart.

To fix: free a volume slot, then attach a volume to this service at `/state`.
No code or config change is needed — the path already points there.
