"""Soccer three-way replay: recorded two-venue soccer tapes (one binary
market per outcome) through src/pmus_directional_backtest.py, baseline vs
trading-first (edge 2c, hedge give 1c), touch and through fills, plus

  * the PRODUCTION three-way gate applied to the trading-first fair
    (engine rn1_soccer_prob: all three international outcome mids fresh
    and summing to 1 within the tolerance, and this outcome's own mid
    settled; otherwise NO fair for any outcome of the match). The baseline
    has no such gate in the engine either: it only has the per-market
    international guard, and is replayed that way;
  * per-market tape statistics that say WHY a policy did or did not trade
    (price band, spreads, venue gap, lead/lag, fillable moves, traded
    volume);
  * the match-level portfolio (three outcome markets = one risk).

OFFLINE apart from the public settlement lookups (gateway
/v1/market/slug/atc-<slug>; CLOSED markets only). Every P&L is THEORETICAL
and PRE-REWARD: recorded books, modelled fills, nothing realized.

usage: python3 scripts/pmus_soccer_replay.py REC.jsonl [REC2.jsonl ...]
       [--meta META.json] [--out OUT_PREFIX] [--offline]
The recorder's `slug` is the international slug; the US market is
atc-<slug>; ib/ia are the international Yes book (first outcome). Copy a
tape that is still being written before reading it (a truncated last line
is skipped)."""
import json
import sys
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import pmus_directional_backtest as B  # noqa: E402
from src import pmus_sports_soccer_fair as SF  # noqa: E402

POLICIES = [
    ("baseline", {}),
    ("trading_first_edge2c_hedge1c",
     {"require_fair": True, "edge_min": "0", "ev_fill_loss": "0.02",
      "hedge_give": "0.01"}),
]
FILLS = ("touch", "through")
TOL = "0.04"                       # PMUS_RN1_SOCCER_SUM_TOL default
PX_MIN = float(B.PRODUCTION_CFG["px_min"])
PX_MAX = float(B.PRODUCTION_CFG["px_max"])
TICK = 0.01
EPS = 1e-9


def load(paths):
    raw = B.load_cross_venue(paths)          # keys "aec-<intl slug>"
    return {"atc-" + k[4:]: v for k, v in raw.items()}


def raw_rows(paths):
    """Every recorder row per US slug, time ordered (incl. empty books)."""
    out = defaultdict(list)
    for p in paths:
        with open(p) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and "slug" in r and "games" not in r:
                    out["atc-" + r["slug"]].append(r)
    for s in out:
        out[s].sort(key=lambda r: r.get("tu") or r.get("ti") or 0)
    return dict(out)


def starts(paths):
    """{US slug: kick-off epoch} from the recorder's `games` header rows
    (international gameStartTime, "YYYY-MM-DD HH:MM:SS+00")."""
    from datetime import datetime
    out = {}
    for p in paths:
        with open(p) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                for g in (r.get("games") or []) if isinstance(r, dict) \
                        else []:
                    try:
                        st = str(g["start"]).replace(" ", "T").replace(
                            "Z", "+00:00")
                        if st.endswith("+00"):
                            st += ":00"
                        out["atc-" + g["slug"]] = datetime.fromisoformat(
                            st).timestamp()
                    except (KeyError, TypeError, ValueError):
                        continue
    return out


def _q(vals, p):
    v = sorted(vals)
    return None if not v else round(v[min(len(v) - 1, int(p * len(v)))], 4)


def tape_stats(rows, start_ts=None):
    """Why a maker bid one tick behind the touch does / does not trade on
    one outcome market. All shares are of frames with a two-sided US book.
      band_yes/no      bid-1tick (YES) / 1-ask-1tick (NO) inside the
                       production price band
      guard_yes/no     in band AND at or under international mid - 1 tick
                       (the production guard; needs an international mid)
      edge2c_yes/no    in band AND international mid - px >= 0.02
      fillable_*       frames whose ask is at or under (touch) / strictly
                       under (through) the bid-1tick quoted two frames
                       earlier (decision + latency): the only way a resting
                       bid one tick behind the touch fills
      lead             >= 2c mid moves seen first on the international
                       book / first on the US book / in the same frame
    """
    n = len(rows)
    two = [r for r in rows if r.get("ub") is not None and
           r.get("ua") is not None and r["ua"] > r["ub"]]
    both = [r for r in two if r.get("ib") is not None and
            r.get("ia") is not None and r["ia"] > r["ib"]]
    ts = [r["tu"] if r.get("tu") is not None else r.get("ti") for r in rows]
    ts = [t for t in ts if t is not None]
    st = {"frames": n, "two_sided_us": len(two), "with_intl": len(both)}
    if ts:
        st["span_min"] = round((ts[-1] - ts[0]) / 60, 1)
        if start_ts is not None:
            st["inplay_share"] = round(
                sum(1 for t in ts if t >= start_ts) / len(ts), 3)
    states = defaultdict(int)
    for r in rows:
        states[str(r.get("us") or "none").replace("MARKET_STATE_", "")] += 1
    st["states"] = dict(states)
    if not two:
        return st
    k = float(len(two))

    def sh(c):
        return round(c / k, 3)

    def yes_px(r):
        return r["ub"] - TICK

    def no_px(r):
        return 1 - r["ua"] - TICK

    def band(px):
        return PX_MIN - EPS <= px <= PX_MAX + EPS

    def im(r):
        return (r["ib"] + r["ia"]) / 2

    st["band_yes"] = sh(sum(1 for r in two if band(yes_px(r))))
    st["band_no"] = sh(sum(1 for r in two if band(no_px(r))))
    st["guard_yes"] = sh(sum(1 for r in both if band(yes_px(r)) and
                             yes_px(r) <= im(r) - TICK + EPS))
    st["guard_no"] = sh(sum(1 for r in both if band(no_px(r)) and
                            no_px(r) <= 1 - im(r) - TICK + EPS))
    st["edge2c_yes"] = sh(sum(1 for r in both if band(yes_px(r)) and
                              im(r) - yes_px(r) >= 0.02 - EPS))
    st["edge2c_no"] = sh(sum(1 for r in both if band(no_px(r)) and
                             (1 - im(r)) - no_px(r) >= 0.02 - EPS))
    us_sp = [r["ua"] - r["ub"] for r in two]
    st["us_spread_med"], st["us_spread_p90"] = _q(us_sp, .5), _q(us_sp, .9)
    in_sp = [r["ia"] - r["ib"] for r in both]
    st["intl_spread_med"], st["intl_spread_p90"] = _q(in_sp, .5), \
        _q(in_sp, .9)
    gap = [abs((r["ub"] + r["ua"]) / 2 - im(r)) for r in both]
    st["gap_med"], st["gap_p90"] = _q(gap, .5), _q(gap, .9)
    st["us_bid_qty_med"] = _q([r.get("ubq") or 0 for r in two], .5)
    st["us_ask_qty_med"] = _q([r.get("uaq") or 0 for r in two], .5)
    st["us_mid_first"] = round((two[0]["ub"] + two[0]["ua"]) / 2, 3)
    st["us_mid_last"] = round((two[-1]["ub"] + two[-1]["ua"]) / 2, 3)
    fy = fyt = fn = fnt = 0
    for i in range(2, len(two)):
        py, pn = yes_px(two[i - 2]), no_px(two[i - 2])
        ay, an = two[i]["ua"], 1 - two[i]["ub"]
        if band(py):
            fy += ay <= py + EPS
            fyt += ay < py - EPS
        if band(pn):
            fn += an <= pn + EPS
            fnt += an < pn - EPS
    st["fillable_yes_touch"], st["fillable_yes_through"] = fy, fyt
    st["fillable_no_touch"], st["fillable_no_through"] = fn, fnt
    il = ul = same = 0
    i = 1
    while i < len(both):
        a, b = both[i - 1], both[i]
        du = abs((b["ub"] + b["ua"]) / 2 - (a["ub"] + a["ua"]) / 2)
        di = abs(im(b) - im(a))
        if du >= 0.02 - EPS or di >= 0.02 - EPS:
            if du >= 0.02 - EPS and di >= 0.02 - EPS:
                same += 1
            elif di >= 0.02 - EPS:
                il += 1
            else:
                ul += 1
            i += 5                 # one event: skip the catch-up frames
        else:
            i += 1
    st["lead"] = {"intl_first": il, "us_first": ul, "same_frame": same}
    sh_tr = []
    for r in rows:
        try:
            sh_tr.append(float(r["ush"]))
        except (KeyError, TypeError, ValueError):
            pass
    st["us_shares_traded_in_tape"] = round(sh_tr[-1] - sh_tr[0], 2) \
        if len(sh_tr) > 1 else None
    return st


def _mid(f):
    if f["ib"] is None or f["ia"] is None or f["ia"] <= f["ib"]:
        return None
    return (f["ib"] + f["ia"]) / 2


def three_way_gate(frames, tol=TOL, max_age_s=None, stable_s=None,
                   max_range=None):
    """Frames with the international fair REMOVED (ib = ia = None) wherever
    the engine's rn1_soccer_prob would return no international fair:
      siblings_missing  fewer than three outcome markets of the match
      leg_missing       an outcome has no fresh two-sided international
                        book (own: this frame; sibling: its last sample
                        older than max_age_s)
      sum_inconsistent  the three mids do not sum to 1 within tol
      unsettled         this outcome's own mid moved more than max_range
                        within stable_s, or its unbroken history is shorter
    (the sportsbook 3-way fallback is pre-game only and is not recorded, so
    it is never granted here). Returns (gated frames, per-match stats)."""
    sim = B.SIM_DEFAULTS
    max_age_s = float(sim["intl_max_age_s"] if max_age_s is None
                      else max_age_s)
    stable_s = float(sim["fair_stable_s"] if stable_s is None else stable_s)
    max_range = D(str(sim["fair_max_range"] if max_range is None
                      else max_range))
    by_match = defaultdict(dict)
    out, stats = {}, {}
    for slug, fr in frames.items():
        mo = SF.match_of(slug)
        if mo is None:
            out[slug] = [dict(f, ib=None, ia=None, gate="not_soccer")
                         for f in fr]
            continue
        by_match[mo["match"]][slug] = (mo, fr)
    for match, legs in by_match.items():
        why_n = defaultdict(int)
        sib = next(iter(legs.values()))[0]["siblings"]
        ev = sorted(((f["t"], s, i) for s, (_mo, fr) in legs.items()
                     for i, f in enumerate(fr)), key=lambda e: e[0])
        last = {}                               # slug -> (t, mid | None)
        hist = {s: [] for s in legs}
        res = {s: [None] * len(fr) for s, (_mo, fr) in legs.items()}
        for t, s, i in ev:
            f = legs[s][1][i]
            m = _mid(f)
            if m is not None and f.get("ti") is not None and \
                    abs(t - float(f["ti"])) > max_age_s:
                m = None
            last[s] = (t, m)
            h = hist[s]
            if m is None:
                del h[:]
            else:
                h.append((t, m))
                while len(h) > 1 and t - h[1][0] >= stable_s:
                    h.pop(0)
            why = None
            if len(legs) != 3 or set(legs) != set(sib):
                why = "siblings_missing"
            else:
                mids = []
                for x in sib:
                    lt, lm = last.get(x, (None, None))
                    mids.append(None if lm is None or t - lt > max_age_s
                                else lm)
                if any(x is None for x in mids):
                    why = "leg_missing"
                elif not SF.three_way_consistent(mids, tol)[0]:
                    why = "sum_inconsistent"
                else:
                    vals = [v for _t, v in h]
                    if t - h[0][0] < stable_s or \
                            max(vals) - min(vals) > max_range:
                        why = "unsettled"
            why_n[why or "fair_ok"] += 1
            res[s][i] = f if why is None else dict(f, ib=None, ia=None,
                                                   gate=why)
        out.update(res)
        tot = sum(why_n.values())
        stats[match] = dict(
            {k: v for k, v in sorted(why_n.items())}, frames=tot,
            fair_ok_share=round(why_n["fair_ok"] / tot, 4) if tot else None)
    return out, stats


def gate_audit(frames, gated, far="0.05"):
    """How well the gate isolates bad fairs. Per match: frames where the
    international mid is `far` or more from the US mid (a goal moment, a
    stale leg or a pulled book side), and how many of those the gate left
    with a fair (`far_with_fair`: these still reach the admission rule;
    `far_with_fair_us_tight` = those where the US spread is 2c or less, so
    the gap is a real venue disagreement and not a wide US book)."""
    out = defaultdict(lambda: {"far": 0, "far_with_fair": 0,
                               "far_with_fair_us_tight": 0,
                               "far_sum_inconsistent": 0, "far_other": 0})
    lim = D(str(far))
    for slug, fr in frames.items():
        mo = SF.match_of(slug)
        if mo is None:
            continue
        for f, g in zip(fr, gated[slug]):
            m = _mid(f)
            if m is None or f["yes_ask"] <= f["yes_bid"]:
                continue
            if abs(m - (f["yes_bid"] + f["yes_ask"]) / 2) < lim:
                continue
            o = out[mo["match"]]
            o["far"] += 1
            why = g.get("gate")
            if why is None:
                o["far_with_fair"] += 1
                if f["yes_ask"] - f["yes_bid"] <= D("0.02"):
                    o["far_with_fair_us_tight"] += 1
            elif why == "sum_inconsistent":
                o["far_sum_inconsistent"] += 1
            else:
                o["far_other"] += 1
    return {k: dict(v) for k, v in out.items()}


def sum_consistency(frames, tol=TOL, bucket_s=5.0):
    """Per match: share of time buckets where all three intl mids are
    present and sum to 1 within tol, and the median |sum - 1|."""
    by_match = defaultdict(dict)
    for slug, fr in frames.items():
        mo = SF.match_of(slug)
        if mo:
            by_match[mo["match"]][mo["outcome"]] = fr
    out = {}
    for match, outs in by_match.items():
        if len(outs) != 3:
            out[match] = {"outcomes_recorded": len(outs)}
            continue
        buckets = defaultdict(dict)
        for o, fr in outs.items():
            for f in fr:
                if f["ib"] is None or f["ia"] is None or f["ia"] <= f["ib"]:
                    continue
                buckets[int(f["t"] // bucket_s)][o] = (f["ib"] + f["ia"]) / 2
        full = [b for b in buckets.values() if len(b) == 3]
        devs, ok = [], 0
        for b in full:
            good, s = SF.three_way_consistent(list(b.values()), tol)
            if s is not None:
                devs.append(float(abs(s - 1)))
            ok += bool(good)
        out[match] = {"buckets_all_three": len(full),
                      "share_within_tol": round(ok / len(full), 4)
                      if full else None,
                      "median_abs_dev": round(median(devs), 4)
                      if devs else None,
                      "max_abs_dev": round(max(devs), 4) if devs else None}
    return out


def match_portfolio(rows, meta):
    """The three outcome markets of one match as ONE book, from a run's
    rows (sim keep_fills=True). Per match with at least one fill:
      by_result   exact P&L for each result (SF.outcome_pnl)
      worst       min over results; peak_loss = largest match loss after
                  any fill (filled inventory only)
      dutch       YES held on all three outcomes (locked set)
      result      the settled outcome (the leg whose outcome_yes is 1), and
                  pnl_at_result; sum_of_markets is the per-market trading
                  P&L added up (must equal pnl_at_result when all three
                  legs settled)."""
    by_match = defaultdict(list)
    for r in rows:
        mo = SF.match_of(r["slug"])
        if mo:
            by_match[mo["match"]].append((mo, r))
    out = {}
    for match, legs in by_match.items():
        outs = legs[0][0]["outcomes"]
        fills = sorted(((f["t"], mo["outcome"], f) for mo, r in legs
                        for f in r.get("fill_list") or []),
                       key=lambda x: x[0])
        if not fills:
            continue
        hold = {}
        peak = D(0)
        for _t, o, f in fills:
            h = hold.setdefault(o, {"pos": {"YES": D(0), "NO": D(0)},
                                    "cost": {"YES": D(0), "NO": D(0)}})
            h["pos"][f["side"]] += f["qty"]
            h["cost"][f["side"]] += f["px"] * f["qty"]
            peak = max(peak, SF.match_loss(hold, outs))
        pnl = SF.outcome_pnl(hold, outs)
        won = [mo["outcome"] for mo, r in legs
               if (meta.get(r["slug"]) or {}).get("outcome_yes") == 1]
        settled = len(legs) == 3 and len(won) == 1
        dl = SF.dutch_lock(hold, outs)
        tp = [r.get("trading_pnl") for _mo, r in legs]
        out[match] = {
            "legs_replayed": len(legs), "fills": len(fills),
            "holdings": {o: {k: {sd: str(v) for sd, v in d.items()}
                             for k, d in h.items()}
                         for o, h in hold.items()},
            "by_result": {k: str(v) for k, v in pnl.items()},
            "worst": str(min(pnl.values())), "peak_loss": str(peak),
            "dutch_sets": str(dl["sets"]),
            "dutch_locked_pnl": str(dl["locked_pnl"]),
            "result": won[0] if settled else None,
            "pnl_at_result": str(pnl[won[0]]) if settled else None,
            "sum_of_markets": None if any(x is None for x in tp)
            else str(sum((D(x) for x in tp), D(0))),
            "label": "THEORETICAL, PRE-REWARD"}
    return out


def _alignment(frames):
    """Soccer outcome markets are the SAME instrument on both venues (same
    slug, Yes book), so the international book is never flipped. A market
    is still dropped (None) when the generic alignment test finds no match
    at all (too few joint frames, or books more than 5c apart on average:
    a post-match / emptied book)."""
    out = {}
    for s, fr in frames.items():
        al = B.align_intl(fr)
        out[s] = {"flip": None if al is None else False,
                  "generic_test": al}
    return out


def main(argv):
    paths = [a for a in argv if not a.startswith("--") and
             a.endswith((".jsonl", ".gz"))]
    opt = {argv[i]: argv[i + 1] for i in range(len(argv) - 1)
           if argv[i] in ("--meta", "--out")}
    meta_p = opt.get("--meta", "analysis/l5/sports/soccer/"
                     "soccer_market_meta.json")
    out_p = opt.get("--out", "analysis/l5/sports/soccer/SOCCER_REPLAY")
    Path(meta_p).parent.mkdir(parents=True, exist_ok=True)
    Path(out_p).parent.mkdir(parents=True, exist_ok=True)
    frames = load(paths)
    if "--offline" in argv and Path(meta_p).exists():
        meta = json.load(open(meta_p))
    else:
        meta = B.fetch_meta(frames.keys(), meta_p)
    al = _alignment(frames)
    for s in frames:
        meta.setdefault(s, {})
        meta[s]["flip"] = al[s]["flip"]
        if not meta[s].get("closed"):
            meta[s]["outcome_yes"] = None       # CLOSED matches only
    closed = sorted(s for s in frames if meta[s].get("outcome_yes")
                    is not None)
    gated, gate_stats = three_way_gate(frames)
    head = ["THEORETICAL, PRE-REWARD soccer replay: recorded books, "
            "modelled fills, nothing realized. Recordings: " +
            ", ".join(Path(p).name for p in paths),
            "outcome markets recorded: %d (matches %d); closed with "
            "settlement: %d" % (
                len(frames), len({SF.match_of(s)["match"] for s in frames
                                  if SF.match_of(s)}), len(closed)),
            "baseline = production quoting + per-market international "
            "guard; trading_first = the same on the three-way gated fair "
            "(no fair, no opening), edge 2c, hedge give 1c"]
    res, lines, port = {}, [], {}
    for fill in FILLS:
        for name, pol in POLICIES:
            src = gated if pol.get("require_fair") else frames
            r = B.run(src, meta, dict(pol, directional=False),
                      {"pauses": True, "fill": fill, "keep_fills": True})
            key = "%s/%s" % (fill, name)
            port[key] = match_portfolio(r["rows"], meta)
            dec = defaultdict(int)
            for row in r["rows"]:
                for k, v in row["decisions"].items():
                    dec[k] += v
            r["decisions_total"] = dict(dec)
            res[key] = r
            lines.append(
                "%-38s mkts %2d traded %3d fills %4d ctr %7s spend %8s "
                "trading %8s (matched %7s residual %8s) per-mkt %+.3f se %s"
                % (key, r["markets"], r["markets_traded"], r["fills"],
                   r["contracts"], r["spend_usd"][:8],
                   r["1_settled_trading_pnl_after_fees_usd"][:8],
                   r["6_matched_pnl_usd"][:7], r["6_residual_pnl_usd"][:8],
                   r["per_market_mean_usd"], r["per_market_se_usd"]))
            lines.append("    decisions %s" % json.dumps(dict(dec),
                                                         sort_keys=True))
            for m, v in sorted(port[key].items()):
                lines.append(
                    "    match %-26s fills %3d by_result %s worst %s "
                    "peak_loss %s dutch_sets %s result %s pnl %s" % (
                        m, v["fills"], json.dumps(v["by_result"]),
                        v["worst"], v["peak_loss"], v["dutch_sets"],
                        v["result"], v["pnl_at_result"]))
    sk = next(iter(res.values()))["skipped"] if res else {}
    head.append("skipped: %s" % json.dumps(
        {v: sum(1 for x in sk.values() if x == v) for v in set(sk.values())},
        sort_keys=True))
    cons = sum_consistency(frames)
    audit = gate_audit(frames, gated)
    cl = ["three-way intl mid sum (5 s buckets, tol %s): %s %s" % (TOL, m, v)
          for m, v in sorted(cons.items())]
    cl += ["three-way gate (per frame, production rule): %s %s audit %s" % (
        m, json.dumps(v, sort_keys=True), json.dumps(audit.get(m) or {},
                                                     sort_keys=True))
           for m, v in sorted(gate_stats.items())]
    raw = raw_rows(paths)
    ko = starts(paths)
    stats = {s: tape_stats(rw, ko.get(s)) for s, rw in sorted(raw.items())}
    sl = ["tape %-34s %s" % (s[4:], json.dumps(v, sort_keys=True))
          for s, v in stats.items()]
    txt = "\n".join(head + lines + cl + sl) + "\n"
    Path(out_p + ".txt").write_text(txt)
    json.dump({"runs": res, "match_portfolio": port,
               "sum_consistency": cons, "gate": gate_stats,
               "gate_audit": audit, "tape_stats": stats,
               "alignment": al, "closed": closed},
              open(out_p + ".json", "w"), indent=1, default=str)
    print(txt)


if __name__ == "__main__":
    main(sys.argv[1:])
