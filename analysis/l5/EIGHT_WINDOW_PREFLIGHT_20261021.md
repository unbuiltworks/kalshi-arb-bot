# Eight-window live TWO_SIDED preflight — exact configuration

Date 2026-10-21. **NOT ACTIVATED.** Nothing in this document has been
applied. `L5_DEFENSIVE_COMPLETION` is absent from the production service, the
campaign approval is stale, and `PMUS_Q1_LIVE`/`PMUS_Q1_ARM` are as the owner
left them.

Service `l5-pmus-live` (`87f1834e-530e-4fc4-a310-91c8d3add196`), project
`50fb7357-e2bc-4d04-a49e-71eb5d73b0d1`, environment `production`
(`f3642d27-26dc-4e21-9170-bc76290c076f`). Watchdog
`fb038b8c-c7c1-4599-b06a-54f72bad544f` stays as it is — it cannot submit
orders and holds independent HALT authority.

## A. Variables to CHANGE

Only two, and one of them is new.

| variable | from | to | why |
|---|---|---|---|
| `L5_LIVE_AUTH_MAX_WINDOWS` | `4` | `8` | the window count the authorization permits |
| `L5_DEFENSIVE_COMPLETION` | *absent* | `1` | arms the defensive path. **Absent is OFF.** Only `1`/`yes`/`true`/`on` arm it; a typo cannot half-enable it |

`MAX_WINDOWS` in `src/l5_campaign_approval.py` is already `8` at the pushed
SHA, so the code ceiling no longer clamps the request to four.

**If you want the eight windows WITHOUT the defensive path,** leave
`L5_DEFENSIVE_COMPLETION` absent. Everything else below is unchanged and the
campaign behaves exactly as the four-window one did. The defensive path is a
separate decision from the window count and I have kept them separable on
purpose.

## B. Variables that must be CONFIRMED UNCHANGED

This connection returns variable names only — values are redacted — so I
cannot read these back and am not guessing at them. Each must hold the value
shown:

| variable | required value | what it holds |
|---|---|---|
| `L5_MAX_CASH_DEPLOYED_USD` | `5` | $5 aggregate exposure |
| `L5_MAX_L5_DAILY_LOSS_USD` | `5` | $5 daily realized-loss halt |
| `L5_MAX_LIVE_ORDERS` | `1` | one outstanding order |
| `L5_WINDOW_STRATEGY` | `TWO_SIDED` | strategy lock |
| `L5_LIVE_AUTH_MARKET_SERIES` | `cpc-btc-updown-15m-` | series allow-list |
| `L5_TS_CANCEL_MARGIN_S` | `300` | **sets the guard.** See the warning below |
| `L5_LIVE_AUTH_MAX_OPEN` | `8` | per-campaign OPEN budget |
| `L5_LIVE_AUTH_MAX_COMPLETE` | `12` | per-campaign COMPLETE budget |
| `L5_LIVE_AUTH_MAX_REPLACE` | `16` | per-campaign REPLACE budget |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | `28` | total submission backstop |
| `L5_SHADOW_ONLY` | `0` | must be 0 for live; **this is the live switch** |
| `L5_MAX_UNMATCHED_CONTRACTS` | as currently set | residual bound |
| `PMUS_Q1_LIVE` / `PMUS_Q1_ARM` | owner's | I do not read, generate or set these |

> **`L5_TS_CANCEL_MARGIN_S` is load-bearing for the guard.** The defensive
> window is the last 60 seconds before the *binding* terminal, which is
> `900 − L5_TS_CANCEL_MARGIN_S`. At `300` that is `[T+540, T+600)`, less the
> 15-second execution floor, so the effective action window is
> `[T+540, T+585)`. The code reads both clocks and takes the minimum, so the
> guard **moves with this variable** rather than breaking — but if you change
> it, the guard is no longer the interval you approved. Changing it from 300
> needs a fresh read of the arithmetic.

## B2. A consequence you should decide on, not one I should quietly fix

The class budgets in section B were approved for a **four**-window campaign.
Spread over eight they are materially tighter per window, and I am not
raising them:

| budget | total | per window over 8 | per window as approved for 4 |
|---|---|---|---|
| OPEN | 8 | 1.0 | 2.0 |
| COMPLETE | 12 | 1.5 | 3.0 |
| REPLACE | 16 | 2.0 | 4.0 |
| **total backstop** | **28** | **3.5** | **7.0** |

The 28 backstop binds before the class budgets do (8+12+16 = 36), so the
campaign's real limit is **3.5 submissions per window averaged over eight
windows**. A window needs at least one OPEN and one COMPLETE. Where a defensive order
is charged depends on whether a complement was already placed on that leg:
`acc_leg_attempt` grants attempt 0 for a leg's first placement and attempt
>= 1 after a venue-confirmed cancellation, and `submission_class` charges any
attempt > 0 to REPLACE. So a defensive unwind that **replaces a cancelled
complement** is REPLACE, and one on a leg **never placed** is COMPLETE (it
reduces |residual|). Neither is ever OPEN.

**What happens if it runs out:** the submission claim refuses and later
windows submit nothing. That is a safe failure — no order, no exposure — but
it means an eight-window campaign may in practice trade six or seven
windows. If you want eight windows that all trade, the backstop needs
raising, and that is your call and not something I will do on my own. The
alternative is to accept that the campaign stops when the budget is spent.

## C. Code constants — no variable, deliberately

These are not configurable at runtime, so they cannot be widened by an
environment change:

| constant | value | file |
|---|---|---|
| defensive loss budget | **$2.00** per campaign | `l5_defensive_completion.DEFENSIVE_LOSS_BUDGET_USD` |
| defensive pair ceiling | **1.20** (≤ $0.20 realized loss/share) | `...DEFENSIVE_PAIR_CEILING` |
| maker completion ceiling | **0.96** | `...MAKER_COMPLETE_CEILING` / `TS.max_pair_cost()` |
| guard length | 60 s | `...DEFENSIVE_ARM_S` |
| execution floor | 15 s | `...DEFENSIVE_MIN_EXECUTION_S` |
| campaign duration ceiling | 4 h | `l5_campaign_approval.MAX_CAMPAIGN_S` |
| exposure / daily-loss ceilings | $5 / $5 | `l5_campaign_approval` |

## D. The approval document — yours to issue, not mine

Written to `campaign_approval.json` in the campaign store. I do not generate,
read, print or transmit the nonce or the arm token. The document must carry:

```
approved                 true
nonce                    <owner's, never mine>
series                   cpc-btc-updown-15m-
issued_utc               <ISO 8601 UTC, within the last hour>
expires_utc              <ISO 8601 UTC, issued + up to 2h for eight windows>
max_windows              8
max_total_exposure_usd   5
max_daily_loss_usd       5
```

An approval is the **intersection** of what it asks and what the code
permits; it can never widen a ceiling. A document older than one hour is
stale even if its campaign has not expired.

## E. Preflight sequence, in order

1. set the two variables in section A;
2. confirm every value in section B (I cannot read them);
3. issue the approval document in section D;
4. touch `deploy/l5_pmus_live/DEPLOY_TRIGGER` — **the service watches only
   that path**, so a code push alone does not deploy and a deploy without it
   will report SKIPPED;
5. verify the deployment reports SUCCESS on the SHA in section F;
6. verify the campaign reads `approved` and the defensive flag reads enabled
   in the service's own telemetry;
7. **then** the campaign may be armed, on your explicit authorization.

## F. Deploy-ready SHA

See the covering message. The branch is
`claude/l5-defensive-8window-prod`.

## G. What is NOT in this configuration

* no new services, volumes or infrastructure;
* no change to the watchdog;
* no change to the HALT latch or any existing latch;
* no shadow observation campaign;
* no global taker permission — the maker-only overlay stands for every order
  and is relaxed only for one order a live defensive mandate names exactly;
* no automatic relaunch after a completed or crashed money-moving session.
