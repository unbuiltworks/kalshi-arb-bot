"""Drive the REAL production path to the mutation boundary, and stop there.

WHY THIS EXISTS. Two defects in a row made the driver PERMANENTLY INCAPABLE of
trading, and both survived a large adversarial suite because every test only
ever exercised the REFUSING direction. A suite that checks the safe path
refuses cannot tell "correctly fail-closed" from "broken shut". The second one
cost a live authorization to discover.

So this runs everything the armed path runs -- the real readers, the real
awaited async terms read, the real account and open-order reads, the real
symbol selection, the real TTE, the real recovery, the real controller, the
real working-order diff -- and stops at the exact instruction before the first
frame would go on a socket.

IT CANNOT SEND. Three independent reasons, none of which is a promise:

  it imports no session      ws_session and GeminiMakerSession are not
                             imported here and a test asserts it over the AST
  it passes no socket        place() is called with session=None
  the gate refuses first     Driver.place() calls require_authorization()
                             BEFORE it touches the session, so the refusal
                             happens with no socket in the picture at all

THE LAST ONE IS THE POINT, and it is why the boundary check is a real call to
the real `place()` rather than an inspection. Proving the mutation is blocked
by ASKING FOR IT and being refused is evidence; reading the code and asserting
it would be refused is what we did twice before, and it was wrong twice.

ONE GATE IS SIMULATED, EXPLICITLY. Authorization is deliberately absent, so
`Driver.authorize()` would stop the run at step 2 and nothing downstream would
ever be exercised -- which is the whole thing we are trying to test. The probe
therefore records the real authorization state, then advances the driver's
state itself, labelled SIMULATED_AUTHORIZATION_FOR_PROOF_ONLY. That simulation
authorizes NOTHING: the final boundary call still goes through
require_authorization() and still refuses, and if it ever stopped refusing this
probe would fail rather than pass.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional

from src.gemini_execution import continuous_preflight as PF
from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import metadata as MD
from src.gemini_execution import payload as P
from src.gemini_execution import run_continuous as RC
from src.gemini_execution import spec
from src.gemini_execution import ws_continuous as WC

LABEL = "GEMINI_L5_PREMUTATION_PROBE_V1"

READY = "READY_TO_MUTATE_BUT_UNAUTHORIZED"
BLOCKED = "BLOCKED_BEFORE_THE_BOUNDARY"

#: the id used to build the driver. It is NOT an authorization and cannot
#: become one: no latch is consumed and the arm is never set.
PROOF_ID = "PROOF-ONLY-NOT-AN-AUTHORIZATION"


def _gate(name: str, ok: bool, **detail: Any) -> Dict[str, Any]:
    return {"gate": name, "ok": bool(ok), **detail}


def _finish(rep: Dict[str, Any], verdict: Optional[str] = None,
            why: Optional[str] = None) -> Dict[str, Any]:
    """The ONLY exit. Every return fills in the same summary fields.

    Early returns that skipped `failed_gates` made a BLOCKED report say less
    than a READY one -- so the reader could not tell WHICH gate stopped it
    without re-reading the list by hand. A report of a refusal has to name the
    refusal.
    """
    g = rep.get("gates") or []
    failed = [x["gate"] for x in g if not x["ok"]]
    rep["failed_gates"] = failed
    rep["verdict"] = verdict or (READY if (g and not failed) else BLOCKED)
    if why:
        rep["why"] = why
    if rep["verdict"] != READY and not rep.get("why") and failed:
        rep["why"] = "blocked at: %s" % ", ".join(failed)
    return rep


async def prove(symbol: Optional[str] = None, *,
                ledger_path: str = "/tmp/premutation_ledger.jsonl",
                now: Optional[float] = None) -> Dict[str, Any]:
    """Walk the production path to the boundary. Sends nothing, ever."""
    rep: Dict[str, Any] = {
        "label": LABEL,
        "utc": datetime.now(timezone.utc).isoformat(),
        "driver_executable_sha": WC.executable_sha(),
        "driver_envelope_sha": WC.envelope_sha(),
        "envelope": WC.envelope(),
        "gates": [],
        "frames_sent": 0,
        "latch_consumed": False,
    }
    g: List[Dict[str, Any]] = rep["gates"]

    # -------------------------------------------------- 1. symbol selection
    if symbol is None:
        sel = RC.eligible_symbol(now)
        rep["symbol_selection"] = sel
        if not sel.get("eligible"):
            g.append(_gate("symbol_selected", False, why=sel.get("why")))
            return _finish(rep, why=sel.get("why"))
        symbol = sel["chosen"]["symbol"]
    rep["symbol"] = symbol
    g.append(_gate("symbol_selected", True, symbol=symbol))

    # ------------------------------------------- the REAL production readers
    ts_value: Dict[str, Any] = {}
    ts_awaited = False
    try:
        ts_value = await MD.terms_status() or {}
        ts_awaited = True
    except Exception as exc:                                   # noqa: BLE001
        rep["terms_error"] = "%s: %s" % (type(exc).__name__, exc)
    readers = {"terms_for": MD.terms_for,
               "terms_status": (lambda: ts_value),
               "outcome_book": MD.outcome_book}

    # -------------------------------------------------- the real Driver
    try:
        driver = WC.Driver(PROOF_ID, symbol, ledger_path=ledger_path)
    except CP.ProbeStop as exc:
        g.append(_gate("driver_constructed", False, why=str(exc)))
        return _finish(rep, why=str(exc))

    # -------------------------------------------- 2,3,4. the real preflight
    pf = driver.preflight(readers=readers)
    p = pf["preflight"]
    rep["preflight"] = p
    g.append(_gate("contract_active_open", (p.get("contract_status") == "active"
                                            and p.get("market_state") == "open"),
                   contract_status=p.get("contract_status"),
                   market_state=p.get("market_state")))
    g.append(_gate("increments_agree",
                   (p.get("quantityMinimum") == "0.01"
                    and p.get("quantityIncrement") == "0.01"
                    and p.get("priceMinimum") == "0.01"
                    and p.get("priceIncrement") == "0.01"),
                   quantityMinimum=p.get("quantityMinimum"),
                   quantityIncrement=p.get("quantityIncrement"),
                   priceMinimum=p.get("priceMinimum"),
                   priceIncrement=p.get("priceIncrement")))
    g.append(_gate("terms_awaited_and_accepted",
                   bool(ts_awaited and p.get("terms_accepted") is True),
                   awaited=ts_awaited, accepted=p.get("terms_accepted"),
                   version=p.get("terms_version"),
                   why=p.get("terms_why")))
    g.append(_gate("preflight_agrees", bool(pf["agrees"]),
                   discrepancies=pf["discrepancies"]))
    if driver.state in WC.TERMINAL:
        return _finish(rep, why=driver.why)

    # ------------------------------ the authorization gate, RECORDED not used
    auth = WC.authorization_state()
    rep["authorization"] = auth
    rep["authorization_simulated"] = {
        "simulated": True,
        "label": "SIMULATED_AUTHORIZATION_FOR_PROOF_ONLY",
        "why": ("authorization is deliberately absent, so Driver.authorize() "
                "would stop the run here and nothing downstream would be "
                "exercised. The state is advanced by this probe so the REST "
                "of the production path runs. It authorizes nothing: the "
                "boundary call below still goes through "
                "require_authorization() and still refuses."),
    }
    driver.state = WC.AUTHORIZED

    # ---------------------------------------- 5,6,7,12. the real recovery
    st = await RC.authoritative_state(symbol)
    rep["authoritative_reads"] = {
        "fills_readable": st.get("fills") is not None,
        "open_orders_readable": st.get("open_orders") is not None,
        "order_history": st.get("order_history_raw"),
        "active": st.get("active_raw"),
        "fills": st.get("fills"),
        "open_orders": st.get("open_orders"),
    }
    g.append(_gate("inventory_readable", st.get("fills") is not None,
                   n_fills=(None if st.get("fills") is None
                            else len(st["fills"]))))
    g.append(_gate("open_orders_readable", st.get("open_orders") is not None,
                   n_open=(None if st.get("open_orders") is None
                           else len(st["open_orders"]))))

    rec = driver.recover(venue_fills=st.get("fills"),
                         venue_open=st.get("open_orders"),
                         venue_orders=st.get("orders_seen"))
    rep["recovery"] = rec
    g.append(_gate("ledger_recovery_readable", bool(rec.get("recovered")),
                   why=rec.get("why"), note=rec.get("budget_note")))
    g.append(_gate("budget_reconstructed", rec.get("budget_consumed") is not None,
                   consumed=rec.get("budget_consumed"),
                   left=rec.get("budget_left")))
    if not rec.get("recovered"):
        return _finish(rep, why=driver.why)

    # ------------------------------------------ 8. starting exposure in bounds
    inv = driver.inv
    breaches = CP.risk_gates(inv, driver.budget_consumed or 0)
    rep["starting_inventory"] = inv.report()
    g.append(_gate("starting_exposure_within_envelope", not breaches,
                   breaches=breaches,
                   yes=str(inv.yes_qty), no=str(inv.no_qty),
                   residual=str(inv.residual()),
                   imbalance=str(inv.imbalance()),
                   spend=str(inv.spend())))

    # ----------------------------------------------- 1(b). WindowGuard bound
    b = driver.bind()
    rep["binding"] = b
    g.append(_gate("window_guard_bound", bool(b.get("bound")),
                   symbol=driver.symbol,
                   actions_taken=b.get("actions_taken")))
    if not b.get("bound"):
        return _finish(rep, why=driver.why)

    # --------------------------------------------------- 9. the real TTE
    tte = float(RC.tte_of(symbol)())
    phase = CP.window_phase(tte)
    rep["tte_s"] = tte
    rep["window_phase"] = phase
    g.append(_gate("tte_readable_and_executable",
                   tte > 0 and phase == CP.WINDOW_OPEN,
                   tte_s=tte, phase=phase))

    # ------------------------------ 10,11. the real controller and the diff
    out = driver.decide(tte)
    rep["decision"] = {
        "window_phase": out.get("window_phase"),
        "relations": [a.get("action") for a in
                      ((out.get("turn") or {}).get("actions") or [])],
        "desired_surface": [
            {"action": a.get("action"), "leg": a.get("leg"),
             "price": str(a.get("price")), "qty": str(a.get("qty"))}
            for a in ((out.get("turn") or {}).get("actions") or [])],
        "holds": (out.get("turn") or {}).get("holds"),
        "budget_left": out.get("budget_left"),
    }
    plan = out.get("plan") or {}
    rep["plan"] = {
        "keep": [{"leg": k["order"]["leg"], "price": k["order"]["price"],
                  "qty": k["order"]["qty"]} for k in plan.get("keep", [])],
        "place": [{"leg": pl["rung"]["leg"], "price": str(pl["rung"]["price"]),
                   "qty": str(pl["rung"]["qty"])} for pl in plan.get("place", [])],
        "cancel": [{"client_order_id": c["order"]["client_order_id"]}
                   for c in plan.get("cancel", [])],
        "mutations": plan.get("mutations"),
    }
    g.append(_gate("controller_produced_a_surface",
                   bool(rep["decision"]["desired_surface"]),
                   n=len(rep["decision"]["desired_surface"])))
    g.append(_gate("diff_produced_a_mutation_plan",
                   plan.get("mutations") is not None,
                   mutations=plan.get("mutations"),
                   keep=len(plan.get("keep", [])),
                   place=len(plan.get("place", [])),
                   cancel=len(plan.get("cancel", []))))

    # the EXACT frames that would go out, rendered and sanitized
    rep["would_send"] = []
    for pl in plan.get("place", []):
        r = pl["rung"]
        params = P.build_ws_order(
            symbol=symbol, price=str(r["price"]), quantity=str(r["qty"]),
            client_order_id="l5cp-<auth10>-%s-<uuid8>" % str(r["leg"]).lower(),
            outcome=(spec.WS_OUTCOME_YES if r["leg"] == "YES"
                     else spec.WS_OUTCOME_NO))
        rep["would_send"].append(
            P.sanitized_ws_frame(params,
                                 request_id="<uuid4 hex, per request>")["frame"])
    rep["slots_that_would_be_consumed"] = plan.get("mutations")
    rep["slots_remaining_after"] = (driver.budget_left()
                                    - int(plan.get("mutations") or 0))

    # ------------------------- 13. THE BOUNDARY. Ask to mutate; be refused.
    boundary: Dict[str, Any]
    if plan.get("place"):
        rung = plan["place"][0]["rung"]
        try:
            await driver.place(None, rung)               # session=None
            boundary = {"refused": False,
                        "ALARM": "place() did NOT refuse while unauthorized"}
        except WC.NotAuthorized as exc:
            boundary = {"refused": True, "by": "NotAuthorized",
                        "why": str(exc),
                        "reached_socket": False,
                        "note": ("require_authorization() runs before the "
                                 "session is touched, so the refusal happened "
                                 "with no socket in the picture")}
        except Exception as exc:                               # noqa: BLE001
            boundary = {"refused": True, "by": type(exc).__name__,
                        "why": str(exc),
                        "ALARM": "refused, but NOT by the authorization gate"}
    else:
        boundary = {"refused": None,
                    "why": "the surface proposed no PLACE this cycle"}
    rep["boundary"] = boundary
    g.append(_gate("authorization_is_the_sole_blocker",
                   bool(boundary.get("refused")
                        and boundary.get("by") == "NotAuthorized"),
                   **{k: v for k, v in boundary.items() if k != "why"}))

    # nothing may have been spent by any of the above
    rep["ledger_rows"] = driver.ledger.consumed()
    rep["frames_sent"] = len(getattr(driver, "_sent", []) or [])
    g.append(_gate("no_mutation_was_recorded",
                   driver.ledger.consumed() == (rec.get("budget_consumed") or 0),
                   ledger_rows=driver.ledger.consumed()))

    return _finish(rep)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="analysis/gemini/premutation_proof.json")
    ap.add_argument("--ledger", default="/tmp/premutation_ledger.jsonl")
    a = ap.parse_args(argv)
    rep = asyncio.run(prove(ledger_path=a.ledger))
    try:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=2, default=str)
    except OSError:
        pass
    print("GEMINI_PREMUTATION " + json.dumps(rep, default=str), flush=True)
    return 0 if rep.get("verdict") == READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
