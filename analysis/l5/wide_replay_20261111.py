"""Wide replay of inventory recovery, out of sample.

TWO DATA SETS, both read back from this service's own production logs and
the venue's public market records (data/l5/recent/wide_20261111/):

  S. STRAND EPISODES. Every BTC15m market this account settled since
     2026-10-25 in which it held unmatched inventory, with the venue book
     (eight levels a side, with sizes) sampled every five seconds for as
     long as it was unmatched, the venue-confirmed cost of the held leg and
     the venue's settlement. The first leg is REAL: its side, size, cost
     and the adverse move after it are what happened. Only the completion
     policy is replayed.

  W. FULL WINDOWS. Every BTC15m window from 2026-11-10 18:15Z on, traded or
     not, as a bid/ask path every five to seven seconds with the venue's
     outcome. The whole policy -- opening and completion -- is modelled.

NOTHING HERE IS REALIZED P&L. A modelled fill is not a fill. Fees are zero
because every venue-confirmed fill of ours was charged 0.0000; the fee rows
charge the venue's published coefficient on every fill as the adverse case.
No rebate is assumed anywhere.

NO POLICY IS CHOSEN ON THE WINDOWS IT IS SCORED ON. Each set is split in
time: the earlier half picks, the later half scores.
"""
from __future__ import annotations

import itertools
import json
import os
import random
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

import replay_20261111 as RP

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "..", "data", "l5", "recent", "wide_20261111")
TICK = D("0.01")
FEE = D("0.095")                       # the venue's published coefficient
#: one round at a time at the deployed size; the recovery behaviours
ONE = dict(concurrent=1, cash=D("0.90"), allocations=3, open_after=9.0)
REC = dict(reprice_on_fill=True, hedge_depth=0, rest_hedge=True)


def _j(name: str) -> Any:
    with open(os.path.join(DATA, name)) as f:
        return json.load(f)


def _floor(x: D) -> D:
    return (x / TICK).to_integral_value(rounding="ROUND_FLOOR") * TICK


# ====================================================================== S
def episodes() -> List[Dict[str, Any]]:
    settled, residual = _j("settled.json"), _j("residual.json")
    out = []
    for slug in sorted(residual):
        rows, s = residual[slug], settled.get(slug)
        if not rows or not s:
            continue
        first = rows[0]
        try:
            signed = D(str(first["residual"]))
            yes = D(str(s["yes_value"]))
        except Exception:                                      # noqa: BLE001
            continue
        if signed == 0 or yes not in (D(0), D(1)):
            continue
        held = "UP" if signed > 0 else "DOWN"
        q = abs(signed)
        hq = D(str(s["up_qty" if held == "UP" else "dn_qty"] or 0))
        hc = D(str(s["up_cost" if held == "UP" else "dn_cost"] or 0))
        if hq <= 0:
            continue
        avg = hc / hq
        # a leg booked at the $1/share bound has no measured cost
        if avg >= D("0.99") or avg <= D("0.01"):
            continue
        book = []
        for r in rows:
            try:
                bids = [(D(p), D(z)) for p, z in (r.get("bids") or [])]
                asks = [(D(p), D(z)) for p, z in (r.get("asks") or [])]
            except Exception:                                  # noqa: BLE001
                continue
            if not bids or not asks or bids[0][0] >= asks[0][0]:
                continue
            book.append((float(r["t"]), bids, asks))
        if len(book) < 5:
            continue
        out.append({"slug": slug, "held": held, "qty": q, "avg": avg,
                    "t0": book[0][0], "last_t": book[-1][0],
                    "won": (yes == 1) == (held == "UP"), "book": book,
                    "production": {
                        "matched_qty": s.get("matched_qty"),
                        "residual_qty": s.get("residual_qty"),
                        "settlement_pnl": s.get("settlement_pnl")}})
    return out


def sim_episode(ep: Dict[str, Any], *, target: Optional[D],
                terminal: float, delay: float = 8.0, latency: float = 0.0,
                queue: str = "THROUGH", fee: D = D(0)) -> Dict[str, Any]:
    """The missing leg as a maker quote at min(touch, target - cost).

    `target` None is HOLD: nothing is quoted. `queue` THROUGH fills only
    when the other side of the book reaches our price, so everything ahead
    of us has traded. DEPLETION also counts a fall in the size displayed
    at our level, while it is the best level, as trades ahead of us and
    then at us -- the optimistic bound, since some of that is cancels.
    """
    held, q0, avg = ep["held"], ep["qty"], ep["avg"]
    remaining, locked, fees = q0, D(0), D(0)
    fills: List[Tuple[float, D, D]] = []
    if target is not None:
        cap = _floor(target - avg)
        resting: Optional[D] = None
        ahead: Optional[D] = None
        prev_size: Optional[D] = None
        live_from = ep["t0"] + delay + latency
        for t, bids, asks in ep["book"]:
            if t >= terminal or remaining <= 0:
                break
            if t < live_from:
                continue
            bb, ba = bids[0][0], asks[0][0]
            if held == "DOWN":                 # we buy UP: a YES bid
                touch, opp = bb, ba
                level = dict(bids)
            else:                              # we buy DOWN: a YES offer
                touch, opp = D(1) - ba, D(1) - bb
                level = {D(1) - p: z for p, z in asks}
            if resting is not None:
                if opp <= resting:             # the book came to us
                    fills.append((t, remaining, resting))
                    remaining = D(0)
                    break
                if queue == "DEPLETION" and resting == touch \
                        and ahead is not None and prev_size is not None:
                    size = level.get(resting, D(0))
                    traded = max(D(0), prev_size - size)
                    ahead -= traded
                    if ahead < 0:
                        f = min(remaining, -ahead)
                        fills.append((t, f, resting))
                        remaining -= f
                        ahead = D(0)
                    prev_size = size
            want = min(touch, cap, opp - TICK)
            if want >= TICK and (resting is None or want > resting):
                resting = want
                # behind everything displayed at that price; a level not in
                # the eight shown has an unknown queue and waits for a
                # trade-through
                ahead = level.get(want)
                prev_size = level.get(want)
            elif resting is not None and resting == touch:
                prev_size = level.get(resting, prev_size)
        for _t, f, px in fills:
            locked += f * (D(1) - avg - px)
            fees += fee * px * (D(1) - px) * f
    fees += fee * avg * (D(1) - avg) * q0 if fee else D(0)
    resid_pnl = remaining * ((D(1) if ep["won"] else D(0)) - avg)
    done = fills[-1][0] if (fills and remaining <= 0) else None
    return {"pairs": q0 - remaining, "unmatched": remaining,
            "locked": locked, "residual_pnl": resid_pnl, "fees": fees,
            "pnl": locked + resid_pnl - fees,
            "pair_cost": ((avg + sum(f * p for _t, f, p in fills)
                           / (q0 - remaining)) if q0 > remaining else None),
            "seconds_unmatched": ((done if done is not None
                                   else min(terminal, ep["last_t"]))
                                  - ep["t0"]),
            "covered": ep["last_t"] >= min(terminal, 880.0) - 15.0
            or remaining <= 0}


S_GRID = [(tg, term) for tg in (D("0.98"), D("1.00"))
          for term in (600.0, 780.0, 840.0)]


def s_label(tg: Optional[D], term: float) -> str:
    if tg is None:
        return "hold to settlement"
    return "target %s, completion to %s" % (
        tg, {600.0: "T+600 (today)", 780.0: "minute 13",
             840.0: "minute 14"}[term])


# ====================================================================== W
def windows_380() -> List[Dict[str, Any]]:
    """The larger, earlier set: 380 windows, 2026-11-03 to 11-10, the venue
    book about every 2.6 s from roughly T+55 to T+816, with the venue's
    settlement. Independent of every window this account traded."""
    raw = _j("books_380.json")
    out = []
    for slug in sorted(raw):
        path = [(float(t), D(b), D(a)) for t, b, a in raw[slug]["rows"]]
        path = [x for x in path if D(0) < x[1] < x[2] < D(1)]
        if len(path) < 150 or path[0][0] > 120 or path[-1][0] < 780:
            continue
        out.append({"slug": slug, "path": path,
                    "winner": raw[slug]["winner"]})
    return out


HOLD = dict(reprice_on_fill=True, complete_ceiling=D("0"), rest_hedge=False)
W380_POLICIES: List[Tuple[str, Dict[str, Any]]] = [
    ("deployed", {}),
    ("first leg held, never completed", dict(HOLD)),
]
for _tg, _term in itertools.product((D("0.98"), D("1.00")),
                                    (600.0, 780.0, 840.0)):
    W380_POLICIES.append((
        "recovery %s, to %d s" % (_tg, _term),
        dict(complete_ceiling=_tg, terminal=_term, **REC)))
W380_POLICIES += [
    ("recovery 0.98, to 600 s, 15 s quotes",
     dict(complete_ceiling=D("0.98"), terminal=600.0, life=15.0,
          attempts=16, **REC)),
    ("recovery 0.98, to 840 s, 15 s quotes",
     dict(complete_ceiling=D("0.98"), terminal=840.0, life=15.0,
          attempts=16, **REC)),
    ("recovery 0.98, to 840 s, entry to 780 s",
     dict(complete_ceiling=D("0.98"), terminal=840.0, entry_cutoff=780.0,
          **REC)),
]


def w380() -> Dict[str, Any]:
    ws = windows_380()
    tune, score = split(ws)
    print("W380. INDEPENDENT WINDOWS: %d (%s .. %s); tune %d, score %d" % (
        len(ws), ws[0]["slug"][-16:], ws[-1]["slug"][-16:], len(tune),
        len(score)))
    rows = []
    for conc in (1, 2):
        for lat, through, fee in ((0.0, False, D(0)), (6.0, True, D(0)),
                                  (6.0, True, FEE)):
            base_b = None
            for label, over in W380_POLICIES:
                o = dict(ONE, open_after=0.0, concurrent=conc,
                         cancel_latency=lat, through_only=through,
                         fee_coefficient=fee, **over)
                a = [RP.run((w["path"], w["winner"]), **o) for w in tune]
                b = [RP.run((w["path"], w["winner"]), **o) for w in score]
                if label == "deployed":
                    base_b = b
                rows.append({
                    "rounds": conc,
                    "assumption": "%s, cancel %ds late, fees %s" % (
                        "must trade through" if through else "touch fills",
                        lat, "charged" if fee else "as observed"),
                    "policy": label,
                    "tune": stats([r["pnl"] for r in a]),
                    "score": stats([r["pnl"] for r in b]),
                    "score_vs_deployed": paired(
                        [r["pnl"] for r in b], [r["pnl"] for r in base_b]),
                    "score_pairs": str(sum(r["pairs_contracts"] for r in b)),
                    "score_unmatched": str(sum(r["unmatched"] for r in b)),
                    "score_fills": sum(r["fills"] for r in b),
                    "score_locked": str(sum(r["locked_pair_pnl"] for r in b
                                            ).quantize(D("0.0001"))),
                    "score_max_exposure": str(max(
                        r["max_at_risk"] for r in b).quantize(D("0.0001")))})
    last = None
    for r in rows:
        key = (r["rounds"], r["assumption"])
        if key != last:
            last = key
            print("  -- %d round(s) at a time; %s" % key)
            print("     %-40s %8s | %8s %-24s %-25s %6s %7s %7s %7s %8s" % (
                "policy", "tune", "SCORE", "a window [95%]",
                "vs deployed [95%]", "pairs", "unmatch", "locked", "worst",
                "drawdown"))
        s, v = r["score"], r["score_vs_deployed"]
        print("     %-40s %+8.3f | %+8.3f %+.4f [%+.4f,%+.4f] %+.4f "
              "[%+.4f,%+.4f] %6s %7s %7s %+7.3f %8.3f" % (
                  r["policy"][:40], r["tune"]["total"], s["total"],
                  s["mean"], s["ci95"][0], s["ci95"][1], v["mean"],
                  v["ci95"][0], v["ci95"][1], r["score_pairs"],
                  r["score_unmatched"], r["score_locked"], s["worst"],
                  s["max_drawdown"]))
    picks = {}
    for key in sorted({(r["rounds"], r["assumption"]) for r in rows}):
        rs = [r for r in rows if (r["rounds"], r["assumption"]) == key]
        best = max(rs, key=lambda r: r["tune"]["total"])
        picks["%d round(s); %s" % key] = {
            "picked_on_tune": best["policy"], "its_score": best["score"],
            "its_score_vs_deployed": best["score_vs_deployed"]}
        print("  PICK [%d round(s); %s]: %s -> scored %+.3f, %+.4f a window "
              "[%+.4f, %+.4f]" % (key[0], key[1], best["policy"],
                                  best["score"]["total"],
                                  best["score"]["mean"],
                                  best["score"]["ci95"][0],
                                  best["score"]["ci95"][1]))
    return {"rows": rows, "picks": picks}


OPENING_VARIANTS: List[Tuple[str, Dict[str, Any]]] = [
    ("deployed (2 ticks behind, floor 0.30)", {}),
    ("opening 4 ticks behind", dict(depth=4)),
    ("opening 6 ticks behind", dict(depth=6)),
    ("opening 8 ticks behind", dict(depth=8)),
    ("floor 0.05", dict(floor=D("0.05"))),
    ("floor 0.40", dict(floor=D("0.40"))),
    ("entry stops at 240 s", dict(entry_cutoff=240.0)),
    ("entry stops at 120 s", dict(entry_cutoff=120.0)),
    ("calm 30 s within 1 tick", dict(calm_s=30.0, calm_ticks=1)),
    ("calm 60 s within 2 ticks", dict(calm_s=60.0, calm_ticks=2)),
    ("calm 120 s within 3 ticks", dict(calm_s=120.0, calm_ticks=3)),
    # COMBINED AFTER SEEING THE SCORE HALF of the two rows it joins, so its
    # score on the 380 set is NOT out of sample. Its only clean test is the
    # later windows below, which played no part in choosing it.
    ("8 ticks behind, floor 0.40 (chosen late)",
     dict(depth=8, floor=D("0.40"))),
]


def opening_variants() -> Dict[str, Any]:
    """Where the loss comes from, and whether any opening rule removes it."""
    base = dict(ONE, open_after=0.0, cancel_latency=6.0, through_only=True)
    ws = windows_380()
    tune, score = split(ws)
    later = windows()
    out = []
    print("O. OPENING VARIANTS, one round, must trade through, cancel 6 s "
          "late. 380 set: tune %d, score %d; LATER windows %d (11-10 18:45Z "
          "on)" % (len(tune), len(score), len(later)))
    dep_b = dep_l = None
    for label, over in OPENING_VARIANTS:
        o = dict(base, **over)
        a = [RP.run((w["path"], w["winner"]), **o) for w in tune]
        b = [RP.run((w["path"], w["winner"]), **o) for w in score]
        c = [RP.run((w["path"], w["winner"]), **dict(o, open_after=9.0))
             for w in later]
        if dep_b is None:
            dep_b, dep_l = b, c
        pairs = sum(r["pairs_contracts"] for r in b)
        unm = sum(r["unmatched"] for r in b)
        locked = sum(r["locked_pair_pnl"] for r in b)
        s = stats([r["pnl"] for r in b])
        row = {"policy": label, "tune": stats([r["pnl"] for r in a]),
               "score": s,
               "score_vs_deployed": paired([r["pnl"] for r in b],
                                           [r["pnl"] for r in dep_b]),
               "score_pairs": str(pairs), "score_unmatched": str(unm),
               "score_locked": str(locked.quantize(D("0.0001"))),
               "score_unmatched_pnl": str((D(str(s["total"])) - locked
                                           ).quantize(D("0.0001"))),
               "later": stats([r["pnl"] for r in c]),
               "later_vs_deployed": paired([r["pnl"] for r in c],
                                           [r["pnl"] for r in dep_l]),
               "later_pairs": str(sum(r["pairs_contracts"] for r in c)),
               "later_unmatched": str(sum(r["unmatched"] for r in c))}
        out.append(row)
        share = float(unm / (pairs + unm)) if pairs + unm else 0.0
        print("  %-40s tune %+7.3f | score %+7.3f %+.4f [%+.4f,%+.4f] pairs "
              "%6s unmatched %6s (%2.0f%%) locked %+.3f unmatched pnl %+.3f"
              " | LATER %+7.3f %+.4f [%+.4f,%+.4f] vs deployed %+.4f "
              "[%+.4f,%+.4f]" % (
                  label[:40], row["tune"]["total"], s["total"], s["mean"],
                  s["ci95"][0], s["ci95"][1], pairs, unm, 100 * share,
                  locked, D(str(s["total"])) - locked,
                  row["later"]["total"], row["later"]["mean"],
                  row["later"]["ci95"][0], row["later"]["ci95"][1],
                  row["later_vs_deployed"]["mean"],
                  row["later_vs_deployed"]["ci95"][0],
                  row["later_vs_deployed"]["ci95"][1]))
    return {"rows": out}


def windows() -> List[Dict[str, Any]]:
    books, outcomes = _j("books.json"), _j("outcomes.json")
    out = []
    for slug in sorted(books):
        oc = outcomes.get(slug)
        try:
            yes = json.loads(oc) if isinstance(oc, str) else oc
            winner = {"1": "UP", "0": "DOWN"}[str(yes[0])]
        except Exception:                                      # noqa: BLE001
            continue
        path = [(float(t), D(b), D(a)) for t, b, a in books[slug]]
        path = [x for x in path if D(0) < x[1] < x[2] < D(1)]
        if len(path) < 60 or path[0][0] > 30 or path[-1][0] < 800:
            continue
        out.append({"slug": slug, "path": path, "winner": winner})
    return out


W_POLICIES: List[Tuple[str, Dict[str, Any]]] = [("deployed", dict(ONE))]
for _tg, _term, _conc, _life in itertools.product(
        (D("0.98"), D("1.00")), (600.0, 780.0, 840.0), (1, 2), (120.0, 15.0)):
    W_POLICIES.append((
        "recovery %s, to %d s, %d round%s, %d s quotes" % (
            _tg, _term, _conc, "" if _conc == 1 else "s", _life),
        dict(ONE, concurrent=_conc, complete_ceiling=_tg, terminal=_term,
             life=_life, attempts=16, **REC)))


def run_window(w: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    return RP.run((w["path"], w["winner"]), **over)


# ============================================================== statistics
def stats(pnls: Sequence[D]) -> Dict[str, Any]:
    xs = [float(x) for x in pnls]
    n = len(xs)
    if not n:
        return {"n": 0}
    cum, peak, dd = 0.0, 0.0, 0.0
    for x in xs:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    rnd = random.Random(20261111)
    means = sorted(sum(rnd.choice(xs) for _ in range(n)) / n
                   for _ in range(2000))
    return {"n": n, "total": round(sum(xs), 4), "mean": round(sum(xs) / n, 5),
            "ci95": [round(means[50], 5), round(means[1949], 5)],
            "worst": round(min(xs), 4), "max_drawdown": round(dd, 4),
            "share_positive": round(sum(x > 0 for x in xs) / n, 3)}


def paired(a: Sequence[D], b: Sequence[D]) -> Dict[str, Any]:
    return stats([x - y for x, y in zip(a, b)])


def split(xs: List[Any]) -> Tuple[List[Any], List[Any]]:
    h = len(xs) // 2
    return xs[:h], xs[h:]


def main() -> None:
    out: Dict[str, Any] = {}
    # ------------------------------------------------------------- S
    eps = episodes()
    tune, test = split(eps)
    print("S. STRAND EPISODES: %d (%s .. %s); tune %d, score %d" % (
        len(eps), eps[0]["slug"][-16:], eps[-1]["slug"][-16:],
        len(tune), len(test)))
    print("   unmatched at the first sample %.2f contracts at an average "
          "cost %.4f; the held side won %d of %d" % (
              float(sum(e["qty"] for e in eps)),
              float(sum(e["qty"] * e["avg"] for e in eps)
                    / sum(e["qty"] for e in eps)),
              sum(e["won"] for e in eps), len(eps)))
    srows = []
    cfgs: List[Tuple[Optional[D], float]] = [(None, 900.0)] + S_GRID
    for qmode, lat, fee in (("THROUGH", 0.0, D(0)), ("THROUGH", 6.0, D(0)),
                            ("DEPLETION", 0.0, D(0)),
                            ("THROUGH", 6.0, FEE)):
        for tg, term in cfgs:
            def go(es: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
                return [sim_episode(e, target=tg, terminal=term, queue=qmode,
                                    latency=lat, fee=fee) for e in es]
            a, b = go(tune), go(test)
            hold = [sim_episode(e, target=None, terminal=900.0, fee=fee)
                    for e in test]
            srows.append({
                "assumption": "%s, cancel %ds late, fees %s" % (
                    qmode.lower(), lat, "charged" if fee else "as observed"),
                "policy": s_label(tg, term),
                "tune": stats([r["pnl"] for r in a]),
                "score": stats([r["pnl"] for r in b]),
                "score_vs_hold": paired([r["pnl"] for r in b],
                                        [r["pnl"] for r in hold]),
                "score_pairs": str(sum(r["pairs"] for r in b)),
                "score_unmatched": str(sum(r["unmatched"] for r in b)),
                "score_completed_share": round(
                    sum(r["unmatched"] <= 0 for r in b) / max(1, len(b)), 3),
                "score_locked": str(sum(r["locked"] for r in b).quantize(
                    D("0.0001"))),
                "score_seconds_unmatched_median": sorted(
                    r["seconds_unmatched"] for r in b)[len(b) // 2],
                "score_worst_exposure_usd": str(max(
                    (e["qty"] * e["avg"] for e in test)).quantize(
                        D("0.0001"))),
                "score_not_covered": sum(not r["covered"] for r in b)})
    out["strand_episodes"] = srows
    last = None
    for r in srows:
        if r["assumption"] != last:
            last = r["assumption"]
            print("  -- %s" % last)
            print("     %-40s %9s %9s | %9s %-20s %8s %6s %7s %7s %8s" % (
                "policy", "tune tot", "tune avg", "SCORE tot", "avg [95%]",
                "vs hold", "pairs", "unmatch", "done %", "drawdown"))
        s = r["score"]
        print("     %-40s %+9.4f %+9.5f | %+9.4f %+.4f [%+.4f,%+.4f] %+8.4f "
              "%6s %7s %6.0f%% %8.4f" % (
                  r["policy"][:40], r["tune"]["total"], r["tune"]["mean"],
                  s["total"], s["mean"], s["ci95"][0], s["ci95"][1],
                  r["score_vs_hold"]["total"], r["score_pairs"],
                  r["score_unmatched"], 100 * r["score_completed_share"],
                  s["max_drawdown"]))
    print("  PAIRED, per contract (each window weighted equally): policy "
          "minus hold; through, cancel 6 s late")
    pc_rows = []
    for tag, es in (("tune", tune), ("score", test), ("all", eps)):
        for tg, term in S_GRID:
            a = [sim_episode(e, target=tg, terminal=term, latency=6.0)
                 for e in es]
            b = [sim_episode(e, target=None, terminal=900.0) for e in es]
            d = stats([(x["pnl"] - y["pnl"]) / e["qty"]
                       for x, y, e in zip(a, b, es)])
            pol = stats([x["pnl"] / e["qty"] for x, e in zip(a, es)])
            hold = stats([y["pnl"] / e["qty"] for y, e in zip(b, es)])
            pc_rows.append({"set": tag, "policy": s_label(tg, term),
                            "policy_minus_hold": d, "policy": pol,
                            "hold": hold, "label": s_label(tg, term)})
            print("     %-5s %-40s diff %+.4f [%+.4f,%+.4f] | policy %+.4f "
                  "[%+.4f,%+.4f] | hold %+.4f [%+.4f,%+.4f]" % (
                      tag, s_label(tg, term)[:40], d["mean"], d["ci95"][0],
                      d["ci95"][1], pol["mean"], pol["ci95"][0],
                      pol["ci95"][1], hold["mean"], hold["ci95"][0],
                      hold["ci95"][1]))
    out["strand_per_contract"] = pc_rows
    # which policy the EARLIER half picks, and what it then scored
    picks = {}
    for a in sorted({r["assumption"] for r in srows}):
        rows = [r for r in srows if r["assumption"] == a]
        best = max(rows, key=lambda r: r["tune"]["total"])
        picks[a] = {"picked_on_tune": best["policy"],
                    "its_score": best["score"],
                    "its_score_vs_hold": best["score_vs_hold"]}
        print("  PICK under [%s]: %s -> scored %+.4f total, %+.5f a window "
              "[%+.5f, %+.5f]" % (a, best["policy"], best["score"]["total"],
                                  best["score"]["mean"],
                                  best["score"]["ci95"][0],
                                  best["score"]["ci95"][1]))
    out["strand_picks"] = picks
    # ------------------------------------------------------------- W
    ws = windows()
    wt, wsc = split(ws)
    print("W. FULL WINDOWS: %d (%s .. %s); tune %d, score %d" % (
        len(ws), ws[0]["slug"][-16:], ws[-1]["slug"][-16:], len(wt),
        len(wsc)))
    wrows = []
    for lat, through, fee in ((0.0, False, D(0)), (6.0, True, D(0)),
                              (6.0, True, FEE)):
        for label, over in W_POLICIES:
            o = dict(over, cancel_latency=lat, through_only=through,
                     fee_coefficient=fee)
            a = [run_window(w, o) for w in wt]
            b = [run_window(w, o) for w in wsc]
            wrows.append({
                "assumption": "%s, cancel %ds late, fees %s" % (
                    "must trade through" if through else "touch fills",
                    lat, "charged" if fee else "as observed"),
                "policy": label,
                "tune": stats([r["pnl"] for r in a]),
                "score": stats([r["pnl"] for r in b]),
                "score_pairs": str(sum(r["pairs_contracts"] for r in b)),
                "score_unmatched": str(sum(r["unmatched"] for r in b)),
                "score_fills": sum(r["fills"] for r in b),
                "score_locked": str(sum(r["locked_pair_pnl"] for r in b
                                        ).quantize(D("0.0001"))),
                "score_max_exposure": str(max(r["max_at_risk"] for r in b
                                              ).quantize(D("0.0001")))})
    out["full_windows"] = wrows
    last = None
    for r in wrows:
        if r["assumption"] != last:
            last = r["assumption"]
            print("  -- %s" % last)
            print("     %-46s %9s | %9s %-22s %6s %7s %7s %8s %8s %7s" % (
                "policy", "tune tot", "SCORE tot", "avg [95%]", "pairs",
                "unmatch", "locked", "worst", "drawdown", "max $"))
        s = r["score"]
        print("     %-46s %+9.4f | %+9.4f %+.4f [%+.4f,%+.4f] %6s %7s %7s "
              "%+8.4f %8.4f %7s" % (
                  r["policy"][:46], r["tune"]["total"], s["total"],
                  s["mean"], s["ci95"][0], s["ci95"][1], r["score_pairs"],
                  r["score_unmatched"], r["score_locked"], s["worst"],
                  s["max_drawdown"], r["score_max_exposure"]))
    wp = {}
    for a in sorted({r["assumption"] for r in wrows}):
        rows = [r for r in wrows if r["assumption"] == a]
        best = max(rows, key=lambda r: r["tune"]["total"])
        base = next(r for r in rows if r["policy"] == "deployed")
        wp[a] = {"picked_on_tune": best["policy"],
                 "its_score": best["score"], "deployed_score": base["score"]}
        print("  PICK under [%s]: %s -> scored %+.4f total, %+.5f a window "
              "[%+.5f, %+.5f]; deployed scored %+.4f" % (
                  a, best["policy"], best["score"]["total"],
                  best["score"]["mean"], best["score"]["ci95"][0],
                  best["score"]["ci95"][1], base["score"]["total"]))
    out["full_window_picks"] = wp
    out["independent_380"] = w380()
    out["opening_variants"] = opening_variants()
    with open(os.path.join(HERE, "wide_replay_20261111.json"), "w") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    import sys
    if "--opening" in sys.argv:
        _o = opening_variants()
        _p = os.path.join(HERE, "wide_replay_20261111.json")
        _all = json.load(open(_p)) if os.path.exists(_p) else {}
        _all["opening_variants"] = _o
        json.dump(_all, open(_p, "w"), indent=1)
    else:
        main()
