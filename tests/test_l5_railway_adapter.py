"""The Railway adapter: requests built right, errors handled, writes closed.

THE TRANSPORT IS STUBBED, NOT THE CLIENT. A test that replaces the client
proves nothing about the queries it sends, and the queries are the part
that can silently address the wrong service. So these drive the real
`_gql`, the real query strings and the real error handling, with only the
HTTP callable replaced.

VERIFIED AGAINST THE LIVE API SEPARATELY. The header is
`Project-Access-Token` -- `Authorization: Bearer` returns "Project Token
not found" -- and the token is scoped to one project and one environment,
which `verify_scope` checks rather than assumes.
"""
from __future__ import annotations

import json

import pytest

from src import l5_railway as RW

PID = "50fb7357-e2bc-4d04-a49e-71eb5d73b0d1"
EID = "f3642d27-26dc-4e21-9170-bc76290c076f"
SID = "87f1834e-530e-4fc4-a310-91c8d3add196"


class Stub:
    """Records every request and answers from a queue."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append({"url": url, "body": body, "headers": headers,
                           "timeout": timeout})
        if not self.answers:
            raise AssertionError("no stubbed answer left")
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a


def client(*answers, **kw):
    http = Stub(*answers)
    kw.setdefault("token", "tok-123")
    c = RW.RailwayClient(project_id=PID, environment_id=EID,
                         service_id=SID, http=http,
                         sleeper=lambda s: None, **kw)
    return c, http


# =====================================================================
# AUTH AND SCOPE
# =====================================================================

def test_it_sends_the_project_access_token_header():
    c, http = client({"data": {"projectToken": {"projectId": PID,
                                                "environmentId": EID}}})
    c.verify_scope()
    h = http.calls[0]["headers"]
    assert h["Project-Access-Token"] == "tok-123"
    assert "Authorization" not in h, (
        "Bearer is refused by Railway for project tokens")
    assert http.calls[0]["url"] == RW.API


def test_both_real_transports_add_the_json_content_type():
    """The client passes only the auth header; each transport supplies the
    content type, so BOTH have to, or swapping the transport changes the
    request."""
    import inspect
    for fn in (RW._post_curl, RW._post_urllib):
        src = inspect.getsource(fn)
        assert '"Content-Type": "application/json"' in src, fn.__name__


def test_a_token_for_another_project_is_refused_by_name():
    c, _ = client({"data": {"projectToken": {"projectId": "other",
                                             "environmentId": EID}}})
    with pytest.raises(RW.RailwayError) as ei:
        c.verify_scope()
    assert "scope mismatch" in str(ei.value)


def test_a_token_for_another_environment_is_refused():
    c, _ = client({"data": {"projectToken": {"projectId": PID,
                                             "environmentId": "other"}}})
    with pytest.raises(RW.RailwayError):
        c.verify_scope()


def test_no_token_at_all_raises_before_any_request(monkeypatch):
    monkeypatch.delenv(RW.TOKEN_ENV, raising=False)
    http = Stub()
    c = RW.RailwayClient(project_id=PID, environment_id=EID,
                         service_id=SID, http=http, token=None)
    with pytest.raises(RW.RailwayError) as ei:
        c.latest_deployment()
    assert RW.TOKEN_ENV in str(ei.value)
    assert http.calls == []


# =====================================================================
# ERRORS ARE ERRORS
# =====================================================================

def test_a_graphql_error_becomes_an_exception_carrying_the_message():
    c, _ = client({"errors": [{"message": "Not Authorized"}], "data": None})
    with pytest.raises(RW.RailwayError) as ei:
        c.latest_deployment()
    assert "Not Authorized" in str(ei.value)


def test_an_answer_with_no_data_is_an_error_not_an_empty_result():
    c, _ = client({"foo": "bar"})
    with pytest.raises(RW.RailwayError):
        c.latest_deployment()


def test_a_service_with_no_deployments_is_an_error():
    c, _ = client({"data": {"deployments": {"edges": []}}})
    with pytest.raises(RW.RailwayError) as ei:
        c.latest_deployment()
    assert "no deployments" in str(ei.value)


# =====================================================================
# DEPLOYMENTS
# =====================================================================

def dep(status="SUCCESS", sha="a" * 40, created="2026-11-10T00:00:00Z"):
    return {"data": {"deployments": {"edges": [{"node": {
        "id": "dep-1", "status": status, "createdAt": created,
        "meta": {"commitHash": sha, "branch": "b", "reason": "deploy"}}}]}}}


def test_the_deployment_query_names_our_project_env_and_service():
    c, http = client(dep())
    c.latest_deployment()
    v = http.calls[0]["body"]["variables"]
    assert (v["pid"], v["eid"], v["sid"]) == (PID, EID, SID)


def test_the_commit_hash_is_lifted_out_of_meta():
    c, _ = client(dep(sha="b" * 40))
    assert c.latest_deployment()["commitHash"] == "b" * 40


@pytest.mark.parametrize("status", ["FAILED", "CRASHED", "REMOVED"])
def test_a_terminal_status_is_not_retried(status):
    """Polling through a broken deploy turns it into a timeout nobody
    reads."""
    c, http = client(dep(status=status))
    with pytest.raises(RW.RailwayError) as ei:
        c.wait_for_deployment(timeout_s=99.0, poll_s=0.0)
    assert status in str(ei.value)
    assert len(http.calls) == 1, "it polled a terminal status"


def test_it_waits_through_building_and_returns_on_success():
    c, http = client(dep(status="BUILDING"), dep(status="DEPLOYING"),
                     dep(status="SUCCESS"))
    out = c.wait_for_deployment(timeout_s=99.0, poll_s=0.0)
    assert out["status"] == "SUCCESS"
    assert len(http.calls) == 3


def test_a_success_on_the_WRONG_commit_is_refused():
    c, _ = client(dep(sha="b" * 40))
    with pytest.raises(RW.RailwayError) as ei:
        c.wait_for_deployment(expect_sha="a" * 40, timeout_s=99.0,
                              poll_s=0.0)
    assert "expected" in str(ei.value)


def test_a_STALE_success_is_not_mistaken_for_a_fresh_one():
    """Without `not_before`, a write that never triggered a rebuild looks
    like a completed one."""
    c, _ = client(dep(created="2026-11-10T00:00:00Z"))
    with pytest.raises(RW.RailwayError) as ei:
        c.wait_for_deployment(timeout_s=0.0, poll_s=0.0,
                              not_before="2026-11-10T00:00:00Z")
    assert "timed out" in str(ei.value)


def test_a_fresh_success_after_the_marker_is_accepted():
    c, _ = client(dep(created="2026-11-10T00:00:05Z"))
    out = c.wait_for_deployment(timeout_s=99.0, poll_s=0.0,
                                not_before="2026-11-10T00:00:00Z")
    assert out["status"] == "SUCCESS"


def test_it_times_out_rather_than_polling_forever():
    c, _ = client(dep(status="BUILDING"))
    with pytest.raises(RW.RailwayError) as ei:
        c.wait_for_deployment(timeout_s=0.0, poll_s=0.0)
    assert "timed out" in str(ei.value)


# =====================================================================
# LOGS AND LABELS
# =====================================================================

def logs(*messages):
    return {"data": {"deploymentLogs": [
        {"timestamp": "2026-11-10T00:00:%02dZ" % i, "message": m}
        for i, m in enumerate(messages)]}}


def test_the_newest_labelled_line_wins():
    c, _ = client(dep(), logs('L5_LIVE_HB {"n":1}', 'L5_LIVE_HB {"n":2}'))
    got = c.newest_labelled(RW.HEARTBEAT)
    assert got["payload"]["n"] == 2


def test_a_line_that_merely_MENTIONS_the_label_is_not_the_artifact():
    """The heartbeat's own `labels` text names other lines, so a substring
    filter returns them too. Requiring the label at the START is what
    separates the artifact from a line talking about it."""
    c, _ = client(dep(), logs(
        'SOMETHING_ELSE {"labels":"see the L5_LIVE_HB line"}'))
    assert c.newest_labelled(RW.HEARTBEAT) is None


def test_an_unparseable_payload_is_None_not_a_crash():
    c, _ = client(dep(), logs('L5_LIVE_HB {not json'))
    assert c.newest_labelled(RW.HEARTBEAT) is None


def test_the_heartbeat_helper_returns_the_payload():
    c, _ = client(dep(), logs('L5_LIVE_HB {"utc":"x","market":"m"}'))
    assert c.heartbeat()["market"] == "m"


def test_auth_preflight_prefers_the_heartbeats_market_specific_block():
    """The standalone line is emitted once at STARTUP with no slug, so it
    evaluates the document alone. The heartbeat's block carries
    `evaluated_market` and is the one a launch needs."""
    c, _ = client(dep(), logs(json.dumps(
        {"utc": "x", "authorization_preflight":
         {"ok": False, "evaluated_market": "slug-1"}}).join(
             ["L5_LIVE_HB ", ""])))
    got = c.auth_preflight()
    assert got["evaluated_market"] == "slug-1"


def test_venue_bounds_match_the_slug_EXACTLY():
    """A confirmation for a neighbouring window is not a confirmation for
    this one."""
    c, _ = client(dep(), logs(
        'L5_LIVE_WINDOW_CONFIRMED {"market":"other","bounds":{"source":"VENUE"}}'))
    assert c.venue_window_bounds("mine") is None


def test_venue_bounds_come_back_when_the_slug_matches():
    c, _ = client(dep(), logs(
        'L5_LIVE_WINDOW_CONFIRMED {"market":"mine","bounds":'
        '{"source":"VENUE","start":"s","end":"e"}}'))
    b = c.venue_window_bounds("mine")
    assert b["source"] == "VENUE" and b["start"] == "s"


# =====================================================================
# THE WRITE SURFACE IS CLOSED TWICE OVER
# =====================================================================

def test_a_read_only_client_refuses_every_write():
    c, http = client()
    with pytest.raises(RW.WriteRefused) as ei:
        c.set_variables({"L5_TS_MARKET": "x"})
    assert "read-only" in str(ei.value)
    assert http.calls == [], "it sent something anyway"


def test_a_write_client_still_refuses_names_outside_the_allowlist():
    c, http = client({"data": {"variableCollectionUpsert": True}},
                     allow_writes=True, writable=("L5_TS_MARKET",))
    with pytest.raises(RW.WriteRefused) as ei:
        c.set_variables({"L5_MAX_TOTAL_EXPOSURE_USD": "99"})
    assert "allowlist" in str(ei.value)
    assert http.calls == []


def test_one_forbidden_name_refuses_the_WHOLE_batch():
    """All or nothing: a partial write would leave the configuration in a
    state nobody approved."""
    c, http = client({"data": {"variableCollectionUpsert": True}},
                     allow_writes=True, writable=("L5_TS_MARKET",))
    with pytest.raises(RW.WriteRefused):
        c.set_variables({"L5_TS_MARKET": "ok",
                         "L5_TS_COMPLETION_CROSS": "YES"})
    assert http.calls == []


def test_an_allowlisted_write_is_sent_and_reports_the_redeploy():
    c, http = client({"data": {"variableCollectionUpsert": True}},
                     allow_writes=True,
                     writable=("L5_TS_MARKET", "L5_SHADOW_ONLY"))
    out = c.set_variables({"L5_TS_MARKET": "slug", "L5_SHADOW_ONLY": "0"})
    assert out["written"] == ["L5_SHADOW_ONLY", "L5_TS_MARKET"]
    assert out["triggers_redeploy"] is True
    body = http.calls[0]["body"]
    assert "variableCollectionUpsert" in body["query"]
    inp = body["variables"]["input"]
    assert (inp["projectId"], inp["environmentId"],
            inp["serviceId"]) == (PID, EID, SID)
    assert inp["variables"] == {"L5_TS_MARKET": "slug",
                                "L5_SHADOW_ONLY": "0"}
    assert inp["replace"] is False, "a write must not clear other variables"


def test_an_empty_write_is_refused():
    c, http = client(allow_writes=True, writable=("L5_TS_MARKET",))
    with pytest.raises(RW.WriteRefused):
        c.set_variables({})
    assert http.calls == []


def test_the_launchers_writable_set_is_what_the_client_is_given():
    """Defence in depth: the allowlist is the launcher's, so there is one
    definition and the transport enforces it again."""
    from src import l5_launch as L
    c, _ = client(allow_writes=True, writable=L.WRITABLE)
    assert c.writable == frozenset(L.WRITABLE)
    for name in L.FORBIDDEN:
        if name == "L5_LIVE_HALT":
            continue
        assert name not in c.writable, name


def test_values_are_stringified_so_a_number_cannot_become_null():
    c, http = client({"data": {"variableCollectionUpsert": True}},
                     allow_writes=True, writable=("L5_TS_MARKET",))
    c.set_variables({"L5_TS_MARKET": 7})
    assert http.calls[0]["body"]["variables"]["input"][
        "variables"]["L5_TS_MARKET"] == "7"


# =====================================================================
# VARIABLES, READ
# =====================================================================

def test_variables_come_back_as_a_mapping():
    c, _ = client({"data": {"variables": {"A": "1", "B": "2"}}})
    assert c.variables() == {"A": "1", "B": "2"}


def test_a_non_mapping_variables_answer_is_an_error():
    c, _ = client({"data": {"variables": ["A"]}})
    with pytest.raises(RW.RailwayError):
        c.variables()


def test_the_request_count_is_observable():
    c, _ = client(dep(), dep())
    c.latest_deployment()
    c.latest_deployment()
    assert c.requests == 2
