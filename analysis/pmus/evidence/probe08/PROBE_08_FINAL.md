# Probe 08b — the repair side filled half the time, and only when it was expensive

Market `cpc-btc-updown-15m-2026-09-26-1130z`, session
`l5probe-20260926-btc15m-08b`, account `d51b44a3fa83`, deployment `b1e6a69d`
(commit `46a71b1`). Treatment `IMBALANCE_INCREASE_SUPPRESSED`, maker-only.

## Evidence, and that it was not touched

The live run crashed in `report()` at 11:43:33Z, after `close_out()` and
`auth.consume()` had already run — they are in the `finally` block. The
window's only record is two files on the volume, extracted read-only by
deployment `98d39cae`:

| artifact | sha256 | bytes | unchanged after |
|---|---|---|---|
| `l5_probe08b_journal.jsonl` | `e8e5dd6c…b95e` | 223,207 | **yes** |
| `l5_probe08b_inventory.json` | `e831c963…4ae6` | 3,239 | **yes** |

Reconciliation ran against a byte-identical copy; the recovery wrote to
separate paths. `all_unchanged: true` is a measurement taken after the run,
not a promise. Both deployment logs are captured locally and hashed.

## PRIMARY ENDPOINT

| seq | side | class | own px | qty | filled | venue state |
|---|---|---|---:|---:|---:|---|
| 1 | DOWN | FLAT_OPEN | 0.41 | 1.68 | 1.68 | FILLED |
| 2 | DOWN | FLAT_OPEN | 0.47 | 2.25 | 2.25 | FILLED |
| 3 | UP | REDUCE_RESIDUAL | 0.56 | 0.42 | **0** | NEW |
| 4 | UP | REDUCE_RESIDUAL | 0.60 | 2.25 | **0** | NEW |
| 5 | UP | REDUCE_RESIDUAL | 0.61 | 2.25 | **0** | NEW |
| 6 | UP | REDUCE_RESIDUAL | 0.75 | 2.25 | 2.25 | FILLED |
| 7 | UP | REDUCE_RESIDUAL | 0.76 | 2.25 | 2.25 | FILLED |
| 8 | UP | REDUCE_RESIDUAL | 0.80 | 0.22 | 0.22 | FILLED |

    REDUCE_RESIDUAL   placed 6 | partially filled 0 | fully filled 3
                      3/6 orders (50.0%), 4.72/9.64 shares (49.0%)

**And the half that filled is the wrong half.** The three cheap repairs —
0.56, 0.60, 0.61 — rested and never filled. The three that filled were 0.75,
0.76 and 0.80. The repair side fills when the market has already moved
against you, which is adverse selection, and it is the exact threat named
before the probe ran: *"we would be resting on the harder side, and our fill
rate there is unmeasured."* It is measured now, on one window.

## Suppression, and the gap it did not close

`IMBALANCE_INCREASE_SUPPRESSED` **8**, zero leaked. 53 actions:

| class | actions | submitted | suppressed |
|---|---:|---:|---:|
| FLAT_OPEN | 2 | 2 | 0 |
| REDUCE_RESIDUAL | 9 | 6 | 0 |
| INCREASE_RESIDUAL | 8 | 0 | **8** |
| UNCLASSIFIED (refused `MAKER_ONLY_NO_TAKER` before classification) | 34 | 0 | 0 |

Other refusals: `MAKER_ONLY_NO_TAKER` 34, `MAX_USD_PER_MARKET` 3.

**The treatment permitted the imbalance it exists to prevent.** Both DOWN
opens carry `residual_before: "0"` and were classified `FLAT_OPEN` — the
second one because the first had not yet *filled*. The rule reads REALIZED
residual, as specified, so two same-side orders submitted inside the same
unfilled window both look like opening from flat. That built the −3.93
imbalance the rest of the window then failed to repair. This is a design gap
in the rule as written, not a bug in its implementation.

## Inventory trajectory and economics

Gross **UP 4.72 / DOWN 3.93** → matched **3.93**, terminal residual **0.79
UP**. Settlement `MARKET_STATE_EXPIRED`, `settlementPx 1.0000` (UP won).

| | |
|---|---:|
| matched qty | 3.93 |
| matched acquisition cost | 4.7106 |
| matched redemption | 3.9300 |
| **matched P&L** | **−0.7806** |
| **average pair cost** | **1.1986** |
| residual qty | 0.79 UP |
| residual cost | 0.6092 |
| **residual P&L** | **+0.1808** |
| maker rebates | **+0.0400** (4 of 5 fills) |
| **REALIZED NET** | **−0.5598** |

Identity: `redemption 4.72 − spend 5.3198 + rebates 0.0400 = −0.5598`.

The matched book was bought at **$1.1986 a pair** against a $1.00 redemption
— worse than Probe 07's already-bad $1.0299, and far from the ≤0.96 spring
discipline. The residual *made* money, but only because UP won; that is
variance, not skill.

## Risk

Peak `risk_exposure_usd` **4.7849** against the hard **$5.00** gate —
**within**. Trajectory: 0.9912 → 2.1837 → 1.9989 → 1.0989 → 1.2114 → 2.8989
→ 4.6089 → **4.7849**. The engine then refused three further
REDUCE_RESIDUAL actions `MAX_USD_PER_MARKET`, which is its own statement
that the bound bit. Reconstructed with the production risk function from
venue-confirmed fills and the recorded resting orders; the per-action values
the engine itself computed are in the frozen journal.

At close-out `open_orders_at_venue: 0` and all three resting orders were
released. No live exposure survived the window.

## Versus Probe 07 (execution behaviour, not P&L)

| | Probe 07 NATIVE | Probe 08b TREATED |
|---|---:|---:|
| orders submitted | 9 | 8 |
| fully filled | 9 (100%) | 5 (62.5%) |
| repair-side fill rate | n/a | **3/6 (50%)** |
| maker fills / taker fills | 9 / 0 | 5 / 0 |
| maker rebate | +0.01 | +0.04 |
| peak risk_exposure_usd | 1.3800 | 4.7849 |
| average pair cost | 1.0299 | 1.1986 |
| terminal residual | 2.81 DOWN | 0.79 UP |
| realized | −0.77180 | −0.5598 |

Probe 08 ended less negative than Probe 07. **That is not evidence the
treatment worked.** The residual was smaller and happened to settle the
right way; the matched book — the part whose economics are locked at fill
time and cannot be luck — got *worse*, from 1.0299 to 1.1986 a pair. One
window each, and the settlement direction is noise.

## What is not established

* Balance before/after is **not yet read back**. Expected 62.25745 →
  61.69765 on a −0.5598 net; unconfirmed until an authoritative balance read.
* Two windows are not a fill rate. 3/6 is a number, not a distribution.
* The crash reached the venue open-order adoption path for the first time in
  the programme, which is lifecycle evidence that the treatment changed
  behaviour — and says nothing about whether complement repair earns.
