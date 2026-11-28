# Arming the four-lane 5-minute fleet

Status 2026-11-16. Source: `src/l5_btc5m_execution.py` (`account_lanes`,
`lane_cap`), `src/l5_5m_fleet.py` (`child_env`), `arm_campaign.py`.

## What was broken

Under `L5_BTC5M_FOREIGN_EXPOSURE=REPORTED` the account ceiling is checked
against `own_usd`, which each lane reads from **its own ledger**. Every
lane of the fleet has a separate ledger — that separation is what stops
two assets overwriting each other's orders — so the ETH lane saw none of
the BTC lane's inventory. Four lanes each measured themselves against the
whole $17 ceiling and could commit **4x it** between them.

The venue's own buying power was the only thing left bounding the account,
and it is a race: four lanes can read it, all pass, and all submit.

## The fix

`L5_ACCOUNT_LANES` declares how many lanes share the ceiling, and each
lane enforces `cap / lanes`:

| lanes | $17 ceiling | a lane may risk |
|---|---|---|
| 1 | 17.00 | 17.00 |
| 2 | 17.00 | 8.50 |
| 4 | 17.00 | 4.25 |

- **Unset is one lane**, so a single executor is byte-for-byte unchanged.
- **A value that does not parse refuses** rather than defaulting to 1,
  because 1 is the permissive answer.
- **Dividing by one returns the ceiling untouched** — not an optimisation:
  a $0.0986 ceiling admitting exactly one 0.58 x 0.17 leg is a pinned
  case, and flooring a single lane's share to $0.09 would refuse an order
  the owner's ceiling allows.
- `l5_5m_fleet.child_env` sets it from the child count, so the lanes
  cannot disagree, and a stale service variable cannot out-vote it.
- **`CHARGED` keeps the undivided ceiling on purpose.** There a sibling
  lane *is* foreign inventory and is already counted at $1 a contract;
  dividing as well would charge it twice.

## Per-asset arming

One invocation arms every lane:

```
python3 -I analysis/l5/btc5m_accumulation/arm_campaign.py \
    --strategy BTC5M,ETH5M,SOL5M,XRP5M --shares 1 --windows 4 [--go]
```

The lanes are children of one service, so they **share** `L5_BTC5M_LIVE`,
`L5_BTC5M_NOT_AFTER` and the strategy parameters. Arming one at a time
would refuse the second ("already armed"), and a second deploy would
orphan a window already in flight. So: N approvals, the shared variables
once, one deploy.

What is per-asset is exactly the approval document — one document names
one series — written as `L5_CAMPAIGN_APPROVAL_<STRATEGY>`, the name
`child_env` hands that child. A lane with no approval of its own refuses
every order, which is the intended failure.

The first lane's window start is **pinned for the rest**: each generation
otherwise picks its own next boundary, so a run crossing a 5-minute
boundary partway through would approve different windows per asset, and
the shared `NOT_AFTER` would be wrong for at least one of them.

## THE BOTTLENECK: the fleet is not armable at a useful size

Not the cap split — the `$1/share` cost-basis overstatement
(`COST_BASIS_OVERSTATEMENT.md`) against the $11 daily-loss room. The
ledger books a losing leg at $1.00/share rather than its real $0.08, and
the **account-wide** daily halt is judged on the booked figure, so N lanes
book N times as much against one room.

Measured against an $11 room and a $17 cap:

| config | result | booked |
|---|---|---|
| 4 lanes, 1 share, 4 windows | refused | 16.00 |
| 4 lanes, 1 share, 2 windows | refused | 15.36 |
| 3 lanes, 1 share, 4 windows | refused | 12.00 |
| **2 lanes, 1 share, 4 windows** | **armable** | 8.00 |
| **1 lane, 2 shares, 4 windows** | **armable** | 8.00 |
| 1 lane, 4 shares, 4 windows | refused | 16.00 |

Real risk in the refused four-lane case is **$0.64 a lane**. The halt is
tripping on a 12.5x phantom.

Four lanes needs one of:

1. the cost basis fixed so legs book at their real entry price — then
   four lanes at 2 shares is $2.56 of real worst case and fits trivially;
2. more daily-loss room, which is an owner decision, not a code change.

Raising the room to make a phantom fit would be increasing risk to make
results look better. Fix the basis instead.

## Still required before the fleet can run

- the service start command must become `python3 -u -m src.l5_5m_fleet`
  (it currently starts a single `l5_btc5m_runner`). Money-capable
  deployment change — owner approval required.
- per-lane approved daily loss is still `DAILY_LOSS` each. The
  account-wide halt is what binds across lanes; the per-lane figure is
  not divided, and that is deliberate until an owner decides otherwise.
