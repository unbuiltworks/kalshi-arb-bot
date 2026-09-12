from pathlib import Path

p = Path("src/pmus_spring_l5_session.py")
s = p.read_text()
marker = "            else:                               # complete: deficit side only\n"
m = s.index(marker)
start = s.index('                qty = min(inv.residual.copy_abs(), spec["qty"])\n', m)
end = s.index("            actions += 1\n", start)

new = '''                # One behavioral COMPLETE may require multiple EXECUTION attempts.
                # A zero-fill post-only rejection caused by the book moving through
                # our price is an execution race, not a new L5 decision. Keep the
                # same decision_id and retry after a fresh BBO/economic check.
                remaining = min(inv.residual.copy_abs(), spec["qty"])
                execution_attempt = 0
                while remaining > 0 and not out_of_time():
                    execution_attempt += 1
                    qty = remaining
                    oid = micro.place_limit_buy(
                        t, slug, "OUTCOME_SIDE_YES" if deficit_up
                        else "OUTCOME_SIDE_NO", px, qty,
                        f"spring_complete_{actions + 1}_x{execution_attempt}",
                        post_only=True)
                    reached_order_stage = True
                    sw = _still_wanted(deficit_up, px, "complete", skey)
                    got, rest_s, end_reason = _await_order(
                        t, oid, qty,
                        min(stop_at, clock() + max(
                            prop["next_reevaluation_s"], MIN_REEVAL_S,
                            maker_rest_max_s())),
                        clock, sleep, still_wanted=sw, ledger=fill_ledger)
                    _harvest(
                        oid, f"spring_complete_{actions + 1}_x{execution_attempt}",
                        deficit_up, px, qty, "complete", got, rest_s,
                        end_reason)
                    orow = report["orders"][-1]
                    orow["rest_book"] = dict(sw.track)
                    orow["l5_touch_px"] = str(l5_touch)
                    orow["behavioral_decision_id"] = decision_id
                    orow["execution_attempt"] = execution_attempt
                    mo = sw.track.get("min_opp_ask")
                    orow["l5_touch_reached_by_opposite_ask"] = (
                        bool(D(mo) <= l5_touch) if mo is not None else None)

                    complete_submissions += 1
                    if end_reason.startswith("rejected"):
                        complete_reject_streak += 1
                        complete_rejections += 1
                        rej = _classify_rejection(deficit_up, px)
                        orow["rejection_class"] = rej["class"]
                        orow["fresh_opp_ask"] = rej["opp_ask"]
                        orow["rejection_margin_ticks"] = rej.get("margin_ticks")
                        orow["tick"] = str(spec["tick"])
                        orow["venue_market"] = rej.get("venue_market")
                        orow["recorder_book"] = rej.get("recorder_book")
                        if is_infra_rejection(end_reason, rej["class"]):
                            infra_rejections += 1
                        orow["complete_reject_streak"] = complete_reject_streak
                        orow["complete_reject_rate"] = (
                            f"{complete_rejections}/{complete_submissions}")
                        orow["infra_rejections"] = infra_rejections

                        # Only an immediate post-only race gets a sticky retry.
                        # Non-crossing rejection still revokes the venue proof.
                        if not rej["crossed"]:
                            revoke_complement_proof(
                                slug, {"order_id": oid,
                                       "side": "YES" if deficit_up else "NO",
                                       "px": str(px), "qty": str(qty),
                                       "end_reason": end_reason,
                                       "fresh_opp_ask": rej["opp_ask"]},
                                "complete_rejected_at_fresh_non_crossing_price")
                            break
                        if (complete_reject_streak >= COMPLETE_REJECT_STREAK_MAX
                                or infra_rejections >= INFRA_REJECT_MAX):
                            break
                        if (complete_submissions >= COMPLETE_REJECT_RATE_MIN_N
                                and complete_rejections
                                > COMPLETE_REJECT_RATE_MAX * complete_submissions):
                            break

                        # Fresh book + fresh economic bound, SAME behavioral
                        # decision. Still maker-only; never crosses/takes.
                        opp = q1.outcome_bbo(
                            t, slug,
                            "OUTCOME_SIDE_YES" if deficit_up else "OUTCOME_SIDE_NO")
                        spec = micro.market_micro_spec(t, slug, bbo["bid"])
                        l5_touch = (
                            D(str(opp["bid"])) / spec["tick"]
                        ).to_integral_value(rounding=ROUND_DOWN) * spec["tick"]
                        surplus_avg = inv.avg_up() if not deficit_up else inv.avg_dn()
                        px, retry_econ = spring_complement_price(
                            surplus_avg, opp["ask"], spec["tick"],
                            touch=l5_touch if at_touch_regime else None)
                        orow["sticky_retry"] = {
                            "same_behavioral_decision": True,
                            "next_attempt": execution_attempt + 1,
                            "refreshed_economics": retry_econ,
                            "refreshed_l5_touch_px": str(l5_touch),
                            "refreshed_px": str(px) if px is not None else None,
                        }
                        if px is None:
                            break
                        remaining = min(remaining, inv.residual.copy_abs())
                        continue

                    # A resting/accepted attempt clears the rejection streak.
                    complete_reject_streak = 0
                    if got > 0:
                        fill = micro._terminal_positive_fill(t, oid, qty) or \\
                            {"price": px, "qty": got}
                        inv.record(deficit_up, D(str(fill["price"])), got,
                                   "complete")
                        record_behavioral_fill(
                            inv, fid_art, skey, "complete", deficit_up)
                        # Partial fill retries only this decision's remainder.
                        remaining = min(max(D("0"), qty - got),
                                        inv.residual.copy_abs())
                        if remaining > 0:
                            opp = q1.outcome_bbo(
                                t, slug,
                                "OUTCOME_SIDE_YES" if deficit_up else "OUTCOME_SIDE_NO")
                            spec = micro.market_micro_spec(t, slug, bbo["bid"])
                            l5_touch = (
                                D(str(opp["bid"])) / spec["tick"]
                            ).to_integral_value(rounding=ROUND_DOWN) * spec["tick"]
                            surplus_avg = inv.avg_up() if not deficit_up else inv.avg_dn()
                            px, retry_econ = spring_complement_price(
                                surplus_avg, opp["ask"], spec["tick"],
                                touch=l5_touch if at_touch_regime else None)
                            orow["partial_fill_retry"] = {
                                "remaining_qty": str(remaining),
                                "refreshed_economics": retry_econ,
                                "refreshed_px": str(px) if px is not None else None,
                            }
                            if px is not None:
                                continue
                    break
'''

p.write_text(s[:start] + new + s[end:])

Path("tests/test_sticky_complete_retry.py").write_text('''from pathlib import Path\n\n\ndef _src():\n    return Path("src/pmus_spring_l5_session.py").read_text()\n\n\ndef test_complete_retries_inside_same_behavioral_decision():\n    s = _src()\n    assert '"same_behavioral_decision": True' in s\n    assert 'orow["behavioral_decision_id"] = decision_id' in s\n    assert 'orow["execution_attempt"] = execution_attempt' in s\n    assert 'if not rej["crossed"]:' in s\n    assert 'remaining = min(max(D("0"), qty - got)' in s\n\n\ndef test_retry_is_bounded_and_maker_only():\n    s = _src()\n    assert 'complete_reject_streak >= COMPLETE_REJECT_STREAK_MAX' in s\n    assert 'post_only=True' in s\n    assert 'spring_complement_price(' in s\n    assert 'while remaining > 0 and not out_of_time():' in s\n''')
