# L5_FOLLOWING decision comparison — one complete BTC15m window

**Deliverable:** action-by-action comparison of L5's actual decisions against
our translated decisions, through the deployed money path, with zero
submissions.

**Window:** `cpc-btc-updown-15m-2026-09-23-1630z` (2026-09-23T16:30:00Z, 91 L5
actions over 13m03s). Four further settled windows are summarised at the end.

**Produced by:** `analysis/pmus/l5_following_compare.py --window 1630z --acquire --table`

## What was run

The SAME `FaithfulExecutor` the production runner builds —
`maker_treatment=True, maker_only=True`, `interaction={}` — over observer rows
already on disk (`analysis/pmus/evidence/observer_rows_settled_windows.jsonl`).
No new strategy, service or executor. Every decision, translation and refusal
below comes from the deployed path, not from a model of it.

Limits are the deployed ones, read from the money path's own accessors on
deployment `240af6ca` (`L5_EFFECTIVE_RUNTIME_CONFIG`, 2026-10-26T02:05:11Z):
`window_strategy` effective **L5_FOLLOWING**, `max_live_orders` effective
**4**, `shadow_only` **true**, aggregate exposure **$5**, daily loss halt
**$5**, maker pair ceiling **0.96**, opening notional **$0.80**, worst-case
deficit **$2.00**.

`band` is `None`, as deployed: the live `SafetyLimits` defaults it to None and
the frozen study module's `(0.20, 0.80)` is that module's own default. An
earlier run of mine passed it and manufactured 31 `BAND` refusals the deployed
engine does not make. Those are not in this record.

## Zero submissions — three independent prohibitions

1. `NullTransport.armed is False`; `submit` raises `NotArmed` and the order
   stage records an INTENT. `summarize()` reports **`submitted_live: 0`** for
   every window below.
2. The container that produced this has no route to the venue:
   `api.polymarket.us` and `data-api.polymarket.com` both refuse CONNECT
   (HTTP 000). Only the public read-only gateway resolves.
3. Production side, independently: `l5-pmus-live` heartbeat
   2026-10-26T02:19:30Z reports `armed: false`,
   `submission_gate.blocked: true`, `reason: SHADOW_ONLY_LOCK`; and the
   separate `l5-halt-watchdog` reports `open_orders: 0`, `read_error: null`,
   `cancel_enabled: true` every ~20s.

`L5_SHADOW_ONLY` was not changed, the executor was not armed, and no order was
submitted.

## Result for 1630z

| | count | share |
|---|---|---|
| L5 actions (L5_NATIVE, unfiltered) | 91 | — |
| our MAKER intents (`would_submit`) | 29 | 31.9% |
| refused `SAFETY_OVERLAY / MAKER_ONLY_NO_TAKER` | 45 | 49.5% |
| refused `PMUS_TRANSLATION / NO_BOOK` | 17 | 18.7% |
| **`submitted_live`** | **0** | — |

L5's own sequence: 1 `open`, 70 `extend`, 20 `complete`; 71 UP, 20 DOWN —
the OPEN → EXTEND → COMPLETE arc, with extends dominating.

### The 45 refusals are all crossing actions, by a median of one cent

| L5 price vs our own touch | verdict | n |
|---|---|---|
| crossing | MAKER_ONLY_NO_TAKER | 45 |
| crossing | NO_BOOK | 7 |
| restable | WOULD_SUBMIT | 29 |
| restable | NO_BOOK | 2 |

`L5_px − our_touch` on those 45: min **0.0000**, median **+0.0100**, max
**+0.0700**; 23 of 45 (51.1%) are at or within one cent of our own touch.
Across all five settled windows (120 such refusals): median **+0.0100**,
p90 +0.0700, max +0.1600, and **68 of 120 (56.7%)** within one cent — of
which **30 are `complete` legs**, the leg we have most trouble filling.

### Inventory and cash

The disarmed run commits nothing, because nothing fills — so the gates that
read inventory and cash never engage, and this run cannot speak to them. The
acquisition counterfactual (fills **assumed**, not evidence) does:

| | reproduced | committed |
|---|---|---|
| venue only, our caps off | 45 of 91 | $17.1635 |
| with our deployed $5 envelope | 8 of 91 | $4.9988 |

The $5 cap is exhausted to the cent after 8 of the 45 venue-executable
actions; 37 further actions refuse `MAX_COMMITTED_USD`. Following this one
window at λ = 1 share at L5's median size needs about **$17.16** of
commitment. `max_live_orders` never bound in either run.

## All five settled windows

| window | L5 actions | would_submit (all MAKER) | MAKER_ONLY_NO_TAKER | NO_BOOK | submitted_live | intents > 0.96 |
|---|---|---|---|---|---|---|
| 1615z | 49 | 31 | 12 | 6 | 0 | 0 |
| 1630z | 91 | 29 | 45 | 17 | 0 | 4 |
| 1645z | 50 | 22 | 23 | 5 | 0 | 0 |
| 1730z | 77 | 34 | 25 | 14 | 0 | 0 |
| 1930z | 27 | 9 | 15 | 3 | 0 | 0 |
| **total** | **294** | **125 (42.5%)** | **120 (40.8%)** | **45 (15.3%)** | **0** | **4** |

## The single highest-impact finding

**Our own `maker_only=True` policy, not the venue and not our capital, refuses
40.8% of L5's actions — and it refuses them by a median of one cent.**

`MAKER_ONLY_NO_TAKER` is a policy refusal, not a venue inability: the book was
two-sided on all 120, and L5's price was at or above our touch by a penny at
the median. L5's sequence depends on crossing by a tick roughly half the time,
including on 30 `complete` legs. A maker-only engine cannot reproduce that arc
at any capital level, which is why raising `max_live_orders` to 4 changed
nothing here: order slots were never the binding constraint in any window.

Caveats held open deliberately:

- Four of the 29 intents in 1630z price above the deployed 0.96 maker pair
  ceiling (0.97–0.981 `complete` legs). This run did not attach
  `ValidationGates`, which the runner does, so those four would face a further
  refusal live. 29 is an upper bound.
- The book here is the public gateway at our receipt. Authenticated books
  exist only for `2026-09-23-2230z`, which has no L5 observer rows, so an
  authenticated-book version of this comparison is not possible from data on
  disk.
- No realized P&L appears anywhere in this record, because no order was
  submitted. Nothing above is profit of any kind.

## Full per-action table (1630z)

`t+s` is seconds after window open. `our_px` is the intent price, which for a
MAKER translation is always L5's own price (zero slippage by construction).

```
#    t+s     rel     side  L5_px     L5_size    our    our_px    verdict
1    24.0    open    UP    0.65      10         -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
2    25.0    extend  UP    0.62      15         -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
3    27.0    extend  UP    0.6200000 5.394737   -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
4    39.0    extend  UP    0.68      93.75      -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
5    60.0    complet DOWN  0.27      76.22      -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
6    79.0    extend  UP    0.68      100        -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
7    151.0   extend  UP    0.62      15         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
8    175.0   extend  UP    0.6500000 72.942858  -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
9    250.0   extend  UP    0.63      0.06       MAKER  0.63      WOULD_SUBMIT (NOT_ARMED)
10   250.0   extend  UP    0.6300000 5.394595   MAKER  0.6300000 WOULD_SUBMIT (NOT_ARMED)
11   250.0   extend  UP    0.6300000 6.513514   MAKER  0.6300000 WOULD_SUBMIT (NOT_ARMED)
12   250.0   extend  UP    0.6300000 6.945946   MAKER  0.6300000 WOULD_SUBMIT (NOT_ARMED)
13   259.0   extend  UP    0.6600000 52.411765  -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
14   286.0   complet DOWN  0.36      100        MAKER  0.36      WOULD_SUBMIT (NOT_ARMED)
15   294.0   extend  UP    0.5900000 5.121952   MAKER  0.5900000 WOULD_SUBMIT (NOT_ARMED)
16   298.0   extend  UP    0.5800000 91.571429  MAKER  0.5800000 WOULD_SUBMIT (NOT_ARMED)
17   298.0   extend  UP    0.59      100        MAKER  0.59      WOULD_SUBMIT (NOT_ARMED)
18   351.0   extend  UP    0.34      15         MAKER  0.34      WOULD_SUBMIT (NOT_ARMED)
19   352.0   extend  UP    0.33      70         MAKER  0.33      WOULD_SUBMIT (NOT_ARMED)
20   388.0   extend  UP    0.38      25         MAKER  0.38      WOULD_SUBMIT (NOT_ARMED)
21   402.0   complet DOWN  0.54      42.17      -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
22   408.0   complet DOWN  0.5600000 45.454546  -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
23   408.0   complet DOWN  0.5600000 30.454546  -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
24   414.0   extend  UP    0.41      80         MAKER  0.41      WOULD_SUBMIT (NOT_ARMED)
25   429.0   complet DOWN  0.5400000 16.739131  MAKER  0.5400000 WOULD_SUBMIT (NOT_ARMED)
26   460.0   complet DOWN  0.5400000 37.804348  MAKER  0.5400000 WOULD_SUBMIT (NOT_ARMED)
27   460.0   complet DOWN  0.55      100        -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
28   507.0   extend  UP    0.34      30         MAKER  0.34      WOULD_SUBMIT (NOT_ARMED)
29   507.0   extend  UP    0.3400001 3.030302   MAKER  0.3400001 WOULD_SUBMIT (NOT_ARMED)
30   508.0   extend  UP    0.34      2          MAKER  0.34      WOULD_SUBMIT (NOT_ARMED)
31   513.0   complet DOWN  0.6300000 10.353514  -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
32   513.0   complet DOWN  0.64      100        -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
33   522.0   complet DOWN  0.56      100        MAKER  0.56      WOULD_SUBMIT (NOT_ARMED)
34   541.0   extend  UP    0.2400000 30.394737  -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
35   541.0   extend  UP    0.24      30.4       -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
36   543.0   extend  UP    0.2400001 1.447366   -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
37   543.0   extend  UP    0.2400000 14.855262  -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
38   543.0   extend  UP    0.24      22.89      -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
39   556.0   extend  UP    0.21      35         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
40   556.0   extend  UP    0.21      30         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
41   558.0   extend  UP    0.21      35         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
42   559.0   complet DOWN  0.77      100        MAKER  0.77      WOULD_SUBMIT (NOT_ARMED)
43   562.0   extend  UP    0.2       18         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
44   564.0   extend  UP    0.19      21.91      -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
45   564.0   extend  UP    0.19      78.09      -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
46   564.0   extend  UP    0.2       82         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
47   565.0   complet DOWN  0.81      100        MAKER  0.81      WOULD_SUBMIT (NOT_ARMED)
48   567.0   extend  UP    0.16      79.97      -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
49   567.0   extend  UP    0.16      20.03      -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
50   582.0   extend  UP    0.12      25         MAKER  0.12      WOULD_SUBMIT (NOT_ARMED)
51   583.0   extend  UP    0.11      47.7       MAKER  0.11      WOULD_SUBMIT (NOT_ARMED)
52   595.0   extend  UP    0.09      10         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
53   595.0   extend  UP    0.1       3.07       -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
54   597.0   extend  UP    0.09      10         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
55   597.0   extend  UP    0.0900000 5.109888   -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
56   597.0   extend  UP    0.09      10         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
57   598.0   extend  UP    0.09      10         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
58   598.0   extend  UP    0.09      9.61       -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
59   598.0   extend  UP    0.09      10         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
60   598.0   extend  UP    0.09      10         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
61   603.0   extend  UP    0.07      13.19      -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
62   603.0   extend  UP    0.0700000 1.075257   -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
63   603.0   extend  UP    0.07      5          -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
64   603.0   extend  UP    0.07      5          -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
65   603.0   extend  UP    0.07      5          -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
66   604.0   extend  UP    0.0700000 6.064514   -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
67   606.0   extend  UP    0.08      100        -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
68   607.0   extend  UP    0.07      10         -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
69   607.0   extend  UP    0.0700000 11.079128  -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
70   609.0   extend  UP    0.0700000 31.462357  -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
71   613.0   extend  UP    0.0700000 10.752685  -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
72   616.0   extend  UP    0.07      1.35       -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
73   630.0   extend  UP    0.05      21.9       MAKER  0.05      WOULD_SUBMIT (NOT_ARMED)
74   633.0   extend  UP    0.05      1.05262    MAKER  0.05      WOULD_SUBMIT (NOT_ARMED)
75   634.0   extend  UP    0.05      77.04      MAKER  0.05      WOULD_SUBMIT (NOT_ARMED)
76   657.0   extend  UP    0.03      100        -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
77   658.0   extend  UP    0.02      35.51      -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
78   658.0   extend  UP    0.02      64.48975   -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
79   660.0   extend  UP    0.02      5.1        -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
80   660.0   extend  UP    0.02      34.48975   -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
81   661.0   extend  UP    0.02      2.19975    -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
82   663.0   extend  UP    0.02      97.8       -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
83   666.0   complet DOWN  0.98      22         -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
84   666.0   complet DOWN  0.98      5          -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
85   688.0   extend  UP    0.01      50         -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
86   688.0   extend  UP    0.01      20.57      -      -         REFUSED PMUS_TRANSLATION/NO_BOOK
87   735.0   complet DOWN  0.99      1.01       -      -         REFUSED SAFETY_OVERLAY/MAKER_ONLY_NO_TAKER
88   793.0   complet DOWN  0.981     100        MAKER  0.981     WOULD_SUBMIT (NOT_ARMED)
89   798.0   complet DOWN  0.971     70         MAKER  0.971     WOULD_SUBMIT (NOT_ARMED)
90   807.0   complet DOWN  0.97475   60         MAKER  0.97475   WOULD_SUBMIT (NOT_ARMED)
91   807.0   complet DOWN  0.97      100        MAKER  0.97      WOULD_SUBMIT (NOT_ARMED)

```
