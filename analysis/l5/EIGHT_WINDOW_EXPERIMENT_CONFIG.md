# 8-window L5 sequential-pairing experiment — effective limits, config, authorization

**2026-10-26.** PMUS only. **Nothing applied, nothing armed.** Production is
`b1a8d3bf` at `ff1f8ea3`, executor disarmed, prior campaign expired and not
restarted. The $20 aggregate ceiling and both loss limits are **not raised**
anywhere below.

---

## 1. Reconciling the $0.80 cap with 3-contract sizing

They are two independent bounds and `min()` applies:

```
qty = min( opening_leg_max_contracts , opening_notional_budget / own_price )
```

3 contracts costs the notional cap exactly at `p = cap/3`, so:

| notional cap | 3 contracts reachable while |
|---|---|
| **$0.80 (deployed)** | **p ≤ 0.2667** |
| $1.00 | p ≤ 0.3333 |
| **$3.00** | **p ≤ 1.0000 (all legal prices)** |
| $5.00 (approved) | p ≤ 1.6667 (unreachable range) |

The deployed first-leg floor is **0.20**, so under $0.80 the band where 3
contracts is reachable is **p ∈ [0.2000, 0.2667] — a 7-tick window**. At the
measured mean first-leg price of **0.5733** the $0.80 cap yields **1.40
contracts**, and the 3-contract allowance is **unreachable**.

| own price | qty @ $0.80 | qty @ $3.00 | notional @ $3.00 | binds |
|---|---|---|---|---|
| 0.20 | 3.00 | 3.00 | $0.60 | contracts |
| 0.2667 | 3.00 | 3.00 | $0.80 | contracts |
| 0.45 | 1.78 | 3.00 | $1.35 | contracts |
| **0.5733** (mean) | **1.40** | **3.00** | **$1.72** | contracts |
| 0.90 | 0.89 | 3.00 | $2.70 | contracts |
| 0.99 | 0.81 | 3.00 | $2.97 | contracts |

### The exact effective cap

3 contracts at the highest legal price costs **3 × 0.99 = $2.97**, and
$2.97 < $5, so **with both approved limits in force the contract cap binds at
every legal price and the $5 notional is never reached**.

> **EFFECTIVE OPENING CAP = 3 contracts, i.e. notional ≤ $2.97.**
> `$3.00` is the smallest notional budget that makes this true everywhere, so
> it is what I propose — tighter than the approved $5 and operationally
> identical.

---

## 2. Configuration changes needed

### 2a. TWO RISES THAT NEED YOUR EXPLICIT CONFIRMATION

You said preserve all existing protections. These two are **arithmetically
required** by 3-contract sizing, and both stay inside your approved envelope —
but they are **loosenings of deployed values** and I will not apply them on my
own reading.

| variable | deployed | proposed | why it must move |
|---|---|---|---|
| `L5_TS_OPENING_NOTIONAL_BUDGET_USD` | **0.80** | **3.00** | at $0.80, 3 contracts is reachable only in p ∈ [0.20, 0.2667]; at the mean price it yields 1.40 contracts |
| `L5_MAX_WORST_CASE_DEFICIT_USD` | **0.90** | **3.00** | computed from the real gate: a 3-contract opening leg's worst-case deficit, swept over every legal price 0.20–0.99, is **$2.99**. At 0.90 every opening leg above ~0.30 would be refused and the experiment would place nothing. |

The $3.00 deficit bound is deliberately *not* higher: same-side stacking of two
3-contract orders reaches **$6.00** and stays **refused by design**.

Verified from the gate, not assumed:

| scenario | worst-case deficit |
|---|---|
| open 3 @ 0.20 | $0.65 |
| open 3 @ 0.5733 | $1.79 |
| open 3 @ 0.99 | **$2.99** |
| hold 3 UP @0.5733 + complete 3 DN @0.2929 (pair 0.8662) | **−$0.3414** |
| hold 3 UP @0.4217 + complete 3 DN @0.5383 (pair 0.9600) | **−$0.0500** |
| same-side stack, 2 × 3 @ 0.99 | $6.00 → **refused** |

**A completed pair has a negative deficit** — profitable on both outcomes,
carrying no directional risk at all. That is the strategy working as intended,
and it is why the deficit gate does not obstruct completion.

### 2b. Other changes, all tightenings or within approval

| variable | deployed | proposed | note |
|---|---|---|---|
| `L5_TS_OPENING_LEG_MAX_CONTRACTS` | 2 | **3** | as approved; code ceiling is 3 |
| `L5_MAX_CASH_DEPLOYED_USD` | 4.64 | **6.00** | a 3-contract pair costs ≤ $2.88; 6.00 covers two windows if a settlement reconciliation lags. 4.64 would cover one and fail closed on an overlap, wasting windows rather than risking money. |
| `L5_MAX_TOTAL_EXPOSURE_USD` | 5 | **20** | your approved aggregate; equals the code ceiling, **not raised** |
| `L5_MAX_CUMULATIVE_LOSS_USD` | — | **10** | your approved cumulative |
| `L5_MAX_L5_DAILY_LOSS_USD` | 5 | **5** | unchanged |
| `L5_LIVE_AUTH_MAX_WINDOWS` | 4 | **8** | equals code ceiling `CA.MAX_WINDOWS` |
| `L5_LIVE_AUTH_MAX_OPEN` | 6 | **12** | 8 windows + margin |
| `L5_LIVE_AUTH_MAX_COMPLETE` | 9 | **18** | ≥ 2 per obligation × 8 windows (the completion reserve) |
| `L5_LIVE_AUTH_MAX_REPLACE` | 12 | **24** | reprice budget |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | 21 | **42** | flat backstop, tighter than the class sum |
| `L5_LIVE_AUTH_NOT_AFTER` | EXPIRED | **fresh, ≤ 4 h** | currently −4,394 s |
| `L5_CAMPAIGN_APPROVAL` | stale | **the document in §4** | |
| `PMUS_Q1_LIVE` / `PMUS_Q1_ARM` | — | `YES` / `=PMUS_Q1_SESSION_ID` | the arming handshake, **last** |

### 2c. Unchanged, and not negotiable in this experiment

`L5_TS_MIN_FIRST_LEG_PRICE=0.20` · pair ceiling **0.96** ·
`L5_MAX_LIVE_ORDERS=1` · `L5_DEFENSIVE_COMPLETION=0` ·
`L5_TS_COMPLETION_CROSS` disabled · `L5_MAKER_PAIR_GATE` unset (and its code is
not in this lineage) · maker-only execution · independent watchdog ·
exact-order cancellation · durable reconciliation · account-wide loss gates.
**No safety lock removed, no account-wide gate overridden.**

---

## 3. Live verification, on the running deployment

| check | state at 19:21:54Z |
|---|---|
| deployment | `b1a8d3bf` **SUCCESS**, SHA `ff1f8ea3` |
| executor armed | **false**; gate `blocked: true, reason: NOT_ARMED` |
| halt latch | **CLEAR** |
| process preflight | **PASSED** |
| authorization preflight | fails on `absolute_deadline` + `campaign_approval` — **by design**, both expired; all 9 other rows PASS |
| independent watchdog | `BOTH_DOCUMENTS_FRESH` from `l5-halt-watchdog.railway.internal:8080` |
| durable reconciliation | ingest cursor `durable: true`, `resumed: true`; auth ledger `durable: true` |
| **outstanding orders** | **`pending_terminal: []` — none** |
| existing positions | flat |
| available cash | **$339.16685** (authoritative `/v1/account/balances`) |
| account-wide realized | **−$13.6405** all-time |
| day realized | **$0** for 2026-10-26 |
| campaign | `approval_stale`, prior nonce spent 4/4, **not restarted** |
| budget selfcheck | `all_passed: true` |
| residual-side selfcheck | `PASS` 7/7 |
| feed errors | **1** (was 0) — minor, flagged not dismissed |

---

## 4. Prepared campaign authorization — 8 windows, time-limited

`scripts/l5_sequential_experiment_approval_emit.py`. **Prints a document;
authorizes nothing.** The executor cannot write its own approval
(`EnvApprovalStore.put` raises deliberately).

```json
{"approved":true,"expires_utc":"2026-10-26T22:24:36+00:00","issued_utc":"2026-10-26T19:24:36+00:00","max_daily_loss_usd":"5","max_total_exposure_usd":"20","max_windows":8,"nonce":"eb79a4b271122db56669c089","series":"cpc-btc-updown-15m-"}
```

- `sha256` **d55b2708ab73d2361b15682b5a5c5b4e1a3450a0e21b084402a2d026692efe9a`**
- `nonce_sid` **`camp1.eb79a4b271122db56669c089`** — fresh, so its window count
  starts at **0 of 8**. The spent `camp1.87265e609a92b5a2889e5392` (4/4,
  expired 17:42:45Z) is left untouched as the audit record.
- **Validated against the real gate**: `approval_gate().state()` returns
  `approved: True`, `why: None`, nothing clamped. All `REQUIRED_FIELDS`
  present, nonce 24 chars (≥8), series allow-listed, and `max_windows 8`,
  `exposure 20`, `daily_loss 5` each **equal** their code ceilings rather than
  exceeding them.
- 8 consecutive 15-minute windows span **2 hours**; the document runs 3 hours
  and `state()` clamps the effective expiry to issued + 4 h regardless.
- **Re-issue with a fresh timestamp at activation time** — this one is already
  ageing against the 4-hour staleness bound.

---

## 5. Projected outcome at the approved envelope

40,000 trials, 8 windows, 3 contracts, **empirical** PMUS pair costs and strand
prices, with the $5 daily and $10 cumulative halts live:

| | |
|---|---|
| completion rate used | **47.7%** (measured, but **modelled fills**) |
| **P(halt before 8 windows)** | **1.5%** — all of it the $5 daily gate |
| windows actually run | **7.97 of 8** |
| net P&L | mean **+$1.52**, p5 **−$3.00**, median +$1.59, p95 +$5.79 |
| **P(net > 0)** | **72.4%** |
| peak cash deployed | **$2.88** — 6.9× headroom under the $20 ceiling |

**A correction to my own earlier figure:** I previously put P(halt) at 3
contracts at 4.3%, using the mean *completed* first-leg price (0.5733) for
strand risk. Strands actually occur at mean own cost **0.4217** — cheaper — so
the correct figure is **1.5%**. The envelope is more robust than I said.

### Which gate binds

| situation | magnitude | gate | result |
|---|---|---|---|
| opening leg, worst legal price | $2.97 | deficit $3.00 | permitted |
| completed pair, worst case | $2.88 | cash $6.00 | 2.1× headroom |
| peak cash over 8 windows | $2.88 | aggregate $20 | 6.9× headroom |
| one worst-case strand | $2.97 | daily $5 | 1.7 strands/day |
| one worst-case strand | $2.97 | cumulative $10 | 3.4 strands total |
| same-side stacking | $6.00 | deficit $3.00 | **refused by design** |

Expected strands over 8 windows is **4.2** against a cumulative gate that
absorbs **3.4** worst-case ones — tight, which is exactly why P(halt) is not
zero. It is not breached in expectation because the mean strand is $1.27, not
$2.97.

---

## 6. What I need from you before activating

1. **Confirm the two rises in §2a** — `L5_TS_OPENING_NOTIONAL_BUDGET_USD`
   0.80 → 3.00 and `L5_MAX_WORST_CASE_DEFICIT_USD` 0.90 → 3.00. Without both,
   3-contract sizing places nothing; with them, no other protection moves.
2. **Confirm `L5_MAX_CASH_DEPLOYED_USD` 4.64 → 6.00**, or tell me to leave it
   at 4.64 and accept skipped windows on a settlement-reconciliation overlap.

On your word I will apply the batch, re-issue the campaign document with a
fresh timestamp, restart on the existing image, re-verify every gate in §3,
and only then arm through the existing owner handshake — reporting the
deployment ID, nonce, exact expiry, armed state, and the first venue-confirmed
submission or refusal.

## 7. What will be collected, and the honest expectation

**Collected per window:** venue-confirmed submissions, maker fills, partial
fills, submit→ack latency, pair completions with both legs' own prices, residual
inventory, fees actually charged, venue settlements, and realized net P&L —
each labelled by evidence grade, with completed-pair economics reported
**separately** from directional residual, and compared action-by-action against
L5's corresponding decisions.

**Expectation, stated plainly:** mean **+$1.52** over 8 windows, not $5–10 per
window. The experiment's value is the **first venue-confirmed maker fill** and
a real completion rate — the 47.7% that every economic figure here rests on has
never been observed. Eight windows will not give a significant per-window CI
either way. **After 8 windows it stops. No extension, no capital increase, no
automatic scaling.**
