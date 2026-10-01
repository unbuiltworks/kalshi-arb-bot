# CANONICAL L5 FINANCIAL BENCHMARK — frozen at 1f43db3b

**This is the authoritative financial benchmark for the captured 12-day
population (2026-09-01..10 and 09-22..23).** It supersedes every earlier
headline. It may be superseded only by explicit source evidence, never by a
replay, a model or an estimate.

## Settlement coverage

| | |
|---|---:|
| conditionIds fetched | **4,766 / 4,766 (100%)** |
| not-found | 0 |
| errors | 0 |
| UNRESOLVED | **0** |
| INVALID_AMBIGUOUS | **0** |

Source: CLOB market record per `conditionId`, per-token `winner` flag. Raw
responses with url/status/`fetched_at` in `data/l5/settlement/sep_resolutions.jsonl`.

## Zero-redemption audit

| | markets | spend |
|---|---:|---:|
| RESOLVED_LOSS (proven) | **825** | **$14,902.20** |
| REDEMPTION_NOT_CAPTURED | 12 | $433.40 |
| genuinely unresolved | **0** | $0.00 |

## Authoritative economics

| line | value |
|---|---:|
| **Trading P&L** | **−$6,338.70** |
| spend | $1,831,143.99 |
| captured redemption | $1,824,805.29 |
| maker incentives | +$13,565.72 |
| liquidity REWARD | +$8,229.66 |
| taker incentives | +$1,252.02 |
| **total incentives** | **+$23,047.40** |
| **total realized** | **+$16,708.70** |
| trading rate | **−34.6 bps** |

**Qualification, preserved not papered over:** the 12 proven winners with
uncaptured redemption mean true trading P&L is slightly **better** than
−$6,338.70. Their redemption cash is **not invented**; they are excluded and
listed.

## Regime split (market-attributed by each market's own window epoch)

| regime | markets | trading | rate | net after incentives |
|---|---:|---:|---:|---:|
| **A** Sep 01–06, REWARD live | 2,862 | −$903.78 | **−9.2 bps** | +$14,466.61 |
| **B** Sep 07–10, REWARD gone | 1,349 | −$4,173.79 | **−71.6 bps** | +$945.96 |
| **C** Sep 22–23 | 543 | −$1,261.12 | −47.3 bps | +$1,296.16 |

Regime B independently reproduces the prior result (~−$4,175 trading, ~+$944 net,
~−0.72%) from raw records by a different route.

## THE CENTRAL CONCLUSION, SOURCE-ESTABLISHED

> **L5's trading was NEGATIVE over this captured population. Venue incentives made
> the business profitable.**

No longer an inference from a replay.

## SUPERSEDED headlines — evidence retained, differences explained

| figure | status | why it differs |
|---|---|---|
| **+$7,125.74** | **SUPERSEDED** | filtered on `redeem_qty > 0`, excluding 708 zero-redemption markets **by construction**; every one is now proven a loss. Wrong by ~$13,464. |
| **−$5,047.88** | **SUPERSEDED** | correct method, different population (4,217 markets, "resolved ≥10 min pre-harvest") and a different capture. |
| **−$5,508.95** | **SUPERSEDED** | raw cashflow in the window: includes 19 orphan redemptions ($1,263.14, bought before capture) and the 12 winners' spend. Reconciles exactly: −5,508.95 + 433.39 − 1,263.14 = −6,338.70. |

Old documents are **not deleted**. Each now carries its correction inline.

## THE TEN ACCOUNTING LESSONS (binding on all future work)

1. **`usdcSize` is the cash field.** Never reconstruct cash from `price × size`
   where `usdcSize` exists — they disagree by 52.0 bps in aggregate.
2. **Deduplicate overlapping pagination** — 5.1% of rows repeat.
3. **Never infer winners from last price.**
4. **Missing redemption ≠ loss.** It is equally consistent with unresolved
   inventory or an uncaptured row.
5. **Use authoritative conditionId/token settlement.**
6. **Never allocate account-level incentives to markets** without source
   attribution; L5's incentive rows carry an empty `conditionId`.
7. **Keep unresolved inventory out of realized P&L.**
8. **Preserve Decimal/integer precision** as far as source fidelity permits — the
   source serves JSON floats, which is a ceiling, not a defect.
9. **Market and account totals are safer than arbitrary event-date buckets** —
   1.30% of redemption cash crosses a UTC day boundary from its purchase.
10. **Every unexplained reconciliation residual must fail loudly.**

Enforced by `src/l5_cash_ledger.py` and 21 mutation tests in
`tests/test_l5_cash_ledger.py`.
