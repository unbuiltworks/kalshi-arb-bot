# Lifecycle 3 — SETTLED, venue-confirmed TOTAL, attributed split

> **CORRECTED 2026-09-17 (OWNER P0).** The first version of this file called
> the matched/residual split "venue-confirmed REALIZED". It is not. The venue
> confirmed a **total**; the split is **ours**. Classification:
> **`MATCHED_REALIZED_UNCONFIRMED`**. Regression tests in
> `tests/test_income_scoreboard.py` make the old wording impossible to reuse.

`aec-itfme-maxreh-alewei-2026-09-17` · resolved 13:35:29Z · audited 13:36:49Z

## The three streams, kept separate and never summed

| stream | value | status |
|---|---|---|
| matched-pair **attributed** | +$0.00929 | **`MATCHED_REALIZED_UNCONFIRMED`** — ours, not the venue's |
| directional-residual **attributed** | −$0.09249 | same attribution, same convention |
| fees | `venue_unreported` | — |
| incentives | $0.0000 | — |
| **TOTAL venue-confirmed realized** | **−$0.08320** | **the only venue-confirmed number here** |

### What the venue actually said, in full

```
{"kind":"REALIZED","slug":"aec-itfme-maxreh-alewei-2026-09-17",
 "realized_usd":"-0.0832",
 "resolution":{"side":"POSITION_RESOLUTION_SIDE_LONG",
               "beforeNet":"-0.1900","afterNet":"0.0000"}}
```

**`beforeNet -0.1900` is the whole point.** The venue resolves a **NET**
position. The 0.40 matched YES and 0.40 matched NO cancelled and produced **no
cashflow of their own** — they never appear in the resolution. There is no
venue line stating that the pair earned anything, and no fee line at all.

### Why the split is still reported, and under what convention

The durable ledger fixes it: quantities (0.40 YES, 0.59 NO), per-fill cost
(avg 0.49 / 0.4868, spend $0.4832) and the settlement outcome. Under the
**average-cost convention** used in all authoritative accounting here,

```
matched  = min(up, dn) = 0.40 pairs at 0.9768  ->  0.40 x (1 - 0.9768) = +0.00929
residual = -0.19 NO at 0.4868                  ->  0 - 0.19 x 0.4868   = -0.09249
                                                                   sum = -0.08321
```

and that sum equals the venue's **−0.0832** to the cent. The identity holds, so
the decomposition is **determinate — not an allocation of an aggregate the
ledger cannot support**. It is nevertheless **convention-dependent** (FIFO
would move it) and **not venue-confirmed**, which is exactly what
`MATCHED_REALIZED_UNCONFIRMED` means.

`realized_confirmed: true`, `settled_by: position_resolution`,
resolution `POSITION_RESOLUTION_SIDE_LONG`, `beforeNet −0.1900 → afterNet 0.0000`.

## The finding

| | |
|---|---|
| matched pairs | **0.40** at combined cost **0.9768** |
| directional residual | **−0.19** (net NO, the side that settled worthless) |
| cost basis | $0.4832 |
| bought | 0.40 YES @ 0.49 · 0.59 NO @ 0.4868 |

**Under the stated convention the matched pair made money and the entire loss
was the directional residual.** 0.40 pairs at 0.9768 attribute +$0.00929; the
unmatched 0.19 NO attributes −$0.09249.

This is the **third consecutive lifecycle** where directional residual is the
sole loss driver, and the **first** where the matched leg is separately
**attributable** — because the total was confirmed and the ledger carries the
quantities and costs. It is **not** the first where it is separately
*confirmed*: no venue here has yet served a per-leg realized figure, so that
remains at zero markets.

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
| cumulative matched-pair **attributed** | +$0.00929 `MATCHED_REALIZED_UNCONFIRMED` |
| **cumulative matched-pair venue-CONFIRMED** | **$0.00 — no venue has served a per-leg figure** |

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
