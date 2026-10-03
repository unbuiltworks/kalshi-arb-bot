# Can PMUS BTC15m support OPEN → EXTEND → COMPLEMENT → LOCK?

**No — and the reason is the venue's market structure, not our data, our model or our
fill discipline.** The evidence is below. Nothing was imported from the Gemini R2 lane.

## 0. State preserved

Branch `claude/l5-pmus-economic-results-7c116e`, head `7c76115e`, working tree clean.
The active prospective experiment is untouched: `IMBALANCE_PROSPECTIVE_TABLE.json`
(11 windows), `PMUS_IMBALANCE_MECHANISM_AUDIT.json`, `GATE_PASS_1730Z_1745Z.md`,
`WINDOW_CHARACTER_*.json`, `PREREGISTRATION_EXIT_RULES.md`. Nothing here overwrites or
re-scores any of it; the incentive fetch was written to the scratchpad, not over the
existing census.

## 1. The structural finding, from authoritative venue evidence

Four independent observations, all live from `gateway.polymarket.us`:

**(a) The two "sides" share one instrument identifier.**
`/v1/markets?slug=cpc-btc-updown-15m-2026-10-02-2045z` returns
`marketSides: [{id 2330944, description "Yes", long true, identifier
"cpc-btc-updown-15m-2026-10-02-2045z"}, {id 2330945, description "No", long false,
identifier "cpc-btc-updown-15m-2026-10-02-2045z"}]`.
Same `identifier`. They are the long and short side of **one** instrument, not two
instruments.

**(b) The book is keyed by market, and there is exactly one.**
`/v1/markets/{slug}/bbo` returns a single `bestBid` / `bestAsk` / `bidDepth` /
`askDepth`. There is no per-side book endpoint — `/v1/orderbook`, `/v1/book`,
`/v1/orderbooks` and `/v1/markets/orderbook` all 404. The authenticated WS
(`/v1/ws/markets`) subscribes by slug and delivers one `bids`/`asks` pair per market,
which is why our recorder keeps exactly one book per slug.

**(c) The short price is derived, not quoted.** On the live market
`cpc-btc-updown-15m-2026-10-03-1345z` at 13:55Z:
`bestBid 0.8900, bestAsk 0.9000, longPx 0.8900, shortPx 0.11`.
`longPx + shortPx = 1.0000` **exactly**. Same on expired windows at both extremes
(0.9900/0.01 and 0.0100/0.99). The No price is 1 − Yes, to the tick, always.

**(d) Therefore simultaneous two-sided acquisition costs more than $1 by construction.**
Buying the long takes the ask (0.9000); buying the short takes the short ask, which is
`1 − bestBid = 1 − 0.8900 = 0.1100`. Total **1.0100** — one spread above par, before
two taker fees. There is no state in which `p_yes + p_no < 1`. **The sub-$1 pair this
strategy depends on does not exist on this venue.**

### What that means for the requested state machine

`COMPLEMENT ≡ EXIT`. Acquiring the opposite outcome *is* selling what you hold, on the
same book, at the same instant's complementary price. So the four-action machine
collapses:

> **OPEN → EXTEND → EXIT** (three actions, one of which is directional accumulation)

You cannot hold YES and NO as separate inventory that later locks a pair. Matched
inventory nets flat. Consequently **matched-pair P&L and directional residual P&L are
not separately realizable here** — the "pair" is a round trip, and its profit is
`sell − buy`, nothing more.

This retro-explains three earlier findings that looked like strategy results and were
actually identities: `PAIR ≡ profitable UNWIND`; "completed pairs never lose, minimum
$0.00" (true by construction — a completed pair *is* a profitable round trip); and
FORCED exits carrying 100% of the loss (unsold directional inventory marked out).

### Why L5's behaviour cannot be reproduced here

The behaviour the brief describes — repeatedly add to whichever outcome is cheap, let
inventory oscillate, buy the other side later when economics improve, hold both —
requires **two separately tradeable tokens with independent books**, which is what
Polymarket's main CLOB has (YES/NO outcome tokens) and what L5 actually trades. PMUS,
the regulated venue, exposes one book per market with a derived short price. The
strategy is not mis-specified; **the venue is the wrong one for it.**

## 2. EXTEND: the verdict the brief asked for in advance

The brief's own standard was: *EXTEND must have positive incremental expectancy given
current price, inventory and remaining opportunity to complement/settle. If another
purchase only makes historical P&L look better because we're averaging cost, reject
it.*

On a single-book venue there is **no complement path**. For an extension at price `p₂`
on an existing long at `p₁`, the added contract pays only if it can later be sold above
`p₂` (plus the crossing and fees), or settles at 1. Formally, the added unit's
incremental expectancy is

> `E[added] = P(settle UP)·(1 − p₂) − P(settle DOWN)·p₂ − fees`, or `s − p₂ − fees` if sold at `s`

which contains **no term involving `p₁`, the existing inventory, or the matched-pair
cost**. Averaging down changes the *reported* average cost and nothing about the
expectancy of the new unit. So on this venue EXTEND is definitionally "add directional
risk at the current price" — exactly the thing the brief said to reject. **Rejected.**

## 3. Economics — already measured, no new replay needed

The question "does the reduced OPEN → EXTEND → EXIT machine make money" is already
answered on gate-passing, duplicate-free, cap-uncensored data:

| measure | value | source |
|---|---|---|
| clean windows scored | 11 | `IMBALANCE_PROSPECTIVE_TABLE.json` |
| first legs | 2,401 | same |
| **cumulative baseline net, c.99** | **−$637.05** | same |
| toxic share of fills | ~29–53% by ceiling | mechanism audit |
| MAE vs MFE at every entry depth | MAE 3.1–3.7× MFE | entry-depth diagnostic |
| forced-exit share, clean windows | 17.6%–84.1% | window character |
| net per leg, frozen baseline | −$0.22 | `82714061` rescore |

Every variant tested — touch-following peg, fixed-price resting, four pair ceilings,
three TTLs, four entry depths, 13 pre-registered candidate rules — is **negative at
useful capacity**. The two configurations that once looked positive (A420, A540 at
+0.22/leg) went **negative** when the recorder defects were fixed. There is no
$1/$2/$3/$5-per-window contract size to report, because the per-window expectancy is
negative at every size: scaling multiplies a loss. Stating a size table would imply a
positive base rate that does not exist.

## 4. Rebates and rewards — authoritative, and worse than assumed

**Taker fee θ = 0.0695** per `p(1−p)` per share. Confirmed twice: the live market
object carries `feeCoefficient: 0.0695`, and `src/pmus_fees.py` reproduces all 99 rows
of the published `docs.polymarket.us/fees` table from θ = 0.0695 with banker's
rounding (effective 2026-09-17, previously 0.06).

**Maker rebate = 0.0125·p(1−p) per share**, credited at fill, same published table.
A credit, never netted against edge.

**Taker rebate: not ours.** Tiers begin at $250,000 of prior-month taker volume.

**There is NO liquidity-incentive program on BTC15m.** The complete `category=CRY`
listing is **54 markets on a single program** (`crypto_20260924`, pool $400/day,
targetSize 15,000, discountFactor 0.3, daily) and **none of them is an up/down
market** — zero `updown`, zero `btc-updown-15m`. A 30,000-program scan of the
unfiltered endpoint likewise returned only SPR (29,904) and POL (79). So resting
liquidity on BTC15m earns **nothing** beyond the per-fill maker rebate.

Maker economics therefore cannot change which actions are worthwhile by much: the
rebate is 0.0125/0.0695 ≈ **18%** of the taker fee, and at p = 0.5 it is $0.0031 per
share. Against measured per-leg losses of ~$0.22 it is not a swing factor.

### A discrepancy in our own code, found here

`scripts/pmus_pair_or_unwind.py` and `scripts/pmus_imbalance_mechanism_audit.py` both
hard-code `TAKER = 0.07`, not the authoritative **0.0695** in `src/pmus_fees.py`. The
direction is conservative (it overstates fees by 0.7%) and it does not change any
verdict above, but those scripts should read the fee module rather than a copied
constant — that is precisely the failure `pmus_fees.py` was written to end.

## 5. A capability found while checking — and a reference-feed error it exposes

`/v1/markets?slug=…` returns `assetPriceTerms` per market:
`indexSymbol: BRTI`, `priceToBeat: 84223.83`, `settlementPrice: 84445.85`,
`windowStart`, `windowEnd`, plus 1-second chart bucket metadata.

Two consequences:

1. **Strike distance is obtainable after all.** I previously reported it as
   `NOT_IN_TAPE`; that was true of the authenticated WS rows but not of the venue API.
   The window-character descriptors can be completed from this endpoint.
2. **Our BTC reference is the wrong index.** PMUS settles on **BRTI** (CF Benchmarks
   Real-Time Index). The `btc-ref-collector` records **Coinbase** spot. Every
   external-BTC statement in this program is therefore a proxy for an index we were not
   measuring — including the lead/lag result (peak at +0.05 s, corr 0.5138). That
   conclusion may well survive, but it was measured against the wrong series and should
   be labelled as such. `priceToBeat` and `settlementPrice` give the true per-window
   anchors with no proxy at all.

## 6. The single missing measurement

For the question as asked: **nothing is missing.** It is answered negatively by venue
structure, and no further collection would change it.

If the goal is genuine L5-style two-sided inventory replication, the missing thing is
not a measurement but a venue: a market with **two independently quoted outcome books**
where `p_yes + p_no < 1` can occur. That is Polymarket's main CLOB, which is where L5
trades. Whether that venue is accessible and lawful for this account is a separate
question I have not investigated and would not without instruction.

## 7. Bounded live probe

**I am not proposing one.** A probe tests a hypothesis with positive expected value;
the measured expectancy here is negative at every configuration tried, the four-action
machine the probe would test cannot exist on this venue, and there is no liquidity
reward to harvest. Committing money to the reduced OPEN → EXTEND → EXIT machine would
be buying a measured loss.

The smallest *defensible* next step, if this lane is to continue at all, is not a probe
but a decision: either accept that PMUS BTC15m is a single-book directional venue and
stop trying to run matched-inventory strategy on it, or move the L5 replication
question to a venue with two-token books. The prospective `trade_imbalance` diagnostic
can keep running on new windows at zero cost while that is decided.
