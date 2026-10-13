# L5 Round 7 — PMUS Sports / Sub-4h Census (first pass)

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · continues `99e8e033`
**Mode:** read-only. Railway log reads + repo reads. No orders, deploys, services, spend, no new engine, no change to replication infrastructure. Gate unchanged.

---

## Headline

**The venue-native action is verified, the opportunity *frequency* is real and measurable, and the dollar metric currently reported by the live collector is definitional rather than economic — so net expectancy remains UNMEASURED.** At the only depth the gate actually certifies (q=1), the theoretical ceiling across the whole cleared slate is **$26–42/day**, below the $50 archive threshold *before* any execution haircut.

I applied the Gemini lesson first: I proved what the venue natively supports and audited what the headline metric means **before** assigning any P&L.

## 1. Venue-native executable semantics — verified from our own adapter's output

From the live `PMUS_SPORTS_L5_DECISION` (14:51Z today), a real decision row:

```json
"next_action": {"decision":"quote","relation":"open","side":"NO","target_price":"0.49",
                "q1_size":1,"inventory_effect":"+1 NO","max_pair_cost":"0.98",
                "state":"p45-55c|tte>10m|flat|open|q<5",
                "reason":"v2 recommends open (p=0.0593) under the >=1c-after-known-fees bound;
                          side chosen by cheaper maker touch (adapter convention, not a model output)"}
```

| Property | Established value |
|---|---|
| Native action | **`quote`** — post a resting maker order |
| Side semantics | **buy-the-outcome**: `side ∈ {YES, NO}`, `inventory_effect: "+1 NO"` |
| Mechanism | **pair both outcomes** to `max_pair_cost ≤ 0.98` ⇒ ≥1¢ locked per pair |
| Size | **`q1_size: 1`** — one contract |
| Price band | `MIN_PRICE/MAX_PRICE = 0.20 / 0.80` |
| Execution state | `order_entry: false`, **DISARMED, no credentials or order path** |

**This is structurally better than the Gemini maker-spread mechanism.** It is not a directional bet and not spread capture: buying YES and NO for ≤$0.98 combined is a *genuine locked pair* — the original thesis in `CLAUDE.md`. Unlike Gemini's `we_sell`, this action is confirmed native, because our own adapter emits it as a buy on a named outcome.

**What the collector explicitly cannot observe** (its own labels):
- *"direction inferred from lastTradePx vs BBO"* — aggressor side is **inferred**, not observed
- *"maker identity unobservable"* — we cannot tell whether a given print was maker or taker
- *"fills are 'possible' upper bounds from later inferred trades"*
- *"ALL fills/economics THEORETICAL from public snapshots; nothing realized"*

## 2. Coverage and opportunity frequency — measured

Live status, 2026-10-13 14:51Z:

| Metric | 15m window | 1h window | since restart |
|---|---:|---:|---:|
| Markets tracked | **2,601** | 2,601 | 2,601 |
| Markets with data | 223 | **478** | 478 |
| `Q1_GATE_CLEARED` | 1 | **9** | 9 |
| `SHADOW_CANDIDATE` | 29 | 31 | 31 |
| `NOT_READY` | 193 | 438 | 438 |

Leagues appearing among candidates: **ITF men's tennis (`itfme`)**, **ATP**, **WTA**, **cricket (`t20icr` T20, `testcr` Test)**, `setkameua`. Leading candidates show `ask_side_served_pct: 100.0` with 14–27 inferred trades and `matched: 1–2`.

So: of 2,601 tracked markets, **~18% produce usable data and ~2% (40 markets) reach candidate status; 9 clear the gate at a given moment.** Frequency is genuine and non-trivial — this is a real universe, not an empty one.

## 3. The critical audit — the dollar metric is definitional

`theoretical_dollars_per_capital_hour` is the collector's headline economic number (0.0975–0.1950 across candidates). Its definition, from `src/pmus_sports_offers_hotfix.py`:

```python
hold_h = st.SLOT_S / 3600.0                                  # 300s -> 0.08333 h
edge   = float(st.MIN_EDGE + 2 * st.maker_rebate(st.D("0.5")))
per_cap_hour = (matched / max(len(slots),1)) * edge / max(hold_h,1e-9)
```

with `MIN_EDGE = D("0.01")`, `SLOT_S = 300.0`, `maker_rebate(p) = D("0.0125")*p*(1-p)`.

At p=0.50: `edge = 0.01 + 2(0.003125) = $0.01625`, and `0.01625 / 0.08333 = 0.195`. **This reproduces the reported figure exactly** — 0.195 is a 100% slot-match rate, 0.0975 is 50%.

**Three consequences, and they are decisive:**

1. **There is no capital in the formula.** Despite the name, it is **dollars per hour per single q=1 pair**, not a return on capital. Any reading of 0.195 as a rate of return is wrong.
2. **The edge is assumed, not measured.** `MIN_EDGE` is the *configured gate threshold*. The formula asserts that every matched slot captures exactly the minimum qualifying edge. The only measured input is the **match rate**. It contains **no fill probability, no adverse selection, no residual-inventory loss, and no queue model.**
3. **38% of the assumed edge is an unverified rebate.** `2 × maker_rebate(0.5) = $0.00625` of the $0.01625 comes from a hardcoded `0.0125·p(1−p)`. Note the contrast within the same file: `taker_fee` deliberately reads from an effective-dated schedule with the comment *"never copied… a local copy here would under-report the cost"* — but the maker rebate **is** a local copy. PMUS maker-rebate eligibility is unverified.

This is the Gemini failure mode recurring in a different costume: a number that looks like P&L but is a configuration constant multiplied by an observation.

## 4. Theoretical ceiling at the only certified depth

The gate's depth test is literally `float(bid_touch_qty) >= 1` — it certifies **one contract**. Depth beyond q=1 is not established anywhere in this pipeline.

| Scenario (q=1, theoretical, no execution haircut) | $/hour | $/day |
|---|---:|---:|
| 9 `Q1_GATE_CLEARED` × 0.195 (100% match, incl. assumed rebate) | $1.76 | **$42** |
| Same, **rebate stripped** (edge = $0.01) | $1.08 | **$26** |
| 9 cleared + 31 shadow candidates, at their reported rates | ~$4.8 | ~$115 |

**Even the most generous reading is below $150/day, and the defensible reading is below the $50 archive threshold** — and every figure above assumes the threshold edge is captured on every matched slot with no adverse selection, no missed fills and no residual losses. Realized economics can only be lower.

To reach the $480/day objective from this mechanism would require roughly **12–18× more size or markets** than q=1 across 9–40 markets provides, and size is exactly what the gate does not certify.

## 5. Gate application and verdict

`ARB_MECHANISM_CAPACITY_GATE.md`, thresholds unchanged.

**Status: `UNMEASURABLE` for net expectancy, with a theoretical ceiling below the gate at certified depth.**

Stated precisely, and distinguishing the four levels you asked for:
- **Observable opportunity:** real and measured — 40 candidate markets, 9 gate-cleared, 100% ask-side served on leaders.
- **Hypothetical fill:** the collector's own upper bound; maker identity unobservable, direction inferred.
- **Confirmed execution:** none. `order_entry: false`, DISARMED, no order path.
- **Settled realized:** none.

No capacity figure is assigned. I am **not** repeating the round-5 error of multiplying an assumed per-unit edge by a volume figure.

## 6. Three strongest categories by defensible estimated capacity

Ranked on measured frequency and data quality, with capacity explicitly **UNMEASURABLE** in each:

| # | Category | Measured frequency | Data quality | Capacity |
|---|---|---|---|---|
| **1** | **ITF men's tennis (`itfme`)** | Most gate-clears; 2 of 5 top 15m candidates, `matched: 2`, ask served 100% | Best — two-sided inferred flow, 14–27 trades/window | **UNMEASURABLE** (ceiling ≤$42/day at q=1 slate-wide) |
| **2** | **ATP / WTA tennis** | Present in both 15m and 1h candidate sets | Good — two-sided flow | **UNMEASURABLE** |
| **3** | **Cricket (T20 `t20icr`, Test `testcr`)** | Present in candidates; long-duration, many in-play windows | Moderate; Test cricket breaches the 4-hour settlement preference | **UNMEASURABLE** |

Tennis is the strongest family: discrete scoring, frequent price movement, many concurrent matches, and genuinely sub-4-hour settlement.

## 7. Largest blocker, next action, costs

**Largest blocker:** we have never measured the **actual achievable pair cost**. The pipeline assumes `≥1¢ after known fees` as a gate and then reports that same 1¢ back as earnings. Nobody has measured the *distribution* of `yes_ask + no_ask` on these markets — which is the real edge — nor depth beyond one contract.

**Next concrete action (read-only, no new infrastructure):** measure the realized pair-cost distribution directly from `pmus-ws-evidence-recorder` book rows, which already carry `bid/ask/bid_qty/ask_qty/status` per market and are **live now**. Specifically, for sports markets: the distribution of `yes_ask + no_ask` (how often is it ≤ $0.99, ≤ $0.98, ≤ $0.95?), the depth available at those touches beyond q=1, and how long such a pair persists. That replaces the assumed 1¢ with a measured edge and the assumed q=1 with measured size — the two inputs that decide whether this lane can reach $50, $150 or $480/day.

**Costs:** $0. The collector and recorder are already running; this is log reads plus analysis.

**Data gap to state plainly:** settlement joinability for sports markets is **not yet established**. PMUS resolution records were not located in this pass. That matters for any hold-to-settlement claim, but — unlike Gemini — it is *not* on the critical path, because the pair mechanism locks its result at entry rather than depending on outcome direction.

## 8. Handoff to the replication session (informational; no change requested)

The transferable finding is the one you identified: **a seemingly profitable strategy can disappear when the modelled action is not supported by execution mechanics, or when the headline metric is definitional.** Concretely for PMUS: the native action is a `quote` posting a buy on a named outcome (`side ∈ {YES,NO}`, `inventory_effect "+1 NO"`), size q=1, paired to `max_pair_cost ≤ 0.98`; `theoretical_dollars_per_capital_hour` is `match_rate × (MIN_EDGE + 2×assumed_rebate) / slot_hours` and must not be read as realized or as a return on capital; and the maker rebate inside it is a hardcoded local copy while the taker fee is schedule-read. No code or policy change is requested.
