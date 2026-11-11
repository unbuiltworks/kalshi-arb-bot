# Campaign budget vs account-wide cumulative loss: audit

**Verdict: no double counting inside any gate. Realized loss legitimately
enters two independent ceilings, which is conservative and correct. But the
campaign budget is NOT independent of account history, and calling a fresh
campaign's allowance "$29.30" is misleading — its real incremental room is
`$29.30 − account-wide realized loss`. That is the finding worth acting on,
and it is a documentation and telemetry fix, not a limit change.**

## The two ceilings and what each reads

| ceiling | value | reads | scope |
|---|---|---|---|
| campaign worst-case budget | $29.30 | `worst_case_usd(realized_usd=…)` = realized losses + unsettled cost + outstanding notional | **account-wide**, high-water marked per campaign as `peak_usd` |
| cumulative realized-loss halt | $20 | `store.realized_today()` = `realized_attributed + realized_unattributed` | account-wide, lifetime |
| daily realized-loss halt | $5 | `store.realized_current_utc_day()` | account-wide, **UTC day** |
| cash envelope | $1.65 | filled + still-fillable outstanding + the proposed order | account-wide |
| exposure ceiling | $2 | committed by market | account-wide |

## Is realized loss double counted?

**Within a gate, no.** `worst_case_usd` adds realized losses, unsettled cost
and outstanding notional once each, and the outstanding term counts
`leaves_qty` only — the filled part is already a leg, which the code comments
flag explicitly as the thing that must not be counted twice.

**Across gates, it appears twice by design.** Realized loss enters both the
campaign budget and the cumulative halt. These are not components of one
calculation; they are two independent ceilings, each of which must pass. A
quantity appearing in two conjunctive tests is conservative, not erroneous —
loosening either because the other also sees it would be the actual defect.

## What the ledger does and does not store

The document holds only `{campaign_id, closed, opened_utc, peak_usd}`, plus
`closed_utc` and `released_for` once closed. **It stores no realized-loss
figure at all**, which is why closing a campaign cannot erase one: realized
losses live in the executor's store and are recomputed into every gate
evaluation. A regression test now pins that key set.

## The real issue: a fresh campaign's allowance is not fresh

`open_or_resume` writes `peak_usd: "0"`, so campaign-specific usage genuinely
starts at zero. But the very next evaluation re-absorbs the account's standing
realized loss, because `worst_case_usd` is account-wide. Observed at the
20261024a handover:

| | |
|---|---|
| `peak_usd` written on claim | **0** |
| `peak_usd` at the next heartbeat | **10.21000000** |
| account-wide realized at that moment | **−8.56** |
| one outstanding obligation | **1.65** |
| 8.56 + 1.65 | **10.21** ✔ |
| `budget_headroom_usd` the planner then used | **20.74000000** |

So the incremental room was **29.30 − 8.56 = 20.74**, not 29.30. After the
eight-window campaign closed at **+$0.6581**, account-wide realized improved to
**−7.90190000**, so the equivalent room is now **29.30 − 7.9019 = 21.40**.

This is conservative and I am not proposing to change it. Two consequences
should be stated rather than discovered later:

1. **A fresh campaign nonce buys a fresh window budget, never a fresh loss
   allowance.** As account-wide losses accumulate, every future campaign's
   usable budget shrinks, even starting from `peak_usd: 0`.
2. **The two ceilings will collide before either is reached alone.** The $20
   cumulative halt trips at −$20 realized; the $29.30 budget is exhausted by
   realized loss alone at −$29.30. So the cumulative halt binds first by
   design, and the budget's remaining role is to bound *unsettled and
   outstanding* exposure on top of it. Worth saying because the budget's
   headline number invites the opposite reading.

**Recommended, non-limit-changing:** report `incremental_room_usd =
budget_usd − max(0, −realized)` alongside `remaining_usd` in the
`campaign_budget` telemetry block, so the figure the planner actually sizes
against is visible without arithmetic. No gate reads it.

## Fail-closed behaviour, re-verified after the journal change

| condition | refusal |
|---|---|
| ledger unreadable | `CAMPAIGN_BUDGET_UNREADABLE` |
| foreign campaign holds the budget | `CAMPAIGN_BUDGET_LOCKED:<id>` |
| campaign releasing itself | `CANNOT_RELEASE_OWN_OPEN_CAMPAIGN` |
| orders outstanding at release | `CAMPAIGN_OUTSTANDING_ORDERS` |
| unsettled legs at release | `CAMPAIGN_UNSETTLED_EXPOSURE:<n>` |
| peak at or above budget | `CAMPAIGN_WORST_CASE_EXHAUSTED:<p>>=<b>` |
| windows spent | `CAMPAIGN_WINDOWS_EXHAUSTED` |
| unrealized loss unavailable | `CUMULATIVE_UNREALIZED_UNAVAILABLE` |
| gate config malformed | `REQUIRED_RISK_CONFIG_UNREADABLE` (`RefuseAllGates`) |

All nine are covered by tests, and the journal is subordinate: it is written
before the document is replaced and **every journal failure is swallowed**,
because a ledger that refused to record its own budget over an unwritable
audit file would be strictly worse than a missing history line. A test asserts
that ordering against the source and another asserts a release still succeeds
when journal serialisation raises.
