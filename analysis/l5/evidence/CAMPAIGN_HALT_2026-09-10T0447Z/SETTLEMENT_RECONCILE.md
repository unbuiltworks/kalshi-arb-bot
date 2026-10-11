# Settlement reconcile after the halt — venue-confirmed, 2026-09-10T12:00:50Z

Deployment `9c94205c`, commit `346e9e11`, `verdict SHUTDOWN_VERIFIED`, disarmed
throughout. Source: the `shutdown_idle.settlement` row, read-only.

## Steps 4–5, from a fresh venue read

```
sweep            {seen: 0, cancelled: [], foreign: [], errors: []}
still_open_ours  []
foreign_open     []
held_preserved   []
```

Open orders zero, ours and foreign reported separately, both empty, from a
fresh `/v1/orders/open` read rather than the sweep's own return. Nothing was
resting. Nothing is held.

## Step 6 — every pending market resolved

`pending_before` 6, `settled_now` 6, `still_pending` **0**, `errors` **0**.

| market | fills | YES / NO | cost | pair cost | venue realized |
|---|---|---|---|---|---|
| `tsc-nfl-ne-sea-…-24pt5` | 7 | 16.53 / 16.53 | 14.7275 | 0.8910 | *unconfirmed* |
| `tsc-nfl-ne-sea-…-28pt5` | 6 | 15.95 / 15.95 | 15.9430 | **0.99956** | *unconfirmed* |
| `tsc-nfl-ne-sea-…-26pt5` | 9 | 16.72 / 16.72 | 14.5480 | 0.8701 | *unconfirmed* |
| `tsc-mls-laf-nyr-…-3pt5` | 3 | 16.70 / 0 | 7.3470 | — | **−7.3470** |
| `aec-cs2-all-tyloo-…` | 6 | 12.69 / 12.69 | 12.2628 | 0.9663 | *unconfirmed* |
| `aec-itfwo-hinino-vaiadk-…` | 3 | 14.97 / 0 | 6.5244 | — | **+8.4456** |

All 31 fills are maker; **taker_fills is 0 on every market**.

Four of six report `realized_confirmed: false` — *"unconfirmed: a trade lacks
the venue's realizedPnl"*. Their matched contributions (+1.8025, +0.0070,
+2.1720, +0.4272) are **DERIVED from ledger fills and are not realized**. They
sum to +4.4087 and that number must not be spent, quoted as profit, or added
to anything. It is recorded here because the venue withheld its own figure,
not because we have an opinion about it.

`campaign_end` reported `pending: 17`; `unsettled_slugs()` — filled-but-not-
settled from the durable ledger — found **6**. Those count different things
(the campaign's own settlement list vs. markets carrying unsettled fills), and
I have not reconciled the difference. Six is what had money in it.

## Step 7 — the budget as it now stands

```
realized_today_usd                 -0.1533
unsettled_floor_usd                 0
combined_loss_usd                  -0.1533
combined_budget_usd               -15.00
budget_permits_normal_operation     true
```

**The −$15 brake would currently permit normal operation**, with $14.85 of
headroom. The unsettled floor is zero because nothing is held: the $6.52 that
tripped the budget was that one residual's worst branch, and the branch
resolved.

## The finding that matters more than the budget

The two markets the venue *did* confirm are the two **one-sided residuals**,
and between them they moved more money than every matched pair combined:

```
aec-itfwo-hinino-vaiadk   14.97 UP, $6.5244  ->  LONG resolved   +8.4456
tsc-mls-laf-nyr-3pt5      16.70 UP, $7.3470  ->  SHORT resolved  -7.3470
                                                        net      +1.0986
```

One paid the full best branch, one lost the entire cost basis. That is the
directional-residual lottery `L5_OPERATING_MODEL.md` says has no demonstrated
predictive skill, and here it is the dominant term in both directions. The
14.97 position winning is **not** evidence the halt was harmless or that
holding unhedged residuals is sound — the MLS market is the same shape with
the opposite outcome, drawn from the same distribution.

Against that, the matched pairs — the part of the strategy that is supposed to
be economic — produced derived contributions of at most a few dollars on
$57.48 of cost basis, and `…-28pt5` completed at a pair cost of **0.99956**:
$15.94 deployed to earn a derived $0.0070. The ≤1.00 ceiling held, and it held
at the very edge, where a pair is legal and worth nothing.

## Not done

Not re-armed. `PMUS_SPRING_SHUTDOWN` remains `1`.
