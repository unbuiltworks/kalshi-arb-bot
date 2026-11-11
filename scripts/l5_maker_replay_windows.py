import json, collections, datetime as dt
from decimal import Decimal as D
from src import l5_maker_replay as R

LAM = D("0.04518752824220514525")

rows=[json.loads(l) for l in open("analysis/pmus/evidence/observer_rows_all_windows.jsonl") if l.strip()]
def bbo(r): return (r.get("pmus") or {}).get("bbo") or {}
clean=[r for r in rows if not r.get("backlog") and bbo(r).get("bid") is not None
       and bbo(r).get("ask") is not None and r.get("still_in_window_at_receipt")]

# trade tape, for TRADE_FLOW
tape=collections.defaultdict(list)
for l in open("analysis/pmus/evidence/auth_book_tape.jsonl", errors="replace"):
    if "{" not in l: continue
    try: d=json.loads(l[l.index("{"):], strict=False)
    except Exception: continue
    for m, rs in (d.get("markets") or {}).items():
        for x in rs:
            if isinstance(x, dict): tape[m].append(x)

by_window=collections.defaultdict(list)
for r in clean: by_window[r["alignment"]["pmus_slug"]].append(r)

def ts(r):
    return dt.datetime.fromisoformat(r["receipt_utc"]).timestamp()

def run(max_live):
    out=[]
    for slug, rs in sorted(by_window.items()):
        rs=sorted(rs, key=ts)
        w=R.WindowReplay(slug, lam=LAM, cap_usd=D(5), max_live=max_live)
        start=ts(rs[0]); end=start+900.0
        prints=tape.get(slug, [])
        for r in rs:
            t=ts(r)
            l5=r.get("l5") or {}
            side=l5.get("pmus_side"); px=R._d(l5.get("l5_price")); sz=R._d(l5.get("l5_size"))
            b=bbo(r)
            w.observe_book({"UP": R.touch_for("UP", b), "DOWN": R.touch_for("DOWN", b)})
            w.observe_prints([p for p in prints if p.get("t") and float(p["t"])<=t])
            # L3: L5 reversal cancels resting orders on the opposite side
            if side: w.cancel("L5_REVERSAL", t=t, side=("DOWN" if side=="UP" else "UP"))
            klass=(r.get("copyability") or {}).get("klass")
            if klass==R.MAKER_COPYABLE and side and px and sz:
                w.offer(key=r["action_key"], side=side, price=px, size=sz, t=t)
        w.close(end)
        out.append(w.as_dict())
    return out

for ml,label in ((1,"max_live_orders = 1  (deployed)"), (None,"unbounded count, $5 risk cap only")):
    res=run(ml)
    print("="*74); print(label); print("="*74)
    tot=collections.Counter()
    lives=[]
    for d in res:
        for k in ("maker_intents","placed","blocked_one_live","blocked_envelope",
                  "arrived_while_another_rested","book_reached","trade_flow"):
            tot[k]+=d[k]
        tot["peak_simultaneous"]=max(tot["peak_simultaneous"], d["peak_simultaneous"])
        if d["lifetime_p50"] is not None: lives.append(d)
    peak_prem=max((D(d["peak_premium_usd"]) for d in res), default=D(0))
    print(f"  windows                      {len(res)}")
    print(f"  maker intents                {tot['maker_intents']}")
    print(f"  placed                       {tot['placed']}")
    print(f"  PEAK simultaneous orders     {tot['peak_simultaneous']}")
    print(f"  PEAK concurrent premium $    {peak_prem}")
    print(f"  arrived while another rested {tot['arrived_while_another_rested']}")
    print(f"  lost to one-live-order       {tot['blocked_one_live']}")
    print(f"  lost to $5 envelope          {tot['blocked_envelope']}")
    print(f"  BOOK_REACHED                 {tot['book_reached']}")
    print(f"  TRADE_FLOW                   {tot['trade_flow']}")
    ps=[d["lifetime_p50"] for d in res if d["lifetime_p50"] is not None]
    p7=[d["lifetime_p75"] for d in res if d["lifetime_p75"] is not None]
    p9=[d["lifetime_p90"] for d in res if d["lifetime_p90"] is not None]
    med=lambda v: sorted(v)[len(v)//2] if v else None
    print(f"  lifetime p50/p75/p90 (s)     {med(ps)} / {med(p7)} / {med(p9)}   (median across windows)")
    print()
    if ml is None:
        print("  per-window peaks:")
        for d in sorted(res, key=lambda x:-x["peak_simultaneous"])[:8]:
            print(f"    {d['market'][-5:]}  peak {d['peak_simultaneous']:>2}  premium ${D(d['peak_premium_usd']):.4f}  "
                  f"intents {d['maker_intents']:>3}  placed {d['placed']:>3}  env-blocked {d['blocked_envelope']:>3}")
