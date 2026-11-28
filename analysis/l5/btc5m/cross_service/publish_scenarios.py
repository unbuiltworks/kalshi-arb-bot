"""Run in the l5-pmus-live code line. Builds each scenario's ledger with the
REAL InventoryStore and prints the account-risk record it would publish,
using the store's own day figure, per-market results and lifetime basis."""
import datetime as dt
import json
import sys
import tempfile

sys.path.insert(0, ".")
from src import l5_live_execution as LX          # noqa: E402

NOW = dt.datetime(2026, 11, 20, 17, 8, tzinfo=dt.timezone.utc)
T = "2026-11-20T17:00:00+00:00"                  # today
Y = "2026-11-19T12:00:00+00:00"                  # an earlier UTC day
Y_LATE = "2026-11-19T23:59:50+00:00"             # just before midnight
T_EARLY = "2026-11-20T00:00:30+00:00"            # just after midnight
#: as in production: the whole anchor is attributed, nothing unattributed
REBASE = {"t": "2026-11-16T00:20:00+00:00", "attributed": "-39.468",
          "unattributed": "0", "cash_delta": "-39.468", "reason": "r",
          "id": "x"}
ANCHOR = {"at_read_utc": "2026-11-16T00:11:52Z"}
OTHER = ("cpc-btc-updown-15m-2026-11-20-1200z", T, "-1.00")
A = "cpc-btc-updown-5m-2026-11-20-1420z"
B = "cpc-btc-updown-5m-2026-11-20-1705z"
P = "cpc-btc-updown-5m-2026-11-19-1200z"          # lane, earlier day
R = "cpc-btc-updown-5m-2026-11-19-2355z"          # lane, at the rollover

#: name -> (lane-market rows this ledger holds, state tweaks)
SCEN = {
    "S1 not yet retired here": ({}, {}),
    "S2 booked at the $1 bound (harsher)": ({B: (T, "-0.44")}, {}),
    "S3 booked ZERO, lane lost": ({B: (T, "0")}, {}),
    "S4 restated to the true result": ({A: (T, "-0.0114")}, {}),
    "S5 gain booked here, matched loss unseen": ({B: (T, "0.05")}, {}),
    "S6 one loss booked, one not yet": ({A: (T, "-0.0114")}, {}),
    "S8 an undated row (day unknown)": ({B: ("", "-0.05")}, {}),
    "S9 unattributed residual off the anchor": ({B: (T, "-0.05")},
                                                {"bump_unattributed": "-2"}),
    "S10 an unreadable row": ({B: (T, "?")}, {}),
    "L1 PRIOR-DAY loss never booked here": ({}, {}),
    "L2 prior-day loss booked at its result": ({P: (Y, "-0.30")}, {}),
    "L3 lane loss from BEFORE the cash anchor": ({}, {}),
    "L4 rollover: lane 23:59, retired here 00:00": ({R: (T_EARLY, "-0.20")},
                                                    {}),
    "L5 rollover: retired here 23:59, lane 00:00": ({R: (Y_LATE, "-0.20")},
                                                    {}),
    "L6 lifetime does not reconcile": ({P: (Y, "-0.30")},
                                       {"bump_attributed": "-0.77"}),
    "L7 re-anchor history missing": ({P: (Y, "-0.30")}, {"drop_log": True}),
    "L8 prior-day loss booked at ZERO": ({P: (Y, "0")}, {}),
}
out = {}
for name, (rows, tweak) in SCEN.items():
    s = LX.InventoryStore(tempfile.mktemp())
    s.state["accounting_rebase_log"] = [dict(REBASE)]
    s.state["cash_anchor"] = dict(ANCHOR)
    s.state["retired_markets"] = {OTHER[0]: {"t": OTHER[1],
                                             "settlement_pnl": OTHER[2]}}
    from decimal import Decimal as D
    att = D("-39.468") + D(OTHER[2])
    for m, (t, pnl) in rows.items():
        s.state["retired_markets"][m] = {"t": t, "settlement_pnl": pnl}
        try:
            att += D(pnl)
        except Exception:
            pass
    att += D(tweak.get("bump_attributed", "0"))
    s.state["realized_attributed"] = str(att)
    s.state["realized_unattributed"] = str(
        D(tweak.get("bump_unattributed", "0")))
    if tweak.get("drop_log"):
        s.state.pop("accounting_rebase_log")
    day = s.realized_current_utc_day(now=NOW.timestamp())
    life = s.realized_today()
    risk = {"readable": day is not None,
            "reason": None if day is not None else "DAY_UNKNOWN",
            "utc_day": "2026-11-20",
            "utc_day_realized_usd": None if day is None else str(day),
            "daily_halt_usd": "50", "lifetime_realized_usd": str(life),
            "cumulative_loss_ceiling_usd": "75", "operator_halt": False,
            "retired_markets": sorted(s.state["retired_markets"]),
            "portfolio_drawdown": {"state": "OFF", "refusal": None},
            "equity_baseline": {"state": "OFF", "refusal": None}}
    patched = hasattr(s, "lifetime_basis")
    if patched:
        res = s.retired_results_current_utc_day(now=NOW.timestamp())
        risk["retired_results_utc_day"] = (
            None if res is None else {"utc_day": "2026-11-20",
                                      "results": res})
        risk["lifetime_basis"] = s.lifetime_basis()
    out[name] = risk
print(json.dumps({"patched": patched, "scenarios": out}))
