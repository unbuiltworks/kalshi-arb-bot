# Is there an executable BTC15m maker strategy with positive net?

**NOT ESTABLISHED. The bounds bracket zero, and the one number that decides
the sign is absent from the records — not because it is unobservable, but
because the pricing step was never run.**

READ-ONLY. No order, deployment, service or limit. System shadow-locked
throughout.

## 1. Fee cross-tab, BTC15m-lane only (no sports transfer)

From the live `L5_COST_BASIS_AUDIT` legs, grouped by lane and fee sign.
Notional is the venue's measured acquisition cost.

| lane | fee sign | legs | fills | qty | notional | fees | effective |
|---|---|---|---|---|---|---|---|
| **btc15m** | charged | 10 | 11 | 51.69 | $12.1075 | $0.3600 | **2.9734%** |
| **btc15m** | rebate | 1 | 1 | 5.00 | $0.6500 | −$0.0100 | **−1.5385%** |
| **btc15m** | zero | 5 | 9 | 7.90 | $1.4500 | $0.0000 | 0% |
| btc15m | TOTAL | 16 | 21 | 64.59 | $14.2075 | $0.3500 | 2.4635% |
| eth15m | TOTAL | 11 | 13 | 85.96 | $11.0545 | $0.2800 | 2.5329% |
| sol15m | TOTAL | 11 | 18 | 71.86 | $12.1230 | $0.2500 | 2.0622% |
| xrp15m | TOTAL | 9 | 17 | 68.64 | $13.2298 | $0.2600 | 1.9653% |

**BTC15m maker (zero + rebate), measured, lane-only: 6 legs, 10 fills,
$2.1000 notional, −$0.0100 → −0.4762% (a small net CREDIT).**
**BTC15m taker: 2.9734% of notional.** By notional only **14.8%** of
btc15m flow was fee-free; 85.2% was charged.

**Sports maker, reported SEPARATELY and NOT transferred:** 25 executions in
`venue_rebate_credits.json`, every one `our_side: passiveExecution` with a
negative commission, −$0.42 over 119.49 shares. That artifact carries no
price, so a sports rate per notional is **not computable** and is not used
anywhere below.

**Caveat on the classifier.** The maker/taker split above is by FEE SIGN,
which is weaker than the venue's own flag. `fee_crosstab` (added this
commit) groups by `isAggressor` per lane and emits on the audit's next
boot; the audit reports `our_side_selectable_via_isAggressor: 1911`, so the
authoritative split is available and simply has not run yet.

## 2. Passive-order evidence, 97 persisted BTC15m windows

`pmus-ws-evidence-recorder` is live and emits `PMUS_SHADOW_POLICY` every 15
minutes to `/state/pmus-ws/l5_shadow_policy.jsonl` (`store_errors: 0`). It
also tapes authenticated book, ladder (4 rungs/side) and trades with taker
direction — the inputs a counterfactual passive fill needs, with no order.

97 distinct windows, 2026-10-17T21:45Z to 2026-10-18T21:45Z:

- **outcome: BOTH 66, DOWN_ONLY 14, UP_ONLY 11, NEITHER 1**, null 5
- **selected: HOLD 71, CROSS 21** — the policy chose to pay taker in 21
- `reason`: NOT_ELIGIBLE 4, MISSED_WINDOW_OPEN 1

**This supersedes the 3-window committed sample** (`policy_eval.jsonl`,
3/3 BOTH) that I cited earlier. It was not representative: **26 of 97
windows end one-sided with matched 0**, and only **10 of 97** end with
matched > 0 and zero residual.

| variant | matched | residual | res/matched | wc residual loss | max committed |
|---|---|---|---|---|---|
| B10 | 370 | 470 | 1.270 | $194.37 | $10.00 |
| **B25** | 900 | 700 | **0.778** | $300.88 | $25.00 |
| B50 | 1360 | 815 | 0.599 | $349.45 | $49.95 |
| B25D2 | 830 | 770 | 0.928 | $322.68 | $25.00 |
| B25D3 | 770 | 755 | 0.981 | $309.39 | $25.00 |

Queue is deep against our clip: the live 2200z book carried 5,551–8,821
shares at the bid against a 1.25–1.43 share clip, and the shadow's
`queue_ahead` ran 511–3,419 shares. Repricing never fires: the committed
sample shows `REPRICE:HOLD_PRIORITY` on all 844 decisions.

## 3. The answer, and why it is not yet signed

Maker gain per matched pair at the deployed `<= 0.96` discipline, using the
BTC15m-lane-only measured maker rate: `$0.04 + 0.4762% x 0.96 = $0.04457`.

| variant | matched gain/win | **BEST bound** | **WORST bound** |
|---|---|---|---|
| B10 | $0.17002 | **+$0.17002** | **−$1.83376** |
| **B25** | $0.41355 | **+$0.41355** | **−$2.68826** |
| B50 | $0.62492 | **+$0.62492** | **−$2.97768** |

BEST assumes the modelled residual is EV-neutral; WORST assumes it settles
worthless. **Every variant brackets zero.** The residual's worst case is
7.5x the matched gain (B25: $3.10 vs $0.41 per window), so the sign is
decided entirely by realized residual settlement — and
`net_usd: null, priced: false` on every variant of all 97 records.

What the strategy can tolerate, and what L5 actually showed:

| variant | drag tolerance | net @ L5's measured 2.2773% | $ committed for $5/win |
|---|---|---|---|
| B10 | 8.48% | +$0.12438 | $401.99 |
| **B25** | **13.33%** | **+$0.34291** | **$364.53** |
| **B50** | **17.35%** | **+$0.54288** | **$460.05** |
| B25D2 | 11.46% | +$0.30563 | $408.99 |
| B25D3 | 11.09% | +$0.28118 | $444.56 |

**L5's 2.2773% sits well inside every tolerance**, so IF our residual
behaves like his the strategy is positive — at **+$0.34 to +$0.54 per
window, 9x to 15x short of the $5 objective.** His drag is a borrowed
constant, measured on his wallet, not ours. It is the assumption the whole
verdict rests on.

**Capital for $5/window: $364–$460 of committed capital per window.** That
exceeds the $344.41 account and requires full redeployment every window,
which `NO_RECYCLING` forbids. Against the authorized $4.65 cap the ceiling
is ~$0.06–0.10/window.

## 4. Minimum missing measurement

**The realized settlement of each window's modelled residual.** Not more
collection — the 97 windows already hold `residual_qty`,
`max_committed_capital_usd` and `worst_case_residual_loss_usd`, and each
window's settlement outcome is public and already observed by the
recorder. `net_usd` is null only because the pricing step was never run.

**How the existing shadow collects it with no orders:** it already has.
The records are persisted and accumulate at ~96 windows/day. Pricing them
is pure arithmetic over data on disk: settle each window's residual at that
window's actual outcome, charge the identified fee model (taker
`round_half_up(0.0695 q p (1-p))` where the policy `selected: CROSS`, the
measured maker rate where it held), and read off our OWN adverse-selection
drag instead of borrowing L5's.

At 97 windows a signed mean is already estimable; the drag tolerances above
(8.5–17.4%) are wide relative to plausible drag, so this sample can
plausibly separate positive from negative rather than merely narrow a CI.

## 5. Single highest-impact next action

**Price the 97 persisted shadow windows.** One read-only computation over
`/state/pmus-ws/l5_shadow_policy.jsonl` that fills in `net_usd` by settling
each modelled residual at its window's actual outcome under the identified
fee model, and reports our own residual drag.

It needs no order, no new service, no deployment and no new collection. It
converts bounds that bracket zero into a signed expectancy on ~100 windows,
and it replaces the one borrowed constant (L5's 2.2773%) with a measured
one. Every other question here — capital, clip size, depth variant, maker
share — is a sensitivity around that single number.
