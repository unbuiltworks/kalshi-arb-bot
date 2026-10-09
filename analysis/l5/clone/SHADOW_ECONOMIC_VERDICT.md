# One L5 system, running end to end — the economic verdict

**Service** `bc7a1f5b-f059-4937-8486-02d68dcb3bf9` (display name still
`r5ft-clean-2ebb50f-buildcheck`), project `arb-ev-wallet-shadow`, production,
commit `ee96c4015`, branch `claude/august-l5-forensic-replay-788cml`.
**1,493 cycles, continuous since 02:23Z.** Read-only, no credentials, E1
disarmed. Every figure below is SHADOW; none is realized profit.

## 1. Is the complete system running?

Yes, as one pipeline in one service: discovery → clock resolution → book
capture → both regime policies on the same state → inventory → queue-gated
feasibility → markouts → completion rule → settlement attempt. 23,183
markets served per sweep, 336 clock-resolvable, ~5 serving a live book at
any instant. State survives redeploys (resumed at cycle 1294, then 1492).

## 2. Is it making L5-style decisions?

Yes, and the decomposition is explicit on every row:

| action source | early | late |
|---|---|---|
| entry rule (ours — the clone cannot enter) | 38 | 38 |
| `CLONE_EXTEND` (the trained model's argmax) | 4 | 20 |
| `CLONE_COMPLEMENT` | 0 | 0 |
| completion rule (ours — risk, not prediction) | fires, refuses on the ceiling | same |

The sequence is L5-shaped — OPEN, same-side EXTEND, patient complete — and
the late regime is measurably more aggressive (20 extends vs 4) on identical
observations, which matches the regime difference in the corpus. But the
clone is doing only the EXTEND part: it never entered (out of domain with no
prior fill) and never chose COMPLEMENT, because the trained argmax is EXTEND
~70% of the time by construction.

## 3. Are those decisions economically promising after realistic costs?

**No. Measured, on this venue, the pair never clears.**

The decisive number is the pair cost actually *available at the moment we
hold a residual* — the residual's own cost basis plus the complement's
own-side maker price:

| | value |
|---|---|
| pair cost available (min / median / max) | **1.08 / 1.08–1.12 / 1.12** |
| fraction at or below the 0.96 ceiling | **0.0** |
| shortfall when refused | **+0.12 to +0.16** |

A matched pair pays exactly $1.00. A pair that costs $1.08–1.12 to assemble
is a guaranteed 8–12¢/share loss before fees, not a thin edge. The 0.96
spring ceiling is not missing by a tick; it is missing by a dime.

This is consistent with, and explains, the two other measurements:

- **Queue-cleared feasibility 36–45%** (early 17/47, late 29/66). Fills are
  obtainable. Getting filled is not the problem.
- **At entry, the hypothetical pair looked completable** ≤0.96 in 57–74% of
  candidates 30 s later, and the "pair edge if completed maker" read
  +1.9¢ to +2.7¢/share. That number is computed from the *candidate's* price
  against a *hypothetical* complement. Once the leg is actually held, the
  same book offers 1.08–1.12. The gap between those two figures is the
  measurement of adverse selection, in pair-cost units: **the quotes that
  get filled are the ones where the other side has already moved away.**

Mechanically: a maker pair cost is `1 − spread`, and PMUS crypto up/down
quotes 1–2¢ wide, so even a perfectly simultaneous two-sided maker fill
costs ~0.98–0.99 — above the 0.96 ceiling, and only ~1¢ gross before
adverse selection and residual drag. There is no configuration of this
strategy on this market that produces a defensible positive expectancy.

## 4. What exactly prevents confirmed realized profit?

In order, most binding first:

1. **The pair cannot be assembled below $1.00 on PMUS BTC up/down.**
   Measured 1.08–1.12 when holding, 0% at or below ceiling. This is
   economic, not technical, and it is the reason to not run a live probe
   here.
2. **Adverse selection on the fills we would get.** 36–45% of quotes clear
   the queue, but the ones that do are the ones the market has moved
   through, which is why the available complement is 12–16¢ beyond budget.
3. **Settlement of residuals is not obtainable from public PMUS data.** All
   three closed-market query forms sweep 2,499–4,000 rows and find **0 of
   12** held slugs: expired crypto windows disappear from the public list,
   and `/v1/markets/{slug}` is HTTP 404. So a shadow cannot book a
   venue-confirmed settled P&L at all. The only resolution source is the
   authenticated position-resolution activity feed, which covers our own
   positions only — i.e. confirmed realized P&L on this market requires
   actually trading it.
4. **Opportunity frequency is thin.** ~5 live books at a time out of 336
   clock-resolvable windows; the rest are future windows or
   far-out-of-the-money hourly strikes with zero bids
   (`cpc-btc-above-hr-...-90800`: 0 bid levels, one 1¢ offer). 8.5 hours
   produced 47 early / 66 late candidates.
5. **The clone still cannot place orders.** 100% of entries come from our
   hand-written rule; order placement is unidentifiable from public fills
   (`L5_BEHAVIORAL_CLONE_FINDINGS.md` §5) and no amount of shadow time
   changes that.

## Verdict: NO-GO for a bounded live probe on PMUS BTC up/down

Not on sizing grounds — on the arithmetic. The executor is ready and stays
disarmed. Preparing it for live validation here would be funding a measured
negative edge.

## Next best evidence-supported market — not a new strategy

The same L5 sequence, unchanged, against the **PMUS sports slate**, which
already has a running read-only shadow (`pmus-shadow-breadth`) with
measured reachability data. The reason is in the measurements above: this
strategy dies on a 1–2¢ spread, because a maker pair cost is `1 − spread`.
Sports markets on this venue quote materially wider, which is the only
condition under which a sub-$1 passive pair is reachable at all. Evidence
to gather there first, before any live discussion:

1. the same `pair_cost_available` distribution when holding a residual;
2. queue-cleared feasibility on those books;
3. whether settlement is readable from the public surface (sports markets
   stay in the list far longer than 15-minute crypto windows).

Items 1 and 2 are the same code: the clone shadow is market-agnostic and
needs only its clock resolver extended to a sports window, which is exactly
the thing `src/pmus_market_clock.py` currently refuses to guess at
(`UNRESOLVED` for `gameStartTime` + 14-day contract expiry). That is the
next piece of work, and it is a resolver, not another engine.

The historical comparison stands as the reason this was worth testing:
L5's own habitat showed pair costs below $1.00 in **77.9%** of early-regime
two-sided events (`L5_ECONOMIC_DECISION_20261009.md`). PMUS BTC up/down, on
measured live data, shows **0%**. The behavioral model transferred; the
market did not.
