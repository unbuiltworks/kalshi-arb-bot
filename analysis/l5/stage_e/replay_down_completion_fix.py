"""Before/after the unit fix, on REAL recorded PMUS books.

No synthetic fills. Every book is a recorded `L5_SHADOW_RESIDUAL_BOOK_SAMPLE`
frame off the authenticated feed, carrying full rungs, the held side, the
unmatched quantity and `first_leg_own_cost` -- the modelled inventory the
maker-only arm actually held. The only thing varied is the one line.
"""
from __future__ import annotations
import sys; sys.path.insert(0, ".")
import collections, glob, json
from decimal import Decimal as D
from src import l5_book_completion as BC

TOOLS = ("/root/.claude/projects/-home-user-kalshi-arb-bot/"
         "1ec9ca12-5dab-5253-8ef5-126eaccc8b25/tool-results/"
         "mcp-Railway-get-logs-*.txt")

def samples():
    seen = set()
    for p in sorted(glob.glob(TOOLS)):
        try: doc = json.load(open(p))
        except Exception: continue
        for e in doc.get("deploy") or ():
            m = e.get("message") or ""
            if "L5_SHADOW_RESIDUAL_BOOK_SAMPLE" not in m: continue
            i = m.find("{")
            try: d = json.loads(m[i:])
            except Exception: continue
            k = (e.get("timestamp"), d.get("slug"), str(d.get("residual")))
            if k in seen: continue
            seen.add(k)
            row = ((d.get("book") or {}).get("row") or {})
            bids, asks = row.get("bids") or [], row.get("asks") or []
            if not bids or not asks: continue
            try:
                held = str(d["held_side"]).upper()
                q = abs(D(str(d["residual"])))
                basis = D(str((d.get("reachability") or {})
                              .get("pair_if_maker", {})
                              .get("first_leg_own_px")))
            except Exception: continue
            if q <= 0 or held not in ("UP", "DOWN"): continue
            yield {"held": held, "q": q, "basis": basis,
                   "bid": D(str(bids[0][0])), "ask": D(str(asks[0][0])),
                   "bs": D(str(bids[0][1])), "as_": D(str(asks[0][1])),
                   "slug": str(d.get("slug") or "").split("|")[0]}

def run(buggy: bool):
    """`buggy=True` reproduces the double conversion, for the comparison."""
    orig = BC._would_cross
    out = collections.Counter(); ok_rows = []
    def patched(side, own_px, bid, ask):
        return orig(side, own_px if side == "UP" else (D(1) - own_px),
                    bid, ask)
    if buggy:
        BC._would_cross = patched
    try:
        for s in S:
            need = "DOWN" if s["held"] == "UP" else "UP"
            r = BC.decide(
                market=s["slug"], held_side=s["held"], held_qty=s["q"],
                held_cost_usd=s["q"] * s["basis"],
                book={"bid": s["bid"], "ask": s["ask"], "stream_age_s": 0.1,
                      "bid_shares": s["bs"], "ask_shares": s["as_"]},
                now_t=1000.0, window_start=400.0, tick="0.01",
                min_qty="0.01", depth_ticks=0,
                residual_budget_usd=D("1.50"))
            key = "%s|%s" % (need, "OK" if r.get("ok") else
                             str(r.get("reason"))[:34])
            out[key] += 1
            if r.get("ok"):
                ok_rows.append((need, r))
    finally:
        BC._would_cross = orig
    return out, ok_rows

S = list(samples())
print("recorded book frames with modelled unmatched inventory: %d" % len(S))
print("  held UP (needs DOWN): %d | held DOWN (needs UP): %d\n"
      % (sum(1 for s in S if s["held"] == "UP"),
         sum(1 for s in S if s["held"] == "DOWN")))

for lbl, buggy in (("BEFORE (double conversion)", True),
                   ("AFTER  (fixed)", False)):
    cnt, oks = run(buggy)
    dn_ok = sum(v for k, v in cnt.items() if k.startswith("DOWN|OK"))
    up_ok = sum(v for k, v in cnt.items() if k.startswith("UP|OK"))
    print("=== %s ===" % lbl)
    print("   DOWN completions accepted: %4d" % dn_ok)
    print("   UP   completions accepted: %4d   <- must not regress" % up_ok)
    for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])[:7]:
        print("      %-44s %4d" % (k, v))
    print()
