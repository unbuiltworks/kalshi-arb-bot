"""Section 4 of the overnight brief: underlying-BTC / settlement conditioning.

HONESTY CLAUSE (fixed in PREREGISTRATION_OVERNIGHT.md before any result):
the index Polymarket settles its BTC up-down markets against is NOT available
to this study. The Zenodo archive carries no strike, no reference price and no
settlement index. Everything here is derived from a DIFFERENT venue's spot
tape (Coinbase BTC-USD 1m), carried verbatim with the label
PROXY_NOT_SETTLEMENT_INDEX. Distance-from-strike is NOT derivable and is NOT
derived. Any result below is a statement about a proxy, never about the
settlement condition.

Resolution limit, stated before the numbers: the proxy is 1-minute. A 15m
window therefore holds at most 15 proxy observations and a 5m window at most
5, so within-window underlying variation is coarse by construction. A null
result here is partly a resolution null and is reported as such.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

from analysis.l5.microstructure.clips import build_clips
from analysis.l5.microstructure.load import load
from analysis.l5.microstructure.parpair import find_pairs, _id
from analysis.l5.microstructure.stats import cliffs_delta, qs, spearman

LABEL = "PROXY_NOT_SETTLEMENT_INDEX"
GAP_S = 10
CLIP80 = 80.0
VOL_WINDOW_MIN = 15


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


class Proxy:
    """1-minute spot closes, indexed by minute-start epoch."""

    def __init__(self, path: str):
        d = json.load(open(path, encoding="utf-8"))
        if d.get("LABEL") != LABEL:
            raise SystemExit("refusing a series that is not labelled %s" % LABEL)
        self.label = d["LABEL"]
        self.source = d["source"]
        self.close = {int(r["t"]): float(r["close"]) for r in d["rows"]}
        self.minutes = sorted(self.close)
        self.first, self.last = self.minutes[0], self.minutes[-1]

    def ret(self, t: int, mins: int) -> Optional[float]:
        """Log return over `mins` minutes ending at the last minute CLOSED
        strictly before t. No future information: a minute whose close is
        stamped at or after t is not used."""
        end = (int(t) // 60) * 60 - 60
        beg = end - 60 * mins
        a, b = self.close.get(beg), self.close.get(end)
        if a is None or b is None or a <= 0 or b <= 0:
            return None
        return math.log(b / a)

    def vol(self, t: int, mins: int = VOL_WINDOW_MIN) -> Optional[float]:
        end = (int(t) // 60) * 60 - 60
        pts = [self.close[end - 60 * k] for k in range(mins + 1)
               if (end - 60 * k) in self.close]
        if len(pts) < 6:
            return None
        pts = pts[::-1]
        rs = [math.log(pts[i + 1] / pts[i]) for i in range(len(pts) - 1)
              if pts[i] > 0 and pts[i + 1] > 0]
        if len(rs) < 5:
            return None
        m = sum(rs) / len(rs)
        return math.sqrt(sum((r - m) ** 2 for r in rs) / (len(rs) - 1))


def features(px: Proxy, t: int) -> Dict[str, Optional[float]]:
    return {
        "proxy_ret_1m": px.ret(t, 1),
        "proxy_ret_3m": px.ret(t, 3),
        "proxy_ret_5m": px.ret(t, 5),
        "proxy_realized_vol_15m": px.vol(t),
    }


def _lift(rows: List[dict], feat: str) -> Dict[str, Any]:
    """Does the side L5 next takes line up with the sign of a proxy move?

    UP-aligned means: proxy moved up and L5's clip is on UP, or proxy moved
    down and L5's clip is on DOWN. Compared against the base rate of UP
    clips, so a side-preference imbalance cannot masquerade as alignment.
    """
    use = [r for r in rows if r.get(feat) is not None
           and abs(r[feat]) > 1e-12]
    if len(use) < 50:
        return {"n": len(use), "VERDICT": "INSUFFICIENT_SAMPLE_min_50"}
    align = sum(1 for r in use
                if (r[feat] > 0) == (r["outcome"] == "up"))
    base_up = sum(1 for r in use if r["outcome"] == "up") / len(use)
    up_moves = sum(1 for r in use if r[feat] > 0) / len(use)
    # chance alignment if side were independent of the move
    chance = up_moves * base_up + (1 - up_moves) * (1 - base_up)
    obs = align / len(use)
    return {
        "n": len(use),
        "share_proxy_move_up": round(up_moves, 4),
        "base_rate_clip_on_up": round(base_up, 4),
        "observed_alignment": round(obs, 4),
        "chance_alignment_if_independent": round(chance, 4),
        "lift": round(obs / chance, 4) if chance else None,
        "interpretation": "lift>1 momentum-aligned, lift<1 contrarian",
    }


def analyse(tag: str, evp: str, pqp: str, px: Proxy) -> Dict[str, Any]:
    ev = jl(evp)
    byw: Dict[str, List[dict]] = defaultdict(list)
    for r in ev:
        byw[r["market_slug"]].append(r)
    for g in byw.values():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
    books, wins = load(pqp, contract_length=tag, slugs=set(byw))

    clips: List[dict] = []
    pair_rows: List[dict] = []
    covered = uncovered = 0
    for slug, seq in byw.items():
        pairs, member = find_pairs(seq)
        passive = [r for r in seq if _id(r) not in member]
        cs = build_clips(passive, books, gap_s=GAP_S, slug=slug,
                         clen=tag, start=int(slug.rsplit("-", 1)[1]))
        cs = sorted(cs, key=lambda c: c["t_first"])
        for i, c in enumerate(cs):
            t = int(c["t_first"])
            if px.first <= t <= px.last:
                covered += 1
            else:
                uncovered += 1
                continue
            # same "apparent withdrawal" definition as structure.py S3:
            # no LATER clip on this side inside the window.
            later_same = any(x["outcome"] == c["outcome"] for x in cs[i + 1:])
            row = dict(features(px, t))
            row.update(outcome=c["outcome"], slug=slug,
                       frac80=min(c["clip_total_shares"] / CLIP80, 1.0),
                       total=c["clip_total_shares"],
                       withdrew=not later_same)
            clips.append(row)
        for p in pairs:
            t = int(p["up"]["t"])
            if not (px.first <= t <= px.last):
                continue
            row = dict(features(px, t))
            row.update(slug=slug, quantity=p["up"]["quantity"])
            pair_rows.append(row)

    res: Dict[str, Any] = {
        "proxy_source": px.source,
        "proxy_label": px.label,
        "proxy_coverage_clips": {"in_range": covered, "out_of_range": uncovered},
        "n_clips": len(clips),
        "n_par_pair_transactions": len(pair_rows),
    }

    # E4a -- side choice vs proxy direction
    res["E4a_side_choice_vs_proxy_direction"] = {
        f: _lift(clips, f) for f in
        ("proxy_ret_1m", "proxy_ret_3m", "proxy_ret_5m")}

    # E4b -- exposed size vs proxy volatility / velocity
    res["E4b_clip_size_vs_proxy"] = {}
    for f in ("proxy_realized_vol_15m", "proxy_ret_1m", "proxy_ret_5m"):
        use = [r for r in clips if r.get(f) is not None]
        if len(use) < 50:
            res["E4b_clip_size_vs_proxy"][f] = {
                "n": len(use), "VERDICT": "INSUFFICIENT_SAMPLE_min_50"}
            continue
        res["E4b_clip_size_vs_proxy"][f] = {
            "vs_frac_of_80": spearman([r[f] for r in use],
                                      [r["frac80"] for r in use]),
            "vs_abs_move_frac_of_80": spearman(
                [abs(r[f]) for r in use], [r["frac80"] for r in use]),
        }

    # E4c -- withdrawal vs proxy state
    wd = [r for r in clips if r["withdrew"]]
    co = [r for r in clips if not r["withdrew"]]
    res["E4c_withdrawal_vs_proxy"] = {
        "n_withdrawal": len(wd), "n_continuation": len(co)}
    for f in ("proxy_realized_vol_15m", "proxy_ret_1m", "proxy_ret_5m"):
        a = [r[f] for r in wd if r.get(f) is not None]
        b = [r[f] for r in co if r.get(f) is not None]
        res["E4c_withdrawal_vs_proxy"][f] = (
            {"n_a": len(a), "n_b": len(b),
             "VERDICT": "INSUFFICIENT_SAMPLE_min_50_continuation"}
            if len(b) < 50 else
            {"withdrawal": qs(a), "continuation": qs(b),
             "cliffs_delta": cliffs_delta(a, b)})

    # E4d -- par-pair injection vs proxy volatility
    a = [r["proxy_realized_vol_15m"] for r in pair_rows
         if r.get("proxy_realized_vol_15m") is not None]
    b = [r["proxy_realized_vol_15m"] for r in clips
         if r.get("proxy_realized_vol_15m") is not None]
    res["E4d_par_pair_vs_proxy_vol"] = (
        {"n_a": len(a), "n_b": len(b),
         "VERDICT": "INSUFFICIENT_SAMPLE_min_50"} if min(len(a), len(b)) < 50
        else {"par_pair": qs(a), "passive_clip": qs(b),
              "cliffs_delta_pair_minus_clip": cliffs_delta(a, b)})
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events15", required=True)
    ap.add_argument("--events5", required=True)
    ap.add_argument("--parquet15", required=True)
    ap.add_argument("--parquet5", required=True)
    ap.add_argument("--proxy", default="data/btc_spot_proxy/btc_1m.json")
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    if not os.path.exists(a.proxy):
        out = {"S4_underlying_conditioning":
               {"VERDICT": "NOT TESTED -- DATA UNAVAILABLE",
                "detail": "no proxy series on disk and the exact settlement "
                          "index is not obtainable; no substitute was "
                          "silently used"}}
    else:
        px = Proxy(a.proxy)
        out = {
            "protocol": "PREREGISTRATION_OVERNIGHT.md",
            "SETTLEMENT_INDEX": "UNAVAILABLE -- not in the Zenodo archive and "
                                "not obtainable; no strike, no reference "
                                "price, no index. Distance-from-strike is NOT "
                                "derivable and is not derived.",
            "RESOLUTION_LIMIT": "proxy is 1-minute: <=15 observations per 15m "
                                "window, <=5 per 5m window",
            "proxy_window": {"first_utc": None, "n_minutes": len(px.minutes)},
            "S4_BTC15m": analyse("15m", a.events15, a.parquet15, px),
            "S4_BTC5m": analyse("5m", a.events5, a.parquet5, px),
        }
    os.makedirs(a.outdir, exist_ok=True)
    with open(os.path.join(a.outdir, "UNDERLYING.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("written UNDERLYING.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
