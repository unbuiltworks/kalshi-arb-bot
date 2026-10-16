"""Can a patient two-sided maker actually get BOTH legs filled?

P1. The historical +$175,210 completion improvement is a HYPOTHESIS computed
from window-average prices. This measures the thing it depends on, using only
quotes that existed at the moment of each decision:

  - no hindsight: a decision at time t reads only BBO rows with t' <= t
  - no touch=fill: a resting order fills only when the book trades THROUGH
    its price, never when the touch merely reaches it
  - no assumed queue priority: strict penetration is the fill test, which is
    the most pessimistic reading of a queue we cannot observe
  - fees and settlement are the venue's, applied per order

Source: data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz -- a real BBO tape
with sizes, 23,318 rows over 136 BTC-15m windows (2026-08-30 .. 2026-09-02).

TWO LIMITS OF THIS TAPE, STATED UP FRONT:

  1. It is the UP side only. Zero of the 136 windows carry a DOWN row. The
     DOWN book is DERIVED by binary complementarity (DOWN bid = 1 - UP ask,
     DOWN ask = 1 - UP bid). That is an identity assumption, not a
     measurement, and --no-complement reports what can be said without it.
  2. It is Gemini, not PMUS. The fill geometry of a 15m BTC up/down book
     should carry; the queue and the fee schedule do not. PMUS fees are
     applied; the fill rate is labelled as measured off Gemini.

  python3 -I scripts/l5_two_sided_fill_feasibility.py
"""

from __future__ import annotations

import gzip
import json
import os
import statistics as st
from decimal import Decimal as D, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, List, Optional, Tuple

TAPE = "data/transfer_backtest/gemini_bbo_btc15m.jsonl.gz"
WINDOW_S = 900.0
DECISION_EVERY_S = 60.0       # how often a patient maker re-decides
TAKER_THETA = D("0.0695")
MAKER_THETA = D("-0.0125")
# From the realized L5 audit, zero rebate assumed:
MATCHED_EDGE_PER_SHARE = D("0.026607")
RESIDUAL_HELD_PER_SHARE = D("-0.128955")


def load_tape(path: str = TAPE) -> Dict[str, List[Dict[str, Any]]]:
    """Group the BBO rows by window, ascending in time."""
    if not os.path.exists(path):
        return {}
    by_window: Dict[str, List[Dict[str, Any]]] = {}
    for line in gzip.open(path, "rt"):
        row = json.loads(line)
        sym = row.get("symbol") or ""
        base, _, side = sym.rpartition("-")
        if side != "UP" or not base:
            continue
        try:
            quote = {
                "t": float(row["t"]),
                "bid": D(str(row["bid"])),
                "ask": D(str(row["ask"])),
                "bid_sz": D(str(row.get("bid_sz") or 0)),
                "ask_sz": D(str(row.get("ask_sz") or 0)),
            }
        except Exception:
            continue
        if quote["bid"] <= 0 or quote["ask"] >= 1 or quote["bid"] >= quote["ask"]:
            continue
        by_window.setdefault(base, []).append(quote)
    for rows in by_window.values():
        rows.sort(key=lambda r: r["t"])
    return by_window


def order_fee(theta: D, qty: D, price: D) -> D:
    """Venue fee, per ORDER, rounded away from zero to the cent."""
    raw = theta * qty * price * (D(1) - price)
    if raw == 0:
        return D("0")
    q = raw.quantize(D("0.01"), rounding=ROUND_UP if raw > 0 else ROUND_DOWN)
    return q if q != 0 else (D("0.01") if raw > 0 else D("-0.01"))


def evaluate_window(rows: List[Dict[str, Any]], *, strict: bool = True
                    ) -> List[Dict[str, Any]]:
    """For each decision time, rest UP at the bid and DOWN at its own bid.

    UP (bought at the UP bid p) fills when a seller crosses down to us, i.e.
    the UP ask falls below p. DOWN at its own bid is the UP ask a, and fills
    when a buyer crosses up, i.e. the UP bid rises above a.

    `strict` requires the book to trade THROUGH the price. With strict off the
    touch counts, which assumes we are at the front of a queue we cannot see.
    """
    if not rows:
        return []
    t0 = rows[0]["t"]
    out = []
    for row in rows:
        if (row["t"] - t0) % DECISION_EVERY_S > 1.0 and row is not rows[0]:
            continue
        p_up = row["bid"]          # our UP maker price
        p_dn_as_up = row["ask"]    # our DOWN maker price, in UP terms
        later = [r for r in rows if r["t"] > row["t"]]
        if strict:
            up_filled = any(r["ask"] < p_up for r in later)
            dn_filled = any(r["bid"] > p_dn_as_up for r in later)
        else:
            up_filled = any(r["ask"] <= p_up for r in later)
            dn_filled = any(r["bid"] >= p_dn_as_up for r in later)
        out.append({
            "t": row["t"],
            "age_s": row["t"] - t0,
            "p_up": p_up,
            "p_dn": D(1) - p_dn_as_up,       # DOWN's own price
            "pair_cost": p_up + (D(1) - p_dn_as_up),
            "spread": row["ask"] - row["bid"],
            "up_filled": up_filled,
            "dn_filled": dn_filled,
            "both": up_filled and dn_filled,
            "one_sided": up_filled != dn_filled,
            "neither": not up_filled and not dn_filled,
            "observations_after": len(later),
        })
    return out


def main() -> None:
    tape = load_tape()
    if not tape:
        print("NO TAPE at %s" % TAPE)
        return
    print("=" * 76)
    print("TWO-SIDED MAKER FILL FEASIBILITY  (%d windows, contemporaneous only)" % len(tape))
    print("=" * 76)
    print("  source: %s -- UP side only; DOWN derived by binary complementarity" % TAPE)
    print("  venue:  Gemini BTC-15m. Fill geometry measured here; PMUS fees applied.")
    print()

    for strict, label in ((True, "STRICT: book must trade THROUGH our price (no queue priority)"),
                          (False, "LOOSE:  touch counts as a fill (assumes front of queue)")):
        decisions = []
        for rows in tape.values():
            decisions.extend(evaluate_window(rows, strict=strict))
        if not decisions:
            continue
        n = len(decisions)
        both = sum(1 for d in decisions if d["both"])
        one = sum(1 for d in decisions if d["one_sided"])
        none = sum(1 for d in decisions if d["neither"])
        print(label)
        print("  decisions %d across %d windows, one every %.0fs" % (n, len(tape), DECISION_EVERY_S))
        print("    BOTH legs filled   %6d  %6.2f%%" % (both, 100 * both / n))
        print("    ONE leg only       %6d  %6.2f%%" % (one, 100 * one / n))
        print("    NEITHER            %6d  %6.2f%%" % (none, 100 * none / n))
        filled_attempts = both + one
        if filled_attempts:
            print("    both / (both+one)  %6.2f%%   <-- compare against the 82.90%% requirement"
                  % (100 * both / filled_attempts))
        print("    mean quoted spread %.4f   median pair cost %.4f"
              % (st.mean(float(d["spread"]) for d in decisions),
                 st.median(float(d["pair_cost"]) for d in decisions)))
        print()

    # Net expectancy under the strict reading, zero rebate.
    decisions = []
    for rows in tape.values():
        decisions.extend(evaluate_window(rows, strict=True))
    both = [d for d in decisions if d["both"]]
    one = [d for d in decisions if d["one_sided"]]
    acted = len(both) + len(one)
    if not acted:
        print("no filled attempts; nothing to price")
        return
    print("=" * 76)
    print("NET EXPECTANCY PER ATTEMPT, ZERO REBATE, STRICT FILLS")
    print("=" * 76)
    # a filled pair earns 1 - its OWN quoted pair cost, not the historical average
    pair_edge = sum((D(1) - d["pair_cost"]) for d in both)
    print("  both-filled attempts %d, summed pair edge $%+.4f per contract-pair"
          % (len(both), pair_edge))
    print("  mean pair edge on a both-fill: $%+.6f" % (pair_edge / len(both)) if both else "")
    print("  one-sided attempts %d" % len(one))
    print()
    print("  residual policy, per one-sided contract:")
    print("    HOLD to settlement        %+0.6f   (measured, L5 realized)" % RESIDUAL_HELD_PER_SHARE)
    for slip in (D("0.00"), D("0.01"), D("0.02")):
        # cross the complement at its own ask = 1 - UP bid, plus slippage
        tot = D("0")
        for d in one:
            complement_ask = D(1) - d["p_up"] + slip
            if complement_ask >= 1:
                complement_ask = D("0.99")
            fee = order_fee(TAKER_THETA, D(1), complement_ask)
            tot += (D(1) - d["p_up"] - complement_ask) - fee
        print("    COMPLETE by crossing +%s slip  %+0.6f" % (slip, tot / len(one)))
    print()
    per_attempt_hold = (pair_edge + len(one) * RESIDUAL_HELD_PER_SHARE) / acted
    print("  per ACTED attempt, hold policy:     $%+0.6f" % per_attempt_hold)
    tot = D("0")
    for d in one:
        complement_ask = D(1) - d["p_up"]
        fee = order_fee(TAKER_THETA, D(1), complement_ask)
        tot += (D(1) - d["p_up"] - complement_ask) - fee
    print("  per ACTED attempt, complete policy: $%+0.6f" % ((pair_edge + tot) / acted))
    print()
    print("=" * 76)
    print("THE REQUIREMENT MOVES WITH THE RESIDUAL POLICY")
    print("=" * 76)
    measured = D(len(both)) / D(acted)
    per_both = pair_edge / len(both)
    print("  measured both-fill share of acted attempts: %.2f%%" % (100 * measured))
    print("  mean pair edge on a both-fill:              %+0.6f" % per_both)
    print()
    print("  %-34s %12s %14s %12s" % ("residual policy", "per-residual", "break-even", "verdict"))
    policies = [("hold to settlement", RESIDUAL_HELD_PER_SHARE)]
    for slip in (D("0.00"), D("0.01"), D("0.02")):
        tot = D("0")
        for d in one:
            ca = min(D(1) - d["p_up"] + slip, D("0.99"))
            tot += (D(1) - d["p_up"] - ca) - order_fee(TAKER_THETA, D(1), ca)
        policies.append(("complete by crossing +%s" % slip, tot / len(one)))
    for name, per_one in policies:
        need = (-per_one) / (per_both - per_one)
        ok = "MET" if measured >= need else "NOT MET"
        print("  %-34s %+12.6f %13.2f%% %12s" % (name, per_one, 100 * need, ok))
    print()
    print("  Completing the residual lowers the bar from the 82.90%% derived")
    print("  against hold-to-settlement. The measured rate sits just under the")
    print("  completion bar, which is why per-attempt expectancy is ~zero and")
    print("  slightly negative rather than clearly either way.")


if __name__ == "__main__":
    main()
