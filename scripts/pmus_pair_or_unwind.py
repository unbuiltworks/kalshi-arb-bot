#!/usr/bin/env python3
"""PMUS PAIR_OR_UNWIND shadow on the authenticated BTC15m tape.

Source: data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl, harvested from the
EXISTING pmus-ws-evidence-recorder (project arb-ev-wallet-shadow/production).
Nothing was deployed, restarted or re-capped to produce it.

ONLY SCORABLE TRADES MAY FILL. A trade is scorable under the already-established
acceptance rules: `dir` known, bid_at_trade/ask_at_trade present, a preceding
book row exists, that row carries bid_qty and ask_qty, and book_age <= 2000 ms.
11,624 of 16,620 trades qualify. UNSCORABLE stays UNKNOWN and fills nothing.

QUEUE DISCIPLINE, unchanged: no touch=fill; a fill needs evidenced queue
consumption by a trade whose `dir` says it consumed our side; queue-ahead is
the displayed touch size from the preceding book row; there is no depth beyond
the touch so a quote resting behind it can never fill.

  dir "buy"  -> taker bought -> consumed the ASK (our resting sell)
  dir "sell" -> taker sold   -> consumed the BID (our resting buy)

THREE DISTINCT EXIT PATHS, reported separately even where the venue action
coincides:
  PAIR_*    complement acquisition -- acquire the opposite outcome, allowed
            only when executable pair cost <= the pre-registered ceiling
  UNWIND_*  original-leg sale -- sell the first leg on its own economics
  FORCED    deadline/loss-taking exit, never counted as a qualifying exit

and within the first two:
  _MAKER    passive resting exit, filled only by evidenced queue consumption
  _TAKER    IMMEDIATELY EXECUTABLE exit: existing displayed depth already gives
            profitable economics after the taker fee, so no passive fill is
            required. This is a real execution path, not an assumption: it is
            taken only against size actually displayed at the touch.

A STRUCTURAL NOTE, stated rather than hidden: this tape carries ONE book per
slug (a single bid/ask in YES terms). Acquiring the complement and selling the
first leg therefore resolve to the same venue action on the same book side, so
their queue and depth mechanics are IDENTICAL here. Both paths are implemented
and reported separately as instructed, and the fact that they coincide is a
property of the data, not of the strategy. If PMUS exposes a separate NO-token
book, this tape does not show it.

Fees: the authoritative schedule in `src/pmus_fees.py` (taker theta 0.0695 from
2026-09-17, maker rebate 0.0125), never a local copy of the numbers.
No parameter is fitted. Ceilings are the pre-registered .99/.98/.97/.95.
"""
from __future__ import annotations
import json, os, sys, bisect, collections, statistics
from decimal import Decimal as D, getcontext
import datetime as dt

getcontext().prec = 28
TAPE = "data/pmus_ws_tape/pmus_btc15m_auth_tape.jsonl"
OUT = "analysis/l5/pmus_rewards/PMUS_PAIR_OR_UNWIND.json"
CEILINGS = (D("0.99"), D("0.98"), D("0.97"), D("0.95"))
QS = (5, 25, 100)
DEADLINE_S = 90
FRESH_MS = 2000
import sys as _sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_sys.path.insert(0, _ROOT)
from src import pmus_fees as _F                                   # noqa: E402

# authoritative, not transcribed: 0.0695 from 2026-09-17, 0.06 before it.
TAKER = _F.taker_theta()
MAKER_REB = _F.maker_theta()


def fee(p, q):
    return TAKER * p * (1 - p) * q


def reb(p, q):
    return MAKER_REB * p * (1 - p) * q


def window_end(slug):
    # cpc-btc-updown-15m-YYYY-MM-DD-HHMMz
    tail = slug.rsplit("-", 1)[-1].rstrip("z")
    date = "-".join(slug.split("-")[4:7])
    t = dt.datetime.strptime(date + tail, "%Y-%m-%d%H%M").replace(tzinfo=dt.timezone.utc)
    return (t + dt.timedelta(minutes=15)).timestamp()


def load():
    tr = collections.defaultdict(list)
    bk = collections.defaultdict(list)
    for line in open(TAPE):
        r = json.loads(line)
        (tr if r["kind"] == "trade" else bk)[r["slug"]].append(r)
    for s in tr:
        tr[s].sort(key=lambda x: x["t"])
    for s in bk:
        bk[s].sort(key=lambda x: x["t"])
    return tr, bk


def scorable(tr, bk):
    """Attach the preceding book row; drop everything UNSCORABLE."""
    out = collections.defaultdict(list)
    dropped = collections.Counter()
    for s, ts in tr.items():
        b = bk.get(s) or []
        bt = [x["t"] for x in b]
        for x in ts:
            if x.get("dir") in (None, "", "unknown"):
                dropped["unknown_dir"] += 1; continue
            if not x.get("bid_at_trade") or not x.get("ask_at_trade"):
                dropped["no_touch"] += 1; continue
            i = bisect.bisect_left(bt, x["t"]) - 1
            if i < 0:
                dropped["no_prior_book"] += 1; continue
            br = b[i]
            if not br.get("bid_qty") or not br.get("ask_qty"):
                dropped["no_queue_size"] += 1; continue
            if (x["t"] - br["t"]) * 1000 > FRESH_MS:
                dropped["stale_book"] += 1; continue
            y = dict(x); y["book"] = br
            out[s].append(y)
    return out, dropped


def run(sc, slug, q, ceiling, mode):
    """mode: 'pair_or_unwind' | 'two_sided' | 'forced'"""
    end = window_end(slug)
    dead = end - DEADLINE_S
    lots = []
    cash = D(0); fees = D(0); rebs = D(0); caph = D(0)
    entry = {"buy": None, "sell": None}
    last_t = None
    gross_pair = D(0)

    for x in sc[slug]:
        t = x["t"]
        px = D(str(x["px"])); qty = D(str(x["qty"]))
        bid = D(str(x["bid_at_trade"])); ask = D(str(x["ask_at_trade"]))
        br = x["book"]
        bq = D(str(br["bid_qty"])); aq = D(str(br["ask_qty"]))
        consumed = "sell" if x["dir"] == "buy" else "buy"   # buy taker eats the ASK
        past = t >= dead
        if last_t is not None:
            caph += D(str(t - last_t)) / D(3600) * sum(l["px"] * l["open"] for l in lots)
        last_t = t
        avail = qty

        # ---- EXIT legs resolve before new entries
        for lot in lots:
            if lot["open"] <= 0 or avail <= 0 or lot["rest"][0] is None:
                continue
            if lot["exit_side"] != consumed:
                continue
            epx, ahead, _ = lot["rest"]
            if consumed == "buy" and px > epx:
                continue
            if consumed == "sell" and px < epx:
                continue
            if ahead > 0:
                eat = min(ahead, avail); ahead -= eat; avail -= eat
            if avail > 0:
                take = min(avail, lot["open"]); avail -= take
                cash += (epx * take) if lot["exit_side"] == "sell" else (-epx * take)
                rebs += reb(epx, take)
                lot["open"] -= take
                lot["exit_px"] = epx; lot["exit_t"] = t
                lot["exit_kind"] = lot["rest_kind"]
                if lot["rest_kind"].startswith("PAIR"):
                    gross_pair += (D(1) - lot["pair_cost_at_rest"]) * take
            lot["rest"] = (epx, ahead, D(0))

        # ---- FIRST-LEG entry (maker at the touch), only before the deadline
        if not past and avail > 0:
            st = entry.get(consumed)
            if st:
                mypx, ahead, got = st
                ok = (px <= mypx) if consumed == "buy" else (px >= mypx)
                if ok:
                    if ahead > 0:
                        eat = min(ahead, avail); ahead -= eat; avail -= eat
                    if avail > 0 and got < q:
                        take = min(avail, D(q) - got); got += take
                        cash += (-mypx * take) if consumed == "buy" else (mypx * take)
                        rebs += reb(mypx, take)
                        lots.append({"px": mypx, "qty": take, "open": take,
                                     "side": consumed, "t": t,
                                     "exit_side": "sell" if consumed == "buy" else "buy",
                                     "rest": (None, D(0), D(0)), "rest_kind": "PAIR_MAKER",
                                     "exit_px": None, "exit_t": None, "exit_kind": None,
                                     "qualifying_seen": False,
                                     "pair_cost_at_rest": D(1), "best_pair": None})
                    entry[consumed] = (mypx, ahead, got) if got < q else None

        # ---- (re)place the FIRST-LEG entry quotes at the touch.
        # Missing in the first revision, which is why it reported zero first
        # legs: the entry dict was only ever written after a fill, so no quote
        # was ever resting to be filled. Queue-ahead is the displayed touch
        # size from the preceding book row -- never assumed, never beyond-touch.
        if not past:
            for side, qp, qsz in (("buy", bid, bq), ("sell", ask, aq)):
                cur = entry.get(side)
                if cur is None or cur[0] != qp:
                    entry[side] = (qp, qsz, D(0))

        # ---- decide each open lot's standing order
        for lot in lots:
            if lot["open"] <= 0:
                continue
            long_ = lot["side"] == "buy"
            # executable exit price = the opposite TOUCH
            ex_px = bid if long_ else ask
            ex_depth = bq if long_ else aq
            pair_cost = (lot["px"] + (D(1) - ex_px)) if long_ else ((D(1) - lot["px"]) + ex_px)
            if lot["best_pair"] is None or pair_cost < lot["best_pair"]:
                lot["best_pair"] = pair_cost
            gross = (D(1) - pair_cost)
            f = fee(ex_px, min(lot["open"], ex_depth))
            net_now = gross * min(lot["open"], ex_depth) - f
            if pair_cost <= ceiling:
                lot["qualifying_seen"] = True

            if mode == "forced" or past:
                take = min(lot["open"], ex_depth)
                if take > 0:
                    cash += (ex_px * take) if long_ else (-ex_px * take)
                    fees += fee(ex_px, take)
                    lot["open"] -= take
                    lot["exit_px"] = ex_px; lot["exit_t"] = t
                    lot["exit_kind"] = "FORCED"
                continue
            if mode == "two_sided":
                lot["rest"] = ((ask if long_ else bid), (aq if long_ else bq), D(0))
                lot["rest_kind"] = "UNWIND_MAKER"
                continue
            # PAIR_OR_UNWIND
            # 1. immediately executable complement acquisition, if displayed
            #    depth already gives profitable economics after fees
            if pair_cost <= ceiling and net_now > 0 and ex_depth > 0:
                take = min(lot["open"], ex_depth)
                cash += (ex_px * take) if long_ else (-ex_px * take)
                fees += fee(ex_px, take)
                gross_pair += gross * take
                lot["open"] -= take
                lot["exit_px"] = ex_px; lot["exit_t"] = t
                lot["exit_kind"] = "PAIR_TAKER"
                continue
            # 2. otherwise rest passively at the ceiling-qualifying price
            need = (D(1) - ceiling + lot["px"]) if long_ else (lot["px"] - (D(1) - ceiling))
            rest_px = max(need, ask) if long_ else min(need, bid)
            lot["rest"] = (rest_px, (aq if long_ else bq), D(0))
            lot["rest_kind"] = "PAIR_MAKER"
            lot["pair_cost_at_rest"] = (lot["px"] + (D(1) - rest_px)) if long_ \
                else ((D(1) - lot["px"]) + rest_px)

    return {"slug": slug, "q": q, "ceiling": str(ceiling), "mode": mode,
            "first_legs": len(lots),
            "unresolved": sum(1 for l in lots if l["open"] > 0),
            "qualifying_seen": sum(1 for l in lots if l["qualifying_seen"]),
            "PAIR_MAKER": sum(1 for l in lots if l["exit_kind"] == "PAIR_MAKER"),
            "PAIR_TAKER": sum(1 for l in lots if l["exit_kind"] == "PAIR_TAKER"),
            "UNWIND_MAKER": sum(1 for l in lots if l["exit_kind"] == "UNWIND_MAKER"),
            "FORCED": sum(1 for l in lots if l["exit_kind"] == "FORCED"),
            "matched_qty": str(sum(l["qty"] - l["open"] for l in lots)),
            "gross_pair": str(gross_pair),
            "maker_rebate": str(rebs), "taker_fees": str(fees),
            "net_before_comp": str(cash - fees),
            "net_after_comp": str(cash - fees + rebs),
            "capital_hours": str(caph),
            "time_to_exit_s": [l["exit_t"] - l["t"] for l in lots if l["exit_t"]],
            "worst_lot": str(min([ (l["exit_px"] - l["px"]) * l["qty"] if l["exit_px"] and l["side"]=="buy"
                                  else ((l["px"] - l["exit_px"]) * l["qty"] if l["exit_px"] else D(0))
                                  for l in lots] or [D(0)])),
            "best_pair_costs": [str(l["best_pair"]) for l in lots if l["best_pair"] is not None]}


def main():
    tr, bk = load()
    sc, dropped = scorable(tr, bk)
    n_sc = sum(len(v) for v in sc.values())
    print(f"scorable trades {n_sc}   dropped {dict(dropped)}")
    print(f"windows {len(sc)}")
    rows = []
    for slug in sorted(sc):
        for q in QS:
            for c in CEILINGS:
                rows.append(run(sc, slug, q, c, "pair_or_unwind"))
            rows.append(run(sc, slug, q, D("0.99"), "two_sided"))
            rows.append(run(sc, slug, q, D("0.99"), "forced"))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"tape": TAPE, "scorable_trades": n_sc, "dropped": dict(dropped),
               "deadline_s": DEADLINE_S, "fresh_ms": FRESH_MS,
               "ceilings": [str(c) for c in CEILINGS], "rows": rows},
              open(OUT, "w"), indent=2, default=str)
    print(f"-> {OUT}  configs {len(rows)}")
    return rows


if __name__ == "__main__":
    main()
