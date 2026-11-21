# Parallel lane findings — 2026-09-17

Four results, each independently verified. Two settle a P0; two are structural
blockers that decide what either lane may do next.

Labels throughout: nothing below is realized profit, no order was placed, and
no result here authorizes a canary.

---

## 1. LANE B P0 — SETTLED. The complement is not broken; the market is quiet.

**Question.** Did the earlier 17 evidenced fills / zero complements come from
the obsolete one-shot/complement architecture, or does it survive a persistent
simultaneous BUY+SELL surface?

**Answer: it survives, and it is not a complement defect.**

`src/gemini_l5_surface_shadow.py` was already the persistent simultaneous
surface — six of its seven surfaces quote BUY and SELL together and quotes
persist in `st.quotes` across observations. The obsolete sequential controller
is `gemini_l5_shadow_v2.py`, which is untouched and not running.

Two segments answer it together:

**Segment `20260917T212645Z`** (side-split, 380 rows, 337 observations):

| | |
|---|---|
| evidenced fills | 6 |
| fills BUY / SELL | 6 / 0 |
| opposite recycling 5/15/30/60/120/300s | 0 at every mark |
| `two_sided_symmetric` | BUY 1 / SELL 0 |
| `two_sided_asymmetric` | BUY 1 / SELL 0 |

That reproduces the earlier pattern, so the architecture is exonerated. But a
fill split alone cannot say *why* the sell side is empty — a sell side never
placed is our defect, one that rested and was never traded through is the
market. Opposite conclusions.

**Segment `20260917T214616Z`** (per-side placement counters, recorded by the
surface as it ran, not reconstructed):

| | BUY | SELL |
|---|---|---|
| placed | 80 | **122** |
| restable | 356 | **368** |
| book_reached | 276 | 239 |
| trade_flow | **0** | **0** |

`sell_ever_placed: true`. The sell side is placed *more often than the buy
side* at this point, rests more, and reaches the book comparably. Nothing had
traded through either side yet, at a median spread of 0.22 — but that segment
was ~2 minutes old, and the next report supersedes it.

**Segment `20260917T214616Z` at 22:07:38Z** (656 rows, 641 observations):

| | BUY | SELL |
|---|---|---|
| placed | 209 | 89 |
| restable | 719 | 601 |
| book_reached | 499 | 407 |
| **trade_flow** | 0 | **20** |
| evidenced fills | 2 | **0** |

`sell_ever_placed: true`, median spread 0.26.

**Verdict: `BUY_ONLY_FILLS_SELL_RESTED_UNFILLED`, and the reason is the
QUEUE, not the market.** Trade flow went *through* a sell quote **20 times**
and produced **zero** sell fills. The sell side is not merely placed and
resting — it is being traded through, and what stops the fill is
`volume_ahead` never clearing.

This supersedes the earlier reading. At 21:47 the honest statement was "the
venue did not trade through our quotes"; with 641 observations it is "the
venue trades through them and we are behind the queue". The two point to
different remedies — waiting for a busier market versus quoting earlier, finer,
or smaller — so the distinction is the finding, not a detail.

Measurement note, so the BUY column is not misread: `rungs_side` counts LIVE
quotes per observation, and a filled quote is popped from `st.quotes`, so the
flow that produced a fill is not counted in the live tally afterwards. The two
BUY fills are carried by `BUY|VOLUME_AHEAD_CLEARED`, not by `BUY_trade_flow`.
`BUY_trade_flow 0` means no *currently resting* buy quote has flow through it,
not that the buy fills came from nowhere.

---

## 2. LANE B BLOCKER — Gemini publishes no settlement value.

Four expired BTC5m windows, 578s to 3878s past expiry, read 21:44Z:

| symbol | expired | status | book | trades | last |
|---|---|---|---|---|---|
| `GEMI-BTC05M2609172135-UP` | 578s | closed | 0 / 0 | 19 | 0.35 |
| `GEMI-BTC05M2609172130-UP` | 878s | closed | 0 / 0 | 5 | 0.97 |
| `GEMI-BTC05M2609172120-UP` | 1478s | closed | 0 / 0 | 4 | 0.90 |
| `GEMI-BTC05M2609172040-UP` | 3878s | closed | 0 / 0 | 8 | 0.17 |

An expired contract closes with an empty book, the public tape survives, and
the venue publishes **no terminal value anywhere**. A contract whose last print
was 0.97 may still settle 0, so the last print is not a settlement.

The finding is **CANNOT**, not *has not yet* — the difference decides whether
anyone keeps looking for an endpoint that is not there. Only the authenticated
account surface (`/v1/mytrades`, `/v1/balances`, `/v1/transfers`) can close it.
The underlying against the strike is a **model**, admissible as
`INFERRED_SETTLEMENT`, never as realized.

Gate: `src/gemini_settlement.py`, five requirements each able to block alone.
Status **BLOCKED**, marked `NOT_EXERCISED` rather than PASS — the structure is
proven, the path is not, and this module holds no credential.

**Venue terms measured at the same time** (`/v1/symbols/details`):

- `tick_size` 0.01, `quote_increment` 0.01
- **`min_order_size` 0.01** — one hundredth of a contract. The shadow's
  `UNIT = 1` is **100× the venue minimum**, so a bounded canary can be far
  smaller than the shadow's unit (~half a cent of notional at 0.50).
- **`status: limit_only`** — the venue itself rejects anything but a limit
  order here. Maker-first on this instrument is not only our policy.

---

## 3. LANE A — live two-sided sports books exist, and the print tape does not.

150 markets sampled from a 602-market live slate (32 live events):

| | |
|---|---|
| two-sided BBO | 23 / 150 = **0.153** |
| depth on **both** sides of the book | 23 / 23 of those |
| book levels | median **13**, max 36 |
| spread | median 0.030, p25 0.010, p75 0.080 |
| fee-safe maker pair cost ≤ 0.96 | 10 of 23 |

A two-sided BBO on PMUS reliably means a real two-sided book — the 23/23 is
the useful part. Deepest observed: `tsc-mlb-sd-col-2026-09-17-11pt5` at
0.47 × 5487 / 0.47 × 25678, and `aec-county-nor-der-2026-09-15` at
0.43 × 20 / 0.48 × 570 with a 0.05 spread and pair cost 0.95.

**But there is no public print tape.** Re-verified against a live slug:
`/trades`, `/history`, `/ohlc`, `/price-history` and `/v1/trades?marketSlug`
**all 404**. The gateway serves `sharesTraded`, a *cumulative per-market*
counter — it says trades happened somewhere in the market, never at our price,
which is precisely what `VOLUME_AHEAD_CLEARED` requires.

*Correction on the record:* my first pass reported zero depth on both sides
while 23 markets showed a two-sided BBO. That was my own misread — the book
nests under `marketData` with `px.value`, not under `book` with `price` — not a
venue finding. `src/pmus_live_slate_census.py` reads it correctly.

---

## 4. THE RACE — neither lane is canary-ready, and they are blocked at
opposite ends of the same ladder.

`src/l5_surface/lane_gates.py`

| lane | propose | score | blocked by |
|---|---|---|---|
| `L5_ADAPTED_GEMINI_CRYPTO` | ✅ | ❌ | SETTLED, REALIZED |
| `L5_ADAPTED_PMUS_SPORTS` | ❌ | ❌ | TRADE_FLOW, EVIDENCED_FILL, OPPOSITE_RECYCLING, SETTLED, REALIZED |

- **PMUS is blocked in the middle**, at `TRADE_FLOW`. `EVIDENCED_FILL` and
  `OPPOSITE_RECYCLING` fall with it. The authenticated WS
  (`wss://api.polymarket.us/v1/ws/markets`) *does* carry a real trade tape and
  `src/pmus_ws_evidence_recorder.py` already reads it read-only, and this
  operation holds PMUS credentials — so this is a **deployment away**, not a
  venue limitation.
- **Gemini is blocked at the end**, at `SETTLED`. It can evidence every stage a
  canary *proposal* needs, including the prints PMUS lacks.

Proposal-readiness and scoring-readiness are tracked separately because they
fail separately. A lane that can place a canary and cannot settle it spends
real money on a result nobody can read — under settlement-only realized
labelling that is not a canary, it is an unscored loss. So `canary_ready` is
the conjunction, and **Gemini reads `can_propose_canary: true`,
`can_score_canary: false`, `canary_ready: FALSE`.**

**Closest lane: Gemini**, blocked only on settlement accounting.

---

## What each lane needs next

**Lane B (Gemini) — closest.** One blocker: authoritative settlement. And one
newly sharpened research question that is *not* a blocker: the sell side is
queue-bound, not flow-starved, so the next passive-surface question is where in
the queue a sell quote would have to sit to clear — which bears directly on the
`min_order_size 0.01` finding, since a smaller lot clears a queue sooner. It needs
an authenticated read of `/v1/mytrades`, `/v1/balances` and `/v1/transfers`
across a window expiry, run where the Gemini credentials already live, to turn
`src/gemini_settlement.py` from BLOCKED to OPEN. No order is involved. That is
the single step between this lane and a scoreable canary proposal.

**Lane A (PMUS) — one deployment behind.** It needs the authenticated WS trade
tape wired into the surface evidence ladder so `TRADE_FLOW` becomes
observable at a price level. The recorder exists and is read-only; what is
missing is the join between its trade tape and the surface's resting quotes.

Neither lane blocks the other. Neither may be advanced past its blocker by
weakening the evidence rule. No live orders without explicit authorization.

---

# Addendum — 2026-09-17 evening, both lanes wired

## LANE A — wired, and validated against live books

`src/pmus_l5_surface_shadow.py` + `src/pmus_l5_surface_runner.py` run the
frozen `L5_ADAPTED_PMUS_SPORTS` spec as a persistent two-sided surface and
produce the per-side funnel with queue-ahead frozen at placement.

The engine needed **no change**. PMUS quotes one slug (YES); buying NO at *p*
is selling YES at *1−p*, so a two-sided surface on a slug is exactly the
two-outcome surface the engine already emits. The engine is addressed in *its*
names — the frozen spec's `both_sides_policy` scope is literally `["Up","Down"]`,
and handing it `YES`/`NO` keys produced `emitted: True` with **zero rungs**, a
surface silently quoting nothing. That now reports `no_rungs_in_scope`.

**Validated live, 30 markets, 20 cycles, 4 minutes:**

| | BUY_YES | BUY_NO |
|---|---|---|
| PLACED | 34 | 48 |
| RESTABLE | 34 | 48 |
| BOOK_REACHED | 34 | 48 |
| TRADE_FLOW | — | — |
| EVIDENCED_FILL | — | — |

Queue-ahead captured at placement ranged **0.65 → 1233 shares**. 8 of 30
markets were never two-sided (props/handicaps).

That run caught a defect of my own: it first reported `NO_FILLS_NO_FLOW` when
the public gateway serves **no tape at all**. `TRADE_FLOW 0` from an
*unreadable* tape is not an observation that nothing traded — it is the
unreadable-versus-empty error. The verdict is now
**`TRADE_FLOW_NOT_OBSERVABLE`**, tape readability is counted per observation,
and a readable-but-empty tape still reports `NO_FILLS_NO_FLOW`.

Entry is **not** gated on pair cost; `0.96` appears in no AST constant.

**Exact remaining blocker:** the surface must run where the authenticated WS
tape is. That tape is recorded by `pmus-ws-evidence-recorder`
(`71b6ea99-…`, **live**, 50GB volume at `/state/pmus-ws`, holds the PMUS
credentials), whose Railway source branch is `feature/l5-transfer-backtest` —
not the designated branch. Running the surface there means either pointing that
service at this branch or merging, and it redeploys a live credentialed
recorder. **That is an owner decision, not mine to take.**

## LANE B — queue-bound diagnosed; settlement gate built and not yet run

`analysis/l5/gemini_surface/queue_diagnosis.py`, wired into the supervisor
cadence as `L5_GEMINI_QUEUE` (promoted).

Both wrong answers are excluded **in code**, with tests, so neither gets
re-asserted: **not flow-starved** (flow went through the sell side and was
recorded) and **not a broken complement** (the sell side is placed, rests and
reaches the book *more often* than the buy side).

Four explanations, each measured off the tape already being written:
`PERSISTENCE` (re-placement rate — a re-placed quote goes to the back),
`PLACEMENT` (queue depth by offset), `REFRESH` (re-placing at an *unchanged*
price is pure priority loss), `QUEUE_MODEL` (`VOLUME_AHEAD_CLEARED` assumes we
joined behind *everything*, so it is a **lower bound**; reported as the
priority factor our true position would need).

It states what it **cannot** settle: our real queue position is unpublished, so
no tape analysis converts the model into a fact.

**Size is not a queue fix and is not presented as one.** `queue_priority_effect:
NONE` — reducing our lot does not reduce volume already ahead of us; the queue
is unchanged and a smaller order waits behind the same depth. It is reported
as a **risk/capacity** axis: the shadow's `UNIT` is 100× the venue minimum, and
a lot at the minimum cannot partially fill, which removes a source of residual.

**Settlement.** The credentials are **not** on Railway and **not** on the AWS
host — they are **GitHub Actions secrets**, and `gemini-subsidy-verify.yml` is
the established read-only precedent. `.github/workflows/gemini-settlement-gate.yml`
mirrors it exactly: guards run **before** any secret enters scope, `NO_CREDENTIALS`
fails closed with no fallback, only `analysis/gemini/` artifacts may be
committed, manual dispatch only, no order submitted. The probe emits the
required chain per symbol — contract → expiry → authoritative outcome →
position → settlement cashflow → fees → ledger reconciliation — reporting the
**first break** rather than a score.

**Exact remaining blocker:** `workflow_dispatch` registers only from the
**default branch**. The file is on `claude/l5-execution-instrumentation-io6nzi`
and **not on `main`**, so the API returns 404 and it cannot be run. Pushing to
`main` is outside my branch authorization. **The owner must place it on `main`
(merge or cherry-pick) — then it is one dispatch.**

Note on what that run will and will not close: we have never traded on Gemini,
so `/v1/mytrades` will be empty and the gate will still report BLOCKED on
`AUTHORITATIVE_FILLS`. What it **does** decisively establish is
`settlement_endpoint_exists` — whether an authoritative settlement/outcome read
exists for this account at all. That single fact determines whether Lane B can
*ever* score a canary.
