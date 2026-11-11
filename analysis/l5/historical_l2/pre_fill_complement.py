"""Did liquidity at the eventual complement price exist BEFORE L5's first fill?

THE QUESTION THIS DECIDES. Arm R reacts: it waits for a first fill and only
then quotes the other side. Arm S quotes both sides from market state, before
either fills. Those two make different predictions about the book, and this is
the one place historical data can separate them:

    if a level at the price L5 eventually paid on the COMPLEMENT token was
    already sitting in the book before his first fill, a both-sides-first
    process is consistent with what happened.
    if that level only appears afterwards, a reactive process is.

WHAT THIS CANNOT DO, AND MUST NOT PRETEND TO. The dataset is anonymous depth.
A level existing is a level existing. It is NEVER asserted to be L5's order --
not his resting quote, not his counterparty's. Every label below is a statement
about SIZE AT A PRICE and nothing more, and the OBSERVED label deliberately
says `LIQUIDITY_AT_COMPLEMENT_PRICE`, not `L5_WAS_RESTING`.

THREE LABELS, and the third is not a rounding of the second:

  PRE_FIRST_FILL_LIQUIDITY_AT_COMPLEMENT_PRICE_OBSERVED
  PRE_FIRST_FILL_LIQUIDITY_AT_COMPLEMENT_PRICE_NOT_OBSERVED
  DATA_INSUFFICIENT      no book coverage before the first fill, or the nearest
                         snapshot is older than the collector's own resync
                         bound. An absence we could not have SEEN is not an
                         absence, and collapsing it into NOT_OBSERVED is how a
                         gap in coverage becomes evidence for the reactive
                         model.

THE ANSWER, AND IT IS A NEGATIVE ONE. All 12 overlapping windows come back
OBSERVED -- and so does essentially every other price. The negative control
below sweeps the whole 1c grid at the same instant on the same token and finds
liquidity at 1175 of 1176 prices, 99.9%. These BTC15m books are quoted
continuously across the entire grid, so "was there size at price q" is
answered yes whatever q is.

SO 12/12 IS NOT EVIDENCE FOR A BOTH-SIDES-FIRST PROCESS. It is what this
dataset returns for any price at all, and reporting it as support for arm S
would be reading a constant as a signal. The control runs on every invocation
and the verdict is downgraded to NON_DISCRIMINATING automatically, so the
headline count can never be quoted without it.

Separating R from S needs a dataset where we know WHICH ORDERS ARE OURS. That
is the prospective experiment, not this.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book

OBSERVED = "PRE_FIRST_FILL_LIQUIDITY_AT_COMPLEMENT_PRICE_OBSERVED"
NOT_OBSERVED = "PRE_FIRST_FILL_LIQUIDITY_AT_COMPLEMENT_PRICE_NOT_OBSERVED"
INSUFFICIENT = "DATA_INSUFFICIENT"
# how far back before the first fill to look for the level
LOOKBACK_S = 900.0


def _side_size(row: dict, price: D, side: str) -> D:
    p_key, s_key = f"{side}_prices", f"{side}_sizes"
    for p, s in zip(row.get(p_key) or [], row.get(s_key) or []):
        if D(str(p)) == price:
            return D(str(s))
    return D("0")


def scan_before(book: Book, price: D, *, until: float,
                lookback_s: float = LOOKBACK_S) -> Dict[str, Any]:
    """Was there size at `price` on either side, at any point before `until`?

    Returns coverage as well as the answer, because "we looked and there was
    nothing" and "we could not look" are different findings.
    """
    if not book.t:
        return {"label": INSUFFICIENT, "why": "no snapshots for this token"}
    i = bisect.bisect_right(book.t, until) - 1
    if i < 0:
        return {"label": INSUFFICIENT,
                "why": "no snapshot at or before the first fill"}
    age = until - book.t[i]
    if age > MAX_CARRY_S:
        return {"label": INSUFFICIENT,
                "why": (f"nearest snapshot is {age:.1f}s old, beyond the "
                        f"collector's {MAX_CARRY_S:.0f}s resync bound")}
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None
    max_bid = D("0")
    max_ask = D("0")
    n = 0
    j = i
    while j >= 0 and until - book.t[j] <= lookback_s:
        row = book.rows[j]
        b = _side_size(row, price, "bid")
        a = _side_size(row, price, "ask")
        n += 1
        if b > 0 or a > 0:
            first_seen = book.t[j]
            last_seen = book.t[j] if last_seen is None else last_seen
            max_bid = max(max_bid, b)
            max_ask = max(max_ask, a)
        j -= 1
    if first_seen is None:
        return {"label": NOT_OBSERVED, "snapshots_examined": n,
                "coverage_s": round(until - book.t[max(j + 1, 0)], 1),
                "why": ("the book was readable before the first fill and "
                        "carried no size at this price")}
    return {
        "label": OBSERVED,
        "snapshots_examined": n,
        "earliest_seen_s_before_first_fill": round(until - first_seen, 1),
        "latest_seen_s_before_first_fill": round(until - last_seen, 1),
        "max_bid_size_at_price": str(max_bid),
        "max_ask_size_at_price": str(max_ask),
        "why": ("SIZE AT A PRICE in an anonymous book. This is NOT asserted to "
                "be L5's order, nor his counterparty's."),
    }


def windows(bf: dict, block_times: Dict[str, int]) -> List[Dict[str, Any]]:
    """Per window: the first fill, the first opposite fill, and their times."""
    out = []
    for w in bf["rows"]:
        fills = sorted(w["fills"], key=lambda f: (f.get("block") or 0,
                                                  f.get("log_index") or 0))
        if not fills:
            continue
        first = fills[0]
        opp = next((f for f in fills
                    if str(f["token_id"]) != str(first["token_id"])), None)
        t0 = block_times.get(str(first.get("block")))
        t1 = block_times.get(str(opp.get("block"))) if opp else None
        out.append({
            "market_slug": w["market_slug"],
            "n_fills": len(fills),
            "first_token": str(first["token_id"]),
            "first_price": first["price"],
            "first_role": first.get("l5_role"),
            "first_t": (float(t0) if t0 is not None else None),
            "opposite_token": (str(opp["token_id"]) if opp else None),
            "opposite_price": (opp["price"] if opp else None),
            "opposite_role": (opp.get("l5_role") if opp else None),
            "opposite_t": (float(t1) if t1 is not None else None),
        })
    return out


CONTROL_GRID = tuple(D(c) / D(100) for c in range(1, 100))
# above this share, "liquidity existed" is a property of the book rather than
# of anything L5 did, and the test cannot separate the two hypotheses
VACUOUS_ABOVE = 0.80


def control(book: Book, exclude: D, *, until: float) -> Dict[str, Any]:
    """The same question at every OTHER price on the grid, same instant.

    Without this the headline is unfalsifiable: a test that returns OBSERVED
    for everything returns OBSERVED for the complement price too.
    """
    tested = observed = 0
    for p in CONTROL_GRID:
        if p == exclude:
            continue
        tested += 1
        if scan_before(book, p, until=until)["label"] == OBSERVED:
            observed += 1
    return {"prices_tested": tested, "observed": observed,
            "observed_share": (round(observed / tested, 4) if tested else None)}


def discrimination(by_label: Dict[str, int],
                   control_share: Optional[float]) -> Dict[str, Any]:
    """Does the real answer differ from the answer at an arbitrary price?"""
    n = sum(by_label.values()) or 1
    real_share = by_label.get(OBSERVED, 0) / n
    if control_share is None:
        return {"verdict": "UNKNOWN", "why": "the control did not run"}
    if control_share >= VACUOUS_ABOVE:
        return {
            "verdict": "NON_DISCRIMINATING",
            "real_observed_share": round(real_share, 4),
            "control_observed_share": control_share,
            "why": ("liquidity is present at essentially every price on the "
                    "grid, so OBSERVED at the complement price is a property "
                    "of the book and not evidence about L5's process. This "
                    "result must NOT be cited as support for either arm."),
        }
    return {"verdict": "DISCRIMINATING",
            "real_observed_share": round(real_share, 4),
            "control_observed_share": control_share,
            "why": "the complement price differs from an arbitrary price"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times",
                    default="analysis/l5/historical_l2/block_times.json")
    ap.add_argument("--out",
                    default="analysis/l5/historical_l2/pre_fill_complement.json")
    a = ap.parse_args(argv)

    import pyarrow.parquet as pq
    d = pq.read_table(a.parquet).to_pydict()
    books: Dict[str, Book] = defaultdict(Book)
    for i, coin in enumerate(d["coin"]):
        if coin != "btc":
            continue
        books[str(d["token_id"][i])].add(
            d["captured_at"][i].timestamp(),
            {"bid_prices": d["bid_prices"][i], "bid_sizes": d["bid_sizes"][i],
             "ask_prices": d["ask_prices"][i], "ask_sizes": d["ask_sizes"][i],
             "best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i]})
    for b in books.values():
        b.sort()

    bt = json.load(open(a.block_times))["block_times"]
    bf = json.load(open(a.backfill))
    rows = []
    for w in windows(bf, bt):
        r = dict(w)
        if not w["opposite_token"] or w["first_t"] is None:
            r["verdict"] = {"label": INSUFFICIENT,
                            "why": ("no opposite-side fill in this window, or "
                                    "no block timestamp for the first fill")}
        else:
            bk = books.get(w["opposite_token"])
            if bk is None:
                r["verdict"] = {"label": INSUFFICIENT,
                                "why": "the dataset has no book for the "
                                       "complement token"}
            else:
                r["verdict"] = scan_before(bk, D(str(w["opposite_price"])),
                                           until=w["first_t"])
        if w["opposite_t"] and w["first_t"]:
            r["first_to_opposite_s"] = round(w["opposite_t"] - w["first_t"], 1)
        rows.append(r)

    ctl = {"prices_tested": 0, "observed": 0}
    for w in windows(bf, bt):
        if not w["opposite_token"] or w["first_t"] is None:
            continue
        bk = books.get(w["opposite_token"])
        if bk is None:
            continue
        one = control(bk, D(str(w["opposite_price"])), until=w["first_t"])
        ctl["prices_tested"] += one["prices_tested"]
        ctl["observed"] += one["observed"]
    ctl["observed_share"] = (round(ctl["observed"] / ctl["prices_tested"], 4)
                             if ctl["prices_tested"] else None)

    c = Counter(r["verdict"]["label"] for r in rows)
    rep = {
        "windows": len(rows),
        "by_label": dict(c),
        "negative_control": ctl,
        "discrimination": discrimination(dict(c), ctl["observed_share"]),
        "reads": ("OBSERVED is consistent with a both-sides-first process; "
                  "NOT_OBSERVED with a reactive one. Neither is proof, and "
                  "DATA_INSUFFICIENT is neither."),
        "never": ("no anonymous level in this dataset is attributed to L5 or "
                  "to any trader"),
        "rows": rows,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: rep[k] for k in ("windows", "by_label",
                                          "negative_control",
                                          "discrimination")},
                     indent=1))
    for r in rows:
        v = r["verdict"]
        print(f"\n{r['market_slug']}  fills={r['n_fills']}")
        print(f"   first    {r['first_price']} ({r['first_role']}) "
              f"token {r['first_token'][:14]}…")
        print(f"   opposite {r['opposite_price']} ({r['opposite_role']}) "
              f"+{r.get('first_to_opposite_s')}s")
        print(f"   -> {v['label']}")
        for k in ("earliest_seen_s_before_first_fill", "max_bid_size_at_price",
                  "max_ask_size_at_price", "snapshots_examined", "why"):
            if k in v:
                print(f"      {k}: {v[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
