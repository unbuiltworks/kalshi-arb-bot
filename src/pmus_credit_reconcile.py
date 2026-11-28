"""Durable reconciliation of PMUS venue credits. OBSERVED ONLY IS REALIZED.

WHY THIS EXISTS. The incentive question is the whole economic case for this
strategy, and the evidence for it kept evaporating. On 2026-10-07 the audit
in `pmus_incentive_audit` ran once against the venue and established, from
the venue's own records, that it had credited $10.36 and charged $2.86 on
this account's own fills across 825 credited fills. Then it wrote its full
document -- the per-fill credit rows, the program walk, the size arithmetic
-- to `/tmp/PMUS_INCENTIVES_REAUDIT.json` inside a Railway container that
has since been REMOVED. Only an eleven-key verdict summary ever reached the
logs. The per-fill detail behind $10.36 is permanently gone, no run exists
after 2026-11-02, and NO committed code referenced that path: the run was
ad-hoc, so nothing could have re-created it.

The consequence was not a missing file. It was that the four venue-confirmed
maker fills of the 2026-11-02 campaign could not be reconciled against the
venue's credit records at all, and their recorded `fee_usd 0.0000` -- which
`l5_live_execution._money_or_none` has since shown to mean UNKNOWN, not
zero -- was read as proof that the rebate did not exist.

So this module does one thing: it makes the reconciliation DURABLE, in the
repository, where a removed container cannot take it away.

WHAT MAY BE CALLED REALIZED. Only a credit the venue itself reported against
one of our own fills. Specifically:

  * REALIZED  -- `credited_on_fills.our_rows`, which reads the venue's
    `commissionNotionalCollected` on whichever execution is OURS by
    `isAggressor`. A NEGATIVE commission there is money the venue paid us.
  * OBSERVED-UNATTRIBUTED -- the `/v1/incentives/earnings` total. It is the
    venue's own figure, but it does not say which fills it belongs to, so it
    is kept separate and never summed with the per-fill rows. On 2026-10-07
    the two disagreed by $0.15 ($10.51 against $10.36) and the audit did not
    reconcile them; that gap is carried as an explicit unresolved item
    rather than averaged away.
  * NEVER REALIZED -- formula estimates, published programs we have not been
    paid under, pending credits, and anything the audit labelled truncated
    or active-only. The 2026-10-07 program scan walked 400 pages of 40,000
    and said so: "a sample, which cannot support an absence claim".

Nothing here contacts the venue. `reconcile` is a pure function over an
audit document so it can be tested without credentials, and `load_rows`
merges per-fill rows into the durable store with the venue tag that
`l5_venue_credits` makes mandatory.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src import l5_venue_credits as vc

#: The durable store. In the repository, not in a container's /tmp.
STORE = os.path.join("analysis", "l5", "pmus_credit_reconciliation.json")
#: The per-fill credit rows, in the schema `l5_venue_credits` validates.
ROWS = os.path.join("analysis", "l5", "venue_rebate_credits.json")

REALIZED = "realized_venue_credited_on_our_fills"
UNATTRIBUTED = "observed_unattributed_earnings_total"
NEVER = "not_realized"


class CreditEvidenceMissing(RuntimeError):
    """A figure was asked for that no reconciled record supports.

    Raised instead of returning an estimate. The caller reports the gap.
    """


def _d(v: Any) -> Optional[D]:
    try:
        return None if v is None else D(str(v))
    except Exception:                                          # noqa: BLE001
        return None


def reconcile(doc: Mapping[str, Any]) -> Dict[str, Any]:
    """Split an audit document into realized, observed and never-realized.

    Accepts either the full audit document or the verdict-only summary that
    is all the 2026-10-07 run left behind, because that summary is the only
    venue evidence we still have and it must still reconcile.
    """
    v = doc.get("verdict") if isinstance(doc.get("verdict"), Mapping) else doc
    rows = []
    cof = doc.get("credited_on_fills")
    if isinstance(cof, Mapping):
        rows = [r for r in (cof.get("our_rows") or []) if isinstance(r, Mapping)]

    credited = _d(v.get("venue_credited_on_our_fills_usd"))
    charged = _d(v.get("venue_charged_us_on_our_fills_usd"))
    # the per-fill rows, when we have them, must add up to the credited total
    row_sum = None
    if rows:
        row_sum = sum((abs(_d(r.get("our_commission")) or D(0))
                       for r in rows
                       if (_d(r.get("our_commission")) or D(0)) < 0), D(0))

    earnings_total = None
    et = v.get("observed_credits_earnings_endpoint")
    if isinstance(et, str) and "total" in et:
        try:
            earnings_total = _d(et.split("total", 1)[1].strip().split()[0])
        except Exception:                                      # noqa: BLE001
            earnings_total = None
    elif isinstance(et, Mapping):
        earnings_total = _d(et.get("total"))

    unresolved: List[Dict[str, Any]] = []
    if credited is not None and earnings_total is not None \
            and credited != earnings_total:
        unresolved.append({
            "item": "earnings_endpoint_vs_per_fill_total",
            "per_fill_usd": str(credited),
            "earnings_endpoint_usd": str(earnings_total),
            "difference_usd": str(earnings_total - credited),
            "why": ("two venue figures for the same thing that the audit did "
                    "not reconcile. Neither is discarded and they are never "
                    "summed.")})
    if credited is not None and row_sum is not None and row_sum != credited:
        unresolved.append({
            "item": "per_fill_rows_do_not_sum_to_credited_total",
            "rows_usd": str(row_sum), "credited_usd": str(credited),
            "difference_usd": str(credited - row_sum),
            "why": "the audit truncates our_rows, so a gap is expected here"})
    if not rows:
        unresolved.append({
            "item": "per_fill_credit_detail_absent",
            "why": ("no per-fill rows in this document. The 2026-10-07 run "
                    "wrote them to container-local /tmp on a deployment that "
                    "has been REMOVED, so the detail behind its $10.36 is "
                    "unrecoverable. Attribution of a credit to a market or a "
                    "strategy is impossible without them."),
            "remedy": ("re-run the audit with its full document written to "
                       "this durable store")})

    never: Dict[str, Any] = {}
    est = v.get("rebate_at_our_executed_size_after_rounding")
    if est is not None:
        never["formula_estimate_at_our_size_usd"] = str(est)
    if v.get("published_programs_covering_our_markets") is not None:
        never["published_programs_covering_our_markets"] = \
            v.get("published_programs_covering_our_markets")
    for k in ("mlb", "reading"):
        if isinstance(v.get(k), str):
            never.setdefault("audit_caveats", {})[k] = v[k]

    return {
        "schema": "pmus_credit_reconciliation/1",
        "retrieved_at_utc": doc.get("retrieved_at_utc"),
        REALIZED: {
            "credited_usd": None if credited is None else str(credited),
            "charged_usd": None if charged is None else str(charged),
            "net_usd": (None if (credited is None or charged is None)
                        else str(credited - charged)),
            "fills_with_a_credit": v.get("our_fills_with_a_credit"),
            "fills_at_or_above_rebate_floor":
                v.get("our_fills_at_or_above_rebate_floor"),
            "per_fill_rows": len(rows),
            "contracts_denominator": None,
            "labels": ("VENUE-REPORTED on our own fills. `contracts_"
                       "denominator` is null because the verdict summary "
                       "carries fill COUNTS and not contract quantities, so "
                       "no per-contract rate may be derived from this block "
                       "alone -- ask `per_contract_rate` and it refuses."),
        },
        UNATTRIBUTED: {
            "earnings_endpoint_usd": (None if earnings_total is None
                                      else str(earnings_total)),
            "labels": ("the venue's own total, but with no fill attribution. "
                       "Never summed with the per-fill figure."),
        },
        NEVER: never,
        "unresolved": unresolved,
    }


def per_contract_rate(rec: Mapping[str, Any]) -> D:
    """The realized credit per CONTRACT, or refuse.

    There is no estimate fallback. A per-contract rate needs a contract
    denominator, and the only surviving venue evidence counts FILLS.
    """
    blk = rec.get(REALIZED) or {}
    ct = _d(blk.get("contracts_denominator"))
    usd = _d(blk.get("credited_usd"))
    if usd is None:
        raise CreditEvidenceMissing("no reconciled credited total")
    if ct is None or ct <= 0:
        raise CreditEvidenceMissing(
            "reconciled credits carry no contract denominator: the surviving "
            "venue evidence counts FILLS, not contracts, so a per-contract "
            "rate cannot be derived. Re-run the audit with per-fill rows.")
    return usd / ct


def load_rows(path: str = ROWS) -> List[Dict[str, Any]]:
    """Per-fill credit rows from the durable store, venue tag enforced."""
    if not os.path.exists(path):
        return []
    return [dict(r) for r in vc.load_credits(path)]


def merge_rows(existing: Iterable[Mapping[str, Any]],
               new: Iterable[Mapping[str, Any]],
               venue: str = vc.POLYMARKET_US) -> List[Dict[str, Any]]:
    """Union by `id`. Every new row is venue-tagged and validated.

    An untagged credit is not a credit, it is an unlabelled number -- see
    `l5_venue_credits`, which exists because a 25-row PMUS sample was nearly
    re-attributed to Gemini.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for r in existing:
        out[str(r.get("id"))] = dict(r)
    for r in new:
        row = dict(r)
        row.setdefault("venue", venue)
        vc.validate_row(row)
        out[str(row.get("id"))] = row
    return [out[k] for k in sorted(out)]


def write(rec: Mapping[str, Any], path: str = STORE) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return path
