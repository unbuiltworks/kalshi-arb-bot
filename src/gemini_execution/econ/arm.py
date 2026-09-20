"""THE CHALLENGER ARM: GEMINI_ECON_V1, beside the frozen control.

WHAT IT IS. One more `_Arm`-shaped participant in `shadow_soak`, with its own
guard, its own inventory and its own hypothetical book, sharing only the tape.
It asks the frozen Model A exactly what the control asks it, records the answer
verbatim, and then applies the Gemini economics the control has no way to know
about. Nothing here edits Model A, its spec, or any frozen artifact.

THE TWO AXES ARE KEPT APART BY CONSTRUCTION, not by discipline:

    model_a_wanted   what L5 asked for, recorded before we touch it
    econ_acted       what the venue economics allowed
    divergences      one row per difference, each CLASSIFIED and given a reason

so "positive Gemini economics" can always be attributed to faithful transfer or
to an explicit venue adaptation. A divergence is never written back into an L5
field, and `REDUCE` / `EXIT` -- which have no L5 counterpart at all -- are
carried only in `gemini_disposition`.

THE FOUR CLASSES OF DIVERGENCE, and which one to be suspicious of:

    FAITHFUL              acted exactly what L5 wanted
    ADAPTATION_SUPPRESSED L5 wanted it; an entry gate or the veto refused
    ADAPTATION_SUBSTITUTED L5 wanted COMPLETE; we took REDUCE or EXIT instead
    ADAPTATION_ADDED      we acted something L5 never asked for

ADAPTATION_ADDED is the one that would let an "L5 discovery" be written out of
an adaptation, so it is counted separately and loudly. This arm never generates
a rung Model A did not propose -- it only ever suppresses or re-routes -- and a
test asserts the counter stays at zero.

NO TRANSPORT. No session, no signer, no order verb, no mutation. The arm's
output is a decision record; `HypotheticalBook` walks the public tape against
it exactly as it does for every other arm.
"""
from __future__ import annotations

import time
from decimal import Decimal as D
from decimal import InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import hypothetical as HY
from src.gemini_execution.econ import actions as AC
from src.gemini_execution.econ import entry as EN
from src.gemini_execution.econ import hazard as HZ
from src.gemini_execution.econ import policy as PO
from src.gemini_execution.econ import scoreboard as SB
from src.gemini_execution.econ import veto as VT

SCHEMA = "gemini_econ_arm/v1"
LABEL = "GEMINI_ECON_V1"

FAITHFUL = "FAITHFUL"
SUPPRESSED = "ADAPTATION_SUPPRESSED"
SUBSTITUTED = "ADAPTATION_SUBSTITUTED"
ADDED = "ADAPTATION_ADDED"


#: Which frozen spec each cohort's challenger asks. BTC15m MUST inherit
#: L5_ADAPTED_GEMINI_BTC15M_V2, or the [0,300] band in the default lane
#: withholds the surface for the whole 900->300 region and the cohort policy
#: below could never be exercised. Inheriting it also makes the comparison the
#: right one: the challenger then differs from the deployed BTC15M_V2 arm in
#: the ECONOMICS and in nothing else.
CORRECTED_SPEC_FOR = {"BTC15m": "L5_ADAPTED_GEMINI_BTC15M_V2"}


def spec_for(cohort_name: str):
    """The corrected spec for this cohort, or None for the frozen default.

    Built exactly as `shadow_soak.correction_spec` builds it -- probe-scoped
    size and residual, and the arm's own label preserved so a decision record
    can still say WHICH spec produced it.
    """
    label = CORRECTED_SPEC_FOR.get(cohort_name)
    if not label:
        return None
    from src.l5_surface import params as PR
    probe = CP.probe_params(PR.load_frozen()[label])
    return PR.SurfaceParams(
        label=label, kind="ADAPTED", params=probe.params,
        notes=("%s under the probe's own size and residual, inherited by "
               "GEMINI_ECON_V1 so the challenger differs from the deployed "
               "BTC15M_V2 arm in economics only; %s"
               % (label, probe.notes or "")))


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


class GeminiEconArm:
    """Same surface area as `shadow_soak._Arm`, plus the economics."""

    __slots__ = ("label", "sp", "guard", "inv", "hyp", "error", "improve",
                 "preserve", "cohort", "pol", "board", "hazard",
                 "first_fill_t", "divergences", "last")

    def __init__(self, *, symbol: str, cohort_prefix: Optional[str],
                 policy: str, cohort_name: str,
                 hazard_path: Optional[str] = None,
                 label: str = LABEL):
        self.label = label
        # the frozen Model A, unmodified. For BTC15m this is the already-
        # reviewed timing correction, not a new spec written here.
        self.sp = spec_for(cohort_name)
        self.improve = True             # execution adaptation A: zero queue-ahead
        self.preserve = False
        self.error = None
        self.cohort = cohort_name
        self.pol = PO.policy_for(cohort_name)
        self.guard = CP.WindowGuard(symbol, auth_id="SHADOW",
                                    cohort_prefix=cohort_prefix)
        self.inv = CP.Inventory()
        self.hyp = HY.HypotheticalBook(symbol=symbol, policy=policy)
        self.board = SB.Scoreboard(cohort=cohort_name)
        self.hazard = (HZ.HazardEstimator.load(hazard_path) if hazard_path
                       else HZ.HazardEstimator())
        self.first_fill_t: Optional[float] = None
        self.divergences: Dict[str, int] = {}
        self.last: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    def _note(self, kind: str) -> None:
        self.divergences[kind] = self.divergences.get(kind, 0) + 1

    def _held(self) -> Dict[str, Any]:
        res = self.inv.residual()
        legs = AC.held_and_complement(res)
        held = legs["held"]
        cost = None
        if held == "YES":
            cost = self.inv.avg_yes()
        elif held == "NO":
            cost = self.inv.avg_no()
        return {"residual": res, "held": held,
                "complement": legs["complement"], "held_avg_cost": cost}

    def step(self, *, symbol: str, tte: Any, book: Mapping[str, Any],
             depth: Any, flow: Mapping[str, Any],
             policy: str) -> Dict[str, Any]:
        """One cycle. Prints first, then the policy, then the economics."""
        # THE PRINTS COME FIRST, for the same reason every other arm does it:
        # this tape landed while the PREVIOUS cycle's rungs were resting.
        fills = self.hyp.consume(flow.get("prints") or [], inv=self.inv,
                                 book=book, depth=depth, tte_s=tte)
        if fills and self.first_fill_t is None:
            self.first_fill_t = time.time()
            self.board.first_fill(maker=True, zero_ahead=bool(self.improve))

        ms = CP.state_from_venue(symbol, tte, self.inv, book=book)
        kw: Dict[str, Any] = {"now": time.time()}
        if self.sp is not None:
            kw["sp"] = self.sp
        turn = self.guard.step(ms, self.inv, **kw)
        wanted: List[Dict[str, Any]] = list(turn.get("actions") or [])

        held = self._held()
        mode = self.pol.mode(tte) if self.pol else {
            "mode": PO.WITHDRAW, "why": "no policy for cohort %r" % self.cohort}

        acted: List[Dict[str, Any]] = []
        records: List[Dict[str, Any]] = []
        paths: Dict[str, Any] = {}

        # ---------------- INVENTORY STATE: evaluate all four paths first,
        # because what we do with a held leg governs whether a further OPEN
        # is even sensible.
        if held["held"] is not None and held["held_avg_cost"] is not None:
            paths = AC.evaluate_paths(
                residual=held["residual"], held_avg_cost=held["held_avg_cost"],
                book=book, qty=abs(held["residual"]),
                min_edge=(self.pol.thresholds.min_edge if self.pol else "0.005"),
                rescue_edge=("-0.01" if (self.pol and self.pol.may_rescue(tte))
                             else None),
                tte_s=tte)
            for name, p in (paths.get("paths") or {}).items():
                if p.get("available") and p.get("disposition") == AC.COMPLETE:
                    self.board.complement_seen(acceptable=bool(p.get("admitted")))

        # ---------------- walk what Model A asked for
        for a in wanted:
            act = a.get("action")
            rec: Dict[str, Any] = {"l5_action": act, "leg": a.get("leg"),
                                   "price": a.get("price"),
                                   "qty": a.get("qty")}

            if act == CP.COMPLETE:
                # THE HARD VETO. Model A may ask to complete at any price its
                # geometry produced; we complete only at one that pays.
                cost = held["held_avg_cost"]
                adm = VT.admits(
                    held_avg_cost=cost, proposed_price=a.get("price"),
                    min_edge=(self.pol.thresholds.min_edge if self.pol
                              else "0.005"))
                forbidden = None
                if not adm["admitted"] and cost is not None:
                    # measure what the overlay refused, per the ruling's
                    # "carry its stated cost" condition
                    q = abs(held["residual"])
                    forbidden = VT.forbidden_pair(
                        held_qty=q, held_avg_cost=cost, complement_qty=q,
                        complement_price=a.get("price"))
                self.board.completion_considered(adm["admitted"], adm["reason"],
                                                 forbidden=forbidden)
                if adm["admitted"]:
                    acted.append(a)
                    rec.update(gemini_disposition=AC.COMPLETE,
                               fidelity=FAITHFUL, admitted=True)
                    self._note(FAITHFUL)
                else:
                    # can a different path do better than not acting?
                    alt = AC.rank(paths) if paths else []
                    sub = next((r for r in alt
                                if r["disposition"] in (AC.REDUCE, AC.EXIT)),
                               None)
                    if sub is not None:
                        rec.update(gemini_disposition=sub["disposition"],
                                   fidelity=SUBSTITUTED, admitted=False,
                                   substituted_path=sub["path"],
                                   substituted_price=sub["price"],
                                   why=adm["reason"], forbidden=forbidden)
                        self._note(SUBSTITUTED)
                    else:
                        rec.update(gemini_disposition=None,
                                   fidelity=SUPPRESSED, admitted=False,
                                   why=adm["reason"], forbidden=forbidden)
                        self._note(SUPPRESSED)

            elif act in (CP.OPEN, CP.EXTEND):
                if not (self.pol and self.pol.may_open(tte)):
                    rec.update(gemini_disposition=None, fidelity=SUPPRESSED,
                               admitted=False,
                               why="cohort mode %s does not open"
                                   % mode.get("mode"))
                    self._note(SUPPRESSED)
                else:
                    feat = HZ.features(
                        tte_s=tte,
                        held_leg=(held["held"] or a.get("leg") or "YES"),
                        held_leg_price=a.get("price"),
                        required_complement_price=self._required(a),
                        book=book, prints=(flow.get("prints") or []),
                        same_side_run_len=self.inv.run_len)
                    est = self.hazard.estimate(feat)
                    dec = EN.admit_entry(
                        feat=feat, thresholds=self.pol.thresholds,
                        proposed_price=a.get("price"),
                        inventory=self.inv.report(), hazard_estimate=est)
                    self.board.entry(dec)
                    self.hazard.observe(feat, resolved=False,
                                        filled_acceptably=False)
                    if dec["admitted"]:
                        acted.append(a)
                        rec.update(gemini_disposition=act, fidelity=FAITHFUL,
                                   admitted=True, hazard=est)
                        self._note(FAITHFUL)
                    else:
                        rec.update(gemini_disposition=None,
                                   fidelity=SUPPRESSED, admitted=False,
                                   why=dec["why"],
                                   failed_gates=dec["failed_gates"],
                                   hazard=est)
                        self._note(SUPPRESSED)
            else:
                rec.update(gemini_disposition=None, fidelity=SUPPRESSED,
                           admitted=False, why="unhandled action %r" % act)
                self._note(SUPPRESSED)
            records.append(rec)

        # EXECUTION ADAPTATION A on the admitted rungs only -- price is
        # improved to reach zero queue-ahead, and an improvement that would
        # reach the ask is refused by `ab_arms` itself.
        if self.improve and acted:
            from src.gemini_execution import ab_arms as AB
            acted = AB.apply_A(acted, book or {})

        self.hyp.retarget(acted, now_ms=int(time.time() * 1000), depth=depth,
                          preserve=self.preserve)

        self.last = {
            "schema": SCHEMA, "label": self.label, "cohort": self.cohort,
            "mode": mode, "held": {k: (str(v) if isinstance(v, D) else v)
                                   for k, v in held.items()},
            "model_a_wanted": wanted,
            "econ_acted": acted,
            "decisions": records,
            "paths": paths,
            "hypothetical_fills": fills,
            "inventory": self.inv.report(),
            "divergences": dict(self.divergences),
            "labels": [
                "SHADOW/HYPOTHETICAL. No order exists and nothing here is "
                "submitted, filled, settled, credited or realized.",
                "model_a_wanted is recorded BEFORE the economics are applied; "
                "REDUCE and EXIT are never written into an L5 field.",
            ],
        }
        return self.last

    def _required(self, a: Mapping[str, Any]) -> Optional[str]:
        """The complement price this entry would need to be worth taking."""
        r = VT.max_complement_price(
            held_avg_cost=a.get("price"),
            min_edge=(self.pol.thresholds.min_edge if self.pol else "0.005"))
        return r.get("ceiling")

    # -- the shape `shadow_soak._step_arm` returns, so the challenger can ride
    # the existing collector without that function learning anything about
    # economics. IS_ECON_ARM is how it is recognised; nothing else in
    # shadow_soak changes.
    IS_ECON_ARM = True

    def step_compat(self, *, symbol: str, tte: Any, book: Mapping[str, Any],
                    depth: Any, flow: Mapping[str, Any],
                    policy: str) -> Dict[str, Any]:
        st = self.step(symbol=symbol, tte=tte, book=book, depth=depth,
                       flow=flow, policy=policy)
        wanted = st["model_a_wanted"]
        acted = st["econ_acted"]
        acted_keys = {(a.get("leg"), a.get("rung_index")) for a in acted}
        vetoed = [w for w in wanted
                  if (w.get("leg"), w.get("rung_index")) not in acted_keys]
        return {
            "market_state": None, "turn": {"actions": acted},
            "wanted": wanted, "vetoed": vetoed, "acted": acted,
            "fills": st["hypothetical_fills"],
            "decision": {"turn": {"actions": acted}, "policy": policy,
                         "model_a_wanted": wanted,
                         "vetoed_by_adapter": vetoed,
                         "gemini_econ": {
                             "mode": st["mode"], "held": st["held"],
                             "decisions": st["decisions"],
                             "paths": st["paths"],
                             "divergences": st["divergences"]},
                         "plan": {"keep": [],
                                  "place": [{"rung": a} for a in acted],
                                  "cancel": [], "mutations": len(acted)}},
        }

    def report(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "label": self.label, "cohort": self.cohort,
                "spec_label": (self.sp.label if self.sp is not None
                               else "FROZEN_DEFAULT"),
                "scoreboard": self.board.report(),
                "divergences": dict(self.divergences),
                "policy": self.pol.report() if self.pol else None,
                "error": self.error}
