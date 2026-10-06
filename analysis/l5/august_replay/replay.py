"""Run FROZEN Candidate S1 through one historical August BTC15m tape.

DIAGNOSTIC HISTORICAL REPLAY ONLY. This is not validation, is not part of the
prospective 25-market S1 cohort, and must not be used to modify S1 while that
cohort is running. Nothing here writes to the candidate.

WHAT IS REUSED VERBATIM, AND WHAT IS REPLACED. The frozen module's decision
logic is IMPORTED, never copied: CANDIDATE, Order, classify_fill, ladder,
size_at, best_bid, best_ask. The fingerprint is asserted before a single tick
runs. What this module replaces is ONLY the clock and the data source --
`collect_market` reads books over HTTP at wall-clock time, and here the same
policy sequence is driven off a historical tape second by second. The ORDER of
operations inside a tick is reproduced exactly as `collect_market` performs it,
including that an evidenced fill replenishes within the SAME tick.

THREE RULES THE TASK FIXES, ENFORCED STRUCTURALLY:

  NO CREDITING S1 WITH L5'S FILLS. L5's fills move L5's ledger only. S1's
  inventory changes solely through `classify_fill` returning an EVIDENCED_*
  verdict on S1's OWN hypothetical order. The two ledgers are separate dicts
  and nothing copies between them.

  NO touch=fill. S1 is never filled because its price equals the touch. Only
  the frozen classifier's evidenced verdicts move inventory; UNCERTAIN_PARTIAL
  and QUEUE_CLEARED_FRONT_OF_BOOK are recorded and EXCLUDED from economics,
  exactly as the frozen module treats them.

  NO FUTURE INFORMATION. The book at tick t is read with Book.at(t), which
  returns the latest row at or BEFORE t. Prints are taken from the interval
  (t-1, t]. Nothing at t+1 or later is ever consulted, and the tape is walked
  strictly forward.

TWO ARMS, BECAUSE THE FROZEN IMPLEMENTATION HAS A DEAD STANDARD. In the frozen
module `last_prints` is initialised empty at line 263 and NEVER ASSIGNED, so
`cum` is always 0 and EVIDENCED_TRADE_THROUGH cannot fire: live S1 polls GET
/book, which carries no trades. Arm A reproduces that faithfully (prints always
empty). Arm B supplies the real chain prints the historical tape does have, as
a sensitivity test. Both are reported; the headline is Arm A because that is
what the frozen code actually does.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import (MAX_CARRY_S, Book,
                                                  placement_consistency)
from analysis.l5.historical_l2.mint_match import feasible
from analysis.l5.august_replay.select_window import WINDOW_S, load_books
from src.polymarket_l5.owned_order_shadow import (CANDIDATE, FINGERPRINT,
                                                  Order, best_ask, best_bid,
                                                  classify_fill, size_at)

FROZEN_FINGERPRINT = ("441c375e35cc2d588b075d7d9f6a740db7c92f95c10bf7ea"
                      "76caf333fe24b6fe")
EVIDENCED = ("EVIDENCED_LEVEL_CONSUMED", "EVIDENCED_TRADE_THROUGH")
UNCERTAIN = ("UNCERTAIN_PARTIAL", "QUEUE_CLEARED_FRONT_OF_BOOK")


def assert_frozen() -> Dict[str, Any]:
    """Fail closed before any tick if the candidate is not the frozen one."""
    assert FINGERPRINT == FROZEN_FINGERPRINT, (
        "CANDIDATE CHANGED -- refusing to replay: %s" % FINGERPRINT)
    assert CANDIDATE["no_5c_extend_rule"] is True
    assert CANDIDATE["residual_control"] == "QUOTE_SUPPRESSION_ONLY"
    assert CANDIDATE["quantity_shares"] == 23.61
    assert CANDIDATE["residual_cap_shares"] == 80
    assert CANDIDATE["tte_shutdown_s"] == 60
    assert CANDIDATE["offset_from_bid_ticks"] == 0
    import src.polymarket_l5.owned_order_shadow as mod
    src = open(mod.__file__, "rb").read()
    return {"candidate_fingerprint_sha256": FINGERPRINT,
            "matches_frozen": True,
            "module_file_sha256": hashlib.sha256(src).hexdigest(),
            "classify_fill_is_imported_not_reimplemented": True}


def ladders(snap: dict) -> Tuple[List[Tuple[float, float]],
                                 List[Tuple[float, float]]]:
    """Parquet aligned arrays -> the (price, size) form the frozen code takes.

    Built from the dataset's own ladders, not from best_bid/best_ask alone,
    because `size_at` and `classify_fill` need the level the order sits on.
    """
    bid = [(float(p), float(s)) for p, s in
           zip(snap.get("bid_prices") or [], snap.get("bid_sizes") or [])]
    ask = [(float(p), float(s)) for p, s in
           zip(snap.get("ask_prices") or [], snap.get("ask_sizes") or [])]
    return bid, ask


def vwap(cost: float, qty: float) -> Optional[float]:
    return round(cost / qty, 6) if qty > 0 else None


def pair_cost(cost: Dict[str, float], inv: Dict[str, float],
              up: str, dn: str) -> Optional[float]:
    """L5's own Up cost plus his own Down cost -- the pair he actually holds.

    This is `l5_observed_pair_cost`, NOT the mint-feasibility sum. It is the
    sum of the two legs' VWAPs and is defined only once BOTH legs exist.
    """
    a, b = vwap(cost[up], inv[up]), vwap(cost[dn], inv[dn])
    return None if a is None or b is None else round(a + b, 6)


class Ledger:
    """One side's inventory, cost and derived pair economics."""

    def __init__(self, up: str, dn: str) -> None:
        self.up, self.dn = up, dn
        self.inv = {up: 0.0, dn: 0.0}
        self.cost = {up: 0.0, dn: 0.0}
        self.n_fills = 0

    def buy(self, outcome: str, px: float, qty: float) -> None:
        self.inv[outcome] += qty
        self.cost[outcome] += px * qty
        self.n_fills += 1

    @property
    def matched(self) -> float:
        return min(self.inv[self.up], self.inv[self.dn])

    @property
    def residual(self) -> float:
        return abs(self.inv[self.up] - self.inv[self.dn])

    def snap(self) -> Dict[str, Any]:
        return {"inv_up": round(self.inv[self.up], 4),
                "inv_down": round(self.inv[self.dn], 4),
                "matched": round(self.matched, 4),
                "residual": round(self.residual, 4),
                "pair_cost": pair_cost(self.cost, self.inv, self.up, self.dn),
                "n_fills": self.n_fills}


def l5_fills_by_second(fills: List[dict], start: int, end: int
                       ) -> Dict[int, List[dict]]:
    out: Dict[int, List[dict]] = {}
    for f in fills:
        ts = f.get("_ts")
        if ts is None or not (start <= ts < end):
            continue
        out.setdefault(int(ts), []).append(f)
    return out


def prints_by_second(legs: List[dict], btimes: Dict[int, int]
                     ) -> Dict[int, List[dict]]:
    out: Dict[int, List[dict]] = {}
    for lg in legs:
        ts = btimes.get(lg.get("block"))
        if ts is None:
            continue
        out.setdefault(int(ts), []).append(lg)
    return out


def replay(books: Dict[str, Book], tok_up: str, tok_dn: str,
           start: int, end: int, l5_by_s: Dict[int, List[dict]],
           prints_by_s: Dict[int, List[dict]], *, use_prints: bool
           ) -> Dict[str, Any]:
    """Walk the tape forward one second at a time. No future information."""
    names = [tok_up, tok_dn]
    label = {tok_up: "up", tok_dn: "down"}
    orders: Dict[str, Optional[Order]] = {tok_up: None, tok_dn: None}
    s1 = Ledger(tok_up, tok_dn)
    l5 = Ledger(tok_up, tok_dn)
    # the frozen module's own structure: empty unless prints are supplied
    last_prints: Dict[str, List[Tuple[float, float]]] = {n: [] for n in names}
    rows: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    unresolvable = 0
    seq = 0
    s1_uncertain: Dict[str, int] = {}

    for t in range(start, end):
        tte = end - t

        # ---- L5's ACTUAL fills at this second. They move L5's ledger ONLY.
        #
        # DELIBERATELY BEFORE THE BOOK CHECK. An L5 fill is a fact on the
        # chain and does not depend on whether the L2 capture can be resolved
        # at this second. An earlier draft skipped the whole tick when the
        # book was unresolvable, which silently DROPPED real L5 fills and
        # understated his activity -- the one direction of error that would
        # have flattered S1.
        l5_here = []
        for f in l5_by_s.get(t, []):
            tok = f["token_id"]
            if tok not in (tok_up, tok_dn):
                continue
            px, sz = float(f["price"]), float(f["size"])
            l5.buy(tok, px, sz)
            cls = f.get("_class")
            l5_here.append({"outcome": label[tok], "price": px, "size": sz,
                            "t_minus_1_class": cls,
                            "mint_consistent": f.get("_mint_consistent")})
            events.append({"t": t, "who": "L5", "kind": "fill",
                           "outcome": label[tok], "price": px, "size": sz,
                           "t_minus_1_class": cls,
                           "mint_consistent": f.get("_mint_consistent")})

        snaps = {}
        missing = False
        for tok in names:
            sp = books[tok].at(float(t), max_age_s=MAX_CARRY_S)
            if sp is None:
                missing = True
                break
            snaps[tok] = sp
        if missing:
            # Mirrors the frozen collector's book_error path, which breaks out
            # of the per-token read and `continue`s the WHOLE poll rather than
            # acting on half a book. A tick we cannot resolve is never
            # recorded as a fill-free poll -- S1's policy simply does not run.
            unresolvable += 1
            rows.append({"t": t, "tte_s": tte,
                         "book": "UNRESOLVABLE_WITHIN_CARRY_BOUND",
                         "l5_fills_this_second": l5_here,
                         "l5_ledger": l5.snap()})
            continue

        bids: Dict[str, List[Tuple[float, float]]] = {}
        asks: Dict[str, List[Tuple[float, float]]] = {}
        for tok in names:
            b, a = ladders(snaps[tok])
            bids[tok], asks[tok] = b, a

        # ---- prints observed since the previous poll: (t-1, t]
        if use_prints:
            for tok in names:
                last_prints[tok] = [
                    (float(p["price"]), float(p["size"]))
                    for p in prints_by_s.get(t, [])
                    if p["token_id"] == tok]

        # ---- FROZEN S1 policy sequence, in collect_market's exact order
        s1_fills_here = []
        for tok in names:
            o = orders[tok]
            if o is not None:
                v = classify_fill(o, bids[tok], last_prints[tok])
                if v in EVIDENCED:
                    o.state, o.evidence, o.state_t = "FILLED", v, t
                    s1.buy(tok, o.px, o.qty)
                    s1_fills_here.append({"outcome": label[tok],
                                          "price": o.px, "size": o.qty,
                                          "evidence": v,
                                          "order_id": o.oid,
                                          "age_s": t - o.placed_t,
                                          "queue_ahead_at_placement":
                                              o.queue_ahead})
                    events.append({"t": t, "who": "S1", "kind": "fill",
                                   "outcome": label[tok], "price": o.px,
                                   "size": o.qty, "evidence": v,
                                   "order_id": o.oid})
                    orders[tok] = None if CANDIDATE[
                        "replenish_after_evidenced_fill"] else o
                elif v in UNCERTAIN:
                    s1_uncertain[v] = s1_uncertain.get(v, 0) + 1
                    events.append({"t": t, "who": "S1", "kind": "uncertain",
                                   "outcome": label[tok], "verdict": v,
                                   "order_id": o.oid, "price": o.px,
                                   "excluded_from_primary_economics": True})
                    o.last_size_at = size_at(bids[tok], o.px)
            # place / replenish under suppression + TTE shutdown
            if orders[tok] is None:
                other = tok_dn if tok == tok_up else tok_up
                if s1.inv[tok] - s1.inv[other] >= CANDIDATE[
                        "residual_cap_shares"]:
                    continue
                if (end - t) < CANDIDATE["tte_shutdown_s"]:
                    continue
                p = best_bid(bids[tok])
                if p is None:
                    continue
                seq += 1
                orders[tok] = Order("replay-%d" % seq, tok, label[tok], p,
                                    CANDIDATE["quantity_shares"], t,
                                    size_at(bids[tok], p),
                                    size_at(bids[tok], p))
                events.append({"t": t, "who": "S1", "kind": "order_placed",
                               "outcome": label[tok], "price": p,
                               "order_id": orders[tok].oid,
                               "queue_ahead": orders[tok].queue_ahead})

        resting = {}
        for tok in names:
            o = orders[tok]
            resting[label[tok]] = None if o is None else {
                "order_id": o.oid, "price": o.px, "qty": o.qty,
                "age_s": t - o.placed_t,
                "queue_ahead_at_placement": o.queue_ahead,
                "size_at_level_now": size_at(bids[tok], o.px)}

        row = {"t": t, "tte_s": tte}
        for tok in names:
            n = label[tok]
            bb, ba = best_bid(bids[tok]), best_ask(asks[tok])
            row[n + "_bid"] = bb
            row[n + "_ask"] = ba
            row[n + "_bid_depth"] = size_at(bids[tok], bb) if bb is not None \
                else None
            row[n + "_ask_depth"] = size_at(asks[tok], ba) if ba is not None \
                else None
            row[n + "_carried_s"] = snaps[tok].get("carried_s")
        bbs = [row["up_bid"], row["down_bid"]]
        row["mint_sum"] = (round(bbs[0] + bbs[1], 6)
                           if None not in bbs else None)
        row["mint_feasible"] = ((row["mint_sum"] >= 1.0)
                                if row["mint_sum"] is not None else None)
        row["l5_fills_this_second"] = l5_here
        row["l5"] = l5.snap()
        row["s1_resting_orders"] = resting
        row["s1_fills_this_second"] = s1_fills_here
        row["s1"] = s1.snap()
        rows.append(row)

    return {"rows": rows, "events": events,
            "s1_final": s1.snap(), "l5_final": l5.snap(),
            "s1_uncertain_counts": s1_uncertain,
            "unresolvable_ticks": unresolvable,
            "orders_placed": seq, "used_prints": use_prints}


def divergence(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The first MATERIAL divergence, defined before it is looked for.

    Material means the economic position differs, not that a quote differs.
    The designated marker is D2 -- L5 holds a matched pair and S1 holds none,
    which is the first moment the two policies' POSITIONS, not their orders,
    have come apart. D1/D3/D4 are reported beside it so the choice of marker
    cannot hide a different story.
    """
    d: Dict[str, Any] = {"D1_first_l5_fill": None,
                         "D2_l5_matched_while_s1_zero": None,
                         "D3_first_s1_evidenced_fill": None,
                         "D4_residual_gap_ge_one_leg": None}
    leg = CANDIDATE["quantity_shares"]
    for r in rows:
        if "l5" not in r:
            continue
        if d["D1_first_l5_fill"] is None and r["l5_fills_this_second"]:
            d["D1_first_l5_fill"] = r["t"]
        if (d["D2_l5_matched_while_s1_zero"] is None
                and r["l5"]["matched"] > 0 and r["s1"]["matched"] == 0):
            d["D2_l5_matched_while_s1_zero"] = r["t"]
        if d["D3_first_s1_evidenced_fill"] is None and \
                r["s1_fills_this_second"]:
            d["D3_first_s1_evidenced_fill"] = r["t"]
        if (d["D4_residual_gap_ge_one_leg"] is None
                and abs(r["l5"]["residual"] - r["s1"]["residual"]) >= leg):
            d["D4_residual_gap_ge_one_leg"] = r["t"]
    d["designated_first_material_divergence"] = d["D2_l5_matched_while_s1_zero"]
    d["definition"] = ("D2: the first second at which L5 holds a matched pair "
                       "and S1 holds none. Chosen as the marker because it is "
                       "the first divergence in POSITION rather than in quote.")
    return d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times", required=True)
    ap.add_argument("--selection", required=True)
    ap.add_argument("--prints", default=None)
    ap.add_argument("--outdir", default="analysis/l5/august_replay")
    a = ap.parse_args(argv)

    frozen = assert_frozen()
    print(json.dumps(frozen, indent=1))

    sel = json.load(open(a.selection))
    slug = sel["selected"]
    if not slug:
        print("no window passed the selection gates")
        return 3
    srow = next(r for r in sel["rows"] if r["market_slug"] == slug)
    start, end = srow["window_start_utc"], srow["window_end_utc"]
    toks = srow["tokens"]
    tok_up = next(t for t, o in toks.items() if str(o).lower() == "up")
    tok_dn = next(t for t, o in toks.items() if str(o).lower() == "down")

    books, _ = load_books(a.parquet, slugs={slug})
    bt = {int(k): v for k, v in
          json.load(open(a.block_times))["block_times"].items()}
    bf = next(w for w in json.load(open(a.backfill))["rows"]
              if w["market_slug"] == slug)

    # classify each L5 fill at T-1 with the SAME frozen conservative
    # classifier the prior verified analysis used -- imported, not rewritten.
    comp = {tok_up: tok_dn, tok_dn: tok_up}
    fills = []
    for f in bf["fills"]:
        g = dict(f)
        g["_ts"] = bt.get(f.get("block"))
        if g["_ts"] is not None:
            bk = books.get(g["token_id"])
            snap = bk.at(g["_ts"] - 1, max_age_s=MAX_CARRY_S) if bk else None
            g["_class"] = placement_consistency(snap, g["price"])
            other = comp.get(g["token_id"])
            osnap = (books[other].at(g["_ts"] - 1, max_age_s=MAX_CARRY_S)
                     if other in books else None)
            g["_mint_consistent"] = feasible(
                g["price"], osnap.get("best_bid") if osnap else None)
        fills.append(g)
    l5_by_s = l5_fills_by_second(fills, start, end)

    pr_by_s: Dict[int, List[dict]] = {}
    prints_meta = {"supplied": False}
    if a.prints and os.path.exists(a.prints):
        pj = json.load(open(a.prints))
        pbt = {int(k): v for k, v in (pj.get("block_times") or {}).items()}
        pr_by_s = prints_by_second(pj.get("maker_order_legs") or [], pbt)
        prints_meta = {"supplied": True,
                       "n_maker_order_legs": pj.get("n_maker_order_legs"),
                       "chunk_holes": pj.get("chunk_holes"),
                       "coverage": pj.get("coverage")}

    os.makedirs(a.outdir, exist_ok=True)
    out: Dict[str, Any] = {
        "classification": "DIAGNOSTIC_HISTORICAL_REPLAY_NOT_VALIDATION",
        "not_part_of_prospective_cohort": True,
        "must_not_modify_s1_while_cohort_runs": True,
        "frozen_candidate": frozen, "candidate": CANDIDATE,
        "market_slug": slug, "window_utc": [start, end],
        "tokens": {"up": tok_up, "down": tok_dn},
        "selection_gates": srow["gates"],
        "l5_fill_classes_in_window": srow["mechanisms"],
        "prints": prints_meta,
        "rules": {
            "no_crediting_s1_with_l5_fills": ("separate ledgers; S1 inventory "
                                              "moves only on an EVIDENCED_* "
                                              "verdict on S1's own order"),
            "no_touch_equals_fill": ("only the frozen classifier's evidenced "
                                     "verdicts move inventory; uncertain "
                                     "verdicts are excluded"),
            "no_future_information": ("Book.at(t) returns the latest row at "
                                      "or before t; prints from (t-1, t]; "
                                      "the tape is walked strictly forward"),
        },
    }

    arms = {}
    for name, use in (("A_faithful_frozen_no_prints", False),
                      ("B_sensitivity_with_chain_prints", True)):
        if use and not pr_by_s:
            arms[name] = {"skipped": "no prints supplied"}
            continue
        r = replay(books, tok_up, tok_dn, start, end, l5_by_s, pr_by_s,
                   use_prints=use)
        r["divergence"] = divergence(r["rows"])
        arms[name] = {k: v for k, v in r.items() if k != "rows"}
        # the full chronological trace, one row per second
        with open(os.path.join(a.outdir, "TRACE_%s.jsonl" % name), "w") as fh:
            for row in r["rows"]:
                fh.write(json.dumps(row, default=str) + "\n")
        cols = ["t", "tte_s", "up_bid", "up_bid_depth", "up_ask",
                "up_ask_depth", "down_bid", "down_bid_depth", "down_ask",
                "down_ask_depth", "mint_sum", "mint_feasible",
                "l5_fills", "l5_inv_up", "l5_inv_down", "l5_matched",
                "l5_residual", "l5_pair_cost",
                "s1_resting_up", "s1_resting_down", "s1_fills",
                "s1_inv_up", "s1_inv_down", "s1_matched", "s1_residual",
                "s1_pair_cost"]
        with open(os.path.join(a.outdir, "TRACE_%s.csv" % name), "w",
                  newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for row in r["rows"]:
                if "l5" not in row:
                    w.writerow([row["t"], row.get("tte_s"),
                                "UNRESOLVABLE"] + [""] * (len(cols) - 3))
                    continue
                rest = row["s1_resting_orders"]
                def fmt(fs):
                    return ";".join("%s@%.4f x%.2f%s" % (
                        f["outcome"], f["price"], f["size"],
                        "/" + f["evidence"] if f.get("evidence") else
                        ("/" + str(f.get("t_minus_1_class") or ""))) 
                        for f in fs)
                def rfmt(o):
                    return "" if not o else "%.4f q%.2f age%ds ahead%.2f" % (
                        o["price"], o["qty"], o["age_s"],
                        o["queue_ahead_at_placement"])
                w.writerow([
                    row["t"], row["tte_s"],
                    row["up_bid"], row["up_bid_depth"], row["up_ask"],
                    row["up_ask_depth"], row["down_bid"],
                    row["down_bid_depth"], row["down_ask"],
                    row["down_ask_depth"], row["mint_sum"],
                    row["mint_feasible"],
                    fmt(row["l5_fills_this_second"]),
                    row["l5"]["inv_up"], row["l5"]["inv_down"],
                    row["l5"]["matched"], row["l5"]["residual"],
                    row["l5"]["pair_cost"],
                    rfmt(rest.get("up")), rfmt(rest.get("down")),
                    fmt(row["s1_fills_this_second"]),
                    row["s1"]["inv_up"], row["s1"]["inv_down"],
                    row["s1"]["matched"], row["s1"]["residual"],
                    row["s1"]["pair_cost"]])
    out["arms"] = arms
    with open(os.path.join(a.outdir, "REPLAY.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in out.items()
                      if k not in ("candidate", "arms")}, indent=1))
    for k, v in arms.items():
        print("\n==", k)
        print(json.dumps({x: y for x, y in v.items() if x != "events"},
                         indent=1, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
