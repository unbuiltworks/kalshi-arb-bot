# Authoritative September realized P&L. The join is closed.

Settlement read per `conditionId` from the CLOB market record's per-token
`winner` flag. **4,766 of 4,766 fetched (100%), 0 not-found, 0 errors.** Raw
responses preserved with url, status and `fetched_at` in
`data/l5/settlement/sep_resolutions.jsonl`. Nothing inferred from price, last
trade, redemption absence, slug text or timing.

## 1. Classification coverage

| class | markets |
|---|---:|
| RESOLVED_WIN | 3,941 |
| RESOLVED_LOSS | 825 |
| UNRESOLVED | **0** |
| INVALID_AMBIGUOUS | **0** |

## 2–4. The 837 zero-redemption markets

| class | markets | spend |
|---|---:|---:|
| **RESOLVED_LOSS — proven** | **825** | **$14,902.20** |
| REDEMPTION_NOT_CAPTURED | 12 | $433.40 |
| **still unresolved** | **0** | **$0.00** |

**97.2% of the disputed spend is now proven loss from the venue's own statement.
Nothing remains unresolved.**

The 12 are markets the venue says L5 **won** that carry no redemption row in this
capture — 4 where he held both sides, 8 where he held only the winner, the largest
($291.37) sitting on the Sep-10 capture boundary. They are **excluded**, because
scoring them `redeem − spend` books a proven winner as a total loss.

## 5. Authoritative realized trading P&L

| | |
|---|---:|
| markets included | 4,754 |
| markets excluded | 12 ($433.40) |
| spend | $1,831,143.99 |
| redeem | $1,824,805.29 |
| **REALIZED TRADING P&L** | **−$6,338.70** |

## 6–7. Incentives and total

| line | amount |
|---|---:|
| MAKER_REBATE | +$13,565.7210 |
| REWARD | +$8,229.6599 |
| TAKER_REBATE | +$1,252.0235 |
| **incentives total** | **+$23,047.4044** |
| **TOTAL REALIZED** | **+$16,708.70** |

Incentives stay account-level: every row carries an empty `conditionId`, so
per-market allocation is not published and is not invented.

## 8. Regime split, market-attributed

Each market assigned by **its own window epoch**, not by event dates, so the
1.30% cross-day redemption effect cannot distort it. 0 markets unattributable.

| regime | markets | spend | redeem | trading | losses | bps | incentives | net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A Sep 01–06 (reward live) | 2,862 | 981,844.63 | 980,940.85 | **−903.78** | 403 | **−9.2** | +15,370.39 | +14,466.61 |
| B Sep 07–10 (reward gone) | 1,349 | 582,729.02 | 578,555.22 | **−4,173.79** | 299 | **−71.6** | +5,119.75 | **+945.96** |
| C Sep 22–23 | 543 | 266,570.34 | 265,309.22 | −1,261.12 | 123 | −47.3 | +2,557.28 | +1,296.16 |
| **total** | **4,754** | | | **−6,338.70** | **825** | | **+23,047.40** | **+16,708.70** |

**Regime B independently confirms the owner's figures**: trading −$4,173.79
against −$4,175 stated, net +$945.96 against +$944, and −71.6 bps against the
−0.72% of turnover stated. Arrived at from raw records by a different route.

**The deterioration is now authoritative, not estimated**: trading cost worsened
**7.8×**, from −9.2 bps while the reward line was live to −71.6 bps after it
stopped.

## 9. Reconciliation residual: zero, every difference named

Cashflow ledger **−$5,508.95** → authoritative **−$6,338.70**:

| step | amount |
|---|---:|
| cashflow ledger trading net | −5,508.95 |
| **+** spend of the 12 excluded winners | +433.39 |
| **−** redemption cash with no purchase in window (19 markets) | −1,263.14 |
| **= authoritative** | **−6,338.70** |

Exact to the cent. The 19 markets are positions **bought before the capture
began**; counting their proceeds without their cost would overstate P&L.

## Why the four numbers differ

| figure | value | why |
|---|---:|---|
| old headline | **+$7,125.74** | filtered on `redeem_qty > 0`, excluding 708 zero-redemption markets **by construction** — every one now proven a loss |
| corrected estimate | **−$5,047.88** | right method, different population (4,217 markets, "resolved ≥10 min pre-harvest") and a different capture |
| cashflow ledger | **−$5,508.95** | all cash in the window, including 19 orphan redemptions and the 12 winners' spend |
| **authoritative** | **−$6,338.70** | resolution-classified, 100% coverage, orphan proceeds and uncaptured redemptions excluded |

The old headline is wrong by **$13,464** against the authoritative figure, and the
825 proven losses are $14,902 of it. **The sign of L5's trading result is now
established by the venue's own settlement data: it is negative.**

## What this does and does not settle

**Settles:** L5's September trading lost **$6,338.70** on $1.83M of spend
(−34.6 bps), while incentives paid **+$23,047.40**. Trading is **−27.9%** of the
incentive total. The business was incentive-funded, and that is no longer an
inference.

**Does not settle:** the capture covers **12 days** (Sep 01–10, 22–23), not the
full month, so this is not "September" in full; and the 12 uncaptured redemptions
mean the true figure is slightly **better** than −$6,338.70, by at most the $433.40
of excluded spend plus whatever those winners paid.

## Trust classification

| number | status |
|---|---|
| Settlement class of all 4,766 markets | **SOURCE-ATTRIBUTED** — venue `winner` flag, 100% coverage, raw responses retained |
| 825 proven losses, $14,902.20 | **SOURCE-ATTRIBUTED** |
| Realized trading P&L −$6,338.70 | **CASH-RECONCILED** — residual zero, every difference named |
| Incentives +$23,047.4044 | **CASH-RECONCILED** at account level; per-market **UNRESOLVED by construction** |
| Regime split | **CASH-RECONCILED**, market-attributed, boundary-free |
| 12 uncaptured redemptions | **UNRESOLVED** — $433.40, direction known (understates profit) |
