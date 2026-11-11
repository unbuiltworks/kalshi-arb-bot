"""L5's OWN markets are live, open to us, and still carry the reward
program. The only thing between us and running his engine is capital.

Measured 2026-09-09T01:57Z against the public Polymarket APIs. Read-only:
every call was an unauthenticated GET to gamma-api / clob.polymarket.com.

--- THE FINDING ---
We have spent this session ranking substitutes -- Kalshi commodity legs,
PMUS sports -- while L5's actual venue sat there accepting orders. It does.
His exact slug pattern resolves to live markets right now:

    btc-updown-15m-<epoch>   and   eth-updown-15m-<epoch>

rolling every 15 minutes, acceptingOrders true, liquidity $1,944-$20,441
per market, and a PAIR COST OF 1.01-1.02 -- the same as Kalshi's best legs
and nothing like the sports lane that cost us 6.458% per dollar.

--- AND THE FEE STRUCTURE IS WHY HIS DRAG IS -0.037% ---
Every one of these markets returns:

    feeSchedule           {"rate": 0.07, "takerOnly": true,
                           "rebateRate": 0.2, "exponent": 1}
    makerRebatesFeeShareBps  10000
    rewardsMinSize           50      (shares)
    rewardsMaxSpread         4.5     (cents from mid)
    orderMinSize             5       (shares)
    orderPriceMinTickSize    0.01

takerOnly TRUE means the maker pays NO FEE AT ALL, and
makerRebatesFeeShareBps 10000 means the maker receives the whole fee share.
That is a structurally different deal from Kalshi, where a maker pays
ceil(0.0175*C*P*(1-P)) on every fill, and it is a large part of why L5's
trading drag is -0.037% per dollar while ours was -6.458%.

--- THE CAPITAL ARITHMETIC, WHICH IS THE WHOLE BLOCKER ---
At a $0.50 contract:

    minimum order              5 shares    ~= $2.50
    minimum to earn rewards   50 shares    ~= $25.00 resting, within 4.5c
    two-sided (L5's shape)                 ~= $50.00 per market
    OUR CURRENT CAP                           $0.05

$0.05 is 50x below the minimum ORDER and 1,000x below a reward-qualifying
two-sided quote. The lane cannot place a single legal order, let alone a
rewarded one. Every software gate we have been removing is downstream of
that.

--- WHAT THIS DOES AND DOES NOT ESTABLISH ---
ESTABLISHED: the venue, the markets, the maker-fee-free structure, the
rebate share and the reward program all still exist and accept us; the book
is 1.01-1.02; and the exact minimum capital to place a legal order and to
qualify for rewards.

NOT ESTABLISHED: our share of the reward pool. That is the same open
question as Kalshi -- but the evidence here is far stronger, because L5's
own receipts prove the program pays on THESE markets: $71,551.74 credited
across 203 events. On Kalshi we have never seen a single credited payout to
anyone we can observe.
"""

from decimal import Decimal as D

OBSERVED_UTC = "2026-09-09T01:57Z"
READ_ONLY = True
SOURCE = ("gamma-api.polymarket.com/markets?slug=... and "
          "clob.polymarket.com/book?token_id=... , unauthenticated GETs")

MARKETS_ARE_LIVE = True
SLUG_PATTERN = ("{asset}-updown-15m-{epoch}", ("btc", "eth"))

# straight off the live market objects, identical on all eight sampled
VENUE_TERMS = {
    "order_min_size_shares": 5,
    "rewards_min_size_shares": 50,
    "rewards_max_spread_cents": D("4.5"),
    "order_price_min_tick": D("0.01"),
    "maker_rebates_fee_share_bps": 10000,
    "fee_rate": D("0.07"),
    "fee_taker_only": True,
    "fee_rebate_rate": D("0.2"),
    "fee_type": "crypto_fees_v2",
    "holding_rewards_enabled": False,
}

MAKER_PAYS_NO_FEE = (
    "feeSchedule.takerOnly is TRUE on every sampled market, and "
    "makerRebatesFeeShareBps is 10000. A maker pays no fee and takes the "
    "whole fee share. Kalshi charges its maker ceil(0.0175*C*P*(1-P)) on "
    "every fill. This is a structural difference, not a rate difference, "
    "and it is a large part of the gap between L5's -0.037%/$ and ours.")

# eight live markets, 01:57Z
LIVE_SAMPLE = {
    "btc-updown-15m-1788918300": {"liquidity_usd": 20441, "pair_cost": D("1.01")},
    "btc-updown-15m-1788919200": {"liquidity_usd": 13959, "pair_cost": D("1.01")},
    "btc-updown-15m-1788920100": {"liquidity_usd": 17991, "pair_cost": D("1.01")},
    "btc-updown-15m-1788921000": {"liquidity_usd": 2651, "pair_cost": D("1.02")},
    "eth-updown-15m-1788918300": {"liquidity_usd": 2779, "pair_cost": None},
    "eth-updown-15m-1788919200": {"liquidity_usd": 1944, "pair_cost": None},
    "eth-updown-15m-1788920100": {"liquidity_usd": 13698, "pair_cost": None},
    "eth-updown-15m-1788921000": {"liquidity_usd": 16338, "pair_cost": None},
}

CURRENT_CAP_USD = D("0.05")        # pmus_micro_executor.PER_MARKET_FIRST_LEG_CAP
REFERENCE_PRICE = D("0.50")        # mid of the band these markets sit in


def min_legal_order_usd(price: D = REFERENCE_PRICE) -> D:
    """Cheapest order the venue will accept at all."""
    return D(VENUE_TERMS["order_min_size_shares"]) * D(price)


def min_reward_qualifying_usd(price: D = REFERENCE_PRICE,
                              two_sided: bool = True) -> D:
    """Capital to rest a reward-qualifying quote. Two-sided by default
    because that is L5's shape and the reward rules score both sides."""
    one = D(VENUE_TERMS["rewards_min_size_shares"]) * D(price)
    return one * (D(2) if two_sided else D(1))


def shortfall(price: D = REFERENCE_PRICE) -> dict:
    """How far the current cap is from each threshold. Reported as a
    multiple, because a percentage understates three orders of magnitude."""
    legal = min_legal_order_usd(price)
    rewarded = min_reward_qualifying_usd(price)
    return {
        "cap_usd": str(CURRENT_CAP_USD),
        "min_legal_order_usd": str(legal),
        "min_reward_qualifying_usd": str(rewarded),
        "cap_is_x_below_min_order": str((legal / CURRENT_CAP_USD)
                                        .quantize(D("1"))),
        "cap_is_x_below_reward_min": str((rewarded / CURRENT_CAP_USD)
                                         .quantize(D("1"))),
    }


THE_BLOCKER = (
    "CAPITAL PER MARKET, and nothing else we control. The venue is open, "
    "the markets are live, the book is 1.01-1.02, the maker pays no fee and "
    "the reward program is configured on every market. A $0.05 per-market "
    "cap cannot buy the 5-share minimum order, let alone the 50-share "
    "reward minimum. This is the hard blocker the owner asked to have "
    "named, and it is a money decision rather than a software one.")

STILL_OPEN = (
    "our share of the reward pool, exactly as on Kalshi. The difference is "
    "the strength of the prior: L5's own receipts show $71,551.74 credited "
    "across 203 events on THESE markets, so the program demonstrably pays "
    "someone. On Kalshi we have never observed a credited payout at all.")

NOT_A_GO = (
    "This module authorises nothing. It places no order, changes no cap and "
    "arms nothing. Raising a per-market money bound is the owner's call and "
    "this is the evidence for that call, not the call itself.")
