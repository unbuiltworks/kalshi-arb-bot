# PMUS BTC5m — verified fee/rebate economics, and whether any scaled
# 0x8dxd-style policy clears them

**Production disarmed. No executor or parameter changed. No straddle
parameter touched to increase fills. No infrastructure added. The
probability model stays non-voting. Nothing armed.**

---

## Priority 1 — our account's actual PMUS economics, read live from the venue

Three sources, and they **disagree with each other**, which matters:

| source | taker coefficient | maker rebate |
|---|---|---|
| our code (`taker_theta`) | **0.0695** | — |
| `docs.polymarket.us/fees`, vendored in `pmus_incentive_audit.py` | **0.06** | 0.0125·C·p·(1−p) at fill |
| **the live market object, read 2026-11-16** | **0.095** | **no rebate field at all** |

`GET /v1/market/slug/cpc-btc-updown-5m-2026-11-16-*` returns
`feeCoefficient: 0.095`, `orderPriceMinTickSize: 0.01`,
`minimumTradeQty: 0.01`. The live figure is **37% above what our code
assumes and 58% above the vendored docs.** Any EV computed with 0.0695
understates taker cost on this instrument today.

### There is no rebate to earn on BTC5m

`GET /v1/incentives` returns **100 published programs. Zero cover any
BTC or updown market.** They are 80 `stsc` (sports, $810 of pool) and 20
`kpic` (earnings, $5,000 of pool). The BTC5m market object declares no
rebate, reward or incentive field.

Our account's historically **credited** rebate — `$2.1300 over 293
fills`, a rate of `0.384%/$` recorded in `l5_capital_efficiency.py` and
`venue_expectancy_ranking.py` — was earned on **sports**, which is where
the programs are. **It does not transfer to BTC5m**, and it was never a
BTC5m number.

### And at our clip size the published rebate rounds to nothing anyway

Even granting the vendored 0.0125 coefficient, it pays
`0.0125·(1−p)` per dollar of notional and is rounded to the cent with
banker's rounding:

| clip | p | raw rebate | **paid after rounding** |
|---|---|---|---|
| $2.00 | 0.44 | $0.01400 | $0.01 (50 bps) |
| $2.00 | 0.97 | $0.00075 | **$0.00** |
| $2.00 | 0.99 | $0.00025 | **$0.00** |
| $2.25 | 0.99 | $0.00028 | **$0.00** |

**The 97–99¢ strategy earns exactly zero rebate at our clip size**, in
addition to there being no programme to pay one. The rebate only has any
magnitude at mid prices (62–70 bps raw at p≈0.44–0.50) — which is the
region the 97–99¢ policy never trades.

For contrast, the taker fee at the live 0.095 coefficient is
`0.095·(1−p)` per dollar of notional: **28.5 bps at 0.97**, 9.5 bps at
0.99, and **532 bps at 0.44**.

## The biggest source of trading losses

Measured on the August era, which is the only era whose cost basis
reconciles (no sells, no SPLIT, 851 of 860 windows verified):

| price band | fills | contracts | win % | net | return |
|---|---|---|---|---|---|
| ≥0.995 | 968 | 186,260 | 100.0% | +$187.52 | +0.10% |
| 0.95–0.99 | 2,487 | 382,545 | 99.7% | **+$46.32** | **+0.01%** |
| 0.80–0.95 | **6** | 4,263 | 83.3% | **−$644.66** | **−16.50%** |
| <0.20 | **3** | 2,010 | 33.3% | **−$182.89** | −94.82% |

**Nine fills priced below the 0.95 band lost −$827.55, erasing the
+$233.84 that 3,455 fills at 0.95+ earned, and more.** By timing, the
fills at `t ≥ 270` — 3,221 of them — returned **−0.15%**, while the
smaller set at t 180–270 returned +0.55%.

So the loss source is not the core trade; it is **a handful of large
entries taken when the "favorite" was not actually near-certain**. And
the core itself (0.95–0.99) returned **+0.01%, indistinguishable from
zero before any fee** — against a 28.5 bps taker cost on our venue.

## Priority 2 — on-chain Polygon reconstruction: BLOCKED, and of limited
## decision value

`polygon-rpc.com`, `rpc.ankr.com/polygon` and `api.polygonscan.com` are
**all refused by this environment's network policy** (connection refused,
not an HTTP error). The allowlist holds the Polymarket and Kalshi hosts
only. I have **not** proposed a network change, for a reason worth
stating plainly rather than burying:

0x8dxd traded **Polymarket international** (`clob.polymarket.com`,
Polygon). Its declared schedule is
`feeSchedule {"rate": 0.07, "takerOnly": true, "rebateRate": 0.2}` —
**a maker there pays no fee at all**. Our lane trades **PMUS**, where the
live coefficient is 0.095 and **no BTC5m rebate programme exists**. A
perfect March reconstruction would therefore establish what was
profitable *on a venue whose maker economics we do not have*. It cannot
establish that the policy clears our costs, which is the open question.

What the reconstruction **would** settle is whether the March two-sided
strategy was profitable at all, by recovering the SPLIT mint cost. That
is worth knowing and I will do it on request — it needs
`polygon-rpc.com` and `api.polygonscan.com` added to the environment's
allowlist, which is your approval to give. I am not asking for it
speculatively.

### Why the March numbers cannot be rescued without it

Observed BUY decisions held to settlement total **+$121,259** across the
tape, and every March price band reads positive (+5.72% at 0.20–0.40,
+20.22% below 0.20). **That is the same understated-cost artifact, not a
result.** Window-level P&L on verified cost basis is **−$39,585**. The
two disagree by roughly the missing SPLIT mint cost: inventory minted at
$1.00 per pair and then sold cheaply makes the surviving buy legs look
cheap in isolation. The buy-side decomposition and the window
decomposition cannot both be right, and the one with the missing cost is
the optimistic one.

## Priority 3 — does any scaled policy clear our costs?

**No, and I am saying so rather than hedging.**

1. **The 97–99¢ late-favorite policy: no.** Its author's own 860 windows
   net **−$545.86**. Its core band returns **+0.01%** gross of fees. On
   our venue it pays **28.5 bps of taker fee** at 0.97 and earns **$0.00
   of rebate** at our clip. There is no arithmetic under which +1 bp
   covers 28.5 bps. It also sits at `t≥270`, which our `CANCEL_LEAD_S=30`
   sweep excludes by design.
2. **The March two-sided policy: not demonstrated, and its one confirmed
   margin does not exist on our venue.** Its pair economics are genuinely
   attractive where measurable (median completed pair **0.9342**, 62.2%
   at or below 0.96), but: its trading P&L on verified cost basis is
   **−$39,585**; its author's confirmed income was **$49,435.87 of
   referral reward** — not a trading strategy and not available to us —
   plus **$13,537.44 of maker rebate at 13.42 bps on a venue where makers
   pay no fee**; and **PMUS pays no BTC5m rebate at all**. Our own live
   shadow on this venue has observed theoretical pair costs of **1.0625,
   1.0725, 1.0725, 1.0367 — every one above par**, i.e. completing lost
   4–7¢ before any incentive.
3. **Scaled to our unchanged limits it does not become viable.** At
   $4.50/window the published-rate rebate would be ~$0.03/window at mid
   prices and $0.00 at 97–99¢, against a per-window edge that is
   unmeasured on PMUS BTC5m and above par where we have looked.

**Conclusion: on PMUS BTC5m, neither historical 0x8dxd strategy has a
demonstrated edge, and the incentive structure that made the two-sided
era pay its author is absent on our venue for this instrument.** Fill
count is not evidence of edge, and account-level referral income is not
evidence of a strategy.

## One actionable next step

**Fix the fee coefficient our EV gate uses, then re-ask the question on
the instrument our venue actually pays for.**

The immediate, zero-risk action is that our code prices taker cost at
**0.0695 while the venue declares 0.095 today**. Every EV number this
program has produced for BTC5m is computed against a coefficient the
venue does not charge. That is a correctness defect in the gate, not a
tuning choice, and it should be read live per market the way
`l5_shadow/terms.py` already does for Polymarket — refusing when the
venue stops declaring it rather than falling back to a remembered rate.

The strategic follow-on, which needs no new infrastructure: the 100
published programmes name exactly where PMUS pays for liquidity —
**sports (`stsc`) and earnings (`kpic`)**, with declared `targetSize`,
`maxSpread` and `rewardPool`. Our account has already been credited
`0.384%/$` there. If the 0x8dxd two-sided *structure* is worth anything
to us, that is where its paid-liquidity-provider economics exist on our
venue, and it is measurable read-only from the existing collectors.
