"""WHERE DO THE MAKER REBATE CREDITS LIVE? A read-only survey, not a reader.

The owner's Activity history shows real `Maker Rebate Reward` credits on this
account -- $0.05 on Aug 29, $0.14 on Aug 30, $0.17 on Sep 1, $0.06 and $0.01
on Sep 8. Those are screenshots of a mobile UI. The instruction is explicit:
find the AUTHENTICATED endpoint that exposes them before building anything,
and do not write a scraper if an account/history endpoint already does.

So this probes and reports. It does not parse a rebate, does not compute a
rate, and does not decide anything. Its whole output is "which of these
surfaces exists, what shape does it return, and does any of it contain a
credit that looks like the ones in the UI".

EVERY PATH IS A READ. No mutation path is reachable: `private_rest_request`
carries no order transport, and the paths below are enumerated literals that
contain no order, cancel or transfer-creating route.

BOTH VERBS ARE TRIED. Gemini's classic private API is POST-with-payload;
the prediction-markets surface has served GET for `terms/status` and
`order_history`. Which verb a path answers is itself part of the finding, so
a 405 is recorded rather than treated as absence.

ABSENCE IS RECORDED AS ABSENCE, never as zero. A 404 means "this endpoint is
not here", which is a different fact from "there were no rebates", and the
two must never collapse -- that is the same unreadable-versus-empty rule this
programme applies everywhere else.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LABEL = "GEMINI_REBATE_SURFACE_PROBE_V1"

#: Every plausible account/history surface, with WHY each is a candidate.
#: Enumerated literals: nothing here is constructed from input.
CANDIDATES = (
    # the mobile UI calls the screen "Activity", and these credits appear in
    # it interleaved with trades and settlements -- so an account activity
    # feed is the single most likely home
    ("/v1/portfolio/activities", "the Activity feed the UI screenshot shows"),
    ("/v1/account/activities", "same feed under an account-scoped path"),
    ("/v1/activities", "same feed, unscoped"),
    # credits frequently post as ledger/transfer rows on Gemini
    ("/v1/transfers", "credits often post as transfer rows"),
    ("/v1/balances", "cash anchor before/after a credit"),
    ("/v1/notionalbalances/usd", "cash anchor in notional terms"),
    # explicitly incentive-shaped surfaces
    ("/v1/incentives", "named incentive surface"),
    ("/v1/incentives/earnings", "credited earnings under incentives"),
    ("/v1/prediction-markets/rewards", "prediction-market rewards"),
    ("/v1/prediction-markets/rebates", "prediction-market rebates"),
    ("/v1/prediction-markets/liquidity-rewards", "liquidity rewards"),
    ("/v1/prediction-markets/incentives", "incentives under prediction markets"),
    ("/v1/prediction-markets/activities", "activity under prediction markets"),
    ("/v1/prediction-markets/settlements", "settlement cashflow, for contrast"),
    # the DENOMINATOR: an effective rebate rate needs maker volume, and a
    # venue-reported volume is far better than one we sum ourselves
    ("/v1/notionalvolume", "venue's own notional volume / fee tier"),
    ("/v1/tradevolume", "venue's own trade volume"),
    ("/v1/prediction-markets/metrics/volume", "prediction-market volume"),
)

#: words that would mark a row as one of the credits in the UI. Matching is
#: for TRIAGE ONLY -- it tells a human where to look next and is never used
#: to classify a credit as a rebate.
REBATE_HINTS = ("rebate", "reward", "incentive", "maker_rebate",
                "makerrebate", "liquidity")


def _looks_like_a_credit(blob: str) -> List[str]:
    low = blob.lower()
    return sorted({h for h in REBATE_HINTS if h in low})


def _shape(obj: Any, depth: int = 0) -> Any:
    """The SHAPE of a response, not its contents.

    A probe report is committed as evidence, so it must not carry account
    balances or anything else that is nobody's business. Keys and types
    answer "does this surface exist and what does it look like"; values do
    not.
    """
    if depth > 3:
        return "..."
    if isinstance(obj, dict):
        return {k: _shape(v, depth + 1) for k, v in list(obj.items())[:25]}
    if isinstance(obj, list):
        return [_shape(obj[0], depth + 1), "...x%d" % len(obj)] if obj else []
    return type(obj).__name__


async def probe_one(method: str, path: str, why: str,
                    params: Optional[Dict[str, Any]] = None,
                    timeout: float = 12.0) -> Dict[str, Any]:
    """One path, one verb. Never raises; a failure is a recorded result."""
    from src.gemini_prediction_auth import private_rest_request
    out: Dict[str, Any] = {"path": path, "method": method, "why": why,
                           "params": params}
    try:
        resp = await private_rest_request(method, path, timeout=timeout,
                                          params=params)
    except Exception as exc:                                   # noqa: BLE001
        out.update({"reachable": False,
                    "error": "%s: %s" % (type(exc).__name__, exc)})
        return out
    out["status"] = resp.status_code
    try:
        body = resp.json()
    except Exception:                                          # noqa: BLE001
        body = None
        out["text_head"] = (resp.text or "")[:200]
    out["reachable"] = True
    out["exists"] = resp.status_code not in (404, 405)
    out["verb_wrong"] = resp.status_code == 405
    if body is not None:
        out["shape"] = _shape(body)
        out["n_rows"] = len(body) if isinstance(body, list) else None
        blob = json.dumps(body, default=str)
        out["bytes"] = len(blob)
        out["rebate_hints"] = _looks_like_a_credit(blob)
        # a SMALL redacted sample, shape-first, so a human can see the row
        # format without the report carrying account figures around
        if isinstance(body, list) and body:
            out["first_row_keys"] = (sorted(body[0].keys())
                                     if isinstance(body[0], dict) else None)
    return out


async def survey(*, include_get: bool = True) -> Dict[str, Any]:
    from src.gemini_prediction_auth import has_credentials
    rep: Dict[str, Any] = {
        "label": LABEL,
        "utc": datetime.now(timezone.utc).isoformat(),
        "credentials": has_credentials(),
        "labels": ("READ-ONLY SURVEY. It locates a surface; it does not read "
                   "a rebate, compute a rate or decide anything. A 404 means "
                   "the endpoint is absent, which is NOT the same fact as "
                   "'there were no rebates'."),
    }
    if not has_credentials():
        rep.update({"verdict": "NO_CREDENTIALS",
                    "why": ("the survey needs the read-only account key; "
                            "without it absence proves nothing")})
        return rep

    results: List[Dict[str, Any]] = []
    for path, why in CANDIDATES:
        results.append(await probe_one("POST", path, why))
        if include_get:
            results.append(await probe_one("GET", path, why))
    rep["probes"] = results

    found = [r for r in results if r.get("exists") and r.get("rebate_hints")]
    exists = sorted({r["path"] for r in results if r.get("exists")})
    rep["endpoints_that_exist"] = exists
    rep["endpoints_mentioning_a_credit"] = sorted(
        {"%s %s" % (r["method"], r["path"]) for r in found})
    rep["verdict"] = ("SURFACE_FOUND" if found else
                      ("ENDPOINTS_EXIST_BUT_NO_CREDIT_ROWS" if exists
                       else "NO_CANDIDATE_ENDPOINT_RESPONDED"))
    rep["next"] = ("build the reader against the named surface"
                   if found else
                   "no authenticated surface exposed a credit row; report "
                   "that rather than writing a scraper")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="analysis/gemini/rebate_surface_probe.json")
    ap.add_argument("--no-get", action="store_true")
    a = ap.parse_args(argv)
    rep = asyncio.run(survey(include_get=not a.no_get))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    print("GEMINI_REBATE_SURFACE " + json.dumps(
        {k: rep.get(k) for k in ("verdict", "credentials",
                                 "endpoints_that_exist",
                                 "endpoints_mentioning_a_credit")},
        default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
