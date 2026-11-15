"""Generate the ACCOUNTING_REBASE spec that re-anchors realized P&L to cash.

WHY A REBASE IS NEEDED. `realized_today()` is the authority for the daily
and cumulative loss halts. On 2026-11-15 it read -78.68 while the venue's
own settled figures across both lanes came to about -20:

    sports settled ledger, venue's own figure   -19.48   (77 markets)
    BTC5m, cost_basis AUTHENTICATED_FILLS        -0.48   (5 windows)

The service says the figure is unproven itself:
`verified_l5_status: UNPROVEN_LEGACY_ATTRIBUTION`, `cash_anchor: {}`,
`realized_unattributed: 0`, and 148 `unknown_basis_keys`. Those 148 legs
are booked at UPPER_BOUND_1USD_PER_CONTRACT -- $1 a contract, a BOUND and
not a cost. That inflates losses and ERASES wins: an 8c leg that settles
worthless books -1.00 instead of -0.08, and one that WINS books
1.00 - 1.00 = 0.00 instead of +0.92. A ~59 gap over 148 such legs is what
that mechanism produces.

So the cumulative ceiling is firing on a number the system labels
UNPROVEN. The fix is the measurement, not the limit.

WHAT THIS SCRIPT REFUSES TO DO. It will not invent a cash delta. A rebase
is only sound between two reads where the account held NOTHING -- then the
balance delta IS realized P&L, with nothing unsettled hiding inside it.
This refuses unless the account is flat NOW, and it requires the operator
to supply the earlier anchor rather than guessing one.

    --base-balance X --base-utc T   the earlier flat read (operator)
    --attributed Y                  what evidence proves, default the
                                    venue-confirmed -19.96 above
    --from-now                      anchor at today's cash instead, which
                                    sets realized to ZERO going forward

READ `--from-now` BEFORE USING IT. It does not say "we lost nothing". It
says "measure from here", and it DISCARDS the historical record as the
halt's input -- including the real ~20 of losses, not just the overstated
part. The contaminated scalar is kept in the store and the rebase is
logged, so it stays auditable, but the cumulative ceiling starts again
from zero and the account can lose its full ceiling afresh. Prefer
--attributed with the venue-confirmed figure: it unblocks trading just as
well (75 - 20 leaves about 55 of room) and keeps a true number.

usage:
  python3 -I analysis/l5/btc5m_accumulation/make_rebase_spec.py --from-now
  python3 -I .../make_rebase_spec.py --base-balance 358.31 --base-utc ...
"""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal as D

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import arm_campaign as A                                    # noqa: E402

#: VENUE-CONFIRMED REALIZED, both lanes. Sports ACCOUNT_AUDIT 22:49Z:
#: -21.62 over 39 markets from the venue's own rows; its book independently
#: gives -17.89 over 95 markets before 4.26 of fees, i.e. -22.15, and the
#: two agree within 0.53. BTC5m adds -0.48 (AUTHENTICATED_FILLS).
VENUE_CONFIRMED = D("-22.10")


def live_state():
    dep, _ = A.latest_deployment(A.PMUS15)
    hb = [m for m in A.logs(dep, 400) if m.startswith("L5_LIVE_HB")]
    if not hb:
        raise SystemExit("REFUSED: no heartbeat; live state unreadable")
    d = json.loads(hb[-1].split(" ", 1)[1])
    cash = (d.get("available_cash") or {}).get("usd")
    if cash is None:
        raise SystemExit("REFUSED: available_cash is None -- never read as 0")
    exp = d.get("account_exposure") or {}
    return {"utc": d.get("utc"), "cash": D(str(cash)),
            "realized_total": D(str((d.get("day_realized") or {})
                                    .get("all_time_realized_usd"))),
            "open_markets": list(exp.get("markets_counted") or []),
            "worst_case": D(str(exp.get("account_total_worst_case_usd") or 0)),
            "unresolved": (d.get("unresolved_accounting") or {}).get("count")}


def main(argv):
    st = live_state()
    print("LIVE STATE  %s" % st["utc"])
    print("  available_cash        %s" % st["cash"])
    print("  realized_total (halt) %s" % st["realized_total"])
    print("  open markets          %s  worst case %s"
          % (st["open_markets"] or "none", st["worst_case"]))
    print("  unresolved accounting %s" % st["unresolved"])

    if st["unresolved"]:
        raise SystemExit("REFUSED: unresolved accounting records exist; a "
                         "loss of unknown size is on the books")
    #: `worst_case` HERE IS THE STORE'S OPINION, NOT THE VENUE'S.
    #: `account_exposure` is computed from
    #: `store.account_wide_risk_by_market()` -- the SERVICE'S OWN STORE.
    #: That store is the component that spent 2026-11-15 adopting sports
    #: orders it did not own, so a leg it still carries may be a phantom.
    #: On 2026-11-15 it reported 1.0000 of worst case in
    #: aec-testcr-afg-zim-2026-11-13 while the venue's own
    #: /v1/portfolio/positions, walked to the end, showed ZERO non-zero
    #: positions at 21:49Z and again at 22:49Z. The venue read wins: the
    #: market is open until 2026-11-27, but holding no position in an open
    #: market is still flat.
    #:
    #: So a non-zero figure here is a PROMPT TO CHECK THE VENUE, not proof
    #: of exposure, and this script cannot do that check -- it holds no
    #: signing credentials. Confirm against positions before relying on it.
    #:
    #: FLATNESS IS A CONDITION ON A CASH DELTA, NOT ON A REBASE.
    #: The requirement exists because `cash_delta` across a non-flat read
    #: folds unsettled positions into a realized figure. With --from-now
    #: the delta is ZERO BY CONSTRUCTION, so there is no delta for an open
    #: position to contaminate: the rebase asserts "realized is measured
    #: from here", and an open leg simply registers when it settles.
    #: Requiring flatness there would be cargo-culting the guard rather
    #: than applying it.
    if st["worst_case"] > 0:
        print("\nSTORE REPORTS %s of worst case in %s."
              % (st["worst_case"], ", ".join(st["open_markets"])))
        print("  This is the STORE's view, not the venue's. Confirm against "
              "/v1/portfolio/positions before treating it as exposure.")
        if "--from-now" in argv:
            print("  ALLOWED here: --from-now sets cash_delta to 0, so no "
                  "cash delta exists for the open position to corrupt. It "
                  "settles into realized afterwards, as it should.")
        elif "--allow-not-flat" not in argv:
            raise SystemExit("REFUSED: a flat-to-flat cash delta needs both "
                             "reads flat. Wait until flat, or pass "
                             "--allow-not-flat and justify it in the reason")

    if "--from-now" in argv:
        attributed, cash_delta = VENUE_CONFIRMED, D("0")
        reason = ("owner instruction 2026-11-15: re-anchor realized to the "
                  "CURRENT balance. The prior total was UNPROVEN_LEGACY_"
                  "ATTRIBUTION with 148 UPPER_BOUND legs; measurement "
                  "restarts here. This DISCARDS the historical record as "
                  "the halt's input, including the venue-confirmed ~20 of "
                  "real losses. Open at the time of the anchor: %s of "
                  "worst case in %s, which settles into realized after "
                  "this point rather than before it."
                  % (st["worst_case"], st["open_markets"] or "nothing"))
    else:
        if "--base-balance" not in argv:
            raise SystemExit("REFUSED: need --base-balance (an earlier FLAT "
                             "read) or an explicit --from-now")
        base = D(argv[argv.index("--base-balance") + 1])
        attributed = (D(argv[argv.index("--attributed") + 1])
                      if "--attributed" in argv else VENUE_CONFIRMED)
        cash_delta = st["cash"] - base
        reason = ("owner instruction 2026-11-15: realized re-anchored to a "
                  "flat-to-flat cash delta; prior total was UNPROVEN_LEGACY_"
                  "ATTRIBUTION with 148 UPPER_BOUND legs.")

    spec = {
        "id": "upper-basis-contamination-20261115",
        "matches_realized_total": str(st["realized_total"]),
        "attributed": str(attributed),
        "cash_delta": str(cash_delta),
        "anchor": {
            "at_read_utc": st["utc"],
            "at_read_balance": str(st["cash"]),
            "base_utc": ("CURRENT BALANCE IS THE BASE" if "--from-now" in argv
                         else argv[argv.index("--base-utc") + 1]
                         if "--base-utc" in argv else "UNSTATED"),
            "flat_at_each_read": ("worst_case %s, open markets %s"
                                  % (st["worst_case"], st["open_markets"])),
        },
        "reason": reason,
    }
    print("\nresulting realized_total after rebase: %s" % cash_delta)
    print("   attributed   %s" % attributed)
    print("   unattributed %s" % (cash_delta - attributed))
    print("\nPASTE INTO ACCOUNTING_REBASES in src/l5_pmus_live_runner.py:")
    print(json.dumps(spec, indent=4))
    print("\nmatches_realized_total MOVES as sports settles. Regenerate "
          "immediately before deploying, or the spec silently no-ops.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
