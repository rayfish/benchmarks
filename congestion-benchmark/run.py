#!/usr/bin/env python3
import concurrent.futures
import json
import math
import re
import shlex
import subprocess
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent
HOSTS = [line.split()[1] for line in (OUT / '.servers').read_text().splitlines()]
SSH = ['ssh', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=10', '-o', 'BatchMode=yes']


def remote(host, command, timeout=90):
    result = subprocess.run(SSH + [f'root@{host}', 'bash -s'], input=command, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'remote command exit {result.returncode}: {result.stderr[-2000:]} {result.stdout[-1000:]}')
    return result.stdout


def parallel(fn):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(fn, HOSTS))


def status(host):
    return json.loads(remote(host, 'ray status --json'))


def network(s):
    return s['networks'][0]


def peer_connection(host):
    return network(status(host))['peers'][0]['connection']


def wait_connected():
    for attempt in range(40):
        try:
            addresses = [network(status(h))['my_ipv6'] for h in HOSTS]
            remote(HOSTS[0], f'ping -n -c 2 -W 2 {shlex.quote(addresses[1])}')
            connections = [peer_connection(h) for h in HOSTS]
            if all(c and c['conn_type'].lower() == 'direct' for c in connections):
                return addresses
        except (RuntimeError, KeyError, TypeError):
            pass
        time.sleep(2)
    raise RuntimeError('Direct peer path did not converge')


def set_controller(controller):
    parallel(lambda h: remote(h, f'ray config set quic-congestion {controller} >/dev/null\nray restart >/dev/null', timeout=120))
    time.sleep(2)
    return wait_connected()


def shape(rate, delay, loss):
    def apply(host):
        other = HOSTS[1 - HOSTS.index(host)]
        addresses = INFO[other]['addresses']
        interfaces = INFO[host]['interfaces']
        lines = ['set -e']
        for dev in interfaces:
            lines += [f'tc qdisc del dev {dev} root 2>/dev/null || true']
            if rate:
                lines += [f'tc qdisc add dev {dev} root handle 1: htb default 20',
                          f'tc class add dev {dev} parent 1: classid 1:20 htb rate 10gbit',
                          f'tc class add dev {dev} parent 1: classid 1:10 htb rate {rate}mbit ceil {rate}mbit',
                          f'tc qdisc add dev {dev} parent 1:10 handle 10: netem delay {delay}ms loss random {loss}% limit 1000']
                for addr in addresses:
                    protocol, match = ('ipv6', 'ip6') if ':' in addr else ('ip', 'ip')
                    suffix = '/128' if ':' in addr else '/32'
                    lines += [f'tc filter add dev {dev} protocol {protocol} parent 1: prio {2 if protocol == 'ipv6' else 1} u32 match {match} dst {addr}{suffix} flowid 1:10']
        return remote(host, '\n'.join(lines))
    parallel(apply)


def ping_stats(output, skip=0):
    values = []
    sent = 0
    received = 0
    for line in output.splitlines():
        sample = re.search(r'icmp_seq=(\d+).*time[=<]([\d.]+)', line)
        if sample and int(sample[1]) > skip:
            values.append(float(sample[2]))
        totals = re.search(r'(\d+) packets transmitted, (\d+) received', line)
        if totals:
            sent, received = map(int, totals.groups())
    values.sort()
    def percentile(q):
        return values[max(0, math.ceil(len(values) * q) - 1)] if values else None
    return {'samples': len(values), 'mean_ms': sum(values) / len(values) if values else None,
            'p50_ms': percentile(.5), 'p95_ms': percentile(.95), 'p99_ms': percentile(.99),
            'max_ms': max(values) if values else None,
            'loss_pct': 100 * (sent - received) / sent if sent else None}


REMOTE_MEASURE = '''import json, subprocess, signal, time, tempfile
ping_file = tempfile.TemporaryFile(mode='w+t')
ping = subprocess.Popen(['ping','-n','-i','0.01',TARGET],stdout=ping_file,stderr=subprocess.PIPE,text=True)
start=time.monotonic()
iperf=subprocess.run(ARGS,capture_output=True,text=True,timeout=45)
elapsed=time.monotonic()-start
ping.send_signal(signal.SIGINT)
_,perr=ping.communicate(timeout=5)
ping_file.seek(0)
pout=ping_file.read()
print(json.dumps({'iperf': json.loads(iperf.stdout), 'ping': pout, 'elapsed': elapsed, 'exit': iperf.returncode, 'stderr': iperf.stderr}))
'''


def measure(source, destination, target, controller, profile, repeat, reverse=False, streams=1, udp=False, direct=False, udp_rate='150M', udp_payload=1200):
    remote(destination, f'pkill -x iperf3 2>/dev/null || true\niperf3 -s -D -B {shlex.quote(target)} --pidfile /run/congestion-iperf.pid -J\nsleep 0.3')
    idle = remote(source, f'ping -n -c 300 -i 0.01 -W 2 {shlex.quote(target)}')
    args = ['iperf3', '-c', target, '-J', '-t', '10', '-O', '2', '-P', str(streams)]
    if reverse:
        args.append('-R')
    if udp:
        args += ['-u', '-b', udp_rate, '-l', str(udp_payload), '--get-server-output']
    before = [] if direct else [peer_connection(h) for h in HOSTS]
    payload = 'TARGET = ' + repr(target) + '\nARGS = ' + repr(args) + '\n' + REMOTE_MEASURE
    result = json.loads(remote(source, "python3 - <<'PY'\n" + payload + '\nPY', timeout=60))
    iperf = result['iperf']
    if result['exit'] or 'error' in iperf:
        raise RuntimeError(f'iperf failed: {iperf.get("error", result["stderr"])}')
    receiver = iperf.get('server_output_json', iperf) if udp else iperf
    received = receiver['end'].get('sum_received', receiver['end'].get('sum'))
    if udp:
        if 'server_output_json' not in iperf:
            raise RuntimeError('UDP receiver summary is unavailable')
        intervals = [interval['sum'] for interval in receiver['intervals'] if not interval['sum'].get('omitted') and interval['sum']['start'] >= .99 and interval['sum']['end'] <= 10.01 and abs(interval['sum']['seconds'] - (interval['sum']['end'] - interval['sum']['start'])) < .05]
        seconds = sum(interval['seconds'] for interval in intervals)
        received = {**received, 'bits_per_second': sum(interval['bytes'] for interval in intervals) * 8 / seconds}
    row = {'profile': profile, 'controller': controller, 'repeat': repeat, 'reverse': reverse,
           'streams': streams, 'udp': udp, 'udp_rate': udp_rate if udp else None, 'udp_payload': udp_payload if udp else None, 'direct': direct, 'mbps': received['bits_per_second'] / 1e6,
           'idle': ping_stats(idle), 'loaded': ping_stats(result['ping'], skip=200),
           'iperf': iperf, 'connections_before': before,
           'connections_after': [] if direct else [peer_connection(h) for h in HOSTS]}
    with (OUT / 'raw.jsonl').open('a') as f:
        f.write(json.dumps(row) + '\n')
    print(json.dumps({key: row[key] for key in ('profile','controller','repeat','reverse','streams','udp','direct','mbps','idle','loaded')}), flush=True)
    remote(destination, 'pkill -x iperf3 2>/dev/null || true')
    return row


INFO = {}
for host in HOSTS:
    data = json.loads(remote(host, 'ip -j address show'))
    interfaces = [d['ifname'] for d in data if d['ifname'].startswith(('eth', 'ens'))]
    addresses = [a['local'] for d in data if d['ifname'] in interfaces for a in d['addr_info'] if a['scope'] == 'global']
    private = next(a['local'] for d in data if d['ifname'] == 'eth1' for a in d['addr_info'] if a['family'] == 'inet')
    INFO[host] = {'interfaces': interfaces, 'addresses': addresses, 'private': private}
(OUT / 'hosts.json').write_text(json.dumps(INFO, indent=2))

if __name__ == '__main__':
    profiles = [('clean', 0, 0, 0), ('100M-40ms-0loss', 100, 20, 0),
                ('100M-40ms-0.1loss', 100, 20, .1), ('100M-40ms-1loss', 100, 20, 1),
                ('100M-40ms-2loss', 100, 20, 2)]
    try:
        for name, rate, delay, loss in profiles:
            print(f'PROFILE {name}', flush=True)
            shape(rate, delay, loss)
            for repeat in range(1, 4):
                controllers = ['cubic','loss-tolerant'] if repeat % 2 else ['loss-tolerant','cubic']
                for controller in controllers:
                    vpn = set_controller(controller)
                    for reverse in [False, True]:
                        measure(HOSTS[0], HOSTS[1], vpn[1], controller, name, repeat, reverse=reverse)
            if loss == 0:
                measure(HOSTS[0], HOSTS[1], INFO[HOSTS[1]]['private'], 'direct', name, 1, direct=True)
                for controller in ['cubic','loss-tolerant']:
                    vpn = set_controller(controller)
                    measure(HOSTS[0], HOSTS[1], vpn[1], controller, name, 1, streams=4)
        shape(100, 20, 0)
        for controller in ['cubic','loss-tolerant']:
            vpn = set_controller(controller)
            measure(HOSTS[0], HOSTS[1], vpn[1], controller, '100M-40ms-UDP-overload', 1, udp=True)
    finally:
        shape(0, 0, 0)
        parallel(lambda h: remote(h, 'pkill -x iperf3 2>/dev/null || true'))
