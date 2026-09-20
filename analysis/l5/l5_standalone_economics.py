"""P0-5: L5's standalone trading economics, reconstructed per window.

PROVENANCE AND LABEL. Every row is source=polymarket_public_activity with
resolution_source=gamma_outcome_prices. These are L5's OWN observed public
trades and the venue's OWN settlement outcomes, so the payout arithmetic is
SETTLEMENT-DERIVED, not modelled -- but it is still a RECONSTRUCTION from public
data, not a cash statement from L5's account. Label: SETTLED-RECONSTRUCTED.
It is never called realized.

EXACT DECOMPOSITION, no pairing convention assumed:

  spend   = spend_up + spend_down
  payout  = qty_on_winning_side * 1.00
  NET     = payout - spend                       <- the ground truth per window

  avg_u = spend_up/qty_up,  avg_d = spend_down/qty_down
  matched_q  = min(qty_up, qty_down)
  matched    = matched_q * (1 - avg_u - avg_d)
  residual_q = |qty_up - qty_down| on side S
  residual   = residual_q * ((1 if S wins else 0) - avg_S)

  matched + residual == NET identically (proved algebraically and asserted
  numerically below). So the split is a presentation of the same money, and no
  conclusion depends on the pairing convention -- which is one of the five
  hypotheses the owner asked to test.

FEES: dollars == price*quantity exactly in the corpus, so no fee is embedded and
none is inferred. Polymarket maker fee is zero.

INCENTIVES ARE NOT IN THIS CORPUS AND ARE NEVER INFERRED. Any incentive figure
is an EXTERNAL input and is reported separately, never folded into standalone
trading.
"""
import gzip, csv, collections, datetime

CORPUS = "/home/user/kalshi-arb-bot/analysis/l5/l5_canonical_sequence.csv.gz"


def f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def windows():
    rows = list(csv.DictReader(gzip.open(CORPUS, "rt")))
    w = collections.defaultdict(list)
    for r in rows:
        w[r["window_id"]].append(r)
    out = []
    for k, v in w.items():
        v.sort(key=lambda r: (int(r["trade_seq"]), f(r["timestamp"])))
        last = v[-1]
        qu, qd = f(last["cum_up_qty"]), f(last["cum_down_qty"])
        su, sd = f(last["cum_up_spend"]), f(last["cum_down_spend"])
        win = last["settlement_outcome"]
        if win not in ("Up", "Down") or (qu + qd) <= 0:
            continue
        spend = su + sd
        payout = qu if win == "Up" else qd
        net = payout - spend
        avu = su / qu if qu else 0.0
        avd = sd / qd if qd else 0.0
        mq = min(qu, qd)
        matched = mq * (1.0 - avu - avd) if mq else 0.0
        rq = abs(qu - qd)
        rside = "Up" if qu > qd else "Down"
        ravg = avu if rside == "Up" else avd
        residual = rq * ((1.0 if rside == win else 0.0) - ravg) if rq else 0.0
        assert abs(matched + residual - net) < 1e-6, "decomposition must be exact"
        mk = collections.Counter(r["maker_taker"] for r in v)
        out.append({
            "window_id": k, "slug": last["market_slug"],
            "asset": last["asset"], "dur": last["duration_minutes"],
            "lane": "%s%sm" % (last["asset"], last["duration_minutes"]),
            "t0": f(v[0]["timestamp"]), "t1": f(last["timestamp"]),
            "month": datetime.datetime.utcfromtimestamp(f(v[0]["timestamp"])).strftime("%Y-%m"),
            "day": datetime.datetime.utcfromtimestamp(f(v[0]["timestamp"])).strftime("%Y-%m-%d"),
            "qty_up": qu, "qty_down": qd, "spend": spend, "payout": payout,
            "net": net, "matched_q": mq, "matched": matched,
            "resid_q": rq, "resid_side": rside, "residual": residual,
            "resid_notional": rq * ravg,
            "pair_basis": (avu + avd) if mq else None,
            "win": win, "n_fills": len(v),
            "maker": mk.get("maker", 0), "taker": mk.get("taker", 0),
            "unknown": mk.get("unknown", 0),
            "corpus_pair_cost": f(last["matched_pair_unit_cost"], None)
                                if last["matched_pair_unit_cost"] else None,
        })
    out.sort(key=lambda r: r["t0"])
    return out
