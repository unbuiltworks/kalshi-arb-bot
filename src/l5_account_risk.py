"""ACCOUNT-WIDE risk limits shared by every L5 lane on one venue account.

OWNER 2026-11-18: "Both executors must respect the same account-wide
exposure, daily loss, and lifetime loss controls. A sports-only $15 stop is
insufficient." and "I would not authorize concurrent live trading on the
basis of the current risk architecture."

ONE DEFINITION, TWO LANES. The BTC5m runner already reads the account-risk
document the BTC15m service publishes on its order registry
(`account_risk_line`: UTC-day realized vs the daily halt, all-time realized
vs the cumulative ceiling) and refuses on ACCOUNT_DAILY_LOSS_HALT /
ACCOUNT_CUMULATIVE_LOSS_CEILING. This module is that same rule, written
once so a second lane cannot hold a looser copy:

  * `evaluate` is the BTC5m reader's semantics, condition for condition
    (src/l5_btc5m_runner.py::account_risk on
    integration/l5-completion-policy-20261118): no URL, unreadable, not
    published, undated, stale, says-unreadable, figures unreadable, or
    another UTC day -> REFUSE. Only the caller's OWN losses not yet in the
    published ledger are added (min(0, x)), so combining can only bind
    earlier, never later.
  * `lane_cap` is the BTC5m execution layer's account-exposure split:
    L5_ACCOUNT_MAX_EXPOSURE_USD divided by L5_ACCOUNT_LANES, floored to the
    cent; unset cap or an unparseable lane count -> None -> REFUSE.

The same environment variable names are read so one value set on both
services is one limit. Pure apart from the optional HTTP fetch; nothing here
places, cancels or authorizes anything.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.request
from decimal import ROUND_DOWN, Decimal as D
from typing import Any, Callable, Dict, Mapping, Optional

RISK_URL_ENV = "L5_ACCOUNT_RISK_URL"
ACCOUNT_CAP_ENV = "L5_ACCOUNT_MAX_EXPOSURE_USD"
ACCOUNT_LANES_ENV = "L5_ACCOUNT_LANES"
RISK_MAX_AGE_S = 30.0
MAX_ACCOUNT_LANES = 16
CENT = D("0.01")


def _dec(v: Any) -> Optional[D]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = D(str(v))
    except Exception:                                    # noqa: BLE001
        return None
    return x if x.is_finite() else None


def fetch_json(url: str, timeout: float = 3.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:   # noqa: S310
        return json.loads(r.read().decode("utf-8"))


#: OWNER 2026-11-23: "Enforce account-wide drawdown in sports. The $3 limit
#: needs to apply consistently across every money-capable service." The
#: account ledger publishes its own drawdown figure and limit in the
#: account-risk block (`portfolio_drawdown`); a reader refuses new exposure
#: at the limit exactly as the ledger does.
#:   L5_ACCOUNT_DRAWDOWN_REQUIRED=1  a publisher that gives no drawdown
#:       block, or has the limit off, is refused (unset: such a publisher
#:       is not a refusal -- the earlier behaviour).
#:   L5_ACCOUNT_DRAWDOWN_AT_RISK_BUDGET_USD  THE OWNER'S DECISION, default
#:       0 = strict. The ledger's figure counts cash held for resting
#:       orders and unpaired contracts as already lost. With a budget set,
#:       up to that much of the READER'S OWN declared at-risk money (resting
#:       orders plus unpaired cost) is not counted as drawdown, and its
#:       marked-down value is counted instead. Money lost for good -- a
#:       settled loss -- always counts in full.
DRAWDOWN_REQUIRED_ENV = "L5_ACCOUNT_DRAWDOWN_REQUIRED"
DRAWDOWN_BUDGET_ENV = "L5_ACCOUNT_DRAWDOWN_AT_RISK_BUDGET_USD"


def drawdown(risk: Mapping[str, Any], *, own_at_risk: Any = None,
             own_marks: Any = 0, env: Optional[Mapping[str, str]] = None
             ) -> Dict[str, Any]:
    """The account drawdown verdict from the published account-risk block.
    `refusal` is None to allow. Unreadable figures refuse."""
    env = os.environ if env is None else env
    required = str(env.get(DRAWDOWN_REQUIRED_ENV, "") or "").strip() == "1"
    pd = risk.get("portfolio_drawdown") if isinstance(risk, Mapping) else None
    if not isinstance(pd, Mapping):
        return {"state": "NOT_PUBLISHED", "refusal":
                "ACCOUNT_DRAWDOWN_NOT_PUBLISHED" if required else None}
    state = str(pd.get("state") or "")
    if state == "OFF":
        return {"state": "OFF", "refusal":
                "ACCOUNT_DRAWDOWN_OFF_AT_PUBLISHER" if required else None}
    if state != "ON":
        return {"state": state or "UNKNOWN",
                "refusal": "ACCOUNT_DRAWDOWN_%s" % (state or "UNKNOWN")}
    dd, mx = _dec(pd.get("drawdown_usd")), _dec(pd.get("max_drawdown_usd"))
    if dd is None or mx is None or mx <= 0:
        return {"state": "ON", "refusal": "ACCOUNT_DRAWDOWN_UNREADABLE"}
    raw_b = str(env.get(DRAWDOWN_BUDGET_ENV, "") or "").strip()
    budget = _dec(raw_b) if raw_b else D(0)
    if budget is None or budget < 0:
        return {"state": "ON", "refusal": "ACCOUNT_DRAWDOWN_BUDGET_UNREADABLE"}
    credit, marked = D(0), D(0)
    if budget > 0:
        risk_now = _dec(own_at_risk) if own_at_risk is not None else None
        marks = _dec(own_marks)
        if risk_now is None or risk_now < 0 or marks is None:
            # unknown own exposure: nothing is excused (the strict figure)
            credit, marked = D(0), D(0)
        else:
            credit = min(risk_now, budget)
            marked = max(D(0), -marks)
    used = dd - credit + marked
    out = {"state": "ON", "published_drawdown_usd": str(dd),
           "max_drawdown_usd": str(mx), "at_risk_budget_usd": str(budget),
           "own_at_risk_not_counted_usd": str(credit),
           "own_marked_loss_counted_usd": str(marked),
           "drawdown_usd": str(used), "room_usd": str(mx - used),
           "refusal": None}
    if used >= mx:
        out["refusal"] = "ACCOUNT_DRAWDOWN_LIMIT"
    elif pd.get("refusal") and not str(pd.get("refusal")).startswith(
            "PORTFOLIO_DRAWDOWN_LIMIT"):
        # the publisher refuses for a reason other than the figure itself
        # (value unavailable, limit misconfigured): so does the reader
        out["refusal"] = "ACCOUNT_DRAWDOWN_PUBLISHER_REFUSES"
        out["publisher_refusal"] = str(pd.get("refusal"))[:80]
    return out


def evaluate(doc: Any, *, now: dt.datetime, own_unaccounted: Any = 0,
             own_at_risk: Any = None) -> Dict[str, Any]:
    """The account-wide loss verdict from an already-fetched document."""
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason)

    risk = doc.get("account_risk") if isinstance(doc, Mapping) else None
    if not isinstance(risk, Mapping):
        return no("ACCOUNT_RISK_NOT_PUBLISHED")
    try:
        as_of = dt.datetime.fromisoformat(
            str(doc.get("as_of_utc")).replace("Z", "+00:00"))
        age = (now - as_of).total_seconds()
    except (TypeError, ValueError):
        return no("ACCOUNT_RISK_UNDATED")
    if age > RISK_MAX_AGE_S or age < -5:
        return no("ACCOUNT_RISK_STALE", age_s=round(age, 1))
    if not risk.get("readable"):
        return no("ACCOUNT_RISK_SAYS_UNREADABLE", why=risk.get("reason"))
    day, halt = (_dec(risk.get("utc_day_realized_usd")),
                 _dec(risk.get("daily_halt_usd")))
    life, cum = (_dec(risk.get("lifetime_realized_usd")),
                 _dec(risk.get("cumulative_loss_ceiling_usd")))
    if None in (day, halt, life, cum):
        return no("ACCOUNT_RISK_FIGURES_UNREADABLE")
    if str(risk.get("utc_day")) != now.strftime("%Y-%m-%d"):
        return no("ACCOUNT_RISK_FOR_ANOTHER_DAY", utc_day=risk.get("utc_day"))
    own = _dec(own_unaccounted)
    if own is None:
        return no("OWN_LOSSES_UNREADABLE")
    add = min(D(0), own)
    day_all, life_all = day + add, life + add
    room = min(halt + day_all, cum + life_all)
    out = {"utc_day_realized_usd": str(day_all), "daily_halt_usd": str(halt),
           "lifetime_realized_usd": str(life_all),
           "cumulative_loss_ceiling_usd": str(cum),
           "own_losses_not_yet_in_account_ledger_usd": str(add),
           "loss_room_usd": str(room), "age_s": round(age, 1)}
    if day_all <= -halt:
        return no("ACCOUNT_DAILY_LOSS_HALT", **out)
    if -life_all >= cum:
        return no("ACCOUNT_CUMULATIVE_LOSS_CEILING", **out)
    dd = drawdown(risk, own_at_risk=own_at_risk, own_marks=add)
    out["drawdown"] = dd
    if dd.get("refusal"):
        return no(str(dd["refusal"]), **out)
    return dict(out, ok=True, reason=None)


def account_risk(*, url: str, now: dt.datetime, own_unaccounted: Any = 0,
                 fetch: Optional[Callable[[str], Any]] = None
                 ) -> Dict[str, Any]:
    """Fetch the published document and evaluate it. Fails closed."""
    if not url:
        return {"ok": False, "reason": "ACCOUNT_RISK_URL_NOT_SET",
                "env": RISK_URL_ENV}
    try:
        doc = (fetch or fetch_json)(url)
    except BaseException as exc:                         # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return {"ok": False,
                "reason": "ACCOUNT_RISK_UNREADABLE:%s" % type(exc).__name__}
    return evaluate(doc, now=now, own_unaccounted=own_unaccounted)


def retired_markets(doc: Any) -> set:
    """Markets the publishing ledger has already booked (so a reader does
    not count its own result for them twice)."""
    risk = doc.get("account_risk") if isinstance(doc, Mapping) else None
    if not isinstance(risk, Mapping):
        return set()
    return {str(m) for m in (risk.get("retired_markets") or [])}


def account_cap(env: Optional[Mapping[str, str]] = None) -> Optional[D]:
    env = os.environ if env is None else env
    cap = _dec(env.get(ACCOUNT_CAP_ENV))
    return cap if cap is not None and cap >= 0 else None


def account_lanes(env: Optional[Mapping[str, str]] = None) -> Optional[int]:
    """Unset is one lane; anything that is not 1..16 refuses (None), since
    1 is the most permissive answer and a typo must not grant it."""
    env = os.environ if env is None else env
    raw = str(env.get(ACCOUNT_LANES_ENV, "") or "").strip()
    if not raw:
        return 1
    if not raw.isdigit():
        return None
    n = int(raw)
    return n if 1 <= n <= MAX_ACCOUNT_LANES else None


def lane_cap(env: Optional[Mapping[str, str]] = None) -> Optional[D]:
    """This lane's share of the account exposure ceiling, or None."""
    cap, lanes = account_cap(env), account_lanes(env)
    if cap is None or lanes is None:
        return None
    if lanes == 1:
        return cap
    return (cap / D(lanes)).quantize(CENT, rounding=ROUND_DOWN)
