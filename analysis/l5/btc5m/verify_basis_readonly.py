"""READ-ONLY production check, run INSIDE the l5-btc5m-live container.

It uses the service's own code: the fleet's path resolution, the
executor's ledger loader and history, and the runner's account-risk read
(which performs the lifetime-basis and ledger-completeness checks exactly
as arming would). It opens the ledger read-only through the executor with
no transport, makes one GET to the account service's registry, places and
cancels nothing, and writes nothing.

Run (Railway CLI, in the l5-btc5m-live service shell, from /app or
wherever `src` is importable):

    python3 - < verify_basis_readonly.py        # or paste the file

Prints one JSON object. PASS needs: verdict.ok true, basis_used true,
basis_refusal null, own_series_markets_not_in_own_history == [].
"""
import datetime as dt
import json
import os
import sys

os.environ.setdefault("L5_BTC5M_CONTROLLER", "ON")
sys.path.insert(0, os.getcwd())
from src import l5_5m_fleet as FLEET          # noqa: E402
from src import l5_btc5m_execution as X       # noqa: E402
from src import l5_btc5m_runner as RUN        # noqa: E402

root = os.environ.get(FLEET.STATE_ROOT_ENV, "").strip() or FLEET.DEFAULT_ROOT
env = FLEET.child_env(X.STRATEGY, root, [X.STRATEGY])
ledger = env["L5_BTC5M_LEDGER"]
out = {"strategy": X.STRATEGY, "ledger_path": ledger,
       "ledger_exists": os.path.exists(ledger),
       "ledger_bytes": os.path.getsize(ledger) if os.path.exists(ledger)
       else None}
#: os.stat only; then the executor's own loader. `_save` is never called:
#: the constructor only reads (it would mark NEW orders uncertain and save
#: if any were found mid-submit; the service is disarmed and idle).
before = os.stat(ledger).st_mtime_ns if out["ledger_exists"] else None
ex = X.Btc5mExecutor(None, ledger)
hist = ex.settled_history()
now = dt.datetime.now(dt.timezone.utc)
by = ex.day_settled_by_market(now)
out["ledger_readable"] = hist is not None
out["own_settled_markets"] = (None if hist is None else len(hist))
out["own_settled_first_last"] = (None if not hist else
                                 [min(hist), max(hist)])
own = RUN.OwnSettled.of(by or {}, hist) if hist is not None else None
url = os.environ.get(RUN.RISK_URL_ENV) or ""
out["risk_url_set"] = bool(url)
if own is not None:
    v = RUN.account_risk(url=url, now=now, own_unaccounted=own)
    keep = ("ok", "reason", "utc_day_realized_usd", "lifetime_realized_usd",
            "daily_halt_usd", "cumulative_loss_ceiling_usd", "loss_room_usd",
            "btc5m_losses_not_yet_in_account_ledger_usd",
            "btc5m_lifetime_losses_added_usd", "btc5m_lifetime_basis",
            "portfolio_drawdown_state", "equity_baseline_state", "age_s")
    out["verdict"] = {k: v.get(k) for k in keep if k in v}
out["ledger_unchanged_by_this_check"] = (
    before is None or os.stat(ledger).st_mtime_ns == before)
out["utc"] = now.isoformat()
print(json.dumps(out, indent=1, default=str, sort_keys=True))
