"""Could our continuous-inventory engine have made money on prices that
actually existed?

Retrospective, evidence-driven. Reads only files under data/. Submits nothing.

THE EVIDENCE IS GRADED, and the grades are never mixed:

  LEVEL 1  ACTUAL EXECUTED AND SETTLED on our own PMUS account. Reported from
           the production cost-basis audit, not recomputed here.
  LEVEL 2  CONSERVATIVE COUNTERFACTUAL on a venue whose contract geometry and
           fee formula match PMUS: Kalshi 15m crypto binaries, from recorded
           two-sided books with depth and recorded prints that carry
           `taker_side`. A resting maker is filled ONLY when observed taker
           volume on the opposite side exceeds the queue that was ahead of it
           at placement.
  LEVEL 3  HYPOTHETICAL, fills unestablished. Reported as a REQUIREMENT -- the
           minimum fill probability for positive expectancy -- never as a
           result.

WHAT MAKES LEVEL 2 POSSIBLE. Every Kalshi print records which side the TAKER
took. A print with taker_side "no" is a taker selling YES into the YES bid, so
it fills a resting YES buyer. That is a direct observation of maker execution,
not an inference from quote movement. Gemini's tape had no such field, which
is why the earlier Gemini pass had to assume trade-through.

THE QUEUE DISCIPLINE, inherited from src/l5_maker_fill_model.py:

  * queue-ahead is captured AT PLACEMENT and never re-read;
  * ONLY observed taker volume advances the queue -- depth shrinking is not
    advancement, because a cancel ahead of us cannot be told from a fill
    ahead of us;
  * TOUCH IS NOT FILL;
  * unknown depth never fills.

VENUE HONESTY. Kalshi is not PMUS. The geometry (binary, $1 settlement, 1c
tick) and the fee formula (Theta * C * p * (1-p)) are the same family, and
PMUS fee constants are applied throughout. The FILL RATE is labelled as
measured on Kalshi and is not claimed to transfer.

  python3 -I scripts/l5_retrospective_maker_opportunity.py
"""

from __future__ import annotations

import collections
import glob
import json
import statistics as st
from decimal import Decimal as D, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, List, Optional, Tuple

TAPES = ("data/policy_d_port/tape_*.jsonl",
         "data/policy_d_port_ext/tape_*.jsonl",
         "data/ref_lag/*.jsonl")

TAKER_THETA = D("0.0695")
MAKER_THETA = D("-0.0125")
OUR_QTY = D("1")
MIN_BBO = 10
MIN_PRINTS = 10
# a settled 15m binary's own terminal consensus; anything between stays UNKNOWN
WON_AT = D("0.99")
LOST_AT = D("0.01")


def _d(v: Any) -> Optional[D]:
    if v is None or v == "":
        return None
    try:
        out = D(str(v))
    except Exception:
        return None
    return out if out.is_finite() else None


#: Exact (unrounded) fees are the HEADLINE, because cent rounding at one
#: contract inflates the maker rebate 3.2x at p=0.50 and 8.9x at p=0.10 --
#: and the whole matched edge on a 1c spread is $0.01 per pair, so the
#: rounding is the same size as the thing being measured. Our own account
#: shows the venue rounds to the NEAREST cent (a $0.00707 modelled rebate was
#: paid as $0.01, a $0.01225 one also as $0.01), which at one contract is
#: mildly in our favour. Exact is therefore the conservative reading.
EXACT_FEES = True


def order_fee(theta: D, qty: D, price: D) -> D:
    """Venue fee, per ORDER. Cent-rounded only when EXACT_FEES is off."""
    raw = theta * qty * price * (D(1) - price)
    if EXACT_FEES or raw == 0:
        return raw
    q = raw.quantize(D("0.01"), rounding=ROUND_UP if raw > 0 else ROUND_DOWN)
    return q if q != 0 else (D("0.01") if raw > 0 else D("-0.01"))


def _top(levels: Any) -> Tuple[Optional[D], Optional[D]]:
    """Best price and the depth AT that price. Unknown depth stays None."""
    if not isinstance(levels, list) or not levels:
        return None, None
    lv = levels[0]
    if not isinstance(lv, list) or len(lv) < 2:
        return None, None
    return _d(lv[0]), _d(lv[1])


def load_markets() -> Dict[str, Dict[str, List[dict]]]:
    """Kalshi markets with a two-sided book and recorded prints."""
    mk: Dict[str, Dict[str, List[dict]]] = collections.defaultdict(
        lambda: {"bbo": [], "print": [], "window": []})
    for pattern in TAPES:
        for path in sorted(glob.glob(pattern)):
            for line in open(path):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("venue") != "kalshi":
                    continue
                kind = r.get("kind")
                market = r.get("market")
                if not market or kind not in ("bbo", "print", "window"):
                    continue
                t = r.get("t")
                if t is None:
                    continue
                mk[market][kind].append(r)
    out = {}
    for market, d in mk.items():
        if len(d["bbo"]) < MIN_BBO or len(d["print"]) < MIN_PRINTS:
            continue
        d["bbo"].sort(key=lambda r: r["t"])
        d["print"].sort(key=lambda r: r["t"])
        out[market] = d
    return out


def terminal_outcome(bbos: List[dict]) -> Optional[str]:
    """The market's own terminal consensus. UNKNOWN unless unambiguous."""
    for r in reversed(bbos):
        yb, _ = _top(r.get("yes_bids"))
        nb, _ = _top(r.get("no_bids"))
        if yb is not None and yb >= WON_AT:
            return "yes"
        if nb is not None and nb >= WON_AT:
            return "no"
        if yb is not None and yb <= LOST_AT and nb is not None and nb >= D("0.9"):
            return "no"
    return None


def _cleared(prints: List[dict], *, after_t: float, taker_side: str,
             yes_price: D, direction: str) -> List[Tuple[float, D]]:
    """Cumulative observed taker volume that would have worked our queue.

    `direction` "down": a taker took NO, selling YES into bids at or below our
    price. "up": a taker took YES, lifting asks at or above our price.
    """
    run = D("0")
    out = []
    for p in prints:
        if p["t"] <= after_t or p.get("taker_side") != taker_side:
            continue
        px = _d(p.get("yes_price"))
        cnt = _d(p.get("count"))
        if px is None or cnt is None:
            continue
        if direction == "down" and px > yes_price:
            continue
        if direction == "up" and px < yes_price:
            continue
        run += cnt
        out.append((p["t"], run))
    return out


def _fill_t(cleared: List[Tuple[float, D]], need: D) -> Optional[float]:
    for t, run in cleared:
        if run >= need:
            return t
    return None


def completion_price(bbos: List[dict], *, after_t: float, want: str
                     ) -> Optional[D]:
    """The price we could actually have crossed at, from the FIRST book
    AFTER the fill. No hindsight: never the best price in the window."""
    for r in bbos:
        if r["t"] < after_t:
            continue
        if want == "yes":
            # buying YES means paying the YES ask = 1 - best no_bid
            nb, _ = _top(r.get("no_bids"))
            return (D(1) - nb) if nb is not None else None
        nb, _ = _top(r.get("yes_bids"))
        return (D(1) - nb) if nb is not None else None
    return None


def evaluate(market: str, d: Dict[str, List[dict]], *, scenario: str
             ) -> List[Dict[str, Any]]:
    bbos, prints = d["bbo"], d["print"]
    outcome = terminal_outcome(bbos)
    if outcome is None:
        return []
    end_t = max(bbos[-1]["t"], prints[-1]["t"])
    rows = []
    for r in bbos:
        if r["t"] >= end_t:
            continue
        p_yes, q_yes = _top(r.get("yes_bids"))
        p_no, q_no = _top(r.get("no_bids"))
        if p_yes is None or p_no is None:
            continue
        if q_yes is None or q_no is None:
            continue           # unknown depth never fills
        if not (D(0) < p_yes < D(1)) or not (D(0) < p_no < D(1)):
            continue
        # how much must trade before we are done, at placement
        extra = OUR_QTY if scenario == "CONSERVATIVE" else D("0")
        yes_t = _fill_t(_cleared(prints, after_t=r["t"], taker_side="no",
                                 yes_price=p_yes, direction="down"),
                        q_yes + extra)
        no_t = _fill_t(_cleared(prints, after_t=r["t"], taker_side="yes",
                                yes_price=D(1) - p_no, direction="up"),
                       q_no + extra)
        rows.append({
            "market": market, "t": r["t"], "outcome": outcome,
            "p_yes": p_yes, "p_no": p_no, "pair_cost": p_yes + p_no,
            "q_yes": q_yes, "q_no": q_no,
            "yes_t": yes_t, "no_t": no_t,
            "both": yes_t is not None and no_t is not None,
            "one": (yes_t is None) != (no_t is None),
            "spread": D(1) - p_yes - p_no,
        })
    return rows


def price_pair(row: Dict[str, Any], *, rebate: bool, policy: str,
               bbos: List[dict]) -> Optional[D]:
    """Per-attempt P&L in dollars, one contract per leg."""
    mt = MAKER_THETA if rebate else D("0")
    if row["both"]:
        pnl = D(1) - row["pair_cost"]
        pnl -= order_fee(mt, OUR_QTY, row["p_yes"])
        pnl -= order_fee(mt, OUR_QTY, row["p_no"])
        return pnl
    if not row["one"]:
        return D("0")
    held_yes = row["yes_t"] is not None
    cost = row["p_yes"] if held_yes else row["p_no"]
    fill_t = row["yes_t"] if held_yes else row["no_t"]
    pnl = -cost - order_fee(mt, OUR_QTY, cost)
    if policy == "HOLD":
        won = (row["outcome"] == "yes") if held_yes else (row["outcome"] == "no")
        return pnl + (D(1) if won else D(0))
    # COMPLETE: cross for the other side at the first book after the fill
    want = "no" if held_yes else "yes"
    px = completion_price(bbos, after_t=fill_t, want=want)
    if px is None or not (D(0) < px < D(1)):
        won = (row["outcome"] == "yes") if held_yes else (row["outcome"] == "no")
        return pnl + (D(1) if won else D(0))
    return pnl - px - order_fee(TAKER_THETA, OUR_QTY, px) + D(1)


def main() -> None:
    markets = load_markets()
    print("=" * 78)
    print("RETROSPECTIVE MAKER OPPORTUNITY -- graded evidence")
    print("=" * 78)
    print()
    print("LEVEL 1  ACTUAL PMUS EXECUTED AND SETTLED")
    print("  Our own account, from the production cost-basis audit:")
    print("    1,911 fill rows, 49 unknown-basis legs, 47 cost-established")
    print("    measured cost $81.3862 against $291.05 of $1/share placeholders")
    print("    maker rebates CREDITED: 831 rows, commission sum -$5.8800")
    print("    account realized -$233.9854, ALL of it UNKNOWN_OWNER")
    print("  No PMUS leg is attributable to engine activity: l5_owned_fills 0")
    print("  on every one. There is no actual PMUS maker-strategy result to")
    print("  report, profitable or otherwise.")
    print()
    print("LEVEL 2  CONSERVATIVE COUNTERFACTUAL (Kalshi 15m crypto binaries)")
    print("  venue geometry and fee formula match PMUS; PMUS fee constants")
    print("  applied. The FILL RATE is Kalshi's and is not claimed to transfer.")
    print("  A maker fills ONLY when observed opposite-side taker volume")
    print("  exceeds the queue ahead of it at placement.")
    print()
    if not markets:
        print("  NO USABLE MARKETS in the recorded tapes.")
        return
    series = collections.Counter(m.split("-")[0] for m in markets)
    print("  markets with two-sided book + prints: %d  %s"
          % (len(markets), dict(series)))

    for scenario in ("CONSERVATIVE", "BASE"):
        rows: List[Dict[str, Any]] = []
        by_market: Dict[str, List[dict]] = {}
        for m, d in markets.items():
            rs = evaluate(m, d, scenario=scenario)
            if rs:
                rows.extend(rs)
                by_market[m] = d["bbo"]
        if not rows:
            continue
        n = len(rows)
        both = sum(1 for r in rows if r["both"])
        one = sum(1 for r in rows if r["one"])
        acted = both + one
        print()
        print("  --- %s (%s) ---"
              % (scenario, "our own size must trade too" if scenario == "CONSERVATIVE"
                 else "filled when the print reaches us"))
        print("    decisions %d over %d settled markets" % (n, len(by_market)))
        print("    both legs %5d  %6.2f%%   one leg %5d  %6.2f%%   neither %6.2f%%"
              % (both, 100 * both / n, one, 100 * one / n, 100 * (n - acted) / n))
        if acted:
            share = D(both) / D(acted)
            print("    both / (both+one) = %.2f%%  <-- the measured fill mix"
                  % (100 * share))
        print("    median quoted pair cost %.4f   median spread %.4f"
              % (st.median(float(r["pair_cost"]) for r in rows),
                 st.median(float(r["spread"]) for r in rows)))
        if not acted:
            continue
        for rebate in (False, True):
            line = []
            for policy in ("HOLD", "COMPLETE"):
                tot = D("0")
                for r in rows:
                    v = price_pair(r, rebate=rebate, policy=policy,
                                   bbos=by_market[r["market"]])
                    if v is not None:
                        tot += v
                line.append((policy, tot / D(acted)))
            print("    per acted attempt, rebate=%-5s  %s"
                  % (rebate, "  ".join("%s %+0.6f" % (p, v) for p, v in line)))

        # LEVEL 3 -- the requirement, from this sample's own prices
        both_rows = [r for r in rows if r["both"]]
        one_rows = [r for r in rows if r["one"]]
        if both_rows and one_rows:
            for rebate in (False, True):
                e_both = sum(price_pair(r, rebate=rebate, policy="HOLD",
                                        bbos=by_market[r["market"]])
                             for r in both_rows) / D(len(both_rows))
                for policy in ("HOLD", "COMPLETE"):
                    e_one = sum(price_pair(r, rebate=rebate, policy=policy,
                                           bbos=by_market[r["market"]])
                                for r in one_rows) / D(len(one_rows))
                    if e_both <= e_one:
                        verdict = "no requirement: one-sided already pays"
                    else:
                        need = (-e_one) / (e_both - e_one)
                        verdict = ("needs %.2f%% both-fill, measured %.2f%% -> %s"
                                   % (100 * need, 100 * share,
                                      "MET" if share >= need else "NOT MET"))
                    print("    LEVEL 3 requirement rebate=%-5s %-9s %s"
                          % (rebate, policy, verdict))


if __name__ == "__main__":
    main()
