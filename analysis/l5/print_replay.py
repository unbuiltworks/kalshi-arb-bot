"""Expanded out-of-sample replay over L5's own 556 BTC15m windows.

WHY NOT THE BBO TAPE. The BBO tape holds 19 usable windows at ~19s sampling.
Five traded windows per half is noise, and the owner asked for the sample to be
expanded before any live change is recommended.

THE FILL RULE, and why it is queue-aware from first principles. Every row in
l5_canonical_sequence is a BUY by L5 on outcome Up or Down at an own price q,
labelled maker or taker.

  * L5 TAKER buy at q  => a resting ASK sat at q. Our bid at p >= q would have
    crossed it and filled immediately.
  * L5 MAKER buy at q  => a seller hit a BID at q. Our bid at p > q had STRICT
    PRICE PRIORITY over L5's, so that seller would have traded with us first.
    Price priority dominates queue position, which is what makes this
    conservative without needing a queue model we cannot observe.

So our resting buy on outcome o at own price p fills on a print (o, q, size):
    q <  p                       -> FILL (strict price priority)
    q == p and size >= QUEUE_MULT*clip -> FILL (tie; only if the print is big
                                          enough that we plausibly get reached)
    otherwise                    -> no fill

This is conservative in three further ways, all of which push fills DOWN:
  1. L5's prints are a SUBSET of all market activity, so real liquidity was
     strictly greater than what we can see here.
  2. A tie needs size; a strict improvement is required otherwise.
  3. We never assume more than `clip` contracts per print.

It is optimistic in one way, which must be stated: filling would have taken
liquidity L5 actually got. At clip 1 against a median print of 5 contracts
that is a small perturbation, but it is not zero, and it is not modelled.

NOTHING HERE IS A FILL OR A REALIZED PROFIT. Settlement uses the average cost
basis per side validated against the venue's own 0245z record.
"""
import gzip, csv, collections
from decimal import Decimal as D, ROUND_HALF_EVEN

CSV = "analysis/l5/l5_canonical_sequence.csv.gz"
TICK = D("0.01")
ENTRY_CUT = D(480); COMPLETE_CUT = D(600)
MREB = D("0.0125"); TAKER = D("0.0695")
QUEUE_MULT = D(5)        # a tie needs a print >= 5x our clip

def cents(x): return x.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)
def fee(th, C, p): return cents(th * D(C) * p * (D(1) - p))
def other(s): return "DOWN" if s == "UP" else "UP"

def load():
    by = collections.defaultdict(list)
    meta = {}
    with gzip.open(CSV, "rt") as f:
        for r in csv.DictReader(f):
            if r.get("duration_minutes") != "15": continue
            w = r["window_id"]
            by[w].append(r)
    out = []
    for w, rs in by.items():
        rs.sort(key=lambda r: int(r["trade_seq"]))
        so = (rs[-1].get("settlement_outcome") or "").strip().upper()
        if so not in ("UP", "DOWN"): continue
        day = rs[-1]["datetime_utc"][:10]
        ev = []
        for r in rs:
            try:
                t = D(str(int(float(r["seconds_from_window_start"]))))
                o = "UP" if r["outcome"].upper().startswith("U") else "DOWN"
                ev.append((t, o, D(r["price"]).quantize(D("0.0001")),
                           D(r["quantity"]), r["maker_taker"]))
            except Exception:
                continue
        if len(ev) < 4: continue
        out.append(dict(wid=w, day=day, win=so, ev=ev, slug=rs[-1]["market_slug"]))
    out.sort(key=lambda x: (x["day"], x["ev"][0][0]))
    return out

def sim(w, *, k, clip, depth, band_lo, band_hi, comp_cap,
        simultaneous, replenish, max_subs,
        entry_cut=ENTRY_CUT, complete_cut=COMPLETE_CUT, rounds=None,
        slots=1, paired=False):
    """One window.

    simultaneous  True  = quote BOTH sides whenever inventory permits
                  False = one resting order at a time (today's engine)
    replenish     after a fill, immediately re-quote that side
    """
    qty = {"UP": D(0), "DOWN": D(0)}; cost = {"UP": D(0), "DOWN": D(0)}
    rest = {"UP": None, "DOWN": None}
    ref = {"UP": None, "DOWN": None}
    fees = D(0); rebate = D(0); subs = 0; fills = 0
    ref_ct = {"RESIDUAL_ABOVE_CAP": 0, "NO_BAND": 0, "PAIR_ABOVE_CEILING": 0,
              "PAST_ENTRY_CUT": 0, "PAST_COMPLETE_CUT": 0, "SUB_BUDGET": 0,
              "ONE_ORDER_SLOT": 0}
    opens = 0
    def resid(): return qty["UP"] - qty["DOWN"]
    def avg(s): return (cost[s] / qty[s]) if qty[s] > 0 else None

    for (t, o, q, size, mt) in w["ev"]:
        ref[o] = q
        # ---- 1. does this print fill our resting quote on this outcome?
        p = rest[o]
        if p is not None:
            hit = (q < p) or (q == p and size >= QUEUE_MULT * D(clip))
            if hit:
                qty[o] += D(clip); cost[o] += p * D(clip); fills += 1
                f = fee(MREB, clip, p); fees -= f; rebate += f
                rest[o] = None
                if not replenish:
                    continue
        # ---- 2. (re)quote
        if paired and resid() == 0 and rest["UP"] is None \
                and rest["DOWN"] is None and t <= entry_cut \
                and ref["UP"] is not None and ref["DOWN"] is not None:
            # THE PAIR IS GATED AS A PAIR, exactly as probe_precheck does:
            # neither leg is placed unless the two together clear the ceiling.
            wu = ref["UP"] - depth * TICK
            wd = ref["DOWN"] - depth * TICK
            if (wu + wd) <= comp_cap and band_lo <= wu <= band_hi \
                    and band_lo <= wd <= band_hi \
                    and (rounds is None or opens < rounds) \
                    and subs + 2 <= max_subs and D(k) >= D(clip):
                rest["UP"], rest["DOWN"] = wu, wd
                subs += 2; opens += 1
                continue
            ref_ct["PAIR_NOT_JOINTLY_ECONOMIC"] = ref_ct.get(
                "PAIR_NOT_JOINTLY_ECONOMIC", 0) + 1
        sides = ("UP", "DOWN") if simultaneous else (o,)
        for s in sides:
            if ref[s] is None: continue
            if rest[s] is not None: continue
            # one-order-at-a-time engine: any resting order blocks the other
            live = sum(1 for z in ("UP", "DOWN") if rest[z] is not None)
            if not simultaneous and live >= slots:
                ref_ct["ONE_ORDER_SLOT"] += 1; continue
            want = ref[s] - depth * TICK
            reduces = (resid() > 0 and s == "DOWN") or (resid() < 0 and s == "UP")
            after = resid() + (D(clip) if s == "UP" else -D(clip))
            if abs(after) > D(k):
                ref_ct["RESIDUAL_ABOVE_CAP"] += 1; continue
            if reduces:
                if t > complete_cut: ref_ct["PAST_COMPLETE_CUT"] += 1; continue
                a = avg(other(s))
                if a is not None and a + want > comp_cap:
                    ref_ct["PAIR_ABOVE_CEILING"] += 1; continue
            else:
                if t > entry_cut: ref_ct["PAST_ENTRY_CUT"] += 1; continue
                if rounds is not None and opens >= rounds:
                    ref_ct["ROUNDS_SPENT"] = ref_ct.get("ROUNDS_SPENT", 0) + 1
                    continue
                if not (band_lo <= want <= band_hi):
                    ref_ct["NO_BAND"] += 1; continue
            if subs >= max_subs: ref_ct["SUB_BUDGET"] += 1; continue
            subs += 1; rest[s] = want
            if not reduces: opens += 1

    matched = min(qty["UP"], qty["DOWN"])
    a_up = avg("UP") or D(0); a_dn = avg("DOWN") or D(0)
    m_pnl = matched * (D(1) - a_up - a_dn)
    rq = abs(resid()); rs_ = "UP" if resid() > 0 else "DOWN"
    r_pnl = D(0)
    if rq > 0:
        r_pnl = rq * ((D(1) if rs_ == w["win"] else D(0)) - avg(rs_))
    return dict(day=w["day"], matched=matched, m_pnl=m_pnl, r_qty=rq,
                r_pnl=r_pnl, net=m_pnl + r_pnl, rebate=rebate, fees=fees,
                subs=subs, fills=fills, ref=ref_ct,
                contracts=qty["UP"] + qty["DOWN"],
                pair_cost=((a_up + a_dn) if matched > 0 else None),
                capital=cost["UP"] + cost["DOWN"])
