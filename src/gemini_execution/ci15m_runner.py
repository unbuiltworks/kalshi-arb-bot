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

    IT PUSHES TO A UNIQUE NEW REF PER SLOT, NOT TO `branch`. This matters
    and it is not a style choice: the Actions step checks out a PINNED SHA,
    so its HEAD is behind the branch tip, and `HEAD:branch` would be
    rejected non-fast-forward. The hook would then fail, the slot would be
    spent, and the probe would stop having bought nothing. Creating a fresh
    ref always succeeds and is just as durable, and `branch` is kept only to
    name the family of refs so they are easy to find.

    A FORCE PUSH TO `branch` WOULD BE THE WRONG FIX -- it would discard
    whatever else the branch had gained, including other latches.
    """
    import subprocess

    def hook(slot_row: Dict[str, Any]) -> bool:
        d = cohort.directory(slot_row["cohort_id"])
        slot = slot_row.get("slot")
        ref = ("refs/heads/ci15m-latch/%s-slot%s"
               % (slot_row.get("cohort_id"), slot))
        msg = ("latch(ci15m): slot %s consumed for %s [skip ci]"
               % (slot, slot_row.get("ticker")))
        steps = [
            ["git", "add", "-f", d],
            ["git", "-c", "user.name=gemini-ci15m[bot]",
             "-c", "user.email=gemini-ci15m@users.noreply.github.com",
             "commit", "-m", msg],
            # a NEW ref: always a fast-forward, never clobbers anything
            ["git", "push", "origin", "HEAD:%s" % ref],
        ]
        for cmd in steps:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=60)
            if r.returncode != 0 and cmd[1] != "commit":
                print("  SLOT HOOK FAILED at %s (%s): %s"
                      % (cmd[1], ref, (r.stderr or "")[-300:]))
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
    conditions = {
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
        "taker_path_inaccessible": v["checks"]["taker_path_shut"],
        "api_key_present": creds["GEMINI_API_KEY"],
        "api_secret_present": creds["GEMINI_API_SECRET"],
        "canary_live_is_yes": live_yes,
        "arming_fully_armed": arming.armed(),
        "expiry_covers_probe_window": exp["ok"],
    }
    failed = [k for k, ok in conditions.items() if not ok]
    return {"ok": not failed, "failed": failed, "conditions": conditions,
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


async def run(cohort_id: str, *, live: bool, durable_branch: Optional[str],
              max_windows: int, runtime_s: float) -> Dict[str, Any]:
    deadline = time.time() + runtime_s
    hook = git_slot_hook(durable_branch) if (live and durable_branch) else None
    done: List[Dict[str, Any]] = []
    while time.time() < deadline:
        why = BUD.expired(cohort_id)
        if why:
            print("COHORT STOP: %s" % why)
            break
        if BUD.completed_windows(cohort_id) >= max_windows:
            print("WINDOW BUDGET reached: %d" % max_windows)
            break
        now = time.time()
        sel = P.select_market(discover(now), now)
        if not sel["selected"]:
            print("%s no eligible BTC15m window yet (%d considered)"
                  % (datetime.now(timezone.utc).strftime("%H:%M:%SZ"),
                     len(sel["considered"])))
            await asyncio.sleep(POLL_S * 4)
            continue
        s = sel["selected"]
        seen = BUD.windows(cohort_id)
        if s["ticker"] in seen:
            await asyncio.sleep(POLL_S * 4)
            continue
        if not live:
            print("WOULD OPEN %s (tte %.0fs) -- not live, nothing sent"
                  % (s["ticker"], s["tte"]))
            break
        BUD.open_window(cohort_id, s["ticker"])
        print("=" * 78)
        print("WINDOW OPEN %s  tte %.0fs" % (s["ticker"], s["tte"]))
        print("=" * 78)
        rep = await P.run_window(cohort_id, s["symbol"], s["ticker"],
                                 now + s["tte"], poll_s=POLL_S,
                                 slot_hook=hook)
        for e in rep["events"]:
            print(json.dumps(e, sort_keys=True, default=str))
        done.append(rep)
        if rep["halted"]:
            print("HALTED: %s" % rep["halted"])
            break
    return {"windows": done, "budget": BUD.state(cohort_id)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", required=True)
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--preflight-only", action="store_true")
    ap.add_argument("--recover-only", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--durable-branch", default=None)
    ap.add_argument("--max-windows", type=int, default=BUD.WINDOW_BUDGET)
    ap.add_argument("--runtime-s", type=float, default=3000.0)
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
        BUD.create(a.cohort)
    if not cohort.exists(a.cohort):
        print("cohort %s does not exist; pass --create" % a.cohort)
        return 2

    if a.recover_only:
        print(json.dumps(BUD.state(a.cohort), indent=2, default=str))
        return 0

    if a.max_windows > BUD.WINDOW_BUDGET:
        print("--max-windows %d exceeds the authorized %d"
              % (a.max_windows, BUD.WINDOW_BUDGET))
        return 2

    pf = preflight(a.cohort, probe_seconds=a.runtime_s)
    print(json.dumps(pf, indent=2, default=str))
    if a.preflight_only:
        return 0 if pf["ok"] else 1
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
                          max_windows=a.max_windows,
                          runtime_s=a.runtime_s))
    print(json.dumps(out["budget"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
