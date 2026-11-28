# L5 — opening-notional cap: shadow evidence and live-authorization request

Commit **`1752e51b`** · deploy **`21d2f2b7`** (SUCCESS 2026-10-25T12:01:30Z)
Service `l5-pmus-live`. **Shadow only. Not armed. No live trading enabled.**

---

## 1. Verifier — from the running production process

`L5_EFFECTIVE_RUNTIME_CONFIG` at 12:01:33Z: **`PASS: true`, `checked: 26`,
`failed: []`.** Every row is the value the money path's own accessor returns,
not the text of a variable.

| row | effective | required |
|---|---|---|
| **`opening_notional_budget_usd`** | **`0.80`** | `0.80` |
| `aggregate_exposure_usd` (variable) | `5` | `5` |
| `aggregate_exposure_in_force_usd` (frozen at boot) | `5` | `5` |
| `campaign_additional_loss_usd` | `5.00` | `5.00` |
| `daily_realized_loss_halt_usd` | `5` | `5` |
| `maker_pair_ceiling` | `0.96` | `0.96` |
| `max_live_orders` | `1` | `1` |
| `maker_only_forbids_defensive_crossing` | `true` | `true` |
| `mandate_signing_key_absent_from_runtime` | `true` | `true` |

The two exposure rows agree, which is the "variable changed, service not
restarted" check passing.

Reported, not graded: **`shadow_only: true`**, `defensive_execution_enabled:
false`, `experiment_id: l5-ts-nokey-20261025T0000Z` (**unchanged** — see §6),
`ts_opening_leg_max_contracts: 2`, `ts_round_allocations: 2`,
`max_worst_case_deficit_usd: 2.00`.

## 2. Shadow evidence that the cap is actually enforced

**(a) In force in the process.** The verifier row above reads
`TS.opening_notional_budget_usd()` — the same accessor `build_orders` calls —
and gets `0.80`.

**(b) Binding on the real live book.** The engine evaluated the **1200z**
window at 12:05:54Z and logged an authentic book: `bid 0.2000 / ask 0.2100`,
`bid_shares 2768`, `ask_shares 4`, `stream_age_s 0.077`. Running the deployed
`build_orders` against that exact book with production's effective config:

| | legs (own) | pair | qty/leg | `sized_by` | worst single-leg notional |
|---|---|---|---|---|---|
| uncapped | UP 0.18, DOWN 0.77 | 0.9500 | **2** | `OPENING_LEG_CONTRACT_CAP` | **$1.5400** |
| **capped $0.80** | UP 0.18, DOWN 0.77 | 0.9500 | **1.03** | **`OPENING_NOTIONAL_CAP`** | **$0.7931** |

Same prices, same pair cost, **48% less single-leg strand risk.** Note the
uncapped figure: **$1.54 on the first live window after deploy, larger than the
$1.1594 that actually lost money in 0130z.** The exposure this bounds is
recurring, not a one-off.

**(c) What is NOT yet demonstrated, stated plainly.** The cap binding on an
engine-built plan end-to-end has not been observed, because the engine refuses
*upstream* of sizing with
`TWO_SIDED_PROBE:PAIR_UP:CAMPAIGN_IDENTITY_UNAVAILABLE` — the campaign
approval expired at 04:00Z. Forcing that observation would require writing a
fresh `L5_CAMPAIGN_APPROVAL`, which is an owner-only arm variable, so I did not.
**(b) is a faithful replay of the deployed code against the real book, not an
end-to-end shadow fill.**

## 3. Replay — capped versus uncapped, implementation-faithful

Grid-floored through the real sizing path, not linearly scaled:

| budget | net | vs baseline | completion rate | worst 1-leg notional |
|---|---|---|---|---|
| uncapped | +$0.1256 | — | 87.5% (7/8) | **$1.5200** |
| $0.60 | +$0.3052 | +$0.1796 | 87.5% (7/8) | $0.6000 |
| **$0.80** | **+$0.3863** | **+$0.2607** | **87.5% (7/8)** | **$0.8000** |
| $1.00 | +$0.2592 | +$0.1336 | 87.5% (7/8) | $1.0000 |
| $1.20 | +$0.1084 | **−$0.0172** | 87.5% (7/8) | $1.1594 |

$0.80 is the optimum; $1.20 is already worse than doing nothing, which is why
not looser. **Completion rate is unchanged at every budget** — all eight
episodes still open and all seven completions survive.

**Fees: no change.** Maker fee is zero on the published schedule, no rebate was
ever credited, and no taker order exists. A smaller clip changes neither.

**0145z, the mandatory counterexample:** retained, 1.56 → 1.53 contracts,
+$0.2028 → +$0.1989 — a **$0.0039** premium.

## 4. Regression — no new failures

| | established baseline | with this change |
|---|---|---|
| failed | 58 | **58** |
| errors | 23 | **23** |
| passed | 13,268 | **13,317** |

+49 passes are exactly the new tests (41 cap, 8 replay). The 58/23 are
pre-existing and in subsystems I did not touch; I verified the three that could
plausibly have been mine fail identically at `2a75167b~1`. The legacy backlog
was not repaired, per instruction.

## 5. Loss gates, reconciliation, watchdog

**Loss gates — all in force** (§1). Account-wide realized **−$11.49570000**
against the $20 lifetime ceiling → **headroom $8.5043**. L5-scoped realized
since baseline −3.1009 against $20. Daily halt $5. Worst-case deficit $2.00.
Campaign additional-loss bound $5.00. Instantaneous exposure $5.

**Reconciliation — healthy, with two legacy items.** `reconciled: true`,
`uncertainty_cleared: true`, `authority: VENUE`, `ALERT: null`,
`open_orders_at_venue: 0`, `pending_terminal_total: 0`,
`live_orders_pending_terminal: []`. Outstanding and unchanged:

- `gross_detail_lost` on two **legacy** markets (2026-10-12-0030z DOWN 2 vs
  venue 0; 2026-10-14-2230z UP 2.9 vs venue 0), both `stranded_without_stamp`.
  Both pre-date this campaign.
- `leg_cost_basis_unknown`: **48 legs**. This is issue **A1** in
  `L5_OUTSTANDING_ACCOUNTING_ISSUES.md` and is still **OPEN**. Nothing here
  reset, reclassified or netted against it.

**Watchdog — independent and healthy.** `l5-halt-watchdog`
(`fb038b8c`, deploy `642a2fbb`) sweeping every ~20s: `cancel_enabled: true`,
`max_order_age_s: 300.0`, `open_orders: 0`, `overdue: []`, `unverified: []`,
`read_error: null`, `halt: false`, heartbeat fresh at 12:06:51Z.

## 6. Exact proposed live configuration

Everything already set stays as-is. **Four owner-only changes, and nothing
else:**

| variable | set to | why |
|---|---|---|
| `L5_EXPERIMENT_ID` | `l5-btc15m-notional-20261025-3b484e74` | scopes submission/window budgets and the claim locks. The current id's OPEN budget is **16/16 spent** and must not be reused. |
| `L5_TS_EXPERIMENT_ID` | `l5-btc15m-notional-20261025-3b484e74` | gates `TS.active()`; same id for traceability |
| `L5_CAMPAIGN_APPROVAL` | fresh JSON, new nonce, `expires_utc` | the single-use consent |
| `L5_LIVE_AUTH_NOT_AFTER` | fresh absolute deadline | the independent second deadline |
| `L5_SHADOW_ONLY` | `0` | **last**, and only after the three above verify |

**Already correct, do not change:** `L5_TS_OPENING_NOTIONAL_BUDGET_USD=0.80`,
`L5_MAX_TOTAL_EXPOSURE_USD=5`, `L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD=5.00`,
`L5_MAX_WORST_CASE_DEFICIT_USD=2.00`, `L5_DAILY_REALIZED_LOSS_HALT_USD=5`,
`L5_MAX_LIVE_ORDERS=1`, `L5_TS_OPENING_LEG_MAX_CONTRACTS=2`,
`L5_TS_ROUND_ALLOCATIONS=2`, `L5_TS_MIN_FIRST_LEG_PRICE=0.20`,
`L5_TS_TWO_ORDER_PAIR=1`, `L5_WINDOW_STRATEGY=TWO_SIDED`.

Resulting bounds, worst case: opening leg **$0.80**; two allocation rounds
**$1.60**, inside the $2.00 deficit bound and the $5 exposure ceiling;
campaign additional loss **$5.00**; lifetime headroom **$8.5043**.

## 7. What I need from you

1. **Approve the four variable changes in §6**, in that order, shadow last.
2. Confirm the **window count** for the campaign (`L5_LIVE_AUTH_MAX_WINDOWS`
   currently 8).
3. Say whether you want me to produce the fresh approval JSON and deadline for
   you to paste, as before — **I will not write those two variables myself.**

Not requested and not implemented: taker completion, any sell path, a maximum
opening price, dynamic order mirroring, larger submission budgets, any increase
in capital or limits.

## 8. Separate, not part of this request

The variable-maintenance question. Of 54 variables only 2 are read by nothing;
the rest are load-bearing because **for several of them "unset" is LOOSER than
the approved value** — `L5_MAX_TOTAL_EXPOSURE_USD` unset gives **$20** against
production's **$5**, and `L5_TS_OPENING_LEG_MAX_CONTRACTS` unset gives **3**
against **2**. Promoting the approved values into the code constants would make
nine of them deletable and turn a forgotten variable from fail-open into
fail-safe. It is a no-op against today's running values but it changes what
happens when a variable is absent, so it needs its own approval. Not done.
