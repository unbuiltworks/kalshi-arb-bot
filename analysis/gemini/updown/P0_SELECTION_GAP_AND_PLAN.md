# BTC5m: the selection gap explained, and where the edge actually is

**Gemini lane. BTC5m primary, BTC15m control. Diagnostic only — no threshold search,
no strategy derived, the rejected price-only favorite/underdog study untouched. No live
orders.**

---

## 0. First, the outstanding result: **PERSIST4 PASSED**

Recovered from run `37200798384` (head `46f794f3`, 2026-10-04). The job's conclusion
reads *failure* only because the shadow driver's non-zero exit propagates through
`SHADOW_RC`; the acceptance itself passed and the evidence was committed and pushed.
Artifact: `analysis/gemini/ci15m_shadow_persist_20261004T120508Z.json` on
`claude/gemini-btc-economic-viability-h3u7zl` at `77939fe`.

| check | result |
|---|---|
| `pass` / `failed` | **true** / `[]` |
| windows required / seen | 4 / **4** |
| all 9 acceptance checks | true (writes disabled, no duplicate OPEN, no forgotten resting order, no unresolved prior window, not halted, stayed alive across windows, within order + window budget, fingerprint unchanged) |
| order slots consumed | 7 of 8 |
| exit | `COHORT_EXPIRED` — `WINDOW_BUDGET_EXHAUSTED: 4 of 4` |
| `write_mode_at_exit` | `SHADOW` |
| elapsed | 2,472.6 s |

Window outcomes: `1215` NO_FILL (OPEN_CANCELLED), `1230`/`1245`/`1300` **RESIDUAL**
(OPEN_FILLED, no complement). So continuous rollover works and the gate for the
already-authorized bounded q1 live cohort is satisfied — but 3 of 4 windows ended
holding an unmatched leg, which is the complement-availability constraint again, not a
rollover defect. **q1 remains unarmed pending your word.**

---

## 1. P0 — the selection gap, explained

The gap is not about favorites. It is about **which side the book quotes an ask on**.

### Book state is the whole classifier

| tte | EMPTY | ONE_SIDED | TWO_SIDED | (ask-only / bid-only) |
|---|---|---|---|---|
| 120s | 1 | **76** | 56 | 29 / 47 |
| 90s | 4 | **99** | 30 | 39 / 60 |
| 60s | 9 | **111** | **13** | 49 / 62 |
| 45s | 7 | **112** | 14 | 49 / 63 |
| 30s | 30 | **101** | 2 | 43 / 58 |

(On this venue DOWN is bought at `1 − YES bid`, so a bid-only book still offers a
purchase — of DOWN. One-sided never means "nothing to buy"; it means only one outcome
can be bought.)

### At 60s, the two regimes

| book state | obs | leader correct | median \|proxy dist\| winners / losers | median depth |
|---|---|---|---|---|
| **ONE_SIDED** | **111** | **94.59%** | $22.95 / $9.98 | 178 |
| **TWO_SIDED** | **13** | **61.54%** | $7.59 / **$1.58** | 1250 |
| EMPTY | 9 | — | — | — |

**That is the entire 91.1% vs 61.5% gap.** The 91.1% is carried by the 111 one-sided
observations; the 61.5% *is* the 13 two-sided ones. They are not two views of the same
population.

### And the one-sided regime is adversely selected by construction

| tte | one-sided obs | leader has **no ask** | only executable side **is the loser** | its average price | leader correct |
|---|---|---|---|---|---|
| 120s | 76 | 97.37% | 90.79% | 0.221 | 93.42% |
| 90s | 99 | 93.94% | 93.94% | 0.240 | 91.92% |
| **60s** | **111** | **100.00%** | **94.59%** | **0.186** | **94.59%** |
| 45s | 112 | 98.21% | 96.43% | 0.179 | 94.64% |
| 30s | 101 | 81.19% | 96.04% | 0.320 | 79.21% |

At 60s the leader has **no ask in 100% of one-sided observations**. "The leader is right
94.6% of the time" and "the only executable side loses 94.6% of the time" are the **same
number from the same cause**: the book offers an ask only on the side that is losing.

### What distinguishes the two regimes

The book goes two-sided **precisely when the outcome is undecided**. Within TWO_SIDED at
60s, winners sit a median **$7.59** from the strike on the proxy; losers **$1.58**. In
ONE_SIDED it is $22.95 vs $9.98. The market is not mispricing — it is withdrawing the
offer once the answer is clear, and quoting both sides only while it is not.

So the executable-favorite subset is not "favorites that perform badly". It is
"the residual population in which nothing is yet decided", priced across a 0.35 spread.

### Not measurable from this tape, stated rather than approximated

- **180s was never collected** (grid is 1/2/5/10/15/20/30/45/60/90/120s) — reported
  NOT COLLECTED, never interpolated.
- **Quote age: absent.** This tape carries our receive stamp but no venue event time
  and no snapshot age, so staleness cannot be measured. That is a P2 requirement.
- **Leader tenure: not resolvable** on an 11-point grid spaced 30–60s here; every cell
  returns the full span, so it is reported as NOT RESOLVABLE rather than as "60.0s".
- Prior flips are 0 at the median through this range, consistent with the persistence
  table.

---

## 2. Where the edge actually is — and it is not small

If the one-sided leader could be bought at the price its own book implies
(`1 − executable price`, i.e. the bid side):

| tte | n | implied leader price | leader wins | **EV/share** | depth median | **$/window at depth** |
|---|---|---|---|---|---|---|
| 120s | 74 | 0.7897 | 93.24% | **+$0.1311** | 189 | **$24.77** |
| 90s | 93 | 0.8014 | 95.70% | **+$0.1445** | 182 | **$26.29** |
| 60s | 111 | 0.8136 | 94.59% | **+$0.1217** | 178 | **$21.67** |
| 45s | 110 | 0.8325 | 96.36% | **+$0.1213** | 178 | **$21.60** |
| 30s | 82 | 0.7934 | 96.34% | +$0.1585 | **8** | $1.19 |

(Taker fee 7%·p·(1−p) applied; depth is the *opposite* side's recorded size.)

Against the targets — BTC5m runs **288 windows/day**, so **$480/day = $1.667/window**,
and **$5/window = $1,440/day**:

**$21–26/window of theoretical value at 45–120s is 13–16× the $1.667/window that
$480/day requires.** The information is abundant. The problem is entirely executional:

- it sits on the side with **no ask**, so it is not reachable as a taker at all;
- the only route is **resting a bid on the leading side**, and our own 960-cycle
  queue-respecting census puts that at **0.6% fill joining the touch, 1.6% improving a
  tick, with a +0.12 median adverse move on the fills that do arrive**;
- **+0.12 adverse selection is the same size as the +0.12 edge.** Whether anything
  survives is an empirical question about *which* resting bids get hit — and that is
  exactly what has never been measured on the leading side in one-sided books.

That is the research question this lane should now answer, and it is a different
question from the one the rejected study asked.

Two expressions are already closed and should not be re-derived: buying the executable
loser is the underdog trade (measured, catastrophic), and *selling* it would be a naked
short whose venue semantics are undetermined and which `sell_path` refuses by design.

---

## 3. P1 — LS-LRRR-XBTUSD: one real lead, still blocked here

`developer.gemini.com`, `docs.gemini.com`, `api.gemini.com`, `kaiko.com`,
`docs.kaiko.com` and `marketing.kaiko.com` are **all denied by this environment's
network policy** (developer.gemini.com was reachable yesterday and is not now). So no
authoritative page could be read this turn, and **Z stays NOT IDENTIFIED** — nothing
was fabricated.

**But the lead that matters changes the approach.** Gemini's own WebSocket streams
documentation exposes an **index-keyed price stream**, subscribed as
`{agency}:{index}@indexPrice`, described as the live reference price associated with a
contract's agency and index. `LS` is Lukka, whose index-calculation and Lukka Prime
services are the plausible provider.

If that stream exists as described, **we do not need to reconstruct the statistic — we
can record its published values directly.** That is strictly better evidence for a
causal `P(UP | state)`: no venue-set guessing, no weighting assumption, no $8–17
single-venue reconstruction error. It converts P1 from a documentation problem into a
recording problem, which P2 owns.

Sources for the lead: [Gemini stream reference](https://developer.gemini.com/prediction-markets/websocket/streams) · [Lukka index calculation services](https://lukka.tech/index-calculation-services/) · [Lukka Prime](https://lukka.tech/solutions/lukka-prime/)

To unblock the documentation route, those hosts need adding under Network access in the
environment settings (Edit → Custom → Allowed domains, keeping the package-manager
defaults): https://code.claude.com/docs/en/cloud-environments#network-access

---

## 4. P2 — the continuous BTC5m recorder

**Reuse, not rebuild.** `analysis/gemini/info_edge/ie_collector.py` already streams
`{sym}@depth` (every diff with `U`/`u` sequence ids and venue `E` in ns),
`{sym}@trade`, `{sym}@depth20` full snapshots for seeding and resync, the events REST
metadata (strike, `strike_at`, source identifier, expiry, effective), the settlement
record, and a Coinbase reference with an explicit `clock_probe` row so skew is measured
rather than assumed. It was the collector that produced the tape this diagnostic runs on.

Four additions are needed, and only four:

1. **`{agency}:{index}@indexPrice`** — subscribe per contract from the contract's own
   `sourceDetails`, and record value + venue timestamp + receive timestamp. This is the
   causal reference the whole P1/P3 chain needs, and nothing in the repo records it yet.
2. **`availableAt`** captured explicitly (strikes are null until it, which is why the
   tradeable life is 300s), alongside expiry.
3. **Checkpoint snapshots at 240/180/120/90/60/45/30/20/15/10/5/2/1s** with **full
   executable YES/NO L2 including sizes** — the current tape kept only the touch, which
   is why depth and quote age are missing above.
4. **`TWO_SIDED` / `ONE_SIDED` / `EMPTY` written explicitly per snapshot**, plus
   `quote_age_s` from the venue event time. Book state is the primary classifier in
   this diagnostic; it should not have to be re-derived.

**It cannot run from this container** — `api.gemini.com` and `ws.gemini.com` are
egress-denied. It has to run where the credentialed Gemini work already runs: a
GitHub Actions job (the pattern `gemini-settlement-gate.yml` uses) or the Railway
service that hosts the other 24/7 recorders. Read-only and unauthenticated, so it
self-recovers and runs continuously per `CLAUDE.md`. **I have not started it this turn**
— say the word and I will wire it to whichever of the two you prefer, and it begins
accumulating the windows that every remaining item needs.

---

## 5. P3 — preregistered, and sealed until P1 closes

Stated now so it cannot be fitted later:

> At each of 180/120/90/60s, estimate `P(UP)` from settlement state alone — the
> remaining move required in the **actual** published reference statistic, normalized
> by the causal uncertainty of that remaining statistic. Then **independently** compare
> `P(UP)` against the executable UP ask plus costs, and `1 − P(UP)` against the
> executable DOWN ask plus costs. The question is whether state-derived probability
> carries information **beyond** Gemini price while the book is still executable. Hold
> to settlement. No optimized exits. Gemini price never defines the predicted winner.

The estimator's *form* already exists and is correct (`src/gemini_maker/fair_value.py`:
variance of the **remaining** part of a partly-realized average, `σ²τ³/3w²` inside the
window — not `σ√T`). Its one blocking assumption is the weight function, which is what
P1 must supply.

**Focus on 180–60s is right**, and this diagnostic sharpens why: by 30s the one-sided
executable price jumps to 0.320 and depth collapses to 8 contracts, so the late window
is unmonetizable for a reason independent of prediction quality.

**And one hard constraint carries over**: within price × TTE, 64 of 65 cells on this
corpus are underpowered and the largest holds 15 windows, against ~300 needed. P3 is
blocked on **window count** as much as on Z. P2 is the only thing that moves it.

---

## 6. P4 — commercial acceptance, as far as it can be stated

Measured now: theoretical value **$21–26/window** at 45–120s on 178–189 contracts of
depth, against **$1.667/window for $480/day** and **$1,440/day for $5/window**. The
frequency is already there (288 windows/day, a one-sided book in 76–112 of 133
observations per checkpoint).

What is **not** measurable until P2 runs, and what decides everything:

| quantity | status |
|---|---|
| maker fill rate on the **leading** side in a one-sided book | **unmeasured** (the 0.6–1.6% census did not condition on book state or side) |
| adverse selection on those fills | +0.12 overall, **unconditioned** — and the same size as the edge |
| realizable net EV/trade, $/window, $/day | blocked on the two above |
| executable depth on the leading side | **never recorded** (only the opposite side's size is in this tape) |
| drawdown, longest losing streak, capital required | blocked |

No Martingale, no assumed fills, no live orders.

## Artifacts

`selection_gap_diagnostic.json` · script `scripts/gemini_selection_gap.py` ·
PERSIST4 result at `analysis/gemini/ci15m_shadow_persist_20261004T120508Z.json`
(branch `claude/gemini-btc-economic-viability-h3u7zl`, `77939fe`).
Accounting label: historical replay on Gemini's own recorded books and settlements.
No order was sent. Nothing here is realized profit.
