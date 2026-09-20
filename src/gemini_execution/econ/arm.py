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
from src.gemini_execution.econ import prefill as PF
from src.gemini_execution.econ import scoreboard as SB
from src.gemini_execution.econ import veto as VT
from src.gemini_execution.econ import counterfactual as CF
from src.gemini_execution.econ import coverage as CVB
from src.gemini_execution.econ import spread_census as SC
from src.gemini_execution.econ import toxicity as TX
from src.gemini_execution.econ import ws_flow as WF

SCHEMA = "gemini_econ_arm/v1"
LABEL = "GEMINI_ECON_V1"
#: the passive-pair challenger, run BESIDE the boolean arm and never instead
PASSIVE_PAIR_LABEL = "PASSIVE_PAIR_V1"

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


def _flt(v: Any) -> Optional[float]:
    d = _dec(v)
    return None if d is None else float(d)


def _dec(v: Any) -> Optional[D]:
    try:
        return D(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


class GeminiEconArm:
    """Same surface area as `shadow_soak._Arm`, plus the economics."""

    __slots__ = ("label", "sp", "guard", "inv", "hyp", "error", "improve",
                 "preserve", "cohort", "pol", "board", "hazard",
                 "first_fill_t", "divergences", "last", "ws", "symbol",
                 "cf", "spreads", "state", "prewarm")

    def __init__(self, *, symbol: str, cohort_prefix: Optional[str],
                 policy: str, cohort_name: str,
                 hazard_path: Optional[str] = None,
                 label: str = LABEL,
                 ws: Any = None,
                 state: Any = None,
                 variant: str = PO.BOOLEAN_VARIANT,
                 cohort_prefix_grid: Optional[str] = None,
                 cohort_minutes: Optional[int] = None):
        self.label = label
        # the frozen Model A, unmodified. For BTC15m this is the already-
        # reviewed timing correction, not a new spec written here.
        self.sp = spec_for(cohort_name)
        self.improve = True             # execution adaptation A: zero queue-ahead
        self.preserve = False
        self.error = None
        self.cohort = cohort_name
        self.pol = PO.policy_for(cohort_name, variant=variant)
        self.guard = CP.WindowGuard(symbol, auth_id="SHADOW",
                                    cohort_prefix=cohort_prefix)
        self.inv = CP.Inventory()
        self.hyp = HY.HypotheticalBook(symbol=symbol, policy=policy)
        self.board = SB.Scoreboard(cohort=cohort_name)
        # PER-COHORT STATE THAT OUTLIVES THIS WINDOW. The arm is rebuilt every
        # window; the census, hazard and fork ledger must not be, or nothing
        # can ever accumulate to a sufficient cell.
        from src.gemini_execution.econ import registry as REG
        self.state = (state if state is not None
                      else REG.state_for(cohort_name, arm=label))
        self.state.fills.arm = label
        self.hazard = self.state.hazard
        self.symbol = symbol
        # FLOW COMES FROM THE MAINTAINED SOCKET, NOT THE POLLED TAPE. See
        # ws_flow's docstring: `habitat.public_flow` is labelled EVIDENCE ONLY
        # and may not be decided on. There is deliberately NO fallback -- an
        # unvouched interval refuses the flow gates rather than quietly
        # re-reading the source this replaces.
        self.ws = self.state.ensure_ws(ws)
        # SUBSCRIBE BEFORE THE DECISION WINDOW. See prewarm.py: a BTC5m
        # contract is 20-60s old when the lane enters it, so a socket opened at
        # the decision can never vouch for a meaningful interval.
        self.prewarm = None
        if cohort_prefix_grid and cohort_minutes:
            self.prewarm = self.state.ensure_prewarm(
                prefix=cohort_prefix_grid, minutes=cohort_minutes)
        # BOTH PATHS AT EVERY VETO. Local pair basis cannot say whether a
        # COMPLETE was wrong; the durable evidence (42.75% of L5's completes
        # above $1.00, and 1.00-1.02 outperforming <=0.96 when scored on the
        # resulting shares) is exactly why. See counterfactual.py.
        self.cf = self.state.cf
        # SPREAD AS A DISTRIBUTION BEFORE IT IS A THRESHOLD. max_spread was
        # preregistered from a single book and binds hard; it is NOT being
        # moved until this census says what the distribution is.
        self.spreads = self.state.spreads
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
        #
        # NOTE WHICH TAPE THIS IS. The HYPOTHETICAL FILL walk still consumes
        # the shared `flow` the collector passes every arm, deliberately: all
        # arms must be filled by the SAME tape or their fill counts stop being
        # comparable, and simulating a fill is not "taking a decision from" the
        # polled read. The ENTRY GATES below read the socket instead, because
        # those are decisions.
        fills = self.hyp.consume(flow.get("prints") or [], inv=self.inv,
                                 book=book, depth=depth, tte_s=tte)
        if fills and self.first_fill_t is None:
            self.first_fill_t = time.time()
            self.board.first_fill(maker=True, zero_ahead=bool(self.improve))
        if fills:
            # a subsequent fill on the VETO path -- the one we actually walk
            self.cf.note_fill()
        # THE FILL SEQUENCE. Price availability is not profit; these are the
        # fills the COMMON hypothetical book says the tape actually reached.
        _now = time.time()
        for _f in (fills or []):
            if not _f.get("was_first_fill"):
                continue
            # CAUSAL ONLY: assembled from ring samples at or before this
            # instant. The outcome is attached later and is the LABEL.
            try:
                self.state.toxicity.add(
                    features=self.state.ring.features_before(
                        symbol, t=_now, leg=_f.get("leg") or "YES"),
                    cohort=self.cohort, arm=self.label, symbol=symbol,
                    leg=_f.get("leg") or "YES", price=_f.get("price"),
                    t=_now)
            except Exception:                                    # noqa: BLE001
                pass
        self.state.fills.note_fills(fills, t=_now, symbol=symbol)

        ms = CP.state_from_venue(symbol, tte, self.inv, book=book)
        kw: Dict[str, Any] = {"now": time.time()}
        if self.sp is not None:
            kw["sp"] = self.sp
        turn = self.guard.step(ms, self.inv, **kw)
        wanted: List[Dict[str, Any]] = list(turn.get("actions") or [])

        # the socket's own view of recent flow, for the gates only
        try:
            # keep upcoming contracts warm so the NEXT window does not open
            # blind; idempotent and bounded.
            if self.prewarm is not None:
                self.prewarm.pump(self.ws)
            self.ws.watch(symbol)
            wsf = self.ws.flow(
                symbol,
                window_s=(self.pol.flow_window_s if self.pol else 120.0))
        except Exception as exc:                                 # noqa: BLE001
            wsf = {"vouched": False, "prints": None,
                   "why": "ws flow unavailable: %s: %s"
                          % (type(exc).__name__, exc)}

        # ---- census + counterfactual marks, every cycle, gating nothing
        now_t = time.time()
        try:
            ppm = None
            if wsf.get("vouched"):
                w_s = (self.pol.flow_window_s if self.pol else 120.0)
                ppm = (len(wsf.get("prints") or []) / (w_s / 60.0)
                       if w_s else None)
            self.spreads.record(SC.observe(
                cohort=self.cohort, tte_s=tte, book=book,
                prints_per_min=ppm,
                flow_source=("GEMINI_WS" if wsf.get("vouched")
                             else "UNVOUCHED"),
                t=now_t))
            self.spreads.advance(
                now=now_t, bid=((book or {}).get("yes") or {}).get("bid"))
            self.cf.mark_open(book=book, t=now_t, tte_s=tte)
            # follow every open candidate first fill down THIS contract's tape
            self.state.blocked.advance(book=book, t=now_t, symbol=symbol)
            self.state.fills.observe(book=book, t=now_t, symbol=symbol)
            # THE CAUSAL RING. Everything a pre-fill rule may look at, sampled
            # at the lane's own cadence. Shared across arms, since it is a
            # property of the market rather than of a strategy.
            self.ws.watch_spot()
            _y = (book or {}).get("yes") or {}
            _n = (book or {}).get("no") or {}
            _pr = wsf.get("prints") or []
            self.state.ring.observe(symbol, PF.Sample(
                t=now_t, tte_s=(None if tte is None else float(tte)),
                yes_bid=_flt(_y.get("bid")), yes_ask=_flt(_y.get("ask")),
                no_bid=_flt(_n.get("bid")), no_ask=_flt(_n.get("ask")),
                spot=self.ws.spot_mid(), strike=self._strike(symbol),
                n_prints=len(_pr),
                buy_prints=sum(1 for p in _pr if p.get("side") == "buy"),
                sell_prints=sum(1 for p in _pr if p.get("side") == "sell"),
                same_side_run_len=self.inv.run_len))
        except Exception as exc:                                 # noqa: BLE001
            # observation must never be able to stop a decision
            pass

        held = self._held()
        mode = self.pol.mode(tte) if self.pol else {
            "mode": PO.WITHDRAW, "why": "no policy for cohort %r" % self.cohort}

        # ---- COVERAGE ACCOUNTING, before any gate runs. A cycle the socket
        # could not vouch for is COVERAGE_WARMUP_EXCLUDED and is NOT an entry
        # refusal: the strategy was never asked.
        quoting = bool(self.pol and self.pol.may_open(tte))
        prewarmed = bool(self.prewarm is not None
                         and self.prewarm.was_prewarmed(symbol,
                                                        entered_at=now_t))
        bucket = self.state.coverage.cycle(
            symbol=symbol, t=now_t, vouched=bool(wsf.get("vouched")),
            quoting=quoting, prewarmed=prewarmed)

        acted: List[Dict[str, Any]] = []
        records: List[Dict[str, Any]] = []
        paths: Dict[str, Any] = {}
        cycle_decided = False
        cycle_admitted = False

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
                if not adm["admitted"] and cost is not None:
                    # fork here: the veto is the branch we walk, ALLOW is the
                    # branch the control walks, and both are scored on TOTAL
                    # inventory-state economics rather than pair basis
                    self.cf.open(
                        symbol=symbol, cohort=self.cohort, t=now_t, tte_s=tte,
                        held_leg=held["held"] or "",
                        complement_leg=held["complement"] or "",
                        held_qty=abs(held["residual"]),
                        held_avg_cost=cost,
                        complement_price=a.get("price"),
                        ceiling=(adm.get("ceiling") or {}).get("ceiling"),
                        book=book,
                        features=self._fork_features(
                            tte=tte, book=book, wsf=wsf, held=held,
                            mode=mode, now_t=now_t))
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
                        book=book,
                        prints=(wsf.get("prints") or []),
                        window_s=(self.pol.flow_window_s if self.pol
                                  else 120.0),
                        same_side_run_len=self.inv.run_len)
                    est = self.hazard.estimate(feat)
                    dec = EN.admit_entry(
                        feat=feat, thresholds=self.pol.thresholds,
                        proposed_price=a.get("price"),
                        inventory=self.inv.report(), hazard_estimate=est,
                        flow_vouched=bool(wsf.get("vouched")),
                        flow_why=wsf.get("why"))
                    self.board.entry(dec)
                    # ---- PROFIT SPRINT research capture. Attribution answers
                    # WHICH gate blocks; the labeler answers whether being
                    # blocked was GOOD. Neither is ever read by a gate.
                    if bucket == CVB.STRATEGY_ELIGIBLE:
                        yes = (book or {}).get("yes") or {}
                        _b, _a = _dec(yes.get("bid")), _dec(yes.get("ask"))
                        _sp = (None if (_b is None or _a is None)
                               else str(_a - _b))
                        att = self.state.attribution.record(
                            cohort=self.cohort, symbol=symbol, t=now_t,
                            tte_s=tte, leg=a.get("leg"), price=a.get("price"),
                            spread=_sp, gates=dec.get("gates") or [],
                            waived=dec.get("waived_unknown_gates") or [],
                            admitted=bool(dec["admitted"]))
                        self.state.blocked.open_candidate(
                            cohort=self.cohort, symbol=symbol, t=now_t,
                            tte_s=tte, leg=a.get("leg"),
                            price=a.get("price"), spread=_sp,
                            admitted=bool(dec["admitted"]),
                            blocking=att["blocking"],
                            marginal_blocker=att["marginal_blocker"],
                            min_edge=(self.pol.thresholds.min_edge
                                      if self.pol else "0.005"),
                            features=self._fork_features(
                                tte=tte, book=book, wsf=wsf, held=held,
                                mode=mode, now_t=now_t))
                    # ONE outcome per CYCLE, recorded after the action loop.
                    # Recording here would count a cycle proposing two rungs
                    # twice, and would count it even on a warm-up-excluded
                    # cycle.
                    cycle_admitted = bool(cycle_admitted) or bool(
                        dec["admitted"])
                    cycle_decided = True
                    self.hazard.observe(feat, resolved=False,
                                        filled_acceptably=False)
                    if dec["admitted"]:
                        acted.append(a)
                        rec.update(gemini_disposition=act, fidelity=FAITHFUL,
                                   admitted=True, hazard=est)
                        self._note(FAITHFUL)
                        # an ADMITTED entry opens a fill sequence: from here
                        # the question is what filled, not what was quotable
                        if act == CP.OPEN:
                            sq = self.state.fills.candidate(
                                cohort=self.cohort, symbol=symbol, t=now_t,
                                tte_s=tte,
                                min_edge=(self.pol.thresholds.min_edge
                                          if self.pol else "0.005"),
                                leg=a.get("leg"), price=a.get("price"),
                                book=book)
                            sq.note_rest([a.get("leg")] if a.get("leg")
                                         else [])
                    else:
                        rec.update(gemini_disposition=None,
                                   fidelity=SUPPRESSED, admitted=False,
                                   why=dec["why"],
                                   failed_gates=dec["failed_gates"],
                                   # an UNKNOWN gate refuses too, and a record
                                   # that shows only failures cannot say
                                   # whether we measured and rejected or
                                   # could not measure at all
                                   unknown_gates=dec["unknown_gates"],
                                   gate_reasons={
                                       g["gate"]: g["why"]
                                       for g in (dec.get("gates") or [])
                                       if g.get("pass") is not True
                                       and g.get("why")},
                                   hazard=est)
                        self._note(SUPPRESSED)
            else:
                rec.update(gemini_disposition=None, fidelity=SUPPRESSED,
                           admitted=False, why="unhandled action %r" % act)
                self._note(SUPPRESSED)
            records.append(rec)

        # ONE COVERAGE OUTCOME PER ELIGIBLE CYCLE. The ledger refuses a
        # decision from any other bucket, so this is belt and braces.
        if cycle_decided:
            self.state.coverage.decision(cycle_admitted, bucket=bucket)

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
            "ws_flow": {k: wsf.get(k) for k in
                        ("vouched", "why", "n_prints", "n_directional",
                         "undirected", "window_requested_s", "window_used_s",
                         "available_view_s", "truncated_by_view")},
            "coverage_bucket": bucket,
            "mode": mode, "held": {k: (str(v) if isinstance(v, D) else v)
                                   for k, v in held.items()},
            "model_a_wanted": wanted,
            "econ_acted": acted,
            "decisions": records,
            "paths": paths,
            "hypothetical_fills": fills,
            "inventory": self.inv.report(),
            "divergences": dict(self.divergences),
            "counterfactual_open": sum(1 for f in self.cf.forks
                                       if f.resolved is None),
            "labels": [
                "SHADOW/HYPOTHETICAL. No order exists and nothing here is "
                "submitted, filled, settled, credited or realized.",
                "model_a_wanted is recorded BEFORE the economics are applied; "
                "REDUCE and EXIT are never written into an L5 field.",
                "Entry gates read the MAINTAINED SOCKET. The polled tape is "
                "EVIDENCE ONLY and is used for the hypothetical fill walk "
                "(shared by every arm) but never for a decision.",
            ],
        }
        return self.last

    def _strike(self, symbol: str):
        """The contract's own published strike, or None. Never inferred."""
        try:
            from src.gemini_execution import metadata as MD
            return _flt((MD.terms_for(symbol) or {}).get("strike"))
        except Exception:                                        # noqa: BLE001
            return None

    def _fork_features(self, *, tte, book, wsf, held, mode,
                       now_t) -> Dict[str, Any]:
        """Every candidate feature AT THE FORK. Captured, never fitted.

        Collected now because a fork's state cannot be reconstructed later --
        Gemini serves no book history, so a feature not recorded at the moment
        is a feature lost.
        """
        yes = (book or {}).get("yes") or {}
        bid, ask = _dec(yes.get("bid")), _dec(yes.get("ask"))
        prints = wsf.get("prints") or []
        sides = [p.get("side") for p in prints if p.get("side")]
        alt = (sum(1 for a, b in zip(sides, sides[1:]) if a != b)
               / (len(sides) - 1)) if len(sides) > 1 else None
        qs = [_dec(p.get("qty")) for p in prints]
        qs = [q for q in qs if q is not None]
        total = sum(qs) if qs else D("0")
        used = wsf.get("window_used_s") or 0
        return {
            "tte_s": (None if tte is None else float(tte)),
            "held_leg": held.get("held"),
            "held_qty": str(abs(held.get("residual") or 0)),
            "held_avg_cost": (None if held.get("held_avg_cost") is None
                              else str(held["held_avg_cost"])),
            "spread": (None if (bid is None or ask is None)
                       else str(ask - bid)),
            "mid": (None if (bid is None or ask is None)
                    else str((bid + ask) / D("2"))),
            "side_alternation": alt,
            "same_side_run_len": self.inv.run_len,
            "prints_per_min": (len(prints) / (used / 60.0)) if used else None,
            "qty_per_min": (float(total) / (used / 60.0)) if used else None,
            "largest_print_share": (float(max(qs) / total)
                                    if qs and total > 0 else None),
            "n_prints": len(prints),
            "flow_window_used_s": used,
            "time_since_first_fill_s": (
                None if self.first_fill_t is None
                else round(now_t - self.first_fill_t, 2)),
            "window_mode": mode.get("mode"),
            "cohort": self.cohort,
        }

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

    def finish(self, *, book: Mapping[str, Any],
               settlement: Optional[str] = None) -> Dict[str, Any]:
        """Window end: resolve every open fork and release the socket.

        Called by the collector at rollover. Resolving here rather than at an
        arbitrary cycle means both branches are scored on the SAME final book.
        """
        n = self.cf.resolve_open(book=book or {}, t=time.time(),
                                 settlement=settlement)
        # close this contract's candidates: the next window is a DIFFERENT
        # instrument and following them there would mark one contract's fill
        # against another contract's book.
        self.state.blocked.settle_all(leg=settlement, symbol=self.symbol)
        self.state.fills.close_symbol(
            self.symbol, residual_qty=abs(self.inv.residual()),
            settlement_leg=settlement)
        # LABEL the captured pre-fill rows with what actually happened. The
        # outcome never enters a feature; it is only ever the target.
        try:
            byt = {round(float(r.get("t") or 0), 1): r
                   for r in self.state.toxicity.rows
                   if r.get("outcome") is None
                   and r.get("symbol") == self.symbol}
            for row in self.state.fills.rows:
                if row.get("symbol") != self.symbol or not row.get("first_fill"):
                    continue
                key = round(float(row.get("t_candidate") or 0), 1)
                tgt = byt.get(key)
                if tgt is None and byt:
                    # nearest capture for this contract, within a cycle
                    k = min(byt, key=lambda x: abs(x - key))
                    tgt = byt[k] if abs(k - key) <= 12.0 else None
                if tgt is None:
                    continue
                PF.ToxicityDataset.label(
                    tgt,
                    markout_60s=(row.get("markout_ticks_by_horizon_s") or {}
                                 ).get("60"),
                    complement_filled=row.get("complement_filled"),
                    max_adverse_ticks=row.get("max_adverse_markout_ticks"))
        except Exception:                                        # noqa: BLE001
            pass
        # NOTE: the socket is NOT dropped here. The pre-warmer owns socket
        # lifetime now, because dropping at window end is precisely what made
        # every next window open blind.
        self.state.window_done()
        return {"forks_resolved": n,
                "counterfactual": self.cf.report()}

    def report(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "label": self.label, "cohort": self.cohort,
                "spec_label": (self.sp.label if self.sp is not None
                               else "FROZEN_DEFAULT"),
                "scoreboard": self.board.report(),
                "coverage": self.state.coverage.report(),
                "variant": (self.pol.thresholds.spread_mode
                            if self.pol else None),
                "fill_sequence": self.state.fills.report(),
                "toxicity_dataset": self.state.toxicity.report(),
                "toxicity_walk_forward": TX.walk_forward(
                    self.state.toxicity.rows),
                "attribution": self.state.attribution.report(),
                "blocked_labels": self.state.blocked.report(),
                "counterfactual": self.cf.report(),
                "spread_census": self.spreads.report(),
                "prewarm": (self.prewarm.report() if self.prewarm else None),
                "state": self.state.report(),
                "divergences": dict(self.divergences),
                "policy": self.pol.report() if self.pol else None,
                "error": self.error}
