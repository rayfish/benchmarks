import json
import run as bench
completed=set()
for line in (bench.OUT/'raw.jsonl').read_text().splitlines():
 r=json.loads(line)
 if r['workload']=='tcp4-long':completed.add((r['controller'],r['direction']))
try:
 bench.shape()
 for controller in bench.CONTROLLERS:
  for i,direction in [(0,'A-to-B'),(1,'B-to-A')]:
   if (controller,direction) in completed:continue
   vpn=bench.configure(controller)
   r=bench.measure(bench.HOSTS[i],bench.HOSTS[1-i],vpn[1-i],controller,0,1,'tcp4-long',direction,duration=60)
   receiver=r['raw']['bulk']['server_output_json']
   intervals=[x['sum'] for x in receiver['intervals'] if not x['sum'].get('omitted') and x['sum']['start']>=30 and x['sum']['end']<=60.01]
   r['last_30s_receiver_mbps']=sum(x['bytes'] for x in intervals)*8/sum(x['seconds'] for x in intervals)/1e6
   with (bench.OUT/'long-summary.jsonl').open('a') as f:f.write(json.dumps({'controller':controller,'direction':direction,'mean_mbps':r['bulk']['mbps'],'last_30s_mbps':r['last_30s_receiver_mbps'],'loaded':r['loaded']})+'\n')
   print('LONG:',controller,direction,'last 30 seconds:',r['last_30s_receiver_mbps'],flush=True)
finally:
 bench.shape(rate=0)
 bench.parallel(lambda h:bench.remote(h,'pkill -x iperf3 2>/dev/null || true'))
