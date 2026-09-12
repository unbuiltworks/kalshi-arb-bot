"""Build-time hotfix for R5FT COMPLETE post-only rejection races.

Applied only in the dedicated PMUS spring image.  It is intentionally a
strict source transform: if the expected rejection block is not present
exactly once, the image build fails instead of silently shipping a partial
patch.

The retry is execution adaptation, not a new L5 decision.  After a COMPLETE
was selected and the venue rejects it because the fresh book moved through
(or to within one tick of) the submitted maker price, we refresh the deficit
side book and retry that SAME COMPLETE once.  The fresh retry is still maker
only, still passes spring_complement_price(), still obeys the <= $1 matched
pair invariant, still uses the existing session deadline/rejection breakers,
and never retries a non-crossing or infrastructure rejection.
"""
from pathlib import Path

PATH = Path("src/pmus_spring_l5_session.py")

OLD = '''                    if rej["crossed"]:
                        sleep(max(prop["next_reevaluation_s"], MIN_REEVAL_S))
                    else:
                        revoke_complement_proof(
                            slug, {"order_id": oid,
                                   "side": "YES" if deficit_up else "NO",
                                   "px": str(px), "qty": str(qty),
                                   "end_reason": end_reason,
                                   "fresh_opp_ask": rej["opp_ask"]},
                            "complete_rejected_at_fresh_non_crossing_price")
'''

NEW = '''                    if rej["crossed"]:
                        # R5FT 2026-09-12: a model-selected COMPLETE at YES
                        # 0.63 was rejected post-only while the fresh ask was
                        # 0.64 (one-tick race).  Resampling the L5 policy here
                        # discarded the COMPLETE and immediately allowed an
                        # EXTEND.  That is an execution artifact, not a new
                        # behavioral observation.  Refresh the book and retry
                        # THIS SAME COMPLETE exactly once; the retry is not a
                        # new decision row and cannot recurse.
                        retry_parent = oid
                        retry_opp = q1.outcome_bbo(
                            t, slug,
                            "OUTCOME_SIDE_YES" if deficit_up
                            else "OUTCOME_SIDE_NO")
                        retry_touch = (
                            D(str(retry_opp["bid"])) / spec["tick"]
                        ).to_integral_value(rounding=ROUND_DOWN) * spec["tick"]
                        retry_px, retry_econ = spring_complement_price(
                            surplus_avg, retry_opp["ask"], spec["tick"],
                            touch=retry_touch if at_touch_regime else None)
                        report["orders"][-1]["same_decision_retry"] = {
                            "eligible": retry_px is not None,
                            "fresh_touch": str(retry_touch),
                            "fresh_opp_ask": str(retry_opp["ask"]),
                            "fresh_px": str(retry_px) if retry_px is not None else None,
                            "pair_economics": retry_econ,
                            "reason": "post_only_cross_race"}
                        # Do not churn an identical rejected quote.  A retry
                        # exists only when the fresh book changes the legal
                        # maker price and every original economic invariant
                        # still passes.
                        if (retry_px is not None and retry_px != px
                                and not out_of_time()
                                and pair_invariant_ok(
                                    inv, deficit_up, retry_px, qty)):
                            retry_oid = micro.place_limit_buy(
                                t, slug,
                                "OUTCOME_SIDE_YES" if deficit_up
                                else "OUTCOME_SIDE_NO",
                                retry_px, qty,
                                f"spring_complete_{actions + 1}_retry1",
                                post_only=True)
                            retry_sw = _still_wanted(
                                deficit_up, retry_px, "complete", skey)
                            retry_got, retry_rest_s, retry_end = _await_order(
                                t, retry_oid, qty,
                                min(stop_at, clock() + max(
                                    prop["next_reevaluation_s"], MIN_REEVAL_S,
                                    maker_rest_max_s())),
                                clock, sleep, still_wanted=retry_sw,
                                ledger=fill_ledger)
                            _harvest(
                                retry_oid,
                                f"spring_complete_{actions + 1}_retry1",
                                deficit_up, retry_px, qty, "complete",
                                retry_got, retry_rest_s, retry_end)
                            retry_row = report["orders"][-1]
                            retry_row["execution_retry"] = True
                            retry_row["retry_of_order_id"] = retry_parent
                            retry_row["rest_book"] = dict(retry_sw.track)
                            retry_row["l5_touch_px"] = str(retry_touch)
                            complete_submissions += 1
                            if retry_end.startswith("rejected"):
                                complete_rejections += 1
                                complete_reject_streak += 1
                                retry_rej = _classify_rejection(
                                    deficit_up, retry_px)
                                retry_row["rejection_class"] = retry_rej["class"]
                                retry_row["fresh_opp_ask"] = retry_rej["opp_ask"]
                                retry_row["rejection_margin_ticks"] = \\
                                    retry_rej.get("margin_ticks")
                                retry_row["tick"] = str(spec["tick"])
                                retry_row["venue_market"] = \\
                                    retry_rej.get("venue_market")
                                retry_row["recorder_book"] = \\
                                    retry_rej.get("recorder_book")
                                if is_infra_rejection(
                                        retry_end, retry_rej["class"]):
                                    infra_rejections += 1
                                if not retry_rej["crossed"]:
                                    revoke_complement_proof(
                                        slug,
                                        {"order_id": retry_oid,
                                         "side": "YES" if deficit_up else "NO",
                                         "px": str(retry_px), "qty": str(qty),
                                         "end_reason": retry_end,
                                         "fresh_opp_ask": retry_rej["opp_ask"]},
                                        "complete_rejected_at_fresh_non_crossing_price")
                            else:
                                # The venue accepted/rested the retry.  A
                                # pricing-race rejection streak is therefore
                                # broken even if the maker order did not fill.
                                complete_reject_streak = 0
                            retry_row["complete_reject_streak"] = \\
                                complete_reject_streak
                            retry_row["complete_reject_rate"] = \\
                                f"{complete_rejections}/{complete_submissions}"
                            retry_row["infra_rejections"] = infra_rejections
                            if retry_got > 0:
                                retry_fill = micro._terminal_positive_fill(
                                    t, retry_oid, qty) or {
                                        "price": retry_px, "qty": retry_got}
                                inv.record(
                                    deficit_up,
                                    D(str(retry_fill["price"])),
                                    retry_got, "complete")
                                record_behavioral_fill(
                                    inv, fid_art, skey, "complete", deficit_up)
                                # Prevent the original rejected order's
                                # post-block fill accounting from running.
                                got = D("0")
                        else:
                            sleep(max(
                                prop["next_reevaluation_s"], MIN_REEVAL_S))
                    else:
                        revoke_complement_proof(
                            slug, {"order_id": oid,
                                   "side": "YES" if deficit_up else "NO",
                                   "px": str(px), "qty": str(qty),
                                   "end_reason": end_reason,
                                   "fresh_opp_ask": rej["opp_ask"]},
                            "complete_rejected_at_fresh_non_crossing_price")
'''

text = PATH.read_text(encoding="utf-8")
count = text.count(OLD)
if count != 1:
    raise SystemExit(
        f"R5FT COMPLETE retry transform refused: expected block exactly once; found {count}")
patched = text.replace(OLD, NEW, 1)
if patched.count("same_decision_retry") != 1:
    raise SystemExit("R5FT COMPLETE retry transform verification failed")
PATH.write_text(patched, encoding="utf-8")
print("R5FT_COMPLETE_REJECT_RETRY_PATCHED")
