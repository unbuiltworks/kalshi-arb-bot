# OBJECTIVE CHANGE — PROFIT FIRST

**The objective is no longer L5 replication.** It is: an executable
prediction-market system averaging **≥$5 realized net per eligible/traded
15-minute window**, under a hard **$200/window capital ceiling** with bounded
residual and loss exposure. L5 evidence stays available as research input;
reproducing L5 behaviour is no longer required.

**Scoreboard, as revised:** the headline number is **realized net dollars per
day**, with $5 average net per *traded* opportunity as the unit target.
Windows the system declines are not failures — a system that says WAIT in
mediocre windows and earns $8 on the ones it trades beats one that forces
$5 into all 96 daily windows. $200 is a ceiling, never a requirement, and
$5 is a target, never permission to add risk.

## Frozen as completed evidence — not to be reopened

| artifact | result |
|---|---|
| A / Am / B (FAVORITE_DCA) | −$1.178 / −$0.536 / +$2.824 per window (B = one window in 28) |
| C (TWO_SIDED_FAVORITE_TRAIL) | −$0.157/window |
| D0–D4 (no-seed trail + execution variants) | D0 −$0.0064, D2 −$0.0006, D3 −$0.0029, D4 −$0.1895 |
| V1 / V1-no-seed (manual-style accumulator) | −$1.201 / −$1.178, gross negative |
| Cell C 2×2 attribution, anatomy, early-path study | the exit carries the edge; 64.3% of episodes fail to clear their own fee; nothing actionable at 1–2 s; early termination cannot recover fees |

**Cell C optimization is closed.** The binding finding carried forward: fees
are front-loaded and symmetric, so the only avoidable fee is one never
incurred — **entry selection, not exit timing.**

## Current state, established

**Repo.** `unbuiltworks/kalshi-arb-bot`, branch
`claude/l5-pmus-economic-results-7c116e` at `ca723bc8`, clean. Gemini work
lives on `claude/gemini-btc-economic-viability-h3u7zl`, not on main.

**PMUS deployment.** `pmus-ws-evidence-recorder` is **live and healthy** —
authenticated socket connected, 2.1M messages, both BTC15m windows
subscribed, book and trade tapes emitting, 0 starved reconnects. The
executor (`pmus-q1-executor`) has **no WS→order path** and is not running.

**Kalshi deployment.** Project `kalshi-longshot-fader`: every deployment is
**REMOVED**; last deploy 2026-08-20, removed 2026-09-21. **The bot is not
running.**

## Lane 3 first, because it changes the plan: the Kalshi bot does not work

I was asked to establish what "works" means from repository and account
evidence. Its own authoritative ledger says it does not.

From `GROUND_TRUTH_APR26.md`, sourced to **Kalshi's own transaction CSV**,
not an API reconstruction:

> **Kalshi-Transactions-2026.csv export, last 30d: −$5,306 net.**

Run-rate **−$176/day**. The same document works through the patches then in
flight and concludes:

> Forward-looking run-rate: from −$176/day to roughly **−$80 to −$120/day**
> … The strategy is still meaningfully bleeding even with the patches.

It also records that an earlier in-repo reconstruction said −$861 and was
wrong by ~6×, and that API-derived estimates ran **2.2× inflated** against
the CSV. The repository has already been through exactly the measurement
failure this programme has been guarding against.

**What is genuinely there is a concentration result**, from
`PROFITABLE_CELLS_APR27.md` over 9,470 trades: 18 cells clear a strict
profitability bar, the top 10 produce **+$3,456/30d**, while losing cells
produce **−$6,073**, netting **−$1,282**. Its own caveat is the right one:

> that's an in-sample selection, so the realistic out-of-sample expectation
> is 50-70% of the headline number.

**Status: the Kalshi lane is a hypothesis about cell selection, not a
working bot to scale.** Its next step is not deployment — it is to establish
whether any of those cells survive out of sample on data after April 2026,
using the CSV as the authoritative ledger. The evidence in-repo stops at
late April; the account may hold five further months that would settle it.

**I have not copied any Kalshi threshold into PMUS and will not.**

## The three lanes

### Lane 1 — PMUS BTC15m directional state/value *(primary)*

Return to the late-favorite / opposite-longshot question rather than another
favorite-DCA variant. Estimate conditional settlement probability from
contemporaneous information only — seconds remaining, BTC distance from
strike, recent BTC path/volatility, executable UP/DOWN prices, flow and book
state — and compare the calibrated probability against **executable** prices
after canonical fees. Expressions tested: BUY favorite, BUY longshot, exit
where mechanically valid, and **WAIT**.

Assets already in hand: 28 gate-passing authenticated windows (book + trade,
top-of-book), the BRTI/Coinbase reference recorder, the authoritative fee
module, and a fill methodology that has survived five adversarial audits.

Known constraint from this programme: the displayed favorite is not
predictive, pre-entry observables separate weakly (gross/fees 0.36 → 1.56),
and anything built here must clear ~1.2¢/share round-trip.

### Lane 2 — Gemini BTC5m *(kept entirely separate)*

Measurement-first, unchanged: real flow, defensible resting-maker capture,
toxicity, **verified** maker compensation, capacity. Its current state is
honest and unresolved — the faithful-transfer question is open, and an
unfixed P0 venue-semantics mismatch (signed single position vs our
two-sided inventory) is documented and blocked on credentials. No live
orders. Its distinguishing feature for this objective is real maker
compensation, which can make a strategy viable that PMUS fees would kill.

### Lane 3 — Kalshi *(re-scoped, above)*

Establish post-April realized results from the CSV; test whether the cell
concentration is a venue-neutral predictive hypothesis or in-sample
selection; only then ask about capacity.

**Datasets and validation samples stay separate. No pooling, ever.**

## Ranking as the evidence stands today

| candidate | expected net/window | lower-bound / OOS | capital | execution confidence | capacity | tail | evidence status |
|---|---|---|---|---|---|---|---|
| PMUS late-favorite / longshot | **unmeasured** | — | — | high (recorder live, fills audited) | unknown | unknown | not started |
| Gemini BTC5m maker | unmeasured | — | — | medium (P0 open) | unknown | unknown | measurement, no fill yet |
| Kalshi cell selection | **−$176/day actual**, in-sample +$115/day | 50–70% of in-sample at best | unknown | bot not running | unknown | losing cells −$6,073/30d | settled/realized, **negative** |
| PMUS favorite-DCA family | −$0.006 to −$1.20 | negative on every robustness test | $3.50–$25 | high | depth-bound above $100 | small | **closed** |

**Nothing currently qualifies.** The only lane with *settled, realized*
evidence is negative; the two lanes that could clear the bar are unmeasured.
That is the honest starting position for the new objective, and it is better
than it sounds: we now have a venue, a tape, an audited fill model and a fee
module that make a real answer cheap to obtain.

## Gates, unchanged in spirit

A candidate qualifies only with positive robust expectancy (mean **and**
median **and** leave-best-1 **and** leave-best-3), acceptable tails,
executable capacity inside $200, and clean source accounting. Evidence
ladder: replay → OOS → shadow → live probe → settled → realized. **No live
orders until a candidate passes its preregistered gates.**
