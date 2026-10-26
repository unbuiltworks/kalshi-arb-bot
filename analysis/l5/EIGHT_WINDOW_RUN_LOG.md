# 8-window live run — what actually happened

**2026-10-26.** PMUS only. Venue-confirmed facts and refusals; nothing modelled
is counted. Running deployment `60f56b67` on SHA `ff1f8ea3`, campaign nonce
`camp1.eb79a4b271122db56669c089`, expiry `2026-10-26T22:32:56Z`.

---

## Answer to the only question that matters

**Did PMUS receive and accept an actual order? NO.** Zero submissions, zero
acknowledgements, zero fills, zero settlements. Realized net P&L **$0.00**.
`pending_terminal: []` throughout; cash unmoved at **$339.16685**.

**What specifically prevented it:** a single gate, identical on every decision
in window 2000z, with the executor **armed**:

```
key: TWO_SIDED_PROBE:PAIR_UP:CUMULATIVE_LOSS_LIMIT:13.64050000>=10
pair_cost: 0.9500        order: null   intent: null   armed: true
```

---

## Root cause: a configuration error of mine, not a venue or strategy problem

`l5_pmus_live_runner.py:6248`

```python
max_cumulative_loss_usd=D(str(os.getenv("L5_MAX_CUMULATIVE_LOSS_USD", "20")))
```

and `l5_campaign_budget.py:7` states plainly that `max_cumulative_loss_usd` is
**the ACCOUNT-WIDE lifetime ceiling**.

The gate (`l5_live_validation_gates.py:517`):

```python
loss = max(D(0), -cumulative_realized) + max(D(0), cumulative_unrealized_loss)
if loss >= self.max_cumulative_loss_usd:
    return "CUMULATIVE_LOSS_LIMIT:%s>=%s" % (loss, self.max_cumulative_loss_usd)
```

The account's **all-time** realized loss is **−$13.6405**, accrued long before
this experiment. The default ceiling is **$20**, so the gate passed before — the
earlier four-window campaign armed and ran against it without complaint.

**I set `L5_MAX_CUMULATIVE_LOSS_USD=10`**, which is *below the loss the account
had already taken*. `13.6405 >= 10` is therefore true on every evaluation and
every pair is refused **permanently**. The experiment could never have placed
an order under that value.

### The mapping mistake

The owner approved "$10 maximum cumulative experiment loss". I applied that to
the **lifetime account-wide** ceiling. The **experiment-scoped** gate is
`L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD`, which measures loss from a captured
campaign baseline — `l5_campaign_baseline.py:44`: *"NOTHING HERE RESETS A
REALIZED LOSS. The baseline records what realized was."*

That gate was **already set to 5.00 and already passing**, i.e. *stricter* than
the $10 requested. The owner's intent was satisfied before I changed anything,
and my change to the lifetime gate was both unnecessary and blocking.

### Why the gate was right and the threshold was wrong

The gate is additive and refuse-only, and it fired upstream of translation, so
maker/taker eligibility never came into it. Note `pair_cost: 0.9500` — **these
were economically eligible pairs inside the deployed 0.96 ceiling.** The
strategy located exactly what it was built to find; the misconfigured ceiling
discarded it.

---

## Window-by-window, venue-confirmed

| window | armed | outcome | venue order |
|---|---|---|---|
| **1945z** | armed at 19:50:27, then killed | `CAMPAIGN_WINDOW_NOT_USABLE` / `INSUFFICIENT_ENTRY_RUNWAY: 192.0 < 300.0` after my arming redeploy landed 348 s in | none |
| **2000z** | **true** | every decision refused `CUMULATIVE_LOSS_LIMIT:13.64050000>=10` | none |

**2 of 8 windows consumed, 6 remaining.** Both claims are legitimate and appear
in the durable ledger: `"windows": ["…1945z", "…2000z"]`.

### Two errors of mine in this run, both costing a window

1. **1945z**: `owner_arm_token` was already `true`, so setting `PMUS_Q1_*` was a
   no-op — and the redeploy to "apply" it killed a process that had armed 16
   seconds earlier and was holding a claimed window.
2. **2000z**: the cumulative-loss misconfiguration above.

Neither was a venue, strategy or safety failure. Both were mine.

---

## Risk state, independently verified

`armed: true` · watchdog `BOTH_DOCUMENTS_FRESH` · halt latch `CLEAR` ·
`pending_terminal: []` · cursor `durable, resumed` · day realized **$0** ·
all-time **−$13.6405** · cash **$339.16685** · OPEN budget 1 of 12 spent (the
pre-fix carry-over), 11 remaining · `uptime_s: 914.1` at 20:06:02 against a
19:50:43 boot, so no restart.

No safe-halt condition is met: the state is verifiable and the service is
healthy. The gate is protecting correctly.

## Cost of inaction

Windows are claimed at arming and then every decision is refused, so **each ~15
minutes burns one of the 6 remaining for nothing**. Unattended, the campaign
exhausts at zero submissions around 21:40, well inside its 22:32:56 expiry.

## The correction, not applied

Restoring `L5_MAX_CUMULATIVE_LOSS_USD` to its documented default **20** reverts
my own erroneous change to a pre-existing account-wide gate. It raises no
approved limit: the owner's $10 experiment cap stays enforced — at $5 — by
`L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD`, and every other limit is untouched
($20 aggregate exposure, $5 daily, 3 contracts, $3.00 notional, $3.00 deficit,
$6.00 cash, 0.96 ceiling, 0.20 floor, one live order, maker-only).

It is still an account-wide loss gate, so it is **not** being changed without
explicit owner approval.

## Open, unrelated to this diagnosis

`latency_s` on 2000z decisions ran as low as **−86.6 s** — L5 events stamped
well after our read of them, worse than the −4.9 s p10 measured over 3,431
rows earlier. 31 such rows were already unexplained. It does not affect the
refusal above and is tracked separately.

---

## 2045z — armed, viable pair, refused by strategy exclusivity

First window to run under the corrected `max_cumulative_loss_usd=20`
(deployment `ed919f9c`, value flipped `10 -> 20` at 20:33:15Z). The
cumulative-loss refusal `CUMULATIVE_LOSS_LIMIT:13.64050000>=10` appears 13
times up to 20:31:25 and **zero times after**.

Arming worked: `L5_LIVE_ARMED` at 20:46:44, claim taken 20:45:00.072 at
window start, `preflight: PASSED`, `execution_mode: MAKER_ONLY`,
`max_live_orders: 1`, `max_cash_deployed_usd: 6.00`.

The economics passed:

| field | 2045z |
|-------|-------|
| `two_sided.ok` | true |
| `pair_cost` | **0.9500** (ceiling 0.96) |
| `edge_per_contract` | 0.0500 |
| `net_if_maker` | +0.056183750000 |
| `net_if_taker` | +0.015618350000 |
| `qty_per_leg` | 1.97 |
| `cash_gate_obligation_usd` | 2.9944 (bound 3.00) |
| `opening_leg_cap_binds` | false |
| `opening_notional_binds` | false |

**No order was submitted.** All 9 live decisions on 2045z carry
`key: STRATEGY_EXCLUSIVE_L5_FOLLOWING`, every one of them on a plan with
`pair_cost: 0.9500`.

### Why this blocks baseline reproduction

`src/l5_live_execution.py:6523` refuses the two-sided engine whenever
`L5_WINDOW_STRATEGY == "L5_FOLLOWING"`: that value names the L5-following
path as the sole owner of the window.

The Oct 24 baseline was produced **entirely by the two-sided engine**. In
each of the seven baseline windows the submissions carry the probe's own
`two_sided.leg_to_place`, and the complement additionally carries
`two_sided_round` and `two_sided_complement`. For 0130z:

    01:31:25  leg_to_place=UP    legs_claimed={UP:False, DOWN:False}  (opening)
    01:33:34  leg_to_place=DOWN  legs_claimed={UP:True,  DOWN:False}  round=0, complement SET

`STRATEGY_EXCLUSIVE_*` never fires anywhere in the baseline tapes, so
`L5_WINDOW_STRATEGY` was unset during the Oct 24 campaign.

Both legs of every baseline pair would be refused under today's setting.
**The baseline is unreproducible while `L5_WINDOW_STRATEGY=L5_FOLLOWING`.**

The L5-following path cannot substitute for it in these windows: its
translation resolves either `mode: TAKER` (refused under maker-only) or
`mode: MAKER, reason: STRICT_NOT_EVALUABLE`. Over the 2030z and 2045z
decisions sampled, no L5-following intent was both evaluable and maker.

This is a routing setting, not a risk control. Changing it would not touch
any exposure, loss, sizing, maker-only or cash gate. It is nonetheless an
explicit standing instruction ("Keep `L5_WINDOW_STRATEGY=L5_FOLLOWING`. Do
not switch to TWO_SIDED or unset exclusivity"), so it is **reported, not
changed**.

### Gates that behaved correctly and need no change

`TWO_SIDED_PROBE:FIRST_LEG_BELOW_MIN_PRICE` accounted for 21 of 22 decisions
on 2030z, refusing openings at own 0.14 and 0.18 against `min_own_px: 0.20`.
The live floor is 0.20 as instructed and the refusals were correct. This was
*not* the 2030z blocker in the way first reported: `ORDER:NOT_ARMED` was a
single row, and 2030z's real loss was its claim being taken at 20:30:01 by
the pre-correction process.

### Authorization state

`windows_spent: 5, windows_remaining: 3, max_windows: 8`,
`submissions_spent: 1, max_submissions: 42`, `approval_state: approved`,
`permits_this_window: true`, expiry 22:32:56Z. Remaining 15-minute
boundaries outnumber the 3 remaining windows, so the window budget binds
before the deadline does.
