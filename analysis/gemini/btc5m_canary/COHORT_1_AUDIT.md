# BTC5m canary cohort 1 -- the five-row settlement and cash audit

AUTHORIZATION: OWNER-2026-10-05-GEMINI-BTC5M-CANARY-1. Cohort run
37335223517 @ 76f976bd; audit run 37340105155 and its successor, both
READ-ONLY (an AST guard rejects the audit module if it can reach an order
transport, a write-capable call name or a mutation path).

EXECUTION/MECHANICS EVIDENCE ONLY. The side rule was UP ALWAYS, which
expresses no view. Five windows is not a sample. Nothing below is evidence
for or against any BTC5m edge, and the five windows are excluded from the
preregistered strategy dataset.

## The five rows

DECISION -> SUBMITTED -> ACK -> FILLED -> SETTLED -> CASH_CONFIRMED

| # | window | T+ | limit | ack | latency | fill | role | fee | side | payout | venue netProfit | REALIZED net |
|---|--------|----|-------|-----|---------|------|------|-----|------|--------|-----------------|--------------|
| 1 | …1550 | 15.4 | 0.72 | RESTING | 493.8 ms | 1 @ 0.72 | maker | 0 | **no** | 0 | −0.72 | **−0.72** |
| 2 | …1555 | 15.3 | 0.67 | FILLED | 183.1 ms | 1 @ 0.67 | taker | 0.02 | **no** | 0 | −0.67 | **−0.69** |
| 3 | …1600 | 15.6 | 0.60 | FILLED | 123.8 ms | 1 @ 0.60 | taker | 0.02 | **no** | 0 | −0.60 | **−0.62** |
| 4 | …1605 | 15.5 | 0.64 | FILLED | 148.9 ms | 1 @ 0.64 | taker | 0.02 | **no** | 0 | −0.64 | **−0.66** |
| 5 | …1610 | 15.6 | 0.74 | FILLED | 152.3 ms | 1 @ 0.74 | taker | 0.02 | **yes** | 1 | +0.26 | **+0.24** |

COHORT REALIZED NET: **−$2.45**. Acquisition principal $3.37, venue fees
$0.08, settlement payout $1.00. The venue's own netProfit sums to −$2.37,
which is the same figure BEFORE its charged fee -- it agrees with
payout − costBasis on every row, and the fee is added by us rather than
double-counted.

Every fill attributed by route **ORDER_ID** -- the exact identifier the
venue minted. SETTLED read 3/3 settled, no flapping, and
`resolutionSide` agrees with `settlementValue >= strike.value` on all five.

## SETTLED and CASH_CONFIRMED are different facts

| # | settledAt | settlementValue | strike | cashOut rows |
|---|-----------|-----------------|--------|--------------|
| 1 | 15:50:05.499Z | 85309.41433333333 | 85309.86 | 0 |
| 2 | 15:55:05.525Z | 85289.7355 | 85309.41 | 0 |
| 3 | 16:00:06.490Z | 85246.313 | 85289.74 | 0 |
| 4 | 16:05:05.570Z | 85200.1825 | 85246.31 | 0 |
| 5 | 16:10:05.498Z | 85280.8975 | 85200.18 | 0 |

**cashOuts[] is EMPTY on all five**, and the cash is on the settled POSITION
row instead: costBasis, payout, netProfit, realizedPnl, settledAt. That is
consistent with the SELL research -- cashOuts is the EARLY-SELL primitive,
and a position held to settlement reports its cashflow on the position, with
`realizedPnl: 0` precisely because nothing was cashed out. An empty cashOuts
is therefore not an absent cashflow; it is the wrong place to look for a held
position. Had the audit only read cashOuts it would have reported five
positions with no confirmed cash, which is the unreadable-versus-empty
mistake wearing a new hat.

## A venue mechanism this surfaced

THE BTC5m STRIKE IS THE PREVIOUS WINDOW'S SETTLEMENT VALUE, to the cent:
85309.41 / 85289.74 / 85246.31 / 85200.18 are exactly the settlementValues
of windows …1550 / …1555 / …1600 / …1605, each becoming the next window's
strike. Four consecutive confirmations. So each BTC5m window asks whether
the reference ends above where the previous one ended -- the windows are
chained, not independently struck.

DESCRIPTIVE. It is not a strategy, not a threshold, and not an edge; it is a
fact about how the venue sets the strike, recorded so the early-window
analysis can use the right causal quantity when the recorder's tapes land.

## Descriptive reconstruction

| # | UP entry | contemporaneous DOWN | UP at entry | settled | counterfactual DOWN net |
|---|----------|----------------------|-------------|---------|--------------------------|
| 1 | 0.72 | 0.50 | leader | no | +0.48 |
| 2 | 0.67 | 0.63 | leader | no | +0.35 |
| 3 | 0.60 | **0.68** | **underdog** | no | +0.30 |
| 4 | 0.64 | 0.63 | leader | no | +0.35 |
| 5 | 0.74 | 0.55 | leader | yes | −0.57 |

DOWN acquisition is `1 − YES bid`, which is what buying NO actually consumes
on this venue's single ladder. The counterfactual is MODELLED: no DOWN order
was sent, so its fee is the published schedule rather than a charged one, and
its net is never realized. It is reported because it was asked for
descriptively; it selects nothing. Four of five windows were ones where UP
was the dearer side at entry and settled no.

## What is still NOT available

`LRRR_XBT − strike` AT ENTRY, for all five. The only preserved reference
tapes end ~95 minutes before these entries, so the nearest published value is
5704-6904s old, and the audit REFUSES to difference a stale reference against
the strike rather than manufacture a D that was never observed. The strike is
now authoritative (`contract.strike.value` via strike_check) and the entry
instants are recorded, so this computes as soon as the 9-hour recorder commits
its tapes. The reference trajectory has the same gap for the same reason.

## Five windows, and what they cannot support

1 of 5 UP settlements against entry prices of 0.60-0.74 is not a finding. The
side rule took no view, the cohort was sized for mechanics discovery, and a
binomial with n=5 distinguishes nothing. Reporting this −$2.45 as evidence
about BTC5m expectancy would be exactly the inference this programme refuses.
