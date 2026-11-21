# Accounting P0: the −$6.1848 / −$7.6236 discrepancy, reconciled

Owner: *"before any further live probe, reconcile the −$6.1848 vs −$7.6236
realized discrepancy. That is accounting P0, because if we can't reconcile
realized cashflow, we can't judge the strategy."*

**Reconciled exactly, to the cent. Both numbers were correct; they answer
different questions, and nothing said so.**

## The missing row

`aec-cs2-rev-is-2026-09-11` — **realized −$1.7500**, venue-confirmed at
**15:25:24Z**, `settled_by: position_resolution_activity`,
`POSITION_RESOLUTION_SIDE_SHORT`, beforeNet 5.0000 → 0.

```
-4.4348  (camrom, 16:18:59Z)
-1.7500  (rev-is, 15:25:24Z)
--------
-6.1848  == campaign_end realized_pnl exactly
```

## Why nickja was not in it

`_sum_realized()` sums venue-confirmed REALIZED settlements **in the current
process's own report**. This process booted at **14:57:23Z** — the deploy that
shipped the risk-set audit. `nickja` realized at **14:00:46Z**, under the
previous process. Its −$3.1888 was never in scope for that sum.

So:

| figure | scope | value |
|---|---|---|
| `campaign_end.realized_pnl` | this process, 14:57:23Z → 16:19:01Z | **−$6.1848** |
| day, venue-confirmed | all processes, 2026-09-11 | **−$9.3736** |

## The defect this exposes, which is worth more than the reconciliation

**A redeploy silently restarts the realized accumulator**, so
`campaign_end.realized_pnl` UNDER-states the day whenever the process was
restarted — and under-stating a loss is the dangerous direction. The figure is
not wrong; it is unlabelled, and an unlabelled figure that looks like a daily
P&L will eventually be read as one.

Fixed by labelling rather than by changing the arithmetic: `realized_scope`
now travels beside `realized_pnl` at every emission site, saying in words that
it is per-process and that a daily figure needs the settlement rows summed
across processes. A test asserts both emission sites carry it.

*Not deployed — the owner has paused live deployments and the campaign is
stopped.*

## The day's venue-confirmed realized, in full

| market | realized | matched | residual | credited |
|---|---:|---:|---:|---:|
| `aec-atp-nickja-moeech-2026-09-11` | −$3.1888 | 5.60 | −8.32 | $0.0600 |
| `aec-cs2-rev-is-2026-09-11` | −$1.7500 | **0** | +5.00 | $0.0100 |
| `aec-itfwo-camrom-josest-2026-09-11` | −$4.4348 | **0** | −12.08 | $0.0300 |
| **total** | **−$9.3736** | | | **$0.1000** |

NOT realized, and kept separate per the owner's standing rule — resolved,
account flat, venue emitted no `POSITION_RESOLUTION` because it carried no
position, so `realized_usd` is null on both:

| market | matched | pair cost | DERIVED matched contribution | credited |
|---|---:|---:|---:|---:|
| `aec-cs2-wba-saw-2026-09-11` | 16.08 | 0.8954 | +$1.6820 *(derived)* | $0.1100 |
| `aec-cs2-1win-nemi-2026-09-11` | 5.15 | 0.9800 | +$0.1030 *(derived)* | $0.0400 |

Venue-credited maker rebates located today: **$0.2500** across 5 markets.

## What the three realized markets say about the strategy

**Two of the three had zero matched pairs.** `rev-is` bought 5.00 YES at 0.35
and never bought a single NO. `camrom` bought NO three times — 4.62 @ 0.55,
4.09 @ 0.63, 3.37 @ 0.75, each extend at a *worse* price — and never bought a
YES. Neither market was a pairing strategy that failed to pair; each was a
directional position from the first fill to settlement.

Together they lost **$6.18** against the **$0.10** of rebates the same markets
credited. That is the entry finding confirmed by venue cashflow rather than by
replay: the loss is created at entry, and the incentive stream is roughly two
orders of magnitude too small to matter beside it.
