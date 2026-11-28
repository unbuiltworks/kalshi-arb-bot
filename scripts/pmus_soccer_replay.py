"""Soccer three-way replay: recorded two-venue soccer tapes (one binary
market per outcome) through src/pmus_directional_backtest.py, baseline vs
trading-first (edge 2c, hedge give 1c), touch and through fills, plus the
three-way sum consistency of the international mids.

OFFLINE apart from the public settlement lookups (gateway
/v1/market/slug/atc-<slug>; CLOSED markets only). Every P&L is THEORETICAL
and PRE-REWARD: recorded books, modelled fills, nothing realized.

usage: python3 scripts/pmus_soccer_replay.py REC.jsonl [REC2.jsonl ...]
       [--meta META.json] [--out OUT_PREFIX] [--offline]
The recorder's `slug` is the international slug; the US market is
atc-<slug>; ib/ia are the international Yes book (first outcome)."""
import json
import sys
from collections import defaultdict
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


def load(paths):
    raw = B.load_cross_venue(paths)          # keys "aec-<intl slug>"
    return {"atc-" + k[4:]: v for k, v in raw.items()}


def sum_consistency(frames, tol="0.04", bucket_s=5.0):
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
    for s, fr in frames.items():
        meta.setdefault(s, {})
        al = B.align_intl(fr)
        meta[s]["flip"] = None if al is None else (not al)
        if not meta[s].get("closed"):
            meta[s]["outcome_yes"] = None       # CLOSED matches only
    closed = sorted(s for s in frames if meta[s].get("outcome_yes")
                    is not None)
    head = ["THEORETICAL, PRE-REWARD soccer replay: recorded books, "
            "modelled fills, nothing realized. Recordings: " +
            ", ".join(paths),
            "outcome markets recorded: %d (matches %d); closed with "
            "settlement: %d %s" % (
                len(frames), len({SF.match_of(s)["match"] for s in frames
                                  if SF.match_of(s)}), len(closed), closed)]
    res, lines = {}, []
    for fill in FILLS:
        for name, pol in POLICIES:
            r = B.run(frames, meta, dict(pol, directional=False),
                      {"pauses": True, "fill": fill})
            res["%s/%s" % (fill, name)] = r
            lines.append(
                "%-38s traded %3d fills %4d ctr %7s spend %8s trading %8s "
                "(matched %7s residual %8s) per-mkt %+.3f se %s" % (
                    "%s/%s" % (fill, name), r["markets_traded"], r["fills"],
                    r["contracts"], r["spend_usd"][:8],
                    r["1_settled_trading_pnl_after_fees_usd"][:8],
                    r["6_matched_pnl_usd"][:7], r["6_residual_pnl_usd"][:8],
                    r["per_market_mean_usd"], r["per_market_se_usd"]))
    sk = next(iter(res.values()))["skipped"] if res else {}
    head.append("skipped: %s" % json.dumps(
        {v: sum(1 for x in sk.values() if x == v) for v in set(sk.values())}))
    cons = sum_consistency(frames)
    cl = ["three-way intl mid sum (5 s buckets, tol 0.04): %s %s" % (m, v)
          for m, v in sorted(cons.items())]
    txt = "\n".join(head + lines + cl) + "\n"
    Path(out_p + ".txt").write_text(txt)
    json.dump({"runs": res, "sum_consistency": cons}, open(
        out_p + ".json", "w"), indent=1, default=str)
    print(txt)


if __name__ == "__main__":
    main(sys.argv[1:])
