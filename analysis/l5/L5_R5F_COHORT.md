# R5F settled cohort — economics by evidence authority

_#167 comment 5577286614 items 2 and 7. Cohort = markets settled at or after 2026-09-07T19:21:39Z (R5F approval consumed). Read-only; produced from the probe's own settlement rows._

**Settled markets in cohort: 15** — 4 venue-confirmed, 11 locked at zero residual, 0 indeterminate, 0 pending.

- **Venue-confirmed realized: -9.2384**
- Derived, locked, pre-fee: 7.9417
- Indicative combined: -1.2967 — INDICATIVE ONLY -- mixes venue-confirmed realized with derived pre-fee figures. It is not a realized number and must never be reported as one.

## Pairing split

| outcome | markets | sum $ | mean per market | 95% CI |
|---|---:|---:|---:|---|
| paired to zero residual | 11 | 7.9417 | 0.722 | [0.2015, 1.4983] |
| left directional residual | 4 | -9.2384 | -2.3096 | [-3.4996, -1.0909] |

_the split is descriptive, not causal: markets that pair to zero and markets that strand residual are not randomly assigned._

## Per market

| settled | market | class | matched pairs | pair cost | residual | value $ |
|---|---|---|---:|---:|---:|---:|
| 20:02:54 | aec-setkamecz-tomvoj-ticjir-2026-09-07 | VENUE_CONFIRMED | 4.11 | 1.110705 | 10.35 | -3.8778 |
| 20:24:23 | tsc-mlb-laa-bos-2026-09-07-7pt5 | VENUE_CONFIRMED | 0 | None | 9.62 | -3.1213 |
| 20:31:36 | aec-setkamemd-salvea-dupale-2026-09-07 | LOCKED_ZERO_RESIDUAL | 10.82 | 0.980000 | 0.00 | 0.2164 |
| 20:57:06 | atc-ekst-psz-plo-2026-09-07-draw | LOCKED_ZERO_RESIDUAL | 13.64 | 0.882918 | 0.00 | 1.5970 |
| 20:57:06 | atc-sea-udi-laz-2026-09-07-udi | LOCKED_ZERO_RESIDUAL | 8.32 | 0.999844 | 0.00 | 0.0013 |
| 20:57:06 | aec-setkameua-malden-pytiva-2026-09-07 | LOCKED_ZERO_RESIDUAL | 16.7 | 0.983353 | 0.0 | 0.2780 |
| 20:58:35 | aec-setkameua-naiole-khaser-2026-09-07 | LOCKED_ZERO_RESIDUAL | 9.58 | 0.995564 | 0.00 | 0.0425 |
| 20:58:36 | aec-setkamemd-scovla-otgand-2026-09-07 | VENUE_CONFIRMED | 0 | None | 5.07 | -1.8252 |
| 00:33:24 | aec-wta-anatik-darsem-2026-09-07 | LOCKED_ZERO_RESIDUAL | 8.7 | 0.952414 | 0.0 | 0.4140 |
| 00:33:24 | aec-atp-fracer-aleblo-2026-09-06 | LOCKED_ZERO_RESIDUAL | 4.23 | 0.995000 | 0.00 | 0.0212 |
| 00:33:24 | aec-mlb-az-kc-2026-09-07 | LOCKED_ZERO_RESIDUAL | 15.84 | 0.970805 | 0.00 | 0.4624 |
| 01:13:01 | aec-setkameua-malden-stoole-2026-09-08 | VENUE_CONFIRMED | 18.90 | 0.942128 | 4.76 | -0.4141 |
| 02:04:18 | aec-atp-karkha-leatie-2026-09-07 | LOCKED_ZERO_RESIDUAL | 11.16 | 0.968051 | 0.00 | 0.3566 |
| 02:22:34 | aec-atp-artgea-botzan-2026-09-06 | LOCKED_ZERO_RESIDUAL | 13.61 | 0.972472 | 0.00 | 0.3746 |
| 02:22:36 | aec-setkamemd-ghifio-butdum-2026-09-08 | LOCKED_ZERO_RESIDUAL | 14.32 | 0.708261 | 0.00 | 4.1777 |

## Fees and incentives

- venue_unreported on every settlement row; every derived figure here is PRE-FEE
- maker rebates are credited separately by the venue and are NOT included in any figure above

_Venue-confirmed realized comes from the venue only. Locked figures are DERIVED from venue-confirmed fills and are exact ONLY because a zero-residual market pays 1.00 per matched pair regardless of the result; they are PRE-FEE (the venue reports fees_usd as venue_unreported) and exclude separately credited maker rebates. Confirmed and derived are never summed into a single 'realized'._
