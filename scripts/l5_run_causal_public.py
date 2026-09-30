"""Run the causal simulator over live full-L2 public-gateway windows.

FILL EVIDENCE CLASS: INFERRED, not observed. The public feed carries no
individual prints. Between two polls we know the CUMULATIVE `sharesTraded` moved
by some amount and what the last price was, so the simulator is fed a synthetic
print of (volume delta) at (last price). That is an inference about how the
volume was distributed, and every number produced here inherits it. Print-derived
results use the authenticated WS slice instead and are reported separately.

The queue side is NOT inferred: displayed size at our exact price comes from the
real 30-44 level ladder, which is the upgrade this data buys.
"""
from __future__ import annotations

import glob
import gzip
import json
import statistics
import subprocess
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.l5_causal_sim import CausalSim, SOURCED_REBATE_PER_CONTRACT

CLIPS = (10, 20, 30, 50)


def _d(v) -> Optional[D]:
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return D(str(v))
    except Exception:                                        # noqa: BLE001
        return None


def load_window(path: str) -> List[dict]:
    op = gzip.open if path.endswith(".gz") else open
    rows = [json.loads(l) for l in op(path, "rt", encoding="utf-8")]
    rows.sort(key=lambda r: r["recv_t"])
    return rows


def settlement(slug: str) -> Optional[str]:
    r = subprocess.run(["curl", "-sS", "--max-time", "20",
                        "https://gateway.polymarket.us/v1/markets/%s/bbo" % slug],
                       capture_output=True, text=True)
    try:
        md = json.loads(r.stdout).get("marketData") or {}
    except Exception:                                        # noqa: BLE001
        return None
    px = _d(md.get("settlementPx"))
    if px is None:
        return None
    return "up" if px == 1 else ("down" if px == 0 else None)


def events(rows: List[dict]):
    """Causal event stream. A synthetic print is emitted only where cumulative
    volume actually moved, and it is priced at the venue's own last trade."""
    prev_vol = None
    for r in rows:
        t = float(r["recv_t"])
        bids = {}
        offers = {}
        for l in (r.get("bids") or []):
            px, q = _d(l.get("px")), _d(l.get("qty"))
            if px is not None and q is not None and q > 0:
                bids[px] = q
        for l in (r.get("offers") or []):
            px, q = _d(l.get("px")), _d(l.get("qty"))
            if px is not None and q is not None and q > 0:
                offers[px] = q
        bid = max(bids) if bids else None
        ask = min(offers) if offers else None
        yield (t, "book", {
            "bid": str(bid) if bid is not None else None,
            "ask": str(ask) if ask is not None else None,
            "bid_qty": str(bids[bid]) if bid is not None else None,
            "ask_qty": str(offers[ask]) if ask is not None else None,
            "status": "OK" if (bid is not None and ask is not None)
                      else "ONE_SIDED",
            "_ladder": {"bids": bids, "offers": offers}})
        vol = _d(r.get("shares_traded"))
        last = _d(r.get("last_trade_px"))
        if vol is not None and prev_vol is not None and last is not None:
            dv = vol - prev_vol
            if dv > 0:
                yield (t, "trade", {"px": str(last), "qty": str(dv)})
        if vol is not None:
            prev_vol = vol


ARMS = {
    "persistent_maker": dict(persistent_maker=True),
    "maker+take": dict(persistent_maker=True, reactive_take=True),
    "maker+take+extend": dict(persistent_maker=True, reactive_take=True,
                              basis_extend=True),
}


if __name__ == "__main__":
    paths = sorted(glob.glob("data/pmus_public_l2/window_*.jsonl.gz"))
    out: Dict[str, Any] = {"LABEL": "CAUSAL_SIM_PUBLIC_L2",
                           "fill_evidence": "INFERRED (no individual prints)",
                           "windows": [], "arms": {}}
    wins = {}
    for p in paths:
        rows = load_window(p)
        slug = rows[0]["slug"]
        w = settlement(slug)
        wins[p] = (slug, w, rows)
        print("%s  %d snapshots  settlement=%s" % (slug[-5:], len(rows), w),
              flush=True)
    print()
    hdr = ("%-20s %4s %6s %7s %7s %8s %9s %8s %8s %8s %7s")
    print(hdr % ("arm", "clip", "fills", "pairs", "resid", "matched$",
                 "resid$", "fees", "incent", "NET", "peak$"))
    for arm, kw in ARMS.items():
        for clip in CLIPS:
            agg = []
            for p, (slug, w, rows) in wins.items():
                if w is None:
                    continue
                sim = CausalSim(clip=D(clip), **kw)
                res = sim.run(events(rows), winner=w)
                agg.append(res)
            if not agg:
                continue
            n = len(agg)
            f = lambda k: sum(D(str(x[k])) for x in agg) / n   # noqa: E731
            row = {"arm": arm, "clip": clip, "n": n,
                   "fills": sum(x["n_fills"] for x in agg) / n,
                   "pairs": float(f("matched_qty")) / clip,
                   "resid_qty": float(f("resid_qty")),
                   "matched_pnl": float(f("matched_pnl")),
                   "resid_pnl": float(f("resid_pnl")),
                   "fees": float(f("fees")),
                   "incentive": float(f("sourced_incentive")),
                   "net": float(f("realistic_net")),
                   "peak_cash": float(f("peak_cash")),
                   "contracts": float(f("contracts")),
                   "worst_residual": float(f("worst_residual"))}
            out["arms"].setdefault(arm, []).append(row)
            print(hdr % (arm, clip, "%.0f" % row["fills"],
                         "%.2f" % row["pairs"], "%.0f" % row["resid_qty"],
                         "%+.3f" % row["matched_pnl"],
                         "%+.3f" % row["resid_pnl"], "%.3f" % row["fees"],
                         "%.3f" % row["incentive"], "%+.3f" % row["net"],
                         "%.0f" % row["peak_cash"]), flush=True)
    json.dump(out, open("analysis/l5/v1_causal_public_l2.json", "w"),
              indent=1, default=str)
    print("\nWROTE analysis/l5/v1_causal_public_l2.json")
