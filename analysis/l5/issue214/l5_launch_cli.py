"""`l5 launch` -- prepare, apply, shutdown. One approval, one application.

USAGE

  prepare   read-only. Reads a heartbeat, a runtime-config line, a
            deployment record and an authorization-preflight line (each a
            file, or a log line pasted into one), and prints ONE verdict:
            READY_FOR_APPROVAL or BLOCKED with every gate named. Writes
            nothing, arms nothing, reserves nothing.

  apply     takes the digest the OWNER approved. Rebuilds the document,
            refuses unless the digests match, then writes only the bound
            variables and verifies durable read-back. `--unlock-shadow` is
            a separate flag; `--reset-operator-halt` is a separate flag
            with its own reason and evidence. Neither happens by default.

  shutdown  what must happen at the end of authorization, and whether it
            is due.

THE WRITER IS INJECTED. `apply` with no `--write-cmd` is a DRY RUN: it
prints exactly what it would set and verifies nothing was sent. That is
deliberate -- this tool runs on an operator's machine, and the Railway
write is the operator's action, performed through whatever path they
already trust.

NOTHING HERE HOLDS A LIMIT. Every ceiling, latch and budget is read from
the module that enforces it. See `src/l5_launch.py`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src import l5_launch as L                                  # noqa: E402
from src import l5_probe_preflight as PF                        # noqa: E402


def load(path, label):
    """A JSON file, or a log line with a leading label stripped."""
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read().strip()
    if not raw:
        return None
    for prefix in ("L5_LIVE_HB ", "L5_EFFECTIVE_RUNTIME_CONFIG ",
                   "L5_AUTHORIZATION_PREFLIGHT ", "L5_LIVE_WINDOW_CONFIRMED "):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise SystemExit("REFUSED: %s is not readable JSON: %s"
                         % (label, exc))


def mark(ok):
    return {True: "PASS", False: "FAIL"}.get(ok, str(ok))


def print_prepare(p):
    print("verdict            %s" % p["verdict"])
    print("market             %s" % p["market"])
    print("window start UTC   %s" % p["window_start_utc"])
    print("lead time          %s s" % p["lead_s"])
    print("document digest    %s" % (p["document_digest"] or "-"))
    print()
    print("%-42s %s" % ("gate", "verdict"))
    for g in p["gates"]:
        print("  %-40s %s" % (g["gate"], mark(g["ok"])))
        if g["ok"] is not True:
            detail = {k: v for k, v in g.items()
                      if k not in ("gate", "ok", "note")}
            if detail:
                print("      %s" % json.dumps(detail, default=str)[:300])
    print()
    if p["verdict"] == L.READY:
        print("READY FOR APPROVAL")
        print("  Approve BTC15m %s | maker only | $%s exposure | $%s daily "
              "halt | expires %s" % (
                  p["window_start_utc"],
                  p["document"]["max_total_exposure_usd"],
                  p["document"]["max_daily_loss_usd"],
                  p["document"]["expires_utc"]))
        print()
        print("  digest to approve:")
        print("    %s" % p["document_digest"])
        print()
        print("  then:  apply --approve %s" % p["document_digest"])
    else:
        print("BLOCKED: %s" % ", ".join(p["blocking"]))
        print("  Nothing was changed. Every gate above is read from the "
              "accessor that enforces it;")
        print("  UNKNOWN is never READY, and a market=null authorization "
              "verdict is never READY.")


def main() -> int:
    ap = argparse.ArgumentParser(prog="l5 launch")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("prepare", "apply"):
        s = sub.add_parser(name)
        s.add_argument("--heartbeat", required=True)
        s.add_argument("--runtime-config")
        s.add_argument("--deployment", required=True)
        s.add_argument("--auth-preflight", required=True)
        s.add_argument("--venue-bounds")
        s.add_argument("--calibration")
        s.add_argument("--plan")
        s.add_argument("--expected-sha", required=True)
        s.add_argument("--lead-min", type=int, default=50)
        s.add_argument("--window-start",
                       help="exact UTC window start; else the next full "
                            "window at least --lead-min away")
        s.add_argument("--operator-halted", choices=("true", "false"),
                       required=True,
                       help="from the runtime: L5_LIVE_HALT engaged?")
        s.add_argument("--json", action="store_true")
        if name == "prepare":
            s.add_argument("--save",
                           help="write the prepared plan here. THE OWNER "
                                "APPROVES THIS ARTIFACT; `apply --plan` "
                                "publishes exactly it, so the digest is "
                                "digest is stable across the two invocations.")
        if name == "apply":
            s.add_argument("--approved-plan", dest="plan_file",
                           required=True,
                           help="the artifact `prepare --save` wrote. "
                                "Named distinctly from --plan, which is "
                                "the ORDER plan input -- two different "
                                "things, and conflating them is an "
                                "argparse error rather than a silent one.")
            s.add_argument("--approve", required=True,
                           help="the digest the OWNER approved")
            s.add_argument("--unlock-shadow", action="store_true")
            s.add_argument("--reset-operator-halt", default=None)
            s.add_argument("--halt-evidence",
                           help="JSON file of HALT_RESET_PRECONDITIONS")
            s.add_argument("--write-cmd",
                           help="shell command receiving the variables as "
                                "JSON on stdin. ABSENT = DRY RUN")
            s.add_argument("--read-back-cmd",
                           help="shell command printing the fresh "
                                "heartbeat JSON. ABSENT = DRY RUN")

    sd = sub.add_parser("shutdown")
    sd.add_argument("--heartbeat", required=True)
    sd.add_argument("--window-start", required=True)
    sd.add_argument("--expires-utc")
    sd.add_argument("--json", action="store_true")

    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)

    if a.cmd == "shutdown":
        hb = load(a.heartbeat, "heartbeat") or {}
        ws = dt.datetime.fromisoformat(a.window_start.replace("Z", "+00:00"))
        out = L.shutdown_plan(heartbeat=hb, now=now, window_start=ws,
                              expires_utc=a.expires_utc)
        print(json.dumps(out, indent=1) if a.json else
              "\n".join(["due                %s" % out["due"],
                         "deadline           %s (%s)" % (out["deadline_utc"],
                                                         out["deadline_is"]),
                         "outstanding clear  %s" % out["outstanding_clear"],
                         "", "actions:"] +
                        ["  %d. %s" % (i + 1, x["action"])
                         for i, x in enumerate(out["actions"])]))
        return 0

    hb = load(a.heartbeat, "heartbeat") or {}
    ws = (dt.datetime.fromisoformat(a.window_start.replace("Z", "+00:00"))
          if a.window_start else L.next_full_window(now,
                                                    lead_min=a.lead_min))
    prepared = L.prepare(
        heartbeat=hb,
        deployment=load(a.deployment, "deployment") or {},
        expected_sha=a.expected_sha,
        window_start=ws, now=now,
        auth_preflight=load(a.auth_preflight, "auth-preflight") or {},
        operator_halted=(a.operator_halted == "true"),
        calibration=(hb.get("fill_calibration")
                     or load(a.calibration, "calibration")),
        plan=load(a.plan, "plan"),
        venue_confirmed_bounds=load(a.venue_bounds, "venue-bounds"),
        runtime_config=load(a.runtime_config, "runtime-config"))

    if a.cmd == "prepare":
        if a.json:
            print(json.dumps(prepared, indent=1, default=str))
        else:
            print_prepare(prepared)          # prints; returns None
        if getattr(a, "save", None):
            with open(a.save, "w", encoding="utf-8") as fh:
                json.dump(prepared, fh, indent=1, default=str)
            print("\nplan saved to %s" % a.save)
            print("  the owner approves THIS artifact; "
                  "`apply --approved-plan %s --approve <digest>` "
                  "publishes exactly it" % a.save)
        return 0 if prepared["verdict"] == L.READY else 2

    # ---- apply
    #
    # THE DOCUMENT IS LOADED, NOT REGENERATED, and that is a correctness
    # requirement rather than a convenience. `build_document` stamps
    # `issued_utc` from the clock, so re-deriving it here would produce a
    # different digest from the one the owner approved seconds earlier and
    # `apply_plan` would refuse every single time. Found by the dry run.
    #
    # THE FRESH GATES STILL RUN. `prepared` above was computed from current
    # telemetry and must still be READY -- so a runtime that regressed
    # between approval and application is refused, while the document and
    # its digest come from the artifact the owner actually saw.
    #
    # TAMPERING IS STILL CAUGHT: the owner approves a DIGEST, and
    # `apply_plan` hashes whatever document the file carries and refuses
    # unless the two match.
    saved = load(a.plan_file, "plan file") or {}
    for key in ("document", "document_line", "document_digest",
                "variables", "market", "window_start_utc"):
        if not saved.get(key):
            raise SystemExit("REFUSED: plan file lacks %r; re-run "
                             "`prepare --save`" % key)
    if prepared["verdict"] != L.READY:
        print("REFUSED: the runtime is no longer READY")
        print(json.dumps({"blocking": prepared["blocking"]}, indent=1))
        print("\nNothing was applied.")
        return 3
    if saved.get("market") != prepared.get("market"):
        raise SystemExit(
            "REFUSED: the plan file names %s and the current window is %s"
            % (saved.get("market"), prepared.get("market")))
    prepared = dict(prepared,
                    document=saved["document"],
                    document_line=saved["document_line"],
                    document_digest=saved["document_digest"],
                    variables=saved["variables"])

    dry = not (a.write_cmd and a.read_back_cmd)
    sent = {}

    def write(variables):
        sent.update(variables)
        if dry:
            return None
        return subprocess.run(a.write_cmd, shell=True, check=True,
                              input=json.dumps(variables).encode())

    def read_back():
        if dry:
            # a dry run asserts nothing about the runtime: it echoes the
            # digest it WOULD have published so the verify step is
            # exercised without claiming a real read-back happened.
            return {"campaign": {
                "document_sha256": prepared["document_digest"],
                "approval_state": "DRY_RUN_NOT_A_READ_BACK"}}
        out = subprocess.run(a.read_back_cmd, shell=True, check=True,
                             capture_output=True)
        return json.loads(out.stdout.decode() or "{}")

    try:
        res = L.apply_plan(
            prepared=prepared, approved_digest=a.approve,
            set_variables=write, read_back=read_back,
            unlock_shadow=bool(a.unlock_shadow),
            reset_operator_halt=a.reset_operator_halt,
            halt_reset_evidence=load(a.halt_evidence, "halt-evidence"))
    except L.ApplyRefused as exc:
        print("REFUSED: %s" % exc.reason)
        if exc.detail:
            print(json.dumps(exc.detail, indent=1, default=str))
        print("\nNothing was applied.")
        return 3

    if a.json:
        print(json.dumps(dict(res, dry_run=dry, variables=sent),
                         indent=1, default=str))
        return 0
    print("%s" % ("DRY RUN -- nothing was sent" if dry else "APPLIED"))
    print("digest             %s" % res["digest"])
    print("market             %s" % res["market"])
    print("shadow unlocked    %s" % res["shadow_unlocked"])
    print("halt reset         %s" % (
        "no" if res["operator_halt_reset"] is None
        else res["operator_halt_reset"]["reason"]))
    print("\nvariables %s:" % ("that WOULD be set" if dry else "set"))
    for k in sorted(sent):
        v = sent[k]
        print("  %-24s %s" % (k, (v[:70] + "...") if len(str(v)) > 70
                              else v))
    print("\nsteps:")
    for st in res["steps"]:
        print("  - %s" % st["step"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
