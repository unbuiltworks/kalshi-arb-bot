# #93 rejection forensics: our book vs the venue's

Orders carrying an authenticated book: **91**  
Of which rejected: **78**

| class | rejections |
|---|---|
| CROSSED_AUTHENTICATED | 21 |
| NON_CROSSING_AUTHENTICATED | 21 |
| UNVERIFIABLE_BOOK_UNREADABLE | 27 |
| UNVERIFIABLE_BOOK_STALE | 9 |
| UNVERIFIABLE_NO_PRICE | 0 |

Explained by crossing the authenticated book: **21** (9 distinct book readings)  
Survive as genuinely non-crossing: **21** (14 distinct book readings across 11 markets)  
Unverifiable: **36** (share 0.4615)

Unexplained by stage: {'spring_open': 2, 'spring_extend': 10, 'spring_complete': 9}  
Crossed by stage: {'spring_open': 14, 'spring_extend': 6, 'spring_complete': 1}  
Unexplained by side: {'NO': 11, 'YES': 10} (an even split rules out the NO-side derived ask, `1 - yes_bid`, as the cause)

## Accepted (control) vs rejected — the same book

| population | orders | readable margin | min | median | max | <=1 tick |
|---|---|---|---|---|---|---|
| ACCEPTED | 13 | 11 | 1.0 | 2.0 | 8.0 | 3 |
| REJECTED | 78 | 51 | -9.0 | 1.0 | 30.0 | 30 |

Accepted by stage: {'spring_complete': 2, 'spring_open': 11}  
Rejected by stage: {'spring_open': 17, 'spring_complete': 43, 'spring_extend': 18}

Accepted orders carry the authenticated book only from the commit that added _control_book; an empty control group means not enough forward evidence yet, NOT that accepted orders behave like rejected ones.

## Matched on stage — the only fair comparison

| stage | population | n | min | median | max | crossing |
|---|---|---|---|---|---|---|
| spring_complete | accepted | 0 | None | None | None | None |
| spring_complete | rejected | 16 | -9.0 | 2.0 | 30.0 | 3 |
| spring_extend | accepted | 0 | None | None | None | None |
| spring_extend | rejected | 18 | -4.0 | 1.0 | 15.0 | 6 |
| spring_open | accepted | 11 | 1.0 | 2.0 | 8.0 | 0 |
| spring_open | rejected | 17 | -4.0 | -2.0 | 2.0 | 14 |

- **spring_complete** — NO_CONTROL_YET: no accepted order on this stage carries a book, so nothing can be concluded here
- **spring_extend** — NO_CONTROL_YET: no accepted order on this stage carries a book, so nothing can be concluded here
- **spring_open** — SEPARATED: accepted never cross and rejected mostly do, so the venue's rule is consistent on this stage

Our ask vs the authenticated ask, in ticks: n=62, min=-27.0, max=12.0, mean=-0.573, nonzero=46

## Rejections the venue's own book does not explain

| order | slug | stage | px | auth opp ask | true margin (ticks) | age s |
|---|---|---|---|---|---|---|
| CC7P2E6AJR42 | aec-setkameua-zhyole-strole-2026-09-08 | spring_extend_4 | 0.33 | 0.4800 | 15.0 | 2.5 |
| CC7NPY4Z2R3X | aec-setkameua-zhyole-strole-2026-09-08 | spring_extend_5 | 0.33 | 0.4800 | 15.0 | 2.5 |
| CC7NX7ZA4R40 | aec-setkameua-zhyole-strole-2026-09-08 | spring_extend_6 | 0.33 | 0.4800 | 15.0 | 2.5 |
| CC7NT4PBWR47 | aec-setkameua-zhyole-strole-2026-09-08 | spring_extend_2 | 0.40 | 0.4800 | 8.0 | 2.5 |
| CC7MXNKAMR3Z | aec-wta-elepri-aneolm-2026-09-07 | spring_complete_1 | 0.58 | 0.6500 | 7.0 | 3.7 |
| CC7DMK8CMR3Y | aec-atp-tiaper-martru-2026-09-08 | spring_complete_5 | 0.47 | 0.5200 | 5.0 | 2.6 |
| CC7DHBHFTR42 | aec-atp-tiaper-martru-2026-09-08 | spring_complete_6 | 0.47 | 0.5200 | 5.0 | 2.6 |
| CC5MNDZK2R45 | aec-atp-grilom-filpel-2026-09-07 | spring_extend_5 | 0.42 | 0.4500 | 3.0 | 2.1 |
| CC5MK2YPMR40 | aec-atp-grilom-filpel-2026-09-07 | spring_extend_6 | 0.42 | 0.4500 | 3.0 | 2.1 |
| CC5MFNF8CR3Y | aec-atp-grilom-filpel-2026-09-07 | spring_extend_1 | 0.42 | 0.4500 | 3.0 | 2.1 |
| CCBH9X9Q8R3W | aec-itfwo-arigee-joeste-2026-09-08 | spring_open_4 | 0.46 | 0.4800 | 2.0 | 2.2 |
| CBYDFP6SMQ3H | asc-cfb-smu-flst-2026-09-07-neg-6pt5 | spring_complete_5 | 0.52 | 0.5400 | 2.0 | 0.2 |
| CBYDFP7RGQ3H | asc-cfb-smu-flst-2026-09-07-neg-6pt5 | spring_complete_6 | 0.52 | 0.5400 | 2.0 | 0.2 |
| CBYHGNYXGQ3H | aec-itfwo-sofcos-hinino-2026-09-08 | spring_complete_6 | 0.57 | 0.5900 | 2.0 | 0.6 |
| CC6QBSW2CR40 | aec-cs2-prv-tyloo-2026-09-08 | spring_open_1 | 0.37 | 0.3900 | 2.0 | 2.4 |
| CBY75M06YQ3Z | aec-setkamemd-ghifio-butdum-2026-09-08 | spring_extend_5 | 0.38 | 0.3900 | 1.0 | 1.0 |
| CBY86SVQRQ45 | aec-setkamemd-ghifio-butdum-2026-09-08 | spring_complete_1 | 0.62 | 0.6300 | 1.0 | 1.6 |
| CC1E43MVJQ3X | aec-setkameua-vervas-kohboh-2026-09-08 | spring_complete_5 | 0.55 | 0.5600 | 1.0 | 0.8 |
| CC7CQ943MR47 | aec-atp-tiaper-martru-2026-09-08 | spring_extend_6 | 0.45 | 0.4600 | 1.0 | 2.9 |
| CC8RH70X8R42 | aec-atp-nikizq-santav-2026-09-08 | spring_extend_2 | 0.41 | 0.4200 | 1.0 | 2.4 |

A rejection is only EXPLAINED when the venue's own book, read within 10s, shows our price at or through the opposite ask. Everything else is reported as it is; nothing is inferred.
