import os
import concurrent.futures, hashlib, json, shlex, subprocess, time
from pathlib import Path
OUT=Path(__file__).resolve().parent
SSH=['ssh','-o','StrictHostKeyChecking=accept-new','-o',f'UserKnownHostsFile={OUT / "known_hosts"}','-o','ConnectTimeout=8','-o','BatchMode=yes']
HOSTS=[r.split()[1] for r in (OUT/'.servers').read_text().splitlines()]

def remote(host,command,timeout=120):
 r=subprocess.run(SSH+[f'root@{host}','bash -s'],input=command,text=True,capture_output=True,timeout=timeout)
 if r.returncode:raise RuntimeError(f'Remote command failed ({r.returncode}): {r.stderr[-1800:]} {r.stdout[-600:]}')
 return r.stdout

def parallel(fn):
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as p:return list(p.map(fn,HOSTS))

def status(h):return json.loads(remote(h,'ray status --json'))
def net(h):return status(h)['networks'][0]
def connection(h):return net(h)['peers'][0]['connection']
def connected():
 for _ in range(60):
  try:
   ips=[net(h)['my_ipv6'] for h in HOSTS]
   remote(HOSTS[0],f'ping -n -c 2 -W 2 {shlex.quote(ips[1])}')
   c=[connection(h) for h in HOSTS]
   if all(x and x['conn_type'].lower()=='direct' for x in c):return ips
  except (RuntimeError,KeyError,IndexError,TypeError):pass
  time.sleep(2)
 raise RuntimeError('Direct QUIC connection did not converge')

if __name__=='__main__':
 binary=Path(os.environ['RAY_BINARY']).resolve(strict=True)
 def prepare(h):
  for _ in range(60):
   try:remote(h,'true',timeout=12);break
   except (RuntimeError,subprocess.TimeoutExpired):time.sleep(3)
  else:raise RuntimeError('SSH did not become available')
  remote(h,'cloud-init status --wait >/dev/null',timeout=300)
  remote(h,"set -e\nprintf 'iperf3 iperf3/start_daemon boolean false\\n' | debconf-set-selections\napt-get update -qq\nDEBIAN_FRONTEND=noninteractive apt-get install -y -qq iperf3 iproute2 iputils-ping python3 >/dev/null",timeout=240)
  subprocess.run(['scp',*SSH[1:],str(binary),f'root@{h}:/tmp/ray'],check=True,capture_output=True)
  remote(h,'set -e\ngetent group rayfish >/dev/null || groupadd rayfish\ninstall -m 755 /tmp/ray /usr/local/bin/ray\nray up >/dev/null\nray firewall off >/dev/null')
  return 'Host prepared'
 print('\n'.join(parallel(prepare)),flush=True)
 create=remote(HOSTS[0],'ray create --open --name benchmark --hostname bench-a',timeout=120)
 (OUT/'create.txt').write_text(create)
 room=net(HOSTS[0])['network_key']
 (OUT/'join.txt').write_text(remote(HOSTS[1],f'ray join {shlex.quote(room)} --name benchmark --hostname bench-b',timeout=120))
 ips=connected()
 info={}
 for h in HOSTS:
  data=json.loads(remote(h,'ip -j address show'))
  devs=[d['ifname'] for d in data if d['ifname'].startswith(('eth','ens'))]
  addresses=[a['local'] for d in data if d['ifname'] in devs for a in d['addr_info'] if a['scope']=='global']
  info[h]={'interfaces':devs,'addresses':addresses}
 (OUT/'hosts.json').write_text(json.dumps(info,indent=2))
 meta={'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'duration_s':10,'warmup_s':2,'repeats':3,'ping_interval_s':.01,'netem_queue_packets':1000}
 (OUT/'provenance.json').write_text(json.dumps(meta,indent=2))
 (OUT/'initial-status.json').write_text(json.dumps(parallel(status),indent=2))
 print('Both hosts joined; direct QUIC verified. Metadata saved locally.',flush=True)
