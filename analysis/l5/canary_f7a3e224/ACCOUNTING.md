# POST-CUTOVER CANARY accounting — `f7a3e224`, deployment `86eb63ff`

**Cutover instant: 2026-09-13T16:33:43Z** (container up 16:36:32Z).
Everything before that instant is **PRE-CUTOVER** and belongs to `8606c87f`.
Nothing below blends the two.

## Provenance rule for the +$5.6734

`daily_realized_pnl_usd = 5.6734` appears on every post-cutover `risk_set` row.
**It is entirely pre-cutover.** All ten of today's terminal markets reached
their terminal state before 16:33:43Z; the field is a UTC-day ledger sum, not a
build-scoped one. It is **not** evidence about `f7a3e224` and must never be
quoted as the corrected build's expectancy.

| | markets | venue-confirmed realized | derived matched contribution |
|---|---|---|---|
| PRE-cutover (`8606c87f`, UTC day) | 10 | **+$5.6734** | +$1.4380 (DERIVED, never realized) |
| **POST-cutover (`f7a3e224`)** | **0** | **$0.00** | — |

## The strict chain, post-cutover only

| stage | count | detail |
|---|---|---|
| SUBMITTED | 12+ | 4 open (lol-lll-png), 4 extend + 2 complete + 2 extend (mzp-ezt) |
| FILLED | **1** | `CFHSWQKHYTN1` NO @ 0.31 × 1.49, `filled_on_cancel`, 16:39:56Z |
| MATCHED | **0** | no complement has filled |
| SETTLED | **0** | — |
| REALIZED | **0** | — |

## The five streams, kept separate

| stream | post-cutover value | source |
|---|---|---|
| matched-pair P&L | **$0.00** | no matched pairs exist yet |
| directional residual P&L | **unrealized** | −9.60 NO, cost basis $3.5062, avg 0.36523 |
| fees | `venue_unreported` | the venue publishes none on our fills |
| confirmed rebates / rewards | **$0.00** | nothing credited post-cutover |
| **net realized P&L** | **$0.00** | nothing has settled |

`total_unmatched_usd 3.5062` = `portfolio_worst_case_usd` — every residual
worthless, matched pairs still pay. `combined_loss_usd 2.1672` against the
**−$30.00** stop.

## Open position

`aec-cs2-mzp-ezt-2026-09-13` — 9.60 NO contracts, $3.5062 cost basis, 0 matched,
residual −9.60. Venue 9.6000 == ledger 9.60, delta 0.0000, `agrees: true`.
Dormant under quarantine, so `acquisition_slots_in_use: 0` while
`total_risk_bearing_markets: 1`.

## Loop milestones toward the scaling gate

| requirement | status |
|---|---|
| correct P0.3 inventory mapping throughout | **MET** — 10 decisions, no false-flat, no sign inversion |
| automatic next-market acquisition | **MET** — `aec-lol-lll-png` opened 16:37:14Z while mzp-ezt dormant |
| no duplicate orders / accounting divergence | **holding** — venue==ledger on every check; orphan sweep 0 |
| at least one automatic settlement / reconciliation | **NOT YET** |
| successful self-healing REARM | **NOT YET** — the gate has not refused since cutover |
| repeated settled cycles | **NOT YET** |

**Three of six met. The scaling proposal is gated on the remaining three and
is not being drafted with numbers until they arrive.**

## Standing labels

Nothing here is realized. `SUBMITTED → FILLED` is where the post-cutover run
currently stands, and the one fill is labelled exactly that.
