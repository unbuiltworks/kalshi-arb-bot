"""The integrated portfolio controller against the deployed pair.

OFFLINE. Drives the PRODUCTION decision function
(`src.l5_portfolio_controller.decide`) over L5's Polymarket BTC15m paths
with the same ruler as `edge_comparison`: one-contract post-only maker buys
that fill only when a later print trades a full tick through them, zero
commission, no rebate, pairs redeem $1, unmatched contracts settle on the
venue outcome. Places nothing and changes no configuration.

THE QUESTION (owner, 2026-11-10). Does one controller that combines pair
accumulation, favored-side accumulation, inventory balancing and adaptive
repricing improve conservative expected settled P&L over the deployed pair?

WHAT IS COMPARED, on identical windows, chronological 60/40 split:

  PAIR        the deployed engine (`edge_comparison.make_pair(2)`).
  PRESETS     the controller at fixed settings from cautious to aggressive,
              chosen BEFORE looking at any result.
  TUNED       the controller at the setting that did best on the first part
              only, among settings that actually trade.

Two panels, as in the edge comparison: the 312 windows with a spot-and-
strike probability (the controller has a MODEL belief and can favor a side)
and all 526 (no model: the belief is the BOOK, so it can only pair and
hedge).

The same caveats apply. It is Polymarket's path sampled where L5 traded;
the model sees a one-minute tape; queue position is the trade-through rule.
"""
from __future__ import annotations

import itertools
from typing import Any, Callable, Dict, List, Optional, Sequence

from analysis.l5 import edge_comparison as EC
from analysis.l5 import favorite_accumulation as FA
from analysis.l5 import pair_completion_replay as PR
from src import l5_portfolio_controller as PC

UP, DOWN = PC.UP, PC.DOWN
BIG = PC.Limits(cash_usd=1e9, exposure_usd=1e9, max_orders=2)

#: fixed before any result was read. `haircut` is margin + adverse: the two
#: enter the valuation only as their sum.
PRESETS: Dict[str, Dict[str, Any]] = {
    "CAUTIOUS (haircut 6c, hedge first)":
        dict(margin=0.03, adverse=0.03, risk=0.15, max_unmatched=1,
             max_qty=1, pair_ceiling=0.99),
    "BALANCED (haircut 4c)":
        dict(margin=0.02, adverse=0.02, risk=0.10, max_unmatched=3,
             max_qty=2),
    "FAVORED (haircut 4c, size on edge)":
        dict(margin=0.02, adverse=0.02, risk=0.05, max_unmatched=6,
             max_qty=3, size_per_cent=0.25),
    "AGGRESSIVE (haircut 0, touch, size on edge)":
        dict(margin=0.0, adverse=0.0, risk=0.0, max_unmatched=6, max_qty=3,
             size_per_cent=0.25),
    "AGGRESSIVE + hedge skew":
        dict(margin=0.0, adverse=0.0, risk=0.15, max_unmatched=6, max_qty=3,
             size_per_cent=0.25),
}

GRID = [dict(margin=h / 2.0, adverse=h / 2.0, risk=r, max_unmatched=mu,
             max_qty=min(3, mu), size_per_cent=sz, pair_ceiling=pc)
        for h, r, mu, sz, pc in itertools.product(
            (0.0, 0.02, 0.04, 0.06, 0.10), (0.0, 0.05, 0.15, 0.30),
            (1, 3, 6), (0.0, 0.25), (None, 0.99))]

#: fills per window on the first part; the top band is the owner's target.
FRONTIER_BANDS = ((0.2, 1.0), (1.0, 3.0), (3.0, 10.0), (10.0, 20.0),
                  (20.0, 1e9))
FRONTIER_KEYS = ("fills_per_window", "net_per_window", "net_per_window_ci95",
                 "net_per_contract", "net_per_contract_ci95", "worst_window")

#: a setting must average at least this many fills a window on the first
#: part to be eligible: "quote nothing" is not a trading strategy.
MIN_FILLS_PER_WINDOW = 1.0


def book_for(m: float) -> Dict[str, float]:
    """The one-tick-wide book the ruler puts around a print."""
    bid = EC._down(m - EC.HALF)
    ask = round(1.0 - EC._down(1.0 - m - EC.HALF), 10)
    return {"bid": bid, "ask": ask}


def make_controller(params: PC.Params, limits: PC.Limits = BIG,
                    use_model: bool = True) -> EC.Strategy:
    """Adapt the production decision function to the replay's order book."""
    def strat(book: EC.Book, t: float, m: float, p: Optional[float],
              s: Dict[str, Any]) -> None:
        seen = s.setdefault("seen", 0)
        cost = s.setdefault("cost", {UP: 0.0, DOWN: 0.0})
        qty = s.setdefault("qty", {UP: 0.0, DOWN: 0.0})
        for f in book.log[seen:]:
            cost[f["side"]] += f["px"]
            qty[f["side"]] += 1.0
        s["seen"] = len(book.log)
        resting = [{"id": oid, "side": o["side"], "price": o["px"],
                    "qty": 1.0} for oid, o in book.resting()]
        port = PC.portfolio(up_qty=qty[UP], up_cost=cost[UP],
                            dn_qty=qty[DOWN], dn_cost=cost[DOWN],
                            orders=resting)
        bk = book_for(m)
        dec = PC.decide(port, bid=bk["bid"], ask=bk["ask"],
                        p_up=(p if use_model else None), t_off=t,
                        params=params, limits=limits)
        want = {q["side"]: q for q in dec["quotes"]}
        for side in (UP, DOWN):
            tgt = want.get(side)
            mine = [(oid, o) for oid, o in book.resting(side)]
            keep = 0
            for oid, o in mine:
                if tgt is not None and keep < int(round(tgt["qty"])) and \
                        abs(o["px"] - tgt["price"]) < 1e-9:
                    keep += 1
                else:
                    book.cancel(oid)
            if tgt is None:
                continue
            for _ in range(int(round(tgt["qty"])) - keep):
                book.place(side, tgt["price"], t, m,
                           "keep-pc-%s" % tgt["role"])
    return strat


def split_roles(results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    out = {"ADD": 0, "HEDGE": 0}
    for r in results:
        for f in r["log"]:
            out["HEDGE" if f["tag"].endswith("HEDGE") else "ADD"] += 1
    return out


def run_panel(ps: Dict[str, Any], slugs: Sequence[str],
              models: Optional[Dict[str, Any]], strat_for: Callable[[], Any],
              through: int = 1) -> List[Dict[str, Any]]:
    out = []
    for s in slugs:
        out.append(EC.run_window(
            ps[s]["path"], ps[s]["winner"], strat_for(),
            model=(models[s] if models is not None else None),
            through=through))
    return out


def run() -> Dict[str, Any]:
    trades = FA.load_trades(FA.load_outcomes())
    ps = PR.paths(trades)
    model_for = EC.load_model()
    models = {}
    for s, w in ps.items():
        fn = model_for(s, w["ws"])
        if fn is not None:
            models[s] = fn
    panels = {}
    spot = sorted(models, key=lambda s: ps[s]["ws"])
    wide = sorted(ps, key=lambda s: ps[s]["ws"])
    for name, order, mdl in (("spot", spot, models), ("wide", wide, None)):
        cut = int(len(order) * 0.6)
        panels[name] = {"IS": order[:cut], "OOS": order[cut:],
                        "models": mdl}
    res: Dict[str, Any] = {
        "evidence_class": ("L5 POLYMARKET PATHS with PMUS ticks and fees, "
                           "one-minute Coinbase reference -- not PMUS "
                           "fills, not our own"),
        "panels": {}}
    for pname, pan in panels.items():
        mdl = pan["models"]
        use_model = mdl is not None

        def score(slugs, strat_for, through=1):
            rs = run_panel(ps, slugs, mdl, strat_for, through)
            summ = EC.summarise(rs)
            summ["_nets"] = [r["net"] for r in rs]
            summ["roles"] = split_roles(rs)
            summ["fills_per_window"] = summ["fills"] / max(1, len(slugs))
            return summ

        def ctl(kw):
            return lambda: make_controller(PC.Params(**kw).checked(),
                                           use_model=use_model)

        # ---- tuned on the first part ONLY
        tried = []
        for kw in GRID:
            s_is = score(pan["IS"], ctl(kw))
            tried.append((kw, s_is))
        eligible = [(kw, s) for kw, s in tried
                    if s["fills_per_window"] >= MIN_FILLS_PER_WINDOW]
        best_kw, _best = max(eligible,
                             key=lambda x: x[1]["net_per_window"])
        best_ctr_kw, _b2 = max(
            eligible, key=lambda x: x[1]["net_per_contract"] or -9)
        cands: Dict[str, Callable[[], Any]] = {
            "PAIR (deployed)": lambda: EC.make_pair(2)}
        for name, kw in PRESETS.items():
            cands[name] = ctl(kw)
        cands["TUNED on IS, by $/window"] = ctl(best_kw)
        cands["TUNED on IS, by $/contract"] = ctl(best_ctr_kw)
        out: Dict[str, Any] = {
            "windows": {"IS": len(pan["IS"]), "OOS": len(pan["OOS"])},
            "belief": "MODEL" if use_model else "BOOK",
            "tuned": {"by_window": best_kw, "by_contract": best_ctr_kw,
                      "grid": len(GRID), "eligible": len(eligible)},
            "grid_IS_best_any": max(
                (s["net_per_window"] for _kw, s in tried)),
            "grid_IS_positive": sum(
                1 for _kw, s in tried if s["net_per_window"] > 0
                and s["fills"] > 0),
            "candidates": {}, "through0": {}, "vs_pair": {}}
        # ---- does trading MORE pay? The best first-part setting at each
        # level of activity, then scored on the second part.
        out["frontier"] = []
        for lo, hi in FRONTIER_BANDS:
            band = [(kw, s) for kw, s in tried
                    if lo <= s["fills_per_window"] < hi]
            if not band:
                continue
            kw, s_is = max(band, key=lambda x: x[1]["net_per_window"])
            s_oos = score(pan["OOS"], ctl(kw))
            out["frontier"].append({
                "band": [lo, hi], "settings_in_band": len(band),
                "params": kw,
                "IS": {k: s_is[k] for k in FRONTIER_KEYS},
                "OOS": {k: s_oos[k] for k in FRONTIER_KEYS}})
        for name, sf in cands.items():
            out["candidates"][name] = {h: score(pan[h], sf)
                                       for h in ("IS", "OOS")}
            out["through0"][name] = score(pan["OOS"], sf, through=0)
        base = out["candidates"]["PAIR (deployed)"]
        for name, by in out["candidates"].items():
            if name == "PAIR (deployed)":
                continue
            out["vs_pair"][name] = {
                h: EC.paired_difference(by[h]["_nets"], base[h]["_nets"])
                for h in ("IS", "OOS")}
        for by in out["candidates"].values():
            for s_ in by.values():
                s_.pop("_nets", None)
        for s_ in out["through0"].values():
            s_.pop("_nets", None)
        res["panels"][pname] = out
    return res


def _f(x, w=8, p=4):
    return (" " * (w - 3) + "n/a") if x is None else ("%+*.*f" % (w, p, x))


def main() -> None:
    import json
    import os
    r = run()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "portfolio_controller_replay.json"), "w") as fh:
        json.dump(r, fh, indent=1, sort_keys=True)
    print("evidence:", r["evidence_class"])
    for pname, pan in r["panels"].items():
        print("\n######## panel %s: %s, belief %s" % (
            pname, pan["windows"], pan["belief"]))
        print("tuned:", pan["tuned"])
        print("grid settings positive on IS: %d of %d; best IS $/window "
              "of any setting %+.4f" % (pan["grid_IS_positive"],
                                        pan["tuned"]["grid"],
                                        pan["grid_IS_best_any"]))
        for half in ("OOS", "IS"):
            print("== %s ==" % half)
            for name, by in pan["candidates"].items():
                s = by[half]
                print("%-44s f/w %5.1f add/hedge %5d/%5d  /ctr %s "
                      "[%+.4f, %+.4f]  /win %s [%+.3f, %+.3f] worst %s" % (
                          name, s["fills_per_window"], s["roles"]["ADD"],
                          s["roles"]["HEDGE"], _f(s["net_per_contract"]),
                          s["net_per_contract_ci95"][0] or 0,
                          s["net_per_contract_ci95"][1] or 0,
                          _f(s["net_per_window"]),
                          s["net_per_window_ci95"][0],
                          s["net_per_window_ci95"][1],
                          _f(s["worst_window"], 6, 2)))
        print("== does trading more pay? best IS setting per activity "
              "band, scored OOS ==")
        for fr in pan["frontier"]:
            print("  IS fills/win %5.1f  IS $/win %+.4f  ->  OOS fills/win "
                  "%5.1f  $/win %+.4f [%+.3f, %+.3f]  /ctr %s  worst %s" % (
                      fr["IS"]["fills_per_window"], fr["IS"]["net_per_window"],
                      fr["OOS"]["fills_per_window"],
                      fr["OOS"]["net_per_window"],
                      fr["OOS"]["net_per_window_ci95"][0],
                      fr["OOS"]["net_per_window_ci95"][1],
                      _f(fr["OOS"]["net_per_contract"]),
                      _f(fr["OOS"]["worst_window"], 6, 2)))
        print("== OOS decomposition per filled contract ==")
        for name, by in pan["candidates"].items():
            b = by["OOS"]["buckets_per_contract"]
            print("%-44s %s" % (name, " ".join(
                "%s %s" % (k[:5], _f(b[k], 7)) for k in EC.BUCKETS)))
        print("== OOS, optimistic fill rule (a touch fills) ==")
        for name, s in pan["through0"].items():
            print("%-44s /ctr %s  /win %s [%+.3f, %+.3f]" % (
                name, _f(s["net_per_contract"]), _f(s["net_per_window"]),
                s["net_per_window_ci95"][0], s["net_per_window_ci95"][1]))
        print("== versus the deployed pair, $ per window ==")
        for name, by in pan["vs_pair"].items():
            print("%-44s %s" % (name, "  ".join(
                "%s %+.4f [%+.4f, %+.4f]" % (
                    h, by[h]["mean"], by[h]["ci95"][0], by[h]["ci95"][1])
                for h in ("IS", "OOS"))))


if __name__ == "__main__":
    main()
