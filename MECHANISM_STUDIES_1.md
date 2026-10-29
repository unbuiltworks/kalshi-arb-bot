# Three mechanism studies + the Gemini slate

**Parameter search is over.** No execution arms were added, Model A is frozen,
GEMINI_RECYCLE and the EXTEND veto stay as the control, and nothing is live.

These four studies ask what the venue was *doing* while our COMPLETE sat
unfilled — not which price we should have chosen.

Code: `mechanism.py` (27 tests), `slate.py`. Reproduce:
`python3 -m src.gemini_execution.mechanism_report`,
`python3 -m src.gemini_execution.slate_report`.
Output: `analysis/gemini/mechanism.json`, `analysis/gemini/slate.json`.

---

## 0. The answer, in one line each

1. **Closest approach:** the complement was never *nearly* reachable. Median
   closest gap **22 ticks**; **95.2% never came within 5 ticks**; **zero**
   episodes came within 2.
2. **Underlying state:** 1 recyclable against 17 leakage states — the sample
   cannot test the hypothesis, and no selector follows.
3. **Liquidity presence:** our 0.01 is **1/800th of a median print** and
   **1/20,000th of median touch depth**. It measures the wire.
4. **The slate:** **BTC05M read zero prints across 8 sampled contracts.** It is
   the worst habitat on Gemini's own slate by the diagnostic that matters.

---

## 1. COMPLEMENT CLOSEST-APPROACH — the crossing bucket, split

124 post-first-fill episodes, strict chronology: only books and prints inside
each quote's own life count. "Executable maker territory" is one tick under the
ask, because at or above the ask is a cross and is not territory we may enter.

| bucket | episodes | share |
|---|---|---|
| **never approached (>5 ticks)** | **118** | **95.2%** |
| approached within 5 ticks | 4 | 3.2% |
| **approached within 2 ticks** | **0** | **0.0%** |
| **touch reached, no qualifying execution** | **0** | **0.0%** |
| maker fill historically possible | 2 | 1.6% |
| no opposite book observed | 0 | 0.0% |

| | |
|---|---|
| **median closest gap** | **22.0 ticks** |
| minimum closest gap across all 124 | see JSON |
| midpoint moved toward our COMPLETE | 57% of episodes (observed on 110) |
| opposite depth *built* while we waited | 19 of 110 observable |

**This closes the pricing question completely.** The earlier frontier said 82%
"required a cross" — which could have meant *just* out of reach. It did not.
The median episode's best executable maker price was **22 cents** from Model
A's COMPLETE, and **not one episode in 124 came within 2 ticks**.

The two "touch reached but no execution" and "within 2 ticks" buckets being
**empty** is the most informative result here: there is no near-miss
population. We were not losing races at the touch. The price we needed and the
price the market offered were in different neighbourhoods for essentially the
entire life of every quote.

**The midpoint drift is a non-story**: 57% toward us is a coin flip, so this is
not a directional-adverse-selection effect either. The market simply was not
there.

## 2. UNDERLYING-STATE RECYCLING

Joined from contemporaneous information only. The **strike** is published at
the window's open (`strike.availableAt`), so it is known before anything
happens; BTC comes from Gemini's **1-minute candles** (1,440 bars, a rolling 24
hours, covering every one of our windows). **`settlementValue` is the outcome
and is never read as a feature** — pinned by a test.

**An honest limit on the spec.** The study asked for BTC velocity at 5/15/30/60
seconds. **Only 60s exists historically.** One-minute bars cannot resolve 5
seconds and interpolating them would be a fabrication, so the shorter horizons
are recorded as `None` with that reason attached, not filled in.

| | |
|---|---|
| first-fill states with an underlying join | **18** |
| recyclable (par-constrained) | **1** |
| leakage | 17 |
| smaller cell | **1** |
| supports a selector | **no** (floor is 30 per cell) |

Distance from strike, distance over volatility, 60s velocity and TTE are all
recorded per state in the JSON, with both cell sizes on every line. **With one
recyclable state, no contrast means anything and none is claimed.** The
hypothesis that recyclability is a fact about the underlying rather than the
book remains untested, not refuted.

## 3. LIQUIDITY-PRESENCE FEASIBILITY

From the venue's own prints and books — 195 prints, 709 book observations.

| anchor | value | our 0.01 as a fraction |
|---|---|---|
| median print | **8** contracts | **0.00125** — 1 in 800 |
| p25 / p75 print | 1.43 / 10 | |
| max print | 714 | |
| **median touch depth** | **202.0** | **0.0000495** — 1 in 20,000 |
| median displayed ladder | 304.0 | |

A bounded presence at reference fractions of observed liquidity:

| fraction | of median print | of median touch | of median ladder |
|---|---|---|---|
| 1% | 0.08 | 2.02 | 3.04 |
| 2% | 0.16 | 4.04 | 6.08 |
| 5% | 0.40 | 10.10 | 15.20 |
| 10% | 0.80 | 20.20 | 30.40 |

*(Reference points for reading the table. Not proposals, nothing wired to
them.)*

**Verdict: MARGINAL — and read the ratios, not the label.** One typical
counterparty bringing a median 8-contract print could fill our entire quote
**800 times over**. Against displayed touch depth we are one part in twenty
thousand. **Our fills have told us the order path works, the coherence gate
holds and the accounting is sound. They have told us essentially nothing about
being a liquidity provider,** because at 0.01 we are not one.

*(The first version of this verdict was binary and returned "meaningful on at
least one anchor" for these numbers — true, and misleading. It is three-way
now, and the threshold is flagged as arbitrary with the ratios as the actual
evidence.)*

> ### The causal question, labelled rather than estimated
>
> **Whether a larger quote would attract flow is not answerable from
> historical replay.** The tape records what happened against the book that
> existed. It cannot record what a different book would have caused. Every
> figure above describes *existing* flow meeting a *hypothetical* size. None
> of it estimates flow that a larger presence might create, and **no such
> estimate should be read into it.** This appears on every object the module
> returns.

## 4. THE GEMINI SLATE — BTC05M is the worst habitat on it

**997 events, 11,012 contracts, 9,274 unresolved.** 160 tapes read, stratified
across series, soonest-expiring first.

*(The first pass sorted by newest expiry and spent its whole budget on
far-future sports contracts that had never traded — which said nothing about
any series. Fixed and re-run.)*

Ranked by **alternation** — how often the aggressor direction changes — because
that is what an inventory oscillator needs and it is exactly what a volume
ranking cannot see:

| series | contracts | prints | two-sided | **alternation** | side balance | balances stated |
|---|---|---|---|---|---|---|
| **BTC15M** | 5 | 11 | 0.20 | **0.600** | 0.571 | 1 |
| **BTC1H** | 9 | 25 | 0.22 | **0.500** | **0.800** | 2 |
| WXHIGH | 5 | 7 | 0.00 | 0.250 | — | 0 |
| ZEC1H | 5 | 34 | 0.60 | 0.231 | 0.167 | 3 |
| SOL | 5 | 142 | 0.80 | 0.207 | 0.143 | 5 |
| **BTC** | 5 | **507** | **1.00** | 0.203 | 0.179 | 5 |
| XRP | 5 | 125 | 0.60 | 0.140 | 0.176 | 5 |
| ETH | 5 | 253 | 0.60 | 0.114 | 0.185 | 4 |
| Economics | 5 | 149 | 0.60 | 0.105 | 0.143 | 5 |
| **BTC05M** | **8** | **0** | **0.00** | — | — | **0** |
| ETH05M / SOL05M / XRP05M | 7/7/6 | 0 | 0.00 | — | — | 0 |
| ETH15M / SOL15M / XRP15M | 5/5/4 | 0 | 0.00 | — | — | 0 |
| Sports / WTI / XAU / XAG / COPPER / BRENT | 5 each | 0 | 0.00 | — | — | 0 |

**Three things stand out.**

1. **BTC05M read zero prints across 8 sampled contracts.** So did every other
   5-minute series. The timeframe, not the underlying, is what is dead.
2. **BTC1H and BTC15M have the highest alternation and side balance** — 0.5–0.6
   alternation, 0.571–0.800 balance — on small print counts. They are the only
   series where the tape actually goes back and forth.
3. **The BTC series has 507 prints and 100% two-sided flow** across all 5
   contracts sampled — by far the most activity on the slate, with two-sided
   flow everywhere.

**Sample sizes are small and stated on every row.** `MIN_PRINTS_FOR_BALANCE`
is 6, so a series with fewer prints gets no balance figure rather than a
meaningless one — which is why "balances stated" is a column. BTC15M's 0.600
alternation rests on **one** contract with a stated balance. These are leads,
not conclusions.

## 5. What this licenses

**Licensed.** The complement on BTC5m was never nearly reachable — a 22-tick
median gap with an empty near-miss population closes the pricing question at
any parameter setting. Our 0.01 probe measures plumbing, not presence. BTC05M
is the emptiest series on Gemini's own slate, and the 5-minute timeframe is
dead across every underlying, not just BTC.

**Not licensed.** Any size increase — the causal question is unknowable here.
Any selector — the underlying study has one positive state. Any habitat
decision from the slate alone — the per-series samples are 5–9 contracts.

**The obvious next step, not taken without you:** run the sequential replay
against **BTC1H / BTC15M / the BTC series**, where alternation is 3–6× BTC05M's
and the tape is two-sided. That is the same engine on the same venue with no
new infrastructure, and it directly tests whether the timeframe was the
problem all along.

Gemini BTC5m collection continues in parallel and its canonical replay,
frontier and habitat reports refresh as independent N grows.

---

**Labels.** Everything is **SHADOW/THEORETICAL**. No orders, no sizing
decision, no authorisation, no artificial or self-generated volume. Matched-pair
economics, directional-residual exposure, fees and rebates are separate lines
and are never summed. Nothing here is realized.
