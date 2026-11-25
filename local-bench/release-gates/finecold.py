"""Measure the first transfer after a restart in 100 ms intervals."""
import argparse
import json
import time

import cloud_bench as c

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary")
parser.add_argument("--setting", choices=("on", "off"))
args = parser.parse_args()
b = c.bench
cloudout = c.OUT
c.HOSTS.update(c.load_hosts())
c.shape("uncapped")
t = b.configure(args.binary, "standalone", args.setting)
c.shape("bad")
for i in range(10):
    j=i%2;s,d=b.HOSTS[j],b.HOSTS[1-j];ip=t[1-j]
    c.remote(d,f'pkill -x iperf3 || true; iperf3 -1 -s -D -i .1 -J -B {ip} -p 5204')
    b.parallel(lambda n:c.remote(n,'systemctl restart raygate'));b.parallel(b.ready)
    started=time.monotonic()
    r=json.loads(c.remote(s,f'iperf3 -c {ip} -p 5204 -J -i .1 -t 6 --connect-timeout 10000 --get-server-output',timeout=40))
    wall=time.monotonic()-started
    vals=[a['sum'] for a in r['server_output_json']['intervals']]
    assert len(vals)>=55 and b.ray_conn(s)['conn_type']=='Direct'
    zero=longest=0
    for v in vals:
        zero=(zero+v['seconds']) if not v['bytes'] else 0
        longest=max(longest,zero)
    row={'run':i+1,'mbps':r['end']['sum_received']['bits_per_second']/1e6,'longest_empty_s':longest,'wall_s':wall,'receiver_intervals':vals,'tcp_connect_ms':r['start'].get('connected',[{}])[0].get('connect_time')}
    with (cloudout/'finecold.jsonl').open('a') as f: print(json.dumps(row),file=f)
    print(i+1,row['mbps'],longest,flush=True)
