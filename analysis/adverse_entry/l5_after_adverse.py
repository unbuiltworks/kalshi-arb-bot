"""What does L5 do after an opening fill that immediately moves against him?

Our 1945z loss was: open UP at 0.44, price collapses to 0.05, strand, settle 0.
The benchmark question is not "should we have completed" -- the controller
correctly refused that -- it is what L5 DOES in the same situation.

Method, from L5's own 10.5k BUY rows: find each market's opening buy from flat,
then follow his own subsequent prints in that market as the price path. Classify
the move over the next action, and classify what he did next: EXTEND (same
outcome), COMPLETE (other outcome), or STOP (nothing more in that market).
Adverse = the price of the outcome he holds fell.
"""
import json, collections, statistics as st

per = collections.defaultdict(list)
for line in open('data/l5/raw/trades.jsonl'):
    try: d = json.loads(line)
    except Exception: continue
    for t in d.get('payload') or []:
        if t.get('side') != 'BUY': continue
        per[t['slug']].append((t['timestamp'], t['outcome'],
                               float(t['price']), float(t['size'])))

rows = []
for slug, tr in per.items():
    tr.sort()
    inv = 0.0
    for i, (ts, out, px, sz) in enumerate(tr):
        opening = abs(inv) < 1e-9
        inv += sz if out == 'Up' else -sz
        if not opening:
            continue
        # the price path of the OUTCOME HE JUST BOUGHT, from his own later prints
        later = [(t2, o2, p2, s2) for (t2, o2, p2, s2) in tr[i+1:]]
        same = [(t2, p2) for (t2, o2, p2, s2) in later if o2 == out]
        nxt = later[0] if later else None
        if nxt is None:
            rows.append(dict(slug=slug, px=px, move=None, nxt='STOP', dt=None))
            continue
        # move: his next print on the SAME outcome, else the implied complement
        if same:
            mv = same[0][1] - px
        else:
            mv = (1.0 - later[0][2]) - px        # other outcome implies ours
        nxt_kind = 'EXTEND' if nxt[1] == out else 'COMPLETE'
        rows.append(dict(slug=slug, px=px, move=mv, nxt=nxt_kind,
                         dt=nxt[0] - ts, nxt_px=nxt[2]))

print("L5 opening fills from flat: %d across %d markets" % (len(rows), len(per)))
adv = [r for r in rows if r['move'] is not None and r['move'] < 0]
fav = [r for r in rows if r['move'] is not None and r['move'] > 0]
flat = [r for r in rows if r['move'] is not None and r['move'] == 0]
print("  price moved AGAINST him next: %d (%.1f%%)" % (len(adv), 100*len(adv)/len(rows)))
print("  moved for him:                %d (%.1f%%)" % (len(fav), 100*len(fav)/len(rows)))
print("  unchanged:                    %d (%.1f%%)" % (len(flat), 100*len(flat)/len(rows)))
print()
print("WHAT HE DOES NEXT, conditioned on the move (the benchmark):")
print("  %-22s %6s %8s %8s %8s %10s" % ("after an opening fill","n","EXTEND","COMPLETE","STOP","med gap s"))
for lab, g in (("price AGAINST him", adv), ("price FOR him", fav), ("unchanged", flat)):
    if not g: continue
    c = collections.Counter(r['nxt'] for r in g)
    gaps = [r['dt'] for r in g if r['dt'] is not None]
    print("  %-22s %6d %7.1f%% %7.1f%% %7.1f%% %10.0f"
          % (lab, len(g), 100*c['EXTEND']/len(g), 100*c['COMPLETE']/len(g),
             100*c['STOP']/len(g), st.median(gaps) if gaps else -1))
allc = collections.Counter(r['nxt'] for r in rows)
print("  %-22s %6d %7.1f%% %7.1f%% %7.1f%%"
      % ("ALL", len(rows), 100*allc['EXTEND']/len(rows),
         100*allc['COMPLETE']/len(rows), 100*allc['STOP']/len(rows)))
print()
print("ADVERSE MOVE SIZE when it goes against him:")
m = sorted(-r['move'] for r in adv)
if m:
    print("  median %.3f  p75 %.3f  p90 %.3f  max %.3f" %
          (m[len(m)//2], m[3*len(m)//4], m[int(.9*len(m))], m[-1]))
    big = [r for r in adv if -r['move'] >= 0.10]
    print("  moves >= 0.10 (our 1945z was 0.39): %d of %d adverse (%.1f%%)"
          % (len(big), len(adv), 100*len(big)/len(adv)))
    if big:
        c = collections.Counter(r['nxt'] for r in big)
        print("    after a >=0.10 adverse move he: EXTEND %.1f%%  COMPLETE %.1f%%  STOP %.1f%%"
              % (100*c['EXTEND']/len(big), 100*c['COMPLETE']/len(big), 100*c['STOP']/len(big)))
json.dump(rows, open('analysis/adverse_entry/l5_openings.json','w'))
