# L5 — P0 Reconciliation Integrity · P1 Counterfactual · P2 Evidence Audit

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · follows `25f552c1`
**Mode:** read-only. No orders, arming, deployments, restarts, services, infrastructure, spend. **No ledger evidence deleted or rewritten.** No code changed. Replication's working tree untouched.

---

# P0 — Reconciliation integrity: this IS a gate defect, and it fails OPEN

## The exact code path

`src/l5_pmus_live_runner.py:1235` (branch `claude/gemini-btc-economic-viability-h3u7zl`, which is what `l5-pmus-live` deploys):

```python
"blocks_live_authorization": bool(unresolved_accounting()),
```

And `unresolved_accounting()` at `:1993`:

```python
def unresolved_accounting() -> List[Dict[str, Any]]:
    """Every STANDING unresolved-accounting record. ...
    Nothing else lifts the block: there is no flag, no override and no
    env var that makes an unresolved entry stop counting."""
    return [dict(r) for r in UNRESOLVED_ACCOUNTING
            if not str(r.get("resolved_utc") or "").strip()]
```

`UNRESOLVED_ACCOUNTING` is a **module-level list of hand-written literal records** ending at `:1990`. The gross/net disagreement is computed somewhere else entirely — `src/l5_live_execution.py:1031-1038` — which builds `gross_net_agrees` and `gross_detail_lost` **and only logs them**.

**There is no code path from `gross_detail_lost` into `UNRESOLVED_ACCOUNTING`.** The two systems are disjoint: one is runtime-detected and advisory, the other is statically curated and authoritative. A runtime reconciliation failure therefore **cannot ever** raise `blocks_live_authorization`.

The asymmetry is the defect. The registry is rigorously hard to *lift* — "no flag, no override, no env var" — and has no automatic way to be *raised*. A matched pair that the reconciler itself describes as *"a locked economic result silently deleted"* is logged, named, and then passed over by the gate.

**This must fail closed before any future arming.** The minimal correction is in §P0-fix below.

## Cause of the 0030z mismatch

Evidence from the 17:29Z heartbeat and reconcile record:

| Field | Value |
|---|---|
| drift | `cpc-btc-updown-15m-2026-10-12-0030z\|DOWN`: `ours 2`, `venue 0` |
| `gross_before` | `up 0, dn 2, matched 0, residual −2, net −2` |
| **`venue_net`** | **`{}` — empty; the venue reports no position at all** |
| `gross_net_agrees` | `false`; market named in `gross_detail_lost` |
| `retired_markets` | includes `cpc-btc-updown-15m-2026-10-12-0030z` |
| `policy.max_clip_filled` | `{"cpc-btc-updown-15m-2026-10-12-0030z": "2", ...}` — our ledger did record a 2-contract fill |
| Market age | window 2026-10-12 00:30Z ≈ **41 hours before the read** |
| `awaiting_settlement` | 153 |

**Most likely: stale local inventory after settlement — not a missing fill and not incorrect settlement maths.** The window closed ~41 h earlier, the venue clears settled positions to nothing (hence `venue_net == {}`), and the local gross book still carries the 2 DOWN because settlement was never booked against it. `max_clip_filled` confirms the fill was real at acquisition, so this is not a phantom.

**But I cannot exclude the alternative**, and the repo shows precedent for it: `analysis/l5/evidence/phantom_repair_1345z/` exists, and the reconciler's own comment records *"how 2.25 DOWN shares arrived at 2045z with no cost."* So phantom local inventory has happened here before.

**The distinguishing test** (read-only, no arming): an authoritative `/v1/order` read of that market's own fills plus its settlement record. If the fills are attested and a settlement credit exists → stale local booking. If the fills are not attested → phantom inventory, which is the more serious case. I did not run it: it needs the live account's credentials and belongs to the replication session's lane.

Either way the position is **not** genuinely open — the venue says flat — so there is no live exposure. The integrity problem is the **unbooked settlement and the silent gate**, not an unhedged position.

## P0-fix — the minimal correction

Make the runtime reconciler a registrar. One call, at the point where `gross_detail_lost` is already computed (`l5_live_execution.py:~1032`): append an `UNRESOLVED_ACCOUNTING`-shaped record for each market in `gross_detail_lost`, so `blocks_live_authorization` becomes true automatically. Nothing is deleted; the existing `resolved_utc` discipline still governs clearing, and clearing still requires naming authoritative evidence.

This is additive, fails closed, preserves all evidence, and touches one call site.

---

# P1 — Counterfactual economic replay

**Scope limit, stated plainly.** I did **not** re-run the sequential replay engine. Doing so means executing the replication session's code paths against their state while their work is in flight, which risks exactly the duplication you ruled out. What follows is an **analytic counterfactual reconstructed from the fields R4 already recorded** — defensible, but weaker than a true A/B/C re-run. **If you want the real three-configuration replay, that is the replication session's call to run, and it is the one thing here I'd hand them rather than do.**

Source: `analysis/l5/R4_ACTION_COMPARISON.md` — 3 ATP markets, 2026-09-07, **217 decisions · 64 submitted · 10 filled (all maker) · 2 settled**.

## The discriminator exists in the recorded data

| Market | `within_old_bound_0_98` | `with_counterfactual` | Settled outcome |
|---|---:|---:|---|
| `aec-atp-petbru-alecan` | **0** | 18 | REALIZED **−$1.9912** |
| `aec-atpcq-aleher-tundur` | 31 | 31 | unsettled — no economics attributable |
| `aec-atpcq-norgom-ollwal` | **5** | 22 | matched 10.21 pairs @ $0.99920 → **+$0.0082** |

Market 1's complement was **never once** inside the 0.98 bound across 18 counterfactual observations. Market 3's was, 5 times. That is precisely the signal configuration B would gate on.

## Three configurations

| | **A — current frozen policy** | **B — complement priceable + depth at ENTRY** | **C — current policy, strict exposure limits, shadow-only extension ledger** |
|---|---|---|---|
| Entries retained / rejected | all 3 markets entered; 64 orders submitted, 10 filled | **market 1 refused**; markets 2–3 retained (1 of 3 entries rejected) | identical to A — C changes no decision, it only records |
| Completed paired shares / cost | 10.21 pairs @ **$0.99920** (market 3); 0 pairs market 1 | 10.21 pairs @ $0.99920 — **unchanged** | unchanged from A |
| Residual exposures / settlement | market 1: **5.24 DOWN-side residual → −$1.9912 settled** | **zero residual** in the settled set | unchanged from A |
| Gross / net P&L | venue realized **−$1.9912**; derived matched **+$0.0082** | **+$0.0082**, improvement **+$1.9994** | unchanged; adds the rejected-extension ledger |
| Fill uncertainty | 10/64 filled; unfilled `deadline` ×41, `rejected` ×10, `repriced` ×3 | strictly fewer entries ⇒ strictly fewer fills; **B cannot create a fill A did not have** | unchanged |
| Max drawdown (settled) | **−$1.9912** single-market | **$0.00** in this set | −$1.9912 |
| Action disagreement vs the fitted L5 model | `acted vs L5 0.9415`, hold share 0.7051 | **worse** — refusing entries raises hold share and widens the gap | same as A; quantifies the gap instead of closing it |

**Which specific losses B avoids, and which profits it keeps:** it avoids the one settled loss (`aec-atp-petbru-alecan`, −$1.9912, the `open YES 5.24@0.38` that drew 18 consecutive `deadline` unfills on its complement) and it keeps the one settled gain (`aec-atpcq-norgom-ollwal`, +$0.0082, including the `unlocked_by_rung_bound` completion at an open-lots basis of 1.0191). **In this sample B costs nothing and avoids everything.**

**Three honest limits on that result.** (i) **n = 3 markets, one settled loss, one settled gain** — not a population, and the +$1.9994 is one avoided event, not a rate. (ii) Per your instruction, B does **not** assume an available complement is purchasable profitably — it only requires that one was observably priceable in-bound *with depth* at entry; whether it then fills is unproven and B makes no claim about it. (iii) B's cost is **opportunity frequency**: it strictly reduces entries, and on the discovery-side reachability evidence (complement never reached in 96.8% of 1,956 sports cases, median gap 2¢, median favourable excursion 0.0¢) the reduction could be severe. **That trade-off is the whole question, and n=3 cannot settle it.**

Configuration C is worth running precisely because it is free: it changes no decision and simply records which extensions the caps reject and what their hypothetical economics would have been. Given `unmatched_usd_cap` produced **62 of 153 holds** and our engine extends 1.56% against the model's 48.64%, C measures the cost of the cap without risking anything.

---

# P2 — Audit of the economic evidence

## (a) The −$0.10 and +$0.04 are NOT verified as additive — I withdraw the −$0.06

`l5-pmus-live` reports `realized_attributed: -0.100000`, defined as *"settlement P&L from filled inventory in own-outcome terms."* The +$0.0400 comes from `WHY_THE_BOT_MISSES_MANUAL_FILLS.md`, described as *"the account's realized +$0.0400"* — an **account-level** figure.

Both are realized figures on what appears to be **the same PMUS account**, over different windows and different order origins. Summing them to −$0.06 assumed additivity that I did not verify. **The −$0.06 figure is withdrawn.**

What *is* attributable: the venue separates origins itself via `manualOrderIndicator` (64 MANUAL / 13 AUTOMATIC of 77 owned orders), so a clean split is obtainable — but it has not been done, and `realized_unattributed` is `0` with `cash_anchor: {}`, i.e. **no authoritative cash anchor is currently set**. Until one is, neither figure can be summed with the other.

The directionally safe statement, which survives the audit: **no lane in this repository has demonstrated verified positive realized net profit**, and the largest single settled trading outcome on record is the R4 residual at **−$1.9912**.

## (b) The action comparison does NOT compare against L5's observed decisions — material correction

R4 reports "expected L5" distributions on three **ATP tennis** markets. But the L5 wallet is **94.3% BTC 15m up/down with zero sports activity** (measured, round 1). **L5 never traded these markets.**

So R4's "expected L5" is the **fitted action model** (`analysis/l5/l5_action_model_v2.json`) evaluated on these states — not L5's behaviour on them. My `25f552c1` §2–§3 framing of "L5 extends 48.64%, our engine 1.56%" is therefore **fidelity-to-model on out-of-domain markets**, not an L5-versus-us comparison. The distinction matters twice over:

- **L5's historical behaviour**: Polymarket, BTC 15m, 100% BUY both outcomes, no sports, no SELLs.
- **Our own manual PMUS trading**: 64 venue-attested manual orders, **median `resting_s` 0.002 s**, 35 of 64 with non-zero commission — i.e. substantially **taker**, and across the entire record **11.98 paired shares, mean pair cost $0.9967, gross $0.0395**.

These are three different populations — L5's wallet, our manual trading, our engine's shadow decisions — and the R4 table compares the third against a model of the first, on markets belonging to none of them. Same markets and chronological states **within** R4 (decision indices 0..72 / 0..78 / 0..64, monotone, states recorded per decision), so R4 is internally consistent; the cross-population label was mine and is corrected.

## (c) The 38¢ residual is one observation

Reaffirmed: **n = 1**, from `aec-atp-petbru-alecan`. It is not a population average, and the 604-pair / 50× break-even arithmetic in `25f552c1` inherits that single-observation weakness. I am not proposing to collect 30 realized losses while disarmed, and shadow results stay hypothetical.

## (d) "One taker leg is always unprofitable" was WRONG — you were right

Computed exactly against PMUS `0.0695·p(1−p)` and the measured 0.330¢ mean pair edge:

| Price | Taker fee | vs 0.330¢ edge |
|---|---:|---|
| 0.50 | 1.738¢ | destroys |
| 0.80 | 1.112¢ | destroys |
| 0.90 | 0.625¢ | destroys |
| 0.95 | 0.330¢ | **breakeven** |
| 0.96 | 0.267¢ | **affordable** |
| 0.98 | 0.136¢ | **affordable** |
| 0.99 | 0.069¢ | **affordable** |

**Exact crossover: p = 0.0500 and p = 0.9500.** A taker completion is fee-affordable at **p ≤ 0.05 or p ≥ 0.95** — i.e. in the tails, where `P(1−P)` collapses.

This is not a footnote. It means a **taker completion is economically permissible in the tails**, where the live book currently sits: the 17:29Z snapshot showed `bid 0.9900 / ask 0.9900`. The `SAFETY_OVERLAY:MAKER_ONLY_NO_TAKER` overlay — which fired **2,738 times today** — is therefore blocking a class of completion that the fee maths permits at those prices, and it is blocking it in exactly the price region where the complement is otherwise unreachable. Whether such a completion is *profitable* also depends on the pair cost achieved and on depth; the fee is simply no longer the obstacle.

---

# Deliverable

## Smallest defensible correction that improves risk-adjusted expectancy without destroying opportunity frequency

**Not** configuration B on its own. B avoids the loss but strictly reduces entries, and on the reachability evidence that could cut frequency severely — which is the half of the objective you asked me not to destroy.

**The correction with both properties: permit a taker completion when `p ≤ 0.05 or p ≥ 0.95` and the resulting pair cost clears the existing ≥1¢ bound — and keep the maker-only overlay everywhere else.**

Why it satisfies both halves:
- **Risk-adjusted expectancy up:** it attacks residual *frequency*, which is the 38¢-per-event term, by giving the engine a way to actually close a leg instead of accumulating `deadline` unfills (41 of the 54 unfilled R4 orders). The −$1.9912 market had 18 consecutive complement deadlines and `within_old_bound_0_98 = 0`.
- **Opportunity frequency preserved:** unlike B it **rejects no entry**. It adds a completion route rather than removing an entry.
- **Fee-verified, not assumed:** §P2(d) gives the exact admissible price region, derived from the venue's own effective-dated schedule and the measured pair edge.
- **Minimal:** one price-conditional exception inside the existing safety overlay, gated by the existing ≥1¢ pair bound. No new service, model, collector or threshold fit.

Sequencing: **P0-fix first** (fail closed), then this, then B only if the residual evidence later says entry gating is also needed.

## GO / NO-GO for further shadow validation

**GO** — for shadow validation only, disarmed, on the two changes above, with configuration C's free extension-rejection ledger running alongside.

**NO-GO** stands unchanged for arming, capital, and any relaxation of risk limits.

## The single remaining blocker to a bounded live test

**The reconciliation gate failing open (§P0).** While `gross_net_agrees: false` can coexist with `blocks_live_authorization: false`, the preconditions you set — durable reconciliation and independent watchdog protection — are not met, because the watchdog cannot see the thing it exists to catch. Everything else on the path (preflight PASSED, $5 per-market cap, $5 total exposure, 1 concurrent market, $5 daily loss halt, maker-only overlay, `owner_arm_token: false`) is in place and functioning.

Second in line, not a blocker but required before a live test is *interpretable*: an authoritative cash anchor, so `realized_attributed` and `realized_unattributed` sum to something auditable (§P2(a)).
