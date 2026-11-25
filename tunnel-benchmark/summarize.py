import csv, json, statistics as st, sys
from collections import defaultdict
from pathlib import Path
OUT = Path(__file__).resolve().parent
ORDER = ['direct', 'wireguard', 'tailscale', 'rayfish-standalone', 'rayfish-fq-codel']
rows = [json.loads(l) for l in (OUT / 'raw.jsonl').read_text().splitlines()]
rows = [r for r in rows if r['repeat'] >= 1]
groups = defaultdict(list)
for r in rows:
    groups[(r['profile'], r['workload'], r['flavor'])].append(r)

def med(values):
    values = [v for v in values if v is not None]
    return st.median(values) if values else None

def cores(r):
    return sum(r['cpu'][s]['busy_s'] / r['cpu'][s]['elapsed_s'] for s in ('src', 'dst'))

def daemon_cores(r):
    return sum(r['cpu'][s]['daemon_s'] / r['cpu'][s]['elapsed_s'] for s in ('src', 'dst'))

out = []
for profile in ['unshaped', 'shaped-0', 'shaped-1']:
    for workload in ['tcp', 'tcp4', 'mixed-kernel']:
        for flavor in ORDER:
            g = groups.get((profile, workload, flavor))
            if not g:
                continue
            mbps = [r['bulk']['mbps'] for r in g]
            out.append({
                'profile': profile, 'workload': workload, 'flavor': flavor, 'runs': len(g),
                'mbps': med(mbps), 'mbps_min': min(mbps), 'mbps_max': max(mbps),
                'retransmits': med([r['bulk'].get('retransmits') for r in g]),
                'small_tcp_mbps': med([r['small']['mbps'] if r['small'] else None for r in g]),
                'idle_p50_ms': med([r['idle']['p50_ms'] for r in g]),
                'loaded_p50_ms': med([r['loaded']['p50_ms'] for r in g]),
                'loaded_p99_ms': med([r['loaded']['p99_ms'] for r in g]),
                'ping_loss_pct': med([r['loaded']['loss_pct'] for r in g]),
                'cpu_cores_both_hosts': med([cores(r) for r in g]),
                'daemon_cores_both_hosts': med([daemon_cores(r) for r in g]),
                'cores_per_gbit': med([cores(r) / (r['bulk']['mbps'] / 1000) for r in g]),
                'netem_avg_skb': med([r['cpu']['src']['netem_bytes'] / r['cpu']['src']['netem_packets'] if r['cpu']['src']['netem_packets'] else None for r in g]),
                'netem_drops': med([r['cpu']['src']['netem_drops'] for r in g]),
            })
with (OUT / 'summary.csv').open('w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]))
    w.writeheader(); w.writerows(out)
def fmt(v, nd=1):
    return '-' if v is None else f'{v:.{nd}f}'
last = None
for o in out:
    head = (o['profile'], o['workload'])
    if head != last:
        print(f"\n## {o['profile']} / {o['workload']}")
        print('flavor               runs   Mbit/s (min-max)        retr  small  idle50  p50    p99    pingloss  cores  daemon  cores/Gbit  skb   drops')
        last = head
    print(f"{o['flavor']:<20} {o['runs']:<5}  {fmt(o['mbps']):>7} ({fmt(o['mbps_min'])}-{fmt(o['mbps_max'])})".ljust(52),
          f"{fmt(o['retransmits'],0):>5} {fmt(o['small_tcp_mbps'],3):>6} {fmt(o['idle_p50_ms'],2):>6} {fmt(o['loaded_p50_ms']):>6} {fmt(o['loaded_p99_ms']):>6} {fmt(o['ping_loss_pct']):>8} {fmt(o['cpu_cores_both_hosts'],2):>6} {fmt(o['daemon_cores_both_hosts'],2):>7} {fmt(o['cores_per_gbit'],2):>10} {fmt(o['netem_avg_skb'],0):>5} {fmt(o['netem_drops'],0):>7}")
