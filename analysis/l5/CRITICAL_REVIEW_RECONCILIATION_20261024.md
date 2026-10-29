# Critical review: reconciliation, retractions, and one candidate

**2026-10-24.** Live trading remains unarmed and nothing here is deployed to
the money path. Three of my earlier claims were wrong and are retracted below
with the cause of each error.

---

## 1. Reconciled profitability table — ONE authoritative figure

Both earlier figures came from the same raw files; the difference was
entirely **my dedup key**.

| variant | identity key | cash basis | rows | buy_out | redeem_in | net |
|---|---|---|---|---|---|---|
| **AUTHORITATIVE** | txHash, asset, ts, size, type, **side** | `usdcSize` | **686,395** | **6,070,392.86** | **6,415,918.50** | **+$345,525.64** |
| same, size×price | ″ | size×price | 686,395 | 6,042,842.93 | 6,415,918.50 | +373,075.58 |
| my earlier run | txHash, asset, ts, size, **price**, type | `usdcSize` | 702,565 | 6,398,129.92 | 6,415,918.50 | +17,788.58 |
| my earlier run | ″ | size×price | 699,320 | 6,348,684.74 | 6,394,062.58 | **+45,377.84** |

**The authoritative row reproduces `l5_btc15m_realized_economics.json` to the
cent**, including `series_rows`, `cash_deployed_all` and the headline total.

**Cause of my error.** The fetch made **18 repeated passes over the early
offsets** (offset 0 appears 18 times, 500 and 1000 seventeen times each,
across 1,570 pages). A re-fetched trade returns with a differently-rounded
float `price`, so keying on `price` kept both copies — 16,170 surplus rows and
**+$327,737 of phantom spend**, which is the whole discrepancy. The
authoritative key omits `price` for exactly this reason and carries `side`
instead, because one `transactionHash` holds many fills.

### The authoritative figure, fully specified

| | |
|---|---|
| source | `data/l5/raw/activity.jsonl` + `activity_gap.jsonl.gz` (sha256 pinned in the artefact) |
| population | unique by (txHash, asset, timestamp, size, type, side); `proxyWallet` == L5; `"btc-updown-15m"` as substring of `slug` |
| exclusions | MAKER_REBATE / TAKER_REBATE / REWARD excluded from P&L; **`incentives_series` is empty — zero incentives in this series** |
| formula | `redeem_in + sell_in − buy_out`, all `usdcSize` |
| settlement | 13 unresolved windows booked as **total loss of spend** (worst case) |
| **net** | **+$345,525.64** over **5,546** windows / **127 active days** |
| per window | mean **+$62.30**, median +$44.55; 4,056 wins / 1,490 losses (73.1%) |
| per day | **+$2,720/active day**; return on deployed cash **+5.69%** |
| legs | **680,690** BUY legs, **0 SELL rows**, median 110/window, **5,360/day** |

### The matched/residual split is NOT identifiable

Same data, same exact total, three pairing conventions:

| convention | MATCHED | RESIDUAL | sum |
|---|---|---|---|
| FIFO | +$60,080 | +$289,140 | +$349,220 |
| LIFO | +$89,800 | +$259,419 | +$349,220 |
| average price | +$167,256 | +$181,964 | +$349,220 |

**Retraction 1.** I claimed "residual is a net drag of −$139,125, eating 74%
of matched profit", then later "residual is 83% of profit". **Both are
wrong.** The first came from the corrupt dedup; the second is a FIFO artifact.
The matched share ranges from 17% to 48% depending purely on convention.

**What survives, convention-free:** both components are positive under every
convention; residual redeems **$0.527 per residual contract** against an
overall average acquisition price of **$0.4753 per contract**; matched pairs
5,815,479; residual contracts 1,139,900 → **0.196 residual per pair**.

---

## 2. Classifier audit — my action mix was wrong

The classifier tested `held_other > 0`. That calls buying the side you are
*already net short* a complement, because you do hold some of the other side.
The correct test is the change in `|up − dn|`.

| action | corrected (by \|imbalance\|) | my earlier claim |
|---|---|---|
| OPEN | 5,546 — **0.81%** | 0.8% |
| **EXTEND** | 368,977 — **54.21%** | 2.8% ✗ |
| **REDUCE** | 305,704 — **44.91%** | 96.3% ✗ |
| FLATTEN | 463 — 0.07% | 0.1% |

**Retraction 2.** "L5 is overwhelmingly a complementer (96.3%)" is false, and
so is my claim that this **supersedes the operating model's EXTEND-66.2%
thesis**. The operating model was substantially right and I was wrong: L5 runs
a roughly balanced 54/45 extend-reduce mix.

**Retraction 3.** "Near settlement he goes 100% COMPLEMENT" is false. The mix
is **flat to the bell** — EXTEND is 54.6% in T−15..10m and 56.9% in the final
30 seconds. Only OPEN collapses (2.78% → 0.01%).

Per-trade ledger (`l5_authoritative_reconstruction.py`) now records inventory
before/after, newly matched contracts, unmatched after, acquisition cost and
marginal pair cost for every one of the 680,690 legs. Marginal pair cost
(FIFO, so convention-dependent): median **0.9800**, p05 0.75, p95 1.2189;
≤0.96 for 40.5% of matching trades, ≤0.98 for 50.5%, ≤1.00 for 62.1% —
**37.9% of his matching trades pair above $1.00.**

---

## 3. Raw-data integrity, and the 680,690 vs 12,913 reconciliation

**Duplicated snapshots: confirmed and quantified.** 1,570 pages, 784,500 rows
seen, 18 offsets fetched 17–18 times each. Dedup choice swings the population
5.4% (666,686 to 702,595 unique btc15m rows).

**The 12,913 figure is coverage, not granularity:**

| dataset | btc15m rows | pages | span |
|---|---|---|---|
| `trades.jsonl` | 9,048 | 22 | **0.5 days** (2026-08-30) |
| `trades_takeronly.jsonl` | 3,373 | 27 | 4.0 days |
| **activity (authoritative)** | **680,690 legs** | 1,570 | **127 active days** |

9,048 + 3,373 = 12,421 ≈ 12,913. Both `/trades` extracts hit the API offset
cap at 22 and 27 pages. On 2026-08-30 the authoritative corpus has **16,414
legs** while `trades.jsonl` captured 9,048 for a partial half-day —
consistent. **No granularity conflict: 5,360 legs/day sustained, against a
truncated endpoint that captured at most four days.**

---

## 4. Audit of "OPEN negative in 66/66 cells" — RETRACTED

The scorer set `P(complete) = 0` whenever the **at-fill** pair exceeded the
ceiling, treating later reachability as impossible. That is wrong, and our own
data disproves it: **1515z completed at a pair of 0.92 — five ticks better
than the 0.97 at-fill pair.** An initially unreachable complement did become
reachable.

Observed `P(complete | fill)` at ceiling 0.96 is **1/5 = 20%**, not 0. With
that and the observed 0.08 margin, and residual treated as EV-neutral:

```
EV/window = 0.20 × 0.08 × 3  +  0.80 × 0  =  +0.048   (marginally POSITIVE)
observed  = −0.744/window
```

### The gap is the residual realization, and it is not distinguishable from luck

The four stranded legs filled at 0.22 / 0.44 / 0.40 / 0.19 — their own fill
prices are the market's breakeven probabilities. All four expired worthless.

```
P(all four worthless | fair pricing) = 0.78×0.56×0.60×0.81 = 0.2123
expected residual redemption = $3.96        realized = $0.00
```

**A 21.2% outcome. Stage 1's −$3.7194 is therefore NOT evidence that the
policy is miscalibrated, and no policy change is justified by that loss.**
This is the central conclusion of the review and it contradicts the direction
of my previous two recommendations.

---

## 5. Chronological out-of-sample, after reconciliation

| | matched | residual | net | per window | resid/pair | win rate |
|---|---|---|---|---|---|---|
| In-sample (first 70%, n=3,873) | +$34,254 | +$147,189 | **+$181,443** | +$46.85 | 0.2182 | 72.0% |
| **Out-of-sample (last 30%, n=1,660)** | +$25,826 | +$141,951 | **+$167,777** | **+$101.07** | 0.1753 | **77.2%** |

**Retraction 4.** I reported a 3.3× out-of-sample decay ($11.23 → $3.36). On
the authoritative data there is **no decay** — out-of-sample is *better* per
window and the win rate *rises*. The earlier decay was an artifact of the
corrupt dedup. (Matched and residual are shown under FIFO and remain
convention-dependent; the net column is not.)

---

## 6. Ceiling comparison on actual observed paths, no lookahead, no touch=fill

Minimum post-fill pair actually observed, per window:

| window | first leg | min observed pair | 0.96 | 0.97 | 0.98 |
|---|---|---|---|---|---|
| 1330z | 4.09 UP @ 0.22 | 0.9900 | no | no | no |
| 1500z | 3.00 UP @ 0.44 | 1.0400 | no | no | no |
| 1515z | 3.00 @ 0.30 | **completed at 0.92** | ✓ | ✓ | ✓ |
| 1530z | 3.00 DN @ 0.40 | 1.0400 | no | no | no |
| 1545z | 2.84 DN @ 0.19 | 0.9800 | no | no | **attempt** |

| ceiling | completions | new attempts | upper-bound net |
|---|---|---|---|
| 0.96 | 1515z | — | −$3.7194 |
| 0.97 | 1515z | — | −$3.7194 (**identical**) |
| 0.98 | 1515z | 1545z | −$3.1230 *if it filled* |

**0.96 and 0.97 are indistinguishable on our data.** 0.98 adds exactly one
attempt worth at most **+$0.596**, and the campaign stays negative.

**And the newly-validated shadow data argues against assuming it would
fill.** Live records show `queue_ahead_bound` of 6,695.72 and 7,277.91 against
a need of 2–3 contracts — **queue multiples of 2,231× and 3,638×**. Every
modelled fill in those records carries `swept: true`: at our size we fill only
when a print sweeps the entire level, which is the definition of adverse
selection. Additional completion volume at a looser ceiling therefore cannot
be assumed to materialise, and this is why I am not recommending the ceiling
change.

---

## 7. Is the deployed module producing usable shadow observations?

Separating the two questions the owner distinguished:

| | status | evidence |
|---|---|---|
| module attached | **yes** | `attached: true`, `attach_errors: {}` |
| ring fed from live feed | **yes** | 6,874 appended / 2,180 deduped in 1800z, `errors: {}` |
| unmatched-inventory path | **VALIDATED** | `shadow_sampler: emitted 180, skipped_not_due 176, tracked_slugs 4` |
| fill path | **VALIDATED on modelled fills** | `L5_SHADOW_FILL_BOOK_EVIDENCE` with full reachability, `queue_ahead_at_placement` 2,903.69 / 7,655.45, `volume_through_level`, 92 bid rungs, taker walk with `insufficient_displayed_depth` |
| **fill-TIME (event-time) evidence** | **NOT VALIDATED** | every record reads `book_at_event.sync.basis = NO_VENUE_EVENT_TIME_SUPPLIED`, `row: null` |
| money-path sampler | never fired | `sampler: emitted 0` — correct, no live inventory |

A modelled fill has no venue timestamp and is not allowed to borrow ours, so
**event-time book reconstruction remains unexercised and can only be
validated by a real venue fill.** An attached module is not fill-time
evidence, and this report does not claim otherwise.

---

## 8. The strongest supported L5 behavioral finding

> **L5 commits his directional decision once, immediately, and then works the
> inventory continuously for the rest of the window — never selling, and
> never changing regime near settlement.**

Every component is convention-free and drawn from all 5,546 windows:

- **exactly one OPEN per window** (5,546 opens / 5,546 windows), at a median
  **16 seconds** into the window (p05 6s, p95 216s);
- OPEN is 2.78% of actions in T−15..10m and **0.01–0.07% thereafter** — he
  never re-opens;
- after that, **110 legs per window** (p95 275, max 756) at a **median
  2-second** spacing, split **54.21% EXTEND / 44.91% REDUCE**;
- that split is **flat to the bell** (56.93% EXTEND in the final 30 seconds) —
  no settlement-driven behavior change;
- **zero SELL rows in 680,690 legs** — he exits only by settlement;
- he ends flat in **1 window out of 5,546**, carrying **0.196 residual
  contracts per matched pair**.

**The discrepancy against us, in the same units:** we placed **1.2 legs per
window** at **33-second** complement spacing and ended with **4.31 residual
contracts per matched pair** — **22× L5's ratio**. The gap is participation
rate and inventory working, not pair pricing.

---

## 9. One candidate change

> **Make complement re-evaluation event-driven — re-price on book change
> while unmatched inventory exists — leaving the 0.96 ceiling, sizing,
> concurrency, capital and every risk limit unchanged.**

**Why this one.** It is the only change that moves us toward the single
robust L5 behavioral finding (continuous inventory working, 2s vs our 33s —
a 16× gap) while being **strictly non-increasing in risk**: more looks can
only discover a qualifying complement sooner, never place a larger order,
never add an order, and never loosen a ceiling. It needs no new parameter.

**Expected economic impact, stated honestly:**

- **On the Stage 1 sample: +$0.00.** The minimum observed post-fill pairs were
  0.99 / 1.04 / 1.04 / 0.98 — none reached 0.96, so no faster look would have
  found a qualifying moment. I am not claiming a gain I cannot measure.
- Its entire potential value is in **sub-33-second qualifying moments that the
  33-second sampling cannot see**. The ring now samples the book continuously
  (6,874 frames/window) and can measure their frequency and duration directly.

**Uncertainty — high, and in three specific places:**

1. **Unknown frequency** of sub-cadence qualifying moments. This is the whole
   value and it is currently unmeasured.
2. **Queue depth may make it moot.** Observed `queue_ahead` is 2,231–3,638×
   our size, and every modelled fill was `swept: true`. Finding a qualifying
   price sooner does not mean filling at it.
3. **Residual EV.** Our own residual sample is 4 legs, all worthless, at
   p=0.21 — indistinguishable from fair. L5's aggregate implies residual is
   roughly fair ($0.527 redeemed vs $0.4753 average cost). If residual is
   genuinely fair, completion is worth only the pair margin and the upside of
   any cadence change is small.

**What I am NOT recommending, and why:** the 0.96→0.98 ceiling change
(indistinguishable at 0.97, worth ≤+$0.596 at 0.98, and undermined by the
queue evidence); two concurrent orders (doubles adverse-selection directions
at a size where one unmatched leg dominates); and deeper resting (selects
harder for large adverse moves). I previously recommended two of these; both
rested on the corrupted decomposition and on the 66/66 claim, and both are
withdrawn.

**Authorization: not sought.** Stage 1's loss is not statistically
distinguishable from bad luck, so it does not justify a policy change, and the
candidate above has a measured impact of zero on the only sample we have. The
correct next step is measurement, not deployment: run the candidate as a
shadow arm beside the unchanged baseline over ≥200 windows and report the
frequency and fillability of sub-cadence qualifying moments. **If that
frequency is near zero, the candidate should be dropped rather than
promoted.**
