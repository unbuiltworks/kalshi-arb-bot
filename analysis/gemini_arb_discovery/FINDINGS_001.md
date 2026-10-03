# Does Gemini contain repeatable, size-meaningful, executable arbitrage?

**Answer: No. Not one candidate reached even the QUOTED stage, in any category,
on any of eleven independent passes.**

Research lane: platform-wide executable-arbitrage discovery.
Branch: `research/gemini-platform-arb-discovery`. Read-only throughout: no order
was placed, no credential was used, no authorization was changed, and the active
Gemini BTC5m/L5 information-edge experiment was not touched.

Measured 2026-10-03, between ~12:05Z and ~12:50Z.

---

## 1. What was measured

| | |
|---|---|
| Discovery endpoint | `GET /v1/prediction-markets/events` (public, paginated) |
| Depth endpoint | `GET /v1/book/{instrumentSymbol}` (public, **full** ladder) |
| Tape endpoint | `GET /v1/trades/{instrumentSymbol}` (public) |
| Active events | **768** |
| Active contracts | **7,632** |
| Pages read / read errors | 20 / **0** |
| Full-payload digest | `33cd67b4…7de567b5` (46,356,815 bytes) |

Coverage of the whole catalogue was complete on every pass — no page failed, so
the negative result is not a negative over an unknown fraction of the platform.

## 2. Structural relations established from settlement rules

Relations were built from the venue's **machine-readable** `strike` objects
(`{type, value}`), `expiryDate` and `source` — not from parsing English labels —
and each one is emitted with the reason it is logically guaranteed.

| Relation | Verified | Basis |
|---|---|---|
| Monotone strike ladders | **638** (covering 6,442 of 7,632 contracts, 84%) | For one quantity at one expiry, `{X ≥ s₂} ⊂ {X ≥ s₁}` when `s₁ < s₂`, so `YES(s₁) ≥ YES(s₂)` must hold in every state of the world |
| Exhaustive partitions | **49** (all Weather) | Contiguous bucket cover at 1 °F with both tails unbounded, verified edge-to-edge |
| Duplicate/equivalent contracts | **0** | Identical `source` + `expiry` + strike across different events |

### Category 1 — same-event YES/NO complement — is impossible here, not merely absent

Gemini runs **one book per contract** and quotes the NO side as an exact
arithmetic complement of it. Over 4,099 active contracts carrying all six quote
fields, the identity

```
bestAsk == buy.yes      buy.no  == 1 - bestBid
bestBid == sell.yes     sell.no == 1 - bestAsk
```

held with **zero exceptions**. "Buy YES and buy NO on the same contract" is
therefore not two books being crossed — it is buying at the ask and selling at
the bid of a single book, costing `1 + spread`, which is never below 1. This
category is asserted void by construction and is not scanned.

## 3. The economic test, and what it found

For every verified pair the scanner priced the only construction that does not
depend on someone choosing to trade with us — **crossing the spread on both
legs**:

> buy YES(low strike) and buy NO(high strike). Payoff is 1 when the underlying
> lands outside `[s₁, s₂)` and 2 inside it, so **1 is guaranteed**. Cost is
> `ask(s₁) + (1 − bid(s₂))`, so the per-unit gross edge is exactly
> `bid(s₂) − ask(s₁)`.

Eleven passes, ten of them over a continuous 405-second window:

| Metric | Result |
|---|---|
| Verified ladder pairs priced **per pass** | **~25,344** |
| Pairs crossed (gross edge > 0) | **0**, every pass |
| Closest approach `bid(short) − ask(long)` | **0.00** (exactly touching, never crossed) |
| Quoted candidates | **0** |
| Depth-executable candidates | **0** |

**The closest the platform came was a gross edge of exactly zero** — bid equal to
ask across two strikes. Zero gross edge is a guaranteed *loss* once fees are
charged, because each leg's fee rounds up to a whole cent independently.

So the result is not "we found small edges that fees ate". It is that **the
monotonicity constraint is never violated at all**, on 25,000 pairs, repeatedly.

## 4. Why the structurally richest families are unreachable anyway

The families where guaranteed relations are easiest to verify are the ones with
**no liquidity whatsoever**.

Spread and two-sided availability are from catalogue quotes (**full** coverage of
every active contract); depth and trade rate are **sampled** and labelled as such.

| Category | Events | Contracts | Two-sided | Median spread | Median touch qty (ask) | Median trades/min |
|---|---|---|---|---|---|---|
| Sports | 608 | 6,883 | 55.9% | 0.03 | 294 | 0.0013 |
| Crypto | 93 | 255 | 76.5% | 0.03 | 178 | 0.0056 |
| Weather | 49 | 322 | **0.0%** | — | — | 0.0464 |
| Commodities | 17 | 124 | **0.0%** | — | — | — |
| Economics | 1 | 5 | 100% | 0.01 | 500 | — |

**All 49 verified exhaustive partitions are in Weather, and Weather has no
two-sided market at all.** Its contracts carry a bid or an ask, never both, so
the partition basket cannot be bought at any size for any price. The 17
commodity ladder events are emptier still: most contracts have no resting order
on either side.

Trade rates put the platform in context: the *median* Crypto contract prints
about once every three hours, and the median Sports contract about once every
thirteen. This is not a venue where a fleeting mispricing gets competed away
faster than we can see it — it is a venue where very little trades at all.

## 5. The trap this scanner was built to avoid, with a live example

`prices.buy.yes` is populated **even when no ask rests in the book**. 18 active
contracts quote a `buy.yes` with no `bestAsk` behind it.

A scanner reading that field as an executable ask reports arbitrage in
`WXHIGH-CHI-2610040359` — a *correctly verified* exhaustive 6-way partition whose
`buy.yes` values sum to **0.99**, i.e. a "1 cent guaranteed edge":

| Contract | `buy.yes` | Real resting asks |
|---|---|---|
| 66 °F to 67 °F | 0.30 | **none** |
| 68 °F to 69 °F | 0.28 | **none** |
| 64 °F to 65 °F | 0.21 | 0.19 × 10 |
| 70 °F to 71 °F | 0.12 | **none** |
| 72 °F or above | 0.05 | 0.04 × 10 |
| 63 °F or below | 0.03 | 0.03 × 10 |

Three of the six legs cannot be bought at all. The basket is unbuyable at any
size, and the "1 cent edge" is an artifact of a display field. This is the single
false positive the UI-level view produces on this platform, and the depth-based
scanner rejects it for the stated reason.

## 6. Honest limits of this result

1. **Not every contract is covered by a verified relation.** 6,442 of 7,632
   contracts (84%) sit in a verified ladder and 322 more in verified partitions.
   The remaining ~895 carry no `strike` — two-way sports moneylines, golf
   outrights. Their exhaustiveness **cannot** be established from structured
   fields: it needs the settlement PDFs read for draw, void and postponement
   provisions. They are reported as `unverified_outcome_sets` and are **not**
   claimed either way. A 2-way market summing below 1 would be an arbitrage only
   if a draw is impossible *and* a postponement cannot void one leg.
2. **Duplicate detection is narrow.** It requires a declared `source`, which only
   249 contracts (all Crypto) carry. Cross-event duplicates among sports
   contracts would not be detected by this pass.
3. **Depth and trade rate are sampled**, 40 and 25 contracts per category. The
   spread and two-sided figures are full-coverage; the depth figures are not.
4. **Displayed size is an upper bound, not a promise.** One contract's best bid
   moved 0.95 → 0.97 between two reads seconds apart. Any capacity figure means
   "resting when measured".
5. **No leg was submitted.** Everything here stops at DEPTH_EXECUTABLE by
   construction. FILLED, SETTLED and REALIZED require an account and are not
   reachable from this lane.

## 7. Legging risk, had anything been found

No multi-leg or batched prediction-market order endpoint appears in the paths
this repository has established for Gemini, so an *n*-leg basket is *n*
independent orders. Between the first and last acknowledgement the remaining
legs can move or empty, leaving outright exposure on whatever filled.
`immediate-or-cancel` bounds a stale leg but does **not** make the basket atomic:
a cancelled leg still leaves the filled ones on. Weather partitions would have
needed 6–7 simultaneous legs on a venue whose Weather books are one-sided —
`SIMULTANEOUSLY_EXECUTABLE` would have been the binding constraint even if a
gross edge had existed.

## 8. Conclusion

**Gemini does not currently contain repeatable, size-meaningful, genuinely
executable prediction-market arbitrage.**

The reason is structural rather than competitive:

- the single-book design makes same-contract complement arbitrage *impossible*,
  not rare;
- the 638 verified monotone relations are **never violated** — 25,000 pairs per
  pass, zero crossed, best approach exactly 0.00;
- the only families with provable exhaustive partitions (Weather, Commodities)
  have **no two-sided market at all**, so their structure is unexploitable
  regardless of price;
- fee rounding to a whole cent *per leg per fill* puts a hard floor under the
  minimum profitable size that the venue's own 0.01 minimum order size is
  nowhere near.

Nothing here is a reason to arm anything. The recommended next step is **not**
a live probe but closing limit (1) above: reading the settlement PDFs for the
~895 strikeless contracts, which is the only part of the platform where a
guaranteed relation might still be hiding.

---

### Reproducing

```
python3 -m src.gemini_arb_discovery.scan    --out <dir>    # one full pass
python3 -m src.gemini_arb_discovery.monitor --passes 10 --interval 45
python3 -m src.gemini_arb_discovery.census  --out <dir>    # liquidity census
python3 -m pytest tests/test_gemini_arb_discovery.py       # 25 tests
```

Committed evidence: `scan_final/` (pruned universe snapshot + SHA-256 of the full
46 MB payload, and the scan result), `monitor_01/`, `census_01/`.

### Why the test suite matters to this report

The headline is a negative, and a negative from an instrument that cannot
register a positive is worthless. `tests/test_gemini_arb_discovery.py` therefore
injects arbitrage that is unambiguously present and requires the scanner to find,
price and size it — including the end-to-end `depth_confirm` path — and requires
it to decline the same setup once fees or depth make it uneconomic. All 25 pass.
