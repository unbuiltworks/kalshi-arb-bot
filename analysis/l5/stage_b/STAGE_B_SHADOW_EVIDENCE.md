# Stage B shadow evidence — and a correction to my own Stage A framing

Deployment `62581f8e` on commit `a4e3658732bf8a7f452b9f467b20cb65ccd0a73d`,
verified disarmed: `armed false`, `owner_arm_token false`, `live_orders 0`,
`shadow_only true`, `L5_TS_COMPLETION_CROSS=NO`. No order was submitted.

## 1. The live completion path cannot produce evidence, for two reasons

Both are visible on the serving deployment:

```
L5_TS_COMPLETION {"reason":"NO_INVENTORY_TO_COMPLETE", ... "up_qty":"0","dn_qty":"0",
  "taker_policy":{"authorized":false,"reason":"OWNER_AUTHORIZATION_ABSENT"}}
```

1. `ts_completion_inputs` reads `store.gross_inventory`, which only **real
   fills** populate. `ShadowOnlyTransport` blocks submission structurally, so
   inventory is always zero and the answer is always
   `NO_INVENTORY_TO_COMPLETE`.
2. Even given inventory, the policy short-circuits at gate 2,
   `OWNER_AUTHORIZATION_ABSENT`, because crossing is correctly disabled. It
   never reaches positivity, margin, depth or outlay.

So the evidence below comes from the streams the engine **already emits 24/7
with nothing at risk**: `L5_SHADOW_DEFENSIVE_DECISION` and
`L5_SHADOW_RESIDUAL_BOOK_SAMPLE`, which carry MODELLED unmatched inventory,
its own cost basis, and `exit_price_own` — the engine's **own taker walk
against displayed depth**. Modelled inventory, real book, real executable
price.

## 2. Coverage and verdicts: 45 windows, 6,426 evaluations

| | |
|---|---|
| distinct BTC15m windows | **45** (requirement was 24) |
| policy evaluations | 6,426 |
| shadow variants | 6 |
| windows with >= 1 authorized cross | **24 of 45 (53.3%)** |
| independent opportunities (best per window+variant) | **46** |

Per-sample verdicts, from the policy's own gates:

| verdict | samples |
|---|---|
| WORST_OUTCOME_NOT_POSITIVE | 4,328 |
| **AUTHORIZED** | **1,286** |
| MARGIN_BELOW_FLOOR | 689 |
| MAKER_PATIENCE_NOT_SPENT | 95 |
| OUTLAY_ABOVE_CAP | 28 |

The gates behave as designed: two thirds of states are refused for
non-positivity, and the margin floor removes a further 689 marginal ones.

## 3. Executable depth and outlay stayed inside the caps

| quantity | p10 | median | p90 | total |
|---|---|---|---|---|
| additional outlay (USD) | 0.02 | **0.15** | 0.98 | 16.60 |
| `completed`, the certain outcome | +0.2867 | **+0.6200** | +1.2367 | +33.6057 |
| t_in_window at authorization (s) | 397.5 | 758.8 | 853.0 | — |

Every authorized outlay was inside the **$1.50** per-cross cap (28 samples
were refused for exceeding it). The 0.25 depth haircut was not binding at
these sizes: authorized quantities were 1–3 contracts against displayed
depth in the hundreds, so a quarter of displayed depth still clears the size
many times over.

Authorizations cluster **late** (median 759 s of 900). That is a property of
the sampler — the defensive path is what emits these records — and it means
this evidence speaks to late-window states, not to the whole window.

## 4. THE CORRECTION: this policy does not create edge

I framed the Stage A 2145z case as turning `-$0.94` into `+$0.84`. That is
true as a **worst-case** statement and it is misleading as an economic one,
because it compares a certainty against the *losing branch* of a gamble that
was, at that moment, far more likely to win.

The engine's own label said so all along:

> "a defensive exit bounds a tail, it does not create edge: at the touch it
> is expected-value neutral before fees and negative by exactly the fee."

Tested on 2145z, using the **current complement price** as the probability
estimate rather than the historical entry basis:

| | |
|---|---|
| held DOWN 2 @ own 0.45, complement UP @ 0.10 | |
| completed (certain) | **+0.8400** |
| maker-only WIN branch | +1.1000 |
| maker-only LOSE branch | −0.9000 |
| market-implied P(held side wins) = 1 − 0.10 | **0.9000** |
| maker-only **expected** value | **+0.9000** |
| completion − expectation | **−0.0600** |
| total fees charged | **0.0600** |

The completion gives up **exactly the fees**. On the median authorized
opportunity the figure is **−$0.05**. What it buys is the collapse of a
**$2.00-wide** gamble to zero.

**My earlier error was using the entry basis `B` as the win probability.**
`B` is what the leg cost when it filled, not what the outcome is worth now.
Substituting the current complement price — which is what the cross actually
pays — turns an apparent edge of `completed/Q` (median +$0.455/contract) into
a fee-sized loss. The apparent edge was an artifact of a stale probability.

**And the Stage A "+$0.39 modelled improvement" was luck, not expectancy.**
It was computed on *realized* outcomes across four windows. On expectancy the
same four crosses would have cost about the fees.

## 5. What the policy is actually for

A variance-reduction tool with a known, small, fee-sized cost:

- It converts an unmatched leg into a certain outcome for ~$0.05–$0.06.
- It cannot improve expected P&L, and must not be presented as doing so.
- It has real value where variance itself is the constraint: the `$5` daily
  settled-loss halt and the campaign worst-case budget are both bounds on
  *outcomes*, and a policy that removes a `$2`-wide tail for `$0.05` buys
  headroom under them.
- `L5_OPERATING_MODEL.md` already forbids the alternative reading: "Do not
  assume residual directional exposure has predictive skill." If we have no
  directional skill, we cannot expect to beat the market's own probability,
  so fee-negative-and-certain is the best a completion can be.

It should be authorized, if at all, as **risk management with a measured
cost**, not as a step toward profitability.

## 6. The single highest-impact remaining blocker: FREQUENCY

Measured from L5's own 689,999 BTC15m rows over 5,566 windows
(`L5_OPENING_INVENTORY_BEHAVIOUR.md`):

| | L5 | ours |
|---|---|---|
| distinct transactions per window | **105** (median) | **2–9** |
| windows buying both outcomes | **99.2%** | — |
| end-of-window balance, min/max | **0.851** (median) | one-sided |
| opening size | 19.59 contracts | cap **3** |
| first trade | **16 s** into the window | 30 s–5 min |
| sells | **0** of 684,275 | n/a (buy-only) |

Stage A's loss was structural adverse selection: with a single first-leg
fill, `pair = P_first + P_complement` anchors near `1 - spread`, the 0.96 cap
is unreachable, and the residual **is** the position — at 8 of 8 fills.

L5 does not beat that with pricing. It beats it with **frequency and
two-sidedness**: at ~105 transactions per window and 99.2% two-sided, a
one-sided fill is a small tail on a largely matched book. At 2–9 submissions,
the one-sided fill is the whole book. **The completion policy mitigates the
symptom; frequency removes the cause** — and only frequency can, because no
completion can create edge.

The binding constraint is configuration, not insight: `max_live_orders` = 1,
`ts_round_allocations` = 4, `ts_opening_leg_max_contracts` = 3, which caps us
near 8 submissions per window. This is also the operating model's own next
rung: **PATIENCE → FREQUENCY → SIZE → CONCURRENCY**.

No frequency, size or concurrency change is made or proposed here.

## 7. What is NOT established

- Nothing is realized. Inventory is modelled, and no order was submitted.
- Fill probability remains unmeasurable: PMUS publishes no print endpoint and
  Polymarket publishes no queue position. L5's 105 transactions say nothing
  about how many orders it posted to get them, and its prices are **fills,
  not quotes**.
- The settled per-window tally is thin: only 13 of the 45 windows have a
  venue resolution on the tape and only 18 window/variant pairs carry
  `held_side`, so the realized comparison rests on 1 joined opportunity and
  is not reported as a result. The expectancy decomposition in section 4
  needs neither, which is why it is the comparison given.
- Authorizations cluster late in the window; early-window behaviour is
  undersampled by this stream.
