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
side*, rests more, and reaches the book comparably. Nothing traded through
**either** side in this window, at a **median spread of 0.22**.

**Verdict: `BUY_ONLY_FILLS_SELL_RESTED_UNFILLED`.** The sell side is working.
The venue simply did not trade through our quotes.

Caveat stated rather than buried: the second segment is ~2 minutes old at the
time of this report. The placement asymmetry (122 > 80) is structural and
robust at that size; `trade_flow 0` on both sides is a small sample and says
only that this window was quiet.

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

**Lane B (Gemini) — closest.** One blocker: authoritative settlement. It needs
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
