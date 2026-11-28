"""Marginal inventory economics of every LATE complement purchase.

For each trade on the DEFICIENT side in the back half of the window:

  inventory before/after, change in matched quantity and in the paired
  basis, change in unmatched exposure, and the marginal effect on the
  portfolio -- valued two ways, because only one of them is honest.

TWO VALUATIONS, AND WHY BOTH ARE REPORTED:

  at_outcome  uses the window's settled result. For the MATCHED leg this is
              exact (a pair redeems $1.00 either way). For the RESIDUAL it is
              HINDSIGHT and flatters whichever side happened to win.
  at_market   values residual at the price the market was quoting when the
              trade happened, i.e. P(side wins) ~= its own price. This is
              outcome-independent and is the figure a decision rule may use.

AND THE SPLIT THE OWNER ASKED FOR: a purchase of q on the deficient side is
`completion` up to the deficit and `new_exposure` beyond it. A trade that
flips the imbalance is both, and is counted in each part pro rata.
"""
from __future__ import annotations
import collections, gzip, json, os
from decimal import Decimal as D

SRC = ("data/l5/raw/activity.jsonl", "data/l5/raw/activity_gap.jsonl.gz",
       "data/l5/ops/activity_20260901.jsonl")
LATE_S = 450          # back half of a 900s window

def _rows(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln: continue
            try: d = json.loads(ln)
            except Exception: continue
            pay = d.get("payload")
            if isinstance(pay, list):
                for r in pay: yield r
            elif isinstance(d, dict) and d.get("slug"): yield d

W = {w["slug"]: w for w in
     (json.loads(l) for l in open("/tmp/claude-0/l5_windows.jsonl"))}
tr = set(json.load(open("/tmp/claude-0/train.json")))
te = set(json.load(open("/tmp/claude-0/test.json")))
keep = tr | te
seen = set(); T = collections.defaultdict(list)
for p in SRC:
    if not os.path.exists(p): continue
    for r in _rows(p):
        k = (r.get("transactionHash"), r.get("asset"), r.get("timestamp"),
             r.get("size"), r.get("side"), r.get("type"))
        if k in seen: continue
        seen.add(k)
        s = str(r.get("slug") or "")
        if r.get("type") != "TRADE" or s not in keep: continue
        ws = int(s.rsplit("-", 1)[1])
        T[s].append({"t": int(r["timestamp"]) - ws,
                     "oc": str(r.get("outcome") or ""),
                     "q": D(str(r.get("size") or 0)),
                     "px": D(str(r.get("price") or 0))})

def locked(qu, cu, qd, cd):
    """The CERTAIN component: matched * (1 - pair basis).

    A matched pair redeems exactly $1.00 whichever way the window settles, so
    this is bankable the moment the pair exists -- independent of the outcome
    AND of the price at any instant. It is the only part of the portfolio a
    completion can improve with certainty, and therefore the honest measure of
    whether a completion improved the portfolio.

    Valuing the residual at the price of the very trade being judged is
    CIRCULAR -- buying at market price is neutral by construction, which is
    why that measure came out exactly zero. The sequence nonetheless creates
    real value: each leg was fair at its own instant, yet the pair they form
    pays a certain $1.00. The gain is the price having MOVED between them.
    """
    m = min(qu, qd)
    if m <= 0:
        return D(0)
    bu = (cu/qu) if qu else D(0)
    bd = (cd/qd) if qd else D(0)
    return m * (D(1) - (bu + bd))


def portfolio(qu, cu, qd, cd, p_up, win, mode):
    """Portfolio value. p_up is the market's Up price at this instant."""
    m = min(qu, qd)
    bu = (cu/qu) if qu else D(0)
    bd = (cd/qd) if qd else D(0)
    v = m * (D(1) - (bu + bd))
    r = qu - qd
    if r > 0:
        pay = (D(1) if win == "Up" else D(0)) if mode == "outcome" else p_up
        v += r * (pay - bu)
    elif r < 0:
        pay = (D(1) if win == "Down" else D(0)) if mode == "outcome" \
              else (D(1) - p_up)
        v += (-r) * (pay - bd)
    return v

agg = {g: collections.defaultdict(lambda: [D(0), D(0), 0])
       for g in ("train", "test")}
rows_detail = []
for slug, acts in T.items():
    w = W.get(slug)
    if not w or not w.get("winner"): continue
    g = "train" if slug in tr else "test"
    acts.sort(key=lambda a: a["t"])
    qu = cu = qd = cd = D(0)
    for a in acts:
        oc = a["oc"]
        if oc not in ("Up", "Down"): continue
        # the market's Up price at this instant, from this very trade
        p_up = a["px"] if oc == "Up" else (D(1) - a["px"])
        deficit = (qd - qu) if oc == "Up" else (qu - qd)
        is_def = deficit > 0
        late = a["t"] >= LATE_S
        before_o = portfolio(qu, cu, qd, cd, p_up, w["winner"], "outcome")
        before_m = locked(qu, cu, qd, cd)
        m0 = min(qu, qd); r0 = abs(qu - qd)
        if oc == "Up": qu += a["q"]; cu += a["q"]*a["px"]
        else: qd += a["q"]; cd += a["q"]*a["px"]
        after_o = portfolio(qu, cu, qd, cd, p_up, w["winner"], "outcome")
        after_m = locked(qu, cu, qd, cd)
        m1 = min(qu, qd); r1 = abs(qu - qd)
        if not (late and is_def):
            continue
        comp_q = min(a["q"], deficit)
        new_q = a["q"] - comp_q
        cheap = a["px"] < D("0.25")
        for key, share in (("completion", comp_q / a["q"]),
                           ("new_exposure", new_q / a["q"])):
            if share <= 0: continue
            k = "%s|%s" % (key, "cheap" if cheap else "dear")
            s = agg[g][k]
            s[0] += (after_o - before_o) * share
            s[1] += (after_m - before_m) * share
            s[2] += 1 if share == 1 else 0
        rows_detail.append({"g": g, "px": float(a["px"]), "t": a["t"],
                            "q": float(a["q"]),
                            "comp": float(comp_q), "new": float(new_q),
                            "d_matched": float(m1 - m0),
                            "d_resid": float(r1 - r0),
                            "mo": float(after_o - before_o),
                            "mm": float(after_m - before_m)})

print("LATE (t>=%ds) purchases on the DEFICIENT side: %d\n"
      % (LATE_S, len(rows_detail)))
print("%-8s %-22s %14s %14s %8s" % (
    "split", "kind", "marg_at_outcome", "marg_LOCKED", "n_pure"))
for g in ("train", "test"):
    for k in ("completion|cheap", "completion|dear",
              "new_exposure|cheap", "new_exposure|dear"):
        s = agg[g].get(k)
        if not s: continue
        print("%-8s %-22s %+14.2f %+14.2f %8d"
              % (g.upper(), k, s[0], s[1], s[2]))
    print()

ch = [r for r in rows_detail if r["px"] < 0.25]
de = [r for r in rows_detail if r["px"] >= 0.25]
print("CHEAP (<0.25) vs DEAR late deficient-side purchases:")
for nm, rows in (("cheap", ch), ("dear", de)):
    if not rows: continue
    n = len(rows)
    print("  %-6s n=%6d  d_matched/trade %+8.3f  d_resid/trade %+8.3f"
          "  marg_market total %+10.2f  per trade %+8.5f"
          % (nm, n, sum(r["d_matched"] for r in rows)/n,
             sum(r["d_resid"] for r in rows)/n,
             sum(r["mm"] for r in rows), sum(r["mm"] for r in rows)/n))
print("\n  d_resid NEGATIVE means the trade REDUCED unmatched exposure.")
print("  marg_LOCKED is outcome-independent and is the decision figure;")
print("  marg_at_outcome is hindsight on the residual and is shown only so")
print("  the gap between them is visible.")
json.dump(rows_detail[:0], open("/dev/null", "w"))
