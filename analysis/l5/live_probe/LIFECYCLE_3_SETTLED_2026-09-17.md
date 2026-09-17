# Lifecycle 3 — SETTLED, venue-confirmed REALIZED

`aec-itfme-maxreh-alewei-2026-09-17` · resolved 13:35:29Z · audited 13:36:49Z

## The three streams, kept separate and never summed

| stream | value |
|---|---|
| **matched-pair realized** | **+$0.00929** |
| **directional-residual realized** | **−$0.09249** |
| fees | `venue_unreported` |
| incentives | $0.0000 |
| **total venue-confirmed realized** | **−$0.08320** |

`realized_confirmed: true`, `settled_by: position_resolution`,
resolution `POSITION_RESOLUTION_SIDE_LONG`, `beforeNet −0.1900 → afterNet 0.0000`.

## The finding

| | |
|---|---|
| matched pairs | **0.40** at combined cost **0.9768** |
| directional residual | **−0.19** (net NO, the side that settled worthless) |
| cost basis | $0.4832 |
| bought | 0.40 YES @ 0.49 · 0.59 NO @ 0.4868 |

**The matched pair made money. The entire loss was the directional residual.**
0.40 pairs bought at 0.9768 redeemed at par — +$0.00929. The unmatched 0.19 NO
lost −$0.09249.

This is the **third consecutive lifecycle** where directional residual is the
sole loss driver, and the **first** where the matched leg is separately
measured rather than left unmeasured by a venue that served no `realizedPnl`.

It is one market. It is a hypothesis with three consistent observations behind
it, not a proven universal rule, and the ADAPTED sweep tests it rather than
assuming it.

## Cumulative, venue-confirmed only

| | |
|---|---|
| lifecycle 1 `aec-itfwo-erishi-jiahan` | −$0.1836 |
| lifecycle 2 `aec-itfme-adrbar-fautab` | −$0.3783 |
| **lifecycle 3 (this one)** | **−$0.0832** |
| **cumulative confirmed realized** | **−$0.6451** |
| **cumulative matched-pair realized** | **+$0.00929** |

**Excluded and named**, settled but the venue served no `realizedPnl`, so
UNMEASURED rather than zero: `aec-itfme-artkuk-egople-2026-09-17` (the first
matched pair, cost basis 0.92) and `aec-itfme-conbra-andpet-2026-09-16`.
Unconfirmed settlement is never merged into the cumulative figure.

## Cohort context

`CAMPAIGN_STOP:HALT` 13:36:57Z. 21 sessions, 10 settled, 1 pending. 64
decisions, `action_selection: stochastic` throughout.

Sampled open 23 / extend 28 / complete 13. Acted open 12 / extend 25 /
complete 16 / hold 11 — **all 11 holds `authenticated_book_unusable`, zero
pair-ceiling refusals**. EXTEND run lengths 1,1,1,1,3,9,2,4,2,1 (max **9**,
against 76 under the modal selector).

47 orders: 5 filled · 26 rested to deadline · **13 rejected, every one an
EXTEND** · 3 repriced. COMPLETE: 16 submitted, **2 filled, 14 rested**, 0
rejected. Residual at settlement −0.19.
