"""Adjudicating the 0xSurferX article against our canonical L5 tape.

The owner's standing rule, applied: an outside "I figured out L5" account is
a HYPOTHESIS SOURCE. Our reconstruction of the wallet's public fills is the
EVIDENCE. Where they disagree, the data wins and the disagreement is stated.

PROVENANCE OF THE DOCUMENT, stated because it bears on weight:
  * I could not fetch it -- x.com is blocked by this environment's egress
    proxy -- so the owner pasted the text. I am adjudicating the pasted text.
  * The article SELF-ASSERTS the wallet: it links
    polymarket.com/@l5zn1bwom8etsk with a "?via=surfer" referral parameter.
    Self-assertion in a promotional post is not proof of control, and a
    trader describing his own strategy is a self-report either way.
  * It is substantially MARKETING: it promotes a build tool by name
    throughout and ends with a Telegram funnel. That does not make it false.
    It does mean its claims are selected to sell an architecture, so they
    carry less weight than the wallet's own prints.

WHAT OUR EVIDENCE COVERS, stated before any verdict:
  71,984 fills, 2,216 windows, 2026-03-19 to 2026-08-31, from
  polymarket_public_activity. Market shapes: btc-updown-{5,15}m (69,798
  fills) and eth-updown-{5,15}m (2,186). THAT IS A SUBSET. The wallet may
  trade markets we never reconstructed, so nothing here bounds its total
  P&L.

=========================================================================
CLAIM-BY-CLAIM
=========================================================================

1. "Buy both sides, lock the pair for 97c, guaranteed $1" -- a SIMULTANEOUS
   two-legged arbitrage.
   OUR TAPE: CONTRADICTED as the dominant mode. Median 47s between legs;
   1.3% of pair records form in the same second; 6.6% within five seconds.
   Of profitable pairs, 78.0% are movement-dominated -- the first leg's
   side rose by at least half the eventual profit BEFORE the complement was
   bought. Same-second pairs earn a median 0.02 while pairs formed more
   than two minutes apart earn 0.11. The money is in the wait, which is not
   what a simultaneous arb does.
   VERDICT: CONTRADICTED BY THE WALLET'S OWN PRINTS.

2. "Risk agent verifies both legs can actually complete... a half-filled
   arb is worse than no arb... dedicated naked-leg monitor."
   OUR TAPE: CONTRADICTED. 2,193 of 2,216 windows (99.0%) END CARRYING A
   RESIDUAL. Only 23 windows (1.0%) finish genuinely flat. Residual units
   are 25.8% of all units and contribute -$19,963.43 against +$18,912.86
   from pairs. Naked legs are not an edge case in this wallet; they are the
   normal end state and they are where the money goes.
   VERDICT: CONTRADICTED.

3. "Low-latency infrastructure physically close to Polymarket's servers --
   every millisecond between legs matters."
   OUR TAPE: NOT SUPPORTED at the stated timescale. The median gap between
   legs is 47 SECONDS. Milliseconds cannot matter to a decision that takes
   forty-seven seconds. Latency may still matter for capturing an
   individual print; it is not what determines these pairs.
   VERDICT: NOT SUPPORTED as described.

4. "GTC resting maker orders beat FAK taker orders; prefer the rebate."
   OUR TAPE: CONSISTENT. 55,647 of 71,984 fills are maker (77.3%), 11,068
   taker (15.4%), 5,269 unknown. This is the one load-bearing mechanical
   claim the tape supports.
   VERDICT: CONSISTENT.

5. "The hunter buys at the ask (not mid or bid)."
   Buying at the ask is TAKER behaviour and contradicts claim 4 within the
   same document. The tape sides with claim 4: predominantly maker.
   VERDICT: INTERNALLY INCONSISTENT; the tape favours the maker claim.

6. "Merge the completed pair back into $1 immediately and recycle capital."
   OUR TAPE: UNTESTABLE. A merge is a collateral operation, not a trade, so
   it does not appear in a fill feed. Note this does NOT change the pair
   economics we measured: merging realises the same pair discount sooner.
   Our +$18,912.86 pair contribution already values every matched unit at
   its discount, whenever it was realised.
   VERDICT: UNTESTABLE, AND ECONOMICALLY NEUTRAL to our numbers.

7. "$66k PnL."
   OUR TAPE: OUT OF SCOPE, AND NOT REFUTED. Our reconstruction covers only
   BTC/ETH up-down windows. Within that subset the public fills net
   -$1,050.57 (pairs +$18,912.86, residuals -$19,963.43; a per-window
   float sum reads -1050.58, a one-cent rounding artifact). That is a
   statement about the subset we reconstructed, NOT about the wallet's
   total. A figure of $66k could come from markets we never ingested, from
   liquidity rewards, or from a different accounting period or convention.
   VERDICT: NOT ADDRESSED BY OUR EVIDENCE. Do not cite our -$1,050.57 as a
   rebuttal of $66k.

8. "Combinatorial arb across logically linked markets."
   OUR TAPE: UNTESTED. Every window we hold is a single binary up-down
   market. If the wallet runs cross-market strategies they are outside our
   ingestion, and this is a genuine gap in our picture rather than a
   negative finding.
   VERDICT: UNTESTED, AND WORTH INGESTING IF WE WANT TO CLOSE IT.

9. "Copying the public wallet is useless -- you only see buys and merges,
   never timing, routing, merge cadence or risk logic."
   OUR TAPE: AGREES, and we reached it independently. Fills cannot
   establish queue priority, resting duration or order-level intent; that
   needs placement/cancel timestamps, order ids and book snapshots.
   VERDICT: CONSISTENT with our own stated evidence limits.

10. "Backtest on real order-book snapshots with depth, never closing
    prices; model that both legs do not always fill."
    Methodological advice, not a claim about the wallet. We already do the
    second half by necessity and cannot do the first: we have no book.
    VERDICT: NOT A CLAIM ABOUT L5.

=========================================================================
WHAT THIS CHANGES FOR US: NOTHING OPERATIONAL
=========================================================================
The article describes a textbook simultaneous-arbitrage architecture. On
the three load-bearing mechanical points the wallet's own prints do not
match it: pairs are sequential, naked legs are the norm, and the decisive
timescale is tens of seconds rather than milliseconds. It matches on maker
preference.

So the document does not overturn any measurement and does not supply a new
testable mechanism we lack. No cap, gate, threshold, flag, model or the
stopped live lane changes because of it. Nothing in it is implemented.

THE ONE THING WORTH ACTING ON LATER, and it is an ingestion question rather
than a strategy one: claim 8. If the wallet runs combinatorial positions
across linked markets, our canonical reconstruction is blind to them, and
every economic statement we make about "L5" is scoped to up-down windows.
That is a known boundary of our evidence, now written down.
"""
from __future__ import annotations

from decimal import Decimal as D

SOURCE = "0xSurferX X article, pasted by the owner; x.com is egress-blocked here"
SOURCE_IS_EVIDENCE = False
SOURCE_SELF_ASSERTS_WALLET = True      # links the wallet with a referral param
SOURCE_IS_PROMOTIONAL = True           # promotes a build tool, ends in a funnel
IDENTITY_ESTABLISHED = False           # self-assertion is not proof of control

# what our evidence actually covers
TAPE_FILLS = 71984
TAPE_WINDOWS = 2216
TAPE_SPAN = ("2026-03-19", "2026-08-31")
TAPE_MARKET_SHAPES = ("btc-updown-{5,15}m", "eth-updown-{5,15}m")
TAPE_IS_A_SUBSET_OF_THE_WALLET = True

# the measurements the verdicts rest on
MEDIAN_LEG_GAP_S = D("47.0")
SAME_SECOND_PAIR_SHARE = D("0.013")
MOVEMENT_DOMINATED_SHARE = D("0.780")
WINDOWS_ENDING_FLAT = (23, 2216)
RESIDUAL_UNIT_SHARE = D("0.258")
PAIR_CONTRIBUTION = D("18912.86")
RESIDUAL_CONTRIBUTION = D("-19963.43")
MAKER_SHARE = D("0.773")

VERDICTS = {
    "1_simultaneous_pair_arb": "CONTRADICTED",
    "2_never_hold_naked_legs": "CONTRADICTED",
    "3_millisecond_latency_decisive": "NOT SUPPORTED",
    "4_gtc_maker_preference": "CONSISTENT",
    "5_buys_at_the_ask": "INTERNALLY INCONSISTENT",
    "6_immediate_merge": "UNTESTABLE",
    "7_66k_pnl": "OUT OF SCOPE",
    "8_combinatorial_arb": "UNTESTED",
    "9_wallet_copying_is_useless": "CONSISTENT",
    "10_backtest_advice": "NOT A CLAIM ABOUT L5",
}

OUR_SUBSET_NET = D("-1050.57")   # = the two components above; a
                                 # per-window float sum reads -1050.58, a
                                 # one-cent rounding artifact, not a second
                                 # measurement
DO_NOT_CITE_AS_REBUTTAL_OF = "the article's $66k PnL figure"

CHANGES_ANYTHING_OPERATIONAL = False
AUTHORISES_MODEL_CHANGE = ()
AUTHORISES_RESTART = ()
IMPLEMENTS_OUTSIDE_STRATEGY = False
OPEN_INGESTION_GAP = ("combinatorial / cross-market positions are outside "
                      "our canonical ingestion, so every economic statement "
                      "we make about L5 is scoped to up-down windows")


def contradicted_claims() -> tuple:
    return tuple(k for k, v in VERDICTS.items()
                 if v in ("CONTRADICTED", "NOT SUPPORTED"))


def supported_claims() -> tuple:
    return tuple(k for k, v in VERDICTS.items() if v == "CONSISTENT")


def net_on_our_subset() -> D:
    return (PAIR_CONTRIBUTION + RESIDUAL_CONTRIBUTION).quantize(D("0.01"))


def windows_ending_flat_share() -> D:
    f, t = WINDOWS_ENDING_FLAT
    return (D(f) / D(t)).quantize(D("0.0001"))
