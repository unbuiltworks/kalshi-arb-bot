# First autonomous settled cycle on the corrected engine

**`aec-cs2-mzp-ezt-2026-09-13` — resolved 17:19:52Z, reconciled 17:21:00Z.**
Build `f7a3e224`, deployment `86eb63ff`. **No human intervention in any
transition.**

## The twelve captures

### 1. Authoritative market resolution
`ACTIVITY_TYPE_POSITION_RESOLUTION`, `side: POSITION_RESOLUTION_SIDE_LONG`,
`updateTime 2026-09-13T17:19:52.410055715Z`.

### 2. Authenticated account position, before / after
`beforeNet -7.8800` → `afterNet 0.0000`; `position_net` read back as `0`.

The residual had *shrunk on its own before resolution*: the sweep read
`-9.6000` at 17:13:22 and `-7.8800` at 17:16:44. A COMPLETE filled in between
— the engine reduced its own exposure unaided.

### 3. Settlement auditor result
`status REALIZED`, `settled_by position_resolution_activity`,
`realized_confirmed true`, `settlement_key 2026-09-13T17:19:52.410055715Z`,
`reconciliation_version canonical-terminal/v1`,
`reconciled_at 2026-09-13T17:21:00.356586+00:00`.
Venue/ledger: `venue_qty_total 14.7600` vs `ledger_qty_total 14.76`.

### 4. Ledger writes
`['settled', 'realized']` — both, once, idempotent.

### 5. Directional residual settlement P&L
`residual_contribution_usd -2.907664310954063604240282686`
on `directional_residual -7.88`, residual cost basis `0.36899`.

### 6. Fees
`venue_unreported` — the venue publishes none on our fills. Not zero, unknown.

### 7. Confirmed rewards / rebates
**+$0.0400** credited, 4 of 9 fills carried a credit, source
`venue commissionNotionalCollected, our side only`. Kept out of trading P&L.

### 8. Realized net
**`realized_usd -2.7322`** — the venue's own number.

### 9. Risk-bearing set, before / after
```
17:20:21Z  n=1  matched_capital 3.2645  unmatched 2.9077  worst_case 2.9077
17:21:21Z  n=0  matched_capital 0       unmatched 0       worst_case 0
```
`daily_realized_pnl_usd 5.6734 → 2.9412`, and **5.6734 − 2.7322 = 2.9412
exactly**. The loss flowed into the daily total unaided;
`combined_loss_usd 2.9412` against the −$30.00 stop.

### 10. Was REARM gated?
**No.** The gate never refused after the cutover boot — the campaign was
continuously armed from 16:36:49Z onward.

### 11. Did self-healing REARM occur?
**No, and it could not have.** There was nothing to heal. **This requirement
remains UNPROVEN in production.** It is covered by 32 tests including a
200-cycle refusal negative, but it has not fired live.

### 12. Next autonomous acquisition
Risk released and the engine resumed seeking immediately:
`selection_skipped_position_held` stopped incrementing (frozen at 14) and the
funnel began counting `no_candidate` (8→12 over four cycles). It is refusing on
the strict-evidence gates — `thin_flow`, `no_q1_touch_depth`, `market_closed` —
which is the gates working, not a stall.

## The accounting reconciles to the cent

```
matched-pair P&L        +0.175464310954063604240282686   DERIVED
directional residual    -2.907664310954063604240282686   venue realized less the above
                        ------------------------------
sum                     -2.7322000000000000000000000000
venue realized_usd      -2.7322                          EXACT
```

Nine maker fills, zero taker. `bought_yes 3.44 @ 0.58`,
`bought_no 11.32 @ 0.36899`, cost basis `$6.1722`, `matched_pairs 3.44` at a
pair cost of **0.94899** — under par, as the ceiling requires.

## What this cycle proves, and what it does not

**PROVEN, live, unaided:**
POSITION HELD → RESOLVED → ACCOUNT RECONCILED → REALIZED → RISK RELEASED →
seeking next market.

**NOT proven:** self-healing REARM (never gated), and repeated settled cycles
(n=1).

## The economics, stated plainly

**This cycle lost $2.73.** The matched pair earned +$0.18 and the 7.88-contract
directional residual lost $2.91 when Metizport won. Per the standing
instruction the model is not being tuned on this outcome; it is one observation.

The residual is where the money went — again. That is the same finding as
jayfri (−$3.95) and the same one the forensic report made: completion is the
binding constraint, and the pair ceiling correctly refused to buy completion at
an above-par price.

## Post-cutover ledger

| | value |
|---|---|
| settled cycles | **1** |
| realized net | **−$2.7322** |
| matched-pair P&L | +$0.1755 (DERIVED) |
| directional residual P&L | −$2.9077 |
| fees | venue_unreported |
| confirmed rebates | +$0.0400 |
| completion rate | 1 of 5 COMPLETEs filled |
| peak unmatched exposure | $3.5062 |
| worst residual loss | −$2.9077 |
| accounting reconciliation | 1/1 exact |
| P0.3 divergence | 0 of 16 material decisions |
| autonomous rearm success | **n/a — never gated** |

The pre-cutover +$5.6734 remains separate and is not this build's.
