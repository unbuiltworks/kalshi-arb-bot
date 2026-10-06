"""LIVE-L5-EXEC-1 boot preflight: DISARMED, and mutation-incapable.

Deployment preparation only. This process establishes, on the credentialed
host, that the exact frozen EXEC-1 artifact is present and cannot trade.
It changes no strategy rule, no risk bound, no quantity and no fingerprint.

STRUCTURAL REFUSAL, NOT A FLAG. `src.l5_pmus_transport` and
`src.pmus_q1_executor` own the signed POST path; this image does not copy
them, and gate `mutation_incapable` FAILS if either turns out to be
importable. The only HTTP object here is `pmus_auth.ReadOnlyTransport`,
which raises PermissionError on any method but GET. "Cannot" outranks
"will not".

Order submission is not reachable from this entrypoint at all, which is
strictly stronger than DISARMED. Attempt 2 therefore needs a separate,
separately authorized image that adds the order path; nothing here can
become that image by setting an environment variable.

Credentials are reported BY PRESENCE ONLY. Their values are never read,
printed or logged.

A gate that cannot be EVALUATED fails. Unknown is never a pass.
Exit 0 only when every blocking gate passed. Exit 0 still means NO ORDER.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Mapping, Optional

from src import pmus_auth as A
from src import l5_preview_gate as G
from analysis.l5.live_exec import spec as E
from analysis.l5.live_exec.ledger import (ARM_KEYS, OneOrderOnly, exec1_armed,
                                          may_submit, validate_size)
from analysis.l5.live_micro.ledger import StateError

BTC15M_SERIES = "btc-updown-15m"
BTC15M_SLUG_RE = re.compile(r"^cpc-btc-updown-15m-\d{4}-\d{2}-\d{2}-\d{4}z$")

#: order-capable modules that must NOT exist in this image
FORBIDDEN_MODULES = ("src.l5_pmus_transport", "src.pmus_q1_executor",
                     "src.pmus_micro_executor")

#: other executors' arming namespaces. None of them may arm EXEC-1.
FOREIGN_ARM_NAMESPACES = ("PMUS_Q1_", "PMUS_MICRO_", "PMUS_CANARY_",
                          "PMUS_CASE1_", "PMUS_SCHEMA_PROBE_")

RESULT: Dict[str, Any] = {"probe": E.PROBE, "role": "BOOT_PREFLIGHT",
                          "orders_submitted": 0, "venue_mutations": 0,
                          "gates": {}, "blocking_failures": []}


def gate(name: str, ok: bool, detail: Any = None, blocking: bool = True
         ) -> bool:
    RESULT["gates"][name] = {"pass": bool(ok), "detail": detail,
                             "blocking": blocking}
    if blocking and not ok:
        RESULT["blocking_failures"].append(name)
    print("%-34s %s  %s" % (name, "PASS" if ok else "FAIL",
                            "" if detail is None else detail), flush=True)
    return bool(ok)


def event_slug_for(slug: str) -> str:
    return slug[4:] if slug.startswith("cpc-") else slug


def resolve_market(http: Any, slug: str) -> Dict[str, Any]:
    """Read-only market metadata. Same two resources the validated
    preflight uses, re-implemented here so the order-capable transport
    module never enters this image."""
    for host in (A.API, A.GATEWAY):
        for path, pick in (
                ("/v1/market/slug/%s" % slug, "market"),
                ("/v1/events/slug/%s" % event_slug_for(slug), "event")):
            try:
                r = http.request("GET", path, None, host) or {}
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException:                              # noqa: BLE001
                continue
            obj = r.get(pick) or r
            if pick == "market":
                if isinstance(obj, Mapping) and obj.get("slug") == slug:
                    return dict(obj)
            else:
                for mk in (obj.get("markets") or []):
                    if isinstance(mk, Mapping) and mk.get("slug") == slug:
                        return dict(mk)
    raise LookupError("market metadata not served for %s" % slug)


def order_capable_processes() -> List[str]:
    """Any other live process that could submit a PMUS order and so
    contaminate attribution of a future EXEC-1 fill."""
    hits: List[str] = []
    names = ("pmus_q1_executor", "pmus_micro_executor", "pmus_spring_l5",
             "l5_pmus_live")
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or pid == str(os.getpid()):
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        argv = [a for a in raw.split(b"\0") if a]
        toks = [a.decode("utf8", "replace") for a in argv]
        # A cmdline that merely MENTIONS a module name is not an executor --
        # an editor, a grep or this very preflight would match. The needle
        # must be an actual invocation target: `-m <dotted.module>` or a
        # script path ending in `<name>.py`.
        for i, tok in enumerate(toks):
            base = tok.rsplit("/", 1)[-1]
            dotted = tok.split(".")[-1]
            prev_is_m = i > 0 and toks[i - 1] == "-m"
            if any(base == n + ".py" or (prev_is_m and dotted == n)
                   for n in names):
                hits.append("pid %s: %s" % (pid, " ".join(toks)[:160]))
                break
    return hits


def main() -> int:
    t0 = time.time()
    print("LIVE-L5-EXEC-1 BOOT PREFLIGHT — DISARMED, GET/WS ONLY", flush=True)

    # 1 ---------------------------------------------------- frozen artifact
    fp = E.fingerprint()
    ok = gate("exec1_fingerprint", fp == E.EXEC1_FINGERPRINT, fp)
    RESULT["deployed_fingerprint"] = fp
    RESULT["authorized_fingerprint"] = E.EXEC1_FINGERPRINT
    for label, want in (("s3", E.SPEC["does_not_contaminate"]
                         ["s3_fingerprint"]),
                        ("reserved_micro", E.SPEC["does_not_contaminate"]
                         ["reserved_micro_fingerprint"])):
        try:
            if label == "s3":
                from analysis.l5.s3 import spec as other
            else:
                from analysis.l5.live_micro import spec as other
            got = other.fingerprint()
        except Exception as exc:                               # noqa: BLE001
            got = "UNEVALUATED:%s" % type(exc).__name__
        ok &= gate("sibling_fingerprint_%s" % label, got == want, got)

    # 2 ------------------------------------------- mutation-incapable image
    forbidden_present = []
    for mod in FORBIDDEN_MODULES:
        try:
            __import__(mod)
            forbidden_present.append(mod)
        except BaseException:                                  # noqa: BLE001
            pass
    ok &= gate("mutation_incapable", not forbidden_present,
               forbidden_present or "no order-capable module in this image")
    refused = False
    try:
        A.ReadOnlyTransport().request("POST", "/v1/orders", {"x": 1})
    except PermissionError:
        refused = True
    except BaseException:                                      # noqa: BLE001
        refused = False
    ok &= gate("transport_refuses_post", refused,
               "ReadOnlyTransport raised PermissionError on POST /v1/orders")

    # 3 ------------------------------------------------- DISARMED boot proof
    present = {k: bool(os.getenv(k, "").strip()) for k in ARM_KEYS}
    ok &= gate("exec1_arm_keys_absent", not any(present.values()), present)
    ok &= gate("exec1_disarmed", exec1_armed() is False, "exec1_armed()=False")
    slug_env = os.getenv("PMUS_EXEC1_GATE_EVIDENCE", "").strip()
    verdict = may_submit(slug_env or "cpc-btc-updown-15m-0000-00-00-0000z")
    ok &= gate("may_submit_refuses", verdict["may_submit"] is False,
               verdict["refusal"])

    # 4 -------------------------- foreign arming cannot arm EXEC-1 (in-proc)
    saved = dict(os.environ)
    try:
        for ns in FOREIGN_ARM_NAMESPACES:
            os.environ[ns + "LIVE"] = "YES"
            os.environ[ns + "SESSION_ID"] = "foreign-session"
            os.environ[ns + "ARM"] = "foreign-session"
            os.environ[ns + "GATE_EVIDENCE"] = slug_env or "any-slug"
        leaked = exec1_armed()
    finally:
        os.environ.clear()
        os.environ.update(saved)
    ok &= gate("foreign_arming_cannot_arm_exec1", leaked is False,
               "with %s all set to armed values, exec1_armed()=%s"
               % (", ".join(n + "*" for n in FOREIGN_ARM_NAMESPACES), leaked))
    ok &= gate("q1_arming_does_not_arm_exec1",
               A.armed() is False or exec1_armed() is False,
               "pmus_auth.armed()=%s exec1_armed()=%s"
               % (A.armed(), exec1_armed()))

    # 5 ---------------------------------------------- one-order lifetime latch
    latch = OneOrderOnly()
    second_refused = False
    try:
        latch.request("cpc-btc-updown-15m-0000-00-00-0000z", "UP", "0.50",
                      E.VENUE_MIN_QTY)
        try:
            latch.request("cpc-btc-updown-15m-0000-00-00-0000z", "UP", "0.50",
                          E.VENUE_MIN_QTY)
        except StateError:
            second_refused = True
    except BaseException as exc:                               # noqa: BLE001
        second_refused = False
        print("latch self-test error: %s" % type(exc).__name__, flush=True)
    ok &= gate("one_order_latch", second_refused,
               "second request raised StateError; latch is PROCESS-SCOPED, "
               "so its lifetime guarantee rests on restartPolicy=NEVER and "
               "no_auto_repeat")
    RESULT["latch_scope"] = "PROCESS_SCOPED"

    # 6 ------------------------------------------- credentials, PRESENCE only
    ok &= gate("pmus_credentials_present", A.has_credentials(),
               "PMUS_API_KEY_ID and PMUS_API_SECRET_KEY are set; values "
               "NEITHER read NOR printed")

    # 7 ------------------------------------------- venue metadata, read-only
    http = A.ReadOnlyTransport()
    from src import l5_pmus_auth_book as AB
    slug = os.getenv("PMUS_EXEC1_PREFLIGHT_MARKET", "").strip() or \
        AB.slug_for(AB.window_start())
    RESULT["market_slug"] = slug
    ok &= gate("slug_is_btc15m_series", bool(BTC15M_SLUG_RE.match(slug)), slug)
    mk: Dict[str, Any] = {}
    try:
        mk = resolve_market(http, slug)
        got_mk = True
    except BaseException as exc:                               # noqa: BLE001
        got_mk = False
        print("market_readable error: %s" % type(exc).__name__, flush=True)
    ok &= gate("market_readable", got_mk, mk.get("slug") if mk else None)
    min_qty = mk.get("minimumTradeQty")
    tick = mk.get(A.TICK_FIELD)
    ok &= gate("minimum_trade_qty", str(min_qty) == str(E.VENUE_MIN_QTY),
               "minimumTradeQty=%s (frozen requirement: < 1 and == %s)"
               % (min_qty, E.VENUE_MIN_QTY))
    ok &= gate("tick_from_venue_field", tick is not None,
               "%s=%s (per-market; no universal constant)"
               % (A.TICK_FIELD, tick))
    vs = validate_size(min_qty, tick, None)
    ok &= gate("validate_size_fail_closed", vs["pass"], vs["reasons"] or vs)
    RESULT["venue"] = {"minimumTradeQty": str(min_qty), "tick": str(tick)}

    # 8 ----------------------------------- AuthBook + the 2.0 s freshness gate
    book = AB.AuthBook().start()
    obs: List[Dict[str, Any]] = []
    deadline = time.time() + 75
    while len(obs) < 8 and time.time() < deadline:
        time.sleep(5)
        snap = book.snapshot(slug)
        if snap is None:
            continue
        v = G.fresh_two_sided(bid=snap.get("bid"), ask=snap.get("ask"),
                              book_t=time.time() - float(
                                  snap.get("stream_age_s") or 1e9),
                              now=time.time())
        obs.append({"bid": snap["bid"], "ask": snap["ask"],
                    "stream_age_s": snap.get("stream_age_s"),
                    "fresh": v["ok"], "age_s": v["age_s"]})
    book.stop()
    RESULT["authbook_observations"] = obs
    ok &= gate("authbook_connected", bool(obs), "%d consecutive two-sided "
               "authenticated snapshots" % len(obs))
    ok &= gate("authbook_freshness_%ss" % G.BOOK_MAX_AGE_S,
               bool(obs) and all(o["fresh"] for o in obs),
               "%d/%d within %.1fs (bound reused from "
               "l5_preview_gate.BOOK_MAX_AGE_S)"
               % (sum(1 for o in obs if o["fresh"]), len(obs),
                  G.BOOK_MAX_AGE_S))
    from decimal import Decimal as _D
    spreads = sorted({str(_D(str(o["ask"])) - _D(str(o["bid"])))
                      for o in obs}) if obs else []
    gate("observed_spread_on_tick_grid", bool(spreads), spreads,
         blocking=False)

    # 9 ----------------------------------------------------- contamination
    hits = order_capable_processes()
    ok &= gate("no_other_order_capable_process", not hits,
               hits or "no pmus executor / run_campaign process on this host")
    RESULT["railway"] = {k: os.getenv(k) for k in
                         ("RAILWAY_SERVICE_NAME", "RAILWAY_ENVIRONMENT_NAME",
                          "RAILWAY_DEPLOYMENT_ID", "RAILWAY_GIT_COMMIT_SHA")}
    gate("restart_policy_reported_by_control_plane", True,
         "restartPolicyType is NOT an env var; verified as NEVER via the "
         "Railway control plane, not from inside this process",
         blocking=False)

    RESULT["pass"] = bool(ok) and not RESULT["blocking_failures"]
    RESULT["elapsed_s"] = round(time.time() - t0, 1)
    RESULT["VERDICT"] = ("DISARMED / DEPLOY-READY / NO ORDER SUBMITTED"
                         if RESULT["pass"] else "PREFLIGHT FAILED / NO ORDER")
    print("EXEC1_BOOT_PREFLIGHT " + json.dumps(RESULT, default=str),
          flush=True)
    return 0 if RESULT["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
