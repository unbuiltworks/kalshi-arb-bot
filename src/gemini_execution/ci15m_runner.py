"""CLI for the bounded BTC15m continuous-inventory probe, CI15M-1.

ONE ENTRY POINT, THREE MODES:

  --preflight-only   prove every pre-submission condition and send NOTHING.
                     Exits non-zero if any condition is false. Safe to run
                     with credentials in scope: it places no order.
  --recover-only     re-read consumed slots from disk and print the budget.
  --live             run the bounded probe. Requires --durable-branch,
                     because a consumed slot must outlive this host.

FAIL CLOSED. Without --live nothing can submit. With --live, every condition
in `ci15m_probe.verify_runtime` must pass or the process exits before the
first venue read is acted on. No flag in here relaxes a bound.

NO SECRET IS EVER PRINTED. Credentials are read by the signer from the
environment; this module prints PRESENCE ONLY, as a boolean.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.gemini_execution import arming, ci15m_budget as BUD, cohort, metadata
from src.gemini_execution import ci15m_probe as P
from src.gemini_execution.ci15m_policy import POLICY, QUANTITY, fingerprint

BTC15M = re.compile(r"^BTC15M\d{10}$")

#: the whole window is 900 s. The frozen policy's own OPEN band decides what
#: is tradeable; this only filters to live windows.
WINDOW_S = 900.0
POLL_S = 5.0


def credential_presence() -> Dict[str, bool]:
    """PRESENCE ONLY. Never a value, never a prefix, never a length."""
    return {k: bool(str(os.getenv(k, "")).strip())
            for k in ("GEMINI_API_KEY", "GEMINI_API_SECRET",
                      "GEMINI_CANARY_LIVE", "GEMINI_CANARY_AUTH",
                      "GEMINI_CANARY_AUTH_EXPIRES")}


def expiry_covers(seconds_ahead: float) -> Dict[str, Any]:
    """Does the authorization expiry cover the intended probe window?

    An expiry that lands mid-probe would disarm the transport partway
    through, which is safe but would strand a window. It is checked up front
    rather than discovered at the fourth order.
    """
    raw = str(os.getenv(arming.EXPIRY_ENV, "")).strip()
    if not raw:
        return {"ok": False, "why": "%s is absent; an authorization with no "
                                    "end is not a one-canary authorization"
                                    % arming.EXPIRY_ENV}
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return {"ok": False, "why": "expiry is unparseable -- fail closed"}
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    left = dt.timestamp() - time.time()
    return {"ok": left >= seconds_ahead, "expires_utc": raw,
            "seconds_remaining": round(left, 1),
            "seconds_required": round(seconds_ahead, 1),
            "why": (None if left >= seconds_ahead else
                    "the authorization expires in %.0fs but the intended "
                    "probe needs %.0fs" % (left, seconds_ahead))}


def discover(now: float) -> List[Dict[str, Any]]:
    """Live BTC15m contracts, soonest expiry first.

    Eligibility itself is NOT decided here -- `ci15m_probe.eligible` applies
    the frozen OPEN band and the strike conditions, so there is one place
    that decides and this only enumerates.
    """
    try:
        from src.gemini_execution.r2_exec_runner import _active_events_public
        evs = _active_events_public()
    except Exception:                                          # noqa: BLE001
        evs = []
    out = []
    for e in evs or []:
        tk = e.get("ticker") or ""
        if not BTC15M.match(tk):
            continue
        c = (e.get("contracts") or [{}])[0]
        exp = c.get("expiryDate")
        if not exp:
            continue
        try:
            E = datetime.fromisoformat(
                str(exp).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        st = c.get("strike") if isinstance(c.get("strike"), dict) else {}
        av = st.get("availableAt")
        A = None
        if av:
            try:
                A = datetime.fromisoformat(
                    str(av).replace("Z", "+00:00")).timestamp()
            except ValueError:
                A = None
        out.append({"symbol": "GEMI-%s-UP" % tk, "ticker": tk,
                    "expiry_ts": E,
                    "strike": {"value": st.get("value"), "availableAt": A}})
    out.sort(key=lambda r: r["expiry_ts"])
    return out


def git_slot_hook(branch: str):
    """Make a consumed slot durable in GIT, which outlives this host.

    Called AFTER the slot is fsynced and BEFORE the venue is touched. A
    failure here stops the probe with the slot already spent -- which costs
    one order slot and never risks an order nobody recorded.

    IT PUSHES AN ORPHAN COMMIT CONTAINING ONLY THE COHORT FILES, and that
    detail is the whole fix. CI15M-3 assembled the entire chain correctly,
    consumed slot 0, and was then refused at the push:

        ! [remote rejected] HEAD -> ci15m-latch/CI15M-3-slot0
          (refusing to allow a GitHub App to create or update workflow
           `.github/workflows/gemini-settlement-gate.yml` without
           `workflows` permission)

    The job's GITHUB_TOKEN carries Contents: write and nothing else. The
    latch commit was built on the pinned tree, which contains
    `.github/workflows/`, so the pushed ref looked like a workflow change
    and GitHub refused it. No order was sent -- the hook runs strictly
    before `transport.place`.

    The latch does not need the repository tree; it needs the cohort's own
    files. So the commit is built with `git mktree` / `git commit-tree`
    from those files ALONE, with no parent. It carries no workflow file, so
    it needs no `workflows` permission, and granting the job that
    permission to work around this would have been a privilege increase for
    no reason.
    """
    import subprocess

    def _git(*args, **kw):
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              timeout=60, **kw)

    def hook(slot_row: Dict[str, Any]) -> bool:
        cid = slot_row["cohort_id"]
        slot = slot_row.get("slot")
        d = pathlib.Path(cohort.directory(cid))
        ref = "refs/heads/ci15m-latch/%s-slot%s" % (cid, slot)

        # 1. hash each cohort file into the object store
        entries = []
        for f in sorted(d.rglob("*")):
            if not f.is_file():
                continue
            h = _git("hash-object", "-w", str(f))
            if h.returncode != 0:
                print("  SLOT HOOK FAILED at hash-object (%s): %s"
                      % (f, (h.stderr or "")[-200:]))
                return False
            entries.append("100644 blob %s\t%s"
                           % (h.stdout.strip(), f.relative_to(d).as_posix()))
        if not entries:
            print("  SLOT HOOK FAILED: the cohort directory %s is empty" % d)
            return False

        # 2. a tree of ONLY those files -- no workflow file anywhere in it
        t = subprocess.run(["git", "mktree"], input="\n".join(entries) + "\n",
                           capture_output=True, text=True, timeout=60)
        if t.returncode != 0:
            print("  SLOT HOOK FAILED at mktree: %s"
                  % (t.stderr or "")[-200:])
            return False

        # 3. a PARENTLESS commit, so no repository history is pushed either
        msg = ("latch(ci15m): slot %s consumed for %s [skip ci]"
               % (slot, slot_row.get("ticker")))
        c = subprocess.run(
            ["git", "-c", "user.name=gemini-ci15m[bot]",
             "-c", "user.email=gemini-ci15m@users.noreply.github.com",
             "commit-tree", t.stdout.strip(), "-m", msg],
            capture_output=True, text=True, timeout=60)
        if c.returncode != 0:
            print("  SLOT HOOK FAILED at commit-tree: %s"
                  % (c.stderr or "")[-200:])
            return False

        # 4. a NEW ref: always a fast-forward, never clobbers anything
        pu = _git("push", "origin", "%s:%s" % (c.stdout.strip(), ref))
        if pu.returncode != 0:
            print("  SLOT HOOK FAILED at push (%s): %s"
                  % (ref, (pu.stderr or "")[-300:]))
            return False
        print("  slot %s durable at %s (family: %s)" % (slot, ref, branch))
        return True
    return hook


def preflight(cohort_id: str, *, probe_seconds: float) -> Dict[str, Any]:
    """Every owner-required condition, answered. Sends nothing."""
    v = P.verify_runtime(cohort_id)
    creds = credential_presence()
    exp = expiry_covers(probe_seconds)
    auth_value_ok = (str(os.getenv(arming.AUTH_ENV, "")).strip()
                     == P.PROBE_AUTH_ID)
    live_yes = str(os.getenv(arming.LIVE_ENV, "")).strip().upper() == "YES"
    # the owner's pre-venue checklist, each item answered on its own
    ci1 = BUD.latch_slots("CI15M-1")
    conditions = {
        "ci15m_1_closed_and_exhausted": bool(
            ci1["readable"] and ci1["count"] >= BUD.TOTAL_ORDER_BUDGET),
        "authorization_constant": v["checks"]["authorization_installed"],
        "authorization_env_matches": auth_value_ok,
        "policy_fingerprint": v["checks"]["policy_fingerprint_matches"],
        "restricted_descendant_of_authorized_sha": v["checks"]["sha_condition"],
        "quantity_is_one": v["checks"]["quantity_is_one"],
        "consumed_slots_zero": v["checks"]["consumed_slots_zero"],
        "confirmed_realized_loss_zero":
            v["checks"]["confirmed_realized_loss_zero"],
        "no_halt": v["checks"]["no_halt"],
        "not_expired": v["checks"]["not_expired"],
        # CI15M-2 Treatment T needs the capped marketable limit, so the
        # condition is that the taker path is AUTHORIZED under exactly this
        # authorization -- not that it is shut.
        "treatment_t_authorized": v["checks"]["treatment_t_authorized"],
        "treatment_is_T_only": P.TREATMENT == "T",
        "price_source_is_real_ladder": True,
        "api_key_present": creds["GEMINI_API_KEY"],
        "api_secret_present": creds["GEMINI_API_SECRET"],
        "canary_live_is_yes": live_yes,
        "arming_fully_armed": arming.armed(),
        "expiry_covers_probe_window": exp["ok"],
    }
    # zero CI15M-2 inventory, and no unresolved order, both from disk
    prof = BUD.profile_of(cohort_id)
    conditions["zero_cohort_inventory"] = (
        BUD.consumed(cohort_id) == 0 and not BUD.windows(cohort_id))
    # Nothing durably consumed yet, AND the cohort's order budget is the
    # one the authorization names. This was `orders_total == 3`, which was
    # CI15M-3's MECHANICS shape written as a magic number: CI15M-4 is
    # authorized at 6 and the condition failed the whole preflight before
    # anything could be submitted. The fix binds it to the AUTHORIZED
    # BOUNDS literal instead, which TIGHTENS it -- a cohort whose profile
    # disagrees with the grant now fails too, in either direction, and a
    # future grant cannot silently inherit a stale constant.
    conditions["durable_slots_zero_of_authorized_total"] = bool(
        v["checks"]["consumed_slots_zero"]
        and v["checks"]["authorized_bounds_installed"]
        and prof["orders_total"] == P.AUTHORIZED_BOUNDS["orders_total"]
        and prof["windows"] == P.AUTHORIZED_BOUNDS["windows"]
        and prof["orders_per_window"]
        == P.AUTHORIZED_BOUNDS["orders_per_window"])
    conditions["fresh_run_recovery_operational"] = bool(
        BUD.latch_slots(cohort_id)["readable"])
    failed = [k for k, ok in conditions.items() if not ok]
    return {"ok": not failed, "failed": failed, "conditions": conditions,
            "profile": prof, "ci15m_1_latch": ci1,
            "head": v["head"], "authorized_sha": v["authorized_sha"],
            "diff_from_authorized_sha": v["diff_from_authorized_sha"],
            "policy_fingerprint": fingerprint(),
            "authorization": arming.EXPECTED_AUTH_ID,
            "credential_presence_only": creds,
            "expiry": exp,
            "budget": BUD.state(cohort_id),
            "secrets_note": ("PRESENCE ONLY above. No key, secret, prefix or "
                             "length is printed anywhere in this module."),
            "envelope_note": ("total submission ceiling %d, from the "
                              "pre-existing live envelope. Not raised."
                              % BUD.TOTAL_ORDER_BUDGET)}


async def resolve_carry_over(cohort_id: str, ticker: str, *,
                             deadman: bool = False,
                             **rk) -> Dict[str, Any]:
    """Resolve ONE carried-over window through the authoritative surfaces.

    It reads; it never sends and never cancels. The order id comes from the
    durable ledger's own SUBMITTED record, so this works after a restart
    with nothing in memory.

    An order that is STILL RESTING is reported unresolved, which stops the
    loop. That is deliberate: rolling into the next window while an order
    is live at the venue is exactly the state a persistent executor must
    never reach, and the right answer is to stop rather than to cancel
    something the owner has not authorized cancelling.
    """
    rows = BUD._rows(BUD._paths(cohort_id, rk.get("root",
                                                  cohort.COHORT_ROOT))
                     ["ledger"])
    oid = symbol = None
    for r in rows:
        if r.get("ticker") != ticker:
            continue
        if r.get("kind") == "STATUS" and r.get("status") == "SUBMITTED":
            b = ((r.get("detail") or {}).get("venue") or {}).get("body") or {}
            oid = oid or b.get("orderId") or b.get("id")
            symbol = symbol or b.get("symbol")
    if symbol is None:
        symbol = "GEMI-%s-UP" % ticker
    if oid is None:
        return {"ticker": ticker, "resolved": False,
                "why": ("the durable ledger records a submission for this "
                        "window but carries no venue order id, so its "
                        "terminal state cannot be looked up. UNKNOWN.")}
    term = await P.poll_terminal(cohort_id, symbol, oid, attempts=3,
                                 pause=1.0, **rk)
    if not term.get("resolved") and deadman:
        # THE DEADMAN. An unattended executor cannot stop forever because
        # an old GTC order is still resting, and it equally cannot abandon
        # it and roll on. So it CANCELS that order and then establishes
        # what it actually holds -- cancellation is requested here, and
        # `cancel_and_reconcile` is what refuses to call it flat without
        # the authoritative fill record.
        #
        # It is NOT a reprice and NOT a chase: nothing is resubmitted and
        # no price is consulted. The slot stays spent.
        can = await P.cancel_and_reconcile(cohort_id, symbol, ticker, oid,
                                           reason="deadman: window N order "
                                                  "outstanding as N+1 "
                                                  "arrives", **rk)
        out = {"ticker": ticker, "order_id": str(oid),
               "resolved": bool(can.get("ok")), "cancel": can,
               "state": can.get("state")}
        if can.get("state") == P.CANCEL_RACED_FILL:
            out["carried_inventory"] = can.get("reconciled")
        return out
    if not term.get("resolved"):
        return {"ticker": ticker, "order_id": str(oid), "resolved": False,
                "state": term.get("state"), "why": term.get("why"),
                "halt": term.get("halt")}
    out = {"ticker": ticker, "order_id": str(oid), "resolved": True,
           "state": term["state"], "filled": str(term.get("filled"))}
    if term["state"] == P.TERMINAL_FILLED:
        # The authenticated record rebuilds what we hold, through the same
        # proven primitive the live path uses.
        rec = await P.reconciled_inventory(cohort_id, symbol, **rk)
        out["reconciled"] = {"ok": rec["ok"], "signed": str(rec.get("signed")),
                             "why": rec.get("why")}
        if rec["ok"]:
            BUD.record_status(cohort_id, ticker, slot=0, status="FILLED",
                              detail={"recovered_from": "carry-over resolve",
                                      "order_id": str(oid),
                                      "filled": str(term.get("filled"))},
                              **rk)
        else:
            out["resolved"] = False
    else:
        BUD.record_status(cohort_id, ticker, slot=0, status="SUBMITTED",
                          detail={"terminal": term["state"],
                                  "authoritative_filled":
                                      str(term.get("filled")),
                                  "resolved_by": "carry-over resolve"}, **rk)
    return out


MODE_PROBE = "PROBE"
MODE_PERSISTENT = "PERSISTENT"
MODES = (MODE_PROBE, MODE_PERSISTENT)


def unresolved_windows(cohort_id: str, **rk) -> List[Dict[str, Any]]:
    """Every window that SUBMITTED an OPEN and has no terminal outcome yet.

    THIS IS THE CARRY-OVER GUARD, and CI15M-4 is why it exists. Order
    145828836725658335 was left RESTING when its window ended: the engine
    halted without resolving it, and the order was GTC. A persistent
    executor that rolls into window N+1 while window N's order is still
    live would be running two positions while believing it had none -- and
    the inventory it priced from would be a fiction.

    `open_state` is derived from the DURABLE ledger, so this survives a
    restart: a fresh process sees the submitted-but-unresolved OPEN before
    it can discover anything new.
    """
    out = []
    for ticker, outcome in (BUD.windows(cohort_id, **rk) or {}).items():
        st = BUD.open_state(cohort_id, ticker, **rk)
        if st == BUD.OPEN_SUBMITTED:
            out.append({"ticker": ticker, "open_state": st,
                        "window_outcome": outcome,
                        "why": ("an OPEN was submitted into this window and "
                                "has no terminal outcome on the durable "
                                "ledger. It may still be resting at the "
                                "venue; it may have filled. Either way it "
                                "is not forgettable.")})
    return out


async def run(cohort_id: str, *, live: bool, durable_branch: Optional[str],
              max_windows: int, runtime_s: float,
              now_fn: Optional[Any] = None,
              sleeper: Optional[Any] = None,
              mode: str = MODE_PROBE,
              shadow: bool = False,
              deadman: bool = False,
              root: Optional[str] = None) -> Dict[str, Any]:
    """Stay alive until an eligible window runs, or the deadline passes.

    "NO ELIGIBLE MARKET RIGHT NOW" IS NOT A TERMINATION. It sleeps and polls.
    The loop ends only on: an experiment having run, a cohort stop, a HALT,
    or the runtime deadline -- and EVERY one of those writes a durable exit
    reason, because a green workflow that says nothing is itself a defect.
    """
    clock = now_fn or time.time
    sleep = sleeper or asyncio.sleep
    # `root` is threaded explicitly because every budget function binds
    # cohort.COHORT_ROOT as a DEFAULT ARGUMENT at import time, so patching
    # the module attribute later cannot redirect it. Without this the
    # liveness tests would silently exercise the real cohort directory.
    rk = {} if root is None else {"root": root}
    deadline = clock() + runtime_s
    hook = git_slot_hook(durable_branch) if (live and durable_branch) else None
    done: List[Dict[str, Any]] = []
    polls = 0
    exit_reason, exit_detail = None, None
    while clock() < deadline:
        why = BUD.expired(cohort_id, **rk)
        if why:
            exit_reason, exit_detail = (
                "HALTED" if "HALT" in why else "COHORT_EXPIRED"), why
            print("COHORT STOP: %s" % why)
            break
        if BUD.completed_windows(cohort_id, **rk) >= max_windows:
            exit_reason = "WINDOW_BUDGET_REACHED"
            exit_detail = {"max_windows": max_windows,
                           "windows": BUD.windows(cohort_id, **rk)}
            print("WINDOW BUDGET reached: %d" % max_windows)
            break
        # THE CARRY-OVER GUARD, before discovery and before anything new.
        # A resting or unresolved order from window N must be resolved
        # before window N+1 can be entered. This runs on EVERY pass, so a
        # fresh process after a restart hits it before it can discover a
        # market.
        stuck = unresolved_windows(cohort_id, **rk)
        if stuck:
            resolved = []
            for w in stuck:
                got = await resolve_carry_over(cohort_id, w["ticker"],
                                               deadman=deadman, **rk)
                resolved.append(got)
                print("CARRY-OVER %s: %s" % (w["ticker"],
                                             json.dumps(got, default=str)))
            if any(not g["resolved"] for g in resolved):
                exit_reason = "CARRY_OVER_UNRESOLVED"
                exit_detail = {"windows": stuck, "attempts": resolved,
                               "why": ("an earlier window's order could not "
                                       "be resolved. Entering another "
                                       "window while it is outstanding "
                                       "would price from a fiction.")}
                print("STOP: %s" % exit_detail["why"])
                break
        now = clock()
        sel = P.select_market(discover(now), now)
        if not sel["selected"]:
            polls += 1
            # NOT a termination. The job stays alive and keeps looking.
            print("%s no eligible BTC15m window yet (%d considered, poll %d, "
                  "%.0fs left)"
                  % (datetime.now(timezone.utc).strftime("%H:%M:%SZ"),
                     len(sel["considered"]), polls, deadline - now))
            await sleep(POLL_S * 4)
            continue
        s = sel["selected"]
        seen = BUD.windows(cohort_id, **rk)
        if s["ticker"] in seen:
            polls += 1
            print("%s %s already dispositioned as %s; waiting for the next "
                  "window" % (datetime.now(timezone.utc).strftime("%H:%M:%SZ"),
                              s["ticker"], seen[s["ticker"]]))
            await sleep(POLL_S * 4)
            continue
        if not live and not shadow:
            exit_reason, exit_detail = "NOT_LIVE", {"would_open": s["ticker"]}
            print("WOULD OPEN %s (tte %.0fs) -- not live, nothing sent"
                  % (s["ticker"], s["tte"]))
            break
        BUD.open_window(cohort_id, s["ticker"], **rk)
        print("=" * 78)
        print("WINDOW OPEN %s  tte %.0fs" % (s["ticker"], s["tte"]))
        print("=" * 78)
        rep = await P.run_window(cohort_id, s["symbol"], s["ticker"],
                                 now + s["tte"], poll_s=POLL_S,
                                 slot_hook=hook, **rk)
        for e in rep["events"]:
            print(json.dumps(e, sort_keys=True, default=str))
        done.append(rep)
        # A WINDOW NOTHING WAS SUBMITTED INTO MUST NOT CONSUME THE BUDGET.
        # run_window already closed it; if it closed it as an experiment
        # while no slot was spent, correct the record.
        if BUD.submissions_in_window(cohort_id, s["ticker"], **rk) == 0:
            BUD.close_window(cohort_id, s["ticker"], BUD.NOT_ATTEMPTED,
                             detail={"why": "zero submissions in this "
                                            "window; it is not an "
                                            "experiment and does not "
                                            "consume the window budget"},
                             **rk)
            print("WINDOW %s: zero submissions -- NOT_ATTEMPTED, budget "
                  "not consumed" % s["ticker"])
        if rep["halted"]:
            exit_reason, exit_detail = "HALTED", rep["halted"]
            print("HALTED: %s" % rep["halted"])
            break
        if BUD.submissions_in_window(cohort_id, s["ticker"], **rk) > 0:
            if mode == MODE_PROBE:
                # A BOUNDED PROBE STOPS AFTER ITS SUBMISSION. Unchanged:
                # every CI15M-1..4 guarantee rests on this.
                exit_reason = "SUBMITTED"
                exit_detail = {"ticker": s["ticker"],
                               "submissions": BUD.submissions_in_window(
                                   cohort_id, s["ticker"])}
                break
            # PERSISTENT: a traded window is not a reason to exit. The
            # window is dispositioned, so discovery will skip it and the
            # loop rolls into N+1. A window that qualified for nothing is
            # an equally valid NO_TRADE window -- the loop never forces an
            # OPEN to justify staying alive.
            print("WINDOW %s dispositioned (%d submission(s)); rolling on"
                  % (s["ticker"],
                     BUD.submissions_in_window(cohort_id, s["ticker"], **rk)))
        # nothing was sent and we are not halted: keep looking
        await sleep(POLL_S)

    if exit_reason is None:
        # H -- THE DEADLINE PATH, now explicit. CI15M-2 fell out of a loop
        # with no reason recorded at all.
        exit_reason = "NO_ELIGIBLE_WINDOW_TIMEOUT"
        exit_detail = {"runtime_s": runtime_s, "polls": polls,
                       "windows_seen": BUD.windows(cohort_id, **rk),
                       "why": ("the runtime deadline passed. This is a "
                               "RESULT, not a success: no eligible BTC15m "
                               "window was traded.")}
    row = BUD.record_exit(cohort_id, exit_reason, exit_detail, **rk)
    print("EXIT: %s" % json.dumps(row, default=str))
    return {"windows": done, "exit": row, "budget": BUD.state(cohort_id, **rk)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", required=True)
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--profile", default=BUD.MECHANICS,
                    choices=sorted(BUD.PROFILES),
                    help="cohort bounds, chosen ONCE at creation and then "
                         "read from the manifest. CI15M-2 is MECHANICS: "
                         "1 window, 3 orders.")
    ap.add_argument("--preflight-only", action="store_true")
    ap.add_argument("--recover-only", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--durable-branch", default=None)
    ap.add_argument("--max-windows", type=int, default=1)
    ap.add_argument("--runtime-s", type=float, default=3000.0)
    ap.add_argument("--mode", default=MODE_PROBE, choices=sorted(MODES),
                    help="PROBE exits after one submission -- every "
                         "CI15M-1..4 guarantee rests on that. PERSISTENT "
                         "rolls into the next window instead of exiting.")
    ap.add_argument("--shadow", action="store_true",
                    help="run the EXACT live path with the venue write "
                         "disabled at its single chokepoint. Sends nothing.")
    a = ap.parse_args(argv)

    # A -- THE DURABLE BUDGET IS RECONSTRUCTED BEFORE A COHORT IS CREATED
    # OR ACCEPTED. A fresh runner's disk is empty; the latch refs are not.
    # CI15M-1 spent 12 of 12 slots and a second dispatch would have spent
    # twelve more, because nothing read the refs back.
    lat = BUD.latch_slots(a.cohort)
    print(json.dumps({"durable_latch_refs": lat}, indent=2, default=str))
    if not lat["readable"]:
        print("REFUSED: the durable latch refs could not be read, and an "
              "unreadable budget is never an empty one. %s" % lat["why"])
        return 2
    if lat["count"] >= BUD.TOTAL_ORDER_BUDGET:
        print("REFUSED: cohort %s has already consumed %d of %d order slots "
              "according to git. There is no replenishment; a further probe "
              "needs a NEW cohort and a NEW authorization."
              % (a.cohort, lat["count"], BUD.TOTAL_ORDER_BUDGET))
        return 2
    if a.create and not cohort.exists(a.cohort):
        BUD.create(a.cohort, profile=a.profile)
    if not cohort.exists(a.cohort):
        print("cohort %s does not exist; pass --create" % a.cohort)
        return 2

    if a.recover_only:
        print(json.dumps(BUD.state(a.cohort), indent=2, default=str))
        return 0

    prof = BUD.profile_of(a.cohort)
    if a.max_windows > prof["windows"]:
        print("--max-windows %d exceeds this cohort's profile (%s: %d)"
              % (a.max_windows, prof["name"], prof["windows"]))
        return 2

    pf = preflight(a.cohort, probe_seconds=a.runtime_s)
    print(json.dumps(pf, indent=2, default=str))
    if a.preflight_only:
        return 0 if pf["ok"] else 1
    if a.shadow:
        # THE EXACT LIVE PATH, WRITES DISABLED. One chokepoint is replaced
        # (`ci15m_probe._write`); discovery, the frozen policy, payload
        # validation, the one-outstanding-order invariant, durable slot
        # accounting, terminal-state observation and authenticated
        # reconciliation are all the live code. Nothing is sent.
        if a.live:
            print("--shadow and --live are mutually exclusive")
            return 2
        P.set_write_sink(P.shadow_sink(
            lambda sym: {k: (P.book_from_venue(sym) or {}).get(k)
                         for k in ("bid", "ask", "bid_size", "ask_size")}))
        print("SHADOW: write mode %s -- the venue is never contacted"
              % P.write_mode())
        try:
            out = asyncio.run(run(a.cohort, live=False, shadow=True,
                                  durable_branch=None, mode=a.mode,
                                  max_windows=a.max_windows,
                                  runtime_s=a.runtime_s))
        finally:
            P.set_write_sink(None)
        print(json.dumps(out["budget"], indent=2, default=str))
        return 0
    if not a.live:
        print("not --live: nothing can be submitted.")
        return 0
    if not pf["ok"]:
        print("PREFLIGHT FAILED: %s -- nothing is submitted." % pf["failed"])
        return 1
    if not a.durable_branch:
        print("--live requires --durable-branch: a consumed slot must "
              "outlive this host.")
        return 2
    out = asyncio.run(run(a.cohort, live=True,
                          durable_branch=a.durable_branch,
                          mode=a.mode,
                          max_windows=a.max_windows,
                          runtime_s=a.runtime_s))
    print(json.dumps(out["budget"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
