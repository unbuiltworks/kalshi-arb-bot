"""A ONE-TIME, OWNER-GATED, AUDITABLE reset of a latched HALT.

WHAT THIS IS NOT. It is not automatic clearing. `HaltAsserter` still has no
clear method, the watchdog sweep still refuses to write `halt: false` over a
latch, and the executor still cannot touch the latch at all. None of that is
weakened here. This module is a separate admin entrypoint with its own
main(); `scripts/l5_halt_watchdog.py` does not import it, so there is no code
path by which a sweep, a restart or the executor can reach it.

WHY IT EXISTS. The latch is a file on the watchdog's volume, and a Railway
volume mounts to exactly one service, so the only process that can write it
is one running in that service. "Clear it by hand with the credentials
directly" therefore meant: no record of who cleared it, no check that the
condition had actually gone, and nothing stopping a second clear. This makes
the same owner action one-time, precondition-checked and audited, which is
strictly more protection than a hand edit, not less.

THE FOUR GATES, all fail-closed. Any one of them unmet writes nothing.

  1. INTENT. L5_HALT_RESET_CONFIRM must equal CONFIRM_PHRASE exactly, and
     L5_HALT_RESET_WHO must name a human. No default, no truthy shortcut.

  2. THE LATCH YOU REVIEWED, AND ONLY THAT ONE. L5_HALT_RESET_EXPECT must be
     the sha256 of the current halt.json bytes. If the watchdog re-latched
     for a NEW reason between the review and the reset, the digest moved and
     this refuses. That is the property a hand edit cannot have: you cannot
     clear a halt you have not read.

  3. ONE TIME. L5_HALT_RESET_ID is recorded in an append-only audit log on
     the same volume and refused if already present. So an admin start
     command left in place by mistake clears nothing on the next restart --
     the second boot refuses RESET_ID_ALREADY_CONSUMED.

  4. THE CONDITION IS ACTUALLY GONE, confirmed independently of the executor
     whose failure latched the halt:
       a. the VENUE reports zero open orders (authoritative, credentialed);
       b. the VENUE reports no non-zero position;
       c. the executor's ownership registry is READABLE, FRESH and EMPTY --
          readable-but-stale is refused, because a report that stopped
          arriving is the wedged signal, not an empty book;
       d. the operator attests watchdog health and the absence of any
          unresolved alarm, naming the evidence, and that attestation is
          recorded verbatim in the audit entry.
     (a)-(c) are machine-verified here. (d) is operator-attested and is
     labelled as such in the record rather than dressed up as a check.

ORDER OF WRITES. The audit entry is appended and fsynced BEFORE the latch is
cleared. If the process dies between the two, the record says a reset was
attempted and the halt is still latched -- the safe asymmetry. The reverse
order could clear a halt leaving no trace.

NOTHING HERE PLACES, PRICES OR CANCELS ANYTHING.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple, Sequence

CONFIRM_PHRASE = "RESET THE LATCHED HALT"

CONFIRM_ENV = "L5_HALT_RESET_CONFIRM"
WHO_ENV = "L5_HALT_RESET_WHO"
ID_ENV = "L5_HALT_RESET_ID"
EXPECT_ENV = "L5_HALT_RESET_EXPECT"
ATTEST_ENV = "L5_HALT_RESET_ATTEST"
#: the latch's OWN reason, retyped by the owner from the inspect output. The
#: digest can be copy-pasted without reading anything; this cannot.
REASON_ENV = "L5_HALT_RESET_CONFIRM_REASON"
#: read-only dry run: print the latch, its digest and every precondition,
#: and write NOTHING. This is how the operator obtains the digest that gate 2
#: requires, and it is what makes "the latch you reviewed" a real review
#: rather than a formality -- the volume is not readable from anywhere else.
INSPECT_ENV = "L5_HALT_RESET_INSPECT"
REGISTRY_ENV = "L5_ORDER_REGISTRY_URL"

#: OFFLINE-LANE ATTESTATION. All THREE are required together and the
#: default (all unset) is the strict behaviour: every configured registry
#: must answer. See `_offline_lane_exemption`.
OFFLINE_LANE_ENV = "L5_HALT_RESET_OFFLINE_LANE"
OFFLINE_PREFIX_ENV = "L5_HALT_RESET_OFFLINE_MARKET_PREFIX"
OFFLINE_EXPECT_ENV = "L5_HALT_RESET_OFFLINE_EXPECT"
STATE_ENV = "L5_WATCHDOG_STATE_DIR"

#: the watchdog's GET-only halt server. Set this (and L5_HALT_RESET_VIA_HTTP)
#: to run the READ-ONLY inspect from a process that does NOT hold the volume,
#: so obtaining the digest never requires stopping the production watchdog.
#: `l5_halt_server.do_GET` returns the stored bytes VERBATIM, so the sha256 of
#: the HTTP body is the sha256 of the file -- the digest gate 2 demands. The
#: clear cannot work this way; see _require_clearable_store.
HALT_URL_ENV = "L5_HALT_URL"
VIA_HTTP_ENV = "L5_HALT_RESET_VIA_HTTP"

#: baked into deploy/l5_halt_reset/Dockerfile and present in NO other image.
#: A reset refuses without it, because on 2026-10-19 a `redeploy` silently
#: reused the WATCHDOG build after dockerfilePath had been pointed at the
#: reset image: the config said one thing and the running container was
#: another. An image that cannot name itself does not clear a halt.
IMAGE_MARKER_ENV = "L5_RESET_IMAGE"
IMAGE_MARKER_VALUE = "l5_halt_reset"
#: the watchdog image is the SECOND legitimate home, and only because the
#: latch lives on its volume and a Railway volume attaches to exactly one
#: service. Railway runs `preDeployCommand` in the NEW deployment's container
#: with the volume mounted while the CURRENT watchdog keeps sweeping, so the
#: clear happens without the independent watchdog ever going offline. Kept a
#: DISTINCT value so a wrong image is still detectable and the audit entry
#: records which image performed the clear.
IMAGE_MARKER_WATCHDOG = "l5_halt_watchdog"
ACCEPTED_IMAGE_MARKERS = (IMAGE_MARKER_VALUE, IMAGE_MARKER_WATCHDOG)

#: PRE-DEPLOY MODE: a refusal must EXIT 0.
#:
#: Railway fails a deployment whose preDeployCommand exits non-zero. A spent
#: reset id refusing RESET_ID_ALREADY_CONSUMED is the NORMAL steady state once
#: a reset has been used, so a leftover pre-deploy command returning 2 would
#: block every future watchdog deployment -- the watchdog could never be
#: updated or restarted again. In this mode every refusal is logged loudly and
#: exits 0: the deployment proceeds, the latch stays latched, and nothing is
#: cleared. Refusing is the expected outcome here, not an error.
PRE_DEPLOY_ENV = "L5_HALT_RESET_PRE_DEPLOY"

#: the audit log, beside the latch on the same volume so they cannot be
#: separated by a redeploy.
AUDIT_KEY = "l5/halt_resets.jsonl"

#: a registry report older than this is STALE, and a stale report is not
#: evidence of an empty book. Deliberately tighter than the watchdog's own
#: report bound: this is a one-shot operator action, not a 24/7 sweep.
MAX_REGISTRY_AGE_S = 90.0


class ResetRefused(RuntimeError):
    """Nothing was written. The reason is the message."""


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def _require_intent() -> Tuple[str, str, str, str]:
    """Gate 1 and the identifiers. Raises ResetRefused."""
    confirm = _env(CONFIRM_ENV)
    if confirm != CONFIRM_PHRASE:
        raise ResetRefused(
            "CONFIRM_PHRASE_MISMATCH: %s must be exactly %r" %
            (CONFIRM_ENV, CONFIRM_PHRASE))
    who = _env(WHO_ENV)
    if not who:
        raise ResetRefused("NO_OPERATOR: %s must name a human" % WHO_ENV)
    rid = _env(ID_ENV)
    if not rid:
        raise ResetRefused("NO_RESET_ID: %s must be a unique id" % ID_ENV)
    expect = _env(EXPECT_ENV).lower()
    if len(expect) != 64 or any(c not in "0123456789abcdef" for c in expect):
        raise ResetRefused(
            "NO_EXPECTED_DIGEST: %s must be the sha256 hex of the halt "
            "record you reviewed" % EXPECT_ENV)
    return who, rid, expect, _env(ATTEST_ENV)


def _require_reset_image() -> str:
    """The running image must be the reset image. Raises ResetRefused.

    THE FAILURE THIS CATCHES, observed 2026-10-19. `redeploy` reuses the
    previous BUILD, so pointing dockerfilePath at deploy/l5_halt_reset and
    redeploying restarted the WATCHDOG instead; the swap landed only on the
    rebuild that followed. Service config is therefore NOT evidence of what
    is running. This marker is, because it travels inside the image.
    """
    got = _env(IMAGE_MARKER_ENV)
    if got not in ACCEPTED_IMAGE_MARKERS:
        raise ResetRefused(
            "WRONG_IMAGE: %s must be one of %r and is %r. The running "
            "container is not a reset-capable image, whatever the service "
            "config says -- a redeploy reuses the previous build."
            % (IMAGE_MARKER_ENV, list(ACCEPTED_IMAGE_MARKERS), got or None))
    return got


def _require_clearable_store(store: Any) -> None:
    """A clear needs the VOLUME. Raises ResetRefused for a remote store.

    A Railway volume attaches to exactly one service, so only a process in
    the watchdog's own service can write the latch or append the audit log.
    `HttpHaltStore` has no write path by design. Refusing EARLY and by name
    beats failing later inside the audit append: the ordering guarantee is
    that the audit entry is fsynced BEFORE the latch is cleared, and a store
    that cannot do either must never get that far.
    """
    from src import l5_halt_gate as HG
    if isinstance(store, HG.HttpHaltStore):
        raise ResetRefused(
            "RESET_REQUIRES_VOLUME_BACKED_STORE: this process reached the "
            "latch over HTTP, which is READ-ONLY. The inspect phase may run "
            "anywhere; the clear must run where the volume is mounted.")
    for verb in ("get", "put"):
        if not callable(getattr(store, verb, None)):
            raise ResetRefused(
                "RESET_REQUIRES_VOLUME_BACKED_STORE: the store cannot %s"
                % verb)


def read_latch(store: Any) -> Tuple[bytes, Dict[str, Any], str]:
    """Gate 2's input: the raw bytes, the parsed record, and the digest."""
    from src import l5_halt_gate as HG
    try:
        raw = store.get(HG.HALT_KEY)
    except BaseException as exc:                               # noqa: BLE001
        raise ResetRefused(
            "LATCH_UNREADABLE: %s -- an unreadable latch is never cleared"
            % type(exc).__name__)
    if raw is None:
        raise ResetRefused(
            "NO_LATCH_PRESENT: there is nothing to reset. An absent record "
            "already reads as halted to the gate; the watchdog's own "
            "heartbeat is what makes it permissive again.")
    try:
        rec = json.loads(raw)
    except BaseException:                                      # noqa: BLE001
        raise ResetRefused("LATCH_UNPARSEABLE: refusing to clear a torn "
                           "record whose meaning cannot be established")
    if not isinstance(rec, dict):
        raise ResetRefused("LATCH_NOT_AN_OBJECT")
    if rec.get("halt") is False:
        raise ResetRefused(
            "NOT_LATCHED: the record already says halt: false, so there is "
            "no latch to reset")
    return raw, rec, hashlib.sha256(raw).hexdigest()


def audit_entries(store: Any) -> List[Dict[str, Any]]:
    """Every prior reset. An unreadable log is treated as UNKNOWN, not empty."""
    try:
        raw = store.get(AUDIT_KEY)
    except BaseException as exc:                               # noqa: BLE001
        raise ResetRefused(
            "AUDIT_LOG_UNREADABLE: %s -- without it the one-time guarantee "
            "cannot be established" % type(exc).__name__)
    if raw is None:
        return []
    out: List[Dict[str, Any]] = []
    for line in (raw.decode("utf-8", "replace")).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except BaseException:                                  # noqa: BLE001
            raise ResetRefused(
                "AUDIT_LOG_CORRUPT: a line will not parse, so a prior reset "
                "may be unrecorded and the one-time guard cannot hold")
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _require_unconsumed(store: Any, rid: str) -> List[Dict[str, Any]]:
    """Gate 3, keyed on the ID ALONE.

    THE DEFECT THIS REPLACES. The guard used to refuse only when a prior
    entry had `cleared is True`, and the entry was written cleared=True
    BEFORE the latch write. So an entry asserted a clear that had not
    happened (a failed latch write left the log claiming one forever), and
    the guard could not coexist with honest recording: the moment an
    interrupted attempt was recorded truthfully as cleared=False, that
    reset_id became REPLAYABLE. "One-time" held only because the record lied.

    The id is now spent by the ATTEMPT, not by its outcome. An interrupted
    reset is recorded CLEAR_ATTEMPTED and its id is spent; the operator
    re-inspects to learn whether the latch moved, then mints a NEW id. Never
    re-running a clear whose effect is unknown is the fail-closed direction.

    WHAT ONE-TIME MEANS: each reset_id is usable exactly once, which is what
    makes an admin start command left in place harmless on restart. It does
    NOT prevent a deliberate new reset with a new id, and must not -- a later
    genuine halt has to be clearable.
    """
    prior = audit_entries(store)
    for rec in prior:
        if str(rec.get("reset_id") or "") == rid:
            raise ResetRefused(
                "RESET_ID_ALREADY_CONSUMED: %r was used at %s with outcome "
                "%r. The id is spent by the ATTEMPT, not by its success. If "
                "that attempt was interrupted, re-run INSPECT to establish "
                "whether the latch moved, then use a NEW id."
                % (rid, rec.get("t"), rec.get("outcome")))
    return prior


def managed_lane_claims(urls: Optional[List[str]] = None, *,
                        opener: Any = None) -> set:
    """Venue ids attested by FRESH managed lanes (L5_WATCHDOG_MANAGED_LANE_URLS).

    ONE definition, shared with the watchdog's age exemption: a lane that
    publishes /l5/live_orders.json with readable=true, manages_own_resting=
    true and an as_of_utc <= 60s old. Unreachable/stale lanes contribute
    nothing (fail closed). Owner 2026-11-14: sports two-sided + BTC5m share
    the account; without this a reset needs the sports lane paused."""
    from src import l5_watchdog as W
    if urls is None:
        urls = W._managed_lanes_from_env()
    if not urls:
        return set()
    w = W.Watchdog(None, None, managed_lanes=list(urls), lane_opener=opener)
    ids, _notes = w.managed_lane_ids()
    return ids


def venue_evidence(transport: Any, *,
                   claimed: Optional[Mapping[str, Any]] = None,
                   lane_ids: Optional[set] = None
                   ) -> Dict[str, Any]:
    """Gate 4(a): no UNRESOLVED L5-OWNED order, on a SHARED account.

    THE PRECONDITION THIS REPLACES, and why it was wrong. It required the
    whole account to be FLAT -- zero open orders anywhere and no non-zero
    position. On an account where the owner trades manually alongside L5 that
    is PERMANENTLY UNSATISFIABLE: any manual position or resting manual order
    would block every future reset, so the administrative procedure would
    have been unusable exactly when it was needed. A manual trade is normal
    activity, not a hazard.

    WHAT IS CHECKED NOW -- ownership, not emptiness. Each WORKING order at the
    venue is classified, and only some classes block:

      * in our durable ownership registry            -> L5_OWNED, BLOCKS.
        This is the original hazard: an L5 order still resting means the
        executor is not managing it, which is what latched the halt.
      * not in the registry, venue says MANUAL       -> attributed manual,
        reported and ALLOWED. Positively attributed by the venue's own
        manualOrderIndicator, not inferred from our silence.
      * not in the registry, venue says AUTOMATIC    -> BLOCKS. An automated
        order we hold no record of may be OURS and unrecorded, which is
        precisely the unmanaged-resting-order hazard. Never guessed away.
      * indicator missing or unreadable              -> BLOCKS.

    That last pair is requirement 6 in code: uncertain ownership fails
    closed. "Not in our registry" alone is NOT taken as "not ours" here --
    the watchdog may safely read it that way before CANCELLING (cancelling a
    stranger's order is the worse error), but for CLEARING A HALT the safe
    reading is the opposite, and the two must not be confused.

    POSITIONS ARE RISK CONTEXT, NOT A GATE. They are read and reported so
    account-wide exposure is visible to the approver, and they no longer
    block: a manual position is not evidence of an executor malfunction. An
    unreadable positions read is still reported as unreadable rather than as
    flat.
    """
    if claimed is None:
        raise ResetRefused(
            "OWNERSHIP_UNAVAILABLE: ownership must be established before any "
            "order can be classified. Guessing is refused.")
    try:
        # a plain signed GET, so a READ-ONLY transport satisfies this. The
        # reset must never need a transport that can cancel.
        body = transport.request("GET", "/v1/orders/open") or {}
        rows = body.get("orders") if isinstance(body, Mapping) else None
        rows = [r for r in (rows or []) if isinstance(r, Mapping)]
    except BaseException as exc:                               # noqa: BLE001
        raise ResetRefused(
            "VENUE_OPEN_ORDERS_UNREADABLE: %s -- an unread venue is never "
            "taken as flat" % type(exc).__name__)
    from src import l5_watchdog as W
    working = [r for r in rows if not W.is_terminal(r)]
    l5_owned, manual, unattributed, lane = [], [], [], []
    lane_ids = set(lane_ids or ())
    for r in working:
        oid = str(r.get("id") or "").strip()
        ind = str(r.get("manualOrderIndicator") or "")
        if oid and oid in lane_ids and oid not in claimed:
            # attributed to a FRESH managed lane that is managing it now
            lane.append({"id": oid, "market": str(r.get("marketSlug") or "")})
        elif oid and oid in claimed:
            l5_owned.append({"id": oid, "market": str(r.get("marketSlug")
                                                      or "")})
        elif ind == W.AUTOMATIC or not ind:
            unattributed.append({"id": oid or None, "indicator": ind or None,
                                 "market": str(r.get("marketSlug") or "")})
        else:
            manual.append({"id": oid or None, "indicator": ind,
                           "market": str(r.get("marketSlug") or "")})
    if l5_owned:
        raise ResetRefused(
            "L5_OWNED_ORDERS_STILL_OPEN: %d order(s) %r are in our own "
            "ownership registry and still working. The hazard that latched "
            "the halt has NOT gone."
            % (len(l5_owned), [o["id"] for o in l5_owned][:8]))
    if unattributed:
        raise ResetRefused(
            "UNATTRIBUTED_AUTOMATED_ORDERS: %d working order(s) %r are not "
            "in our registry and the venue does not call them MANUAL. An "
            "automated order we hold no record of may be ours and "
            "unrecorded, so this fails closed rather than guessing."
            % (len(unattributed), [o["id"] for o in unattributed][:8]))
    positions, pos_err = _positions(transport)
    return {"l5_owned_working_orders": 0,
            "venue_working_orders_seen": len(working),
            #: ADDITIVE, reporting only. The market of every working order,
            #: so an offline-lane attestation can be verified against the
            #: venue instead of against the lane's own silence. Adding a
            #: key cannot change what blocks.
            "venue_working_markets": sorted(
                {str(r.get("marketSlug") or "") for r in working}),
            "owner_manual_orders_allowed": manual,
            "managed_lane_orders_allowed": lane,
            "account_positions": positions if pos_err is None else None,
            "account_positions_error": pos_err,
            "source": "/v1/orders/open and /v1/portfolio/positions, signed",
            "labels": ("ownership-based, NOT account-flatness. Manual orders "
                       "and positions are attributed and reported as "
                       "account-wide risk context; only an L5-owned or "
                       "unattributed-automated working order blocks. "
                       "Positions do not block and are never taken as flat "
                       "when unreadable.")}


def _positions(transport: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Non-zero rows, or an error string. Handles BOTH venue shapes.

    `positions` has been observed as an OBJECT keyed by market slug and as an
    array; iterating the object yields its string KEYS, which is how an
    earlier close-out raised TypeError on p["market"]. Both are handled.
    """
    try:
        body = transport.request("GET", "/v1/portfolio/positions") or {}
    except BaseException as exc:                               # noqa: BLE001
        return ([], type(exc).__name__)
    if not isinstance(body, Mapping):
        return ([], "positions_body_not_an_object")
    raw = body.get("positions")
    rows: List[Dict[str, Any]] = []
    if isinstance(raw, Mapping):
        for slug, row in raw.items():
            if isinstance(row, Mapping):
                rows.append(dict(row, market=row.get("market") or slug))
    elif isinstance(raw, list):
        rows = [dict(r) for r in raw if isinstance(r, Mapping)]
    elif raw is None:
        rows = []
    else:
        return ([], "positions_unexpected_shape")
    out: List[Dict[str, Any]] = []
    for r in rows:
        qty = r.get("quantity", r.get("netQuantity", r.get("size")))
        try:
            if qty is not None and abs(float(str(qty))) > 0:
                out.append({"market": str(r.get("market") or ""),
                            "quantity": str(qty)})
        except BaseException:                                  # noqa: BLE001
            # a quantity we cannot read is not a zero
            out.append({"market": str(r.get("market") or ""),
                        "quantity": "UNREADABLE:%r" % (qty,)})
    return (out, None)


def ownership(url: str, *, opener=None) -> Dict[str, Any]:
    """The ids L5 durably claims, or REFUSE. Requirement 6 in code.

    OwnershipOracle.claimed() returns {} on any doubt -- an unreachable
    registry, an unparseable body, a report saying readable: false. For the
    WATCHDOG that empty answer is safe, because it means "cancel nothing".
    Here it is the opposite: an empty answer would make every working order
    look unowned, and the reset would wave through an L5 order still resting.
    So an empty claim set is only accepted when the registry itself is
    positively readable, which registry_evidence establishes separately; a
    claim set we could not read at all refuses here.
    """
    if not url:
        raise ResetRefused(
            "NO_REGISTRY_URL: %s must name the executor's GET-only report. "
            "Ownership cannot be established without it, and ownership is "
            "what distinguishes an L5 order from the owner's manual one."
            % REGISTRY_ENV)
    from src import l5_watchdog as W
    try:
        oracle = W.OwnershipOracle(url, opener=opener) if opener is not None \
            else W.OwnershipOracle(url)
        return dict(oracle.claimed())
    except BaseException as exc:                               # noqa: BLE001
        raise ResetRefused(
            "OWNERSHIP_UNREADABLE: %s -- ownership must be established, "
            "never guessed" % type(exc).__name__)


def _offline_lane_exemption(url: str, refusal: str, *,
                            halt_digest: Optional[str],
                            venue_markets: Optional[Sequence[str]]
                            ) -> Dict[str, Any]:
    """May THIS registry's unreachability be excused? Fail closed.

    STRICT IS THE DEFAULT. With the three attestation variables unset this
    returns a refusal and `registry_evidence` re-raises, so behaviour is
    exactly as it was: every configured registry must answer.

    WHY IT EXISTS. `L5_ORDER_REGISTRY_URL` names every executor on the
    account. A lane that is DELIBERATELY stopped -- l5-btc5m-live exits on
    ENVELOPE_EXPIRED and its registry server dies with the process -- is
    then indistinguishable from a wedged one, so after the multi-registry
    fix no reset could pass while any lane was intentionally down. The
    answer is not to trust silence; it is to require the owner to say
    which lane is down and to VERIFY that claim against the venue.

    FOUR CONDITIONS, ALL REQUIRED.

      1. The owner names the EXACT lane url, matching a configured one
         byte for byte. A prefix or a hostname is not enough.
      2. The owner names the HALT FINGERPRINT this exemption is for, and
         it must equal the latch being cleared. An exemption therefore
         cannot be left in the environment and silently reused on the
         next, different halt -- which is the failure mode that makes
         standing exemptions dangerous.
      3. The owner names the lane's MARKET PREFIX, and the venue must show
         NO working order in any market carrying it. This is the
         independent check: the lane's own `submission_disabled` is
         unreadable by definition here, so the venue stands in for it. A
         lane with nothing working cannot be mid-submission in a way that
         this clear could strand.
      4. The refusal must be UNREACHABILITY. A registry that answers and
         reports live orders, staleness, or submission NOT disabled is
         telling us something disqualifying, and that always blocks.

    AN UNREACHABLE REGISTRY ALONE NEVER QUALIFIES: condition 4 is
    necessary and on its own insufficient, and 1-3 cannot be satisfied by
    anything the dead lane does or fails to do.
    """
    named = _env(OFFLINE_LANE_ENV)
    prefix = _env(OFFLINE_PREFIX_ENV)
    expect = _env(OFFLINE_EXPECT_ENV)
    if not (named or prefix or expect):
        return {"exempt": False, "why": "NO_OFFLINE_ATTESTATION"}
    if not (named and prefix and expect):
        return {"exempt": False, "why": "INCOMPLETE_OFFLINE_ATTESTATION",
                "detail": ("all three of %s, %s and %s are required together"
                           % (OFFLINE_LANE_ENV, OFFLINE_PREFIX_ENV,
                              OFFLINE_EXPECT_ENV))}
    if named != url:
        return {"exempt": False, "why": "OFFLINE_LANE_NOT_THIS_ONE",
                "attested": named, "refused": url}
    if not str(refusal).startswith("REGISTRY_UNREADABLE"):
        #: the lane ANSWERED. Whatever it said disqualifies the clear.
        return {"exempt": False, "why": "LANE_ANSWERED_AND_DISQUALIFIES",
                "refusal": str(refusal)[:160]}
    if not halt_digest:
        return {"exempt": False, "why": "NO_HALT_FINGERPRINT_AVAILABLE"}
    if expect != halt_digest:
        return {"exempt": False, "why": "OFFLINE_ATTESTATION_WRONG_HALT",
                "attested": expect, "current": halt_digest}
    if venue_markets is None:
        #: never read as "nothing is working". Unknown is not empty.
        return {"exempt": False, "why": "VENUE_MARKETS_UNREADABLE"}
    hits = sorted(m for m in venue_markets if str(m).startswith(prefix))
    if hits:
        return {"exempt": False, "why": "LANE_STILL_HAS_WORKING_ORDERS",
                "market_prefix": prefix, "markets": hits[:8]}
    return {"exempt": True, "lane": url, "market_prefix": prefix,
            "halt_sha256": halt_digest,
            "venue_working_markets_checked": len(venue_markets),
            "labels": ("owner-attested offline lane, verified against the "
                       "venue: no working order carries this lane's market "
                       "prefix. The lane's own submission lock was NOT "
                       "readable and was NOT assumed.")}


def registry_evidence(url: str, *, opener=None,
                      halt_digest: Optional[str] = None,
                      venue_markets: Optional[Sequence[str]] = None
                      ) -> Dict[str, Any]:
    """Gate 4(c). EVERY configured registry READABLE, FRESH and EMPTY.

    COMMA-SEPARATED URLS, READ THE SAME WAY THE SWEEP READS THEM. This
    took the whole `L5_ORDER_REGISTRY_URL` value as one url while
    `l5_watchdog.registry_urls` split it on commas. With both executors
    configured the gate then tried to GET a host named
    "http://a:8090,http://b:8090", which cannot resolve, so the reset
    refused REGISTRY_UNREADABLE however healthy the registries were. The
    2026-11-17 23:09Z clear could only be made to pass by temporarily
    rewriting the variable to a single url -- a configuration change made
    to satisfy a guard, which is how a precondition stops meaning what it
    says.

    EVERY REGISTRY MUST ATTEST, and that is the opposite direction from
    the sweep's ownership union, deliberately. There, a claim only ever
    GRANTS the power to cancel, so a union is the safe direction and a
    missing claim costs a cancellation. Here the question is "could any
    executor still send?", so one unreadable, stale, non-empty or
    unlocked registry must refuse the clear. Anything else would let a
    second executor's silence read as its consent.

    With one url configured this is byte-for-byte the previous behaviour.
    """
    from src import l5_watchdog as W
    urls = W.registry_urls(url)
    if not urls:
        raise ResetRefused(
            "NO_REGISTRY_URL: %s must name the executor's GET-only report, "
            "which is the independent witness that it holds no live order"
            % REGISTRY_ENV)
    if len(urls) > 1:
        #: each one in turn, by the same rules; the FIRST refusal stands,
        #: so the message names the registry that failed rather than a
        #: merged verdict nobody can act on.
        seen = []; exempt = []
        for one in urls:
            try:
                seen.append(_one_registry_evidence(one, opener=opener))
            except ResetRefused as exc:
                ex = _offline_lane_exemption(
                    one, str(exc), halt_digest=halt_digest,
                    venue_markets=venue_markets)
                if ex.get("exempt") is True:
                    exempt.append(ex)
                    continue
                #: name the registry that failed, and why the exemption did
                #: not apply. A merged verdict would tell the operator the
                #: clear was refused without saying which executor to go
                #: and look at, or what would have excused it.
                raise ResetRefused(
                    "%s [registry %s of %d: %s; offline-lane exemption not "
                    "applied: %s]"
                    % (exc, urls.index(one) + 1, len(urls), one,
                       ex.get("why")))
        if not seen:
            #: every configured registry was exempted, so nothing attested
            #: anything. An exemption excuses ONE dead lane, never the
            #: absence of all evidence.
            raise ResetRefused(
                "NO_REGISTRY_ATTESTED: every configured registry was "
                "exempted, so no executor published a submission lock. An "
                "exemption excuses a dead lane, not the whole check.")
        return {"registry_readable": True,
                "registry_age_s": max(r["registry_age_s"] for r in seen),
                "registry_live_orders": 0,
                "registry_url": ",".join(urls),
                "registries": seen,
                "registry_count": len(seen),
                "offline_lanes_exempted": exempt,
                "submission_disabled": True}
    return _one_registry_evidence(urls[0], opener=opener)


def _one_registry_evidence(url: str, *, opener=None) -> Dict[str, Any]:
    """Gate 4(c) for exactly ONE registry. READABLE, FRESH and EMPTY."""
    from src import l5_watchdog as W
    src = W.RegistryOrderSource(url, opener=opener)
    try:
        doc = src.fetch()
    except BaseException as exc:                               # noqa: BLE001
        raise ResetRefused(
            "REGISTRY_UNREADABLE: %s -- a report that stopped arriving is "
            "the WEDGED signal, not an empty book" % exc)
    age = src.report_age_s()
    if age is None:
        raise ResetRefused(
            "REGISTRY_AGE_UNKNOWN: a report whose age cannot be established "
            "is treated as stale")
    if age > MAX_REGISTRY_AGE_S:
        raise ResetRefused(
            "REGISTRY_STALE: report is %.1fs old, bound is %.1fs"
            % (age, MAX_REGISTRY_AGE_S))
    rows = src.open_orders()
    if rows:
        raise ResetRefused(
            "EXECUTOR_STILL_CLAIMS_LIVE_ORDERS: %r"
            % ([str(r.get("id") or "") for r in rows][:8],))
    # SUBMISSION MUST BE PROVEN DISABLED, not assumed, and this is the one
    # check that holds THROUGHOUT the clear rather than at an instant.
    #
    # Zero open orders says what is RESTING now. It does not say that nothing
    # new can be sent while the latch is being cleared and the watchdog
    # briefly restarts. `submission_disabled` is the executor publishing its
    # own structural lock, read from the same L5_SHADOW_ONLY that
    # authorize_arming step 1 refuses on, so the published answer cannot
    # drift from the real one.
    #
    # ABSENT IS NOT PERMISSION. An executor too old to publish the field is
    # an executor whose lock cannot be established, and that refuses -- the
    # same rule this module applies to an unreadable registry and an
    # unattributed venue order.
    locked = (doc or {}).get("submission_disabled")
    if locked is not True:
        raise ResetRefused(
            "SUBMISSION_NOT_PROVEN_DISABLED: the executor reports "
            "submission_disabled=%r. A halt is never cleared while the "
            "executor could send a new order; set L5_SHADOW_ONLY=1 and "
            "confirm the field before retrying." % (locked,))
    return {"registry_readable": True, "registry_age_s": round(age, 3),
            "registry_live_orders": 0, "registry_url": url,
            "submission_disabled": True}


def _append_audit(store: Any, prior: List[Dict[str, Any]],
                  entry: Dict[str, Any]) -> None:
    """Append-only. Rewrites the whole object because the store's verb is PUT.

    Every prior line is preserved verbatim; a store that cannot PUT raises,
    and the caller then clears nothing.
    """
    lines = [json.dumps(r, sort_keys=True) for r in prior]
    lines.append(json.dumps(entry, sort_keys=True))
    store.put(AUDIT_KEY, ("\n".join(lines) + "\n").encode())


def inspect(*, store: Any, transport: Optional[Any], registry_url: str,
            registry_opener=None) -> Dict[str, Any]:
    """READ-ONLY. Never writes, never clears, and never raises ResetRefused
    for a failed precondition -- it REPORTS each one so the operator sees the
    whole picture in a single run. The only hard error is a latch that cannot
    be read at all."""
    raw, rec, digest = read_latch(store)
    out: Dict[str, Any] = {
        "latch": rec, "sha256": digest,
        "set_this_as": {EXPECT_ENV: digest,
                        REASON_ENV: str(rec.get("reason") or "")},
        "preconditions": {},
        "labels": ("READ-ONLY. Nothing was written. Pass the sha256 above as "
                   "%s to clear THIS latch and no other." % EXPECT_ENV),
    }
    claimed = None
    claimed_error = None
    try:
        claimed = ownership(registry_url, opener=registry_opener)
    except ResetRefused as exc:
        claimed_error = str(exc)
    out["l5_claimed_order_ids"] = (None if claimed is None
                                   else sorted(claimed.keys()))
    out["ownership_error"] = claimed_error

    def _venue():
        if transport is None:
            raise ResetRefused(
                "NO_VENUE_CREDENTIALS: this boot cannot perform the "
                "authenticated venue checks. The latch digest and registry "
                "evidence below are still valid.")
        return venue_evidence(transport, claimed=claimed,
                              lane_ids=managed_lane_claims())

    for name, fn in (("venue", _venue),
                     ("registry", lambda: registry_evidence(
                         registry_url, opener=registry_opener))):
        try:
            out["preconditions"][name] = {"ok": True, "evidence": fn()}
        except ResetRefused as exc:
            out["preconditions"][name] = {"ok": False, "refused": str(exc)}
    try:
        prior = audit_entries(store)
        out["prior_resets"] = [
            {"t": r.get("t"), "reset_id": r.get("reset_id"),
             "who": r.get("who"), "cleared": r.get("cleared")} for r in prior]
    except ResetRefused as exc:
        out["prior_resets_error"] = str(exc)
    out["would_clear"] = all(
        v.get("ok") for v in out["preconditions"].values())
    return out


def perform_reset(*, store: Any, transport: Any, registry_url: str,
                  now: Optional[dt.datetime] = None,
                  registry_opener=None) -> Dict[str, Any]:
    """All four gates, then audit, then clear. Raises ResetRefused."""
    who, rid, expect, attest = _require_intent()
    # GATE 3 FIRST, before the latch is read. A spent id must refuse
    # identically whatever the latch is doing: if an earlier attempt already
    # cleared it, read_latch would answer NOT_LATCHED and hide the fact that
    # this id spent itself.
    prior = _require_unconsumed(store, rid)
    raw, rec, digest = read_latch(store)
    if digest != expect:
        raise ResetRefused(
            "LATCH_CHANGED_SINCE_REVIEW: current sha256 is %s, you named %s. "
            "The watchdog may have re-latched for a DIFFERENT reason (%r). "
            "Re-read the latch and review it again."
            % (digest, expect, rec.get("reason")))
    # The owner confirms WHAT is being cleared, not just a hash.
    want_reason = _env(REASON_ENV)
    have_reason = str(rec.get("reason") or "")
    if not want_reason:
        raise ResetRefused(
            "NO_CONFIRMED_REASON: %s must repeat the latch's own reason "
            "(this latch says %r)." % (REASON_ENV, have_reason))
    if want_reason != have_reason:
        raise ResetRefused(
            "CONFIRMED_REASON_MISMATCH: you confirmed %r but the latch says "
            "%r" % (want_reason, have_reason))
    if not attest:
        raise ResetRefused(
            "NO_ATTESTATION: %s must name the watchdog-health and "
            "no-open-alarm evidence you checked. It is recorded as an "
            "operator attestation, not as a machine check." % ATTEST_ENV)
    claimed = ownership(registry_url, opener=registry_opener)
    venue = venue_evidence(transport, claimed=claimed,
                           lane_ids=managed_lane_claims())
    #: the halt fingerprint and the venue's own working markets are what
    #: make an offline-lane attestation verifiable rather than trusted.
    registry = registry_evidence(
        registry_url, opener=registry_opener, halt_digest=digest,
        venue_markets=venue.get("venue_working_markets"))
    t = (now or _utc()).isoformat()
    entry = {
        # the honest pre-write state: an ATTEMPT, not an accomplished clear
        "t": t, "reset_id": rid, "who": who,
        "outcome": "CLEAR_ATTEMPTED", "cleared": False,
        "prior_latch": rec, "prior_latch_sha256": digest,
        "confirmed_reason": have_reason,
        "machine_verified": {"venue": venue, "registry": registry},
        "operator_attested": {
            "text": attest,
            "labels": ("watchdog health and absence of an unresolved alarm "
                       "are ATTESTED by the operator, not verified here")},
        "labels": ("a one-time owner-gated reset of a latched HALT. The "
                   "watchdog and the executor have no code path to this."),
    }
    # the record lands BEFORE the clear: dying between the two leaves an
    # audited attempt and a still-latched halt, which is the safe asymmetry.
    _append_audit(store, prior, entry)
    from src import l5_halt_gate as HG
    cleared = {"halt": False, "t": t,
               "who": "owner-reset:%s" % who, "reset_id": rid,
               "cleared_reason": str(rec.get("reason") or ""),
               "labels": ("written by the one-time owner reset. The "
                          "watchdog's heartbeat keeps it fresh from here.")}
    store.put(HG.HALT_KEY, json.dumps(cleared, sort_keys=True).encode())
    # PHASE 2: only now is the clear a fact, so only now is it recorded as
    # one. If THIS write fails the latch is open while the log still reads
    # CLEAR_ATTEMPTED: the id stays spent so nothing replays, and INSPECT
    # shows the true state. The log understates, never overstates.
    done = dict(entry, outcome="CLEARED", cleared=True,
                cleared_at=_utc().isoformat())
    _append_audit(store, prior, done)
    return {"ok": True, "entry": done, "record": cleared}


def read_only_transport():
    """A GET-ONLY signed transport, and nothing else.

    THE BLOCKER THIS FIXES. main() used to construct
    l5_watchdog.CancelOnlyTransport, which (a) refuses to exist without
    pmus_auth and so could not run in the credential-free watchdog image at
    all, and (b) carried a cancel verb the reset has no business holding.
    pmus_auth.ReadOnlyTransport raises PermissionError on any method but GET,
    so this entrypoint cannot mutate anything at the venue even by mistake.
    Its only write is the halt object on the volume.
    """
    from src import pmus_auth as A
    if not A.has_credentials():
        raise ResetRefused(
            "NO_VENUE_CREDENTIALS: the authenticated read-only venue checks "
            "cannot run. This entrypoint runs only from the one-shot admin "
            "image with TEMPORARY credentials; the ordinary credential-free "
            "watchdog image cannot and must not run it.")
    return A.ReadOnlyTransport()


def build_store(*, inspecting: bool) -> Tuple[Any, str]:
    """The latch's medium for this run, and a word naming it.

    INSPECT may read over the watchdog's GET-only HTTP server, which is what
    lets the digest be obtained WITHOUT stopping the production watchdog. The
    bytes are served verbatim, so the digest is identical to the file's.
    A CLEAR always uses the mounted volume.
    """
    from src import l5_halt_gate as HG
    url = _env(HALT_URL_ENV)
    if inspecting and _env(VIA_HTTP_ENV).upper() == "YES":
        if not url:
            raise ResetRefused(
                "NO_HALT_URL: %s=YES needs %s" % (VIA_HTTP_ENV, HALT_URL_ENV))
        return HG.HttpHaltStore(url), "http_read_only"
    return HG.FileStore(os.getenv(STATE_ENV, "/data")), "volume"


def _refusal_label(pre_deploy: bool) -> str:
    if pre_deploy:
        return ("PRE-DEPLOY: refused and exiting 0 so the watchdog "
                "deployment is NOT blocked. Nothing was cleared and the "
                "latch is untouched. A spent reset id refusing here is the "
                "expected steady state.")
    return "Nothing was written. The latch is untouched."


def main(argv: Optional[List[str]] = None) -> int:
    out: Dict[str, Any]
    inspecting = _env(INSPECT_ENV).upper() == "YES"
    pre_deploy = _env(PRE_DEPLOY_ENV).upper() == "YES"
    try:
        store, medium = build_store(inspecting=inspecting)
        if inspecting:
            # READ-ONLY, so a missing credential must not hide the digest:
            # obtaining it is the whole reason the inspect phase exists.
            try:
                transport = read_only_transport()
            except ResetRefused:
                transport = None
            out = inspect(store=store, transport=transport,
                          registry_url=_env(REGISTRY_ENV))
            out["medium"] = medium
            print("L5_HALT_RESET_INSPECT " + json.dumps(out, sort_keys=True))
            return 0
        # THE CLEAR PATH. Both guards precede every other gate: if the wrong
        # image is running, or the latch is only reachable read-only, nothing
        # else about this run is meaningful.
        _require_reset_image()
        _require_clearable_store(store)
        transport = read_only_transport()
        out = perform_reset(store=store, transport=transport,
                            registry_url=_env(REGISTRY_ENV))
        print("L5_HALT_RESET " + json.dumps(out, sort_keys=True))
        return 0
    except ResetRefused as exc:
        print("L5_HALT_RESET_REFUSED " + json.dumps(
            {"ok": False, "refused": str(exc),
             "pre_deploy": pre_deploy,
             "labels": _refusal_label(pre_deploy)}, sort_keys=True))
        return 0 if pre_deploy else 2
    except BaseException as exc:                               # noqa: BLE001
        print("L5_HALT_RESET_REFUSED " + json.dumps(
            {"ok": False, "refused": "%s: %s" % (type(exc).__name__,
                                                 str(exc)[:200]),
             "pre_deploy": pre_deploy,
             "labels": _refusal_label(pre_deploy)}, sort_keys=True))
        return 0 if pre_deploy else 3


if __name__ == "__main__":
    sys.exit(main())
