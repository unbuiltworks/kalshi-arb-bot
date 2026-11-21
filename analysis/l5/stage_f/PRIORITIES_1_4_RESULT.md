# Priorities 1–4: no entry defect, no contamination, limits read back, stopped at authorization

**Date:** 2026-11-01T23:45Z · **Deployed SHA:** `4d52345d` · **Executor: disarmed**

---

## Priority 1 — NOT A DEFECT. I withdraw my own proposal.

The three orders at +29/+30/+42 ticks were **SHADOW records, never submitted.**

```
1345z 13:58:50 UP   +42 ticks  coid=shadow|ARGMAX|...1345z|1793541529
1400z 14:01:34 DOWN +30 ticks  coid=shadow|ARGMAX|...1400z|1793541694
1445z 14:48:40 DOWN +29 ticks  coid=shadow|ARGMAX|...1445z|1793544519
```

Splitting all 61 pre-submits by `coid` prefix:

| | n | ticks behind touch | qty values |
|---|---|---|---|
| **LIVE** (`l5f-…`) | **39** | **min +1, median +2, max +3** | 0.65 – 2.0 |
| SHADOW (`shadow\|ARGMAX\|…`) | 22 | min +0, median +0, **max +42** | **1.0 only** |

**Answers to your three questions:**

1. **Originating code path:** the ARGMAX shadow controller, not the live
   executor. Those records carry `basis: "MODELLED INVENTORY, REAL BOOK. Not
   a fill, not realized."`
2. **Why ~0.50:** it is the shadow arm's own quoting policy on modelled
   inventory, evaluated against a live book. It never reaches a transport.
3. **Intentional or defect:** **intentional, and correct.** The live path has
   never rested an opening order more than **3 ticks** behind the touch, and
   the deployed quote depth is 2. There is nothing to fix.

**So I am withdrawing the >4-tick refusal.** It would refuse 0 of 39 live
orders — dead code, and exactly the unjustified filter you told me not to
ship. **No source fix, no regression tests for a non-defect, and pair entry
and completion logic untouched.**

### The pattern worth naming

This is the **third** time in this session that shadow records nearly became a
live finding: the 21,161 `already_resting` refusals (shadow-only), the
`L5_SHADOW_COMPLETION` taker refusals (caught before reporting), and now this
one — which I did report before checking, in the previous message. The
discriminator is one field: **`coid` prefix `l5f-` is live, `shadow|` is not.**
I am treating that check as mandatory before any claim about live behaviour.

## Priority 2 — CONFIRMED: no contamination of exposure, sizing or risk gates

| vector | finding | evidence |
|---|---|---|
| **current exposure** | **clean** | `committed_usd: {}` **empty**, `cash_deployed: []` **empty**, `live_orders: 0` at 23:43:57Z |
| **order sizing** | **clean** | sizing derives from the cash envelope, which reads the same empty `committed` |
| **unmatched-contract gate** | **clean, structurally** | `unmatched_after=self.store.residual(market)` — **per-market** at both call sites (`l5_live_execution.py:6179, 7389`), so October residuals cannot consume a November market's 5-contract budget |
| **the two drifted markets** | **both retired** | **both** `2026-10-12-0030z` and `2026-10-14-2230z` are in `retired_markets` (162 entries). `retire_settled_market` is its only writer and "runs only on a venue-terminal state WITH a final settlement price" — a **confirmed settlement by construction**, so `cash_legs_for_gates()` marks them `settled: True` and releases the cash |
| **settlement accounting** | **affected, pessimistically only** | the 50 unknown-basis legs hold the **$1/share upper bound**, so the recorded loss is the *maximum possible*; the true loss is smaller |

The historical double-count you might reasonably have worried about **was a
real defect and is already fixed**: `cash_legs_for_gates` used to iterate
`committed()` unfiltered so "the gates charged it forever". It now honours
`retired_markets`.

**Nothing waived. No recorded loss reduced. No cost basis manufactured.**
The only quantity the stale legs touch is the cumulative realized figure, in
the conservative direction, which is the safe direction for a gate.

## Priority 3 — effective limits, read back from the running process

From `L5_LIVE_GATES` at **23:30:07Z** — not defaults, not inferred:

| gate | effective | current | headroom |
|---|---|---|---|
| `max_l5_daily_loss_usd` | **$5** | −$0.0755 (UTC day) | **$4.92** |
| `max_l5_cumulative_loss_usd` | **$20** | $8.9046 | $11.10 |
| **`max_cumulative_loss_usd`** | **$18.6325** | $8.9046 | **$9.73 ← binding** |
| `max_cash_deployed_usd` | **$12.00** | — | — |
| `max_unmatched_contracts` | **5** (per-market) | — | — |
| `max_worst_case_deficit_usd` | **$1.50** | — | — |
| `max_windows` | 20 | — | — |
| `cash_policy` | `NO_RECYCLING` | — | — |
| `halt_gate_configured` | **true** (`L5_HALT_URL`) | — | — |
| `campaign_id` | **null** | — | — |

**All three loss gates pass.** The binding one is the account-wide
`max_cumulative_loss_usd` at **$18.6325**, with **$9.73** of room — worth
watching, since a 4-window campaign at $20 exposure could in principle consume
a meaningful fraction of it.

Campaign budget ledger: `budget_usd 29.30`, `peak_usd 19.1303`,
`remaining_usd 10.1697`, `closed: false`, **`owns_budget: false`** — consistent
with a stale campaign that this process does not own.

## Priority 4 — STOPPED AT THE AUTHORIZATION GATE

| gate | status |
|---|---|
| watchdog | **PASS** — 20s sweeps, `cancel_enabled true`, `open_orders 0`, `read_error null`, `emergency_cancel false` |
| loss gates | **PASS** — all three, per Priority 3 |
| reconciliation | **PASS** — no contamination, per Priority 2 |
| venue state | **PASS** — 0 open orders, nothing pending-terminal, unacked, adopted or quarantined |
| $20 hard cap | **INTACT** — `MAX_TOTAL_EXPOSURE_USD = D("20")` unmodified; effective exposure reads $20 |
| entry fix deployment | **NOTHING TO DEPLOY** — Priority 1 found no defect |
| **authorization** | **FAIL — `approval_state: approval_stale`, `campaign_id: null`** |

**There is no deployment to make.** You authorised deploying "only the
narrowly justified entry fix", and Priority 1 establishes there is no
justified fix. My branch carries 10 unrelated commits; none is justified by
this task, so I deployed nothing.

### The exact action needed

Install a fresh approval document in the Railway variable
**`L5_CAMPAIGN_APPROVAL`** on `l5-pmus-live`. I cannot do this:
`EnvApprovalStore.put` raises `"the executor may not write its own approval"`
by design, and I will not route around it.

Required fields at the deployed SHA (`REQUIRED_FIELDS`):

```json
{
  "approved": true,
  "nonce": "<>= 8 chars, single-use>",
  "series": "cpc-btc-updown-15m-",
  "issued_utc": "<ISO8601, must be < 1h old when read>",
  "expires_utc": "<ISO8601, clamped to issued + 4h>",
  "starts_utc":  "<ISO8601, the FIRST window's start>",
  "max_windows": 4,
  "max_total_exposure_usd": "20",
  "max_daily_loss_usd": "5"
}
```

Four constraints worth knowing before you mint it:

- **`issued_utc` must be under 1 hour old** when the executor reads it
  (`DEFAULT_MAX_AGE_S = 3600`), so mint it shortly before arming;
- **`expires_utc` is clamped** to `issued + 4h` (`MAX_CAMPAIGN_S`);
- **`max_total_exposure_usd` above 20 is silently clamped to 20** — your $50
  allowance is an upper bound, and I have not touched the cap;
- **`starts_utc` matters**: without it, arming early can spend a window grant
  on an already-live window, which cost a previous campaign its last window.

Once it is in place I will run the full preflight, report the five
pre-submission items, and execute the four windows only if every gate passes.

**Nothing armed. No order submitted. No limit changed. No executor variable
written. No approval fabricated or bypassed. No credential touched. No new
service. $0 cost.**
