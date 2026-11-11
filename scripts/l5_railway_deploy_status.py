#!/usr/bin/env python3
"""Read a Railway deployment status, or FAIL. Never report an empty one.

WHAT WENT WRONG, AND IT WAS NOT A MALFORMED QUERY. The ad-hoc watcher used
during the 2026-11-06/07 campaigns polled
`https://backboard.railway.com/graphql/v2` with curl and read
`.data.deployments.edges[0].node.status` out of the response. The session's
proxy-injected credential for that host is NOT authorized for those fields,
so the API answered HTTP 200 with:

    {"errors":[{"message":"Not Authorized","path":["deployments"], ...}],
     "data":null}

`data` is null, the jq path yields nothing, and the watcher printed an empty
string -- which was then read as "no deployment found". That produced two
false negatives on deployments that had in fact succeeded. Verified again
while writing this: `me`, `projects` and `deployments` all answer
"Not Authorized" from this session, so there is no query to repair.

    $ curl -sS -X POST .../graphql/v2 -d '{"query":"query{me{id}}"}'
    {"errors":[{"message":"Not Authorized", ...}],"data":null}

THE AUTHORITATIVE PATH IS THE RAILWAY MCP TOOL (`list-deployments`), which
holds its own credential and returns the real record. Checked against it:
deployment 8fc60289-b778-4120-9163-46b42652d5b7, SUCCESS, commit
0be92a32c6b0ff4b25d875a9f5592e2f42319275, branch deploy/l5-issue195-phaseA5,
updated 2026-11-07T00:34:07.459Z.

SO THIS MODULE'S JOB IS TO FAIL LOUDLY. `parse_status` is pure and is the
whole fix: a GraphQL body only yields a status when it carries one, and
EVERY other shape -- an `errors` array, a null `data`, a missing field, an
empty edge list, a non-JSON body -- raises. A deployment watcher that cannot
tell "not authorized" from "not deployed" is worse than no watcher, because
both of its false negatives looked like a clean answer.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Mapping, Tuple

#: Statuses Railway reports. A value outside this set is a schema change and
#: is refused rather than passed through as if it were understood.
KNOWN_STATUSES = frozenset((
    "WAITING", "NEEDS_APPROVAL", "QUEUED", "INITIALIZING", "SKIPPED",
    "BUILDING", "DEPLOYING", "SUCCESS", "FAILED", "REMOVED", "CRASHED",
    "REMOVING", "SLEEPING",
))

#: Terminal-and-healthy. Anything else must not be treated as deployed.
LIVE_STATUSES = frozenset(("SUCCESS",))


class DeployStatusUnreadable(RuntimeError):
    """The body did not contain a status. It is never guessed."""


def parse_status(body: Any) -> Tuple[str, Dict[str, Any]]:
    """Return (status, node) from a Railway `deployments` response, or raise.

    `body` is the raw response text or an already-decoded object.
    """
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "replace")
    if isinstance(body, str):
        if not body.strip():
            raise DeployStatusUnreadable("empty response body")
        try:
            body = json.loads(body)
        except ValueError as exc:
            raise DeployStatusUnreadable(
                "response is not JSON: %s" % (body[:200],)) from exc
    if not isinstance(body, Mapping):
        raise DeployStatusUnreadable("response is not an object: %r"
                                     % (type(body).__name__,))

    # THE CHECK THE WATCHER DID NOT MAKE. GraphQL reports authorization and
    # schema failures in `errors` with HTTP 200, so the status code proves
    # nothing and `data: null` is a FAILURE, not an absence.
    errs = body.get("errors")
    if errs:
        msgs = []
        for e in (errs if isinstance(errs, list) else [errs]):
            if isinstance(e, Mapping):
                msgs.append(str(e.get("message") or e))
            else:
                msgs.append(str(e))
        raise DeployStatusUnreadable("graphql errors: " + "; ".join(msgs))

    if "data" not in body:
        raise DeployStatusUnreadable("response carries no data key")
    data = body.get("data")
    if data is None:
        raise DeployStatusUnreadable("data is null with no errors reported")
    if not isinstance(data, Mapping):
        raise DeployStatusUnreadable("data is not an object")

    dep = data.get("deployments")
    if not isinstance(dep, Mapping):
        raise DeployStatusUnreadable("data.deployments missing or not an "
                                     "object")
    edges = dep.get("edges")
    if not isinstance(edges, list):
        raise DeployStatusUnreadable("data.deployments.edges is not a list")
    if not edges:
        # A REAL ANSWER, AND STILL NOT A STATUS. The service may genuinely
        # have no deployment; the caller must handle that explicitly rather
        # than receive "" and compare it to "SUCCESS".
        raise DeployStatusUnreadable("no deployments returned for this "
                                     "service/environment")
    first = edges[0]
    if not isinstance(first, Mapping):
        raise DeployStatusUnreadable("edge is not an object")
    node = first.get("node")
    if not isinstance(node, Mapping):
        raise DeployStatusUnreadable("edge carries no node object")
    status = node.get("status")
    if not isinstance(status, str) or not status.strip():
        raise DeployStatusUnreadable("node carries no status string")
    status = status.strip()
    if status not in KNOWN_STATUSES:
        raise DeployStatusUnreadable("unknown status %r -- schema changed"
                                     % (status,))
    return status, dict(node)


def is_live(status: str) -> bool:
    """SUCCESS only. BUILDING and DEPLOYING are not yet running code."""
    return status in LIVE_STATUSES


def main(argv: list) -> int:
    """Read a response body from a file or stdin and print the status.

    Deliberately does NOT make the request. Raw GraphQL from this session is
    unauthorized, so a watcher that fetched here would only ever reproduce
    the original false negative. Pipe in a body captured by an authorized
    client, or use the Railway MCP `list-deployments` tool directly.
    """
    raw = (open(argv[1], encoding="utf-8").read() if len(argv) > 1
           else sys.stdin.read())
    try:
        status, node = parse_status(raw)
    except DeployStatusUnreadable as exc:
        sys.stderr.write("DEPLOY STATUS UNREADABLE: %s\n" % (exc,))
        return 2
    meta = node.get("meta") or {}
    print(json.dumps({
        "status": status,
        "live": is_live(status),
        "id": node.get("id"),
        "createdAt": node.get("createdAt"),
        "commitHash": (meta.get("commitHash")
                       if isinstance(meta, Mapping) else None),
        "branch": (meta.get("branch")
                   if isinstance(meta, Mapping) else None),
    }, indent=2, sort_keys=True))
    return 0 if is_live(status) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
