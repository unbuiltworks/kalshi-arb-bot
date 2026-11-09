#!/usr/bin/env python3
"""The complete one-approval launch flow, driven from LIVE production state.

READ-ONLY. It pulls the real heartbeat, deployment, runtime config,
authorization preflight, venue bounds and recorder read-back from the
running service, runs the real `l5_launch` phases against them, and prints
what the owner would approve.

IT WRITES NOTHING AND ARMS NOTHING. There is no transport here and no
credential; `apply` is never called.

THREE PASSES, because one is not a demonstration:

  1. PRODUCTION AS IT IS. Expected to BLOCK -- the deployed SHA is not the
     candidate and the operator halt is engaged. A launcher that reported
     READY here would be the 08:30Z failure.
  2. THE CANDIDATE DEPLOYED, HALT STILL ENGAGED. Expected to BLOCK on the
     halt alone. This is the state the approved disarmed deployment
     produces, and the point is that it is still not READY.
  3. THE CANDIDATE DEPLOYED, HALT RESET, LOCK CLEARED. Expected READY, and
     it prints the one line the owner would approve plus its digest.

Pass 3 is a SIMULATION of a state that does not exist and must not be
reached without separate explicit authorization. It is printed so the
owner can see the artifact they would be asked to approve, not as a claim
that the system is ready.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

sys.path.insert(0, ".")

from src import l5_launch as L
from src import l5_railway as RW

UTC = dt.timezone.utc
PROJECT = "50fb7357-e2bc-4d04-a49e-71eb5d73b0d1"
ENVIRON = "f3642d27-26dc-4e21-9170-bc76290c076f"
EXECUTOR = "87f1834e-530e-4fc4-a310-91c8d3add196"


def live():
    c = RW.RailwayClient(project_id=PROJECT, environment_id=ENVIRON,
                         service_id=EXECUTOR)
    scope = c.verify_scope()
    dep = c.latest_deployment()
    hb = c.heartbeat() or {}
    cfg = c.runtime_config()
    ap = c.auth_preflight() or {}
    vars_ = c.variables()
    return {"scope": scope, "deployment": dep, "heartbeat": hb,
            "runtime_config": cfg, "auth_preflight": ap, "variables": vars_,
            "client": c}


def show(title, out, *, limit=None):
    print("=" * 70)
    print(title)
    print("=" * 70)
    print("verdict  %s" % out["verdict"])
    print("market   %s" % out.get("market"))
    print("window   %s  (lead %.1f min)"
          % (out.get("window_start_utc"), (out.get("lead_s") or 0) / 60.0))
    bad = [g for g in out["gates"] if g["ok"] is not True]
    print("gates    %d, %d not passing" % (len(out["gates"]), len(bad)))
    for g in bad[:limit or 99]:
        extra = {k: v for k, v in g.items()
                 if k not in ("gate", "ok", "labels") and v is not None}
        print("   %-34s %-7s %s"
              % (g["gate"], str(g["ok"]), json.dumps(extra, default=str)[:150]))
    if out["verdict"] == L.READY:
        d = out["document"]
        print()
        print("  THE OWNER'S LINE")
        print("    Approve BTC15m %s | maker only | $%s exposure | "
              "$%s daily halt | expires %s"
              % (d["window_starts"][0], d["max_total_exposure_usd"],
                 d["max_daily_loss_usd"], d["expires_utc"]))
        print("    digest: %s" % out["document_digest"])
        print()
        print("  WHAT APPLY WOULD PUBLISH (names only; nothing is sent)")
        for k in sorted(out["variables"]):
            v = out["variables"][k]
            print("    %-24s %s" % (k, v if len(str(v)) < 70
                                    else str(v)[:67] + "..."))
    print()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--expected-sha", required=True)
    p.add_argument("--lead-min", type=float, default=40.0)
    p.add_argument("--window-start")
    a = p.parse_args()

    st = live()
    c = st["client"]
    hb = st["heartbeat"]
    now = dt.datetime.now(UTC)
    if a.window_start:
        ws = dt.datetime.fromisoformat(a.window_start.replace("Z", "+00:00"))
    else:
        ws = L.next_full_window(now, lead_min=a.lead_min)
    slug = L.slug_for_window(ws)
    bounds = c.venue_window_bounds(slug) if slug else None

    print("LIVE PRODUCTION STATE, read-only")
    print("  scope        %s" % st["scope"].get("ok"))
    print("  deployment   %s %s %s"
          % (st["deployment"]["id"][:8], st["deployment"]["status"],
             (st["deployment"].get("commitHash") or "")[:12]))
    print("  heartbeat    %s | market %s" % (hb.get("utc"), hb.get("market")))
    print("  halt_local_env %s | L5_LIVE_HALT %r | L5_SHADOW_ONLY %r"
          % (hb.get("halt_local_env"), st["variables"].get("L5_LIVE_HALT"),
             st["variables"].get("L5_SHADOW_ONLY")))
    sg = hb.get("submission_gate") or {}
    print("  gate         blocked=%s reason=%s shadow_lock=%s latch=%s "
          "arm_token=%s armed=%s"
          % (sg.get("blocked"), sg.get("reason"), sg.get("shadow_lock"),
             sg.get("halt_latch"), sg.get("owner_arm_token"),
             sg.get("armed")))
    print("  proposed     %s at %s" % (slug, L._iso(ws)))
    print("  venue bounds %s" % (json.dumps(bounds) if bounds else
                                 "NOT CONFIRMED YET (the window is future; "
                                 "the executor confirms it as it nears)"))
    print()

    halted = bool(str(st["variables"].get("L5_LIVE_HALT") or "").strip())
    common = dict(window_start=ws, now=now,
                  auth_preflight=st["auth_preflight"],
                  calibration=hb.get("fill_calibration"),
                  venue_confirmed_bounds=bounds,
                  runtime_config=st["runtime_config"])

    show("PASS 1 -- PRODUCTION AS IT IS. Expected BLOCKED.",
         L.prepare(heartbeat=hb, deployment=st["deployment"],
                   expected_sha=a.expected_sha, operator_halted=halted,
                   **common))

    # ---- PASS 2: the candidate deployed, halt still engaged.
    dep2 = dict(st["deployment"])
    dep2["commitHash"] = a.expected_sha
    dep2["status"] = "SUCCESS"
    hb2 = json.loads(json.dumps(hb, default=str))
    (hb2.setdefault("submission_gate", {})).update(
        {"shadow_lock": True, "operator_halt": True, "halt_latch": "CLEAR",
         "owner_arm_token": True, "armed": False, "blocked": True,
         "reason": "OPERATOR_HALT"})
    show("PASS 2 -- CANDIDATE DEPLOYED, HALT ENGAGED, LOCK ON. "
         "Expected BLOCKED.",
         L.prepare(heartbeat=hb2, deployment=dep2,
                   expected_sha=a.expected_sha, operator_halted=True,
                   **common))

    # ---- PASS 3: SIMULATED fully-authorized state.
    hb3 = json.loads(json.dumps(hb2, default=str))
    hb3["submission_gate"].update(
        {"shadow_lock": False, "operator_halt": False,
         "reason": "NOT_ARMED", "blocked": True})
    hb3["campaign"] = dict(hb3.get("campaign") or {},
                           approval_state="approved", windows_spent=0,
                           windows_remaining=1, max_windows=1,
                           permits_this_window=True, reason=None)
    ap3 = dict(st["auth_preflight"], ok=True, fail=[], unknown=[],
               evaluated_market=slug, fail_reasons={})
    show("PASS 3 -- SIMULATED: halt reset, lock cleared, fresh approval. "
         "NOT A REAL STATE.",
         L.prepare(heartbeat=hb3, deployment=dep2,
                   expected_sha=a.expected_sha, operator_halted=False,
                   window_start=ws, now=now, auth_preflight=ap3,
                   calibration=hb.get("fill_calibration"),
                   venue_confirmed_bounds=(bounds or {
                       "source": "VENUE", "start": L._iso(ws),
                       "end": L._iso(ws + dt.timedelta(minutes=15)),
                       "SIMULATED": True}),
                   runtime_config=st["runtime_config"]))

    print("NOTHING WAS WRITTEN. `apply` was not called. No variable, no "
          "deployment, no arming, no order.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
