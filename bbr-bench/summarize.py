#!/usr/bin/env python3
import json, os, statistics as st, collections
rows = [json.loads(l) for l in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'raw.jsonl'))]
cells = collections.defaultdict(list)
for r in rows:
    if 'error' not in r:
        cells[(r['flavor'], r['loss'], r['cc'])].append(r)
errs = collections.Counter((r['flavor'], r['loss'], r['cc']) for r in rows if 'error' in r)
med = lambda xs: round(st.median(xs), 2) if xs else None
print(f"{'flavor':8} {'loss%':5} {'cc':6} {'n':>2} {'Mbit/s':>8} {'min-max':>13} {'retr':>6} {'p50':>7} {'p99':>7} {'pingloss':>8} {'conn'}")
for k in sorted(cells, key=lambda k: (k[0], float(k[1]), k[2])):
    v = cells[k]
    m = [r['mbps'] for r in v]
    conns = collections.Counter(str(r.get('conn', '-')) for r in v)
    ccs = {r.get('sender_cc') for r in v}
    print(f"{k[0]:8} {k[1]:5} {k[2]:6} {len(v):>2} {med(m):>8} {min(m):>6}-{max(m):<6} {med([r['retr'] for r in v]):>6} "
          f"{med([r['ping_p50'] for r in v if r['ping_p50'] is not None])!s:>7} {med([r['ping_p99'] for r in v if r['ping_p99'] is not None])!s:>7} "
          f"{med([r['ping_loss'] for r in v if r['ping_loss'] is not None])!s:>8} {dict(conns)} cc={ccs}")
for k, n in errs.items():
    print('errors', k, n)
