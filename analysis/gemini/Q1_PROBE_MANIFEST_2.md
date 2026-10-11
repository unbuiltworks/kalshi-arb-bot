# READY_FOR_Q1_APPROVAL — bounded q=1 short-crypto probe #2

**NOT ARMED. NOTHING SUBMITTED.** `AUTHORIZED_BUNDLE is None`,
`arming.EXPECTED_AUTH_ID == "NO-AUTHORIZATION-IN-FORCE"` — an identifier no
environment value matches, so the transport cannot arm even if every flag were
set by accident. This manifest asks for approval; it does not take it.

## OBSERVED FACTS — what is deployed right now, verified independently

| | value | how verified |
|---|---|---|
| branch | `claude/gemini-btc-economic-viability-h3u7zl` | Railway deployment meta |
| deployed commit | **`e6b7ef97`** | `list-deployments` → `d3b2cf90`, status SUCCESS 00:21:12Z |
| previous deployment | `d6a23b9d` serving `8fb01c8` | superseded 00:18Z |
| P10 recorder | **LIVE, independent of any session** | `P10_RECORDER start root=/state/gemini-l5-faithful/p10`, `P10_HB` btc15m book age **0.83s**, btc5m **1.06s**, `degraded: false`, `mutations_sent: 0` |
| bundle_sha (new) | `ce00418d82853fa983decf6963fa5d84f5b81271c32d97631e35b96c48218a64` | `bundle.bundle_sha()` |
| AUTHORIZED_BUNDLE | `None` | disarmed |

The **shadow** collector and the **live** probe are different execution paths:
Railway runs the read-only shadow and the P10 recorder; a live probe is a
GitHub-Actions-dispatched `run_continuous` armed run. Deploying to Railway
cannot place an order.

## OBSERVED FACTS — the configuration that last traded

The last armed trading was **EP016 / COHORT-1**, 2026-09-22 00:28–03:02Z, runs
[35671834127] and [35677907781].

* spec `btc15m_q1_cap4_w60_880`, `spec_sha 223b81ed…`, quote interval **TTE [60, 880]**
* envelope `GEMINI_L5_LIVE_ENVELOPE_Q1_BTC15M_CAP4_V1`, `3aed2098…`
* 6 windows observed, 6 traded, 31 of 120 mutation slots, stop `STOPPED_AGGREGATE_LOSS`
* **18 fills, every one maker, every one `fee: 0.0000`**
* settlement over the 4 recorded windows: spend 6.86, payout 8.00, **net +1.14**
  — settlement-implied, **NOT REALIZED**, no cash movement read back

**Two corrections to things I said earlier in this session, from this evidence:**

1. **CAP4 is not untested at relevant depth in LIVE trading.** It bound
   **twice** — "a fill would take NO to 5, past 4" (window 3) and "…YES to 5,
   past 4" (window 5). My "UNTESTED AT RELEVANT INVENTORY DEPTH" finding was
   measured on the *shadow* sample, where no window reached 4 fills. The live
   cohort reaches depth 4 routinely. The counterfactual logging below will
   therefore produce real evidence immediately, not eventually.
2. **The last live cohort did not lose money.** It stopped on
   `STOPPED_AGGREGATE_LOSS` at 10.63 of a 10.00 cap, but that measure is cash
   OUT from confirmed fills with no settlement credit — for a strategy that
   buys both legs and is paid on one, it accumulates roughly half of turnover
   as "loss". It is a turnover cap wearing a loss cap's name, and it halted a
   cohort whose recorded windows were net **+1.14**.

## The probe: the SAME proven configuration, plus instrumentation only

**Every behavioural parameter is unchanged from EP016.** No band moves, no tick
moves, no threshold moves, no cap moves. The requested BTC15m horizon
restoration is **already in place**: EP016 ran TTE [60, 880], which is [0, 900]
clipped by the existing end-of-window floor — `NO_NEW_OPEN_EXTEND_TTE_S` = 60
below which no OPEN or EXTEND may start, and 880 because a contract does not
exist above 900 and entry is at 840–880. It is evidence-backed, not chosen
now: of L5's 626 BTC15m OPEN fills across 527 windows, **560 (89.5%) fall at
TTE 600–900 and none below TTE 60**.

| field | value | changed vs EP016? |
|---|---|---|
| spec | `btc15m_q1_cap4_w60_880_c2`, `spec_sha ba64b54c…` | ledger chain only |
| family / envelope | btc15m / `LIVE_Q1_BTC15M_CAP4` (`3aed2098…`) | **no** |
| quote interval | TTE [60, 880] | **no** |
| entry band | 840:880 | **no** |
| q | 1 | **no** |
| rungs, offsets, pricing, aggressiveness | 1 rung/outcome at the bid | **no** |
| maker/post-only | enforced | **no** |
| rest floor | 30s | **no** |
| settlement guards | OPEN/EXTEND stop <60s, withdraw-all <30s | **no** |
| CAP4 and all dollar bounds | $4.00 committed / $4.00 residual / 4 units / 2 working orders / 12 actions / 1 market | **no** |

### What IS new — all of it instrumentation, none of it decides anything

`src/gemini_execution/fill_quality.py` (**added to the reviewed bundle**, which
is why `bundle_sha` moved). After every fill it computes and the caller logs:

matched inventory · residual inventory (signed) · average cost each side ·
effective matched pair cost · **executable complement cost** (the ASK we would
have to pay, never the mid) · **projected pair cost if completed now** ·
`pair_locked_below_1` · **residual dollars at risk** (basis, not $1 face) ·
the L5 controller's **next desired action** · fee paid, or `NOT YET RECONCILED`.

Applied to the LIVE-3 fill that was logged as just "FILL NO 1 @ 0.02", it
yields: needed leg YES, executable complement **0.96**, projected pair cost
**0.98**, locked below 1.00, residual at risk **$0.02**. That was knowable at
the fill and was not written down.

`cap_counterfactual()` records, **on every offered action whether or not it
binds**, what the controller wanted, whether the envelope would change it,
which bound, and the remaining headroom. The denominator is visible, so the
answer can never again be an unreachable zero.

Lifecycle is `SUBMITTED → FILLED → SETTLED → CASH_RECONCILED`. **REALIZED is
deliberately absent** and is refused as a stage: no number is realized without
an attributable cash movement.

**Rebate appears in no field.** A test asserts the record contains no rebate
term. The trade is acceptable on pre-rebate economics or it is not acceptable.

## Maximum possible loss under this probe

| | |
|---|---|
| per market, worst case | **$4.00** — `max_committed_usd`; four binary contracts cannot cost more and a contract that resolves against pays nothing back |
| concurrent markets | **1** |
| cumulative loss stop | **$10.00** (`window_loss_usd`, cash out with no settlement credit) |
| **maximum loss before every stop has fired** | **$10.00 of cash out**, and at most $4.00 of it exposed in any one market |

The $10.00 stop is **deliberately left exactly as it is**, mis-naming and all.
It is conservative: it halts sooner than a settlement-aware measure would, and
it already demonstrated that by halting a net-positive cohort. Making it
settlement-aware would make it fire *less* often, which is loosening a safety
control, and I am not proposing that inside a probe manifest.

## Stop conditions

1. `STOPPED_AGGREGATE_LOSS` — cumulative `window_loss_usd` ≥ $10.00
2. envelope refusal — imbalance >4 units, any leg >4, committed or residual >$4.00
3. mutation budget — 12 action attempts per window, one ledger per window
4. end of window — no OPEN/EXTEND below TTE 60s; withdraw all below TTE 30s
5. deadman — market state older than 20s withdraws and stops
6. bounded UNREADABLE hold — 5 consecutive unreadable/crossed cycles, then withdraw
7. fail-closed rollover — an order still listed, an unreadable order list, or an unreadable position STOPS the cohort rather than skipping a contract
8. authorization expiry — `GEMINI_CANARY_AUTH_EXPIRES` is mandatory
9. a completed or crashed probe **does not relaunch itself**

## Rollback / disarm — exact commands

Fastest first; any one is sufficient.

```bash
# 1. immediate, no deploy: the arm flag
unset GEMINI_CANARY_LIVE          # or set it to anything but YES

# 2. burn the identifier (reviewable commit, then redeploy)
sed -i 's/^EXPECTED_AUTH_ID = .*/EXPECTED_AUTH_ID = "NO-AUTHORIZATION-IN-FORCE"/' \
    src/gemini_execution/arming.py

# 3. break the bundle
sed -i 's/^AUTHORIZED_BUNDLE = .*/AUTHORIZED_BUNDLE = None/' \
    src/gemini_execution/bundle.py

# 4. cancel anything resting, by venue orderId from the outstanding ledger
python3 -m src.gemini_execution.run_continuous --cleanup-only

# 5. verify disarmed
python3 -c "import sys;sys.path.insert(0,'.');\
from src.gemini_execution import arming,bundle;\
print(arming.state()); print(bundle.state()['authorized'])"
```

## Two known defects carried into this probe, unfixed and declared

1. **`window_loss_usd` is a turnover measure, not a loss measure.** Kept as-is
   because it is the conservative direction. Expect the probe to stop early
   again. That is acceptable for a probe whose product is evidence.
2. **`ledger_for` returns the first UNSPENT ledger**, so a window can inherit a
   partly-spent one: in EP016 two of six windows got 4 and 5 slots instead of
   12. Not fixed here because fixing it *increases* activity per window, which
   is an owner decision, not a manifest author's.

## Not included, and why

**The BTC5m placement arm is NOT in this probe.** `ab_arms.maker_only_improve`
does satisfy the stated condition — it returns `None` whenever the improved
price would reach the ask, so it never crosses and never touches it. But
putting it on the live path means wiring `apply_A` into `run_continuous`'s
order construction, which is a change to the **live decision path** with zero
live evidence behind it, and `max_concurrent_markets = 1` means it could not
run beside BTC15m anyway. It should be its own approval after this probe
produces trade-quality evidence. I have not made that change.

## What approval would authorize

One cohort of BTC15m windows at q=1, one market at a time, under the bounds
above, with the new telemetry recording every fill. On approval the remaining
steps are: issue a new `EXPECTED_AUTH_ID`, set `AUTHORIZED_BUNDLE` to
`ce00418d…`, grant a ledger chain, and dispatch. **None of that is done.**

## P10 continues in parallel and is not a gate on this probe

The persistent recorder is live and independent (book age 0.83s / 1.06s,
`degraded: false`). The pre-registered decoder gate remains FAIL CLOSED at
n=144 with G2 unresolved, and it governs **historical** conclusions only. This
probe produces its own forward, venue-confirmed evidence and does not depend on
decoding the historical tape.
