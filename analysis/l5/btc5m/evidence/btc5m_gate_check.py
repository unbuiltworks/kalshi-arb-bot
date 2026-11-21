"""Feed what l5-pmus-live PUBLISHED into the BTC5m account-risk reader as
deployed (a178c20f4), then break the record in each way that must block."""
import copy, datetime as dt, json, subprocess, sys
from decimal import Decimal as D
sys.path.insert(0, ".")
from src import l5_btc5m_runner as RUN
pub = json.load(open(sys.argv[1]))
t = dt.datetime.fromisoformat(pub["hb_utc"].replace("Z", "+00:00"))
NOW = t + dt.timedelta(seconds=3)
day = pub["day_realized"]
base = {"readable": True, "reason": None, "utc_day": day["utc_day"],
        "utc_day_realized_usd": day["utc_day_realized_usd"],
        "daily_halt_usd": day["halt_usd"],
        "lifetime_realized_usd": pub["realized"]["realized_total"],
        "cumulative_loss_ceiling_usd": "75", "operator_halt": True,
        "portfolio_drawdown": pub["portfolio_drawdown"],
        "equity_baseline": pub["equity_baseline"]}
def run(risk, age=3):
    doc = {"as_of_utc": (NOW - dt.timedelta(seconds=age)).isoformat(),
           "account_risk": risk}
    g = RUN.account_risk(url="http://x", now=NOW, own_unaccounted=D(0),
                         fetch=lambda u: doc)
    return g.get("ok"), g.get("reason"), g.get("loss_room_usd")
def mod(f):
    r = copy.deepcopy(base); f(r); return r
cases = [
 ("as published", base, 3),
 ("record 31s old (stale)", base, 31),
 ("drawdown block missing", mod(lambda r: r.pop("portfolio_drawdown")), 3),
 ("baseline block missing", mod(lambda r: r.pop("equity_baseline")), 3),
 ("balance stale at publisher", mod(lambda r: (r["portfolio_drawdown"].update(refusal="PORTFOLIO_VALUE_UNAVAILABLE"), r["equity_baseline"].update(refusal="EQUITY_UNAVAILABLE"))), 3),
 ("transfers not established", mod(lambda r: r["equity_baseline"].update(refusal="EQUITY_TRANSFERS_NOT_ESTABLISHED")), 3),
 ("drawdown limit breached", mod(lambda r: r["portfolio_drawdown"].update(refusal="PORTFOLIO_DRAWDOWN_LIMIT:1.0000>=1.00", room_usd="0.0000")), 3),
 ("baseline breached", mod(lambda r: r["equity_baseline"].update(refusal="EQUITY_DRAWDOWN_LIMIT:1.0000>=1.00", room_usd="0.0000")), 3),
 ("room zero, no refusal text", mod(lambda r: r["equity_baseline"].update(room_usd="0")), 3),
 ("room unreadable", mod(lambda r: r["portfolio_drawdown"].pop("room_usd")), 3),
 ("baseline INVALID", mod(lambda r: r["equity_baseline"].update(state="INVALID", refusal="EQUITY_BASELINE_FIELDS")), 3),
 ("cumulative at -75", mod(lambda r: r.update(lifetime_realized_usd="-75")), 3),
 ("daily at -50", mod(lambda r: r.update(utc_day_realized_usd="-50")), 3),
]
print("commit", subprocess.run(["git","rev-parse","--short","HEAD"],capture_output=True,text=True).stdout.strip())
for name, r, age in cases:
    ok, why, room = run(r, age)
    print("%-28s ok=%-5s room=%-8s %s" % (name, ok, room, why))
