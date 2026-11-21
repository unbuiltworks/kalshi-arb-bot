# The smallest executable crypto loop — worked backward from money

**Read-only synthesis over all existing Gemini/L5 evidence, plus the frozen
collector's own live counters read from its log stream at 2026-09-20T18:24Z.
Nothing here was tuned, no threshold moved, and no live money was armed.**

The question was: *which complete cycle, start to finish, is most likely to
produce positive realized expectancy, how often does it occur, and what is
left before we risk $1.*

---

## 0. Where the realized money actually is, in this programme, today

Three separate ledgers. Only the first two contain venue-confirmed cash.

| ledger | realized | source |
|---|---|---|
| PMUS sports lane (L5 clone) | **−$10.3782** net (−$12.5082 trading + $2.1300 credited rebates) | `REALIZED_BRIDGE.md`, venue `realizedPnl` |
| Gemini crypto lane — trading | **$0.00** | no Gemini position has ever been opened *and* settled by this programme |
| Gemini crypto lane — rebates | **+$0.51** credited, 7 `AdminCredit` rows, on **$52.2573** of venue-reported maker notional = **97.59 bp** | `rebate_reconciliation_20260919T041240Z.json`, `POST /v1/transfers` |

So the crypto lane has **real maker fills** (61 of them, Aug 27 – Sep 18, on
BTC5m/BTC15m/ETH5m/XRP5m) and **real credited rebate income**, and has never
carried a position to settlement. Every crypto P&L number in this document is
therefore either (a) venue-confirmed rebate cash, (b) published fee arithmetic,
or (c) explicitly labelled shadow.

---

## 1. The structural facts that constrain every candidate

These are arithmetic or venue-published, not observations, and they rule out
several mechanisms before any data is consulted.

**1a. On Gemini the "passive pair" and the "passive same-side round trip"
are the same trade.** `no_bid == 1 − yes_ask` (checked, not assumed, via
`metadata.outcome_book`). Resting a NO bid at `n` *is* resting a YES ask at
`1 − n`. So pair cost `yes_bid + no_bid = 1 − spread`, and the pair profit
before fees *is* the spread. Mechanisms 1 and 2 of the six are one mechanism,
and they cannot be compared against each other.

**1b. A maker fill completed immediately by a taker cannot be sub-par. It is
arithmetic.** Rest a YES bid at `p`, get hit by an aggressor SELL → the
surviving best bid `b ≤ p`, so NO costs `1 − b` and the gross pair is
`p + 1 − b ≥ 1`. Mirror image on the NO leg. Every cent of sub-par basis must
come from the book **moving after the fill, in favour of the leg already
held**. (`hybrid_complete.json`, guarded by a test.)

**1c. The rebate exceeds the maker fee, structurally.** Published schedule:
maker fee `0.0175 × C × P × (1−P)`; crypto maker rebate
`0.30 × 0.07 × C × P × (1−P) = 0.021 × C × P × (1−P)`. The ratio is 1.2, so a
maker fill is **fee-positive by `0.0035 × C × P(1−P)`** — about **+17.5 bp of
notional at P = 0.50**, or +$0.00175 on a matched pair struck exactly at par.
This is the only edge on this venue that requires predicting nothing.

**1d. …but the rebate rounds DOWN to the cent, daily, and the fee does not.**
A day's whole accrual must reach $0.01 before any cash moves. At P = 0.50 that
is `0.01 / (0.021 × 0.25) = 1.905 contracts of maker fills per day`. At the
q = 0.01 size used by every probe so far, that is ~191 fills in one day, which
is why five real live fills earned **$0.00**. **Any probe below roughly
q = 1 collects the fee and none of the rebate.** This is a sizing constraint,
not a strategy one, and it has been silently costing every probe to date.

**1e. The eligible rebate band is $0.20–$0.80.** Two of the five live fills
(YES 0.92, NO 0.14) were outside it and earned nothing at all.

**1f. Gemini withdraws in the final minute.** Across 8,186 time-weighted
samples, roughly half of all samples inside the last 60 s have *no ask side at
all* (46.3% BTC15m, 51.7% BTC5m). Outside that minute the venue is **not**
capacity-limited for our sizes: q = 20 is fillable 99.25% of the time on
BTC15m and 88.66% on BTC5m, and on BTC15m the rate is flat from q = 1 to
q = 20. **Depth is not the constraint. Flow is.**

**1g. Executed flow is thin and lumpy.** 30 BTC5m windows off the public tape:
162 prints, 3,756.14 contracts, **$2,114.51** notional — about **5.4 prints and
$70 of notional per 5-minute window**, against 600–900 contracts resting.
92.3% of that notional falls inside the window where we could be quoting.

---

## 2. The six mechanisms, scored on the evidence that exists

`gross` = pair/roundtrip P&L before fees. `fees` = published schedule.
`net excl subsidy` = gross − fees. `net incl subsidy` counts **only** credited
cash where it exists, and says ACCRUED where it does not. Nothing below is
realized unless the row says so.

| # | mechanism | best measured completion | net per pair, **excl** subsidy | verdict |
|---|---|---|---|---|
| 1 | passive first fill → **passive complement** | **0.50** (YES leg, BTC5m, n=48) / **0.00** (NO leg, n=140) | **+$0.0011337** at basis 0.99 | **the only one with a positive, fee-inclusive, subsidy-free number and a measured frequency** |
| 2 | passive first fill → **same-side maker exit** | — | — | **not a distinct mechanism** — identical to #1 by 1a |
| 3 | passive first fill → **bounded aggressive exit** | n/a | pays maker 1.75c-equivalent + taker 7c-equivalent + the spread | dominated; the spread on these books runs 0.19–0.22 |
| 4 | first fill → **complement with bounded aggressive completion** (hybrid) | recycles **14 of 29** residual episodes vs 5 for maker/maker | `net_locked_usd_if_settled` $0.0141–0.0174 across 59 windows, at a **certain ~9.9c round-trip cost per pair** | **DO NOT PROMOTE** (pre-existing verdict, re-confirmed): "a risk/turnover tool, not a source of pair edge", and by 1b it cannot be sub-par at the instant of completion |
| 5 | **asymmetric quoting / toxicity avoidance** | — | — | **not a standalone loop; it is the admission rule for #1** — and it is now measurable (§3) |
| 6 | **continuous L5-style inventory recycling** | 83.42% observed on the *sports* lane | −$0.125646 per dollar-hour pre-credit, **−$0.072111** with credits | **negative at the current rung**: −$1.84/day, break-even at 84.52–85.28%, gap −1.10 pp. On Gemini the equivalent control has never filled anything. |

Supporting detail for #1 from the offline replay corpus (21 seeded first
fills, `completion_model_b.json`): the best arm, `IMPROVE_1+REPRICE+EXTEND_VETO`,
reached **3 matched pairs of 21 first fills (14.3%)** at a median effective pair
cost of **0.84** with residual still **82.0%** of gross. Complement reach rate
across every arm was **2.0–3.5%**, and same-side EXTENDs filled **1.67–5.0×**
more often than complements. **77.57%** of complement notional arrived *after*
our cycles had ended.

---

## 3. The finding: the toxic side is the *leg*, and it is visible before the fill

This is new since the freeze at `c42c9fe`, and it is the answer to the
pre-registered question "can we identify the toxic first-fill side BEFORE it
fills?" — asked on the earlier 22-fill cohort, answered on a disjoint one.

Cumulative counters from the frozen collector, 2026-09-20T18:24:44Z:

```
BTC5m  PP_F0   cand=140 rested=140 first=88  ffRate=0.629
               compPrice=72 compFill=24  medComplete=12.18s
               matched=24 basis=0.99 net=+0.0011337 (excl subsidy)
               byLeg NO  n=40  completion 0.000
                     YES n=48  completion 0.500  markout@60s +1.5
               perDay cand=3177 first=1997 complete=545

BTC5m  PP_F1   cand=90  first=14  ffRate=0.156
               compPrice=14 compFill=10  medComplete=11.08s
               matched=10 basis=0.99 net=+0.00047 (excl subsidy)
               byLeg YES n=14 completion 0.714 markout@60s +5.0
               perDay cand=2270 first=353 complete=252

BTC15m PP_F0   first=100 compPrice=100 compFill=0
               byLeg NO n=100 completion 0.000 markout@60s -49.0
```

**Pooled across cohorts: the NO leg has 140 first fills and 0 completions. The
YES leg has 48 first fills and 24 completions.** That is not a threshold, a
model or a fitted parameter — it is the side label, the cheapest feature
available, and it is known before the order is placed.

The mechanism is legible on Gemini's single YES tape. A resting NO bid *is* a
YES ask, so a NO fill means an aggressor **bought** YES; completing then needs
YES **selling** to come back, and it does not. A YES bid fills when an aggressor
**sells** YES; completing needs YES buying to come back, and it does, half the
time, in a median **12 seconds**. Directional demand for the UP contract is
persistent; supply mean-reverts.

**Flow evidence also predicts fill quality, which was uncomputable at the
freeze.** F1 (≥1 print) against F0 (no minimum): first-fill rate 0.156 vs
0.629, but completion **0.714 vs 0.353**, markout@60s **+5.0 vs −11.0**,
residual rate **0.286 vs 0.647**. Fewer fills, materially better ones. F2 and
F5 still have zero candidates, so the ≥2 and ≥5 rungs remain uncomputable.

Consistent with L5's own 40,455 maker fills on BTC15m
(`L5_MAKER_FILL_TOXICITY.md`): his maker book is **−0.77¢/$** of adverse
selection against **+0.72¢/$** of modeled rebate — a wash — with an ex-ante
side/price/inventory filter validating out-of-sample at **+6.25¢/$** on 41.7%
of the dollars. *His crypto maker business does not make money on trading; it
makes money on incentives, and the incentives only survive a side filter.*

---

## 4. The candidate, stated as a loop

**PASSIVE YES-ONLY FIRST LEG ON GEMINI BTC5m, PASSIVE NO COMPLEMENT.**

1. **What causes an order** — a passive-pair-admissible state:
   `yes_bid + no_bid + maker fees ≤ 1.00 − min_edge`, quote two-sided,
   inventory headroom, TTE inside the entry band, **≥1 WS print of flow
   evidence** (the F1 rung).
2. **Which side** — YES only. Never rest the first leg on NO.
3. **What price** — the YES bid, maker-or-cancel, inside $0.20–$0.80 so the
   rebate is eligible.
4. **What size** — q ≥ 1 contract, for the reason in 1d.
5. **After a fill** — rest the NO complement at a price that keeps the pair
   basis fee-safe. Do not EXTEND. Do not cross.
6. **How we get flat** — the complement fills (median 12 s), or the contract
   settles and the residual is a directional outcome.
7. **Net per completed cycle** — **+$0.0011337** per contract-pair at basis
   0.99, fees in, subsidy out. Rebate on top at P = 0.50 **accrues** a further
   ~$0.0105 per pair, which becomes cash only above the daily cent (1d).
8. **Frequency** — F1: **252 completions/day** from 353 first fills/day.
   F0: 545 from 1,997.
9. **Capital** — ~$1 per contract-pair for a median 12 s, plus residual held to
   expiry (≤ 300 s on BTC5m). Trivial against a $111 bankroll; capital has
   never been the constraint (mean utilisation 0.93%).
10. **Residual when completion fails** — **THIS IS THE HOLE. See §5.**

Estimated trading net excluding subsidy, q = 1: F1 ≈ **$0.12/day**,
F0 ≈ **$0.67/day**. Depth supports q = 5, 10 and 20 outside the final minute
(1f), so the line scales roughly linearly to **$0.6 / $1.2 / $2.4 per day**
(F1) — *if and only if the residual term in §5 is not negative, which is
unknown*. These are shadow extrapolations from an upper-bound fill model over
a span of hours, not a forecast.

---

## 5. Why this does NOT yet qualify for a live probe — the one missing fact

The loop's expectancy is
`P(complete) × pair_net + (1 − P(complete)) × residual_settlement`.

At F1, `P(complete) = 0.714` and `pair_net = +$0.00047`. The first term is
worth **+$0.00034 per cycle**. The residual term carries **$0.20–$0.80 of
directional exposure per contract** and is multiplied by 0.286.

**A residual averaging just −$0.0012 per contract — one eighth of one tick —
makes the entire loop negative.** The pair term is three orders of magnitude
smaller than the exposure the residual term carries. Nothing else in the
calculation can move the sign; only this can.

**And it has never been measured.** `residSettle` reads `None` on every arm,
on every window, since the collector started. Tracing it:

- `fill_sequence.Sequence.row()` computes `residual_settlement_value` only when
  `settlement_leg in ("YES","NO")`;
- `arm.finish(book=..., settlement=None)` — the collector calls
  `arm.finish(book=book)` at `shadow_soak.py:448` and **never passes a
  settlement**;
- so `settlement_leg` is always `None`, and step 11 of the sequence schema
  ("terminal — settlement where the venue has said") has never executed once.

This is a wiring gap, not a research programme. The outcome is **public and
read-only**: `metadata.resolution_for(symbol)` already returns the contract
object's `resolutionSide`, with a strike-consistency check and an
N-read rule for the endpoint's known flapping.

**A second, urgent gap found in the same trace: the fill-sequence ledger is not
durable.** `registry.CohortState.save()` persists `hazard`, `spreads` and
`toxicity` — and not `fills`. The 88 first fills and 24 matched pairs quoted in
§3 exist only in the process's memory, under `restartPolicyType: ALWAYS`. A
crash loses the entire cohort. This is the same class of defect the freeze
commit repaired for the toxicity dataset and missed here.

### The bounded probe specification is therefore NOT issued

No live money. What is authorised by "collect only that" is exactly two
things, both read-only and both repairs:

1. pass the venue's published `resolutionSide` into the ledger at rollover, so
   the residual's settlement value is recorded;
2. persist the fill-sequence ledger, so the cohort survives a restart.

**What would make this probe-ready:** a settled-residual sample large enough
to bound `E[residual_settlement | YES first leg, F1 admission]` away from
−$0.0012 per contract. At 353 first fills/day and a 0.286 residual rate that
is ~100 labelled residuals per day, so the answer is days, not weeks — and it
costs nothing.

**If that number comes back non-negative**, the probe to write is:
one market, one outstanding order, maker-or-cancel, YES first leg only,
**q = 1** (never 0.01), price inside $0.20–$0.80, entry band excluding the
final 60 s, hard cash cap, hard residual cap of one contract, daily realized
loss stop, authoritative order/inventory reconciliation before every mutation,
deadman cancel at window end, settlement and cash reconciliation after, and no
automatic repeat. It is not written here because it is not yet earned.

---

## 6. What this does not claim

- No fill above is a real fill. `HypotheticalBook` is an explicit **upper
  bound**; F0's 0.63 first-fill rate is closer to its loosest than F1's 0.16.
- `basis=0.99` and `net=+0.0011337` are medians over 24 shadow pairs in one
  day, not a distribution.
- The per-day figures extrapolate a few hours of observation.
- No rebate above is realized. The $0.51 is; everything computed from the
  published formula is an accrual and is reported on its own line.
- The YES/NO asymmetry is the strongest result here, and it is still 188 fills
  over one day on one venue. It is a pre-registered hypothesis confirmed on a
  second cohort, not a settled fact.
