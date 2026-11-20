"""A venue-confirmed EQUITY BASELINE and the period measured from it.

WHY (owner direction 2026-11-20). The account-wide loss figure is a ledger
total in which many rows are costed at an assumed $1.00 a contract, and it
was re-anchored once already. It is not reliable enough to be the primary
financial control. The venue's own balance is. So: fix a baseline from a
venue read, measure the account from there, and adjust for money moved in
or out -- while keeping the historical loss record and every existing halt.

WHAT THIS MODULE IS. Pure functions. It reads an owner-written document and
figures handed to it, and returns reports. It writes no state, moves no
figure in the ledger, and is read by no gate in this service. Publishing its
line is the only wiring. The existing portfolio drawdown limit
(`l5_portfolio_drawdown`, owner decision 2026-11-13) and the cumulative loss
ceiling are untouched and still run.

THE THREE THINGS IT ADDS to the existing drawdown limit:

  1. A baseline that is a RECORD, not a bare number: when it was read, the
     buying power, the positions, the open orders, and the historical
     figures it supersedes as a starting point -- which stay on the record.
  2. Deposits and withdrawals DETECTED from the venue's feed instead of
     depending on someone remembering to move an anchor. Their amounts are
     deliberately NOT interpreted: no deposit, transfer or bonus row has
     been observed since this was written, so the sign and meaning of the
     venue's amount field are unverified. Any such row dated after the
     baseline, or a feed that could not be read back to it, is a REFUSAL
     until the owner writes a new baseline. That can stop trading early and
     can never hide a withdrawal or count a deposit as profit.
  3. A period result that counts only rows with a measured cost. Rows still
     at the assumed bound are listed beside it and added to nothing: not a
     confirmed loss, not a confirmed profit.

EQUITY is the conservative one the drawdown limit already uses:
    buying power + min(UP, DOWN) per market
An unmatched contract counts 0, so buying a leg shows as a drawdown until it
pairs or settles. That can only trip early.

    change   = equity_now - baseline_equity - net_transfers_since_baseline
    drawdown = -change           refuse when drawdown >= max_drawdown_usd
"""
from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Mapping, Optional

DOC_ENV = "L5_EQUITY_BASELINE"
OFF = "OFF"

ACKNOWLEDGEMENT = (
    "THIS SETS A STARTING POINT FOR MEASUREMENT. HISTORICAL LOSSES ARE "
    "PRESERVED IN historical AND ARE NOT ERASED. IT DOES NOT RESET, RAISE OR "
    "REPLACE THE CUMULATIVE LOSS CEILING OR ANY OTHER LIMIT.")

FIELDS = frozenset((
    "id", "at_utc", "buying_power_usd", "matched_redemption_usd",
    "equity_usd", "positions", "open_orders", "max_drawdown_usd",
    "historical", "acknowledgement"))
HISTORICAL_FIELDS = frozenset((
    "ledger_realized_usd", "prior_anchor_usd", "prior_drawdown_usd"))

#: cost bases that are a MEASURED cost. Anything else is uncertain.
MEASURED = frozenset(("AUTHENTICATED_FILLS",))
MEASURED_PROVENANCE = frozenset((
    "VENUE_ATTESTED_COST", "MEASURED_FROM_VENUE_FILLS"))


def _dec(v: Any) -> Optional[D]:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = D(str(v).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def _when(v: Any) -> Optional[dt.datetime]:
    try:
        t = dt.datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return t if t.tzinfo is not None else None


def _bad(why: str, **more: Any) -> Dict[str, Any]:
    return dict({"state": "INVALID", "why": why}, **more)


def matched_redemption(positions: Any) -> Optional[D]:
    """Sum of min(UP, DOWN) over markets. None when any figure is unusable."""
    if not isinstance(positions, Mapping):
        return None
    total = D(0)
    for sides in positions.values():
        if not isinstance(sides, Mapping):
            return None
        up, dn = _dec(sides.get("UP", 0)), _dec(sides.get("DOWN", 0))
        if up is None or dn is None or up < 0 or dn < 0:
            return None
        total += min(up, dn)
    return total


def read_document(env: Optional[Mapping[str, str]] = None, *,
                  now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """{"state": OFF} | {"state": "ON", ...} | {"state": "INVALID", "why"}.

    Unset is OFF and changes nothing. Anything set but not exactly right is
    INVALID, which a reader treats as a refusal: a baseline someone tried to
    set must not silently vanish. Nothing is clamped or defaulted.
    """
    e = os.environ if env is None else env
    raw = str(e.get(DOC_ENV) or "").strip()
    if not raw:
        return {"state": OFF}
    try:
        doc = json.loads(raw)
    except ValueError:
        return _bad("NOT_JSON")
    if not isinstance(doc, dict):
        return _bad("NOT_AN_OBJECT")
    if set(doc) != FIELDS:
        return _bad("FIELDS", missing=sorted(FIELDS - set(doc)),
                    unknown=sorted(set(doc) - FIELDS))
    if doc["acknowledgement"] != ACKNOWLEDGEMENT:
        return _bad("ACKNOWLEDGEMENT")
    if not isinstance(doc["id"], str) or len(doc["id"].strip()) < 8:
        return _bad("ID")
    at = _when(doc["at_utc"])
    if at is None:
        return _bad("AT_UTC")
    n = now or dt.datetime.now(dt.timezone.utc)
    if at > n + dt.timedelta(seconds=5):
        return _bad("AT_UTC_IN_THE_FUTURE")
    bp, mr, eq, mx = (_dec(doc["buying_power_usd"]),
                      _dec(doc["matched_redemption_usd"]),
                      _dec(doc["equity_usd"]), _dec(doc["max_drawdown_usd"]))
    if None in (bp, mr, eq, mx):
        return _bad("FIGURES_UNREADABLE")
    if bp < 0 or mr < 0 or eq <= 0 or mx <= 0:
        return _bad("FIGURES_OUT_OF_RANGE")
    got = matched_redemption(doc["positions"])
    if got is None:
        return _bad("POSITIONS_UNREADABLE")
    if got != mr:
        return _bad("MATCHED_REDEMPTION_NE_POSITIONS",
                    stated=str(mr), from_positions=str(got))
    if bp + mr != eq:
        return _bad("EQUITY_NE_BUYING_POWER_PLUS_MATCHED",
                    stated=str(eq), computed=str(bp + mr))
    oo = doc["open_orders"]
    if isinstance(oo, bool) or not isinstance(oo, int) or oo < 0:
        return _bad("OPEN_ORDERS")
    if oo != 0:
        #: funds held against a resting order are not in buying power and
        #: not in equity, so a baseline read with orders resting understates
        #: the account and hands back headroom when they cancel.
        return _bad("BASELINE_READ_WITH_ORDERS_RESTING", open_orders=oo)
    h = doc["historical"]
    if not isinstance(h, dict) or set(h) != HISTORICAL_FIELDS:
        return _bad("HISTORICAL_FIELDS")
    hv = {k: _dec(h[k]) for k in HISTORICAL_FIELDS}
    if None in hv.values():
        return _bad("HISTORICAL_UNREADABLE")
    return {"state": "ON", "id": doc["id"].strip(), "at_utc": at,
            "buying_power_usd": bp, "matched_redemption_usd": mr,
            "equity_usd": eq, "max_drawdown_usd": mx,
            "positions": {str(m): {"UP": str(_dec(s.get("UP", 0))),
                                   "DOWN": str(_dec(s.get("DOWN", 0)))}
                          for m, s in doc["positions"].items()},
            "historical": {k: str(v) for k, v in hv.items()}}


def net_transfers(seen: Any, since: dt.datetime) -> Dict[str, Any]:
    """Whether any money moved in or out since the baseline.

    `seen` is {"complete_back_to": <iso or None>, "rows": [{"type", "time"}]}
    -- the non-trading rows a feed read passed over and how far back that
    read is complete. Returns {"net_usd": D(0)} only when the read reaches
    the baseline and holds no such row after it; otherwise net_usd is None
    with the reason, which `refusal` turns into a refusal.
    """
    if not isinstance(seen, Mapping):
        return {"net_usd": None, "why": "FEED_NOT_READ"}
    back = _when(seen.get("complete_back_to"))
    if back is None or back > since:
        return {"net_usd": None, "why": "FEED_NOT_READ_BACK_TO_BASELINE",
                "complete_back_to": seen.get("complete_back_to")}
    rows = seen.get("rows")
    if not isinstance(rows, (list, tuple)):
        return {"net_usd": None, "why": "FEED_ROWS_UNREADABLE"}
    after, undated = [], 0
    for r in rows:
        if not isinstance(r, Mapping):
            return {"net_usd": None, "why": "FEED_ROWS_UNREADABLE"}
        t = _when(r.get("time"))
        if t is None:
            undated += 1
        elif t >= since:
            after.append({"type": str(r.get("type")),
                          "time": t.isoformat()})
    if undated:
        return {"net_usd": None, "why": "UNDATED_TRANSFER_ROW",
                "undated": undated}
    if after:
        return {"net_usd": None, "why": "MONEY_MOVED_SINCE_BASELINE",
                "rows": after[:20], "count": len(after)}
    return {"net_usd": D(0), "why": None, "rows_before_baseline": len(rows)}


def refusal(doc: Mapping[str, Any], equity_now: Optional[D],
            net_transfers: Optional[D]) -> Optional[str]:
    """None to allow, else the reason. OFF allows: it is not configured."""
    st = doc.get("state")
    if st == OFF:
        return None
    if st != "ON":
        return "EQUITY_BASELINE_%s" % doc.get("why", "INVALID")
    if equity_now is None:
        return "EQUITY_UNAVAILABLE"
    if net_transfers is None:
        return "EQUITY_TRANSFERS_NOT_ESTABLISHED"
    dd = doc["equity_usd"] + net_transfers - equity_now
    if dd >= doc["max_drawdown_usd"]:
        return "EQUITY_DRAWDOWN_LIMIT:%s>=%s" % (dd, doc["max_drawdown_usd"])
    return None


def line(doc: Mapping[str, Any], equity_now: Optional[D],
         net_transfers: Optional[D], *,
         transfers_detail: Any = None) -> Dict[str, Any]:
    """The published block. Reporting; `refusal` is the verdict in it."""
    out: Dict[str, Any] = {"state": doc.get("state")}
    if doc.get("state") == "INVALID":
        out["why"] = doc.get("why")
    if doc.get("state") == "ON":
        out.update(
            baseline_id=doc["id"], baseline_at_utc=doc["at_utc"].isoformat(),
            baseline_equity_usd=str(doc["equity_usd"]),
            max_drawdown_usd=str(doc["max_drawdown_usd"]),
            historical=dict(doc["historical"]),
            equity_now_usd=None if equity_now is None else str(equity_now),
            net_transfers_usd=(None if net_transfers is None
                               else str(net_transfers)),
            transfers=transfers_detail)
        if equity_now is not None and net_transfers is not None:
            ch = equity_now - doc["equity_usd"] - net_transfers
            #: A GAIN ADDS NO ROOM. Room is what may still be lost from the
            #: baseline, never more than the allowance: a rise in equity --
            #: which is also what a not-yet-seen deposit looks like -- must
            #: not become permission to lose more.
            out.update(period_change_usd=str(ch), drawdown_usd=str(-ch),
                       room_usd=str(doc["max_drawdown_usd"]
                                    + min(D(0), ch)))
    out["refusal"] = refusal(doc, equity_now, net_transfers)
    out["labels"] = (
        "equity = buying power + min(UP,DOWN) per market; an unmatched "
        "contract counts 0. period_change is equity now, less the baseline, "
        "less deposits net of withdrawals since it. `historical` is the "
        "record the baseline starts after; it is preserved, not erased, and "
        "the cumulative loss ceiling is a separate limit this does not "
        "touch.")
    return out


def period_realized(retired: Any, since: dt.datetime) -> Dict[str, Any]:
    """Settled results since the baseline, MEASURED and UNCERTAIN apart.

    A row counts as measured only when its cost came from fills. A row still
    at the assumed $1.00 a contract is counted, its booked figure is shown as
    a bound, and it is added to NOTHING: an uncertain row is neither a
    confirmed loss nor a confirmed profit. Reads; writes nothing.
    """
    conf, bound = D(0), D(0)
    n_conf = n_unc = n_bad = 0
    uncertain = []
    for m, row in (retired.items() if isinstance(retired, Mapping) else ()):
        if not isinstance(row, Mapping):
            continue
        t = _when(row.get("t"))
        if t is None:
            n_bad += 1
            continue
        if t < since:
            continue
        pnl = _dec(row.get("settlement_pnl"))
        if pnl is None:
            n_bad += 1
            continue
        if (row.get("origin_cost_basis") in MEASURED
                or row.get("pnl_provenance") in MEASURED_PROVENANCE):
            conf += pnl
            n_conf += 1
        else:
            bound += pnl
            n_unc += 1
            if len(uncertain) < 50:
                uncertain.append(str(m))
    return {"since_utc": since.isoformat(),
            "measured_realized_usd": str(conf), "measured_rows": n_conf,
            "uncertain_rows": n_unc,
            "uncertain_booked_bound_usd": str(bound),
            "uncertain_markets": uncertain,
            "unreadable_rows": n_bad,
            "complete": n_bad == 0,
            "labels": ("measured_realized counts only rows whose cost came "
                       "from fills. uncertain_booked_bound is what the "
                       "ledger booked for rows it could not cost; it is a "
                       "bound, it is NOT in measured_realized, and it is "
                       "neither a confirmed loss nor a confirmed profit.")}
