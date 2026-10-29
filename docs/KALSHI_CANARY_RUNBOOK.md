# Kalshi Live Canary — Runbook

Purpose: measure **real fill rate and real fill quality** for frozen Policy D
against the shadow (print-proxy) numbers, at a hard-bounded cost. This is a
measurement instrument, not a deployment. One clean session answers the only
question the shadow can't: *what fraction of the shadow edge survives a real
order queue?*

## FROZEN BENCHMARK AND SUCCESS DEFINITION (reconciled 2026-08-31)

Earlier drafts cited +7.6c/$ — an interim reading at 14 windows. The final
50-window scored result superseded it with a regime split:

- **Evening regime (~7:30pm–1am ET): +3.79c/$ net after fees, 28 windows,
  17 positive.** This is the edge the canary tests.
- Overnight (1am–7am ET): ~−25c/$. The canary must NOT run overnight.

**Frozen benchmark = the same-window shadow.** Success is never judged
against the historical +3.79c average: while the canary runs, the shadow
evaluation runs on the same windows, and the comparison is
real-vs-shadow on identical windows:

1. **Capture ratio** = real filled notional / shadow filled notional
   (same windows).
2. **Fill quality** = realized c/$ on real fills vs shadow c/$ on the same
   windows. Real fills materially worse than shadow's on the same windows =
   adverse selection at the back of the queue — the key failure mode.
3. **Ops cleanliness** = zero unexplained rejects, zero deadman halts, zero
   unsettled inventory, clean settlement reconciliation.

Promotion discussion needs >= 3 clean evening sessions with positive realized
net P&L, not one lucky run. One session answers "does anything fill and is it
poisoned"; three answer "is it repeatable".

## WHEN TO RUN

Evenings only, ~7:30pm–1am ET (the measured edge regime). Do not run
overnight sessions; the shadow says that regime loses ~25c/$.

## Where to run it

**On your own machine or your AWS box — NOT in a Claude cloud session.**
Cloud session containers recycle without warning; a recycled container would
orphan real resting orders. The canary has a startup sweep and cancel-on-exit
for exactly that failure, but don't invite it.

## Worst-case cost (enforced in code)

| Cap | Value | Where |
|---|---|---|
| Quote size | 1 contract | `CANARY_QUOTE_SIZE` |
| Spend per window | $3.00 | `CANARY_MAX_SPEND` |
| Worst settlement loss per window | $1.00 | decision_core contract |
| Windows per session | 5 (CLI, hard ceiling 10) | `--max-windows` |
| **Worst case per session** | **~$5 + ~$0.50 fees** | max_windows × $1 |

The CLI can only tighten these. Raising them requires editing the source —
which per our rules we don't do to force progress.

## Setup — Railway (preferred: credentials already live there)

The existing Railway environment already holds `KALSHI_API_KEY_ID` and
`KALSHI_PRIVATE_KEY_PEM`, and `src/auth.py` reads `KALSHI_PRIVATE_KEY_PEM`
directly (literal `\n` in the PEM is handled). No new key, no laptop PEM.

Attach an isolated one-off service (restart policy **NEVER** — a crash must
not auto-restart a live trader; the next manual start's sweep cancels
leftovers) to branch `claude/polymarket-l5-historical-analysis-2ih9ui`:

- Build: `pip install -r requirements.txt`
- Env: `KALSHI_ENV=prod` (+ the two existing Kalshi secrets)
- Start (dry run first): `python3 -m src.kalshi_maker.live_canary --max-windows 2`
- Start (live): `python3 -m src.kalshi_maker.live_canary --live --max-windows 5`

Every event mirrors to stdout, so **Railway logs are the full record** — the
local JSONL under `data/kalshi_canary/` is ephemeral on Railway. Save the
deploy logs after each session (or paste them back) so the real-vs-shadow
scoring can run on them.

## Setup — laptop (fallback)

1. Clone the repo, branch `claude/polymarket-l5-historical-analysis-2ih9ui`.
2. `pip install -r requirements.txt`.
3. Create a Kalshi API key (kalshi.com → account → API), save the RSA private
   key PEM to a file OUTSIDE the repo.
4. Create `.env` in the repo root (never commit it):
   ```
   KALSHI_ENV=prod
   KALSHI_API_KEY_ID=<your key id>
   KALSHI_PRIVATE_KEY_PATH=/absolute/path/to/private_key.pem
   ```

## Run sequence — do these in order

### 1. Dry run (no credentials needed, zero risk)
```
KALSHI_ENV=prod python3 -m src.kalshi_maker.live_canary --max-windows 2
```
Watch it discover KXBTC15M/KXETH15M windows and log `dry_run_order` intents.
Confirms data plumbing + decision core on live market data. Ctrl+C to stop.

### 2. Live, one window
```
python3 -m src.kalshi_maker.live_canary --live --max-windows 1
```
Worst case ≈ $1. Watch for:
- `exchange_status` then `startup_sweep_*` lines at start
- `order_placed` with `post_only: true` — if you see `order_rejected`, the
  error body is logged in full; send it to Claude, do not retry blindly
- `fill` lines (may be zero — a no-fill window is a valid data point)
- `window_settled` with `realized`

### 3. Live, full canary (several sessions)
```
python3 -m src.kalshi_maker.live_canary --live --max-windows 5
```
Repeat across 2–3 different times of day. Everything logs to
`data/kalshi_canary/canary_<stamp>.jsonl`. Commit those files (they contain
no secrets) so Claude can score real-vs-shadow.

## What the scoring will compare

See the frozen success definition above: capture ratio, same-window fill
quality vs shadow, and ops cleanliness. Any `order_rejected`, `deadman_halt`,
or unsettled inventory ends the session as "not clean" regardless of P&L.

## Stopping / safety

- Ctrl+C cancels all resting orders on the way out (`canary_end` line).
- If the process dies hard, the next start's `startup_sweep` cancels any
  leftovers; you can also cancel manually in the Kalshi UI.
- Dead-man: 15s of stale market data → cancel-all + halt that market.
- Kill switch honored: the session halts itself after `--max-windows` and
  after all held windows settle.

## Known behavior to expect (not bugs)

- **TAPE_WARMUP** for the first ~30 prints of each window — no quotes early.
- **EXPIRY_STOP** — no new quotes in the last 90s.
- It will sometimes join very high bids (e.g. 99¢+ sub-cent) on the
  near-certain side late in a window: tiny upside, ~full-price downside, but
  within the frozen policy and bounded by the $1 worst-loss cap. The frozen
  shadow P&L includes these. We observe, we don't tune mid-canary.

## Accounting rule (unchanged)

No incentive/rebate dollars in edge or P&L. These series carry no incentive
programs today; if that changes, credits are tracked separately as
`incentive_pending` until Kalshi actually credits the account.
