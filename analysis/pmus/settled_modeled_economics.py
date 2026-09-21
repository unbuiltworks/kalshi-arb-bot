"""P0-1: SHADOW -> SETTLED-MODELED economics for the prospective cohort.

NEVER REALIZED. No order was ever submitted, so no fill is venue-confirmed.
What is venue-confirmed here is ONLY the SETTLEMENT OUTCOME of the market. The
positions are modelled. The correct label is SETTLED-MODELED.

SETTLEMENT IS READ ONLY FROM state == MARKET_STATE_EXPIRED. An OPEN market also
returns settlementPx 0.0000 as a DEFAULT, not a result; using it would silently
score every open market as YES-loses. Open markets are reported as STILL-OPEN
and contribute nothing.

Accounting keeps the lines apart and never sums them into one "profit":
  matched-pair P&L   = (1.00 - pair_basis) * matched_units   [outcome-independent]
  residual P&L       = (settle_value_on_surplus_side - avg_cost) * residual_units
  known fees         = 0.00  (PMUS maker fee is zero)
  rebates            = 0.00  (EXPLICITLY ZERO, never estimated)
Combined is reported as a separate line, clearly labelled, because the owner
asked for it -- it is not a claim that the two lines are the same kind of thing.
"""
import json, os, ssl, urllib.request, collections, statistics as stt, time

_CA = "/root/.ccr/ca-bundle.crt"
CTX = (ssl.create_default_context(cafile=_CA) if os.path.exists(_CA)
       else ssl.create_default_context())


def market(slug, tries=3):
    u = "https://gateway.polymarket.us/v1/markets/%s/bbo" % slug
    for i in range(tries):
        try:
            r = urllib.request.Request(u, headers={"User-Agent": "l5-research/1.0",
                                                   "Accept": "application/json"})
            with urllib.request.urlopen(r, timeout=25, context=CTX) as h:
                return (json.loads(h.read()) or {}).get("marketData") or {}
        except Exception:
            if i == tries - 1:
                return {}
            time.sleep(1.0 * (i + 1))
    return {}


def fnum(v):
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def settlements(slugs, threads=8):
    import threading, queue
    q, out, lock = queue.Queue(), {}, threading.Lock()
    for s in slugs:
        q.put(s)

    def w():
        while True:
            try:
                s = q.get_nowait()
            except queue.Empty:
                return
            m = market(s)
            state = m.get("state")
            px = fnum(m.get("settlementPx"))
            with lock:
                out[s] = {"state": state,
                          "settled": state == "MARKET_STATE_EXPIRED",
                          "yes_settle": px if state == "MARKET_STATE_EXPIRED" else None}
    ts = [threading.Thread(target=w, daemon=True) for _ in range(threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out


def episode_pnl(e, s):
    """SETTLED-MODELED P&L for one episode, or None when not settled."""
    if not s or not s.get("settled") or s.get("yes_settle") is None:
        return None
    yes_settle = s["yes_settle"]
    yn, nn = e["yes_n"], e["no_n"]
    mq = e["matched_qty"]
    basis = float(e["pair_basis"]) if e.get("pair_basis") else None
    matched_pnl = (1.0 - basis) * mq if (basis is not None and mq) else 0.0
    rq = e["residual_qty"] if not e["closed"] else 0
    resid_pnl = 0.0
    surplus = None
    avg = None
    if rq:
        if yn > nn and yn:
            surplus, avg, val = "YES", float(e["yes_cost"]) / yn, yes_settle
        elif nn:
            surplus, avg, val = "NO", float(e["no_cost"]) / nn, 1.0 - yes_settle
        elif yn:
            surplus, avg, val = "YES", float(e["yes_cost"]) / yn, yes_settle
        else:
            val = None
        if surplus:
            resid_pnl = (val - avg) * rq
    cap = float(e["yes_cost"]) + float(e["no_cost"])
    return {"matched_pnl": matched_pnl, "resid_pnl": resid_pnl,
            "combined": matched_pnl + resid_pnl, "fees": 0.0, "rebates": 0.0,
            "capital": cap, "matched_units": mq, "resid_units": rq,
            "surplus": surplus, "resid_avg_cost": avg, "yes_settle": yes_settle}
