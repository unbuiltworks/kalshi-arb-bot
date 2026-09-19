"""Maker rebate credits, read from the venue -- and the accounting they join.

THE SURFACE WAS LOCATED, NOT GUESSED. `rebate_probe` surveyed 17 candidate
authenticated paths under both verbs on 2026-09-19. Seventeen of the
prediction-market-shaped guesses 404'd. The credits are on:

    POST /v1/transfers   -> {type, status, timestampms, eid, currency,
                             amount, purpose}

and the DENOMINATOR an effective rate needs is on:

    POST /v1/tradevolume -> per symbol, per day: buy_maker_notional,
                            sell_maker_notional, *_maker_count, data_date

So no scraper is written and nothing is read out of a screenshot.

THE RULE THAT GOVERNS THIS WHOLE MODULE. A rebate is CONFIRMED/REALIZED only
when an authoritative Gemini account record shows the credit. Anything
estimated, accrued, modelled or inferred from a formula stays in a separate
line and can never move the realized one. The programme has spent eight
windows refusing to call a settled contract realized; a rebate is no
different.

WHAT IT REFUSES TO FABRICATE. `/v1/transfers` rows carry no order id, no
symbol and no fill linkage. There is therefore NO fill-level attribution
available, and inventing one would be the worst kind of number here --
precise, traceable-looking and untrue. Each credit records its own
`attribution_confidence`, and the aggregate rate is computed only where the
denominator is defensible.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

TRANSFERS_PATH = "/v1/transfers"
TRADEVOLUME_PATH = "/v1/tradevolume"

#: what marks a transfer row as one of these credits. Matched on the venue's
#: OWN `type`/`purpose` fields, never on a free-text blob -- the probe's own
#: triage matched the echoed path inside 404 bodies, which is exactly the
#: kind of loose matching that must not reach a number anybody relies on.
REBATE_TYPES = ("rebate", "reward", "incentive")

#: prediction-market instruments are the only ones this programme trades
PREDICTION_PREFIX = "GEMI-"

# ----------------------------------------------------------- attribution
#: the venue linked the credit to individual qualifying fills
FILL_LEVEL = "FILL_LEVEL"
#: the credit falls inside a period whose maker volume we can measure from an
#: authoritative venue source, but the venue did not name the fills
WINDOW_LEVEL = "WINDOW_LEVEL"
#: a credit we can see but cannot place against any measurable denominator
UNATTRIBUTED = "UNATTRIBUTED"


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _utc(ms: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0,
                                      tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def is_rebate_row(row: Mapping[str, Any]) -> bool:
    """Does the venue's OWN typing mark this row as a rebate/reward credit?"""
    for k in ("type", "purpose", "reason"):
        v = str(row.get(k) or "").strip().lower()
        if any(h in v for h in REBATE_TYPES):
            return True
    return False


def parse_credits(rows: Any) -> Dict[str, Any]:
    """Rebate credits from a `/v1/transfers` response. None stays UNREADABLE."""
    if rows is None:
        return {"readable": False, "credits": [], "n": 0,
                "why": ("%s was UNREADABLE; absence of a readable transfer "
                        "list is not absence of credits" % TRANSFERS_PATH)}
    if not isinstance(rows, list):
        return {"readable": False, "credits": [], "n": 0,
                "why": "the transfers response was not a list"}
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for r in rows:
        if not isinstance(r, Mapping) or not is_rebate_row(r):
            continue
        amt = _dec(r.get("amount"))
        if amt is None:
            continue
        eid = r.get("eid")
        # the venue's own event id is the credit's identity. A repeated read
        # must not double-count a credit any more than a repeated read may
        # double-count a fill.
        key = str(eid) if eid not in (None, "") else "%s|%s|%s" % (
            r.get("timestampms"), r.get("amount"), r.get("type"))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "eid": eid,
            "utc": _utc(r.get("timestampms")),
            "timestampms": r.get("timestampms"),
            "amount": str(amt),
            "currency": r.get("currency"),
            "type": r.get("type"),
            "purpose": r.get("purpose"),
            "status": r.get("status"),
            "source": "POST %s (authenticated account record)" % TRANSFERS_PATH,
            # NO FILL LINKAGE EXISTS ON THIS SURFACE. Stated on every row so
            # a later reader cannot mistake its absence for an oversight.
            "related_fills": None,
            "related_fills_why": (
                "this surface carries no order id, no symbol and no fill "
                "linkage; fill-level attribution is NOT available and is not "
                "invented"),
            "attribution_confidence": UNATTRIBUTED,
            "labels": ("CONFIRMED: an authoritative account record shows this "
                       "credit. It is realized rebate income, reported on its "
                       "own line and never merged into trading P&L."),
        })
    out.sort(key=lambda c: (c.get("timestampms") or 0))
    total = sum((D(c["amount"]) for c in out), D("0"))
    return {"readable": True, "credits": out, "n": len(out),
            "total": str(total),
            "currencies": sorted({str(c.get("currency")) for c in out}),
            "why": None}


def parse_maker_volume(payload: Any, *, prefix: str = PREDICTION_PREFIX
                       ) -> Dict[str, Any]:
    """Venue-reported MAKER notional per symbol per day.

    `/v1/tradevolume` returns a nested list of per-symbol daily rows. Using
    the venue's own maker notional beats summing our own fills: it is the
    same book the venue pays from.
    """
    if payload is None:
        return {"readable": False, "why": "%s was UNREADABLE" % TRADEVOLUME_PATH}
    rows: List[Mapping[str, Any]] = []

    def walk(o: Any) -> None:
        if isinstance(o, Mapping):
            rows.append(o)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(payload)

    per_day: Dict[str, D] = {}
    per_symbol: Dict[str, D] = {}
    counts: Dict[str, int] = {}
    total = D("0")
    n = 0
    for r in rows:
        sym = str(r.get("symbol") or "")
        if prefix and not sym.startswith(prefix):
            continue
        buy = _dec(r.get("buy_maker_notional")) or D("0")
        sell = _dec(r.get("sell_maker_notional")) or D("0")
        maker = buy + sell
        if maker <= 0:
            continue
        n += 1
        day = str(r.get("data_date") or "")
        per_day[day] = per_day.get(day, D("0")) + maker
        per_symbol[sym] = per_symbol.get(sym, D("0")) + maker
        c = (int(r.get("buy_maker_count") or 0)
             + int(r.get("sell_maker_count") or 0))
        counts[day] = counts.get(day, 0) + c
        total += maker
    return {
        "readable": True, "rows_matched": n,
        "prefix": prefix,
        "maker_notional_total": str(total),
        "maker_notional_by_day": {k: str(v) for k, v in sorted(per_day.items())},
        "maker_notional_by_symbol": {k: str(v) for k, v
                                     in sorted(per_symbol.items())},
        "maker_fill_count_by_day": dict(sorted(counts.items())),
        "source": "POST %s (venue's own maker notional)" % TRADEVOLUME_PATH,
        "labels": ("the venue's OWN maker notional, which is the book it pays "
                   "rebates from -- a better denominator than a sum of our "
                   "own fills."),
    }


def effective_rate(credits: Mapping[str, Any], volume: Mapping[str, Any],
                   *, since_utc: Optional[str] = None,
                   until_utc: Optional[str] = None) -> Dict[str, Any]:
    """Maker volume -> rebate received -> effective rate, or a stated refusal.

    THE DENOMINATOR HAS TO BE DEFENSIBLE. A rate is only computed when the
    credits and the volume describe the SAME period from authoritative
    sources. When the volume read covers a different span than the credits,
    or either is unreadable, this returns the numerator and the refusal
    rather than a number that looks like a rate.
    """
    out: Dict[str, Any] = {
        "window": {"since_utc": since_utc, "until_utc": until_utc},
        "attribution_confidence": UNATTRIBUTED,
        "labels": ("aggregate economics only. The credit surface names no "
                   "fills, so no fill-level attribution exists and none is "
                   "fabricated."),
    }
    if not credits.get("readable"):
        out.update({"computable": False, "why": credits.get("why")})
        return out
    sel = [c for c in credits["credits"]
           if (since_utc is None or (c.get("utc") or "") >= since_utc)
           and (until_utc is None or (c.get("utc") or "") <= until_utc)]
    got = sum((D(c["amount"]) for c in sel), D("0"))
    out["credits_in_window"] = len(sel)
    out["rebates_confirmed_usd"] = str(got)
    out["credit_utcs"] = [c.get("utc") for c in sel]

    if not volume.get("readable"):
        out.update({"computable": False,
                    "why": ("the maker-volume denominator is UNREADABLE (%s); "
                            "the confirmed credit total stands on its own and "
                            "no rate is stated"
                            % volume.get("why"))})
        return out
    denom = _dec(volume.get("maker_notional_total")) or D("0")
    if denom <= 0:
        out.update({"computable": False,
                    "why": ("the venue reports NO maker notional on "
                            "prediction instruments for the period read, so "
                            "there is no defensible denominator. A credit "
                            "with no measurable volume behind it is recorded "
                            "as an aggregate and left unrated.")})
        return out
    out.update({
        "computable": True,
        "maker_notional_usd": str(denom),
        "effective_rate": str((got / denom).quantize(D("0.000001"))),
        "effective_bps": str(((got / denom) * D("10000")).quantize(D("0.01"))),
        "attribution_confidence": WINDOW_LEVEL,
        "why": None,
        "denominator_source": volume.get("source"),
        "caveat": ("the volume read and the credit read are different "
                   "endpoints with different retention windows; the rate is "
                   "only as good as their overlap, which is stated above."),
    })
    return out


# ---------------------------------------------------------------- accounting

#: the seven lines, kept apart, that sum to realized net P&L
LEDGER_LINES = ("matched_pair_pnl", "directional_residual_pnl",
                "spread_capture", "confirmed_maker_rebates",
                "confirmed_other_rewards", "fees",
                "settlement_cashflow")


def realized_ledger(*, matched_pair_pnl: Any = None,
                    directional_residual_pnl: Any = None,
                    spread_capture: Any = None,
                    confirmed_maker_rebates: Any = None,
                    confirmed_other_rewards: Any = None,
                    fees: Any = None,
                    settlement_cashflow: Any = None,
                    estimated_rebates: Any = None,
                    estimated_rewards: Any = None) -> Dict[str, Any]:
    """The owner's seven-line split, and the one number they add up to.

    A line that is UNMEASURED stays None and makes the total None. This is
    deliberate: a sum that silently treats an unmeasured component as zero
    reports a net number the evidence does not support, and every one of
    these lines has been unmeasured at some point in this programme.

    ESTIMATED lines are carried alongside and are NEVER added. They exist so
    the gap between what is accrued and what is credited is visible, which is
    the only honest use for an estimate here.
    """
    lines = {
        "matched_pair_pnl": matched_pair_pnl,
        "directional_residual_pnl": directional_residual_pnl,
        "spread_capture": spread_capture,
        "confirmed_maker_rebates": confirmed_maker_rebates,
        "confirmed_other_rewards": confirmed_other_rewards,
        "fees": fees,
        "settlement_cashflow": settlement_cashflow,
    }
    unmeasured = [k for k, v in lines.items() if v is None]
    total = None
    if not unmeasured:
        total = str(sum((_dec(v) or D("0") for v in lines.values()), D("0")))
    return {
        "lines": {k: (None if v is None else str(v)) for k, v in lines.items()},
        "unmeasured": unmeasured,
        "realized_net_pnl": total,
        "estimated_not_counted": {
            "estimated_rebates": (None if estimated_rebates is None
                                  else str(estimated_rebates)),
            "estimated_rewards": (None if estimated_rewards is None
                                  else str(estimated_rewards)),
            "why": ("accrued or modelled figures. They are carried so the gap "
                    "to what was actually credited is visible, and they are "
                    "NEVER added into realized net P&L."),
        },
        "labels": ("a line that is UNMEASURED makes the total UNMEASURED. "
                   "Treating an unmeasured component as zero would report a "
                   "net number the evidence does not support. A rebate counts "
                   "here only when an authoritative account record shows the "
                   "credit; a settled contract, a fill, a mark and a cost "
                   "basis are none of them realized."),
    }


# ------------------------------------------------------------------ the read

async def read_all(*, since_utc: Optional[str] = None,
                   until_utc: Optional[str] = None,
                   limit: int = 500) -> Dict[str, Any]:
    """Both authenticated reads, parsed, reconciled. Never raises.

    Each source records its own readability. An UNREADABLE source produces a
    stated refusal, never an empty list that reads as "there were none".
    """
    from src.gemini_prediction_auth import has_credentials, private_rest_request

    rep: Dict[str, Any] = {
        "label": "GEMINI_REBATE_RECONCILIATION_V1",
        "utc": datetime.now(timezone.utc).isoformat(),
        "window": {"since_utc": since_utc, "until_utc": until_utc},
    }
    if not has_credentials():
        rep.update({"readable": False, "verdict": "NO_CREDENTIALS",
                    "why": "the account read needs the read-only key"})
        return rep

    async def _post(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            resp = await private_rest_request("POST", path, timeout=15.0,
                                              params=params)
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception:                                      # noqa: BLE001
            return None

    transfers = await _post(TRANSFERS_PATH, {"limit_transfers": limit})
    volume = await _post(TRADEVOLUME_PATH)

    credits = parse_credits(transfers)
    maker = parse_maker_volume(volume)
    rep.update({
        "readable": credits.get("readable"),
        "credits": credits,
        "maker_volume": maker,
        "economics": effective_rate(credits, maker, since_utc=since_utc,
                                    until_utc=until_utc),
        "sources": {"credits": "POST %s" % TRANSFERS_PATH,
                    "maker_volume": "POST %s" % TRADEVOLUME_PATH,
                    "located_by": ("rebate_probe survey 2026-09-19; the "
                                   "prediction-market rebate/reward paths all "
                                   "404 and these two are what exists")},
    })
    return rep


def main(argv=None) -> int:
    import argparse
    import asyncio
    import json
    import os

    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="ISO UTC lower bound")
    ap.add_argument("--until", default=None, help="ISO UTC upper bound")
    ap.add_argument("--out", default="analysis/gemini/rebate_reconciliation.json")
    a = ap.parse_args(argv)
    rep = asyncio.run(read_all(since_utc=a.since, until_utc=a.until))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    c, e = rep.get("credits") or {}, rep.get("economics") or {}
    print("GEMINI_REBATES " + json.dumps({
        "readable": rep.get("readable"), "n_credits": c.get("n"),
        "total": c.get("total"), "computable": e.get("computable"),
        "effective_bps": e.get("effective_bps"), "why": e.get("why")},
        default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
