# Capital efficiency

16 settled markets, **10.367019 dollar-hours** of capital consumed.

| | paired | stranded |
|---|---|---|
| markets | 12 | 4 |
| dollar-hours | 5.804634 | 4.562385 |
| median lock (s) | 40.1 | 633.3 |

**25.0% of markets consume 44.0% of the capital-hours.**

## Per bankroll-dollar-hour

Basis: reconciling settled markets only (15 markets, 10.320179 dollar-hours)

| quantity | value |
|---|---|
| completed pairs | 149.93 |
| completed pairs per dollar-hour | **14.5278** |
| realized $ per dollar-hour, pre-credit | **$-0.125646** |
| confirmed rebate attributable | $0.5525 |
| realized $ per dollar-hour, with confirmed credits | **$-0.072111** |

maker rebates only, mapped per fill and therefore approximate; liquidity rewards are $0.00 credited and enter as zero, never at accrued face value

## Turnover

- span **10.06 h**
- **38.18** settled markets/day
- **24.7405** dollar-hours/day
- mean capital in use **$1.0309**

## Opportunity cost

4 of 16 settled markets stranded residual and consumed 44.0% of every capital-hour the campaign spent. With concurrency 1 those hours are not merely idle capital -- they are the whole lane, because selection does not run while a position is held.

> L5 carries residual to settlement and so do we; the HOLD policy is not in question. This says completion rate is the same lever for capital efficiency as for P&L, not that stranded inventory should be dumped.

## Anchoring

Paired locks end when the residual reaches zero in the campaign log; stranded locks end at the venue's settled_utc. Reading both from the log would understate stranded capital by however long the tape ends early -- here about 25%.

## Per market

| market | paired | $ basis | lock s | anchor | $-hours |
|---|---|---|---|---|---|
| `aec-setkameua-malden-stoole-2026-09-08` | no | 19.3141 | 455.1 | venue settlement | 2.441738 |
| `atc-ekst-psz-plo-2026-09-07-draw` | yes | 12.043 | 703.1 | residual reached zero (log) | 2.352115 |
| `aec-setkamemd-ghifio-butdum-2026-09-08` | yes | 10.1423 | 438.6 | residual reached zero (log) | 1.235704 |
| `atc-sea-udi-laz-2026-09-07-udi` | yes | 8.3187 | 397.9 | residual reached zero (log) | 0.919409 |
| `aec-setkameua-naiole-khaser-2026-09-07` | yes | 9.5375 | 310.8 | residual reached zero (log) | 0.823363 |
| `aec-setkamecz-tomvoj-ticjir-2026-09-07` | no | 7.9878 | 365.0 | venue settlement | 0.809782 |
| `tsc-mlb-laa-bos-2026-09-07-7pt5` | no | 3.1213 | 811.5 | venue settlement | 0.703559 |
| `aec-setkamemd-scovla-otgand-2026-09-07` | no | 1.8252 | 1197.8 | venue settlement | 0.607306 |
| `aec-wta-anatik-darsem-2026-09-07` | yes | 8.286 | 69.8 | residual reached zero (log) | 0.160669 |
| `aec-atp-karkha-leatie-2026-09-07` | yes | 10.80345 | 41.9 | residual reached zero (log) | 0.125796 |
| `aec-mlb-az-kc-2026-09-07` | yes | 15.37755 | 16.7 | residual reached zero (log) | 0.07147 |
| `aec-setkameua-vervas-kohboh-2026-09-08` | yes | 4.4082 | 38.3 | residual reached zero (log) | 0.04684 |
| `aec-setkameua-malden-pytiva-2026-09-07` | yes | 16.422 | 9.2 | residual reached zero (log) | 0.042068 |
| `aec-atp-artgea-botzan-2026-09-06` | yes | 13.23535 | 3.4 | residual reached zero (log) | 0.012412 |
| `aec-setkamemd-salvea-dupale-2026-09-07` | yes | 10.6036 | 3.4 | residual reached zero (log) | 0.010115 |
| `aec-atp-fracer-aleblo-2026-09-06` | yes | 4.20885 | 4.0 | residual reached zero (log) | 0.004673 |
