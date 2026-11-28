"""Campaign economics: trading results joined with venue reward rows (pure).

Owner 2026-11-26: report, per market, by sport and per campaign, what the
bot actually made, with rewards attributed to the market that earned them
and NEVER netted in before the venue pays them. Read-only, logging only.

Inputs (both already read by the sports bot's hourly audit):

  pilot   the pilot meter's result (src/pmus_pilot_pnl.meter): per market,
          venue-reconciled fills, contracts, spend, completed pairs, pair
          edge, residual, settled net and status. Its markets are the
          campaign's (orders placed at/after the campaign marker).
  rows    the venue's /v1/incentives/earnings rows: marketSlug, date,
          reward (or amount), status.

Labels (strict; never mixed):

  settled_trading_usd     net of markets with a venue resolution and final
                          price ("settled" status only): REALIZED
  realized_cash_basis_usd the meter's cash-basis figure (completed pairs
                          are paid at pairing + settled residuals); shown
                          next to it, not used in `net`
  rewards_paid_usd        rows the venue marks paid: REALIZED INCOME,
                          reported separately from trading
  rewards_pending_usd     PENDING rows: NOT realized
  rewards_skipped_usd     SKIPPED rows: not money (the venue will not pay)
  rewards_other_usd       any other status: NOT realized until understood
  net_realized_usd        settled_trading_usd + rewards_paid_usd ONLY

Campaign scoping. A reward row belongs to the campaign when its date (UTC
day) is on or after the campaign start's day and, with a window end, not
after the end's day. Undated rows and rows outside the window are counted
but not attributed. Rows on markets the bot never held are counted as
`other_markets` (shared account), never attributed.

Flags per market: `reward_without_fills` (a reward row in scope on a
market with no confirmed campaign fills: attributed, flagged),
`fills_unconfirmed` / `position_unconfirmed` (excluded from every sum, as
in the meter), `pending_only` (reward rows in scope, none paid).

Size is bounded: at most `max_markets` markets (largest absolute
trading + reward first) and `max_dates` dates are listed, and markets are
dropped further until the result fits `max_chars` of JSON; the rest are
summed in the totals and counted as omitted.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, Iterable, Mapping, Optional

from src.l5_realized_bridge import CREDITED_REWARD_STATUSES as PAID_STATUSES
UNCONFIRMED = ("fills_unconfirmed", "position_unconfirmed")
_BUCKETS = ("paid", "pending", "skipped", "other")
LABEL = ("settled_trading_usd = venue-resolved markets only (REALIZED); "
         "rewards_paid_usd = venue-paid reward rows (REALIZED INCOME, "
         "separate); rewards_pending/skipped/other_usd are NOT realized; "
         "net_realized_usd = settled_trading_usd + rewards_paid_usd only")


def _d(v: Any) -> Optional[D]:
    if isinstance(v, Mapping):
        v = v.get("value") if v.get("value") is not None else v.get("amount")
    if v is None or v == "":
        return None
    try:
        return D(str(v))
    except Exception:                                    # noqa: BLE001
        return None


def sport_of(slug: str) -> str:
    parts = str(slug).split("-")
    return parts[1] if len(parts) > 1 else "?"


def bucket(status: Any) -> str:
    st = str(status or "").upper()
    if st in PAID_STATUSES:
        return "paid"
    if st == "PENDING":
        return "pending"
    if st == "SKIPPED":
        return "skipped"
    return "other"


def _day(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), timezone.utc
                                  ).strftime("%Y-%m-%d")


def _row_day(row: Mapping[str, Any]) -> Optional[str]:
    for k in ("date", "day", "periodDate", "earnedDate"):
        v = row.get(k)
        if v:
            s = str(v)[:10]
            if len(s) == 10 and s[4] == "-" and s[7] == "-":
                return s
    return None


def _zero_rw() -> Dict[str, D]:
    return {k: D(0) for k in _BUCKETS}


def _s(v: D) -> str:
    return str(v)


def economics(pilot: Optional[Mapping[str, Any]],
              rows: Iterable[Mapping[str, Any]],
              *, since: Optional[float], until: Optional[float] = None,
              our_markets: Iterable[str] = (),
              campaign_id: Optional[str] = None,
              rows_total: Optional[int] = None,
              max_markets: int = 25, max_dates: int = 14,
              max_chars: int = 16000) -> Dict[str, Any]:
    by_market_t: Mapping[str, Mapping[str, Any]] = \
        (pilot or {}).get("by_market") or {}
    ours = set(our_markets) | set(by_market_t)
    d0, d1 = _day(since), _day(until)
    rw: Dict[str, Dict[str, D]] = {}
    rw_dates: Dict[str, set] = {}
    by_date: Dict[str, Dict[str, D]] = {}
    skipped_rows = {"out_of_window": 0, "undated": 0, "other_markets": 0}
    other_mk = _zero_rw()
    seen = 0
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        seen += 1
        slug = str(r.get("marketSlug") or "")
        amt = _d(r.get("reward"))
        if amt is None:
            amt = _d(r.get("amount")) or D(0)
        b = bucket(r.get("status"))
        if slug not in ours:
            skipped_rows["other_markets"] += 1
            other_mk[b] += amt
            continue
        day = _row_day(r)
        if day is None:
            skipped_rows["undated"] += 1
            continue
        if (d0 is not None and day < d0) or (d1 is not None and day > d1):
            skipped_rows["out_of_window"] += 1
            continue
        rw.setdefault(slug, _zero_rw())[b] += amt
        rw_dates.setdefault(slug, set()).add(day)
        by_date.setdefault(day, _zero_rw())[b] += amt

    per: Dict[str, Dict[str, Any]] = {}
    sports: Dict[str, Dict[str, Any]] = {}
    tot = {"markets": 0, "fills": 0, "contracts": D(0), "spend": D(0),
           "pairs": D(0), "pair_edge": D(0), "residual_contracts": D(0),
           "residual_cost": D(0), "settled_trading": D(0),
           "realized_cash_basis": D(0), "fees_charged": D(0),
           **{"rewards_" + k: D(0) for k in _BUCKETS}}
    flagged: Dict[str, list] = {"reward_without_fills": [],
                                "unconfirmed": [], "pending_only": []}
    for slug in sorted(set(by_market_t) | set(rw)):
        t = by_market_t.get(slug)
        st = (t or {}).get("status") or "no_campaign_fills"
        confirmed = t is not None and st not in UNCONFIRMED
        has_fills = confirmed and st != "no_fills"
        r_ = rw.get(slug) or _zero_rw()
        flags = []
        if st in UNCONFIRMED:
            flags.append(st)
            flagged["unconfirmed"].append(slug)
        if slug in rw and not has_fills:
            flags.append("reward_without_fills")
            flagged["reward_without_fills"].append(slug)
        if slug in rw and r_["paid"] == 0 and \
                (r_["pending"] > 0 or r_["other"] > 0):
            flags.append("pending_only")
            flagged["pending_only"].append(slug)
        settled = D(str(t["net"])) if confirmed and st == "settled" \
            else D(0)
        cash = D(str(t["realized_net"])) if confirmed and \
            t.get("realized_net") is not None else D(0)
        m: Dict[str, Any] = {"sport": sport_of(slug), "status": st}
        if confirmed:
            m.update(fills=int(t.get("fills") or 0),
                     contracts=_s(D(str(t["yes"])) + D(str(t["no"]))),
                     spend_usd=str(t["cost"]),
                     completed_pairs=str(t.get("completed_pairs") or "0"),
                     pair_cost=t.get("pair_cost"),
                     pair_edge_usd=t.get("pair_edge"),
                     residual_side=t.get("residual_side"),
                     residual_contracts=str(t.get("residual_qty") or "0"),
                     residual_cost_usd=str(t.get("residual_cost") or "0"),
                     residual_result_usd=t.get("residual"),
                     settled_trading_usd=_s(settled) if st == "settled"
                     else None,
                     realized_cash_basis_usd=t.get("realized_net"),
                     fees_charged_usd=t.get("fees_charged"))
        m.update({"rewards_%s_usd" % k: _s(r_[k]) for k in _BUCKETS})
        m["reward_dates"] = sorted(rw_dates.get(slug, ()))[-max_dates:]
        m["net_realized_usd"] = _s(settled + r_["paid"])
        if flags:
            m["flags"] = flags
        per[slug] = m
        sp = sports.setdefault(m["sport"], {
            "markets": 0, "fills": 0, "contracts": D(0), "spend": D(0),
            "pair_edge": D(0), "settled_trading": D(0),
            **{"rewards_" + k: D(0) for k in _BUCKETS}})
        sp["markets"] += 1
        tot["markets"] += 1
        for k in _BUCKETS:
            sp["rewards_" + k] += r_[k]
            tot["rewards_" + k] += r_[k]
        if not confirmed:
            continue
        c = D(str(t["yes"])) + D(str(t["no"]))
        pe = D(str(t.get("pair_edge") or 0))
        sp["fills"] += int(t.get("fills") or 0)
        sp["contracts"] += c
        sp["spend"] += D(str(t["cost"]))
        sp["pair_edge"] += pe
        sp["settled_trading"] += settled
        tot["fills"] += int(t.get("fills") or 0)
        tot["contracts"] += c
        tot["spend"] += D(str(t["cost"]))
        tot["pairs"] += D(str(t.get("completed_pairs") or 0))
        tot["pair_edge"] += pe
        tot["residual_contracts"] += D(str(t.get("residual_qty") or 0))
        tot["residual_cost"] += D(str(t.get("residual_cost") or 0))
        tot["settled_trading"] += settled
        tot["realized_cash_basis"] += cash
        tot["fees_charged"] += D(str(t.get("fees_charged") or 0))

    def out_sp(v: Mapping[str, Any]) -> Dict[str, Any]:
        o = {"markets": v["markets"], "fills": v["fills"],
             "contracts": _s(v["contracts"]), "spend_usd": _s(v["spend"]),
             "pair_edge_usd": _s(v["pair_edge"]),
             "settled_trading_usd": _s(v["settled_trading"])}
        o.update({"rewards_%s_usd" % k: _s(v["rewards_" + k])
                  for k in _BUCKETS})
        o["net_realized_usd"] = _s(v["settled_trading"] + v["rewards_paid"])
        return o

    def weight(item: Any) -> D:
        m = item[1]
        return abs(D(m.get("settled_trading_usd") or 0)) + \
            abs(D(m.get("realized_cash_basis_usd") or 0)) + \
            sum(D(m["rewards_%s_usd" % k]) for k in _BUCKETS)

    listed = sorted(per.items(), key=lambda it: (-weight(it), it[0]))
    dates = sorted(by_date)[-max_dates:]
    campaign = {
        "campaign_id": campaign_id,
        "since": None if since is None else datetime.fromtimestamp(
            float(since), timezone.utc).isoformat(),
        "until": None if until is None else datetime.fromtimestamp(
            float(until), timezone.utc).isoformat(),
        "markets": tot["markets"], "fills": tot["fills"],
        "contracts": _s(tot["contracts"]), "spend_usd": _s(tot["spend"]),
        "completed_pairs": _s(tot["pairs"]),
        "pair_edge_usd": _s(tot["pair_edge"]),
        "residual_contracts": _s(tot["residual_contracts"]),
        "residual_cost_usd": _s(tot["residual_cost"]),
        "fees_charged_usd": _s(tot["fees_charged"]),
        "settled_trading_usd": _s(tot["settled_trading"]),
        "realized_cash_basis_usd": _s(tot["realized_cash_basis"]),
        **{"rewards_%s_usd" % k: _s(tot["rewards_" + k]) for k in _BUCKETS},
        "net_realized_usd": _s(tot["settled_trading"] + tot["rewards_paid"]),
    }
    out = {
        "trading_available": pilot is not None,
        "campaign": campaign,
        "by_sport": {k: out_sp(v) for k, v in sorted(sports.items())},
        "by_date": {k: {"rewards_%s_usd" % b: _s(by_date[k][b])
                        for b in _BUCKETS} for k in dates},
        "dates_omitted": max(0, len(by_date) - len(dates)),
        "by_market": dict(listed[:max(0, max_markets)]),
        "markets_omitted": max(0, len(listed) - max(0, max_markets)),
        "flags": {k: v[:20] for k, v in flagged.items()},
        "reward_rows": {
            "seen": seen, "total_reported": rows_total,
            "truncated": rows_total is not None and rows_total > seen,
            "not_attributed": skipped_rows,
            "other_markets": {"rewards_%s_usd" % k: _s(other_mk[k])
                              for k in _BUCKETS}},
        "label": LABEL,
    }
    # hard bound on the line: drop the smallest listed markets first
    keep = list(out["by_market"].items())
    while keep and len(json.dumps(out, default=str)) > max_chars:
        keep = keep[:len(keep) // 2]
        out["by_market"] = dict(keep)
        out["markets_omitted"] = len(listed) - len(keep)
    return out
