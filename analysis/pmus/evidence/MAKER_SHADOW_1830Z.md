# Maker treatment, shadow over the 1830z window

Deployment `90b460e3-7075-4424-b8fd-a6b8c77709f1`, commit `929daf3`,
market `cpc-btc-updown-15m-2026-09-25-1830z`, 18:29:33 -> 18:45:12Z.

First log line, which exists because a stale flag hijacked the two previous
attempts:

    L5_MODE {"mode": "OBSERVE_ONLY", "observe_only": "YES",
             "recover_only": "", "market_pin": "", "recover_market": ""}

`armed: false`, `post_capable: false`, `transport: NULL`,
`authorization_claimed: false`, **`submitted_live: 0`**.

## Passive actions now produce resting-order intents

| | |
|---|---:|
| distinct L5 actions | 99 |
| refused STRICT_NOT_EVALUABLE | 33 |
| **maker intents produced** | **33** |
| placeable after flooring | **33** |
| unplaceable after flooring | 0 |
| by side | UP 27 / DOWN 6 |
| total intent notional | **$29.0067** |

Every action the frozen rule called MAKER and refused now yields a genuine
order intent -- side, L5's own price, a lambda-scaled quantity floored to the
0.01 increment, and `post_only: true`. Before this treatment those 33 actions
produced nothing at all.

Four sample intents, verbatim:

| L5 action | side | price | desired | executable | delta |
|---|---|---:|---:|---:|---:|
| BUY 0.5200000034 x46.674792 | UP | 0.5200000034 | 2.10911848 | 2.10 | 0.00911848 |
| BUY 0.53 x50 | UP | 0.53 | 2.25937641 | 2.25 | 0.00937641 |
| BUY 0.55 x10.41 | UP | 0.55 | 0.47040217 | 0.47 | 0.00040217 |
| BUY 0.65 x50 | UP | 0.65 | 2.25937641 | 2.25 | 0.00937641 |

The price is L5's own in every case, to the full precision he traded at --
P1 holds: never improved, never rounded toward our touch.

## Identity held

99 distinct admitted, **4 distinct refused MARKET_IDENTITY_MISMATCH**, three
distinct foreign tokens named, **0 foreign admitted**. Binding:

    slug         btc-updown-15m-1790361000
    conditionId  0x8f189a1d7676a9dfb1b2d71460f6473e51f5b2545c9b45f75b372533cc532bd5
    window       1790361000 .. 1790361900   (18:30:00Z .. 18:45:00Z)

Book: `AUTHENTICATED_WS`, 11,769 frames, 2,000 states, feed 176/176 polls,
zero failures.

## The obstruction, now with a number

**$29.0067 of intent notional against a $5 envelope, and one live order at a
time.** Those 33 intents cannot all rest: the envelope admits roughly a sixth
of them by notional, and the single-order limit admits one at a time where a
liquidity provider's whole method is many resting quotes at once.

This is the measurement the design said to take before proposing any change,
and it is now taken. It does NOT yet say the limits are wrong -- a resting
order that never fills consumes envelope without producing anything, and we
have no fill evidence at all yet. What it says is that one-live-order and $5
are the binding constraints on maker replication, and that the first real
maker probe will be measuring the fill rate of a very small number of
resting orders rather than reproducing L5's quoting behaviour.

INTENTS ONLY. Nothing was placed, nothing rested, nothing filled.
