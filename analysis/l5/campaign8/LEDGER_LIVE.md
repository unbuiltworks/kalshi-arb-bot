# Campaign 8 — live fill ledger, windows 0200z–0230z

Reconciled 2026-10-29 ~02:52Z while the campaign was still armed. Every line is
tied to `L5_LIVE_CASH.available_cash_usd` (`/v1/account/balances`), which is the
authoritative series. Baseline `342.01615` at 02:03:10Z.

## Price convention

The wire is YES/UP. `own(UP) = wire`, `own(DOWN) = 1 - wire`. The
`fill_own_px` field in `L5_FILL_BOOK_EVIDENCE` is **mislabeled** — it carries
the *wire* price, not the own-side price. This was already established in
Campaign 7 and holds unchanged here. All `own_cost` figures below are derived,
not read.

Matched pairs are released to cash **on match**, within one 60s balance refresh
— not at window settlement. (The `campaign_budget` label "Settlement releases
exposure; matching does not" describes the gate's conservative *exposure*
accounting, not the cash mechanics.) Only *unmatched* legs wait for settlement.

## Fills

| # | utc | win | side | qty | wire | own | cost |
|---|------|------|------|------|------|------|------|
| 1 | 02:03:13 | 0200z | DOWN | 1.77 | 0.74 | 0.26 | 0.4602 |
| 2 | 02:03:50 | 0200z | UP | 1.77 | 0.67 | 0.67 | 1.1859 |
| 3 | 02:04:28 | 0200z | DOWN | 1.73 | 0.78 | 0.22 | 0.3806 |
| 4 | 02:04:56 | 0200z | UP | 1.73 | 0.68 | 0.68 | 1.1764 |
| 5 | 02:05:37 | 0200z | UP | 2.06 | 0.45 | 0.45 | 0.9270 |
| 6 | 02:06:01 | 0200z | DOWN | 2.06 | 0.35 | 0.65 | 1.3390 |
| 7 | 02:06:35 | 0200z | UP | 2.17 | 0.38 | 0.38 | 0.8246 |
| 8 | 02:08:27 | 0200z | DOWN | 2.17 | 0.52 | 0.48 | 1.0416 |
| 9 | 02:15:52 | 0215z | UP | 2.12 | 0.41 | 0.41 | 0.8692 |
| 10 | 02:16:50 | 0215z | DOWN | 2.12 | 0.44 | 0.56 | 1.1872 |
| 11 | 02:22:22 | 0215z | DOWN | 1.94 | 0.59 | 0.41 | 0.7954 |
| 12 | 02:30:52 | 0230z | UP | 2.18 | 0.37 | 0.37 | 0.8066 |
| 13 | 02:31:23 | 0230z | DOWN | 2.18 | 0.40 | 0.60 | 1.3080 |
| 14 | 02:32:05 | 0230z | UP | 2.12 | 0.41 | 0.41 | 0.8692 |
| 15 | 02:33:18 | 0230z | DOWN | 2.12 | 0.40 | 0.60 | 1.2720 |
| 16 | 02:35:55 | 0230z | UP | 2.47 | 0.21 | 0.21 | 0.5187 |
| 17 | 02:36:29 | 0230z | DOWN | 1.45 | 0.24 | 0.76 | 1.1020 |

Sides for #1-#8, #11 and #12 are derived from the cash deltas (the
`L5_FILL_BOOK_EVIDENCE` window had already rolled off for those); #13-#17 carry
an explicit `side` field and agree.

## Completed pairs

| pair | win | legs | own costs | pair cost | matched qty | net |
|------|------|------|-----------|-----------|-------------|-----|
| A | 0200z | 1+2 | 0.26 + 0.67 | **0.93** | 1.77 | +0.1239 |
| B | 0200z | 3+4 | 0.22 + 0.68 | **0.90** | 1.73 | +0.1730 |
| C | 0200z | 5+6 | 0.45 + 0.65 | **1.10** | 2.06 | **-0.2060** |
| D | 0200z | 7+8 | 0.38 + 0.48 | **0.86** | 2.17 | +0.3038 |
| E | 0215z | 9+10 | 0.41 + 0.56 | **0.97** | 2.12 | +0.0636 |
| F | 0230z | 12+13 | 0.37 + 0.60 | **0.97** | 2.18 | +0.0654 |
| G | 0230z | 14+15 | 0.41 + 0.60 | **1.01** | 2.12 | **-0.0212** |
| H | 0230z | 16+17 | 0.21 + 0.76 | **0.97** | 1.45 | +0.0435 |

Matched contracts 15.60. **Matched-pair economics = +$0.3460.**
Mean pair cost 0.965; two of eight priced above $1.00.

## Unmatched legs (settlement coin-flips)

| leg | win | side | qty | own | cost | settled | net |
|-----|------|------|-----|-----|------|---------|-----|
| #11 | 0215z | DOWN | 1.94 | 0.41 | 0.7954 | **WON** +1.94 | **+1.1446** |
| #16 remainder | 0230z | UP | 1.02 | 0.21 | 0.2142 | **LOST** 0 | **-0.2142** |

**Residual settlement = +$0.9304** on $1.0096 of cost at risk.

## Reconciliation

```
matched-pair economics      +0.3460
residual settlement         +0.9304
                            -------
total                       +1.4764
```

Independently confirmed twice:
- cash plateau `343.49255 - 342.01615 = +1.47640` (held 02:37:18 through 02:46:38)
- heartbeat `utc_day_realized_usd = "1.476400"`

Every intermediate cash reading is reproduced exactly by the fill sequence
above; there is no unexplained residue.

## Reading

**63% of the realized net is one stranded leg that won a coin flip**, not
matched-pair edge. Matched economics alone are +$0.3460 over four windows
(+$0.0865/window). The two above-$1.00 pairs cost -$0.2272 — guaranteed matched
losses, both admitted through the `completion_pair_rescue` path because
`PAIR_COST_ABOVE_CEILING` tests the *cumulative average* `eff = cost_after /
matched_after` rather than the marginal pair.

Stranding appeared in 2 of 4 windows, which is the behaviour the Campaign 7
dwell-time finding predicted (quotes rest a median 8s while fills arrive at a
median 11s; 17% quote-time coverage).
