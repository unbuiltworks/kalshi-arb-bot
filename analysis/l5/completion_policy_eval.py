"""Compare COMPLETION-order policies on the same chronological opportunities.

WHY A DIFFERENT TAPE. The 2026-10-26 holdout is five windows and eight
sequences, and three stranded legs decide its sign. That is enough to show
the 0.99 ceiling is inert; it is nowhere near enough to choose a completion
policy. `data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz` is 61,497 BBO
rows across 400 distinct windows, which is 80x the holdout.

WHAT IS AND IS NOT TRANSFERABLE FROM IT, STATED UP FRONT. It is a DIFFERENT
VENUE (Gemini, not PMUS) and a DIFFERENT DURATION (5 minutes, not 15). So
it cannot establish PMUS fee economics or PMUS queue behaviour, and no
number from it is a PMUS result. What it CAN establish is the structural
question this task is about: given a filled first leg in a short-dated
binary up/down market, how often does a resting complement fill, at what
price, and what does the residual cost when it does not. That is a property
of binary up/down book dynamics, and 400 windows of it is evidence where
five windows is an anecdote.

PMUS confirmation uses `analysis/pmus/evidence/observer_rows_settled_
windows.jsonl` -- 5 settled PMUS windows from 2026-09-23 carrying the
authenticated BBO at each L5 action. Those are untouched by every earlier
analysis in this programme, which all used 2026-10-25/26 data.

THE OPENING RULE IS HELD FIXED so the comparison is about completion only.
It opens the CHEAPER side by own maker cost -- causal, symmetric, and the
side of the book L5 actually rests on (his maker price median is 0.38 and
p25 is 0.18). Every policy below gets the identical opening decisions.

TIME IS MEASURED TO EXPIRY, WHICH NEEDED FINDING OUT. Every row in that tape
precedes its symbol's nominal time, by between 31 and 4,125 seconds -- so the
timestamp in `BTC05M2608302330` is the contract's EXPIRY, not a window start,
and the tape is a forward curve sampling many upcoming contracts rather than
a high-resolution recording of one. A first attempt that read it as a start
selected ZERO rows.

So the decision window here is the last 900 seconds before expiry, and
`elapsed_s` is `900 - time_to_expiry`. That maps onto the replay's native
600s/720s cutoffs with NO scaling, and it is the right clock anyway: what
decides whether a resting complement fills is how long is left, not a
nominal start. It yields 12,673 rows across 205 windows, median 52 rows per
window.
"""
from __future__ import annotations

import collections
import datetime as dt
import gzip
import json
import pathlib
from decimal import Decimal as D
from typing import Any, Dict, List

from src import l5_maker_placement_replay as M
from src import l5_sequential_replay as R

ROOT = pathlib.Path(__file__).resolve().parents[2]
GEMINI = ROOT / "data/transfer_backtest/gemini_bbo_btc05m.jsonl.gz"
OBSERVER = ROOT / "analysis/pmus/evidence/observer_rows_settled_windows.jsonl"


def _window_start(sym: str) -> float:
    """GEMI-BTC05M2608302330-UP -> epoch of 2026-08-30 23:30 UTC."""
    core = sym.split("-")[1]            # BTC05M2608302330
    d = core[6:]                        # 2608302330
    t = dt.datetime(2000 + int(d[0:2]), int(d[2:4]), int(d[4:6]),
                    int(d[6:8]), int(d[8:10]), tzinfo=dt.timezone.utc)
    return t.timestamp()


#: the decision window: the last this-many seconds before expiry.
LOOKBACK_S = 900.0


def load_gemini(*, lookback_s: float = LOOKBACK_S) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with gzip.open(GEMINI, "rt") as f:
        for line in f:
            r = json.loads(line)
            b, a = r.get("bid"), r.get("ask")
            if not b or not a:
                continue
            bid, ask = D(str(b)), D(str(a))
            if not (0 < bid < ask < 1):
                continue
            sym = r["symbol"]
            tte = _window_start(sym) - float(r["t"])      # seconds to expiry
            if not (0 < tte <= lookback_s):
                continue
            rows.append({"market": sym.rsplit("-", 1)[0], "t": r["t"],
                         "elapsed_s": lookback_s - tte, "bid": bid,
                         "ask": ask,
                         "side": ("UP" if bid <= (D(1) - ask) else "DOWN")})
    rows.sort(key=lambda r: (r["market"], r["t"]))
    return rows


def load_observer(*, scale: float = 1.0) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in open(OBSERVER):
        r = json.loads(line)
        p = (r.get("pmus") or {})
        bbo = p.get("bbo") or {}
        b, a = bbo.get("bid"), bbo.get("ask")
        slug, ws = p.get("slug"), r.get("window_start_utc")
        if not (b and a and slug and ws):
            continue
        bid, ask = D(str(b)), D(str(a))
        if not (0 < bid < ask < 1):
            continue
        t0 = dt.datetime.fromisoformat(ws.replace("Z", "+00:00")).timestamp()
        t = dt.datetime.fromisoformat(
            r["receipt_utc"].replace("Z", "+00:00")).timestamp()
        el = (t - t0) * scale
        if el < 0:
            continue
        rows.append({"market": slug, "t": t, "elapsed_s": el,
                     "bid": bid, "ask": ask,
                     "side": ("UP" if bid <= (D(1) - ask) else "DOWN")})
    rows.sort(key=lambda r: (r["market"], r["t"]))
    return rows


def outcomes(rows) -> Dict[str, Dict[str, Any]]:
    """Terminal-book inference, labelled as such. Never a venue settlement."""
    last: Dict[str, Any] = {}
    for r in rows:
        last[r["market"]] = r
    out = {}
    for m, r in last.items():
        mid = (r["bid"] + r["ask"]) / D(2)
        if mid >= D("0.75"):
            w = "UP"
        elif mid <= D("0.25"):
            w = "DOWN"
        else:
            continue                 # undecided at the last read: no outcome
        out[m] = {"winner": w, "evidence": R.TERMINAL_BOOK_INFERRED}
    return out


#: the candidate completion policies. Every one shares the opening rule and
#: the 0.96 live ceiling; they differ ONLY in how the complement is rested.
POLICIES = (
    ("A JOIN (baseline)",      dict(complete_improve_ticks=0)),
    ("B HOLD (no reprice)",    dict(complete_improve_ticks=0,
                                    complete_drift=D("9"))),
    ("C TRACK (drift 0.01)",   dict(complete_improve_ticks=0,
                                    complete_drift=D("0.01"))),
    ("D IMPROVE 1 tick",       dict(complete_improve_ticks=1)),
    ("E IMPROVE 2 ticks",      dict(complete_improve_ticks=2)),
    ("F ESCALATE 1->0",        dict(complete_improve_ticks=1,
                                    escalate_after_s=480.0)),
    ("G ESCALATE 2->0",        dict(complete_improve_ticks=2,
                                    escalate_after_s=480.0)),
    ("H JOIN + REDUCE@660",    dict(complete_improve_ticks=0,
                                    reduce_after_s=660.0)),
    ("I ESCALATE + REDUCE",    dict(complete_improve_ticks=1,
                                    escalate_after_s=480.0,
                                    reduce_after_s=660.0)),
)


def run(rows, outs, fill_mode=M.FILL_STRICT) -> List[Dict[str, Any]]:
    res = []
    for label, kw in POLICIES:
        o = M.replay(rows, outs, ceiling=D("0.96"), tight_ceiling=D("0.96"),
                     fill_mode=fill_mode, open_maker=True,
                     policy_label=label, **kw)
        res.append(o)
    return res


def _boot_ci(vals, n_boot=2000, seed=11):
    """Bootstrap 95% CI of the MEAN per-window net. Stated because a policy
    chosen on a point estimate of 400 noisy windows is not chosen."""
    import random
    if not vals:
        return (None, None)
    rnd = random.Random(seed)
    means = []
    n = len(vals)
    for _ in range(n_boot):
        means.append(sum(rnd.choice(vals) for _ in range(n)) / n)
    means.sort()
    return (means[int(0.025 * n_boot)], means[int(0.975 * n_boot)])


def report(rows, outs, title) -> None:
    print("=" * 104)
    print(title)
    print("=" * 104)
    nw = len({r["market"] for r in rows})
    print("rows %d   windows %d   windows with an inferred outcome %d"
          % (len(rows), nw, len(outs)))
    print()
    hdr = ("%-22s %-5s %-5s %-5s %-11s %-11s %-11s %-9s %s"
           % ("policy", "cmp", "str", "red", "net $", "pairs $",
              "strand $", "fill%", "mean/window [95% CI]"))
    print(hdr)
    print("-" * 104)
    for o in run(rows, outs):
        pairs = sum((D(c["net_pnl"]) for c in o["completed_pairs"]), D(0))
        strand = sum((D(s["net_pnl"]) for s in o["stranded"]), D(0))
        seq = o["n_completed"] + o["n_stranded"] + o["n_reduced"]
        fill = (100.0 * o["n_completed"] / seq) if seq else 0.0
        per = []
        byw = collections.defaultdict(D)
        for c in o["completed_pairs"]:
            byw[c["market"]] += D(c["net_pnl"])
        for s in o["stranded"]:
            byw[s["market"]] += D(s["net_pnl"])
        # REDUCED legs are realized P&L and belong in the per-window series.
        # Leaving them out made H/I look positive on the PMUS panel purely
        # by DELETING their loss-making windows from the sample -- the
        # reduce policy turns strands into reductions, so those windows
        # simply vanished. That is a reporting defect, not a result.
        for s in o["reduced"]:
            byw[s["market"]] += D(s["net_pnl"])
        per = [float(v) for v in byw.values()]
        lo, hi = _boot_ci(per)
        print("%-22s %-5d %-5d %-5d %-11s %-11s %-11s %-9s %s"
              % (o["policy_label"], o["n_completed"], o["n_stranded"],
                 o["n_reduced"],
                 D(o["net_realized_usd"]).quantize(D("0.01")),
                 pairs.quantize(D("0.01")), strand.quantize(D("0.01")),
                 "%.1f" % fill,
                 ("%+.4f [%+.4f, %+.4f]" % (sum(per) / len(per), lo, hi))
                 if per else "n/a"))


def main() -> int:
    g = load_gemini()
    report(g, outcomes(g),
           "GEMINI BTC05M -- 400 windows (STRUCTURAL evidence; NOT a PMUS "
           "result)")
    print()
    o = load_observer()
    report(o, outcomes(o),
           "PMUS BTC15M 2026-09-23 observer -- untouched, authenticated BBO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
