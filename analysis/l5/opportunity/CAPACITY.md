# LANE E — capacity, in two estimates that are never merged

**(a) defensible executable capacity** is how much notional the observed book
could absorb passively, with the evidence it rests on. **(b) realized-dollars/day
potential** is built from EVIDENCED fills only. A capacity number is a capacity
number; **it is not a revenue forecast**, and nothing in (a) is permitted to
appear in (b).

Accounting labels per CLAUDE.md: *historical replay → shadow → live probe →
submitted → filled → settled → realized*. Theoretical fills, stale public BBO
opportunities, modeled pair profit without fills, shadow inventory,
mark-to-market gains and estimated rebates are **never** called realized. Where
only a theoretical figure exists it is labelled **SHADOW/THEORETICAL in the same
sentence as the number**.

---

## (a) DEFENSIBLE EXECUTABLE CAPACITY

### a.1 The evidenced figure, and it is small

The largest passive, two-sided notional our frozen mechanics have actually
absorbed in one market is

> **$18.8307 cost basis, 11 maker fills, 0 taker fills, 19.87 matched pairs at a
> pair cost of 0.9477, terminal residual 0.00** —
> `aec-setkamecz-mulpat-holtom-2026-09-10`, venue-credited $0.10 maker rebate.
> *Source: `data/pmus/sports_incentives_2026-09-10/credited_reconcile.json`.*

That is the single best capacity datapoint in the repo: a complete OPEN → EXTEND
→ COMPLETE sequence, filled passively, closed to zero residual, inside spring
discipline. Supporting evidenced builds, all post-only at the touch:

| market | category | maker fills | notional absorbed | matched pairs | pair cost | residual |
|---|---|---:|---:|---:|---:|---:|
| `aec-setkamecz-mulpat-holtom-2026-09-10` | tennis | 11 | **$18.8307** | 19.87 | **0.9477** | 0.00 |
| `asc-lal-lev-fcb-2026-09-13-pos-2pt5` | soccer total | 6 | $6.8096 | 7.07 | 0.9477 | 0.28 |
| `atc-epl-cov-bha-2026-09-13-bha` | soccer | 7 | $4.3296 | 5.67 | **0.7636** | 0.00 |
| `aec-atp-jayfri-maxbas-2026-09-13` | tennis | 4 | $3.9506 | 0 | — | +8.58 |
| `aec-cs2-ica-ff-2026-09-10` | esports | 6 | $8.5228 | 4.62 | 0.98 | −9.08 |
| Probe 07 `cpc-btc-updown-15m-...-1945z` | crypto | 9 | $5.3198 gross | 2.47 | 1.0299 | 2.81 |
| Probe 08 `cpc-btc-updown-15m-...` | crypto | 5 | $5.3198 | 3.93 | 1.1986 | 0.79 |

**Largest single passive order ever filled on our account: 2.25 shares, $1.9800**
(Probe 06, `CQ93KVNFWYCC`, 2.25 @ 0.88, `ORDER_STATE_FILLED`, `leavesQuantity 0`).
Every one of the nine Probe 07 orders rested and filled at exactly its limit
price, with real rest durations 1.5 s – 204.8 s. So **passive absorption at the
0.01-share minimum is demonstrated; passive absorption at size is not.**

### a.2 Book depth is far larger — and is NOT our capacity

Observed touch depth, one side, from the habitat sweeps. Converted to notional at
the observed touch price band for scale only.

| category / group | touch depth (contracts) | ≈ one-sided notional at the touch | basis |
|---|---:|---:|---|
| `nfl\|moneyline\|PRE` | 2,252,045 | ~$790,000 | 2 mk / 408 st |
| `sea\|soccer_team_full_time_winner` | 45,538 | ~$16,000 | 3 mk / 224 st |
| `wnba\|moneyline\|LIVE` | 20,329 | ~$7,100 | 1 mk / 204 st |
| `sea\|soccer_team_full_game_total` | 12,535 | ~$4,400 | 3 mk / 224 st |
| `mlb\|moneyline\|LIVE` | 3,951 bid / 68,095 ask | ~$1,380 | 8 mk / 1,339 st |
| `atpcq\|tennis_match_winner` | 3,773 | ~$1,320 | 12 mk / 1,382 st |
| `cpc-btc-updown-15m` | 2,600 bid / 10,000 ask | ~$546 | 167 samples, 1 window |
| `ufc\|moneyline\|LIVE` | 360 bid / 257 ask | ~$130 | 1 mk / 39 st |
| `mls\|drawable_outcome` | 225 | ~$79 | 14 mk / 2,847 st |
| `cs2\|esports_match_winner` | 142 | ~$50 | 7 mk / 648 st |
| `lol\|esports_match_winner` | 112 | ~$39 | 4 mk / 294 st |

**The defensible reading of this table is a negative one.** Even the thinnest
tight-spread category on PMUS shows ~$39 resting at the touch, which is **~8×**
our entire $5/market envelope, and the deep categories exceed it by three to five
orders of magnitude. **Depth is not the binding constraint in any category, by a
very large margin.** What is unproven everywhere is **queue position**: the
shadow module's own label is explicit that every modelled fill is the `possible`
class, *"an UPPER BOUND, queue position UNPROVEN"*, and the no-fill census bins a
whole bucket as `QUEUE_BOUND` — flow arrived and the size resting ahead absorbed
all of it. Converting `QUEUE_BOUND` costs a tick, priced at **62× L5's own
measured drag**. So the depth column is an upper bound on the book, not a capacity
we can claim.

### a.3 What actually binds, with the evidence

| constraint | observed value | binds? |
|---|---|---|
| frozen envelope | **$5 per market, $5 total worst-case**, residual ≤ 10 shares, 1 concurrent market | **YES — this is the binding constraint** |
| peak risk exposure against it | Probe 07 **$1.38** / Probe 08 **$4.78** against $5.00 | approached, never breached |
| bankroll | **$111.00**, mean capital in use **$1.0309 (0.93%)**, largest single position $19.3141 (17.4%) | **NO. "More money buys nothing while concurrency is 1."** |
| candidate supply | 222 flat selection cycles; **41.9% admitted ≥2 markets** | would support a 2nd slot ~2 cycles in 5 — a *supply* measurement, not a P&L one |
| concurrency P&L | **UNVERIFIABLE** — on held cycles `select_candidate` is never reached, so the universe a 2nd slot would draw from was never recorded | — |
| **completion rate** | observed **83.42%** against break-even **84.52%** | **YES — "capacity is not the constraint and capital is not the constraint, completion is"** |

### a.4 The defensible capacity figure, stated once

> **Per market, evidenced: $18.83 of passive two-sided notional absorbed in a
> single market by post-only joins at the touch, with 11 maker fills and zero
> terminal residual. Per single order, evidenced: $1.98. Against a frozen
> envelope of $5/market and one concurrent market, the observed book in every
> tight-spread PMUS category could absorb the whole envelope several times over
> at the touch — but queue position is unproven, so the depth column above is an
> upper bound on the book and not a capacity claim.**

---

## (b) REALIZED-DOLLARS/DAY POTENTIAL — EVIDENCED FILLS ONLY

### b.1 Every evidenced, venue-confirmed, settled cohort in the repo

Kept as separate cohorts rather than one sum, because they come from different
modules with different reconciliation bases; the date ranges do not overlap, so
they are additive, but presenting one merged number would hide that.

| cohort | dates | markets | turnover | **realized net (venue-confirmed)** | rebates credited | liquidity rewards credited |
|---|---|---:|---:|---:|---:|---:|
| R1→R4 PMUS sports scoreboard | 2026-09-05…07 | 26 | $56.06 | **−$1.4690** | +$0.42 (25 prints) | **$0.00** |
| 2026-09-10 settled sports | 2026-09-10 | 2 settled of 3 traded | $27.35 across all 3 | **−$7.5056** (the 2 with a venue number) | +$0.17 | **$0.00** |
| R5F realized bridge | 2026-09-13 | — | — | **−$10.3782** | +$2.13 | **$0.00** (1 PENDING $0.40, 1 SKIPPED $1.08 — neither is money) |
| BTC15m Probe 07 | 2026-09-25 | 1 | ~$5.5 gross | **−$0.7718** | +$0.01 | **$0.00** |
| BTC15m Probe 08 | 2026-09-25 | 1 | $5.32 | **−$0.5598** | +$0.04 | **$0.00** |
| B′ RESIDUAL_CAP_k2 (closed) | 2026-09-27 | 4 | — | **−$1.5530** | $0.00 | **$0.00** |
| Gemini underdog cohort 1 (different venue, closed) | — | 5 | $2.17 | **−$2.27** | $0.00 | **$0.00** |

Sum of the PMUS rows: **−$22.2374** across roughly **7 distinct money-moving
days**, i.e. about **−$3.18 per active day**. Every cohort is negative. The
*entire* confirmed incentive income of the programme, across every category and
every venue, is **$2.77 of credited maker rebates and $0.00 of credited liquidity
rewards**.

### b.2 The best any category does, evidenced

| category | markets | turnover | realized net | per-market mean | s.e. | status |
|---|---:|---:|---:|---:|---:|---|
| tennis | 9 | $20.80 | **+$0.102** | +$0.0508 (at size, n=3) | **$1.1683** | sign not determined |
| esports | 2 | $0.18 | +$0.0188 | — | — | below rebate floor; shape only |
| soccer | 4 | $0.16 | −$0.0132 | — | — | below rebate floor |
| baseball | 6 | $0.24 | −$0.0290 | — | — | below rebate floor |
| football | 5 | $34.68 | **−$1.5476** | −$0.5101 | $2.9345 | worst |
| crypto BTC15m | 2 maker probes (07, 08) | ~$10.8 gross | **−$1.3316** | −$0.6658 | — | pair costs above par |

Tennis is the only category with a positive evidenced sign, and its standard
error ($1.1683 per market at n = 3 at-size) is **23× the mean**. Gate G1 —
*realized net ≥ 0 over ≥ 5 at-size markets with a sign on the mean* — is **NOT
met**, in any category.

### b.3 The figure, stated plainly

> **The evidenced realized-dollars/day potential is $0.00/day.**
>
> Not a small positive number. **Zero.** There is no category, on either venue,
> in which this account has an evidenced positive realized rate. The nearest
> thing to one is tennis at +$0.102 total across nine markets and 16.6
> capital-hours, whose sign is not determined. I am reporting zero rather than
> substituting a theoretical number, as instructed.

The forward-looking statement the repo's own capacity curve supports is also
negative and is **not** a realized figure: at the observed 83.42% completion the
lane runs at **−$1.84/day**, break-even arrives at **84.52%**, and *"scaling
multiplies unit economics, it does not repair them, so every axis of growth makes
the loss bigger until completion clears break-even."*

Against the historical target of **$480/day realized net** — which CLAUDE.md
requires be treated as a target to measure against, never an assumption — the
evidenced rate is **zero**, and the gap is the entire programme.

### b.4 SHADOW/THEORETICAL ceilings, kept separate and labelled

Recorded only so that nobody mistakes them for (b.3), and so the one arithmetic
coincidence in them is defused rather than quoted later.

- **SHADOW/THEORETICAL:** at a ≥5¢ gross pair edge the exploratory top-of-book
  study puts net at **+$0.03307/contract**, needing **151 contracts/window** to
  make $5/window — which, at 96 BTC15m windows/day, is a **SHADOW/THEORETICAL
  ~$480/day**. That number is **not evidence of anything**, and the resemblance
  to the $480/day historical target is arithmetic coincidence, not corroboration.
  It requires 151 contracts passively filled **on both sides** in one window
  against our evidenced maximum of **4.72 shares gross** in a window (**32×**),
  against a **$5/market** envelope, from a study its own header bars from
  selecting anything (*"THIS IS NOT THE PREREGISTERED TEST … no threshold or rule
  derived here may be used to select anything"*), and on a venue where 60.7% of
  windows carry zero volume.
- **SHADOW/THEORETICAL:** the frozen-model shadow over the live slate produced
  **15 `possible` fills across 5 markets of 14 with enough observations, 0
  opposite-side COMPLETE fills and 0 matched units** — every fill an upper bound
  with queue position unproven.
- **SHADOW/THEORETICAL:** `realized_shadow` of **−$102.77** on 364 decided maker
  quotes with 11 evidenced fills and **matched-pair P&L of exactly $0.00** — a
  settled counterfactual, never submitted, no cash moved. The finding there is the
  **zero**, not the minus: 100% of that shadow position was residual directional
  exposure, which CLAUDE.md states has no demonstrated predictive skill.
- **LOCKED, NOT REALIZED:** the R5F run's locked matched-pair economics of
  **+$1.7100** on 12.74 matched units. Locked means the two legs are held. It is
  not settled and is not realized, and is not added to any realized figure here.

---

## (c) What this lane concludes

1. **BTC15m is not established as the destination.** It wins the clock axis
   outright and nothing else; its evidenced pair costs (1.0299, 1.1986) are the
   worst we hold. Tennis and soccer hold the only evidenced pair costs inside the
   ≤0.96 spring discipline (0.9477, 0.7636) — on one market each.
2. **Capacity is not the constraint.** Depth exceeds our envelope by 8× in the
   thinnest tight category and by 100,000× in the deepest. Bankroll utilisation
   is 0.93%. Completion rate is the constraint, and it sits 1.1 points below
   break-even.
3. **The evidenced realized rate is zero**, and the entire confirmed incentive
   income of the programme is $2.77.
4. **The measurement that would move this lane does not exist**: no post-fill
   markout has ever been computed on our own fills, in any category, on either
   venue; every football reach observation is pregame; and both sports habitat
   sweeps ran off-peak and pre-registered a peak-hours repeat that has not
   happened. Those three are the cheapest next measurements available, and none
   of them moves money.

Per-axis rows, sample sizes and per-category executability conditions are in
`CATEGORY_RANKING.md` and `CATEGORY_RANKING.json` beside this file.
