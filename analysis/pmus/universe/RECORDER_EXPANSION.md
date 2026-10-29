# PMUS RECORDER EXPANSION — before / after

**VENUE:** polymarket US only. No polymarket.com data appears in this file.
Service `pmus-ws-evidence-recorder` (`71b6ea99`). Live engine `f7a3e224` / `86eb63ff` untouched throughout.

---

## Two corrections to my own funnel report

1. **Seats were 128, not 80.** The recorder's own status reports `subscribed_markets=128`. The 80 I reported is the probe-visible row count from a separate downstream status filter, which I labelled as the subscription cap. The coverage diagnosis survives — 128 against ~2,981 qualified markets is 4.3% — but the authorised "80 → 160" step was really **128 → 160**.
2. **The binding risk was memory, not disk.** Disk retention was already bounded (`sweep_raw_tapes`, 12 GiB, every discovery cycle). Memory was not bounded at all.

## The blocker I found before raising anything

`mstate` was **never pruned** — one entry per market *ever observed*, each holding up to 2,000 BBO snapshots and 500 trades, fed continuously by COLD rotation over an 11,201-market live universe. The recorder's own status said it all:

```
fresh_markets    809
stale_markets  228,131      →  228,940 entries in memory
```

That is the 8.27 GB RSS, and it is the memory analogue of the 2026-09-08 ENOSPC. Doubling the seat cap doubles the rate markets enter that dict. **The bound had to land first.**

`prune_state` is deliberately conservative, because admission reads `trades_1h` and `two_sided_trades` off this state:
- never evicts a subscribed or protected market, at any age;
- only evicts markets idle beyond `PMUS_WS_STATE_MAX_IDLE_S` (default 7200 s — comfortably longer than the 1 h window `trades_1h` needs, so a market cannot lose evidence while that evidence is still live);
- only then, if over `PMUS_WS_STATE_MAX_MARKETS` (default 1500), drops oldest-idle first;
- never-observed entries have no evidence to lose and go first.

## Baseline → after

| | before (128 seats) | after (160 seats) |
|---|---|---|
| RSS | **8.27 GB** (avg 7.99, max 8.30) | **0.709 GB** |
| CPU | 0.55 (avg 0.81) | 0.075 |
| disk | 27.5 GB / 50 GB | 27.9 GB / 50 GB (retention sweeping) |
| WS `conn` | connected | connected |
| subscribed | 128 | **160** |
| fresh_subscribed | 119 / 128 | **160 / 160** |
| `stale_markets` (mstate) | **228,131** | **0** |
| `fresh_markets` | 809 | 1,048 |

RSS fell ~12×. Every seat is live; none stale.

## Seat composition — the actual objective

Of the status-visible seat set (38 rows; the status view is a filtered subset of the 160, so these are shares not totals):

| class | before (of 80 probe-visible) | after |
|---|---|---|
| `aec-` moneyline | 10 | **19** |
| `asc-` spread | 45 | 16 |
| `atc-` total | — | 1 |
| `astatc-`/`tsc-` **props** | **21** | **2** |

There are only ~19–21 qualified `aec-` moneylines on the entire live venue. **Essentially the whole liquid core is now seated**, and props no longer crowd it out — which was the point. The prior seat set failed `thin_flow` on 76 of 80 rows precisely because it was spent on player props.

## What I have NOT proven

- **Raw qualified coverage is still low**, and deliberately so: 160 of ~2,700–2,981 qualified is ~5–6%. Per the directive that was never the target; the target was covering the markets that can actually produce L5-style fills.
- **The quota has not yet been shown to bind in production.** Dry-run against the real universe with flow held equal shows the class prior alone does the ordering; the quota is the backstop for when props genuinely out-flow the core. Only live data with real flow separates the two.
- **`eligible` and `chosen` are not yet measurable** — the new seats need to season before admission can judge them on `trades_1h`. The before/after eligibility comparison is Phase 4's remaining half and needs time, not more code.

## An outage I caused

Deploy `b2d8b902` came up SUCCESS at 18:11:50Z and crash-looped from 18:12:52Z:

```
TypeError: plan() got an unexpected keyword argument 'protected'
```

`pmus_ws_evidence_recorder_enriched` **monkeypatches** `pq.plan` with its own wrapper, and I changed the canonical signature without mirroring it. The recorder is the probe's only evidence source, so the live engine was blind for ~6 minutes. It fails closed — no order is admissible without fresh authenticated evidence — so the cost was lost observation, not money or risk. Fixed in `42497917`, with two regressions: one comparing the wrapper's signature to the canonical planner's directly, one proving the wrapper actually *forwards* the value (mirroring the signature while dropping the argument would silently unpin held markets, which is worse than a TypeError because it fails quietly).

## Unchanged

No order size, acquisition concurrency, pair ceiling, residual limit, daily stop, or portfolio exposure was touched. Gates unchanged: band 0.25–0.75, min trades 10, touch depth 1, pair target 0.98.

`&categories=sports` is still in `src/pmus_event_progress.py:51` and still narrows nothing today (61 unfiltered vs 62 filtered live events; the whole live venue is category `sports`). Not removed in the same step that changed the planner and the cap.
