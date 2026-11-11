"""A deployment watcher that cannot tell "not authorized" from "not
deployed" produces clean-looking false negatives. It did, twice.

The body below is the VERBATIM response the session's credential gets from
`https://backboard.railway.com/graphql/v2`, captured 2026-11-07. HTTP 200,
`data: null`, and the reason only in `errors` -- which the ad-hoc watcher
never read.
"""
from __future__ import annotations

import json

import pytest

from scripts.l5_railway_deploy_status import (
    DeployStatusUnreadable, KNOWN_STATUSES, is_live, parse_status)

NOT_AUTHORIZED = json.dumps({
    "errors": [{"message": "Not Authorized",
                "locations": [{"line": 1, "column": 41}],
                "path": ["deployments"],
                "extensions": {"code": "INTERNAL_SERVER_ERROR"},
                "traceId": "6293570205959339900"}],
    "data": None})

#: The authoritative record for the deployment this branch was written
#: against, as the Railway MCP `list-deployments` tool reports it.
AUTHORITATIVE = json.dumps({"data": {"deployments": {"edges": [{"node": {
    "id": "8fc60289-b778-4120-9163-46b42652d5b7",
    "status": "SUCCESS",
    "createdAt": "2026-11-07T00:32:40.392Z",
    "meta": {
        "commitHash": "0be92a32c6b0ff4b25d875a9f5592e2f42319275",
        "branch": "deploy/l5-issue195-phaseA5"}}}]}}})


def test_the_real_unauthorized_body_raises_instead_of_reading_empty():
    with pytest.raises(DeployStatusUnreadable) as ei:
        parse_status(NOT_AUTHORIZED)
    assert "Not Authorized" in str(ei.value)


def test_the_authoritative_record_parses_and_agrees_with_railway():
    status, node = parse_status(AUTHORITATIVE)
    assert status == "SUCCESS"
    assert is_live(status)
    assert node["meta"]["commitHash"].startswith("0be92a32c")
    assert node["meta"]["branch"] == "deploy/l5-issue195-phaseA5"


@pytest.mark.parametrize("body,why", [
    ("", "empty body"),
    ("   ", "whitespace"),
    ("<html>502 Bad Gateway</html>", "not json"),
    ("[]", "not an object"),
    ('{"data": null}', "null data, no errors"),
    ('{}', "no data key"),
    ('{"data": {}}', "no deployments"),
    ('{"data": {"deployments": {}}}', "no edges"),
    ('{"data": {"deployments": {"edges": []}}}', "empty edges"),
    ('{"data": {"deployments": {"edges": [{}]}}}', "no node"),
    ('{"data": {"deployments": {"edges": [{"node": {}}]}}}', "no status"),
    ('{"data": {"deployments": {"edges": [{"node": '
     '{"status": ""}}]}}}', "blank status"),
    ('{"data": {"deployments": {"edges": [{"node": '
     '{"status": "PROBABLY_FINE"}}]}}}', "unknown status"),
    ('{"errors": [{"message": "rate limited"}], "data": '
     '{"deployments": {"edges": [{"node": {"status": "SUCCESS"}}]}}}',
     "errors win even when data looks complete"),
])
def test_every_unreadable_shape_raises_and_none_returns_a_status(body, why):
    with pytest.raises(DeployStatusUnreadable):
        parse_status(body)


@pytest.mark.parametrize("status", sorted(KNOWN_STATUSES))
def test_only_success_counts_as_deployed(status):
    body = json.dumps({"data": {"deployments": {"edges": [
        {"node": {"id": "x", "status": status}}]}}})
    got, _ = parse_status(body)
    assert got == status
    assert is_live(got) is (status == "SUCCESS")


def test_building_and_deploying_are_not_live():
    """The state that mattered: a variable write is not in force until the
    new container is running, so in-flight is never 'deployed'."""
    for s in ("BUILDING", "DEPLOYING", "QUEUED", "INITIALIZING",
              "NEEDS_APPROVAL", "WAITING"):
        assert not is_live(s), s


def test_the_module_makes_no_request_of_its_own():
    """Fetching here would only reproduce the false negative, since raw
    GraphQL from this session is unauthorized for every field tried.

    Checked on the PARSED module, not on its text: the docstring quotes the
    very `curl` command that failed, and a substring scan would match its
    own evidence.
    """
    import ast
    import inspect

    from scripts import l5_railway_deploy_status as M
    tree = ast.parse(inspect.getsource(M))
    banned = {"requests", "urllib", "urllib3", "httpx", "http",
              "socket", "subprocess", "aiohttp"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] not in banned, a.name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned, \
                node.module
