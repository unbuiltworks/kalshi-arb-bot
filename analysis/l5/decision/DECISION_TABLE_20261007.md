# Decision table — 2026-10-07, four lanes in, one live probe pending

Sources: Lane B `analysis/l5/residual_frontier/` (prereg
`4b16c92b…e074d`), Lane C `analysis/l5/incentives/`, Lane D
`analysis/l5/action_scorecard/`, Lane E `analysis/l5/opportunity/`.
Lane A = the authorized XR2W probe, deployment `84f61d1b`, window 02:00Z,
**not yet terminal**.

Everything below is HISTORICAL REPLAY, SHADOW, or VENUE-CONFIRMED CASH,
labelled per claim. Nothing is realized profit.

---

## 1. Can adapted L5 mechanics make money ex incentives?

**Not by any rule we preregistered, and the deficit we were trying to
close is ~67% an accounting artifact rather than a trading loss.**

The headline −$2,745.53 does not survive inspection. A fill-level replay
reproduces the matched component **exactly** (+$22,568.25, asserted in a
test) but nets **−$1,526.81**. The $1,218.72 gap is: **+$1,829.86** of
matched shares on six events with no REDEEM record, which the audit books
at $0.00 instead of their guaranteed $1/share complete-set redemption —
five of the six on 2026-04-21, the day before the May–July coverage hole,
and those six **are era E2 in its entirety**. E2's "−$1,855.08, win rate
0.000" is a harvest-boundary artifact; on modelled settlement E2 is
−$25.23. Plus −$611.14 of residual-valuation shortfall.

Stratified: **on the 1,998 events with source-backed settlement, L5's
ex-incentive cash is +$2,016.81.** The negative sign lives entirely in
the 218 events with no REDEEM, 212 of which are one-sided bets that lost
outright.

Then the structural result. The exchange rate — residual loss avoided per
dollar of matched economics sacrificed — **never exceeds 1.15** and drops
below 1.0 at both extremes. At fill granularity **the matched economics
and the losing residual come from the same fills.** The +$22,568 /
−$25,314 presentation invites reading two separable businesses. There is
one.

11 of 30 cells are positive; 9 gut the engine (matched < 50% of
baseline); the two survivors are +$29.84 (0.4 bp, $1.36/day) with E3
negative, failing the preregistered single-stratum test. Every cell that
produces real dollars destroys **63–82%** of matched economics.

| candidate | net | matched kept | avoid/sac | suppressed | verdict |
|---|---|---|---|---|---|
| `ABS100_EXTEND_ONLY` | +$1,080.71 | 35.1% | 1.122 | 16.0% | GUTTED |
| `TTE120x0.25_EXTEND_ONLY` | +$1,042.12 | 37.0% | 1.127 | 11.2% | GUTTED |
| `ABS200_EXTEND_ONLY` | +$29.84 | 55.7% | 1.135 | 6.5% | not gutted, E3 negative |

`ABS100` suppresses 16% of actions and keeps 79% of turnover yet destroys
65% of matched dollars: **"suppress few actions" and "preserve the
engine" are not the same constraint.**

One supportive, independently useful result: the EXTEND-only semantic
(cap blocks EXTEND, COMPLETE always allowed) is worth +$861 at cap 25 and
**nothing at caps ≥ 100**, because no COMPLETE fill in the corpus ever
pushes |residual| past 100 shares — which corroborates the live shell's
existing `residual_cap_units` choice.

**Held, not claimed.** OOS is named and unrun:
`data/l5/raw/activity_gap.jsonl.gz`, 706,500 records, ~14,800 events,
never read by this protocol, filling the exact May–July hole that caps
every conclusion. The pre-committed test is whether the candidates'
**signs and exchange rates** survive — not net dollars.

---

## 2. What incentives can our account actually receive?

**$16.18 lifetime, venue-confirmed.** Not $0.51, and not L5's numbers.

| line | amount | basis |
|---|---|---|
| PMUS maker rebates | **$9.14** | 737 credited fills of 1,174 |
| PMUS liquidity rewards | **$7.04** | 3 transfers, 44 PAID rows |
| PMUS taker rebates | $0.00 | — |
| PMUS Volume program | $0.00 | taker-only by rule; structurally zero for a maker |
| **PMUS confirmed** | **$16.18** | /v1/portfolio/activities, 1,269 rows / 64 pages |
| Gemini (separate venue) | $0.51 | 7 AdminCredit rows, 97.59 bps |

Excluded as not credited: PENDING $0.70, SKIPPED $1.37, a $10.00 ISV
goodwill credit (not an incentive), a $9.31 formula estimate.

Mechanics: `round_half_even(0.0125·C·p·(1−p), $0.01)`, credited **at
fill**, hard cent floor, **no tiers**. As a rate that is `0.0125·(1−p)`:
87.5 bps at p=.30, 62.5 at .50, 37.5 at .70. Taker θ rose to 0.0695 on
2026-10-01; **maker θ unchanged**, and because the maker rebate is an
absolute coefficient rather than a share of the taker fee, the taker rise
does not lift ours.

**$480/day needs $64,953/day of maker notional** at our measured 73.9
bps. We are at **$0.506/day — 949× short**. At 73.9 bps a single −$3.90
directional residual needs **$528 of maker notional to repay**. The
rebate pays for completing; it does not pay for being wrong.

L5's $71,551.74 of incentives is **not transferable** on four independent
grounds: different account, **different venue** (he settles pUSD on
Polymarket international), different program era (his REWARD stream is
August-only; up/down programs read EXPIRED by 09-10), different shape
(daily pool distributions vs our per-fill credit). Transferable: the
mechanism, not the magnitude.

---

## 3. How accurately do we predict L5's next action?

**Worse than persistence on the three fields that matter.**

| field | model | best baseline | lift | 95% CI |
|---|---|---|---|---|
| exposure_action | 0.6845 | persistence 0.6802 | +0.0043 | [−0.0003, +0.0092] |
| side | 0.6825 | persistence 0.6908 | **−0.0083** | [−0.0173, −0.0082] |
| switch | 0.6923 | majority 0.6962 | **−0.0039** | [−0.0069, −0.0011] |
| size_bucket | 0.3686 | persistence 0.3523 | +0.0163 | [+0.0103, +0.0228] |
| timing_bucket | 0.4816 | majority 0.4793 | +0.0023 | [−0.0044, +0.0100] |

Only `size_bucket` beats its best baseline cleanly, and its notional is
**39% over-scaled**. The deployed state-only model is 14–16 pt *below*
persistence on exposure action and side. The earlier "+14 pt relation
lift" reproduces exactly — it survived only because the baseline chosen
there was the majority class rather than persistence.

**The model reproduces L5's autocorrelation, not L5's decision.** Our own
finding that same-side run length is the strongest EXTEND predictor is
why: the signal and the baseline are the same thing. Under CLAUDE.md's
validation standard **this is not replication**, and the fidelity gate
must not be read as met on aggregate or majority-baselined numbers.

5m and 15m are different objects: on 15m persistence is near-unbeatable;
on 5m persistence collapses to ~0.53 and the model shows real structure
(exposure +0.037, side +0.026, timing +0.066) on an August-only cohort
10× smaller. **E1 is the only era where the model beats every baseline on
both exposure action and side** — and E1 is the era whose economics stood
alone. Size prediction *inverts* by era, so deployed size behaviour is an
August artefact.

Three economically-material mismatches: (1) `complete` called `extend` —
the clone **doubles down where L5 reduced**, 35.2% of complete decisions,
adding ~156.5 residual shares/window against L5's own median 141.8,
bracketed −$1,319 to −$15,911; (2) size over-scales notional 39%, which
multiplies (1) and silently converts a probe size cap into a frequency
cap; (3) a side error on 31.7% of decisions whose **economic sign the
data cannot resolve** (+$1,119 at L5's median pair, −$3,290 at his p75).

Unscorable, with the missing artifact named: **price relative to touch**
(no contemporaneous book over L5's windows, so neither prediction nor
truth exists) and **WITHDRAW** (fill tape only; a cancel is
indistinguishable from a wait).

---

## 4. Where can we execute it today?

**Not BTC15m by default — it wins one axis of eight.**

| rank | category | states | our evidenced result |
|---|---|---|---|
| 1 | tennis | 1,382 | 24 maker / 0 taker; **19.87 matched pairs at 0.9477, zero residual** |
| 2 | soccer | ~3,700 | 5.67 pairs at **0.7636** fully matched (best pair cost we hold) |
| 3 | crypto BTC15m | — | 9/9 rested and maker-filled; pair costs **1.0299 and 1.1986, both above par** |
| 4 | esports | 1,370 | thinnest tight books, closest to L5's source |
| 5 | baseball | 1,339 | spread 0.005, tighter than L5's source, but tight-and-frozen |
| 6 | basketball | 434 | zero fills |
| 7 | football | 5,303 | **all reach data is PREGAME**; our worst evidenced at −$1.5476 |

BTC15m wins **TTE structure** outright (the only native-clock cohort, 96
windows/day, six load-bearing properties identical to L5's source) and
ties on fees. It loses passive fill frequency, spread, two-sided
inventory opportunity and confirmed rewards to tennis and soccer, loses
spread to MLB, and holds **the worst evidenced pair economics we have**.

The governing exclusion: **every L5 cohort outside BTC15m is
taker→taker** (BTC5m 322/322, ETH15m 13/13). The maker→maker mechanism
exists in exactly one L5 cohort.

Honest limit: tennis's and soccer's lead rests on **one fully-matched
market each**. That is a reason to measure them properly, not a verdict.
Economics, politics, commodities, weather, culture: **zero
observations** — the whole PMUS live universe enumerated as category
`sports` plus the BTC series. No rank given rather than ranking on
vapour.

**Capacity is not revenue.** Evidenced capacity $18.83 of passive
two-sided notional in one market, $1.98 per order. Depth is **not** the
binding constraint anywhere (8× to 100,000× our $5 envelope); the binding
constraints are the $5 envelope and the completion rate (83.42% observed
vs 84.52% break-even). Queue position is unproven everywhere, so every
depth figure is an upper bound on the book.

**Evidenced realized dollars/day: $0.00.** Zero, not a small positive.
Every cohort is negative; PMUS sums to −$22.2374 over ~7 active days ≈
−$3.18/active day.

---

## 5. Fastest evidence-backed path toward $480/day realized

The target is **949× from confirmed incentives** and **infinite from
evidenced trading**, which is $0.00/day. No preregistered rule, no
category, and no incentive stream reaches it on current evidence. So the
honest path is not a scaling plan; it is a short list of cheap
measurements that would make any scaling plan non-fictional.

Ranked by evidence gained per dollar and per hour:

1. **Finish Lane A** (in flight, $0.0099 at risk). The first evidenced
   maker fill from the corrected wire path. Nothing else can establish
   that our own orders rest and fill.
2. **Run the held OOS** (`activity_gap.jsonl.gz`, zero dollars). It fills
   the May–July hole that caps every Lane B conclusion and tests whether
   the two candidates' signs survive. If they do not, the frontier is
   closed.
3. **Re-run `src/pmus_incentive_audit.py` where the credential lives**
   (zero dollars). Three weeks of possible credits after 2026-09-14 are
   **unmeasured — a hole, not a zero** — and it settles the **$9.14
   double-count risk** on whether `trade.realizedPnl` is already net of
   `commissionNotionalCollected`.
4. **Compute post-fill markouts on OUR OWN fills** (zero dollars). No
   such measurement exists in any category on either venue, so every
   adverse-selection figure in Lane E is an inference.
5. **Measure tennis and soccer properly** (bounded, after Lane A). Their
   rank rests on one market each, and they beat BTC15m on the axes that
   produce fills.
6. **Repeat the habitat sweeps at peak hours.** Both ran off-peak
   (01:45–03:10 UTC, "essentially no US sport live") and both
   preregistered a peak repeat that never happened.

What would have to change for $480/day to be reachable at all: either
**$64,953/day of maker notional** at our measured rebate rate (8,700×
our session envelope), or a trading edge that is positive ex incentives —
which Lane B says no preregistered residual rule delivers without gutting
the engine, and Lane D says we cannot predict well enough to claim.

**One coincidence defused before it could be quoted:** an exploratory
≥5¢ study's +$0.03307/contract × 151 contracts/window × 96 windows/day is
a **SHADOW/THEORETICAL ~$480/day** that happens to match the target. It
needs 151 contracts passively filled on both sides per window against our
evidenced maximum of 4.72 shares gross, under a $5 envelope, from a study
whose own header bars it from selecting anything. Arithmetic coincidence,
not corroboration.

---

## What is NOT answered

- Lane A is **not terminal**. No evidenced fill from the corrected wire
  path exists yet.
- `docs.polymarket.us`, `polymarket.us/rewards` and
  `gateway.polymarket.us` are **egress-blocked** here, so every
  "published" incentive claim is a 2026-09-10 capture plus search, and the
  live slate could not be re-enumerated (newest census 2026-09-27).
- Whether PMUS now varies taker θ by category: sources conflict, UNKNOWN.
- `habitat_selector_frozen.json` is **not in this working tree** (added
  on another branch, `fd91d38e7`), so the monitoring routine that watches
  its sha256 is watching a file this branch does not have.
- `src/pmus_incentive_audit.py` pins `TAKER_RATE = 0.06`, stale against
  the 2026-10-01 change to 0.0695. **Not edited** — read-only lanes.
