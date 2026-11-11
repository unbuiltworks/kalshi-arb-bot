# PMUS LIVE UNIVERSE FUNNEL — where the live events disappear

**VENUE:** polymarket US (`gateway.polymarket.us`)
**SOURCE:** `/v1/events?live=true&active=true&closed=false` — the same endpoint production's discovery uses
**SETTLEMENT_RULESET:** `pmus_sports_position_resolution`
**Captured:** 2026-09-13 ~18:0xZ · generator `analysis/pmus/universe/audit_live_funnel.py` · 13 regressions

This file contains **no polymarket.com data of any kind**. A test asserts the module cannot reach `gamma-api`.

Live engine `f7a3e224` / `86eb63ff` untouched. Read-only, GET-only throughout.

---

## 1. The funnel — 61 live events

```
61 live events
→ 11,201 markets
→ 10,216 open                (985 resolved/closed)
→  6,326 two-sided           (3,890 one-sided or degenerate book)
→  2,981 in price band 0.25–0.75   (3,345 out of band)
→     62 with a recorder seat
→     62 ELIGIBLE_NOW
```

Buckets are exhaustive and sum to 11,201 by construction; a test pins it.

**2,919 of 2,981 book-and-band-qualified markets — 97.9% — have no recorder seat.**

## 2. This is a coverage problem wearing an economics costume

Admission needs `trades_1h`, `two_sided_trades`, `off_distribution` and `bid_touch_qty`. **Every one of those comes only from the recorder's authenticated WS evidence.** A market with no seat never reaches a gate, so it is never *refused* — it is **invisible**, and it never appears in a refusal counter.

That is why production's own funnel reads like the venue is thin:

```
universe_rows 7,914 (cumulative; n_rows = 80 EVERY cycle)
refusals: thin_flow 6,967 | no_q1_touch_depth 569 | market_closed 191
          off_distribution 123 | not_two_sided 19 | cooldown 9 | from_future 7
admitted 29 · not_started {no_candidate: 89} · chosen null
```

The seat cap is `PMUS_WS_MAX_MARKETS` (code default 24, currently set to **80**). 80 seats against 2,981 qualified markets is **2.7% coverage**. `chosen: null` is what 2.7% coverage looks like.

## 3. What the 80 seats actually hold

All 80 recovered from the probe's own selection row (`refused[] + admitted[]` = `n_rows`):

| reason | seats |
|---|---|
| `strict_evidence_thin_flow` | **76** |
| `strict_evidence_no_q1_touch_depth` | 2 |
| `off_distribution` / `market_closed` | 2 |

**76 of 80 seats fail on thin flow.** Not because the flow gate is wrong — because of *what the seats are spent on*: 21 seats on `astatc-` NFL **player stat props** (`pyd-josall-gte175`, `ryd-kylmon-gte30`), 45 on `asc-` spreads, only **10** on `aec-` event moneylines.

## 4. Per-sport, PMUS only

| sport | events | markets | open | two-sided | in band | seated | one-sided | band-rejected |
|---|---|---|---|---|---|---|---|---|
| football | 8 | 7,934 | 7,208 | 5,243 | 2,494 | 54 | 1,965 | 2,749 |
| baseball | 6 | 2,680 | 2,523 | 916 | 320 | 4 | 1,712 | 491 |
| soccer | 21 | 341 | 280 | 88 | 27 | 1 | 192 | 61 |
| esports | 10 | 132 | 93 | 19 | 10 | 0 | 74 | 9 |
| tennis | 9 | 92 | 92 | 36 | 25 | 2 | 56 | 11 |
| table-tennis | 5 | 21 | 14 | 8 | 6 | 0 | 6 | 2 |
| darts | 1 | 1 | 1 | 0 | 0 | 0 | 1 | 0 |

Two things worth stating plainly:

- **Event count and market supply are inverted.** Soccer has the most live events (21) and almost no tradeable supply (27 in-band); football has 8 events and 2,494, because each NFL game carries ~1,000 prop markets.
- **The PMUS universe looks nothing like polymarket.com's.** On `.com`, soccer was 12,099 markets. On PMUS it is 341. This is exactly why the two must never be combined in one report.

**Seat allocation is NOT skewed by sport.** Football is 88.7% of seats and 86.9% of the qualified set. I initially read the 79%-NFL seat share as mis-weighting; against qualified markets rather than event counts, it is roughly proportional. The skew is by **market type**, not sport.

## 5. The liquid core is tiny

`aec-` event moneylines — the main lines, the only markets structurally likely to carry real two-sided flow — across the entire live PMUS venue:

| sport | qualified `aec-` | seated |
|---|---|---|
| baseball | 5 | 2 |
| tennis | 5 | 2 |
| football | 4 | 0 |
| esports | 3 | 0 |
| table-tennis | 3 | 0 |
| **total** | **21** | **4** |

So: raising the seat cap alone mostly buys **more thin props**. The qualified set is 55% `astatc-` player props and 18% `tsc-` team stat props. Coverage and ranking have to move together.

## 6. Universe-narrowing audit

Audited for: hardcoded category allowlists, slug allowlists, MLB-only logic, event-type restrictions, discovery caps, top-N truncation, rotation starvation, stale live-event tests.

| suspect | verdict |
|---|---|
| `&categories=sports` hardcoded in `src/pmus_event_progress.py:51` | **NOT narrowing.** Measured both ways: unfiltered returns **61** live events, filtered **62** (the difference is timing between two calls). The entire PMUS live universe is category `sports` right now. |
| MLB-only / slug allowlists | none found in the live discovery path |
| discovery paging | `live_events` `hit_cap: false`, `pages: 1` — not truncating |
| event-type restriction | none; `table-tennis` and `darts` come through the same path |
| **`PMUS_WS_MAX_MARKETS` = 80 seats** | **THIS IS THE NARROWING.** 2.7% of the qualified universe. |
| **seat ranking spends seats on props** | **SECOND NARROWING.** 76/80 seats thin-flow-refused; only 10 seats on moneylines. |

I want to be explicit that the `categories=sports` filter is a **negative result**. It looks exactly like the bug, and it is not the bug today. It would become one the moment PMUS lists a non-sports category, so it is worth removing — but removing it would not produce a single extra market right now, and reporting it as the fix would have been wrong.

## 7. What a fix requires

Neither change touches per-market dollars, pair ceiling, residual allowance, daily stop, or portfolio risk. Both are **shadow/coverage** changes.

1. **Raise recorder seats.** `PMUS_WS_MAX_MARKETS` on `pmus-ws-evidence-recorder`. Variable change + recorder restart. **Not free:** more WS subscriptions means more memory and more raw tape on the volume, and this service has already had one ENOSPC incident (00:40Z 09-08, fixed by retention `12b90d0d`). The probe depends on the recorder for *all* evidence — if the recorder dies, the live engine goes blind. So this wants a bounded step with RSS/disk watched, not a 10× jump.
2. **Rank seats by market type.** Prefer `aec-` moneylines and main lines over `astatc-`/`tsc-` props. Code change to the recorder's seat selection + deploy. This is the higher-value half: it costs no extra resources and directly attacks the 76/80 thin-flow refusals.

**Neither is applied.** Both are prepared and awaiting a go, because both change a production service the live engine depends on.

## 8. Status

- PMUS live universe: **enumerated, 11,201 markets, exhaustively bucketed.**
- Cause of `chosen: null`: **recorder seat coverage (2.7%) plus seat composition**, not an opportunity shortage and not an economic gate.
- No gate relaxed. No limit moved. No production change made. Live engine running `f7a3e224`.
