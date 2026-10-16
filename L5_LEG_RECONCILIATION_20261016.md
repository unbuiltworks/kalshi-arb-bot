# L5 — fresh side-aware reconciliation of every corrected leg

2026-10-16. Read-only. Produced with the FIXED `establish_basis`
(side-aware + venue cross-check) against the production audit's own
fills from deploy `119637c4`. **All 47 legs cross-check to a worst
per-fill gap of 0E-8** against the venue's fee-inclusive `cost`.

Ownership: **all 47 legs are `UNKNOWN_OWNER`** — not one is verified
L5 and not one is verified manual. That classification is unchanged.

`difference` = ledger_now − venue-derived. Positive = the ledger
holds too much cost, so realized is too NEGATIVE. Negative = the
ledger holds too little, so realized is FLATTERED.

| leg | side | qty | state | old basis | ledger now | venue-derived | fees | difference |
|---|---|---|---|---|---|---|---|---|
| `cpc-btc-updown-15m-2026-10-14-2230z|UP` | UP | 2.90 | REFUSED | — | 0.590000 | 0.59000000 | 0.0000 | **0E-8** |
| `cpc-btc-updown-15m-2026-10-15-1915z|DOWN` | DOWN | 1.0000 | APPLIED | 1.0000 | 0.88000000 | 0.12000000 | 0.0000 | **0.76000000** |
| `cpc-btc-updown-15m-2026-10-15-1945z|DOWN` | DOWN | 1.0000 | REFUSED | — | 1.4600 | 0.49000000 | 0.0000 | **0.97000000** |
| `cpc-btc-updown-15m-2026-10-15-2215z|DOWN` | DOWN | 1.0000 | APPLIED | 1.0000 | 0.91000000 | 0.09000000 | 0.0000 | **0.82000000** |
| `cpc-btc-updown-15m-2026-10-15-2230z|DOWN` | DOWN | 1.2700 | APPLIED | 1.2700 | 0.29210000 | 0.97790000 | 0.0200 | **-0.68580000** |
| `cpc-btc-updown-15m-2026-10-15-2245z|DOWN` | DOWN | 3.8600 | APPLIED | 3.8600 | 0.91720000 | 2.94280000 | 0.0500 | **-2.02560000** |
| `cpc-btc-updown-15m-2026-10-15-2330z|DOWN` | DOWN | 1.2600 | APPLIED | 1.2600 | 0.27720000 | 0.98280000 | 0.0200 | **-0.70560000** |
| `cpc-btc-updown-15m-2026-10-16-0000z|DOWN` | DOWN | 31.3300 | APPLIED | 31.3300 | 30.39010000 | 0.93990000 | 0.0600 | **29.45020000** |
| `cpc-btc-updown-15m-2026-10-16-0100z|UP` | UP | 3.5100 | APPLIED | 3.5100 | 0.94770000 | 0.94770000 | 0.0500 | **0E-8** |
| `cpc-btc-updown-15m-2026-10-16-0115z|UP` | UP | 1.0900 | APPLIED | 1.0900 | 0.47960000 | 0.47960000 | 0.0200 | **0E-8** |
| `cpc-btc-updown-15m-2026-10-16-0130z|UP` | UP | 1.2800 | APPLIED | 1.2800 | 0.97280000 | 0.97280000 | 0.0200 | **0E-8** |
| `cpc-btc-updown-15m-2026-10-16-0145z|UP` | UP | 3.3900 | APPLIED | 3.3900 | 0.94920000 | 0.94920000 | 0.0500 | **0E-8** |
| `cpc-btc-updown-15m-2026-10-16-0200z|DOWN` | DOWN | 2.1200 | APPLIED | 2.1200 | 1.16600000 | 0.95400000 | 0.0400 | **0.21200000** |
| `cpc-btc-updown-15m-2026-10-16-0215z|DOWN` | DOWN | 2.5800 | APPLIED | 2.5800 | 0.61920000 | 1.96080000 | 0.0300 | **-1.34160000** |
| `cpc-btc-updown-15m-2026-10-16-0330z|UP` | UP | 2.0000 | APPLIED | 2.0000 | 0.16000000 | 0.16000000 | 0.0000 | **0E-8** |
| `cpc-btc-updown-15m-2026-10-16-1230z|UP` | UP | 5.0000 | APPLIED | 5.0000 | 0.65000000 | 0.65000000 | -0.0100 | **0E-8** |
| `cpceth-eth-updown-15m-2026-10-15-2230z|DOWN` | DOWN | 1.5300 | APPLIED | 1.5300 | 0.56610000 | 0.96390000 | 0.0200 | **-0.39780000** |
| `cpceth-eth-updown-15m-2026-10-15-2330z|DOWN` | DOWN | 1.6100 | APPLIED | 1.6100 | 0.64400000 | 0.96600000 | 0.0300 | **-0.32200000** |
| `cpceth-eth-updown-15m-2026-10-15-2345z|DOWN` | DOWN | 15.6600 | APPLIED | 15.6600 | 14.72040000 | 0.93960000 | 0.0600 | **13.78080000** |
| `cpceth-eth-updown-15m-2026-10-16-0100z|UP` | UP | 1.5800 | APPLIED | 1.5800 | 0.96380000 | 0.96380000 | 0.0300 | **0E-8** |
| `cpceth-eth-updown-15m-2026-10-16-0115z|UP` | UP | 1.5900 | APPLIED | 1.5900 | 0.47700000 | 0.47700000 | 0.0200 | **0E-8** |
| `cpceth-eth-updown-15m-2026-10-16-0130z|UP` | UP | 1.1100 | APPLIED | 1.1100 | 0.98790000 | 0.98790000 | 0.0100 | **0E-8** |
| `cpceth-eth-updown-15m-2026-10-16-0145z|DOWN` | DOWN | 2.1300 | APPLIED | 2.1300 | 1.17150000 | 0.95850000 | 0.0400 | **0.21300000** |
| `cpceth-eth-updown-15m-2026-10-16-0200z|DOWN` | DOWN | 2.5100 | APPLIED | 2.5100 | 1.55620000 | 0.95380000 | 0.0400 | **0.60240000** |
| `cpceth-eth-updown-15m-2026-10-16-0215z|UP` | UP | 3.2400 | APPLIED | 3.2400 | 1.94400000 | 1.94400000 | 0.0500 | **0E-8** |
| `cpceth-eth-updown-15m-2026-10-16-0330z|UP` | UP | 50.0000 | APPLIED | 50.0000 | 1.00000000 | 1.00000000 | -0.0100 | **0E-8** |
| `cpceth-eth-updown-15m-2026-10-16-1230z|UP` | UP | 5.0000 | APPLIED | 5.0000 | 0.90000000 | 0.90000000 | -0.0100 | **0E-8** |
| `cpcsol-sol-updown-15m-2026-10-15-2230z|DOWN` | DOWN | 1.5500 | APPLIED | 1.5500 | 0.57350000 | 0.97650000 | 0.0300 | **-0.40300000** |
| `cpcsol-sol-updown-15m-2026-10-15-2330z|UP` | UP | 1.2400 | APPLIED | 1.2400 | 0.96720000 | 0.96720000 | 0.0100 | **0E-8** |
| `cpcsol-sol-updown-15m-2026-10-16-0000z|UP` | UP | 1.6800 | APPLIED | 1.6800 | 0.97440000 | 0.97440000 | 0.0300 | **0E-8** |
| `cpcsol-sol-updown-15m-2026-10-16-0100z|DOWN` | DOWN | 1.7300 | APPLIED | 1.7300 | 0.76120000 | 0.96880000 | 0.0300 | **-0.20760000** |
| `cpcsol-sol-updown-15m-2026-10-16-0115z|UP` | UP | 1.4100 | APPLIED | 1.4100 | 0.47940000 | 0.47940000 | 0.0200 | **0E-8** |
| `cpcsol-sol-updown-15m-2026-10-16-0130z|UP` | UP | 1.7500 | APPLIED | 1.7500 | 0.96250000 | 0.96250000 | 0.0300 | **0E-8** |
| `cpcsol-sol-updown-15m-2026-10-16-0145z|UP` | UP | 2.2300 | APPLIED | 2.2300 | 0.95890000 | 0.95890000 | 0.0400 | **0E-8** |
| `cpcsol-sol-updown-15m-2026-10-16-0200z|UP` | UP | 1.9700 | APPLIED | 1.9700 | 0.96530000 | 0.96530000 | 0.0300 | **0E-8** |
| `cpcsol-sol-updown-15m-2026-10-16-0215z|DOWN` | DOWN | 3.3000 | APPLIED | 3.3000 | 0.33000000 | 2.97000000 | 0.0200 | **-2.64000000** |
| `cpcsol-sol-updown-15m-2026-10-16-0330z|UP` | UP | 50.0000 | APPLIED | 50.0000 | 1.50000000 | 1.50000000 | 0.0100 | **0E-8** |
| `cpcsol-sol-updown-15m-2026-10-16-1230z|UP` | UP | 5.0000 | APPLIED | 5.0000 | 0.40000000 | 0.40000000 | 0.0000 | **0E-8** |
| `cpcxrp-xrp-updown-15m-2026-10-15-2230z|DOWN` | DOWN | 1.2500 | APPLIED | 1.2500 | 0.26250000 | 0.98750000 | 0.0100 | **-0.72500000** |
| `cpcxrp-xrp-updown-15m-2026-10-15-2330z|DOWN` | DOWN | 2.8100 | APPLIED | 2.8100 | 0.87450000 | 1.93550000 | 0.0400 | **-1.06100000** |
| `cpcxrp-xrp-updown-15m-2026-10-16-0100z|DOWN` | DOWN | 1.3200 | APPLIED | 1.3200 | 0.34320000 | 0.97680000 | 0.0100 | **-0.63360000** |
| `cpcxrp-xrp-updown-15m-2026-10-16-0115z|UP` | UP | 1.9900 | APPLIED | 1.9900 | 0.47760000 | 0.47760000 | 0.0300 | **0E-8** |
| `cpcxrp-xrp-updown-15m-2026-10-16-0130z|UP` | UP | 1.4600 | APPLIED | 1.4600 | 0.97820000 | 0.97820000 | 0.0200 | **0E-8** |
| `cpcxrp-xrp-updown-15m-2026-10-16-0145z|DOWN` | DOWN | 2.3100 | APPLIED | 2.3100 | 1.35360000 | 0.95640000 | 0.0400 | **0.39720000** |
| `cpcxrp-xrp-updown-15m-2026-10-16-0200z|DOWN` | DOWN | 2.2200 | APPLIED | 2.2200 | 1.26540000 | 0.95460000 | 0.0400 | **0.31080000** |
| `cpcxrp-xrp-updown-15m-2026-10-16-0215z|DOWN` | DOWN | 5.2800 | APPLIED | 5.2800 | 0.31680000 | 4.96320000 | 0.0200 | **-4.64640000** |
| `cpcxrp-xrp-updown-15m-2026-10-16-0330z|UP` | UP | 50.0000 | REFUSED | — | 37.975400 | 1.00000000 | 0.0500 | **36.97540000** |

| | |
|---|---|
| legs reconciled | **47** (44 applied + 3 refused) |
| basis established, cross-check passed | **47 of 47** |
| UP legs needing no change | **22** (difference 0E-8 on every one) |
| DOWN legs wrong | **22 of 22** |
| net difference | **68.69680000** |
| of which ledger too negative (conservative) | **84.49180000** |
| of which realized FLATTERED | **-15.79500000** (13 legs) |

## The two disputed legs, separately

### `cpcxrp-xrp-updown-15m-2026-10-16-0330z|UP` — CERTIFIABLE
Settlement record (`L5_LIVE_SETTLED`, 2026-10-16T03:48:49.665Z):
`up_qty 50.0000`, `up_cost 37.975400`, `dn_qty 0`, `dn_cost 0`,
`cost_total 37.975400`, `redemption_total 0E-8`,
`settlement_pnl -37.97540000` — **single-sided**, so `cost_total` is this
leg's own cost and no other leg can be erased by correcting it.

Venue fills: 5, all at YES 0.0200, `31.73 + 10.19 + 5.00 + 2.08 + 1.00`
= **50.0000** = `up_qty`. Cost **1.0000**, fees **0.0500**, venue
fee-inclusive `cost` **1.0500**, cross-check gap **0**.

**Four independent readings all say ~$1, and the ledger's 37.975400 is
supported by none of them:**
1. the five fills: 50 × 0.0200 = 1.0000;
2. the venue's own `cost` field: 1.0500 fee-inclusive;
3. the executor's own `committed_released: "1.000000"` on that settlement
   — it only ever committed a dollar of cash to this market;
4. account cash: baseline 350.05255 → 344.41255 = −5.64000, against
   ledger post-baseline settlements of −42.58540; restating this leg
   reconciles the two to within ~$0.03.

Verdict: **bookkeeping defect.** In the manifest.

### `cpc-btc-updown-15m-2026-10-15-1945z|DOWN` — MEASURED, NOT APPLICABLE
Settlement record (`L5_LIVE_SETTLED`, 2026-10-15T20:03:03.085Z):
`up_qty 1`, `up_cost 0.4600`, `dn_qty 1.0000`, `dn_cost 1.0000`,
`matched_qty 1`, `matched_cost 1.4600`, `redemption_total 1.00000000`,
`settlement_pnl -0.46000000`.

So `cost_total 1.4600` is the **PAIR**: a real UP cost of 0.4600 plus the
DOWN leg's $1/share placeholder. The DOWN leg's own measured cost is
**0.4900** (1.0000 at YES 0.5100, venue `cost` 0.49, gap 0), so the
legitimate restatement is `dn_cost 1.0000 → 0.4900`, delta **+0.51**, and
`cost_total` should become **0.9500** — never 0.4900.

`correct_cost_basis` writes the market-level `cost_total` and its measured
cost must re-derive from the named trade ids, so it **cannot express a
per-leg correction on a two-sided market**. The plan now refuses this leg
`MARKET_TWO_SIDED_COST_TOTAL` and preserves it as UNKNOWN. **Excluded from
the manifest.** Fixing it needs a per-leg (`up_cost`/`dn_cost`) correction
capability that does not exist today — a separate owner decision.

### `cpc-btc-updown-15m-2026-10-14-2230z|UP` — NOTHING DUE
Row holds `0.590000`; measured `0.59000000`. Difference 0. Preserved
`NOTHING_TO_CORRECT`. Its 2.31 was never in realized as a placeholder.

## Why the 44 cannot be restated through the existing verb
Every one of their rows now carries `cost_superseded`, so guard 3
(`COST_ALREADY_CORRECTED`) refuses all 44 — and for the 13 flattered legs
the correct cost is HIGHER than what the ledger now holds, which guard 8
(`COST_CORRECTION_MUST_REDUCE`) refuses by design. The one-directional
guard is right; it simply means an upward restatement is a capability that
does not exist. **Owner decision, not a manifest.**

## The manifest
`config/cost_basis_batch_manifest_20261016b.json` — 1 leg, 5 trade ids,
`realized_delta_usd 36.97540000`. `check_batch` returns `ok: true` against
the plan it was built from. **NOT APPLIED**: `L5_COST_BASIS_APPLY` stays at
the spent `cb-20261016-47leg-209.6638` and no new batch id is set.
