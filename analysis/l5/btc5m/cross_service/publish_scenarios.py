"""Run in the l5-pmus-live code line. Builds each scenario's ledger with the
REAL InventoryStore and prints the account-risk record it would publish,
using the store's own day figure and per-market results."""
import datetime as dt
import json
import sys
import tempfile

sys.path.insert(0, ".")
from src import l5_live_execution as LX          # noqa: E402

NOW = dt.datetime(2026, 11, 20, 17, 8, tzinfo=dt.timezone.utc)
T, Y = "2026-11-20T17:00:00+00:00", "2026-11-19T23:59:00+00:00"
OTHER = ("cpc-btc-updown-15m-2026-11-20-1200z", "-1.00")
A, B = "cpc-btc-updown-5m-2026-11-20-1420z", "cpc-btc-updown-5m-2026-11-20-1705z"

#: name -> (rows this ledger holds for the lane's markets, extra state)
SCEN = {
    "S1 not yet retired here": ({}, {}),
    "S2 booked at the $1 bound (harsher)": ({B: (T, "-0.44")}, {}),
    "S3 booked ZERO, lane lost": ({B: (T, "0")}, {}),
    "S4 restated to the true result": ({A: (T, "-0.0114")}, {}),
    "S5 gain booked here, matched loss unseen": ({B: (T, "0.05")}, {}),
    "S6 one loss booked, one not yet": ({A: (T, "-0.0114")}, {}),
    "S7 booked on the PREVIOUS UTC day": ({B: (Y, "-0.05")}, {}),
    "S8 an undated row (day unknown)": ({B: ("", "-0.05")}, {}),
    "S9 unattributed residual (day unknown)":
        ({B: (T, "-0.05")}, {"realized_unattributed": "-2"}),
    "S10 an unreadable row (day unknown)": ({B: (T, "?")}, {}),
}
out = {}
for name, (rows, extra) in SCEN.items():
    s = LX.InventoryStore(tempfile.mktemp())
    s.state["retired_markets"] = {OTHER[0]: {"t": T,
                                             "settlement_pnl": OTHER[1]}}
    for m, (t, pnl) in rows.items():
        s.state["retired_markets"][m] = {"t": t, "settlement_pnl": pnl}
    s.state.update(extra)
    day = s.realized_current_utc_day(now=NOW.timestamp())
    fn = getattr(s, "retired_results_current_utc_day", None)
    res = None if fn is None else fn(now=NOW.timestamp())
    risk = {"readable": day is not None,
            "reason": None if day is not None else "DAY_UNKNOWN",
            "utc_day": "2026-11-20",
            "utc_day_realized_usd": None if day is None else str(day),
            "daily_halt_usd": "50", "lifetime_realized_usd": "-39.4794",
            "cumulative_loss_ceiling_usd": "75", "operator_halt": False,
            "retired_markets": sorted(s.state["retired_markets"]),
            "portfolio_drawdown": {"state": "OFF", "refusal": None},
            "equity_baseline": {"state": "OFF", "refusal": None}}
    if fn is not None:
        risk["retired_results_utc_day"] = (
            None if res is None else {"utc_day": "2026-11-20",
                                      "results": res})
    out[name] = risk
print(json.dumps({"publishes_results": fn is not None, "scenarios": out}))
