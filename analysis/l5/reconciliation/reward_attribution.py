from decimal import Decimal as D
# PAID rows visible in the 2026-11-18T14:07:20Z REWARD_CHECK rows_brief sample
PAID = [
 ("aec-cs2-ep-ytigeres-2026-11-16","0.91"),("aec-cs2-hec-jumbo-2026-11-16","0.02"),
 ("aec-cs2-hero-prv-2026-11-16","0.02"),("aec-cs2-vp-priv-2026-11-16","0.03"),
 ("aec-cs2-ytigeres-jumbo-2026-11-16","0.14"),("aec-dota2-gl-lgd-2026-11-16","0.01"),
 ("aec-atp-alecan-robcat-2026-11-15","0.02"),("aec-atp-filmor-elicou-2026-11-15","0.01"),
 ("aec-atp-fralla-brufer-2026-11-15","0.01"),("aec-atp-frarib-thicig-2026-11-15","0.01"),
 ("aec-atp-fraron-nikizq-2026-11-15","0.06"),
 ("aec-atpdb-liutarpieczo-paulvocel-2026-11-15","0.01"),
 ("aec-cs2-bar-eyx-2026-11-15","0.04"),("aec-cs2-brute-eid-2026-11-15","0.56"),
 ("aec-cs2-btf-baks-2026-11-15","0.24"),("aec-cs2-btf-ht-2026-11-15","0.36"),
 ("aec-cs2-mzp-aces-2026-11-15","0.19"),("aec-cs2-priv-bca-2026-11-15","0.05"),
 ("aec-cs2-prv-100t-2026-11-15","0.03"),("aec-cs2-yl-bang-2026-11-15","0.01"),
 ("aec-dota2-aur-konca-2026-11-15","0.04"),("aec-dota2-fro-leg-2026-11-15","0.35"),
 ("aec-dota2-hul-ch-2026-11-15","0.09"),("aec-dota2-nemiga-iac-2026-11-15","0.03"),
 ("aec-setkamecz-chajos-heiluk-2026-11-15","0.01"),
 ("aec-setkamemd-marant-pansvi-2026-11-15","0.03"),
 ("aec-setkameua-skhser-konvas-2026-11-15","0.77"),
 ("aec-wta-zhikul-valstr-2026-11-15","0.03"),("aec-atp-abeshe-danmil-2026-11-14","0.03"),
 ("aec-cfb-bryant-sacred-2026-11-14","0.07"),("aec-cfb-flatl-tulsa-2026-11-14","0.01"),
 ("aec-cfb-flint-del-2026-11-14","0.01"),("aec-cfb-jackst-alast-2026-11-14","0.08"),
 ("aec-cfb-lehi-rich-2026-11-14","0.04"),("aec-cfb-nmxst-msrst-2026-11-14","0.01"),
 ("aec-cfb-pvam-arpb-2026-11-14","0.17"),("aec-cfb-stnh-robms-2026-11-14","0.16"),
 ("aec-cfb-vill-holy-2026-11-14","0.01"),("aec-cfb-wcar-samf-2026-11-14","0.17"),
]
# markets the SPORTS BOT's own book holds (keys of realized_by_market,
# ACCOUNT_AUDIT 2026-11-18T14:08:01Z) -- these are markets L5 traded
BOT = set("""aec-acb-flc-man-2026-11-15 aec-atp-abeshe-danmil-2026-11-14
aec-atp-aidmch-jerzha-2026-11-16 aec-atp-alecan-robcat-2026-11-15
aec-atp-buvgad-adrgob-2026-11-15 aec-atp-colwel-zacste-2026-11-15
aec-atp-edwwin-enzagu-2026-11-17 aec-atp-filmor-elicou-2026-11-15
aec-atp-fralla-brufer-2026-11-15 aec-atp-frarib-thicig-2026-11-15
aec-atp-fraron-nikizq-2026-11-15 aec-atp-hynbar-alegal-2026-11-16
aec-atp-jaycla-arvnor-2026-11-15 aec-atp-jurrod-nickja-2026-11-18
aec-atp-lasdje-jacfea-2026-11-18 aec-atp-maxgur-oribat-2026-11-17
aec-atp-micrib-edabut-2026-11-16 aec-atp-viggog-maxwis-2026-11-17
aec-atp-zacvii-geobla-2026-11-17 aec-atpdb-arevalpavic-arribaolivet-2026-11-15
aec-atpdb-barryingild-dominkplanin-2026-11-15
aec-atpdb-liutarpieczo-paulvocel-2026-11-15 aec-cbb-penn-stjoe-2026-11-14
aec-cfb-applst-marsh-2026-11-14 aec-cfb-arzst-ucf-2026-11-14
aec-cfb-aubrn-mspst-2026-11-14 aec-cfb-bryant-sacred-2026-11-14
aec-cfb-cin-iowast-2026-11-14 aec-cfb-ecar-charlt-2026-11-14
aec-cfb-fl-uk-2026-11-14 aec-cfb-flatl-tulsa-2026-11-14
aec-cfb-flint-del-2026-11-14 aec-cfb-frest-txst-2026-11-14
aec-cfb-gas-gast-2026-11-14 aec-cfb-jackst-alast-2026-11-14
aec-cfb-kan-wvir-2026-11-14 aec-cfb-lehi-rich-2026-11-14
aec-cfb-lou-ncar-2026-11-14 aec-cfb-nmxst-msrst-2026-11-14
aec-cfb-ntx-utsa-2026-11-14 aec-cfb-presb-more-2026-11-14
aec-cfb-pvam-arpb-2026-11-14 aec-cfb-sc-ark-2026-11-14
aec-cfb-stan-vtech-2026-11-14 aec-cfb-stnh-robms-2026-11-14
aec-cfb-syra-ncst-2026-11-14 aec-cfb-tenn-txam-2026-11-14
aec-cfb-troy-sala-2026-11-14 aec-cfb-tulane-rice-2026-11-14
aec-cfb-tx-lsu-2026-11-14 aec-cfb-utah-arz-2026-11-14
aec-cfb-utahst-sdst-2026-11-14 aec-cfb-vill-holy-2026-11-14
aec-cfb-wash-mst-2026-11-14 aec-cfb-wcar-samf-2026-11-14
aec-cfb-webst-narz-2026-11-14 aec-cs2-all-nvo-2026-11-14
aec-cs2-arr-reda-2026-11-17 aec-cs2-baks-ill-2026-11-14
aec-cs2-bar-eyx-2026-11-15 aec-cs2-bca-big-2026-11-18
aec-cs2-brute-eid-2026-11-15 aec-cs2-btf-baks-2026-11-15
aec-cs2-btf-ht-2026-11-15 aec-cs2-envy-mzp-2026-11-17
aec-cs2-ep-ytigeres-2026-11-16 aec-cs2-forzer-nxs-2026-11-15
aec-cs2-hero-prv-2026-11-16 aec-cs2-mzp-aces-2026-11-15
aec-cs2-priv-bca-2026-11-15 aec-cs2-prv-100t-2026-11-15
aec-cs2-sta-ttbs-2026-11-18 aec-cs2-vp-priv-2026-11-16
aec-cs2-ww-g2a-2026-11-15 aec-cs2-yl-bang-2026-11-15
aec-cs2-ytigeres-jumbo-2026-11-16 aec-dota2-1win-fro-2026-11-14
aec-dota2-aur-konca-2026-11-15 aec-dota2-aur-pari-2026-11-18
aec-dota2-fro-leg-2026-11-15 aec-dota2-gl-lgd-2026-11-16
aec-dota2-hul-ch-2026-11-15 aec-dota2-nemiga-iac-2026-11-15
aec-dota2-xe-ch-2026-11-17 aec-setkamecz-chajos-heiluk-2026-11-15
aec-setkamemd-marant-pansvi-2026-11-15 aec-setkameua-skhser-konvas-2026-11-15
aec-wta-zhikul-valstr-2026-11-15""".split())

ours = [(s,D(v)) for s,v in PAID if s in BOT]
notours = [(s,D(v)) for s,v in PAID if s not in BOT]
tot = sum(v for _,v in [(s,D(v)) for s,v in PAID])
print("VISIBLE PAID rows in the sample: %d, summing $%s" % (len(PAID), tot))
print("  on markets the L5 sports bot traded : %2d rows  $%s" % (len(ours), sum(v for _,v in ours)))
print("  NOT on any market the bot traded    : %2d rows  $%s" % (len(notours), sum(v for _,v in notours)))
print()
print("venue totals from the same line: PAID n=89 $14.13 | PENDING n=11 $0.89 | SKIPPED n=58 $3.47")
print("rows_on_our_markets = 50 of 158 (all statuses)")
print()
print("not-ours rows:", [s for s,_ in notours] or "none")
