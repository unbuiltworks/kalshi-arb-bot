"""Do we have access to L5's markets on Polymarket US? NO. They do not
exist there.

Measured 2026-09-09T02:1xZ, read-only GETs, using the repo's own
enumerate_open_markets against gateway.polymarket.us.

--- THE TWO VENUES ARE NOT THE SAME VENUE ---
Our live lane trades PMUS:      gateway.polymarket.us / api.polymarket.us
L5 traded Polymarket intl:      gamma-api.polymarket.com / clob.polymarket.com

I found btc-updown-15m / eth-updown-15m live, accepting orders, priced at
1.01-1.02, with rewardsMinSize 50 and a maker-pays-no-fee schedule. Those
are on the INTERNATIONAL venue. They are not the venue our probe trades and
I should have separated the two before reporting the earlier result as
though it were reachable.

--- THE PMUS CRYPTO UNIVERSE, ENUMERATED IN FULL ---
25,000 open markets over 50 pages:

    sports      18,481
    politics     5,566
    culture        686
    finance         77
    technology      62
    macro           60
    crypto          49
    geopolitics     11
    science          8

All 49 crypto markets are LONG-DATED PRICE TARGETS -- cpc-btc-150k-12-31-
2026, cpc-btc-hitprice-high-yr-..., cpc-btc-pricerange-yr-... Every one
expires 3,068.6 hours out. That is 128 days.

    markets under 24h:                 0
    markets under 1h:                  0
    slugs containing 'updown':         0

--- WHAT THIS EXPLAINS ---
It explains the sports lane. 18,481 of 25,000 PMUS markets are sports, so
sports is what PMUS actually offers -- the event-progress clock translation,
the props discovery, the slate filters were not arbitrary inventions, they
were the consequence of running L5's policy on the only instrument class
available on our venue.

And it explains the 174x drag. We were not executing L5's strategy badly.
We were executing it on a DIFFERENT INSTRUMENT: a multi-hour sports market
instead of a 15-minute crypto window that settles deterministically and
recycles capital every quarter hour. Same policy, different physics.

--- WHAT IT MEANS FOR "L5, EXACTLY THE SAME" ---
Not possible on PMUS. The instrument his entire engine is built around --
a 15-minute deterministic up/down window -- has no counterpart there. No
amount of barrier removal, capital, or fidelity work changes that, because
the market does not exist to trade.

Three routes remain, and the first two are the owner's to decide, not an
engineering task:

  1. TRADE THE INTERNATIONAL VENUE, where his markets are. This is an
     access and eligibility question -- account, jurisdiction, and terms --
     and it belongs to the owner and probably to a lawyer. I am not
     positioned to assess whether it is available or permitted.
  2. ACCEPT A DIFFERENT INSTRUMENT and stop calling it L5. The closest
     structural match we have MEASURED is Kalshi's 15-minute series:
     deterministic settlement, 15-minute recycling, pair cost 1.01-1.02,
     and a live liquidity program. It is not his venue and the maker there
     pays a fee, but the physics match.
  3. STAY ON PMUS SPORTS, which is the configuration that lost 6.458% per
     dollar, and is stopped.

--- WHAT I GOT WRONG EARLIER TONIGHT ---
I reported "L5's own markets are live and open to us" without checking
which venue "us" meant. The markets are live; "open to us" was not
established and is now known to be false for PMUS. The capital arithmetic
in that report ($2.50 minimum order, $50 two-sided reward-qualifying) is
still correct FOR THE INTERNATIONAL VENUE, and irrelevant to PMUS.
"""

PMUS_HOST = "gateway.polymarket.us"
INTL_HOST = "gamma-api.polymarket.com"
SAME_VENUE = False

PMUS_OPEN_MARKETS = 25_000
PMUS_CATEGORIES = {
    "sports": 18_481, "politics": 5_566, "culture": 686, "finance": 77,
    "technology": 62, "macro": 60, "crypto": 49, "geopolitics": 11,
    "science": 8,
}
PMUS_CRYPTO_MIN_HORIZON_HOURS = 3068.6
PMUS_MARKETS_UNDER_24H = 0
PMUS_MARKETS_UNDER_1H = 0
PMUS_UPDOWN_SLUGS = 0

L5_INSTRUMENT_EXISTS_ON_PMUS = False

ANSWER = ("NO. Polymarket US carries no 15-minute crypto up/down market. "
          "Its entire crypto category is 49 long-dated price targets, all "
          "expiring 128 days out, and zero markets of any category expire "
          "within 24 hours. L5's instrument has no counterpart there.")

WHY_THE_DRAG_WAS_174X = (
    "not bad execution of L5's strategy -- execution of it on a different "
    "instrument. A multi-hour sports market instead of a 15-minute "
    "deterministic crypto window. Same policy, different physics, and the "
    "sports clock translation and props filters were consequences of that "
    "rather than inventions we chose.")

MY_EARLIER_OVERCLAIM = (
    "I reported 'L5's own markets are live and OPEN TO US' without "
    "establishing which venue 'us' meant. Live: true. Open to us on PMUS: "
    "FALSE. The capital arithmetic in that report is still correct for the "
    "international venue and irrelevant to ours.")

ROUTES = (
    ("trade the international venue", "OWNER + LEGAL: access, jurisdiction "
     "and terms; not an engineering task and I cannot assess it"),
    ("accept a different instrument", "Kalshi 15-minute series is the "
     "closest MEASURED structural match: deterministic settlement, "
     "15-minute recycling, pair cost 1.01-1.02, live liquidity program. "
     "Not his venue; the maker pays a fee there."),
    ("stay on PMUS sports", "the configuration that lost 6.458%/$ and is "
     "stopped"),
)
