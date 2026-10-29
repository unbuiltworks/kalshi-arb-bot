# Money audit — every venue, every lane, what has actually been realized

*2026-09-20T20:15Z. No code, no deploys, no service touched. Figures are
venue-confirmed where labelled REALIZED and estimated only where labelled
ESTIMATE.*

---

## The headline

**Lifetime venue-confirmed trading P&L across the whole programme is about
−$10.38, and the infrastructure carrying it is 40 Railway services and
1,106 GB of provisioned volumes.**

The infrastructure has never been costed in any analysis in this repository.
On list pricing the volumes alone are roughly **$276/month** (ESTIMATE — no
billing API is exposed to this session), before compute for ~35 always-on
services. **That makes hosting the largest loss in the programme by one to two
orders of magnitude, and it is the only lane where a decision today changes
the number this month.**

Everything below supports that, and nothing below changes the strategy
conclusions — those still need the evidence that is being collected.

---

## 1. REALIZED — venue-confirmed cash

### Polymarket US (PMUS) — the only lane with a reconciled ledger

`analysis/l5/REALIZED_BRIDGE.md`:

| line | USD |
|---|---:|
| A. Settlement / trading P&L (venue `realizedPnl`) | **−12.5082** |
| B. + confirmed maker rebates (credited at fill) | **+2.1300** |
| C. + confirmed liquidity rewards posted | 0 |
| D. − fees charged to us | 0 |
| **= actual realized net** | **−10.3782** |

Memo, explicitly **not performance**: non-trading credits (goodwill /
remediation) **$10.00**; liquidity rewards accrued but never credited **$1.48**.
Counting either would turn a loss into a wash on money that was not earned.

Sub-scopes, stated with their scope because they are **not additive** with the
above (`REALIZED_RECONCILIATION.md` shows a redeploy silently restarts the
realized accumulator, so a per-process figure understates the day):

- `l5-r1-probe` campaign: 21 sessions, **0 filled markets**, 10 settled,
  realized **−$0.0832**
- 2026-09-11, all processes, venue-confirmed: **−$9.3736** across 3 markets
- first autonomous settled cycle (`aec-cs2-mzp-ezt-2026-09-13`): **−$2.7322**

### Gemini — no reconciled trading ledger exists

| item | value |
|---|---|
| maker fills | **61**, venue's own `/v1/tradevolume` |
| maker notional | **$52.2573**, 2026-08-27 → 2026-09-18 |
| trading P&L | **UNQUANTIFIED — not zero.** Those contracts expired; no Gemini position has ever been carried to settlement and reconciled to the account |
| rebates credited | **+$0.51**, 7 `AdminCredit` rows, 97.59 bp of maker notional |

**This is a real gap.** We have 61 fills and $52 of notional on a venue and we
cannot say what they earned or lost.

### Kalshi

Shadow/collector services only. **No money path, no realized P&L, nothing
submitted.**

### L5 himself (the subject, not us)

+$68,723 lifetime, of which ~$35,200 MAKER_REBATE / ~$28,800 REWARD /
~$7,500 TAKER_REBATE against **−$2,829 trading P&L**. Provenance of the
decomposition is UNRESOLVED. **His trading loses money; the incentives carry
it.** That is the single most important fact about the target and it is easy
to forget when chasing trading edge.

---

## 2. What is running, and what it costs

From the Railway API, 2026-09-20T20:10Z — project `arb-ev-wallet-shadow`.

**40 services.** Of those:

| category | count | note |
|---|---|---|
| doing current work | **3** | `gemini-l5-faithful` (the experiment), `gemini-econ-shadow`, `gemini-maker-shadow` (frozen control, `a44de245`) |
| parked on `cron 0 0 1 1 *` (fires once a year) | **10** | `polymarket-wallet-shadow`, `kalshi-temporal-shadow`, `polymarket-l5-econ-ledger`, `kalshi-incentive-shadow`, `polymarket-temporal-shadow-run`, `l5-ops-research-live`, `gemini-fair-value-shadow`, `polymarket-maker-econ-shadow`, `polymarket-maker-ws-shadow`, `l5-transfer-supervisor-shadow` |
| `*-buildcheck` (temporary by name) | **5** | oldest from 2026-09-12 |
| CRASHED and still deployed | **2** | `polymarket-l5-replay-once` (since 2026-08-28), `pmus-ws-book-diagnostic` (since 2026-09-06) |
| no deployment at all | **2** | `l5-ops-research`, `polymarket-temporal-shadow` |
| money-capable but HALTED | **1** | `l5-r1-probe` — mandate EXPIRED 2026-09-18T00:41:20Z, `restartPolicyType: NEVER` |
| other collectors/shadows | ~17 | several on branches no longer worked on |

**18 volumes, 1,106 GB provisioned** (17 × 50 GB + 1 × 256 GB). Volumes bill on
provisioned size, not usage.

**ESTIMATE, not a bill** — no billing endpoint is exposed to this session:

- volumes at ~$0.25/GB/month ≈ **$276/month**
- ~35 always-on services at even minimal CPU/RAM ≈ **$100–175/month**
- **order of magnitude: $300–500/month**

Against **−$10.38** of lifetime realized trading P&L, the hosting has very
probably cost **20–50× the trading losses**, and unlike the trading losses it
recurs every month whether or not anything trades.

---

## 3. What each lane would need to reach positive expectancy

### PMUS sports — closest to break-even, currently switched off

`BREAKEVEN.md`, 15 settled markets, 179.73 opened legs:

| quantity | value |
|---|---|
| pair profit per matched pair | **+$0.057230** |
| residual loss per residual unit | **−$0.331449** (5.8× the pair) |
| break-even leg completion | **84.52%** (incl. credits) – **85.28%** (excl.) |
| observed | **83.42%** |
| gap | **−1.10 pp** |
| value of one completion point | **+$0.06769/dollar-hour ≈ +$1.6747/day** |

**What it needs:** +1.10 points of completion. `IMPROVEMENT_RANKING.md` #1 is a
*behavioural fidelity fix*, not a new strategy — reprice a resting COMPLETE
when L5's own touch moves. The mechanism already runs for OPEN and EXTEND
(36 and 35 exits via `repriced:touch_moved`) and is off for COMPLETE
(**0 of 868**). In 164 of 404 unfilled COMPLETEs L5's touch moved above where
we sat; in 96 of those the opposite ask reached it.

**Blocker, and it is a deadlock:** gated behind
`L5_COMPLETION_EXTENSION_GATE_v1`, which needs **48 forward hours / 25 firings
/ 15 settled markets** and has **2.35 / 10 / 5** — while `l5-r1-probe` has been
HALTED since 2026-09-17 with an expired mandate. **The gate cannot advance
because the service that feeds it is off.**

### Gemini crypto — UNRESOLVED, being measured now

**What it needs:** an answer to whether the faithful L5 process can execute at
all. `gemini-l5-faithful` is collecting; at last reading 2 windows, 72 OPENs
rested, **0 fills**. Alerts are armed for first fill / 30 zero-fill windows /
operational failure. The most likely verdict is that Gemini cannot fill the
process — valuable, but not income.

Structural facts already established: the maker rebate (`0.021 × C × P(1−P)`)
**exceeds** the maker fee (`0.0175 × …`) by 1.2×, so a maker fill is
fee-positive by ~17.5 bp of notional — but the rebate **rounds down to the cent
daily**, so below roughly q = 1 a probe pays every fee and earns no rebate.
That is why five real live fills earned $0.00.

### Kalshi

Collectors only. Would need a money path built from scratch. **Not a
candidate.**

---

## 4. The honest summary

1. **No lane is currently making money.** One money-capable service exists and
   it is halted with an expired mandate.
2. **Trading losses are small (−$10.38). Hosting is not.** ~$300–500/month
   (ESTIMATE) for 40 services and 1.1 TB of volumes, of which ~19 services are
   plainly dead (parked-on-annual-cron, buildcheck, crashed, or never
   deployed).
3. **The cheapest positive-expectancy action available today is not a trade.**
   It is decommissioning dead infrastructure. That is a real, immediate,
   recurring saving with no strategy risk and no evidence requirement.
4. **The closest trading lane to break-even is PMUS at −1.10 points**, and its
   deciding experiment is deadlocked by its own halt.
5. **Gemini is research.** It may return "cannot execute". That is a fine
   outcome for an experiment and a poor one for a P&L.

## 5. What I am NOT claiming

- Not claiming Gemini failed. **UNRESOLVED**, pending prospective evidence.
- Not claiming PMUS will clear break-even. The +1.10 pp fix is an **upper
  bound** on opportunity, not a forecast — reaching a price is not filling it,
  and a re-quote surrenders queue position.
- Not claiming the hosting estimate is a bill. **No billing API is exposed**;
  the provisioned quantities are exact, the dollars are an estimate.
- Nothing here is realized profit. Shadow stays shadow.

## 6. Decisions this puts in front of the owner

1. **Decommission dead infrastructure?** ~19 services and their volumes.
   Recurring saving, no strategy risk. I have changed nothing and will not
   without an explicit go.
2. **Un-halt PMUS shadow collection** so the completion gate can advance?
   Read-only, no mandate, no money.
3. **Reconcile the Gemini fills** so the one lane with 61 real fills stops
   having an unquantified P&L?
4. **Leave Gemini to run** — already in motion, alerts armed.
