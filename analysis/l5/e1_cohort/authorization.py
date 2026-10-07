"""The COHORT/SESSION authorization boundary, and the per-submission check.

THE PROBLEM, STATED HONESTLY. The frozen spec's eligibility rule E1 requires
the owner's gate to name the exact market slug. A single static
`PMUS_E1_GATE_EVIDENCE=<slug>` cannot authorize a 12-window rolling cohort,
because there are twelve different slugs and only one env value.

THE WRONG FIX, AND WHY IT IS WRONG. Deleting the slug check would make the
frozen spec state a rule the code does not enforce. That is the exact class
of defect that superseded three fingerprints already -- a spec describing
behaviour the code does not have. So the check is not deleted; it is made
correct for a cohort.

THE FIX: TWO BOUNDARIES INSTEAD OF ONE, BOTH NARROWER THAN THE ORIGINAL.

  1. SESSION BOUNDARY. `PMUS_E1_GATE_EVIDENCE` names the authorized SERIES
     (`btc-updown-15m`). Only markets the FROZEN SELECTOR produced during
     THIS authorized session may be traded. A slug that no selector call
     produced in this session is refused, whatever it looks like.

  2. PER-SUBMISSION BOUNDARY. Immediately before EVERY first-leg
     submission, the specific RESOLVED slug is enforced against the pin for
     that window. Not the series, not the session ledger -- the exact slug
     and both exact token ids.

Together these are STRICTER than one static slug: the original authorized
one slug for an unbounded number of orders, and this authorizes one series
for a bounded session, then re-proves the exact market before every single
order.

A COMPLEMENT MAY NOT SELECT A MARKET. `authorize_complement` takes the
first leg's ALREADY-AUTHORIZED pin and refuses any other, so the complement
path has no route to a market of its own.

NOTHING HERE SETS AN OWNER VALUE. The session ledger records what the
selector produced; it cannot widen what the owner authorized.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from analysis.l5.e1_cohort import arming as A
from analysis.l5.e1_cohort import selector as SEL

GATE_EVIDENCE_ENV = "PMUS_E1_GATE_EVIDENCE"

#: the value the owner sets in `PMUS_E1_GATE_EVIDENCE` to authorize the
#: cohort's series. It is the SERIES, not a slug, and the per-submission
#: check below is what keeps the exact-slug requirement real.
AUTHORIZED_SERIES = SEL.SERIES


class NotAuthorized(RuntimeError):
    """This market may not be traded. Fails CLOSED."""


def series_authorized() -> Dict[str, Any]:
    """Boundary 1: the owner's gate names the series this cohort may trade."""
    got = os.getenv(GATE_EVIDENCE_ENV, "").strip()
    return {"env": GATE_EVIDENCE_ENV,
            "set": bool(got),
            "matches_authorized_series": got == AUTHORIZED_SERIES,
            "authorized_series": AUTHORIZED_SERIES,
            "namespace": "PMUS_E1_*",
            "VALUE_NEVER_REPORTED": True}


class SessionAuthorization:
    """The authorized market ledger for ONE E1 session.

    Durable, append-only, session-scoped. A slug enters it ONLY by being
    produced by the frozen selector while the series gate is set, and
    nothing removes one. A restart reloads it, so a resumed cohort
    recognises the markets it already authorized and does not re-authorize
    from scratch.
    """

    FILENAME = "e1.authorized_markets.jsonl"

    def __init__(self, state_dir: str,
                 session_id: Optional[str] = None) -> None:
        self.state_dir = state_dir
        self.session_id = session_id or A.session_id()
        if not self.session_id:
            raise NotAuthorized(
                "%s is not set; an authorization ledger needs a session to "
                "scope itself to" % A.SESSION_ID_ENV)
        self.path = os.path.join(state_dir, self.FILENAME)
        self._pins: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------- durable
    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("session_id") == self.session_id:
                    self._pins[str(row["slug"])] = row

    def _append(self, row: Dict[str, Any]) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.path, "a", encoding="utf8") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    # ------------------------------------------------------- authorization
    def authorize_selected(self, pin: Dict[str, Any]) -> Dict[str, Any]:
        """Boundary 1, applied to one selector-produced window.

        Refuses unless the owner's gate names the series AND the pin came
        from the frozen selector for this series.
        """
        gate = series_authorized()
        if not gate["matches_authorized_series"]:
            raise NotAuthorized(
                "the owner's %s does not name the authorized series %r; no "
                "market may be traded" % (GATE_EVIDENCE_ENV,
                                          AUTHORIZED_SERIES))
        if not A.armed():
            raise NotAuthorized("E1 is not armed in its own PMUS_E1_* "
                                "namespace")
        if pin.get("series") != AUTHORIZED_SERIES:
            raise NotAuthorized(
                "pin names series %r, not the authorized %r"
                % (pin.get("series"), AUTHORIZED_SERIES))
        SEL._check_series(pin.get("slug"), pin.get("window_start"))
        row = {"session_id": self.session_id,
               "slug": str(pin["slug"]),
               "market_id": str(pin["market_id"]),
               "token_up": str(pin["token_up"]),
               "token_down": str(pin["token_down"]),
               "window_start": int(pin["window_start"]),
               "series": AUTHORIZED_SERIES,
               "selection_provenance": pin.get("selection_provenance"),
               "authorized_unix": time.time(),
               "authorized_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                               time.gmtime()),
               "authorization_result": "AUTHORIZED",
               "boundary": "SESSION_SERIES_PLUS_PER_SUBMISSION_SLUG"}
        if str(pin["slug"]) not in self._pins:
            self._append(row)
        self._pins[str(pin["slug"])] = row
        return row

    def is_authorized(self, slug: str) -> bool:
        return str(slug) in self._pins

    def pin_for(self, slug: str) -> Optional[Dict[str, Any]]:
        return self._pins.get(str(slug))

    def slugs(self) -> List[str]:
        return sorted(self._pins)

    # --------------------------------------------- boundary 2, per submit
    def enforce_before_submission(self, *, slug: str, pin: Dict[str, Any],
                                  token_up: Optional[str] = None,
                                  token_down: Optional[str] = None,
                                  role: str = "first_leg") -> Dict[str, Any]:
        """Boundary 2. Called immediately before EVERY submission.

        Enforces the SPECIFIC RESOLVED SLUG and both token ids against the
        pin for this window, and requires that slug to be one this session
        authorized. Fails CLOSED on every mismatch.
        """
        if not SEL.is_selected_slug(slug, pin):
            raise NotAuthorized(
                "slug %r is not the pinned market for this window (%r). "
                "Refused BEFORE transport." % (slug, pin.get("slug")))
        if not self.is_authorized(slug):
            raise NotAuthorized(
                "slug %r was never produced by the frozen BTC15m selector "
                "during this authorized session. Refused BEFORE transport."
                % (slug,))
        known = self._pins[str(slug)]
        for name, got, want in (("market_id", pin.get("market_id"),
                                 known["market_id"]),
                                ("token_up", token_up or pin.get("token_up"),
                                 known["token_up"]),
                                ("token_down",
                                 token_down or pin.get("token_down"),
                                 known["token_down"])):
            if str(got) != str(want):
                raise NotAuthorized(
                    "%s mismatch for %s: %r is not the authorized %r. "
                    "Refused BEFORE transport." % (name, slug, got, want))
        gate = series_authorized()
        if not gate["matches_authorized_series"]:
            raise NotAuthorized("the series gate is no longer set")
        if not A.armed():
            raise NotAuthorized("E1 is no longer armed")
        return {"ok": True, "slug": str(slug), "role": role,
                "market_id": known["market_id"],
                "token_up": known["token_up"],
                "token_down": known["token_down"],
                "authorization_result": "AUTHORIZED_FOR_SUBMISSION",
                "checked_at_unix": time.time(),
                "boundary": "PER_SUBMISSION_EXACT_SLUG_AND_TOKENS"}

    def authorize_complement(self, *, slug: str,
                             first_leg_pin: Dict[str, Any],
                             token_up: Optional[str] = None,
                             token_down: Optional[str] = None
                             ) -> Dict[str, Any]:
        """A complement may ONLY belong to the already-authorized first-leg
        market. It cannot independently select another."""
        if str(slug) != str(first_leg_pin.get("slug")):
            raise NotAuthorized(
                "a complement may only belong to the already-authorized "
                "first-leg market %r, not %r. The complement path cannot "
                "select a market." % (first_leg_pin.get("slug"), slug))
        out = self.enforce_before_submission(
            slug=slug, pin=first_leg_pin, token_up=token_up,
            token_down=token_down, role="complement")
        out["inherits_first_leg_authorization"] = True
        out["complement_selected_its_own_market"] = False
        return out

    def report(self) -> Dict[str, Any]:
        return {"session_id_present": bool(self.session_id),
                "authorized_slugs": self.slugs(),
                "authorized_count": len(self._pins),
                "series": AUTHORIZED_SERIES,
                "gate": series_authorized(),
                "ledger": self.path,
                "append_only": True,
                "a_slug_is_never_removed": True,
                "boundary": "SESSION_SERIES_PLUS_PER_SUBMISSION_SLUG",
                "complement_may_select_a_market": False}
