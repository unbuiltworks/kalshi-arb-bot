# L5 Aggressive Profit Validation — Evidence and Decision

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · follows handoff `59d5911c`
**Mode:** read-only on existing results. No orders, arming, deployments, restarts, services, collectors, storage changes or spending. Nothing in the replication session's working tree touched.

---

# DECISION: STOP on the current parameterisation. Do not arm. Do not add capital.

One number decides it, and it is venue-attested, not modelled:

> **Total verified realized net profit across all lanes, to date: −$0.06.**
> `l5-pmus-live` realized_total **−$0.100000** (venue, 17:29Z today) + manual account realized **+$0.040000** (venue cross-check).

Against a $480/day objective. Everything else in this repository is theoretical, shadow, proxy or another venue's wallet.

---

## 1. Three strongest already-supported opportunities, ranked

| # | Opportunity | Strongest supporting evidence | Defensible executable net-profit potential |
|---|---|---|---|
| **1** | **PMUS BTC 15m / tennis sequential maker pairing** | Only lane with a **live executor, real venue fills, reconciliation, risk limits, preflight PASSED**. 77 venue-attested owned orders; R4 action-by-action campaign with 10 real fills. | **Negative at demonstrated scale.** Mean pair cost **$0.9967** → 0.33¢/pair; one unpaired residual cost **38.0¢/share**. See §4. |
| **2** | **Polymarket L5 wallet mirroring** | 42 days settled cash, **$1,766/day**, 94.3% BTC 15m, 95 of 96 events/day, mean +$12.02/event. | **Not executable by us.** Wrong venue; figure is unaudited net cash flow; no lawful transfer path established; PMUS price structure differs (1¢ spread at every percentile). |
| **3** | **Kalshi `tapered_deci_cent` extremes** (<10¢, >90¢) | Fee math least adverse where `P(1−P)` is minimised; ticks 10× finer (0.1¢). | **Unmeasured** — and the cheapest unexplored thing left. Blocked only by the 5.8-snapshots/day cadence. |

Everything else is already stopped: Gemini maker quoting (`ee75b482`/`99e8e033`), PMUS NFL/UFC/boxing/CFB sports pairing (`f9e4bdac`).

## 2. Action-by-action comparison, top opportunity

Source: `analysis/l5/R4_ACTION_COMPARISON.md` on `claude/l5-shadow-mode-deployment-epkz1i`. Three PMUS ATP tennis markets, 2026-09-07. **217 decisions · 64 orders submitted · 10 filled (all maker) · 2 markets settled.**

| Relation | L5 expected | Our engine *sampled* | Our engine *acted* (excl. hold) |
|---|---:|---:|---:|
| open | 15.58% | 17.05% | 20.31% |
| **extend** | **48.64%** | 50.23% | **1.56%** |
| complete | 35.78% | 32.72% | 78.12% |

**Fidelity: L1 sampled vs L5 = 0.0612** (our *state sampling* tracks L5 almost exactly) but **acted vs L5 = 0.9415** (what we *do* diverges badly). **Hold share 0.7051** — we decline 153 of 217 decisions.

Per-market fills, verbatim:
- `aec-atp-petbru-alecan`: 19 submitted / **1 filled** — `open YES 5.24@0.38`. Unfilled: `deadline` ×18.
- `aec-atpcq-aleher-tundur`: 16 / **5** — `open YES 5.31@0.47`, `complete NO 5.2@0.51`, `complete NO 0.11@0.50`, `open YES 5.1@0.49`, `complete NO 3.24@0.22`. Unfilled: `rejected` ×7, `deadline` ×2, `repriced` ×2.
- `aec-atpcq-norgom-ollwal`: 29 / **4** — `open YES 5.31@0.47`, `complete NO 5.2@0.51`, `extend YES 4.9@0.51`, `complete NO 5.01@0.51`. Unfilled: `deadline` ×21, `rejected` ×3, `repriced` ×1.

## 3. Exact reasons for disagreement

**Timing/inventory — refusals that produced the 153 holds:**

| Refusal | Count | What it is |
|---|---:|---|
| `unmatched_usd_cap` | **62** | residual cap binding — the single largest source of divergence |
| `entry_band_left` | 44 | price left the entry band before we acted |
| `off_distribution_hold` | 24 | state outside the fitted action model |
| `complement_not_priceable` | 21 | complement could not be priced inside the pair bound |
| `complete_without_residual` | 1 | |
| `spend_cap_reached` | 1 | |

**Side/price:** all 10 fills were maker. Completions priced at engine pair bases of 0.98, 0.97, 0.6998, 0.98, 0.9992 — i.e. the engine *does* hold the ≥1¢ bound; one completion was `unlocked_by_rung_bound` at an open-lots basis of **1.0191** (above par on open lots).

**Size:** executed/L5 p50 ratio 0.382 / 0.462 / 0.541 across the three markets — **we trade roughly 38–54% of L5's size.**

**Cadence:** p50 31.4s / 10.1s / 30.1s versus L5's own rhythm; one gap of 1,803s.

**The primary disagreement, stated plainly:** L5 **extends** (48.6% of its actions); our engine extends **1.56%**. The residual cap (`unmatched_usd_cap`, 62 refusals) is what blocks extension. We instead over-complete (78% vs 36%). We are sampling L5's states correctly and then refusing the action L5 most often takes.

**And the venue says the manual trading was mostly taking, not making.** `analysis/l5/WHY_THE_BOT_MISSES_MANUAL_FILLS.md`, from an authenticated order walk (77 owned orders, **64 MANUAL / 13 AUTOMATIC** by the venue's own `manualOrderIndicator`): **median `resting_s` = 0.002 s** — half the manual acquisitions filled in two milliseconds, which are **crosses, not rests** — and **35 of 64 carry a non-zero commission**, the signature of a taker fee. The one clean maker sequence (`1430z`) is *the exception in the manual history, not the pattern*. Six fills are exits (`SELL_LONG`/`SELL_SHORT`), all 1–2 ms.

So part of the "disagreement" is that the engine is maker-only by safety overlay (`SAFETY_OVERLAY:MAKER_ONLY_NO_TAKER` fired **2,738** times today) while the behaviour being replicated was substantially taker.

## 4. Realistic capturable profit, after all four haircuts

**Demonstrated paired edge — the entire venue-attested record:**
```
paired shares ever        11.98
cost basis                $11.9405
mean pair cost            $0.9967   ->  gross edge 0.330c per paired share
total gross, ever         $0.0395   (cross-checks the account's +$0.0400)
```

**Residual settlement loss, measured:** the one unpaired first leg in R4 (`open YES 5.24@0.38`, complement never filled) settled at **venue realized −$1.9912 = −38.0¢ per residual share**.

**Break-even:**
```
paired shares needed to offset ONE such residual   604
paired shares demonstrated, ever                   11.98
shortfall                                           50x
```

**Fees:** PMUS taker is effective-dated `0.0695·p(1−p)` → **1.74¢/share at p=0.50**, 1.11¢ at 0.80, 0.33¢ at 0.95. **A single taker leg destroys a 0.33¢ pair edge at every price.** The strategy is maker-maker-only or it is nothing — which is precisely what the safety overlay already enforces, and why it blocks the taker behaviour the manual history actually used.

**Queue uncertainty:** on the live BTC book right now, a modelled extend at $0.05 faced **`queue_ahead` 19,039.95** shares against `volume_through_level` 19,723.36. The current book is `bid 0.9900 / ask 0.9900` with **47,372 bid shares vs 366 ask shares** — zero-width at 99¢. Queue priority at our own price level remains unknown everywhere.

**Adverse selection:** two independent observations, both consistent in sign — Polymarket unpaired residual ~−12¢/share (accounting attribution) and Gemini buy-only median −20.00¢/contract.

**Net:** 0.33¢ per pair, maker-maker only, against 38¢ per residual event and a 50× break-even shortfall. **Capturable profit at demonstrated scale is negative, and no fill-rate assumption rescues it** — more fills at this pair cost produce more residual events, not more profit.

## 5. Smallest concrete change with an economic effect

**Move the complement-reachability test from completion time to entry time.**

Today `complement_not_priceable` is a refusal evaluated when the engine tries to *complete* (21 holds). By then the first leg is already filled and the residual already exists — which is exactly how the −$1.99 happened: `open YES 5.24@0.38` filled, then 18 consecutive `deadline` unfills on the complement.

The change: **refuse to open a first leg unless the complement is simultaneously priceable inside the pair bound with observed depth at that price.** One predicate, at one call site, in the existing decision path — no new service, collector, model or infrastructure.

Why this and not edge-widening: the arithmetic in §4 says the binding term is **residual frequency × 38¢**, not pair edge. Widening the edge from 0.33¢ to even 1¢ still needs ~200 pairs per residual. Preventing the residual is the only lever with a 50×-scale effect. The supporting measurements already exist — complement never reached in 96.8% of 1,956 sports cases, median gap 2¢, median favourable excursion **0.0¢**.

Expected effect: entries fall sharply (correctly), residual events approach zero, and the lane's realized P&L stops being dominated by a term 115× larger than its edge. It will likely show that qualifying entries are **rare** — which is the honest answer, not a failure.

## 6. Binary decision

**STOP** on the current parameterisation. Specifically: do not arm, do not add capital, do not widen caps to increase fill count.

**Not** "stop forever" — the lane retains a live executor, reconciliation, risk limits and preflight, which are the hard parts and are preserved.

**The single missing measurement that would reopen it:** the **residual settlement loss distribution at n ≥ 30 unpaired first legs**, joined to venue settlement. Everything above turns on 38¢/share from **n = 1**, against a paired edge from **11.98 shares**. Both samples are too thin to be the basis of a capital decision in either direction — and that asymmetry is why the decision is STOP rather than continue: at 50× break-even, the burden is on the opportunity.

This measurement needs **no new infrastructure**: `l5-pmus-live` already reconciles against the venue, already books `residual_contribution_usd` per market, and is already running disarmed. It needs time and the §5 predicate, not spend.

## 7. Two integrity flags found while inspecting live state

**(a) An open reconciliation drift.** `L5_LIVE_RECONCILE` at 17:29Z reports for `cpc-btc-updown-15m-2026-10-12-0030z`: `ours 2 DOWN / venue 0`, `gross_net_agrees: false`, market named in `gross_detail_lost`. The module's own warning: *"a matched pair silently deleted is a locked economic result silently deleted."* `unresolved_accounting.count` is 0 and `blocks_live_authorization` is false, so **this drift is not currently gating arming.** Worth the replication session's attention before any arming decision.

**(b) Current live limits, for the record** (not changed by me): `max_usd_per_market $5`, `max_abs_residual 10`, `max_total_exposure_usd $5`, `max_concurrent_markets 1`, `max_live_orders 8`, `daily_realized_loss_halt_usd $5`, available cash **$242.98**, `armed: false`, `owner_arm_token: false`, policy `RESIDUAL_CAP_k2`, frozen rule `030d8bf`.

## Next executable step

**One predicate, in the existing decision path: make complement priceability-and-depth an entry precondition (§5). Then let `l5-pmus-live` run disarmed until it has n ≥ 30 residual observations, and read `residual_contribution_usd` against settlement.**

That is the whole next step. It requires no authorization, no capital, no new service and no spend — and it is the only change on the table that acts on a term 115× the size of the edge. Owner authorization becomes relevant only if that measurement comes back favourable, and then only with the durable reconciliation, independent watchdog and hard limits you specified.
