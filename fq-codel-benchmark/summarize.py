import csv,json,statistics
from collections import defaultdict
from pathlib import Path
OUT=Path(__file__).resolve().parent
all_rows=[json.loads(x) for x in (OUT/'raw.jsonl').read_text().splitlines()]
rows=[r for r in all_rows if r['workload'] in ('tcp','tcp4','mixed-kernel')]
groups=defaultdict(list)
for r in rows:groups[(r['loss_pct'],r['workload'],r['direction'],r['controller'])].append(r)
def median(rs,fn):
 v=[fn(r) for r in rs];v=[x for x in v if x is not None]
 return statistics.median(v) if v else None
def fmt(x):return '-' if x is None else f'{x:.2f}'
summary=[]
for (loss,workload,direction,controller),rs in groups.items():
 summary.append({'loss_pct':loss,'workload':workload,'direction':direction,'controller':controller,'runs':len(rs),'bulk_mbps':median(rs,lambda r:r['bulk']['mbps']),'small_tcp_mbps':median(rs,lambda r:r['small']['mbps'] if r['small'] else None),'loaded_p50_ms':median(rs,lambda r:r['loaded']['p50_ms']),'loaded_p99_ms':median(rs,lambda r:r['loaded']['p99_ms']),'ping_loss_pct':median(rs,lambda r:r['loaded']['loss_pct']),'four_tcp_jain':median(rs,lambda r:r['bulk']['jain'] if r['workload']=='tcp4' else None)})
summary.sort(key=lambda r:(r['loss_pct'],r['workload'],r['direction'],r['controller']))
with (OUT/'summary.csv').open('w') as f:
 w=csv.DictWriter(f,fieldnames=list(summary[0]));w.writeheader();w.writerows(summary)
lines=['# FQ-CoDel link benchmark','', 'Two dedicated-CPU Linux droplets, same release binary and direct QUIC paths. Each direction is limited to 100 Mbit/s with 20 ms added delay, giving about 40 ms idle RTT. Netem queue limit: 1,000 packets. Three trials per flavor, workload, and direction; flavor order rotates between repetitions. Both peers restart before every trial.','', 'Bulk tests use 10 measured seconds after 2 warmup seconds. Ping runs at 100 probes/s, excluding its first 200 probes from latency percentiles. TCP4 uses four parallel bulk TCP flows. Mixed-kernel traffic offers 150 Mbit/s UDP with 1,000-byte payloads plus a TCP flow capped at 1 Mbit/s, writing 1,024-byte blocks. The TCP flow starts 2 seconds before UDP. Its throughput is measured from kernel TCP byte counters between seconds 4 and 14, while bulk traffic is active. Its 600-probe ping finishes while bulk traffic is still active. Exploratory trials and pilot observations are excluded from these results.','', 'Values below are medians of per-run measurements, not percentiles pooled across runs. Throughput uses receiver data; UDP uses valid steady-state receiver intervals. A Jain index of 1 means equal throughput among the four TCP flows.','', '| Loss (%) | Workload | Direction | Flavor | Runs | Bulk Mbit/s | Small TCP Mbit/s | Ping p50 ms | Ping p99 ms | Ping loss (%) | TCP4 Jain |','|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
for r in summary:
 lines.append('| '+' | '.join(str(r[k]) if k in ['loss_pct','workload','direction','controller','runs'] else fmt(r[k]) for k in ['loss_pct','workload','direction','controller','runs','bulk_mbps','small_tcp_mbps','loaded_p50_ms','loaded_p99_ms','ping_loss_pct','four_tcp_jain'])+' |')
long_rows=[r for r in all_rows if r['workload']=='tcp4-long']
if long_rows:
 lines += ['', 'Four-flow TCP checks ran for 60 measured seconds after 2 warmup seconds. Each flavor was tested once in each direction. The last-30-second rate uses receiver interval bytes and elapsed seconds.', '', '| Flavor | Direction | Full-run Mbit/s | Last 30 seconds Mbit/s |', '|---|---|---:|---:|']
 for r in long_rows:
  intervals=[x['sum'] for x in r['raw']['bulk']['server_output_json']['intervals'] if not x['sum'].get('omitted') and x['sum']['start']>=30 and x['sum']['end']<=60.01]
  rate=sum(x['bytes'] for x in intervals)*8/sum(x['seconds'] for x in intervals)/1e6
  lines.append(f"| {r['controller']} | {r['direction']} | {r['bulk']['mbps']:.2f} | {rate:.2f} |")
lines += ['', 'This compares each complete flavor, including its QUIC buffer size. It does not isolate the scheduler from the buffer-size change, emulate Wi-Fi, or establish long-term fairness. The pilot observations are separate and excluded.','', f'Completed controlled observations: {len(rows)} short trials and {len(long_rows)} long trials.']
(OUT/'report.md').write_text('\n'.join(lines)+'\n')
print(f'{len(rows)} observations summarized.')
for r in summary:
 if r['workload'].startswith('mixed'):print(json.dumps(r))
