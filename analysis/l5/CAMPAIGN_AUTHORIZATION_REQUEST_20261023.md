# Bounded live campaign — terms for confirmation (NOT armed)

Executor `l5-pmus-live`, project `arb-ev-wallet-shadow`. Verified
2026-10-23T14:55Z after applying the authorized budget change.

## 1. The five verifications

| item | state | source |
|---|---|---|
| budget variable | `L5_CAMPAIGN_BUDGET_USD` = **29.30** | set by me, this turn |
| all-time realized | **−$9.30360000** (unchanged, nothing reset) | `L5_LIVE_HB` |
| day realized / room | −$0.4048 / **$4.595200** of the $5 halt | `L5_LIVE_HB` |
| shadow lock | **ON** — `blocked:true`, `reason:SHADOW_ONLY_LOCK`, `armed:false` | `submission_gate` |
| independent watchdog | **PRESENT, `halt:false`, `halt_latch:CLEAR`** | deploy 29123aca SUCCESS |
| authorization | `approval_present:true`, **`approval_state:approval_stale`**, `permits_this_window:null` | `campaign` |
| preflight | **PASSED** | `L5_LIVE_HB` |
| venue cash | $343.50375, `read_ok:true` | `/v1/account/balances` |

**The redeploy.** Setting the variable rebuilt the service from branch HEAD
`7cd9cb89` instead of the owner-verified `d1cead39` (= `d31a9fc5`). I checked
the difference rather than assuming it: `git diff d31a9fc5..HEAD -- src/` is
**two files, 63 added lines, zero execution-path change** —
`l5_capacity_curve.py` (an analysis-cohort constant) and
`l5_runtime_config_verify.py` (one read-only verification row that can only
add a refusal). `L5_DEFENSIVE_DWELL` remains absent, so the dwell gate is
still off, as instructed.

## 2. Effective budget — three ceilings, and the campaign budget is not the binding one

| ceiling | value | room remaining |
|---|---|---|
| campaign worst-case budget (just raised) | $29.30 | 29.30 − 9.3036 = **$19.9964** |
| account-wide cumulative loss (**preserved at $20**) | $20 | 20 − 9.3036 = **$10.6964** |
| daily realized-loss halt (**preserved at $5**) | $5 | **$4.5952** today |

`headroom = budget − (realized losses + unsettled cost + outstanding
notional)`, per `l5_campaign_budget`. So the authorized $29.30 is exactly
calibrated — it yields $19.9964, i.e. the intended ~$20 of fresh worst-case
room, **without erasing the $9.3036**, which still counts on every evaluation.

**But the effective budget is $4.5952 today, not $20**, because the daily halt
binds first. Across a campaign spanning midnight it is $10.6964, set by the
account-wide gate the instruction preserves. I did not raise either.

**Not yet observable in telemetry.** The campaign-budget gate takes
`campaign_id` from the approval's own nonce, so with the approval stale the id
is `None` and the gate is **inert**. The $29.30 is set and confirmed present;
its headroom becomes measurable only once a valid approval exists.

## 3. A side effect of the raise the owner did not ask for

`build_orders` sizes on `min(cap/dear, env/(1+up_px), headroom/(1+up_px))`.
Budget headroom is one of the three sizing terms, so raising it enlarges
orders:

| budget | headroom | by_budget | by_cash ($4.65 env) | qty | acquisition |
|---|---|---|---|---|---|
| $11.10 | $1.7964 | 1.21 | 3.14 | **1.21** | $1.16 |
| **$29.30** | $19.9964 | 13.51 | 3.14 | **3.14** | **$3.01** |

The budget stops binding and the cash envelope takes over: **per-window size
rises 2.6×**, from the 1.17 contracts the October 23 campaign actually traded
to 3.14. At $3.01 of acquisition, one stranded dear leg consumes two-thirds of
today's $4.5952 room, and two bad windows halt the campaign.

**Recommended correction:** tighten `L5_MAX_CASH_DEPLOYED_USD` from $4.65 to
**$1.65**, which pins qty at 1.11 regardless of the budget. That is a
tightening, not a loosening, and it holds size at the level already traded.

## 4. The evidence that matters most, and it is negative

The service's own **CONSERVATIVE production shadow**, 282 settled windows
since 2026-10-19, running the policy now deployed:

| | |
|---|---|
| windows observed / zero-trade / settled | 364 / 94 / 282 |
| **pair_net** | **+$42.4209** |
| **residual_net** | **−$98.9509** |
| **net** | **−$56.53** |
| capital deployed | $346.24 |
| **capital efficiency** | **−0.1682 (−1,682 bp)** |
| **net per settled window** | **−$0.2065** |
| max drawdown | $59.11 |
| fill rate | 0.8185 |
| adverse fill rate | **0.7669** ($51.60) |
| possible missed opportunities | **0** (`possible_miss_rate 0.0000`) |

This is shadow, not realized — labelled as such. But it is out-of-sample, in
production, against the real book, on the actual deployed policy, and it is
the largest sample we have of our own strategy. **It says each window loses
about $0.21.**

The decomposition is the finding: **pairs make money (+$42.42) and residuals
lose more than twice as much (−$98.95).** Combined with last turn's result —
entries at quoted pair cost ≤0.96 earn +186.93 bp out of sample and our gate
already enforces it — the loss is not in entry selection. It is in what
happens to legs that fail to pair. `possible_misses: 0` rules out the gate
being too tight.

A 4-window campaign at the recommended size therefore has an **expected
outcome of about −$0.83**, not a profit.

CLAUDE.md says: *"Do not promote a candidate whose economic evidence is
negative or inconclusive."* On this evidence I cannot represent this campaign
as positive-expectancy, and I am not going to arm it on my own reading of an
instruction to resume live validation. The decision is the owner's, and these
are the numbers it should be made on. If the purpose is to buy venue-confirmed
fill data rather than profit, that is a coherent reason to spend ~$0.83 — but
it should be chosen deliberately, not inherited from an assumption of edge.

## 5. Proposed terms

| term | value |
|---|---|
| series | `cpc-btc-updown-15m-` (only allowlisted family) |
| windows | **4** (code ceiling 8; 4 is what $4.5952 supports) |
| execution mode | **maker-only, post-only, two-sided pair.** No crossing: it needs separate authorization and the `MAKER_ONLY` posture forbids an armed crosser |
| pair-cost ceiling | **≤ 0.96** (the OOS-validated rule, already enforced) |
| order sizing | **1.11 contracts/leg**, obligation $1.6428, acquisition ~$1.07/window |
| aggregate acquisition | **≤ $4.26** across 4 windows |
| max loss | **$4.5952** — the daily halt fires first and stops new commitment |
| instantaneous exposure | ≤ $2.00 (document-narrowed from the $20 code ceiling) |
| residual cap | **3** (production effective, from `residual_cap:3>=3`) |
| approval expiry | **issued + 90 min** (code ceiling 4h; 4 windows = 1h) |
| renewal | none — single-use nonce, spent on claim |

## 6. What still has to happen, and who must do it

I have **not** armed, and three things stand between here and a live order:

1. **`L5_CAMPAIGN_APPROVAL`** — the document below. By design the executor
   may not write it: `EnvApprovalStore.put` raises *"the executor may not
   write its own approval"*, and the module states *"Claude cannot write it,
   so Claude cannot approve a campaign."*
2. **`L5_SHADOW_ONLY` = 0** — removes the shadow lock.
3. **`PMUS_Q1_LIVE` = YES** — the owner handshake.

```json
{
  "approved": true,
  "nonce": "camp-20261023-4w-a1",
  "series": "cpc-btc-updown-15m-",
  "issued_utc": "<now, UTC, tz-aware>",
  "expires_utc": "<now + 90 minutes, UTC, tz-aware>",
  "max_windows": 4,
  "max_total_exposure_usd": "2.00",
  "max_daily_loss_usd": "5"
}
```

Naive or future timestamps, a stale document, a spent nonce, or a series
outside the allowlist all read as refusal. The document can only narrow:
asking for more than the code constant yields the constant.

**One honest caveat about that separation.** The design assumes I cannot write
the approval because only the owner holds Railway. In this session that is not
true — my Railway token wrote `L5_CAMPAIGN_BUDGET_USD` a few minutes ago, so
it could equally write `L5_CAMPAIGN_APPROVAL` and the two arming variables.
I am not going to, because the dual-key property is the control, and a
campaign I both authorize and execute has no second key. The owner should know
the gap exists rather than assume the architecture closes it.
