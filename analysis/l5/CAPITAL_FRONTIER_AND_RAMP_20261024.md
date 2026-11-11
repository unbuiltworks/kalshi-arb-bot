# Capital versus executable profit: the frontier, and why $100 is not spendable

**The frontier saturates at $4.44 of deployed capital per market. Beyond that,
additional cash buys nothing, because the binding constraint stops being money
and becomes the absolute 3-contract residual ceiling. The $100 budget cannot be
deployed by this engine as configured — not for want of authorization, but
arithmetically.**

All figures come from replaying the planner against the five real books at
submission from the preserved campaign tapes, at the venue's own grid
(`min_qty 0.01`, `quantity_increment 0.01`), with observed matched surplus of
**+0.0865/contract** and observed pair completion of **4 of 7 windows (57.1%)**.
Theoretical capacity and executable fills are kept separate throughout.

## 1. Maximum executable matched volume per window, by envelope

| cash envelope | cash-sized qty/leg | obligation | **what binds** | effective qty | matched $/window |
|---|---|---|---|---|---|
| $1.65 | 1.11 | 1.6421 | cash envelope | **1.11** | 0.0957 |
| $3 | 2.02 | 2.9932 | **$2 exposure ceiling** → 1.35 | **1.35** | 0.1169 |
| $5 | 3.36 | 4.9915 | **3-contract residual cap** | **3.00** | 0.2595 |
| $10 | 6.73 | 9.9950 | 3-contract residual cap | **3.00** | 0.2595 |
| $20 | 13.47 | 19.9929 | 3-contract residual cap | **3.00** | 0.2595 |

**Raising cash alone is nearly inert.** Above ~$2.4 of envelope the **$2
account-wide exposure ceiling** binds at 1.35 contracts, and once that is
lifted the **3-contract absolute residual ceiling** binds at 3. Cash and
exposure must move together, and exposure is the one that bites first.

### The frontier as a function of `min(cash, exposure)`

| min(cash, exposure) | qty | effective | matched $/win | × today |
|---|---|---|---|---|
| $1.65 | 1.11 | 1.11 | 0.0964 | 1.00× |
| $2 | 1.35 | 1.35 | 0.1169 | 1.21× |
| $3 | 2.03 | 2.03 | 0.1753 | 1.82× |
| **$4.44** | **3.00** | **3.00** | **0.2595** | **2.69×** |
| $5 | 3.38 | **3.00** | 0.2595 | 2.69× |
| $10 | 6.76 | **3.00** | 0.2595 | 2.69× |
| $20 | 13.51 | **3.00** | 0.2595 | 2.69× |
| $100 | 67.57 | **3.00** | 0.2595 | **2.69×** |

Saturation is exact: 3 contracts × $1.48 obligation per contract = **$4.44**.
From $4.44 to $100 the curve is **flat**. The remaining $95.56 of the owner's
budget is dead capital under the current residual ceiling.

## 2. Fill probability, residual exposure, settlement P&L at each stage

Scaling is linear in quantity and changes none of the per-contract economics:

* **Matched surplus** scales with contracts: 0.0964 → 0.2595 per completed
  window at saturation.
* **Residual exposure** scales identically. The campaign's residual losses were
  −$0.5909 over 3.24 contracts; at 2.7× clip the equivalent is about
  **−$1.60**, and a single adverse residual at 3 contracts costs up to
  **3 × 0.35 = $1.05**.
* **Fill probability is unchanged by size** — see §4.
* **Settlement P&L** therefore scales 2.7× in both directions, with the
  residual component still market-priced (break-even equals its own cost) and
  its expectancy still **unmeasured** (§4).

**A consequence of scaling that must be stated: the $5 daily halt tightens
relative to position size.** At 3 contracts, roughly **five** adverse
residuals reach the halt where today it would take about fourteen. Keeping the
halt at $5 while tripling clip size means it will trigger materially more
often. That is a safety improvement, not a defect, but it will be visible and
should not be mistaken for a malfunction.

## 3. More than one cycle per window, without reusing settled capital

**Mechanically yes, economically no.** `da0ca5ac` implements it and the replay
is decisive: two half-size rounds deliver **6.54 contracts against 6.57**
(0.995×, *fewer*), because the venue grid floors twice instead of once. Pair
surplus is edge × contracts, so expected surplus per window does not rise.
Splitting the envelope is variance reduction at a 0.5% capacity cost. It is
**not** a route to higher realized profit and is correctly left undeployed.

## 4. How size affects queue, execution quality, adverse selection

**Queue: no material effect in the range the risk caps permit.** Observed
`level_depth` ran 346.5–3553 (median 750) and `queue_ahead` 760–8149
(median 5477), across 11 live orders.

| our clip | share of median level | share of the thinnest level |
|---|---|---|
| 1.11 (today) | 0.15% | 0.32% |
| 3.00 (saturation) | **0.40%** | **0.87%** |
| 6.73 | 0.90% | 1.94% |
| 67.57 ($100) | **8.80%** | **19.05%** |

At saturation we remain **under 1% of even the thinnest observed level**, so
queue position is set by other participants, not by our size. Conveniently,
the residual ceiling stops us exactly where we are still queue-invisible. At
the $100 clip we would be a fifth of a thin level, which is where queue and
market-impact effects would begin — another reason that capital is not usable.

**Adverse selection: still unmeasured, and I will not estimate it.** Across
all seven filled first legs the observed win rate was 2/7 = 28.6% against
33.3% expected from entry prices — **−4.7 pp, in the adverse direction, with a
standard error of 17.8 pp**. Detecting a 5 pp effect at 80% power needs about
**1,393 fills**. We have seven. Size cannot be assumed to worsen it and cannot
be assumed not to.

## 5. The smallest capital increase that meaningfully improves expected net

**Raise the cash envelope AND the account-wide exposure ceiling to $5 each,
leaving the 3-contract residual ceiling where it is.** That reaches saturation
(3 contracts), delivers the full **2.69×** capacity gain, and deploys **$5 of
the $100**. Raising cash without exposure gains only 1.21×; raising either
beyond $5 gains nothing.

## Recommended ramp — and it is shorter than the proposed one

The proposed stages were $5, $10, $20, $50, $100. On this evidence **only the
first is reachable by capital alone.** $10 and beyond require a *risk-limit*
change — either the residual ceiling above 3 contracts or concurrency above one
market — and each is a separate authorization needing its own evidence. I am
not proposing them here.

**Stage 1, the only stage this evidence supports:**

| parameter | current | Stage 1 |
|---|---|---|
| cash envelope | $1.65 | **$5.00** |
| account-wide exposure ceiling | $2 | **$5.00** |
| absolute residual ceiling | 3 contracts | **3 (unchanged)** |
| `max_live_orders` | 1 | **1 (unchanged)** |
| concurrent markets | 1 | **1 (unchanged)** |
| pair cap | 0.96 | **0.96 (unchanged)** |
| execution | maker-only, post-only | **unchanged** |
| $5 daily / $20 cumulative halts | active | **unchanged** |
| round divisor | 1 | **1 (unchanged, stays undeployed)** |

Capital at risk per window is bounded by the $5 envelope; worst single-leg loss
at 3 contracts is about **$1.05**, and the $5 daily halt stops the day after
roughly five of those.

### Promotion, demotion, halt

**Promote** from Stage 1 only on all of: at least 16 settled windows; matched
surplus per completed window at or above **0.20** (against the 0.2595
capacity figure); residual settlement loss per window no worse than **2.7×**
the campaign's per-window figure; zero unresolved order states; zero
accounting discrepancies; and a venue-confirmed reconciliation of every
settlement. Promotion beyond Stage 1 **requires a separate risk-limit
authorization**, since capital alone cannot advance it.

**Demote** to the $1.65/$2 configuration automatically on any of: cumulative
realized loss worse than −$2 inside a stage; two consecutive windows with an
unresolved order state; any `journal_errors > 0`; any reconciliation
discrepancy; or any refusal the engine cannot name.

**Halt** on the existing gates unchanged — $5 daily, $20 cumulative,
`ORDER_STATE_UNCERTAIN`, `CAMPAIGN_WINDOWS_EXHAUSTED`, authorization expiry,
watchdog — plus the demotion triggers above.

### Pre-arming verification, to be evidenced from the running process

Account-wide exposure aggregation across outstanding orders and filled
inventory; zero working or uncertain orders; durable authorization ledger;
watchdog healthy; `L5_BUDGET_SELFCHECK all_passed`; `journal_errors: 0`; and
`incremental_room_usd` reported — the last two being observable for the first
time, since they ride `L5_LIVE_GATES` which only emits once an executor is
built.

## Rebates, kept separate

**$0.0337 estimated across 11 orders, zero credited, 11 of 11 still
`venue_actual_status: PENDING`.** Not realized, not counted, and no further
effort spent pending eligibility confirmation and an account credit record.

Nothing in this document is deployed or authorized.
