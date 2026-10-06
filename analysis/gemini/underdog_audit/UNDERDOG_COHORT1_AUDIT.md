# GEMINI BTC5m UNDERDOG COHORT 1 — settlement and cash audit

`GEMINI_BTC5M_UNDERDOG_COHORT1_AUDIT_V1` · grant `OWNER-2026-10-05-GEMINI-BTC5M-UNDERDOG-1`

READ-ONLY. This audit sends no order, no SELL, no cancel. It imports no order
transport, and that is proved by an AST guard before any secret is in scope.
It is accounting housekeeping on five fills that already happened.

## Accounting discipline

`DECISION → SUBMITTED → ACK → FILLED → SETTLED → CASH_CONFIRMED → REALIZED`

Each stage is a separate fact and the chain never collapses:

- **SETTLED** is the OUTCOME leg, from the authoritative contract object
  (`status` / `resolutionSide` / `resolvedAt` / `settlementValue`).
- **CASH_CONFIRMED** is the CASH leg, and only the venue's own settled-position
  cashflow record (`costBasis`, `payout`, `netProfit`, `realizedPnl`,
  `settledAt`) can supply it.
- **A settled outcome is NOT a confirmed cashflow.** Realized net P&L is
  reported only on rows carrying the venue's own cashflow record.
- No mark-to-market figure appears anywhere in this document.
- **Unreadable is not zero.** An unreadable surface is labelled unreadable and
  is never reported as an absent cashflow.

## The cohort

Five completed attempts, `q=1` each, acquisition ceiling `$0.60/share`, cohort
cap `$3.1000`, hold-to-settlement, no SELL, no averaging, taker.
Durable ledger: `analysis/gemini/btc5m_underdog/cohort.jsonl`.
Cohort state on that ledger: 5 attempts consumed, 0 remaining,
committed `$2.2700`, headroom `$0.8300`, **no HALT and no LEDGER_CORRECTION
row**.

All five windows are flagged `probe_contaminated` in the ledger
(`PROBE_CONTAMINATED_WINDOWS.txt`). This is EXECUTION-MECHANICS evidence; it is
excluded from the strategy dataset and five `q=1` fills validate no strategy.

## Local legs — complete, from our own durable ledger

| slot | market | side / outcome | requested | ACK order_id | latency | venue status | filled | avgExecPx | role |
|---|---|---|---|---|---|---|---|---|---|
| 1 | GEMI-BTC05M2610051730-UP | DOWN / `no` | 0.50 × 1 | 145828836799964533 | 133.9 ms | 201 `filled` | 1 | 0.50 | taker |
| 2 | GEMI-BTC05M2610051755-UP | DOWN / `no` | 0.18 × 1 | 145828836800314979 | 197.0 ms | 201 `filled` | 1 | 0.18 | taker |
| 3 | GEMI-BTC05M2610051800-UP | DOWN / `no` | 0.59 × 1 | 145828836800370626 | 166.1 ms | 201 `filled` | 1 | 0.59 | taker |
| 4 | GEMI-BTC05M2610051805-UP | UP / `yes`  | 0.56 × 1 | 145828836800430649 | 183.4 ms | 201 `filled` | 1 | 0.56 | taker |
| 5 | GEMI-BTC05M2610051810-UP | UP / `yes`  | 0.34 × 1 | 145828836800519491 | 179.2 ms | 201 `filled` | 1 | 0.34 | taker |

Attribution is by **exact venue `order_id` against THIS cohort's own ledger**
(`underdog_cohort.attribute_fills`), route `ORDER_ID` on every row that has a
preserved fill. It is not `ci15m_probe.authenticated_fills`, which attributes
against the CI15M cohort root and is exactly why the underdog run artifact
itself recorded all of our own fills under `fills.foreign` with
`"unattributable venue fill(s)"`. Those rows are ours; the run's attribution
root was wrong, not the fills.

### Charged fees

Slots 2–5 have a preserved venue fill row carrying `fee_amount` **$0.02**
(`fee_currency` USD, `maker: taker`, `aggressor: true`) — a CHARGED fee.
Slot 1 has **no preserved fill row anywhere in the repository**: its session's
run artifact kept no window. Its order ACK body carries no fee field, and the
ledger's `projected_fee_usd` is a PROJECTION, not a charge. Slot 1's charged
fee therefore exists only at the venue and is read there.

## Venue legs — SETTLED and CASH_CONFIRMED

<!-- VENUE_LEGS -->
PENDING. The settlement and cash legs require the repository's Gemini Actions
secrets and cannot run in this workspace: `GEMINI_API_KEY` and
`GEMINI_API_SECRET` are both **ABSENT from the local environment** (presence
checked, never a value). At the time the cohort ran, all four preserved windows
read `contract_status: active` — nothing had settled yet, so no settlement leg
exists locally at all.

Dispatched to CI as `.github/workflows/gemini-btc5m-underdog-audit.yml` via the
request file `.github/btc5m_underdog_audit_request`, under its own concurrency
group `gemini-btc5m-underdog-audit`, exactly as the canary audit is dispatched.
That job writes `UNDERDOG_COHORT1_AUDIT.json` and this section is filled from
it.

## Cohort total

PENDING the venue legs. Principal committed, fees charged and payout received
are reported against the `$3.1000` cap once the venue's own cashflow record is
in hand. **No realized net is stated before then.**

Acquisition principal from the preserved fills alone (slots 1–5, at the ACK
`avgExecutionPrice`) is `0.50 + 0.18 + 0.59 + 0.56 + 0.34 = $2.17`, with
`$0.10` of charged fees on slots 2–5 and slot 1's charged fee still to be read
— a committed outlay consistent with the ledger's `$2.2700`. That is a
COMMITMENT figure, not a realized one.
