"""Step 4: filter replay of the edge gate on our own historical PMUS fills.

THIS IS A FILTER OF HISTORICAL FILLS. It is not a simulation of what the new
rule would have been filled on, and none of it is live or realized profit of
the new rule. Inputs: the bot's ORDER/FILL log, the per-market settled result,
rewards paid, plus market_map.json and price_history.json built by
resolve_intl.py / fetch_history.py (public Polymarket-international data).
Outputs: replay_fills.csv, replay_summary.json."""
import bisect, csv, gzip, json, math, os, random, collections
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
R = "/home/user/kalshi-arb-bot/analysis/l5/rewards/"
TICK = 0.01
ADVERSE_TICKS = 1
MAX_AGE_S = 300
PAIR_CAP = 0.97
ALLOW = {"cfb", "cs2", "nba", "nfl", "wta", "sea"}
FEES_RECONCILED = 4.36
EPS = 1e-9
SOCCER = {"unl", "wsl", "lal2", "ligaf", "lexp", "engnl", "sea"}


def league(slug):
    return slug.split("-")[1]


def jump_ticks(slug):
    lg = league(slug)
    if slug.startswith("atc-") or lg in SOCCER:
        return 3
    if lg == "nhl":
        return 2
    if lg in ("cfb", "nfl"):
        return 1
    if lg in ("atp", "wta") or lg.startswith("itf"):
        return 1
    if lg.startswith("setka"):
        return 1
    return 0


def ts(s):
    return datetime.fromisoformat(s).timestamp()


def load():
    rows = json.load(gzip.open(R + "sports_orders_fills_20261114_20261118.json.gz"))
    pnl = json.load(open(R + "sports_market_pnl_20261114_20261118.json"))
    rew = collections.Counter()
    for x in json.load(open(R + "sports_reward_rows_paid_20261114_20261118.json")):
        rew[x["marketSlug"]] += float(x["reward"])
    mm = json.load(open(os.path.join(HERE, "market_map.json")))
    ph = json.load(open(os.path.join(HERE, "price_history.json")))
    orders = {r["order_id"]: r for r in rows if r["kind"] == "ORDER"}
    fills = [r for r in rows if r["kind"] == "FILL"]
    for i, f in enumerate(fills):
        f["i"] = i
        f["ts"] = ts(f["t"])
        f["pxf"], f["qf"] = float(f["px"]), float(f["qty"])
        o = orders.get(f["order_id"]) or {}
        try:
            f["clip"] = float(o.get("qty"))
        except (TypeError, ValueError):
            f["clip"] = None
        if not f["clip"] or f["clip"] <= 0:
            try:
                f["clip"] = float(pnl[f["slug"]].get("qty_mode"))
            except (TypeError, ValueError):
                f["clip"] = f["qf"]
        f["stage"] = o.get("stage")
    fills.sort(key=lambda f: (f["ts"], f["i"]))
    return fills, pnl, rew, mm, ph


def fair_for(f, mm, ph):
    """(fair value of the fill's side, age seconds, reason-if-none)."""
    r = mm.get(f["slug"]) or {}
    if not r.get("match"):
        return None, None, "no_intl_match"
    h = ph.get(f["slug"]) or []
    if not h:
        return None, None, "no_history"
    i = bisect.bisect_right([x[0] for x in h], f["ts"]) - 1
    if i < 0:
        return None, None, "no_point_before_fill"
    age = f["ts"] - h[i][0]
    if age > MAX_AGE_S:
        return None, age, "stale_gt_5min"
    p = h[i][1]
    return (p if f["side"] == "YES" else 1.0 - p), age, ""


def fair_at(f, ph, dt):
    """Side fair value from the most recent history point at or before fill+dt
    (must be newer than the fill-time point window: age <= 5 min at that time)."""
    h = ph.get(f["slug"]) or []
    t = f["ts"] + dt
    i = bisect.bisect_right([x[0] for x in h], t) - 1
    if i < 0 or t - h[i][0] > MAX_AGE_S:
        return None
    return h[i][1] if f["side"] == "YES" else 1.0 - h[i][1]


def gate_market(mfills, mm, ph):
    """Sequential edge gate on one market. Simulated position is built only
    from accepted fills. Returns per-fill decision dicts."""
    q = {"YES": 0.0, "NO": 0.0}
    c = {"YES": 0.0, "NO": 0.0}
    out = []
    for f in mfills:
        s, o = f["side"], ("NO" if f["side"] == "YES" else "YES")
        fair, age, why = fair_for(f, mm, ph)
        same, other = q[s], q[o]
        if same < other - EPS:
            role, clips, adv = "LIGHTER", 0, ADVERSE_TICKS
        elif same > other + EPS:
            role, adv = "ADD", ADVERSE_TICKS + jump_ticks(f["slug"])
            clips = max(1, math.ceil((same - other) / f["clip"] - EPS))
        else:
            role, clips, adv = "OPEN", 0, ADVERSE_TICKS
        d = {"fair": fair, "age": age, "role": role, "clips": clips, "adv_ticks": adv,
             "edge": None, "risk": clips * TICK, "pair_sum": None, "accept": False, "reason": why}
        if fair is None:
            d["reason"] = "reject:" + why
        else:
            edge = fair - f["pxf"] - adv * TICK
            d["edge"] = edge
            ok = edge >= -EPS and edge >= clips * TICK - EPS
            reason = "" if ok else ("reject:edge<0" if edge < -EPS else "reject:edge<risk")
            if role == "LIGHTER":
                ps = f["pxf"] + c[o] / q[o]
                d["pair_sum"] = ps
                if ps > PAIR_CAP + EPS:
                    ok = False
                    reason = (reason + "+" if reason else "reject:") + "pair>0.97"
            d["accept"], d["reason"] = ok, (reason or "accept")
        if d["accept"]:
            q[s] += f["qf"]
            c[s] += f["pxf"] * f["qf"]
        out.append(d)
    return out


def settle_side(slug, pnl, mm):
    """Winning side: PMUS public settlement where the bot's reconciliation
    recorded it (markets left unpaired), else the resolved international market."""
    v = pnl[slug]
    if v.get("settle_px") is not None:
        return ("YES" if float(v["settle_px"]) >= 0.5 else "NO"), "pmus"
    m = (mm.get(slug) or {}).get("match")
    if m and m.get("intl_winner"):
        return m["intl_winner"], "intl"
    return None, None


def market_result(kept, winner, close_ts):
    """Settled trading result of a set of accepted fills in one market.
    Pair/residual split uses average cost per side (as the reconciliation did)."""
    q = {"YES": 0.0, "NO": 0.0}
    c = {"YES": 0.0, "NO": 0.0}
    caph = 0.0
    for f in kept:
        q[f["side"]] += f["qf"]
        c[f["side"]] += f["pxf"] * f["qf"]
        if close_ts:
            caph += f["pxf"] * f["qf"] * max(0.0, close_ts - f["ts"]) / 3600.0
    pairs = min(q["YES"], q["NO"])
    avg = {k: (c[k] / q[k] if q[k] else 0.0) for k in q}
    pair_pnl = pairs * (1.0 - avg["YES"] - avg["NO"])
    rs = "YES" if q["YES"] > q["NO"] + EPS else ("NO" if q["NO"] > q["YES"] + EPS else None)
    res_qty = abs(q["YES"] - q["NO"]) if rs else 0.0
    res_cost = res_qty * avg[rs] if rs else 0.0
    unknown = bool(rs and winner is None)
    res_pnl = 0.0 if not rs else ((res_qty if winner == rs else 0.0) - res_cost if winner else 0.0)
    return {"fills": len(kept), "contracts": q["YES"] + q["NO"], "cost": c["YES"] + c["NO"],
            "pairs": pairs, "pair_pnl": pair_pnl, "res_side": rs, "res_qty": res_qty,
            "res_cost": res_cost, "res_pnl": res_pnl, "pnl": pair_pnl + res_pnl,
            "res_unknown": unknown, "cap_hours": caph if close_ts else None}


def boot_ratio(per, n=10000, seed=7):
    """Bootstrap over markets of sum(pnl)/sum(cost). per: list of (pnl, cost)."""
    rnd = random.Random(seed)
    k = len(per)
    if k < 2:
        return None
    xs = []
    for _ in range(n):
        sp = sc = 0.0
        for _ in range(k):
            a, b = per[rnd.randrange(k)]
            sp += a; sc += b
        if sc > 0:
            xs.append(sp / sc)
    xs.sort()
    return [xs[int(0.025 * len(xs))], xs[int(0.5 * len(xs))], xs[int(0.975 * len(xs)) - 1]]


def boot_diff(slugs, A, B, n=10000, seed=11):
    """Paired bootstrap over the common market universe: (ratio_B - ratio_A)
    and (pnl_B - pnl_A). A/B: slug -> (pnl, cost) (missing = (0, 0))."""
    rnd = random.Random(seed)
    k = len(slugs)
    dr, dp = [], []
    for _ in range(n):
        pa = ca = pb = cb = 0.0
        for _ in range(k):
            s = slugs[rnd.randrange(k)]
            x, y = A.get(s, (0.0, 0.0)), B.get(s, (0.0, 0.0))
            pa += x[0]; ca += x[1]; pb += y[0]; cb += y[1]
        dp.append(pb - pa)
        if ca > 0 and cb > 0:
            dr.append(pb / cb - pa / ca)
    dr.sort(); dp.sort()
    q = lambda v: [v[int(0.025 * len(v))], v[int(0.5 * len(v))], v[int(0.975 * len(v)) - 1]] if v else None
    return {"ratio_diff_ci95": q(dr), "pnl_diff_ci95": q(dp),
            "share_boot_B_better_ratio": (sum(1 for x in dr if x > 0) / len(dr)) if dr else None,
            "n_valid": len(dr)}


def main():
    fills, pnl, rew, mm, ph = load()
    bym = collections.defaultdict(list)
    for f in fills:
        bym[f["slug"]].append(f)
    # gate decisions (identical for V2 and V3: per-market sequential state)
    for slug, mf in bym.items():
        for f, d in zip(mf, gate_market(mf, mm, ph)):
            f["g"] = d
    # fee allocation: per-market taker-fee estimate from the reconciliation file,
    # scaled to the reconciled total, then pro-rata by contracts kept in that market.
    est_tot = sum(float(v.get("taker_fee_est") or 0) for v in pnl.values())
    fee_mkt = {s: float(v.get("taker_fee_est") or 0) * FEES_RECONCILED / est_tot for s, v in pnl.items()}
    tot_contracts = sum(f["qf"] for f in fills)

    def close_ts(slug):
        m = (mm.get(slug) or {}).get("match")
        ct = m and m.get("closedTime")
        if not ct:
            return None
        try:
            return datetime.fromisoformat(ct.replace(" ", "T").replace("+00", "+00:00")).timestamp()
        except ValueError:
            return None

    liquid = {s for s, r in mm.items() if r.get("match") and (r["match"].get("intl_volume") or 0) >= 1000}
    variants = {
        "V0": lambda f: True,
        "V1": lambda f: league(f["slug"]) in ALLOW,
        "V2": lambda f: league(f["slug"]) in ALLOW and f["g"]["accept"],
        "V3": lambda f: f["g"]["accept"],
        "V3_liq": lambda f: f["g"]["accept"] and f["slug"] in liquid,
    }
    elig = {  # markets on which the variant would still quote (for the reward upper bound)
        "V0": lambda s: True,
        "V1": lambda s: league(s) in ALLOW,
        "V2": lambda s: league(s) in ALLOW and bool((mm.get(s) or {}).get("match")),
        "V3": lambda s: bool((mm.get(s) or {}).get("match")),
        "V3_liq": lambda s: s in liquid,
    }
    summary, per_var = {}, {}
    for name, keep in variants.items():
        per, tot = {}, collections.Counter()
        worst = None
        unknown = 0
        cap_cost = 0.0
        fee_mkt_scaled = fee_prorata = 0.0
        for slug, mf in bym.items():
            kept = [f for f in mf if keep(f)]
            if not kept:
                continue
            w, src = settle_side(slug, pnl, mm)
            r = market_result(kept, w, close_ts(slug))
            per[slug] = r
            unknown += r["res_unknown"]
            for k in ("fills", "contracts", "cost", "pairs", "pair_pnl", "res_qty", "res_cost", "res_pnl", "pnl"):
                tot[k] += r[k]
            if r["cap_hours"] is not None:
                tot["cap_hours"] += r["cap_hours"]; cap_cost += r["cost"]
            allq = sum(f["qf"] for f in mf)
            fee_mkt_scaled += fee_mkt.get(slug, 0.0) * r["contracts"] / allq
            fee_prorata += FEES_RECONCILED * r["contracts"] / tot_contracts
            if worst is None or r["pnl"] < worst[1]:
                worst = (slug, r["pnl"])
        ci = boot_ratio([(r["pnl"], r["cost"]) for r in per.values()])
        kept_rew = sum(rew.get(s, 0.0) for s in per)
        elig_m = [s for s in pnl if elig[name](s)]
        summary[name] = {
            "markets": len(per), "fills": int(tot["fills"]), "contracts": round(tot["contracts"], 2),
            "dollars_spent": round(tot["cost"], 2),
            "pairs": round(tot["pairs"], 2), "pair_pnl": round(tot["pair_pnl"], 2),
            "residual_markets": sum(1 for r in per.values() if r["res_side"]),
            "residual_qty": round(tot["res_qty"], 2), "residual_cost": round(tot["res_cost"], 2),
            "residual_pnl": round(tot["res_pnl"], 2),
            "residual_wins": sum(1 for r in per.values() if r["res_side"] and r["res_pnl"] > 0),
            "trading_pnl_before_fees": round(tot["pnl"], 2),
            "pnl_per_dollar": round(tot["pnl"] / tot["cost"], 4) if tot["cost"] else None,
            "pnl_per_dollar_boot_ci95_[lo,med,hi]": [round(x, 4) for x in ci] if ci else None,
            "fees_est_market_taker_scaled": round(-fee_mkt_scaled, 2),
            "fees_est_prorata_contracts": round(-fee_prorata, 2),
            "trading_pnl_after_fees_est": round(tot["pnl"] - fee_mkt_scaled, 2),
            "worst_market": [worst[0], round(worst[1], 2)] if worst else None,
            "capital_hours_usd_h": round(tot["cap_hours"], 1),
            "capital_hours_cost_covered": round(cap_cost, 2),
            "residual_markets_unknown_settlement": unknown,
            "rewards_paid_on_markets_with_kept_fills_UPPER_BOUND": round(kept_rew, 2),
            "rewards_paid_on_eligible_markets_UPPER_BOUND": round(sum(rew.get(s, 0.0) for s in elig_m), 2),
            "eligible_markets": len(elig_m),
        }
        per_var[name] = {s: (r["pnl"], r["cost"]) for s, r in per.items()}
    uni = sorted(bym)
    diffs = {}
    for a, b in (("V0", "V1"), ("V1", "V2"), ("V0", "V3"), ("V0", "V2"), ("V2", "V3")):
        diffs[f"{b}_minus_{a}"] = boot_diff(uni, per_var[a], per_var[b])
    for v in diffs.values():
        for k in ("ratio_diff_ci95", "pnl_diff_ci95"):
            if v[k]:
                v[k] = [round(x, 4) for x in v[k]]

    # coverage
    slugs = list(pnl)
    filled = set(bym)
    tot_cost = sum(f["pxf"] * f["qf"] for f in fills)
    has_match = {s for s in slugs if (mm.get(s) or {}).get("match")}
    has_hist = {s for s in has_match if ph.get(s)}
    fill_fair = [f for f in fills if f["g"]["fair"] is not None]
    chk = [(s, mm[s]["match"]["intl_winner"], mm[s].get("pmus_winner")) for s in has_match if mm[s].get("pmus_winner")]
    lg_all = collections.Counter(league(s) for s in slugs)
    lg_ok = collections.Counter(league(s) for s in has_match)
    why_nofair = collections.Counter(f["g"]["reason"] for f in fills if f["g"]["fair"] is None)
    cov = {
        "markets_total": len(slugs), "markets_with_fills": len(filled),
        "markets_intl_match": len(has_match), "markets_intl_match_with_fills": len(has_match & filled),
        "markets_with_price_history_points": len(has_hist),
        "markets_with_fills_and_>=1_usable_fair": len({f["slug"] for f in fill_fair}),
        "markets_with_fills_all_fills_usable_fair": sum(1 for s, mf in bym.items() if all(f["g"]["fair"] is not None for f in mf)),
        "orientation_established_markets": len(has_match),
        "orientation_check_vs_pmus_settlement": {"checked": len(chk), "agree": sum(1 for _, a, b in chk if a == b),
                                                  "disagree": sum(1 for _, a, b in chk if a and a != b)},
        "fills_total": len(fills), "fills_intl_match": sum(1 for f in fills if f["slug"] in has_match),
        "fills_usable_fair": len(fill_fair),
        "dollars_total": round(tot_cost, 2),
        "dollars_intl_match": round(sum(f["pxf"] * f["qf"] for f in fills if f["slug"] in has_match), 2),
        "dollars_usable_fair": round(sum(f["pxf"] * f["qf"] for f in fill_fair), 2),
        "no_fair_reasons_fills": dict(why_nofair),
        "match_method_counts": dict(collections.Counter(mm[s]["match"]["method"] for s in has_match)),
        "reversed_order_matches": sum(1 for s in has_match if mm[s]["match"]["yes_idx"] == 1),
        "intl_zero_or_unknown_volume_matched_markets": sum(1 for s in has_match if not (mm[s]["match"].get("intl_volume") or 0)),
        "by_league_matched/total": {k: f"{lg_ok[k]}/{lg_all[k]}" for k in sorted(lg_all, key=lambda x: -lg_all[x])},
        "leagues_no_intl_counterpart": sorted(k for k in lg_all if lg_ok[k] == 0),
        "unmatched_markets": {s: mm[s]["why"] for s in slugs if s not in has_match},
        "fair_age_seconds_median": sorted(f["g"]["age"] for f in fill_fair)[len(fill_fair) // 2],
    }
    gate = {
        "decisions_all_fills": dict(collections.Counter(f["g"]["reason"] for f in fills)),
        "accepted_by_role": dict(collections.Counter(f["g"]["role"] for f in fills if f["g"]["accept"])),
        "evaluated_by_role": dict(collections.Counter(f["g"]["role"] for f in fills if f["g"]["fair"] is not None)),
        "by_league_accepted/with_fair/fills": {
            lg: "%d/%d/%d" % (sum(1 for f in fills if league(f["slug"]) == lg and f["g"]["accept"]),
                              sum(1 for f in fills if league(f["slug"]) == lg and f["g"]["fair"] is not None),
                              sum(1 for f in fills if league(f["slug"]) == lg))
            for lg in sorted({league(f["slug"]) for f in fills})},
    }
    # diagnostics on gate-accepted fills: value at the fill-time fair, and later fair (markout)
    def diag(sel):
        ff = [f for f in fills if sel(f) and f["g"]["fair"] is not None]
        o = {"fills": len(ff), "usd_edge_at_fill_fair": round(sum(f["qf"] * (f["g"]["fair"] - f["pxf"]) for f in ff), 2)}
        for dt, nm in ((60, "1m"), (300, "5m")):
            xs = [(f["qf"], fair_at(f, ph, dt) - f["pxf"]) for f in ff if fair_at(f, ph, dt) is not None]
            o["usd_markout_" + nm] = round(sum(q * e for q, e in xs), 2)
            o["n_markout_" + nm] = len(xs)
            o["mean_ticks_markout_" + nm] = round(sum(e for _, e in xs) / len(xs) / TICK, 2) if xs else None
        return o
    markout = {"V3_accepted": diag(lambda f: f["g"]["accept"]),
               "V2_accepted": diag(lambda f: f["g"]["accept"] and league(f["slug"]) in ALLOW),
               "gate_rejected_with_fair": diag(lambda f: not f["g"]["accept"]),
               "all_fills_with_fair": diag(lambda f: True)}
    v0 = summary["V0"]
    recon = {"reconciliation_trading_before_fees": -40.71, "replay_V0_before_fees": v0["trading_pnl_before_fees"],
             "reconciliation_after_fees": -45.07, "replay_V0_after_reconciled_fees": round(v0["trading_pnl_before_fees"] - FEES_RECONCILED, 2),
             "reconciliation_contracts": 1900.97, "reconciliation_cost": 953.91,
             "file_taker_fee_est_sum": round(est_tot, 2), "reconciled_fees": -FEES_RECONCILED,
             "rewards_paid_all_rows": round(sum(rew.values()), 2),
             "rewards_on_the_241_markets": round(sum(v for s, v in rew.items() if s in pnl), 2)}
    json.dump({"label": "FILTER OF HISTORICAL FILLS - not a simulation of fills under the new rule; not live or realized profit of the new rule",
               "params": {"tick": TICK, "adverse_ticks": ADVERSE_TICKS, "max_fair_age_s": MAX_AGE_S, "pair_cap": PAIR_CAP,
                          "allowlist": sorted(ALLOW), "jump_ticks": "soccer 3, nhl 2, cfb/nfl 1, atp/wta/itf* 1, setka* 1, else 0",
                          "bootstrap": "10,000 resamples of markets, percentile 95%"},
               "v0_vs_reconciliation": recon, "variants": summary, "paired_bootstrap_differences": diffs,
               "coverage": cov, "gate": gate, "markout_ESTIMATED": markout},
              open(os.path.join(HERE, "replay_summary.json"), "w"), indent=1)
    with open(os.path.join(HERE, "replay_fills.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "slug", "league", "side", "px", "qty", "order_id", "order_clip_qty", "stage",
                    "hist_pos_yes_after", "hist_pos_no_after", "intl_slug", "match_method",
                    "fair_side_ESTIMATED", "fair_age_s", "sim_role", "sim_unpaired_clips", "adverse_ticks_used",
                    "edge", "risk_allowance", "pair_sum_if_lighter", "gate_decision",
                    "V0", "V1", "V2", "V3", "V3_liq"])
        r4 = lambda x: "" if x is None else round(x, 4)
        for f in fills:
            g = f["g"]
            m = (mm.get(f["slug"]) or {}).get("match") or {}
            w.writerow([f["t"], f["slug"], league(f["slug"]), f["side"], f["px"], f["qty"], f["order_id"], f["clip"],
                        f["stage"], f["pos"].get("YES"), f["pos"].get("NO"), m.get("intl_slug", ""), m.get("method", ""),
                        r4(g["fair"]), "" if g["age"] is None else round(g["age"], 1), g["role"], g["clips"], g["adv_ticks"],
                        r4(g["edge"]), round(g["risk"], 2), r4(g["pair_sum"]), g["reason"]]
                       + [int(bool(variants[v](f))) for v in ("V0", "V1", "V2", "V3", "V3_liq")])
    print(json.dumps({"markout": markout, "unmatched": cov["unmatched_markets"]}, indent=1))


if __name__ == "__main__":
    main()
