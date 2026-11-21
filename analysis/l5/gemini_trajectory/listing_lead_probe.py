"""Read-only: when does Gemini LIST a 5m window, and in what state?

Records, per (symbol,state), the first and last time that pairing was seen,
so "listed but pre-open" is distinguishable from "not listed at all".
No auth, no orders, GET only.
"""
import json, re, time, sys
from datetime import datetime
import httpx

REST = "https://api.gemini.com"
WRE = re.compile(r"^(BTC|ETH)(05|15)M(\d{10})$")
OUT = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad/listing_probe.jsonl"

def exp_epoch(v):
    if v in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None

def main(seconds=660.0, period=3.0):
    seen = {}   # (sym,state) -> [first_t, last_t, expiry_t, event]
    t_end = time.monotonic() + seconds
    polls = 0
    errs = 0
    with httpx.Client(timeout=10.0, headers={"User-Agent": "arb-laneD-readonly/1.0"}) as http:
        while time.monotonic() < t_end:
            t0 = time.time()
            try:
                r = http.get(f"{REST}/v1/prediction-markets/events",
                             params=[("status", "active"), ("category", "Crypto"),
                                     ("limit", "500")])
                polls += 1
                if r.status_code == 200:
                    for row in r.json().get("data", []):
                        tk = str(row.get("ticker") or "").upper()
                        m = WRE.match(tk)
                        if not m or m.group(2) != "05":
                            continue
                        for c in row.get("contracts", []) or []:
                            sym = str(c.get("instrumentSymbol") or "")
                            if not sym.upper().endswith("-UP"):
                                continue
                            st = str(c.get("marketState") or c.get("status") or "").lower()
                            ex = exp_epoch(c.get("expiryDate") or row.get("expiryDate"))
                            k = (sym, st)
                            if k not in seen:
                                seen[k] = [t0, t0, ex, tk]
                            else:
                                seen[k][1] = t0
                else:
                    errs += 1
            except httpx.HTTPError:
                errs += 1
            time.sleep(max(0.0, period - (time.time() - t0)))
    with open(OUT, "w") as fh:
        for (sym, st), (f, l, ex, ev) in sorted(seen.items(), key=lambda kv: kv[1][0]):
            fh.write(json.dumps({
                "symbol": sym, "state": st, "event": ev,
                "first_seen": f, "last_seen": l, "expiry_t": ex,
                # lead = how far BEFORE expiry the symbol was first seen in
                # this state. 330 is the collector's intended subscribe lead.
                "list_lead_s": (round(ex - f, 2) if ex else None),
                "last_lead_s": (round(ex - l, 2) if ex else None),
            }) + "\n")
    print(f"POLLS {polls} ERRS {errs} PAIRS {len(seen)} -> {OUT}", flush=True)

if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 660.0)
