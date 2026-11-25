import json
import statistics
from collections import defaultdict
from pathlib import Path
folder=Path(__file__).resolve().parent
rows=[json.loads(line) for line in (folder/'raw.jsonl').read_text().splitlines()]
groups=defaultdict(list)
for row in rows:
    if row['udp'] and 'server_output_json' not in row['iperf']:
        continue
    groups[(row['profile'],row['controller'],row['streams'],row['udp'],row['direct'])].append(row)
print('profile | controller | flows | UDP | repeats | throughput median Mbit/s | range | idle median ms | loaded median ms | loaded p99 median ms')
for (profile,controller,streams,udp,direct),measurements in groups.items():
    mbps=[r['mbps'] for r in measurements]
    idle=[r['idle']['p50_ms'] for r in measurements]
    median=[r['loaded']['p50_ms'] for r in measurements]
    p99=[r['loaded']['p99_ms'] for r in measurements]
    print(f'{profile} | {controller} | {streams} | {udp} | {len(measurements)} | {statistics.median(mbps):.2f} | {min(mbps):.2f}-{max(mbps):.2f} | {statistics.median(idle):.2f} | {statistics.median(median):.2f} | {statistics.median(p99):.2f}')
    if udp:
        print('UDP loss:',[r.get('udp_loss_pct') for r in measurements])
print('Completed measurements:',len(rows))
