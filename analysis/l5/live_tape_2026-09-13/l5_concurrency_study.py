"""READ-ONLY: did L5 open an unrelated market B while market A still carried
an unmatched residual and had not yet settled?

SEQUENTIAL, not aggregate: for the FIRST trade of each window B at time T_B,
we ask which OTHER windows A had (a) started strictly before T_B, (b) not yet
settled at T_B, and (c) a nonzero residual as of their last trade at or
before T_B. That residual therefore demonstrably existed BEFORE the
unrelated open.

LIMITATION, stated up front: public activity shows trades, not orders. We
cannot observe "zero resting orders" or "venue-confirmed inventory" for L5.
Two of the four quarantine conditions are therefore UNTESTABLE here; this
measures the other two (unmatched residual + awaiting settlement).
"""
import gzip, csv, collections

SRC = "analysis/l5/l5_canonical_sequence.csv.gz"


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


rows_by_w = collections.OrderedDict()
with gzip.open(SRC, "rt", newline="", encoding="utf-8") as fh:
    for row in csv.DictReader(fh):
        rows_by_w.setdefault(row["window_id"], []).append(row)

W = {}
for wid, rows in rows_by_w.items():
    rows.sort(key=lambda r: (int(r["trade_seq"])))
    t0 = int(rows[0]["timestamp"])
    settle = max(int(r["timestamp"]) + int(f(r["seconds_to_settlement"]))
                 for r in rows)
    W[wid] = {"t0": t0, "settle": settle, "rows": rows,
              "slug": rows[0]["market_slug"]}

opens = sorted(((w["t0"], wid) for wid, w in W.items()))
print(f"windows: {len(W)}   first open {opens[0][0]}   last {opens[-1][0]}")

overlap_counts = []
resid_dollars = []
with_resid = 0
for t_b, wid_b in opens:
    live = []
    for wid_a, a in W.items():
        if wid_a == wid_b:
            continue
        if not (a["t0"] < t_b < a["settle"]):
            continue
        # residual of A as of its last trade at or before t_b
        last = None
        for r in a["rows"]:
            if int(r["timestamp"]) <= t_b:
                last = r
            else:
                break
        if last is None:
            continue
        resid = f(last["residual_qty_signed"])
        if abs(resid) > 1e-9:
            up_sp, dn_sp = f(last["cum_up_spend"]), f(last["cum_down_spend"])
            up_q, dn_q = f(last["cum_up_qty"]), f(last["cum_down_qty"])
            matched = f(last["matched_qty"])
            # unmatched dollars: spend attributable to the residual leg
            if resid > 0 and up_q > 0:
                rd = (up_sp / up_q) * abs(resid)
            elif resid < 0 and dn_q > 0:
                rd = (dn_sp / dn_q) * abs(resid)
            else:
                rd = 0.0
            live.append((wid_a, resid, rd, matched))
    overlap_counts.append(len(live))
    if live:
        with_resid += 1
        resid_dollars.extend(d for _, _, d, _ in live)

n = len(opens)
print(f"\nOPENS OF A NEW WINDOW WHILE >=1 UNRELATED WINDOW HELD AN UNMATCHED,")
print(f"UNSETTLED RESIDUAL ESTABLISHED EARLIER: {with_resid}/{n} = {with_resid/n:.1%}")
oc = sorted(overlap_counts)
def q(a, p): return a[int(p*(len(a)-1))]
print(f"  concurrent residual-bearing markets at the moment of the new open:")
print(f"    p50 {q(oc,.5)}   p75 {q(oc,.75)}   p90 {q(oc,.9)}   max {oc[-1]}")
if resid_dollars:
    rd = sorted(resid_dollars)
    print(f"  residual DOLLARS of those pre-existing positions (n={len(rd)}):")
    print(f"    p25 ${q(rd,.25):.2f}  p50 ${q(rd,.5):.2f}  p75 ${q(rd,.75):.2f} "
          f" p90 ${q(rd,.9):.2f}  max ${rd[-1]:.2f}")
    print(f"    mean ${sum(rd)/len(rd):.2f}")
