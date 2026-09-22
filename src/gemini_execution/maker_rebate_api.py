"""GEMINI_MAKER_REBATE_API_V1 -- the venue's OWN rebate payout records.

WHY THIS EXISTS, AND THE MISTAKE IT CORRECTS. `rebate_probe` surveyed
seventeen candidate paths on 2026-09-19, found only `/v1/transfers` and
`/v1/tradevolume`, and this programme then recorded that "every
prediction-market rebate/reward path 404s". That was wrong, and wrong in the
way a guessed enumeration usually is: it missed by ONE PATH SEGMENT. The
probe tried `/v1/prediction-markets/rebates`; the real surface is
`/v1/prediction-markets/maker-rebate/...`. A 404 on a guessed path is
evidence about the guess, never about the venue -- and the survey's own
docstring says absence is recorded as absence, which is exactly the claim it
then overreached on. The paths below are READ FROM THE PUBLISHED SPEC rather
than invented:

    https://developer.gemini.com/prediction-markets-spec/rewards
    (read 2026-09-22)

    GET  /v1/prediction-markets/maker-rebate/rates
         public rate schedule; `rate_rules[]` of {id, rebate_multiplier_bps,
         effective_from, effective_to?, category?}
    POST /v1/prediction-markets/maker-rebate/payouts
         OrderStatus permission; `limit` 1-100 (default 50), `offset`
         per payout: {id, total_volume_usd, total_fill_count,
                      total_rebate_usd, status, paid_at, created_at}
    GET  /v1/prediction-markets/maker-rebate/summary/total
         OrderStatus permission; optional `dateFrom`/`dateTo`, YYYY-MM-DD,
         EASTERN TIME, both required together
         {total_earned_usd, total_fill_count, total_volume_usd,
          payout_count, first_payout_date, last_payout_date}

    Payouts are reported PER CALENDAR DAY IN EASTERN TIME.

WHAT CHANGES FOR OUR ACCOUNTING, AND WHAT DOES NOT. This gives the venue's
own qualifying fill count and qualifying volume beside the dollar figure, so
an accrual computed from the published formula can be checked against three
independent numbers instead of one. It does NOT relax the labelling rule: a
rebate becomes economics only when a payout row says it was PAID. A row in
any other status, and an accrual of our own, both stay out.

THIS MODULE ONLY READS. Every path is an enumerated literal; there is no
order, cancel or transfer-creating route reachable from here, and nothing it
returns can reach a trading decision.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

LABEL = "GEMINI_MAKER_REBATE_API_V1"

SPEC_SOURCE = ("https://developer.gemini.com/prediction-markets-spec/rewards, "
               "read 2026-09-22")

RATES_PATH = "/v1/prediction-markets/maker-rebate/rates"
PAYOUTS_PATH = "/v1/prediction-markets/maker-rebate/payouts"
SUMMARY_PATH = "/v1/prediction-markets/maker-rebate/summary/total"

#: the ONLY status that moves a rebate into economics. Anything else is a row
#: we have read, not money we have been paid.
PAID = "PAID"

CREDITED_LABEL = (
    "CREDITED: the venue's own payout record states this was PAID. It is "
    "rebate income, reported on its own line and never merged into trading "
    "P&L.")
UNPAID_LABEL = (
    "NOT CREDITED. A payout row exists but its status is not PAID, so no "
    "cash is established. It is never counted, and never netted against an "
    "accrual.")


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _fmt(v: Optional[D]) -> Optional[str]:
    return None if v is None else format(v.normalize(), "f")


def parse_payouts(payload: Any) -> Dict[str, Any]:
    """The payout rows, split by whether the venue says they were PAID.

    UNREADABLE IS NOT EMPTY. `None` in means the read failed and that is
    reported as a refusal; an empty list means the venue answered and had
    nothing, which is a different fact.
    """
    if payload is None:
        return {"readable": False, "n": None, "rows": None,
                "why": "the payouts endpoint did not answer; this is not the "
                       "same claim as 'there were no payouts'"}
    rows = payload.get("payouts") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        return {"readable": False, "n": None, "rows": None,
                "why": "the payouts endpoint answered in an unexpected shape"}

    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        status = str(r.get("status") or "").upper()
        paid = status == PAID
        out.append({
            "id": r.get("id"),
            "paid_at": r.get("paid_at"),
            "created_at": r.get("created_at"),
            "status": r.get("status"),
            "total_rebate_usd": _fmt(_dec(r.get("total_rebate_usd"))),
            "total_fill_count": r.get("total_fill_count"),
            "total_volume_usd": _fmt(_dec(r.get("total_volume_usd"))),
            "credited": paid,
            "labels": CREDITED_LABEL if paid else UNPAID_LABEL,
        })
    credited = [r for r in out if r["credited"]]
    return {
        "readable": True, "n": len(out), "rows": out,
        "n_credited": len(credited),
        "credited_total_usd": _fmt(sum(
            (_dec(r["total_rebate_usd"]) or D("0") for r in credited),
            D("0"))),
        "uncredited_total_usd": _fmt(sum(
            (_dec(r["total_rebate_usd"]) or D("0")
             for r in out if not r["credited"]), D("0"))),
        "source": "POST %s" % PAYOUTS_PATH,
        "labels": ("only rows whose status is PAID are credited. The "
                   "uncredited total is reported so a pending payout is "
                   "visible, and it is never added to economics."),
    }


def reconcile(payout: Optional[Mapping[str, Any]],
              accrual: Mapping[str, Any]) -> Dict[str, Any]:
    """One payout row against OUR independently computed accrual.

    THREE INDEPENDENT COMPARISONS, not one. The venue publishes qualifying
    fill count and qualifying volume beside the dollar figure, so a dollar
    agreement reached with the wrong fill count is visible as the coincidence
    it is rather than passing as a match.
    """
    out: Dict[str, Any] = {
        "label": LABEL, "spec_source": SPEC_SOURCE,
        "ours": {
            "eligible_fill_count": accrual.get("n_eligible"),
            "accrued_usd": accrual.get("accrued_usd"),
            "would_pay_usd": accrual.get("would_pay_usd"),
            "qualifying_volume_usd": accrual.get("qualifying_volume_usd"),
        },
    }
    if payout is None:
        out.update({
            "matched": None, "venue": None,
            "verdict": "NO_PAYOUT_ROW",
            "why": ("no payout row covers these fills. That is either 'not "
                    "yet paid' or 'not eligible', and this function cannot "
                    "tell them apart -- the payout date decides it."),
        })
        return out

    v_usd, o_usd = _dec(payout.get("total_rebate_usd")), _dec(
        accrual.get("would_pay_usd"))
    v_n, o_n = payout.get("total_fill_count"), accrual.get("n_eligible")
    v_vol = _dec(payout.get("total_volume_usd"))
    o_vol = _dec(accrual.get("qualifying_volume_usd"))
    out["venue"] = {
        "id": payout.get("id"), "status": payout.get("status"),
        "paid_at": payout.get("paid_at"),
        "total_rebate_usd": payout.get("total_rebate_usd"),
        "total_fill_count": v_n,
        "total_volume_usd": payout.get("total_volume_usd"),
    }
    deltas = {
        "rebate_usd": _fmt((v_usd - o_usd) if (v_usd is not None
                                               and o_usd is not None) else None),
        "fill_count": ((int(v_n) - int(o_n))
                       if (v_n is not None and o_n is not None) else None),
        "volume_usd": _fmt((v_vol - o_vol) if (v_vol is not None
                                               and o_vol is not None) else None),
    }
    agree = (deltas["rebate_usd"] == "0" and deltas["fill_count"] == 0
             and deltas["volume_usd"] == "0")
    out.update({
        "deltas": deltas,
        "matched": agree,
        "credited": str(payout.get("status") or "").upper() == PAID,
        "verdict": ("RECONCILED" if agree else "DISAGREES"),
        "why": (None if agree else
                "the venue's own numbers differ from ours; the DELTAS say "
                "which of the three disagrees, and the venue is authoritative"),
        "labels": ("a rebate enters economics only on status PAID. An "
                   "accrual that matches a PENDING row is still an accrual."),
    })
    return out


async def read_all(*, limit: int = 100,
                   date_from: Optional[str] = None,
                   date_to: Optional[str] = None) -> Dict[str, Any]:
    """Rates, payouts and lifetime summary. Read-only. Never raises."""
    from src.gemini_prediction_auth import has_credentials, private_rest_request

    rep: Dict[str, Any] = {
        "label": LABEL, "utc": datetime.now(timezone.utc).isoformat(),
        "spec_source": SPEC_SOURCE,
        "paths": {"rates": "GET %s" % RATES_PATH,
                  "payouts": "POST %s" % PAYOUTS_PATH,
                  "summary": "GET %s" % SUMMARY_PATH},
        "corrects": ("the 2026-09-19 survey concluded every prediction-market "
                     "rebate path 404s. It had probed "
                     "/v1/prediction-markets/rebates and missed "
                     "/v1/prediction-markets/maker-rebate/... by one path "
                     "segment. These paths come from the published spec."),
    }
    if not has_credentials():
        rep.update({"readable": False, "verdict": "NO_CREDENTIALS",
                    "why": "the payout and summary reads need the key"})
        return rep

    async def _call(method: str, path: str,
                    params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            resp = await private_rest_request(method, path, timeout=15.0,
                                              params=params)
            body = None
            try:
                body = resp.json()
            except Exception:                                  # noqa: BLE001
                body = None
            return {"status": resp.status_code, "body": body,
                    "readable": resp.status_code == 200}
        except Exception as exc:                               # noqa: BLE001
            return {"status": None, "body": None, "readable": False,
                    "error": "%s: %s" % (type(exc).__name__, exc)}

    rates = await _call("GET", RATES_PATH)
    payouts = await _call("POST", PAYOUTS_PATH, {"limit": int(limit)})
    # BOTH OR NEITHER: the spec requires dateFrom and dateTo together, and a
    # half-supplied window is a request the venue is entitled to refuse.
    sp = ({"dateFrom": date_from, "dateTo": date_to}
          if (date_from and date_to) else None)
    summary = await _call("GET", SUMMARY_PATH, sp)

    rep.update({
        "rates": rates,
        "payouts_raw_status": payouts.get("status"),
        "payouts": parse_payouts(payouts.get("body")
                                 if payouts.get("readable") else None),
        "summary": {"status": summary.get("status"),
                    "body": summary.get("body") if summary.get("readable")
                    else None,
                    "window": {"dateFrom": date_from, "dateTo": date_to,
                               "timezone": "America/New_York, per the spec"}},
        "readable": bool(payouts.get("readable")),
    })
    return rep
