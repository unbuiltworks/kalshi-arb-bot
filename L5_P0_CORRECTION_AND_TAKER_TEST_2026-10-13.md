# L5 — P0 Correction, Taker-Completion Test, P2 Scope

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · follows `d48e4386`
**Mode:** read-only. No code shipped, no branch but this one written, no orders, arming, deployments, restarts, services, spend. No ledger evidence deleted or rewritten.

---

# Headline: I did not implement the P0 fix, because my P0 premise was wrong

**The reconciliation gate does not fail open.** Arming is already blocked, durably, by a different mechanism than the one I examined. Shipping the fix as scoped would have re-introduced a failure mode the authors specifically documented and defended against.

And **my own proposed taker-completion exception has zero qualifying opportunities** in all existing evidence. It is withdrawn.

---

# P0 — Correction, with the code path

## What I got wrong in `d48e4386`

I traced `blocks_live_authorization` → `bool(unresolved_accounting())` → a curated static list, concluded the runtime discrepancy could never block arming, and called it a fail-open gate. **The first two steps are correct; the conclusion is not.** `blocks_live_authorization` is *one* blocker, not *the* blocker.

## The mechanism that actually blocks, and it is durable

In the deployed tree (`b6e292a2`, branch `claude/gemini-btc-economic-viability-h3u7zl` — what `l5-pmus-live` runs):

```
src/l5_live_execution.py:2033   disputed = sorted(m for m, ok in agrees.items()
                                                  if not ok and m in net)
src/l5_live_execution.py:2208   if disputed or incoherent or pending or quarantine:
                     :2209          self.state["orders_uncertain"] = (
                                        "gross/net divergence %s; ...")
                     :2215      self.save()                 # <- persisted to the state file
src/l5_pmus_live_runner.py:733  if self.store.orders_uncertain():
                     :734           self.win["arm_refused_orders_uncertain"] += 1
```

So a gross/net divergence **is** written to `state["orders_uncertain"]`, **is** persisted by `save()` (surviving restarts — not a module-level list), and **does** refuse arming. `SafetyLimits.refuse()` also reads it (`:367`, `:376`), and the runner re-checks it on every pass (`:1702`).

The authors state the division of labour explicitly at `l5_pmus_live_runner.py:700-703`:

> *"UNRESOLVED ACCOUNTING. `orders_uncertain` already refuses while the … refusal. It exists for the case `orders_uncertain` cannot cover."*

So the curated registry is the **complement** to the runtime blocker, deliberately. Four of your seven P0 requirements — persist across restarts (1), block genuine discrepancies (4), watchdog/preflight share the condition (5), preserve evidence (7) — are **already met by existing code**.

## Why 0030z did not trip it — deliberate, documented, and correct

`disputed` requires `m in net`. For 0030z the venue reported **nothing** (`venue_net: {}`), so it is excluded. The comment at `:2026-2032`:

> *"ONLY a market the venue actually reports can contradict us. A market it says nothing about is the ordinary post-settlement state — the venue drops a position the moment the market expires, while our book legitimately still holds it until `retire_settled_market` books the redemption. **Treating that silence as divergence would mark the book uncertain after every single settled window.**"*

That is exactly requirement (2) — open-position drift distinguished from missing settled cashflow — already implemented. **Had I shipped a fix that registered every `gross_detail_lost` market as unresolved accounting, I would have blocked arming after every settled window.** The design is right and my proposed correction was wrong.

## Is 0030z explained and reconciled? Explained in mechanism — NOT reconciled

The *gate* behaviour is fully explained. The *position* is not, and one detail is genuinely inconsistent:

- `retired_markets` in the 17:29Z heartbeat **includes** `cpc-btc-updown-15m-2026-10-12-0030z`.
- `retire_settled_market` pops the market's position keys and is once-only and persisted.
- Yet the drift key is `…0030z|DOWN` — a `|DOWN` key, which retirement *does* pop — and `gross_before` still shows `dn 2, residual −2`.

A market cannot be both retired and still holding a `|DOWN` leg. Two readings remain, and the codebase shows precedent for the second: the comment at `:1874-1882` records that 2115z *"re-emitted L5_LIVE_SETTLED every ~3 seconds, 40+ times, because retirement popped only the `|UP` and `|DOWN` keys while the market's real inventory sat under `|ORDER_SIDE_SELL`."*

1. **Benign:** `gross_before` is a pre-retirement snapshot and the book is now clean.
2. **Retirement residue:** retired, but a position key survived — the documented failure class.

**The distinguishing evidence is the live state file** (`settled_markets` and `positions` for that slug) **plus an authoritative `/v1/order` read.** Both live on the service's volume and behind account credentials. I did not obtain them: requirement (3) asks for the authenticated read-only path, which belongs to the live service and the replication session's lane, not to this session.

**So: 0030z is NOT reconciled.** No live exposure either way — the venue says flat — but the position is unexplained.

## The genuine residual gap — narrow, and worth fixing

Not a fail-open gate. A **missing staleness bound**:

> Venue silence is correctly ignored *as divergence*. But nothing bounds **how long** a settled market may sit with `gross_net_agrees: false` before its unbooked redemption becomes an unresolved-accounting matter. If `retire_settled_market` never runs for a market — or runs and leaves residue — the book carries a stale position indefinitely and nothing escalates.

That is your requirement (4)'s "stale reconciliation evidence" clause, and it is the one part not already covered. The smallest durable correction: **escalate to `orders_uncertain` when a market has non-agreeing gross with venue silence AND is past expiry by more than a bounded interval, or appears in `retired_markets` while still holding any position key.** Both conditions are computable from state already present, both persist through the existing `save()`, and neither treats ordinary post-settlement silence as divergence.

## Why I shipped no code

Three reasons, in order of weight:

1. **The premise was wrong.** Shipping the originally-scoped fix would have degraded a working design. The narrow staleness bound above is worth implementing — but it is a different, smaller change than the one directed, and it should be specified before it is written.
2. **No replication session is running to coordinate with.** `ListAgents` reports no other Claude session on this machine. Your directive presupposes an active session to deconflict against; there is none, so "do not create a competing implementation" cannot be satisfied by coordination, only by not implementing.
3. **Pushing to that branch would likely redeploy a live service.** `l5-pmus-live` deploys from `claude/gemini-btc-economic-viability-h3u7zl`. A push to it would very likely trigger a Railway redeploy, restarting a running executor that holds the exact reconciliation state under investigation — and possibly destroying the evidence that distinguishes reading 1 from reading 2 above. That is a deployment, which you excluded.

**No regression tests were run**, because no code was written. I installed `pytest 9.1.1` in this container and checked out the deployed tree into a scratch worktree to read it; the worktree has been removed and nothing was modified.

---

# P1 — Taker-assisted completion: tested, and it is dead

Evaluated against frozen observations, per your constraints. **The answer to "the number of genuinely fee-positive taker-completion opportunities in existing evidence" is zero.**

| Evidence set | First-leg prices | Complement prices | Complements in fee-affordable tail (p≤0.05 or p≥0.95) | First legs ≥0.94 | Qualifying |
|---|---|---|---:|---:|---:|
| R4 ATP sports (5 + 5 fills) | 0.38 – 0.51 | 0.22 – 0.51 | **0** | **0** | **0** |
| Manual BTC15m 1430z maker sequence (venue-attested) | 0.79 – 0.81 | 0.16 – 0.22 | **0** | **0** | **0** |

**The structural reason, which kills the idea rather than merely failing to support it:** the fee is small in the tails, but the **price** is what binds. A cheap tail complement (≤0.05) is only reachable when the first leg sits at ≥0.95 — which never occurred. Every observed first leg is 0.38–0.81, so its complement must cost ~0.19–0.61 to clear the pair bound, and that is precisely the region where the taker fee (0.45–1.74¢) exceeds the measured 0.330¢ pair edge.

I proposed this exception in `d48e4386` as the smallest correction with both properties. **It is withdrawn.** The fee-crossover arithmetic was right; its applicability was not, and I should have checked the inventory distribution before recommending it.

On the per-rejection detail you asked for (complement outcome, executable taker price, depth, quote freshness, execution probability, resultant paired P&L, residual reduction): with the qualifying set empty, these are vacuous for the tail exception. They remain the right schema for evaluating complement-priceability entry gating (configuration B), which is the one candidate still standing — and which, per `d48e4386` §P1, improves the R4 settled set from −$1.9912 to +$0.0082 on **n=3 markets** at the cost of rejecting entries. **No production entry/exit behaviour changed.**

---

# P2 — Accounting and fidelity

**Cash anchor: not obtained, and not obtainable from this session.** `cash_anchor` is `{}` and `realized_unattributed` is `0`, so no authoritative anchor is set. Establishing one requires an authenticated `/v1/account/balances` read against two flat states, which is the live service's credentialed path. **The −$0.10 and +$0.04 remain unsummed**, as instructed, and the −$0.06 figure stays withdrawn.

**ATP R4 is not presented as L5 fidelity.** Restating for the record: L5 is 94.3% BTC 15m with **zero sports activity** and never traded those ATP markets, so R4's "expected L5" is the fitted `l5_action_model_v2` evaluated out-of-domain. The direct L5-versus-engine comparison must use **actual L5 BTC15m decision sequences**, which exist — `l5-ops-data`, `data/l5/ops/activity_*.jsonl`, 42 days, ~450k BTC 15m trades with timestamps, prices, sizes and outcomes. That comparison has not been run. It is the replication session's core deliverable and I am not duplicating it.

---

# Decision and answers

**GO** — disarmed shadow validation only, unchanged. Nothing here moves toward live authorization.

**NO-GO** — unchanged for arming, capital, limit relaxation. Of your five live-authorization preconditions: durable reconciliation **is** largely in place (contrary to my last report), independent watchdog enforcement **is** in place, a verified cash anchor is **not**, executable positive-expectancy evidence is **not**, owner approval is **not** given.

| Your question | Answer |
|---|---|
| Exact P0 correction and regression-test results | **No code shipped; no tests run.** The directed correction was premised on a defect that does not exist. The genuine gap is a missing staleness bound (§P0), specified but not implemented. |
| Is 0030z explained and reconciled? | **Explained, not reconciled.** The gate behaviour is fully explained and correct. The position is inconsistent — retired yet holding a `\|DOWN` key — and distinguishing benign snapshot from retirement residue needs the live state file plus an authoritative order read. |
| Fee-positive taker-completion opportunities in existing evidence | **Zero**, across both evidence sets, for a structural reason. Exception withdrawn. |
| Single biggest remaining blocker to demonstrated net-positive trading | **No measured positive expectancy exists on any lawful venue.** It is no longer a safety-gate problem — the gates largely work. The largest settled trading outcome on record is **−$1.9912**; the entire venue-attested paired record is 11.98 shares at a $0.9967 mean pair cost (0.330¢/pair). The binding question is whether complement-priceability entry gating preserves enough opportunity frequency to matter, and `n = 3` cannot answer it. |

**What I would do next, for your decision rather than my initiative:** have the replication session run the three-configuration replay (A / B / C) on the frozen BTC15m observations with the L5 wallet's actual decision sequences, and read the live state file for 0030z. Both are in their lane, both are read-only, and the second is the only thing that closes the one genuine integrity question outstanding.
