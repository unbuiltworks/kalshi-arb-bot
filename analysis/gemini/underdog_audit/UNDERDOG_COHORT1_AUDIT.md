# GEMINI BTC5m UNDERDOG COHORT 1 — settlement and cash audit

`GEMINI_BTC5M_UNDERDOG_COHORT1_AUDIT_V1` · grant
`OWNER-2026-10-05-GEMINI-BTC5M-UNDERDOG-1` · audit runs 37393705785 and
37393890307 (the second after the payout correction below), both READ-ONLY.

This audit sends no order, no SELL and no cancel. It imports no order
transport and contains no mutation path, and an AST guard asserts that before
any secret is in scope. It is accounting housekeeping on five fills that
already happened. Machine-readable twin: `UNDERDOG_COHORT1_AUDIT.json`.

## Accounting discipline

`DECISION → SUBMITTED → ACK → FILLED → SETTLED → CASH_CONFIRMED → REALIZED`

- **SETTLED** is the OUTCOME leg, from the authoritative contract object.
- **CASH_CONFIRMED** is the CASH leg, and only the venue's own settled-position
  cashflow record supplies it. A settled outcome is NOT a confirmed cashflow.
- Realized net P&L appears only on rows carrying that record.
- No mark-to-market figure appears anywhere below.
- **Unreadable is not zero.** Nothing unreadable is reported as absent cash.

## The five rows

| # | window | side / leg held | T+ | fill | role | **charged fee** | resolved | won | payout | venue `costBasis` | venue `payout` | venue `netProfit` | venue `realizedPnl` | **REALIZED net** |
|---|--------|-----------------|----|------|------|-----------------|----------|-----|--------|------------------|----------------|-------------------|---------------------|------------------|
| 1 | …1730 | DOWN / `no`  | 15.1 | 1 @ 0.50 | taker | $0.02 | **yes** | no | $0 | 0.5  | 0 | −0.5  | 0 | **−$0.52** |
| 2 | …1755 | DOWN / `no`  | 131.6 | 1 @ 0.18 | taker | $0.02 | **yes** | no | $0 | 0.18 | 0 | −0.18 | 0 | **−$0.20** |
| 3 | …1800 | DOWN / `no`  | 17.5 | 1 @ 0.59 | taker | $0.02 | **yes** | no | $0 | 0.59 | 0 | −0.59 | 0 | **−$0.61** |
| 4 | …1805 | UP / `yes`   | 15.0 | 1 @ 0.56 | taker | $0.02 | **no**  | no | $0 | 0.56 | 0 | −0.56 | 0 | **−$0.58** |
| 5 | …1810 | UP / `yes`   | 18.3 | 1 @ 0.34 | taker | $0.02 | **no**  | no | $0 | 0.34 | 0 | −0.34 | 0 | **−$0.36** |

ACK order ids `145828836799964533` / `…800314979` / `…800370626` /
`…800430649` / `…800519491`; ACK 201 `filled` with `remainingQuantity` 0 on
all five, latencies 133.9 / 197.0 / 166.1 / 183.4 / 179.2 ms.

**All five lost.** Each window resolved to the leg we did NOT hold, so the
$1/0 payout is $0 five times and the realized loss is the whole outlay.

Our computed figure is `venue payout − venue costBasis − the venue's own
charged fee`, reported BESIDE the venue's fields, never instead of them. The
venue's `netProfit` equals `payout − costBasis` on every row — it is the
figure BEFORE the trading fee, so the fee is added by us rather than
double-counted. `realizedPnl` is 0 on all five, which is correct and is not a
zero cashflow: see the cashOuts note below.

## Cohort total, against the $3.1000 cap

| | |
|---|---|
| acquisition principal committed | **$2.17** |
| venue fees charged | **$0.10** |
| settlement payout received | **$0.00** |
| **COHORT REALIZED NET** | **−$2.27** |
| total outlay vs cap | $2.27 of $3.1000 — **$0.83 unused, cap never breached** |

This reconciles exactly with the ledger's own `committed_usd` of `$2.2700`:
with a zero payout on every row, the realized loss IS the committed outlay.
The cohort is spent — 5 attempts consumed, 0 remaining — with **no HALT row
and no LEDGER_CORRECTION row** on `analysis/gemini/btc5m_underdog/cohort.jsonl`.

## SETTLED and CASH_CONFIRMED are different facts

| # | `settledAt` | `settlementValue` | `strike.value` | `resolutionSide` | cross-check | reads | cashOut rows |
|---|-------------|-------------------|----------------|------------------|-------------|-------|--------------|
| 1 | 17:30:05.554Z | 85369.38 | 85324.27 | yes | **agrees** | 3/3 settled, no flap | 0 |
| 2 | 17:55:05.458Z | 85359.2855 | 85245.52 | yes | **agrees** | 3/3 settled, no flap | 0 |
| 3 | 18:00:06.309Z | 85426.51416666666 | 85359.29 | yes | **agrees** | 3/3 settled, no flap | 0 |
| 4 | 18:05:05.431Z | 85395.5275 | 85426.51 | no | **agrees** | 3/3 settled, no flap | 0 |
| 5 | 18:10:05.520Z | 85376.04866666667 | 85395.53 | no | **agrees** | 3/3 settled, no flap | 0 |

`resolutionSide` was cross-checked against `settlementValue >= strike.value`
on **every** row (rule: UP resolves `yes` when `settlementValue >= strike`)
and agrees on all five. Strike source is `contract.strike.value` via
`strike_check` on all five — authoritative, not inferred.

`cashOuts[]` is **EMPTY on all five**, and the settled-positions surface was
**READABLE** (HTTP 200, 121 positions, both `withCashOuts` spellings). Those
are different facts and the distinction is the whole point: `cashOuts` is the
EARLY-SELL primitive, and a position held to settlement reports its cashflow
on the POSITION row (`costBasis`, `payout`, `netProfit`, `realizedPnl`,
`settledAt`), with `realizedPnl: 0` precisely because nothing was cashed out.
An audit reading only `cashOuts` would have reported five positions with no
confirmed cash. Empty is the wrong place to look, not an absent cashflow.

## Nothing came back unreadable

Every surface this audit needed was readable:

- settled positions: 200, 121 rows, both parameter spellings;
- the contract object: 3/3 settled reads per symbol, no flapping, no missing
  fields;
- fills: slots 2–5 from the preserved run artifact, slot 1 from the venue's
  own `order_history` + `mytrades` joined on `order_id`;
- reference tape at entry: fresh on all five (0.5–0.9 s old).

### Slot 1's fee existed only at the venue

Slot 1 has no preserved fill row anywhere in the repository — its session's run
artifact kept no window — and the order ACK body carries no fee field. The
ledger's `projected_fee_usd` is a PROJECTION, not a charge, so the audit read
the venue's own record for it: **charged fee $0.02**, taker, matching the other
four. A projected fee is never reported as a charged one.

### Attribution

Every row attributed by route **ORDER_ID** — the exact identifier the venue
minted — against **THIS cohort's own ledger**. It is not
`ci15m_probe.authenticated_fills`, which attributes against the CI15M cohort
root; that is why the underdog run artifact itself filed all five of our own
fills under `fills.foreign` with *"unattributable venue fill(s)"*. The rows
were ours; the attribution root was wrong. Nothing was absorbed: an
unattributable fill still HALTs, and none occurred here.

## The payout bug this audit's own cross-check caught

The first run printed `payout_usd: 1` on three rows whose venue `payout` was
`0`. The payout line read the contract's `resolutionSide` alone and paid $1
whenever the contract resolved `yes` — correct only for a YES holder. The
canary cohort bought `yes` in all five windows, so the bug could not surface
there. The underdog bought `no` (DOWN) in three of five, and **a contract
resolving `yes` pays its NO holder nothing.** The held leg is now carried
explicitly from the ledger and the payout settles against IT. This is exactly
why the venue's own fields are reported beside our computed figure rather than
instead of it: the disagreement is what caught it.

## A venue mechanism, re-confirmed

**The BTC5m strike is the previous window's settlement value, to the cent.**
Slot 3's strike 85359.29 is slot 2's `settlementValue` 85359.2855; slot 4's
85426.51 is slot 3's 85426.5142; slot 5's 85395.53 is slot 4's 85395.5275.
Three further confirmations on top of the canary's four. Each BTC5m window
asks whether the reference ends above where the previous one ended — the
windows are chained, not independently struck. DESCRIPTIVE: a fact about how
the venue sets the strike, not a strategy and not an edge.

## Descriptive reconstruction — selects nothing

| # | UP entry | DOWN entry | cheaper (bought) side | D at entry (reference − strike) | resolved |
|---|----------|-----------|-----------------------|--------------------------------|----------|
| 1 | 0.81 | **0.50** | DOWN | +8.995 | yes |
| 2 | 0.98 | **0.18** | DOWN | +51.675 | yes |
| 3 | 0.72 | **0.59** | DOWN | −0.119 | yes |
| 4 | **0.56** | 0.70 | UP | −0.174 | no |
| 5 | **0.34** | 0.91 | UP | −17.089 | no |

The reference at entry is fresh on all five (0.5–0.9 s), so D is an OBSERVED
difference here rather than the refused stale one. It is **not** Z: the LRRR
statistic's mechanics are not established. All five entries have D on the
opposite side of zero from where the window resolved, which is a description
of five probe-contaminated windows, not a signal.

## What five windows cannot support

The side rule bought the cheaper side by executable q1 VWAP, which expresses
no forecast. 0 of 5 is not a finding: a binomial with n=5 distinguishes
nothing, every window is flagged `probe_contaminated`, and all five are
excluded from the preregistered strategy dataset. This is EXECUTION-MECHANICS
evidence — the submit/ACK/fill/settle/cash chain closes end to end and the
accounting reconciles to the cent. Reading −$2.27 as evidence about BTC5m
expectancy, or as evidence that the underdog side is the losing side, would be
exactly the inference this programme refuses.
