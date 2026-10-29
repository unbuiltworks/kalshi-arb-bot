"""READ-ONLY: does L5 treat a substantially-matched book with a small
residual as a fresh `flat` state?

The clone's state key is state_key(last_price, tte, |residual|) with
IMBALANCE_BUCKETS (1.0,"flat"). Matched inventory, absolute inventory,
spend and residual dollars never enter. This asks what L5 actually did
from exactly that class of states.

PRE-state for trade N = the cum_* columns of trade N-1 in the same window
(the csv's cum_* are POST-trade). Trade 1's pre-state is empty.
"""
import gzip, csv, collections, statistics

SRC = "analysis/l5/l5_canonical_sequence.csv.gz"
FLAT = 1.0          # the clone's flat threshold, L5 share scale


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def windows():
    with gzip.open(SRC, "rt", newline="", encoding="utf-8") as fh:
        cur, wid = [], None
        for row in csv.DictReader(fh):
            if row["window_id"] != wid:
                if cur:
                    yield cur
                cur, wid = [], row["window_id"]
            cur.append(row)
        if cur:
            yield cur


def main():
    hits = []
    tot_trades = 0
    both_sides_live = 0
    for w in windows():
        prev = None
        for row in w:
            tot_trades += 1
            if prev is not None:
                up = f(prev["cum_up_qty"]); dn = f(prev["cum_down_qty"])
                matched = f(prev["matched_qty"])
                resid = f(prev["residual_qty_signed"])
                tte = f(row["seconds_to_settlement"], -1)
                if up > 0 and dn > 0 and matched > 0 and tte > 0:
                    both_sides_live += 1
                    if abs(resid) < FLAT:
                        side_up = row["outcome"] == "Up"
                        resid_side_up = resid > 0
                        if row["side"] == "SELL":
                            act = "sell"
                        elif abs(resid) < 1e-9:
                            act = "buy_on_balanced_book"
                        elif side_up == resid_side_up:
                            act = "same_side_as_residual(extend-like)"
                        else:
                            act = "opposite_side(complete-like)"
                        hits.append({
                            "act": act, "side": row["side"],
                            "outcome": row["outcome"],
                            "px": f(row["price"]), "qty": f(row["quantity"]),
                            "dollars": f(row["dollars"]),
                            "matched": matched, "resid": resid,
                            "spend": f(prev["cum_up_spend"]) + f(prev["cum_down_spend"]),
                            "pair_cost": f(prev["matched_pair_unit_cost"], -1),
                            "tte": tte, "slug": row["market_slug"],
                            "seq": int(row["trade_seq"]),
                        })
            prev = row

    print(f"total trades scanned            : {tot_trades}")
    print(f"both sides nonzero, matched>0, live: {both_sides_live}")
    print(f"  ...of those, |residual| < {FLAT} (our 'flat' bucket): {len(hits)}"
          f"  ({len(hits)/max(1,both_sides_live):.1%})")
    if not hits:
        print("NO OBSERVATIONS -- the state class the clone is in does not "
              "occur in L5's canonical history.")
        return
    print()
    c = collections.Counter(h["act"] for h in hits)
    print("L5's NEXT action from this state class:")
    for k, v in c.most_common():
        print(f"   {v:6}  {v/len(hits):6.1%}  {k}")
    print()
    print("SIDE:", dict(collections.Counter(h["side"] for h in hits)))
    px = sorted(h["px"] for h in hits)
    qty = sorted(h["qty"] for h in hits)
    dol = sorted(h["dollars"] for h in hits)
    def q(a, p):
        return a[int(p*(len(a)-1))]
    print(f"PRICE  p25 {q(px,.25):.4f}  p50 {q(px,.5):.4f}  p75 {q(px,.75):.4f}")
    print(f"QTY    p25 {q(qty,.25):.4f}  p50 {q(qty,.5):.4f}  p75 {q(qty,.75):.4f}")
    print(f"$      p25 {q(dol,.25):.4f}  p50 {q(dol,.5):.4f}  p75 {q(dol,.75):.4f}")
    print()
    def split(name, key, edges):
        print(f"--- by {name}")
        buckets = collections.defaultdict(collections.Counter)
        for h in hits:
            v = key(h)
            lab = next((l for hi, l in edges if v < hi), edges[-1][1])
            buckets[lab][h["act"]] += 1
        for lab in [l for _, l in edges]:
            b = buckets.get(lab)
            if not b:
                continue
            n = sum(b.values())
            top = ", ".join(f"{k.split('(')[0]} {v/n:.0%}" for k, v in b.most_common(3))
            print(f"   {lab:16} n={n:5}  {top}")
    split("matched inventory", lambda h: h["matched"],
          ((1, "<1"), (5, "1-5"), (20, "5-20"), (100, "20-100"), (1e12, ">100")))
    split("total spend $", lambda h: h["spend"],
          ((1, "<$1"), (5, "$1-5"), (20, "$5-20"), (100, "$20-100"), (1e12, ">$100")))
    split("|residual| qty", lambda h: abs(h["resid"]),
          ((0.001, "~0"), (0.1, "<0.1"), (0.5, "0.1-0.5"), (1.0, "0.5-1.0")))
    split("matched pair cost", lambda h: h["pair_cost"],
          ((0, "unknown"), (0.95, "<0.95"), (1.0, "0.95-1.00"), (1e12, ">=1.00")))
    split("seconds to settlement", lambda h: h["tte"],
          ((30, "<30s"), (120, "30-120s"), (300, "2-5m"), (600, "5-10m"), (1e12, ">10m")))
    split("price bucket", lambda h: h["px"],
          ((0.10, "p<10c"), (0.25, "p10-25c"), (0.45, "p25-45c"),
           (0.55, "p45-55c"), (0.75, "p55-75c"), (0.90, "p75-90c"), (1e12, "p>90c")))


main()
