# P18 — Gemini capacity curve and L5 habitat screen

All figures measured, with the method beside each. Nothing here is realized.

## 1. Shadow verification of the post-placement capture

Harness: `analysis/gemini/audit/p17_placement_shadow.py`. Real public book,
real frozen controller (`state_from_venue` → `plan`), real placement rule,
real crossing re-check, real capture. No session, no credential, no `place()`
or `cancel()` reachable.

Live BTC5m book, 2026-09-23 12:45:28Z, `GEMI-BTC05M2609231250-UP`, tte 272 s:

| leg | native | submitted | improved | bid | ask | from_bid | from_ask |
|---|---|---|---|---|---|---|---|
| YES | 0.39 | **0.40** | **true** | 0.39 | 0.68 | +0.01 | −0.28 |
| NO | 0.32 | **0.33** | **true** | 0.32 | 0.61 | +0.01 | −0.28 |

`prices_unchanged_by_capture: true`, `crossing_refusal: null`,
`native → submitted` reconstructable from a single row
(`native_to_submitted_consistent: true`, delta 0.01).

**Rows are emitted and are sufficient. The capture changes no decision and no
mutation.**

### And it resolves Probe B, in the uncomfortable direction

The controller's native price **is the bid**; `maker_improve` produces
**bid + 1 tick**. Probe B submitted at **exactly the bid**, every order,
venue-acked. Therefore the rule did not run.

Root cause: **neither `WC.Driver(...)` construction site in
`run_continuous.py` passes `placement`** (lines 1447 and 1646). `_placement`
defaulted to `None` and the whole block was skipped. The spec was honest —
`placement` is inside `spec_sha bfe7ba23` — and the invoker dropped it.

**Probe B did not test `maker_improve`. It ran the frozen control a second
time, on BTC5m.** Its one complement is definitively not attributable to
placement. The fix is not implemented here: wiring it changes execution
behaviour, and the tree is disarmed.

## 2. Gemini BTC15m executable-capacity curve

Inputs, each measured separately:

| input | value | source |
|---|---|---|
| all-participant volume | **$19,935/day** | 96 consecutive contracts, 24 h to 12:30Z, our prints excluded |
| maker-incentive rate | **1.2493%** | observed accrual 0.08332590 on 6.67 qualifying |
| eligibility | **94.4%** | 17 of 18 fills; one outside band |
| maker-side ceiling | **50%** | only one side of a print can be ours |
| adverse selection (L5) | **−0.0301% of spend** | −80.20 on 266,229.81, 36 h |
| adverse selection (ours) | −71.59% of spend | A+B, **n = 6 windows — NOT projectable** |
| fees | **unverified** | 0 observed on our fills; 102 of 204 account-lifetime rows carry a nonzero fee, so 0 is not safe to assume |

| share of tape | tape $/day | our maker $ | qualifying $ | incentive $ | adv. sel (L5 rate) | **net $/day** |
|---|---|---|---|---|---|---|
| 1% | 199 | 100 | 94 | 1.18 | −0.03 | **+1.15** |
| 5% | 997 | 498 | 471 | 5.88 | −0.15 | **+5.73** |
| 10% | 1,994 | 997 | 941 | 11.76 | −0.30 | **+11.46** |
| 20% | 3,987 | 1,994 | 1,883 | 23.52 | −0.60 | **+22.92** |
| 30% | 5,980 | 2,990 | 2,824 | 35.28 | −0.90 | **+34.38** |
| 50% | 9,968 | 4,984 | 4,707 | 58.80 | −1.50 | +57.30 — *not executable* |
| 100% | 19,935 | 9,968 | 9,414 | 117.60 | −3.00 | +114.60 — **NOT EXECUTABLE** |

100% participation is **not** executable and is shown only as an arithmetic
ceiling. Probe A was ~60% of one contract's tape and that was an artefact of
an almost-empty window, not a sustainable share.

**Reference:** L5's own maker incentive is **$1,226/day**. Gemini BTC15m at a
plausible 10–20% share returns **$11–23/day**, roughly **1–2%** of it.

BTC5m, same method: tape **$36,937/day**, 100% arithmetic ceiling **$217.90/day**.

## 3. Habitat screen — comparable raw metrics

| metric | Gemini BTC15m | Gemini BTC5m | Polymarket BTC15m (L5's lane) |
|---|---|---|---|
| daily volume, all participants | **$19,935** | **$36,937** | ≥ $177,486 *(L5's own spend alone)* |
| trades/minute (public tape) | **0.321** | **0.868** | L5 alone: 146 windows × ~110 fills / 36 h ≈ **7.4** |
| prints per window (median) | **4.0** | **3.0** | **110** (L5's fills only) |
| zero-print windows | 8 of 96 (8.3%) | 42 of 240 (17.5%) | 0 of 146 |
| median inter-print gap | **49.5 s** | **6.6 s** | not comparable (own-fill feed) |
| two-sided acquisition | 1 of 3 traded windows (B) | same observation | **146/146 = 100%** |
| repair opportunity / latency | 1 observed at 43.7 s | — | first complement p50 **26 s**, p90 109 s; repair below 1.00 in 76–100% by bucket, latency 0–408 s |
| spread | BTC15m median 0.060 | BTC5m median 0.280 | not measured here |
| maker incentives | **1.2493%** accrual, observed | same account rate | **$1,226/day** credited (0.92% of spend) |
| fees | unverified, nonzero historically | same | not separable from the activity feed |
| realistic maker capacity | 10–20% of tape | 10–20% of tape | L5 already occupies it |
| expected net $/day at that capacity | **$11–23** | **$22–44** *(same method)* | — |
| settlement horizon | 15 min | 5 min | 15 min |
| data/execution readiness | **live, armed path exists, evidence pipeline proven** | same | read-only research only; no execution access |

**Kalshi and PMUS sports: NOT MEASURED — an honest gap, not a zero.** The
Kalshi public API answers (`/trade-api/v2/markets`, `/exchange/status` both
200), but `status=open` returns 12,000 rows of a single dormant zero-volume
series and gives no representative slice without series-level filtering. A
credible screen needs the series/event taxonomy and per-series volume, which
is a bounded piece of work and was not guessed at here. Polymarket's
`gamma-api` also answers and would give PMUS sports the same way.

## 4. What this means for P15

The mechanism is now independently reproduced and the venue is now measured.
Those two facts point in opposite directions:

- L5's recycling mechanism is real, and a hard ≤0.96 ceiling is incompatible
  with it in **95.9%** of L5's windows.
- Gemini BTC15m can return roughly **1–2%** of the incentive income that
  makes that mechanism pay, and its flow is **~20–340×** thinner per window.

P15 remains the right *next fidelity* experiment. It is not yet the right
*next spend*, because the placement plumbing defect above means we have never
run a placement variant at all — and fixing that is a cheaper, more
informative step than another bounded probe.
