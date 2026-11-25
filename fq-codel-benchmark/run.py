import concurrent.futures, json, math, re, shlex, subprocess, time
from pathlib import Path
from setup import OUT, HOSTS, parallel, remote, status, connection, connected
INFO=json.loads((OUT/'hosts.json').read_text())
CONTROLLERS=['cubic','loss-tolerant','fq-codel']

def shape(rate=100,delay=20,loss=0):
 def apply(h):
  other=HOSTS[1-HOSTS.index(h)]
  lines=['set -e']
  for dev in INFO[h]['interfaces']:
   lines.append(f'tc qdisc del dev {dev} root 2>/dev/null || true')
   if not rate:continue
   lines += [f'tc qdisc add dev {dev} root handle 1: htb default 20',f'tc class add dev {dev} parent 1: classid 1:20 htb rate 10gbit',f'tc class add dev {dev} parent 1: classid 1:10 htb rate {rate}mbit ceil {rate}mbit',f'tc qdisc add dev {dev} parent 1:10 handle 10: netem delay {delay}ms loss random {loss}% limit 1000']
   for addr in INFO[other]['addresses']:
    proto,match,suffix=('ipv6','ip6','/128') if ':' in addr else ('ip','ip','/32')
    lines.append(f'tc filter add dev {dev} protocol {proto} parent 1: prio {2 if proto=="ipv6" else 1} u32 match {match} dst {addr}{suffix} flowid 1:10')
  remote(h,'\n'.join(lines))
 parallel(apply)

def configure(controller):
 parallel(lambda h:remote(h,f'set -e\nray config set quic-congestion {controller} >/dev/null\nray restart >/dev/null',timeout=120))
 return connected()

def ping_stats(output,skip=200):
 values=[];sent=received=0
 for line in output.splitlines():
  m=re.search(r'icmp_seq=(\d+).*time[=<]([\d.]+)',line)
  if m and int(m[1])>skip:values.append(float(m[2]))
  m=re.search(r'(\d+) packets transmitted, (\d+) received',line)
  if m:sent,received=map(int,m.groups())
 values.sort()
 def p(q):return values[max(0,math.ceil(len(values)*q)-1)] if values else None
 return {'samples':len(values),'mean_ms':sum(values)/len(values) if values else None,'p50_ms':p(.5),'p95_ms':p(.95),'p99_ms':p(.99),'max_ms':max(values) if values else None,'loss_pct':100*(sent-received)/sent if sent else None}

MEASURE="""import json, subprocess, signal, tempfile, time
ping_file=tempfile.TemporaryFile(mode='w+t')
small=None
if SMALL:
 small=subprocess.Popen(SMALL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 time.sleep(2)
ping_args=['ping','-n','-i','0.01',TARGET]
if SMALL:ping_args+=['-c','600','-W','2']
ping=subprocess.Popen(ping_args,stdout=ping_file,stderr=subprocess.PIPE,text=True)
bulk=subprocess.Popen(BULK,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
 if SMALL:ping.communicate(timeout=22)
 bout,berr=bulk.communicate(timeout=DURATION+30)
 if small:sout,serr=small.communicate(timeout=30)
 else:sout=serr=''
finally:
 if ping.poll() is None:
  ping.send_signal(signal.SIGINT)
  ping.communicate(timeout=6)
 if bulk.poll() is None:bulk.kill();bulk.wait()
 if small and small.poll() is None:small.kill();small.wait()
ping_file.seek(0)
print(json.dumps({'bulk':json.loads(bout),'bulk_exit':bulk.returncode,'bulk_stderr':berr,'small':json.loads(sout) if sout else None,'small_exit':small.returncode if small else None,'small_stderr':serr,'ping':ping_file.read()}))
"""


def received(iperf,udp=False,window=None):
 receiver=iperf.get('server_output_json',iperf) if udp or window else iperf
 if udp and 'server_output_json' not in iperf:raise RuntimeError('UDP receiver output missing')
 total=receiver['end'].get('sum_received',receiver['end'].get('sum'))
 rate=total['bits_per_second']/1e6
 if udp:
  intervals=[x['sum'] for x in receiver['intervals'] if not x['sum'].get('omitted') and x['sum']['start']>=.99 and x['sum']['end']<=10.01 and abs(x['sum']['seconds']-(x['sum']['end']-x['sum']['start']))<.05]
  if not intervals:raise RuntimeError('No valid UDP receiver intervals')
  rate=sum(x['bytes'] for x in intervals)*8/sum(x['seconds'] for x in intervals)/1e6
 if window:
  intervals=[x['sum'] for x in receiver['intervals'] if x['sum']['start']>=window[0]-.01 and x['sum']['end']<=window[1]+.01]
  if not intervals:raise RuntimeError('No small TCP receiver intervals in load window')
  rate=sum(x['bytes'] for x in intervals)*8/sum(x['end']-x['start'] for x in intervals)/1e6
 streams=[s['receiver']['bits_per_second']/1e6 for s in receiver['end'].get('streams',[]) if 'receiver' in s]
 jain=sum(streams)**2/(len(streams)*sum(x*x for x in streams)) if streams and sum(streams)>0 else None
 return {'mbps':rate,'udp_loss_pct':total.get('lost_percent'),'stream_mbps':streams,'jain':jain}

def measure(source,destination,target,controller,loss,repeat,workload,direction,duration=10):
 ports=[5201,5202] if workload.startswith('mixed') else [5201]
 lines=['set -e','pkill -x iperf3 2>/dev/null || true']
 for port in ports:lines.append(f'iperf3 -s -D -1 -B {shlex.quote(target)} -p {port} --pidfile /run/iperf-{port}.pid -J')
 if workload=='mixed-kernel':lines.append('rm -f /tmp/small-window.json /tmp/small-window.err\nnohup python3 /tmp/tcp_window.py >/tmp/small-window.json 2>/tmp/small-window.err </dev/null &')
 lines.append('sleep 0.3');remote(destination,'\n'.join(lines))
 bulk=['iperf3','-c',target,'-p','5201','-J','-t',str(duration),'-O','2','--get-server-output']
 if workload.startswith('tcp4'):bulk+=['-P','4']
 if workload.startswith('mixed'):bulk+=['-u','-b','150M','-l','1000']
 small=['iperf3','-c',target,'-p','5202','-J','-t','20','-b','1M','-l','1024','--get-server-output'] if workload.startswith('mixed') else []
 before=parallel(connection)
 script=f'TARGET={target!r}\nBULK={bulk!r}\nSMALL={small!r}\nDURATION={duration!r}\n'+MEASURE
 result=json.loads(remote(source,"python3 - <<'PY'\n"+script+'\nPY',timeout=duration+60))
 (OUT/'last-measure.json').write_text(json.dumps(result))
 if result['bulk_exit'] or 'error' in result['bulk']:raise RuntimeError(f'Bulk iperf failed: {result}')
 if result['small'] and (result['small_exit'] or 'error' in result['small']):raise RuntimeError(f'Small iperf failed: {result}')
 small_window=json.loads(remote(destination,'cat /tmp/small-window.json')) if workload=='mixed-kernel' else None
 row={'controller':controller,'loss_pct':loss,'repeat':repeat,'duration_s':duration,'workload':workload,'direction':direction,'bulk':received(result['bulk'],udp=workload.startswith('mixed')),'small':small_window if small_window else (received(result['small'],window=(4,14)) if result['small'] else None),'loaded':ping_stats(result['ping']),'connections_before':before,'connections_after':parallel(connection),'raw':result}
 if not all(x and x['conn_type'].lower()=='direct' for x in row['connections_after']):raise RuntimeError('Benchmark path ceased to be direct')
 with (OUT/'raw.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
 print(json.dumps({k:row[k] for k in ['controller','loss_pct','repeat','workload','direction','bulk','small','loaded']}),flush=True)
 remote(destination,'pkill -x iperf3 2>/dev/null || true')
 return row

if __name__=='__main__':
 completed=set()
 if (OUT/'raw.jsonl').exists():
  for line in (OUT/'raw.jsonl').read_text().splitlines():
   r=json.loads(line);completed.add((r['loss_pct'],r['repeat'],r['controller'],r['workload'],r['direction']))
 try:
  for loss,workloads in [(0,['tcp','tcp4','mixed-kernel']),(1,['tcp'])]:
   shape(loss=loss)
   for repeat in range(1,4):
    order=CONTROLLERS[repeat-1:]+CONTROLLERS[:repeat-1]
    for controller in order:
     for workload in workloads:
      for i,direction in [(0,'A-to-B'),(1,'B-to-A')]:
       if (loss,repeat,controller,workload,direction) in completed:continue
       vpn=configure(controller)
       idle=ping_stats(remote(HOSTS[i],f'ping -n -c 200 -i 0.01 -W 2 {shlex.quote(vpn[1-i])}'),skip=0)
       with (OUT/'idle.jsonl').open('a') as f:f.write(json.dumps({'controller':controller,'loss_pct':loss,'repeat':repeat,'duration_s':10,'workload':workload,'direction':direction,'idle':idle})+'\n')
       measure(HOSTS[i],HOSTS[1-i],vpn[1-i],controller,loss,repeat,workload,direction)
 finally:
  shape(rate=0)
  parallel(lambda h:remote(h,'pkill -x iperf3 2>/dev/null || true'))
