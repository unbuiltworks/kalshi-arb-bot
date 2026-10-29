# L5, September 23-25 2026 — where the drawdown actually is

Source: L5's own public activity rows, harvested day by day from the venue's
data API (`scripts/l5_harvest_sep_activity.py`, raw in
`data/l5/raw/sep/l5_sep23_25_daily.json`). Nothing here is read off the
profile graph. The local ledger stops 2026-08-31 and does not cover these
days at all.

Methodology is the corrected one from `L5_SEPTEMBER_PNL_DECOMPOSITION.md`:
every market L5 touched is carried, and a market with no redemption row is
scored as the total loss it is. Filtering on `redeem > 0` is what once turned
a -$5,048 September into a +$7,126 September.

## Coverage, and one caveat that matters

| day | trades | markets | trade coverage (UTC) |
|---|---:|---:|---|
| Sep 23 | 9,599 | 329 | 00:00–23:56 |
| Sep 24 | 7,516 | 271 | 00:09–23:59 |
| Sep 25 | 5,397 | 258 | 00:00–**16:02, partial** |

Offset pagination on `/activity` refuses past offset 5500, so each day was
harvested in 3-hour `start`/`end` slices; no slice hit the cap.

Redemptions are matched to the market they belong to, not merely to the day
the cash landed. Sep 25 carried **$6,739.69** of redemptions for markets
traded earlier — counting those as Sep 25 profit turns the day from a loss
into a gain, which is exactly the kind of artifact this file exists to avoid.
The figures below are same-market-matched. The residual error is the last
window of each day, which redeems after midnight: on 250-330 markets a day,
a handful of markets.

## The decomposition

| | Sep 23 | Sep 24 | Sep 25 (part) |
|---|---:|---:|---:|
| spend | 135,430 | 116,136 | 64,412 |
| redeem | 132,733 | 107,260 | 61,564 |
| **trading net** | **−2,697** | **−8,876** | **−2,848** |
| matched-pair locked | +7,052 | +4,775 | +2,465 |
| residual settlement | **−9,749** | **−13,651** | **−5,314** |
| incentives (maker+taker rebate) | +1,402 | +1,024 | +564 |
| REWARD | 0 | 0 | 0 |
| **total** | **−1,295** | **−7,853** | **−2,285** |

Per dollar of turnover:

| basis points of spend | Sep 23 | Sep 24 | Sep 25 |
|---|---:|---:|---:|
| trading | −199.1 | −764.3 | −442.2 |
| matched-pair locked | +520.7 | +411.1 | +382.7 |
| residual settlement | −719.8 | −1,175.5 | −824.9 |
| incentives | +103.5 | +88.1 | +87.5 |

## Where the drawdown is concentrated

**Residual directional settlement.** It is negative on all three days and
swamps everything else: −719.8, −1,175.5 and −824.9 bp against a matched leg
earning +520.7, +411.1 and +382.7. Sep 24, the worst day, is the worst
residual day. The imbalance is large and consistently bought high — 59,081,
45,308 and 28,407 residual shares at average costs of 0.3372, 0.3290 and
0.3719 — and a residual share settles at 0 or 1 with nothing locked.

**Matched-pair economics are still positive but deteriorating.** Pair cost
rose 0.9425 → 0.9550 → 0.9562, so locked P&L per pair fell 0.0575 → 0.0450
→ 0.0438. That is a real, monotone decline across the three days, and it is
a second-order contributor: it shrinks the cushion rather than causing the
hole.

**Incentives did not change.** Maker and taker rebates held at 103.5, 88.1
and 87.5 bp of spend, tracking volume. No REWARD rows at all in the window.
Incentives are not the cause and no longer cover the trading loss — they
covered 52%, 12% and 20% of it on the three days.

**This is not the September picture.** In September the book lost 32 bp of
turnover and rebates paid four times that. Here the book loses 199-764 bp and
rebates pay a fraction. The mechanism that made L5 profitable — enormous
maker volume at a controlled small trading loss — is not what these three
days show.

## What this is NOT evidence for

The clone is unchanged on the strength of this. L5 having a losing period is
an out-of-sample behavioural test, not a reason to redesign: the question the
replication asks is whether we reproduce his decisions, and a period where
those decisions lose money is a more informative test of that than a period
where they win. Copying him into a drawdown faithfully is a pass, not a
failure.

What it does bear on is our own economics, separately: a policy whose
profit comes from matched-pair capture while its losses come from residual
direction is a policy whose residual management is the whole game, and our
own envelope currently tolerates a residual of up to 10 shares with no view
on its cost basis.
