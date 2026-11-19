"""A real Railway adapter for the launch orchestrator. Read-mostly.

WHY THIS EXISTS AND WHAT IT IS ALLOWED TO DO. `l5_launch` composes gates
and decides nothing about money; it needs an I/O partner that can read the
runtime's own telemetry and, on an explicit owner approval, write a
narrowly bounded set of variables. This is that partner and nothing more.

AUTHENTICATION. Railway project tokens authenticate with the
`Project-Access-Token` header -- NOT `Authorization: Bearer`, which returns
"Project Token not found". The token is scoped to one project and one
environment, and `projectToken { projectId environmentId }` is the cheap
query that proves which, so `verify_scope()` refuses a client pointed at a
project the token does not cover.

THE WRITE SURFACE IS CLOSED BY DEFAULT AND TWICE BOUNDED.

  1. A client is constructed with `allow_writes=False`. `set_variables`
     raises `WriteRefused` unless the caller said otherwise, so a reading
     tool cannot write by accident.
  2. Every name is checked against an ALLOWLIST passed in by the caller --
     in practice `l5_launch.WRITABLE` -- and anything else is refused by
     name. That list contains no limit, no taker switch and no capital
     figure.

So there are two independent reasons a limit cannot be written here, and
neither is "we remembered not to".

IT NEVER CLEARS A HALT ON ITS OWN. `L5_LIVE_HALT` is not in any default
allowlist. A caller that wants it must name it, and `l5_launch.apply_plan`
only does so behind an explicit reason plus every
`HALT_RESET_PRECONDITIONS` satisfied.

IT HOLDS NO VENUE CREDENTIAL and cannot submit, cancel or amend an order.
It reads logs and variables and writes variables. That is all it can do.
"""
from __future__ import annotations

import json
import os
import time
import subprocess
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

API = "https://backboard.railway.com/graphql/v2"
TOKEN_ENV = "RAILWAY_PROJECT_TOKEN"

#: labels the runner emits, each a JSON payload after one space.
HEARTBEAT = "L5_LIVE_HB"
RUNTIME_CONFIG = "L5_EFFECTIVE_RUNTIME_CONFIG"
AUTH_PREFLIGHT = "L5_AUTHORIZATION_PREFLIGHT"
WINDOW_CONFIRMED = "L5_LIVE_WINDOW_CONFIRMED"
DECISION = "L5_LIVE_DECISION"
LATENCY = "L5_COMPLETION_LATENCY"


class RailwayError(RuntimeError):
    """The API answered, and the answer was an error."""


class WriteRefused(RuntimeError):
    """Nothing was sent. The reason names what was refused and why."""


def _post_urllib(url: str, body: Mapping[str, Any],
                 headers: Mapping[str, str],
                 timeout: float) -> Dict[str, Any]:
    """The stdlib transport. Kept for environments without curl."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers=dict({"Content-Type": "application/json"}, **headers))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _post_curl(url: str, body: Mapping[str, Any],
               headers: Mapping[str, str],
               timeout: float) -> Dict[str, Any]:
    """The DEFAULT transport, and it is curl for a measured reason.

    This environment routes egress through an agent proxy, and curl is one
    of the clients preconfigured for it. The same request that curl
    completes with HTTP 200 comes back 403 from `urllib.request`, which
    does not negotiate that proxy the same way. Rather than reimplement
    the proxy handshake, the adapter uses the client that is already
    configured for it -- and the transport stays INJECTED, so no test ever
    needs either.

    The body goes in on stdin rather than argv so a token-bearing header
    is the only secret on the command line, and `--fail-with-body` makes
    an HTTP error an exception carrying the server's own explanation.
    """
    args = ["curl", "-sS", "--fail-with-body", "-X", "POST", str(url),
            "--max-time", str(int(max(1.0, timeout)))]
    for k, v in dict({"Content-Type": "application/json"},
                     **dict(headers)).items():
        args += ["-H", "%s: %s" % (k, v)]
    args += ["--data-binary", "@-"]
    proc = subprocess.run(args, input=json.dumps(body).encode("utf-8"),
                          capture_output=True)
    out = (proc.stdout or b"").decode("utf-8", "replace")
    if proc.returncode != 0 and not out.strip():
        raise RailwayError(
            "curl failed (%d): %s" % (proc.returncode,
                                      (proc.stderr or b"").decode(
                                          "utf-8", "replace")[:200]))
    try:
        return json.loads(out or "{}")
    except ValueError as exc:
        raise RailwayError(
            "unparseable answer (%d): %s" % (proc.returncode,
                                             out[:200])) from exc


#: the default. Swapped wholesale in tests.
_post = _post_curl


class RailwayClient:
    """One project, one environment, one service.

    `http` is injected so every test drives the real request-building and
    error handling without a network. A test that stubs the client itself
    proves nothing about the queries.
    """

    def __init__(self, *, project_id: str, environment_id: str,
                 service_id: str,
                 token: Optional[str] = None,
                 allow_writes: bool = False,
                 writable: Iterable[str] = (),
                 http: Optional[Callable[..., Dict[str, Any]]] = None,
                 timeout_s: float = 30.0,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.project_id = str(project_id)
        self.environment_id = str(environment_id)
        self.service_id = str(service_id)
        self._token = token if token is not None else os.getenv(TOKEN_ENV)
        self.allow_writes = bool(allow_writes)
        self.writable = frozenset(str(w) for w in writable)
        self._http = http or _post
        self.timeout_s = float(timeout_s)
        self._sleep = sleeper
        self.requests = 0

    # ------------------------------------------------------------ transport
    def _gql(self, query: str, variables: Optional[Mapping[str, Any]] = None
             ) -> Dict[str, Any]:
        if not self._token:
            raise RailwayError("no %s in the environment" % TOKEN_ENV)
        self.requests += 1
        try:
            out = self._http(API, {"query": query,
                                   "variables": dict(variables or {})},
                             {"Project-Access-Token": str(self._token)},
                             self.timeout_s)
        except urllib.error.URLError as exc:
            raise RailwayError("transport failed: %s" % exc) from exc
        if not isinstance(out, Mapping):
            raise RailwayError("unparseable answer")
        if out.get("errors"):
            msgs = [str((e or {}).get("message"))
                    for e in (out.get("errors") or ())]
            raise RailwayError("; ".join(m for m in msgs if m)[:300])
        data = out.get("data")
        if not isinstance(data, Mapping):
            raise RailwayError("answer carried no data")
        return dict(data)

    # --------------------------------------------------------------- scope
    def verify_scope(self) -> Dict[str, Any]:
        """Prove the token covers the project and environment we name.

        A token for ANOTHER project would otherwise read and write the
        wrong service while every query looked healthy.
        """
        d = self._gql("query { projectToken { projectId environmentId } }")
        tok = dict((d.get("projectToken") or {}))
        ok = (str(tok.get("projectId")) == self.project_id
              and str(tok.get("environmentId")) == self.environment_id)
        if not ok:
            raise RailwayError(
                "token scope mismatch: token covers project %s / env %s, "
                "client names %s / %s" % (tok.get("projectId"),
                                          tok.get("environmentId"),
                                          self.project_id,
                                          self.environment_id))
        return {"ok": True, "project_id": self.project_id,
                "environment_id": self.environment_id}

    # --------------------------------------------------------- deployments
    def latest_deployment(self) -> Dict[str, Any]:
        d = self._gql(
            "query($pid:String!,$eid:String!,$sid:String!){"
            "deployments(first:1,input:{projectId:$pid,environmentId:$eid,"
            "serviceId:$sid}){edges{node{id status createdAt meta}}}}",
            {"pid": self.project_id, "eid": self.environment_id,
             "sid": self.service_id})
        edges = ((d.get("deployments") or {}).get("edges") or [])
        if not edges:
            raise RailwayError("the service reports no deployments")
        node = dict((edges[0] or {}).get("node") or {})
        meta = dict(node.get("meta") or {})
        return {"id": node.get("id"), "status": node.get("status"),
                "createdAt": node.get("createdAt"),
                "commitHash": meta.get("commitHash"),
                "branch": meta.get("branch"), "reason": meta.get("reason")}

    def wait_for_deployment(self, *, expect_sha: Optional[str] = None,
                            timeout_s: float = 420.0,
                            poll_s: float = 10.0,
                            not_before: Optional[str] = None
                            ) -> Dict[str, Any]:
        """Poll until a deployment is SUCCESS, or FAIL CLOSED.

        TERMINAL FAILURES ARE NOT RETRIED. FAILED, CRASHED and REMOVED are
        answers, not hiccups, and polling through them would turn a broken
        deploy into a timeout nobody reads.

        `expect_sha` is checked on the SUCCESS, because a deployment that
        succeeded with the wrong commit is the exact confusion a launch
        must not proceed through. `not_before` refuses a stale SUCCESS left
        over from the previous deployment -- without it, a write that never
        triggered a rebuild would look like a completed one.
        """
        deadline = time.time() + float(timeout_s)
        last: Dict[str, Any] = {}
        while True:
            last = self.latest_deployment()
            status = str(last.get("status") or "")
            fresh = (not_before is None
                     or str(last.get("createdAt") or "") > str(not_before))
            if status in ("FAILED", "CRASHED", "REMOVED"):
                raise RailwayError(
                    "deployment %s is %s" % (last.get("id"), status))
            if status == "SUCCESS" and fresh:
                got = str(last.get("commitHash") or "")
                if expect_sha and got != str(expect_sha):
                    raise RailwayError(
                        "deployment %s succeeded on %s, expected %s"
                        % (last.get("id"), got[:12], str(expect_sha)[:12]))
                return dict(last, waited_s=round(
                    float(timeout_s) - max(0.0, deadline - time.time()), 1))
            if time.time() >= deadline:
                raise RailwayError(
                    "timed out after %.0fs waiting for a fresh SUCCESS; "
                    "last was %s %s" % (timeout_s, last.get("id"), status))
            self._sleep(float(poll_s))

    # ---------------------------------------------------------------- logs
    def logs(self, *, deployment_id: Optional[str] = None,
             filter: Optional[str] = None, limit: int = 20
             ) -> List[Dict[str, Any]]:
        did = deployment_id or str(self.latest_deployment().get("id") or "")
        d = self._gql(
            "query($d:String!,$f:String,$n:Int){deploymentLogs("
            "deploymentId:$d,filter:$f,limit:$n){timestamp message}}",
            {"d": did, "f": filter, "n": int(limit)})
        return [dict(r) for r in (d.get("deploymentLogs") or [])
                if isinstance(r, Mapping)]

    def newest_labelled(self, label: str, *, limit: int = 6,
                        deployment_id: Optional[str] = None
                        ) -> Optional[Dict[str, Any]]:
        """The newest `LABEL {json}` line, parsed. None if there is none.

        FILTERED ON THE LABEL AND THEN CHECKED AGAIN. The log filter is a
        substring match, so a line that merely MENTIONS the label -- the
        heartbeat's own `labels` text names other lines -- comes back too.
        Requiring the label at the START is what separates the artifact
        from a line talking about it, and getting that wrong once cost a
        read of the wrong payload.
        """
        rows = self.logs(filter=label, limit=limit,
                         deployment_id=deployment_id)
        best: Optional[Dict[str, Any]] = None
        for row in rows:
            msg = str(row.get("message") or "")
            if not msg.startswith(label + " "):
                continue
            if best is None or str(row.get("timestamp") or "") > str(
                    best.get("timestamp") or ""):
                best = row
        if best is None:
            return None
        try:
            payload = json.loads(str(best["message"])[len(label) + 1:])
        except ValueError:
            return None
        return {"timestamp": best.get("timestamp"), "payload": payload}

    def heartbeat(self, **kw) -> Optional[Mapping[str, Any]]:
        got = self.newest_labelled(HEARTBEAT, **kw)
        return None if got is None else got["payload"]

    def runtime_config(self, **kw) -> Optional[Mapping[str, Any]]:
        got = self.newest_labelled(RUNTIME_CONFIG, **kw)
        return None if got is None else got["payload"]

    def auth_preflight(self, **kw) -> Optional[Mapping[str, Any]]:
        """The MARKET-SPECIFIC authorization verdict, from the heartbeat.

        TWO SOURCES, AND THE RIGHT ONE IS THE HEARTBEAT. The standalone
        `L5_AUTHORIZATION_PREFLIGHT` line is emitted ONCE AT STARTUP, when
        the transport has no slug yet -- so it evaluates the campaign
        DOCUMENT alone and reports coverage as NOT_EVALUATED. Its own
        labels say so. Measured here: a 100-line lookback on the running
        deployment finds it not at all, because it has aged out.
        
        The heartbeat's `authorization_preflight` block is the same verdict
        recomputed every minute WITH the current slug, and it carries
        `evaluated_market`. That is the one a launch needs, and treating
        the startup line as readiness is the mistake that
        `auth_preflight_is_market_specific` exists to refuse.

        So the heartbeat is preferred and the startup line is only a
        fallback. None when neither is readable, which `prepare` treats as
        BLOCKED rather than as absent-and-fine.

        IT MAY NOT CARRY `local_operator_halt`. That row ships in
        `2c7c20b7`; a deployment older than it reports a verdict with the
        gap this programme lost a window to. `l5_launch.prepare` therefore
        reads the halt itself, from the variable, rather than trusting
        whatever the deployed preflight happens to include.
        """
        hb = self.heartbeat(**kw)
        block = (hb or {}).get("authorization_preflight")
        if isinstance(block, Mapping):
            return dict(block)
        got = self.newest_labelled(AUTH_PREFLIGHT, **kw)
        return None if got is None else got["payload"]

    def venue_window_bounds(self, market: str, **kw
                            ) -> Optional[Mapping[str, Any]]:
        """The venue's OWN confirmation for this slug, or None.

        `L5_LIVE_WINDOW_CONFIRMED` is only emitted when
        `OBS.verify_alignment` confirms the venue's `startTime` matches
        ours, so its presence IS the venue confirmation. The slug is
        matched exactly: a confirmation for a neighbouring window is not a
        confirmation for this one.

        EXPECT None MOST OF THE TIME, and that is not a fault. The bounds
        read happens on the ARMING path for the market the probe is bound
        to, and `deploymentLogs` is scoped to ONE deployment -- so a
        service bound to a different or expired window emits none at all.
        Measured on deployment `41defbb9`: zero matches in a 200-line
        lookback while bound to an expired slug.

        A launch therefore cannot certify venue confirmation AHEAD of the
        window. `l5_launch.prepare` reports absent bounds as UNKNOWN and
        BLOCKS on them, which is honest, and the arming boundary refuses
        WINDOW_BOUNDS_UNKNOWN independently -- so a mistaken slug still
        cannot trade.
        """
        rows = self.logs(filter=WINDOW_CONFIRMED, limit=40, **kw)
        for row in sorted(rows, key=lambda r: str(r.get("timestamp") or ""),
                          reverse=True):
            msg = str(row.get("message") or "")
            if not msg.startswith(WINDOW_CONFIRMED + " "):
                continue
            try:
                p = json.loads(msg[len(WINDOW_CONFIRMED) + 1:])
            except ValueError:
                continue
            if str(p.get("market")) == str(market):
                b = dict(p.get("bounds") or {})
                return b or None
        return None

    # ----------------------------------------------------------- variables
    def variables(self) -> Dict[str, Any]:
        d = self._gql(
            "query($pid:String!,$eid:String!,$sid:String!){"
            "variables(projectId:$pid,environmentId:$eid,serviceId:$sid)}",
            {"pid": self.project_id, "eid": self.environment_id,
             "sid": self.service_id})
        got = d.get("variables")
        if not isinstance(got, Mapping):
            raise RailwayError("variables did not come back as a mapping")
        return dict(got)

    def set_variables(self, values: Mapping[str, str]) -> Dict[str, Any]:
        """Write variables. CLOSED BY DEFAULT and bounded by `writable`.

        A Railway variable write triggers a redeploy of the service, which
        is how the values take effect -- so this is reported as what it is
        rather than as a quiet config poke.
        """
        if not self.allow_writes:
            raise WriteRefused(
                "this client was built read-only; construct it with "
                "allow_writes=True to write")
        if not values:
            raise WriteRefused("nothing to write")
        bad = [k for k in values if k not in self.writable]
        if bad:
            raise WriteRefused(
                "refused names outside the allowlist: %s (allowed: %s)"
                % (sorted(bad), sorted(self.writable)))
        self._gql(
            "mutation($input:VariableCollectionUpsertInput!){"
            "variableCollectionUpsert(input:$input)}",
            {"input": {"projectId": self.project_id,
                       "environmentId": self.environment_id,
                       "serviceId": self.service_id,
                       "variables": {str(k): str(v)
                                     for k, v in values.items()},
                       "replace": False}})
        return {"written": sorted(values),
                "triggers_redeploy": True,
                "labels": ("a variable write redeploys the service; the "
                           "values take effect on the new container")}
