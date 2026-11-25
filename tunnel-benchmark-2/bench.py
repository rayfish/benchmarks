"""Tunnel comparison on a shaped link: no tunnel, kernel WireGuard, Tailscale,
and both Rayfish forwarding engines. Resumable: finished trials are skipped."""
import concurrent.futures, json, math, re, shlex, subprocess, sys, time
from pathlib import Path

OUT = Path(__file__).resolve().parent
HOSTS = [l.split()[1] for l in (OUT / '.servers').read_text().splitlines() if l.strip()]
SSH = ['ssh', '-o', 'StrictHostKeyChecking=accept-new', '-o', f'UserKnownHostsFile={OUT / "known_hosts"}',
       '-o', 'ConnectTimeout=8', '-o', 'BatchMode=yes', '-o', 'LogLevel=ERROR',
       '-o', 'ControlMaster=auto', '-o', f'ControlPath={OUT}/.ssh-%h', '-o', 'ControlPersist=300']
ALL = {'TUN_OFFLOAD': 1, 'QUIC_GSO': 1, 'ACK_FREQ': 1}
# flavor -> (binary under /opt, benchmark switches, engine)
RAY = {
    'ray-shipped': ('shipped', {}, 'standalone'),
    'ray-lto': ('lto', {}, 'standalone'),
    'ray-tun': ('bench', {'TUN_OFFLOAD': 1}, 'standalone'),
    'ray-gso': ('bench', {'QUIC_GSO': 1}, 'standalone'),
    'ray-ack': ('bench', {'ACK_FREQ': 1}, 'standalone'),
    'ray-offloads': ('bench', {'TUN_OFFLOAD': 1, 'QUIC_GSO': 1}, 'standalone'),
    'ray-all': ('lto', ALL, 'standalone'),
    'ray-all-fq': ('lto', ALL, 'fq-codel'),
}
FLAVORS = ['direct', 'wireguard', 'tailscale', *RAY]
# Which flavors run on which shaped profile. The '-nic' profiles leave the
# physical NIC's offloads on (the usual case); the plain ones turn them off.
PROFILE_FLAVORS = {
    'shaped-0': {'tailscale', 'ray-shipped', 'ray-tun', 'ray-gso'},
    'shaped-1': {'tailscale', 'ray-shipped', 'ray-tun', 'ray-gso'},
    'shaped-0-nic': {'tailscale', 'ray-shipped', 'ray-all'},
    'shaped-1-nic': {'tailscale', 'ray-shipped', 'ray-all'},
}
SWITCHES = {}
PROFILES = [
    ('unshaped', dict(rate=0, delay=0, loss=0), ['tcp', 'tcp4']),
    ('shaped-0', dict(rate=100, delay=20, loss=0), ['tcp', 'mixed-kernel']),
    ('shaped-1', dict(rate=100, delay=20, loss=1), ['tcp']),
    ('shaped-0-nic', dict(rate=100, delay=20, loss=0, nic_offload=True), ['tcp', 'mixed-kernel']),
    ('shaped-1-nic', dict(rate=100, delay=20, loss=1, nic_offload=True), ['tcp']),
]
WG = ['192.0.2.1', '192.0.2.2']
STOP_ALL = 'systemctl stop rayfish 2>/dev/null; systemctl stop tailscaled 2>/dev/null; wg-quick down wg0 2>/dev/null; pkill -x iperf3 2>/dev/null; true'


def remote(host, command, timeout=120):
    r = subprocess.run(SSH + [f'root@{host}', 'bash -s'], input=command, text=True, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f'remote failed ({r.returncode}): {r.stderr[-1500:]} {r.stdout[-500:]}')
    return r.stdout


def parallel(fn):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as p:
        return list(p.map(fn, HOSTS))


def other(h):
    return HOSTS[1 - HOSTS.index(h)]


def info():
    path = OUT / 'hosts.json'
    if path.exists():
        return json.loads(path.read_text())
    data = {}
    for h in HOSTS:
        links = json.loads(remote(h, 'ip -j address show'))
        devs = [d['ifname'] for d in links if d['ifname'].startswith(('eth', 'ens'))]
        addrs = [a['local'] for d in links if d['ifname'] in devs for a in d['addr_info'] if a['scope'] == 'global']
        data[h] = {'interfaces': devs, 'addresses': addrs}
    path.write_text(json.dumps(data, indent=2))
    return data


INFO = info()


def shape(rate=100, delay=20, loss=0, nic_offload=False):
    """HTB + netem toward the peer only. On shaped runs, segmentation and
    checksum offload are off on the physical NIC so netem handles one packet
    per skb for every tunnel, not a batch of segments."""
    def apply(h):
        lines = ['set -e']
        for dev in INFO[h]['interfaces']:
            lines.append(f'tc qdisc del dev {dev} root 2>/dev/null || true')
            state = 'off' if rate and not nic_offload else 'on'
            lines.append(f'ethtool -K {dev} tso {state} gso {state} tx {state} >/dev/null 2>&1 || true')
            if not rate:
                continue
            lines += [f'tc qdisc add dev {dev} root handle 1: htb default 20',
                      f'tc class add dev {dev} parent 1: classid 1:20 htb rate 10gbit',
                      f'tc class add dev {dev} parent 1: classid 1:10 htb rate {rate}mbit ceil {rate}mbit',
                      f'tc qdisc add dev {dev} parent 1:10 handle 10: netem delay {delay}ms loss random {loss}% limit 1000']
            for addr in INFO[other(h)]['addresses']:
                proto, match, suffix = ('ipv6', 'ip6', '/128') if ':' in addr else ('ip', 'ip', '/32')
                lines.append(f'tc filter add dev {dev} protocol {proto} parent 1: prio {2 if proto == "ipv6" else 1} u32 match {match} dst {addr}{suffix} flowid 1:10')
        remote(h, '\n'.join(lines))
    parallel(apply)


def ray_status(h):
    return json.loads(remote(h, 'ray status --json'))


def ray_conn(h):
    return ray_status(h)['networks'][0]['peers'][0]['connection']


def ts_peer(h):
    peers = json.loads(remote(h, 'tailscale status --json')).get('Peer') or {}
    return next(iter(peers.values()), None)


def path_state(flavor):
    """Evidence that the tunnel path is direct, per host."""
    if flavor.startswith('ray'):
        return [{k: c.get(k) for k in ('conn_type', 'rtt_ms', 'lost_packets', 'quality')} for c in parallel(ray_conn)]
    if flavor == 'tailscale':
        return [{'direct': bool(p and p.get('CurAddr')), 'relay': p and p.get('Relay')} for p in parallel(ts_peer)]
    return None


def is_direct(flavor, state):
    if flavor.startswith('ray'):
        return all(s and str(s['conn_type']).lower() == 'direct' for s in state)
    if flavor == 'tailscale':
        return all(s['direct'] for s in state)
    return True


def activate(flavor):
    """Stop every tunnel, start only this one fresh, return [addr_of_host0, addr_of_host1]."""
    parallel(lambda h: remote(h, STOP_ALL))
    if flavor == 'direct':
        return list(HOSTS)
    if flavor == 'wireguard':
        parallel(lambda h: remote(h, 'wg-quick up wg0 >/dev/null 2>&1'))
        targets = list(WG)
    elif flavor == 'tailscale':
        login = f'https://{HOSTS[0]}'
        parallel(lambda h: remote(h, f'systemctl start tailscaled\nfor i in $(seq 1 20); do tailscale up --login-server {login} --accept-dns=false --hostname bench-{"ab"[HOSTS.index(h)]} --timeout 30s >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1', timeout=300))
        targets = [remote(h, 'tailscale ip -4').strip() for h in HOSTS]
    else:
        binary, switches, engine = RAY[flavor]
        env = ''.join(f'Environment=RAYFISH_BENCH_{k}={v}\\n' for k, v in switches.items())
        def start(h):
            remote(h, f'set -e\ninstall -m 755 /opt/ray-{binary} /usr/local/bin/ray\nprintf "[Service]\\n{env}" > /etc/systemd/system/rayfish.service.d/bench.conf\nsystemctl daemon-reload\nsystemctl start rayfish\nfor i in $(seq 1 30); do ray status --json >/dev/null 2>&1 && break; sleep 0.5; done\nray config set quic-engine {engine} >/dev/null\nray up >/dev/null 2>&1 || true\nray restart >/dev/null\nfor i in $(seq 1 30); do ray status --json >/dev/null 2>&1 && break; sleep 0.5; done')
            got = remote(h, 'ray config get quic-engine')
            if engine not in got:
                raise RuntimeError(f'engine not applied: {got}')
        parallel(start)
        targets = None
    for _ in range(60):
        try:
            if flavor.startswith('ray'):
                targets = [ray_status(h)['networks'][0]['my_ipv6'] for h in HOSTS]
            if flavor == 'tailscale':
                remote(HOSTS[0], f'tailscale ping --until-direct -c 5 {shlex.quote(targets[1])} >/dev/null 2>&1 || true')
            remote(HOSTS[0], f'ping -n -c 2 -W 2 {shlex.quote(targets[1])} >/dev/null')
            remote(HOSTS[1], f'ping -n -c 2 -W 2 {shlex.quote(targets[0])} >/dev/null')
            if is_direct(flavor, path_state(flavor)):
                if flavor in RAY:
                    SWITCHES[flavor] = parallel(lambda h: remote(h, "curl -s --max-time 3 http://127.0.0.1:9090/metrics | grep -E '^rayfish_(tun_tcp_offload|tun_udp_offload|mesh_quic_segmentation_offload)_enabled' | tr '\\n' ' '; tr '\\0' ' ' < /proc/$(pgrep -f 'ray daemon' | head -1)/environ | grep -o 'RAYFISH_BENCH_[A-Z_]*=1' | tr '\\n' ' '").strip())
                return targets
        except (RuntimeError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired):
            pass
        time.sleep(2)
    raise RuntimeError(f'{flavor}: direct path did not converge')


SNAP = r"""cat /proc/uptime; head -1 /proc/stat
for p in $(pgrep -x tailscaled; pgrep -f 'ray daemon'); do cat /proc/$p/stat 2>/dev/null; done
echo QDISC; tc -s -j qdisc show"""


def snapshot(h):
    head, qdisc = remote(h, SNAP).split('QDISC\n', 1)
    lines = head.strip().splitlines()
    cpu = [int(x) for x in lines[1].split()[1:9]]
    daemon = 0
    for line in lines[2:]:
        fields = line.rsplit(')', 1)[1].split()
        daemon += int(fields[11]) + int(fields[12])
    netem = [q for q in json.loads(qdisc) if q.get('kind') == 'netem']
    return {'uptime': float(lines[0].split()[0]), 'busy': sum(cpu) - cpu[3] - cpu[4], 'user': cpu[0] + cpu[1],
            'system': cpu[2], 'softirq': cpu[5] + cpu[6], 'steal': cpu[7], 'daemon': daemon,
            'netem_bytes': sum(q['bytes'] for q in netem), 'netem_packets': sum(q['packets'] for q in netem),
            'netem_drops': sum(q['drops'] for q in netem)}


def usage(before, after):
    hz = 100
    d = {k: after[k] - before[k] for k in before}
    return {'elapsed_s': d['uptime'], 'busy_s': d['busy'] / hz, 'user_s': d['user'] / hz, 'system_s': d['system'] / hz,
            'softirq_s': d['softirq'] / hz, 'steal_s': d['steal'] / hz, 'daemon_s': d['daemon'] / hz,
            'netem_bytes': d['netem_bytes'], 'netem_packets': d['netem_packets'], 'netem_drops': d['netem_drops']}


def ping_stats(output, skip=200):
    values = []; sent = got = 0
    for line in output.splitlines():
        m = re.search(r'icmp_seq=(\d+).*time[=<]([\d.]+)', line)
        if m and int(m[1]) > skip:
            values.append(float(m[2]))
        m = re.search(r'(\d+) packets transmitted, (\d+) received', line)
        if m:
            sent, got = map(int, m.groups())
    values.sort()
    def p(q):
        return values[max(0, math.ceil(len(values) * q) - 1)] if values else None
    return {'samples': len(values), 'mean_ms': sum(values) / len(values) if values else None, 'p50_ms': p(.5),
            'p95_ms': p(.95), 'p99_ms': p(.99), 'max_ms': max(values) if values else None,
            'loss_pct': 100 * (sent - got) / sent if sent else None}


MEASURE = """import json, subprocess, signal, tempfile, time
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


def received(iperf, udp=False):
    if not udp:
        end = iperf['end']
        streams = [s['receiver']['bits_per_second'] / 1e6 for s in end.get('streams', []) if 'receiver' in s]
        jain = sum(streams) ** 2 / (len(streams) * sum(x * x for x in streams)) if streams and sum(streams) > 0 else None
        return {'mbps': end['sum_received']['bits_per_second'] / 1e6, 'retransmits': end['sum_sent'].get('retransmits'),
                'stream_mbps': streams, 'jain': jain}
    server = iperf['server_output_json']
    rows = [x['sum'] for x in server['intervals'] if not x['sum'].get('omitted')]
    rows = rows[1:-1] if len(rows) > 4 else rows
    rate = sum(x['bytes'] for x in rows) * 8 / sum(x['seconds'] for x in rows) / 1e6
    total = server['end'].get('sum_received', server['end'].get('sum'))
    return {'mbps': rate, 'udp_loss_pct': total.get('lost_percent'), 'intervals': len(rows)}


def measure(src, dst, target, flavor, profile, loss, repeat, workload, direction, idle, duration=10):
    ports = [5201, 5202] if workload.startswith('mixed') else [5201]
    lines = ['set -e', 'pkill -x iperf3 2>/dev/null || true', 'sleep 0.2']
    for port in ports:
        lines.append(f'iperf3 -s -D -1 -B {shlex.quote(target)} -p {port} --pidfile /run/iperf-{port}.pid -J')
    if workload == 'mixed-kernel':
        lines.append('rm -f /tmp/small-window.json /tmp/small-window.err\nnohup python3 /tmp/tcp_window.py >/tmp/small-window.json 2>/tmp/small-window.err </dev/null &')
    lines.append('sleep 0.3')
    remote(dst, '\n'.join(lines))
    bulk = ['iperf3', '-c', target, '-p', '5201', '-J', '-t', str(duration), '-O', '2', '--get-server-output']
    if workload.startswith('tcp4'):
        bulk += ['-P', '4']
    if workload.startswith('mixed'):
        bulk += ['-u', '-b', '150M', '-l', '1000']
    small = ['iperf3', '-c', target, '-p', '5202', '-J', '-t', '20', '-b', '1M', '-l', '1024', '--get-server-output'] if workload.startswith('mixed') else []
    path_before = path_state(flavor)
    script = f'TARGET={target!r}\nBULK={bulk!r}\nSMALL={small!r}\nDURATION={duration!r}\n' + MEASURE
    snap0 = parallel(snapshot)
    result = json.loads(remote(src, "python3 - <<'PY'\n" + script + '\nPY', timeout=duration + 70))
    snap1 = parallel(snapshot)
    if result['bulk_exit'] or 'error' in result['bulk']:
        raise RuntimeError(f"bulk iperf failed: {str(result['bulk'].get('error'))[:300]} {result['bulk_stderr'][:300]}")
    if result['small'] and (result['small_exit'] or 'error' in result['small']):
        raise RuntimeError(f"small iperf failed: {str(result['small'].get('error'))[:300]}")
    small_window = json.loads(remote(dst, 'cat /tmp/small-window.json')) if workload == 'mixed-kernel' else None
    path_after = path_state(flavor)
    cpu = {('src' if h == src else 'dst'): usage(a, b) for h, a, b in zip(HOSTS, snap0, snap1)}
    row = {'flavor': flavor, 'profile': profile, 'loss_pct': loss, 'repeat': repeat, 'workload': workload,
           'direction': direction, 'duration_s': duration, 'bulk': received(result['bulk'], udp=workload.startswith('mixed')),
           'small': small_window, 'idle': idle, 'loaded': ping_stats(result['ping']), 'cpu': cpu,
           'path_before': path_before, 'path_after': path_after, 'direct': is_direct(flavor, path_after),
           'switches': SWITCHES.get(flavor)}
    with (OUT / 'raw-full.jsonl').open('a') as f:
        f.write(json.dumps({**{k: row[k] for k in ('flavor', 'profile', 'repeat', 'workload', 'direction')}, 'raw': result}) + '\n')
    if not row['direct']:
        raise RuntimeError('path stopped being direct')
    with (OUT / 'raw.jsonl').open('a') as f:
        f.write(json.dumps(row) + '\n')
    brief = {'flavor': flavor, 'profile': profile, 'rep': repeat, 'wl': workload, 'dir': direction,
             'mbps': round(row['bulk']['mbps'], 1), 'small': small_window and round(small_window['mbps'], 3),
             'p99': row['loaded']['p99_ms'], 'ping_loss': row['loaded']['loss_pct'],
             'busy_s': round(cpu['src']['busy_s'] + cpu['dst']['busy_s'], 2)}
    print(json.dumps(brief), flush=True)
    remote(dst, 'pkill -x iperf3 2>/dev/null || true')
    return row


def fail(flavor, profile, repeat, workload, direction, attempt, e):
    with (OUT / 'errors.jsonl').open('a') as f:
        f.write(json.dumps({'flavor': flavor, 'profile': profile, 'repeat': repeat, 'workload': workload, 'direction': direction, 'attempt': attempt, 'error': str(e)[:600]}) + '\n')
    print(f'ERROR {flavor} {profile} {workload} {direction} attempt {attempt}: {str(e)[:200]}', flush=True)


def key(r):
    return (r['profile'], r['repeat'], r['flavor'], r['workload'], r['direction'])


def main(repeats, flavors, profiles):
    done = set()
    if (OUT / 'raw.jsonl').exists():
        done = {key(json.loads(l)) for l in (OUT / 'raw.jsonl').read_text().splitlines()}
    parallel(lambda h: subprocess.run(['scp', *SSH[1:], str(OUT / 'tcp_window.py'), f'root@{h}:/tmp/tcp_window.py'], check=True, capture_output=True))
    try:
        for repeat in repeats:
            shift = (repeat - 1) % len(flavors)
            for flavor in flavors[shift:] + flavors[:shift]:
                for profile, params, workloads in profiles:
                    if profile != 'unshaped' and flavor not in PROFILE_FLAVORS[profile]:
                        continue
                    todo = [(w, i, d) for w in workloads for i, d in [(0, 'A-to-B'), (1, 'B-to-A')]
                            if (profile, repeat, flavor, w, d) not in done]
                    if not todo:
                        continue
                    shape(**params)
                    try:
                        targets = activate(flavor)
                    except Exception as e:  # noqa: BLE001 - a tunnel that will not come up is a result
                        fail(flavor, profile, repeat, 'activation', '-', 0, e)
                        continue
                    for workload, i, direction in todo:
                        for attempt in (1, 2):
                            try:
                                idle = ping_stats(remote(HOSTS[i], f'ping -n -c 200 -i 0.01 -W 2 {shlex.quote(targets[1 - i])}'), skip=0)
                                measure(HOSTS[i], HOSTS[1 - i], targets[1 - i], flavor, profile, params['loss'], repeat, workload, direction, idle)
                                break
                            except Exception as e:  # noqa: BLE001 - keep the run going, record the failure
                                fail(flavor, profile, repeat, workload, direction, attempt, e)
                                if attempt == 1:
                                    try:
                                        targets = activate(flavor)
                                    except Exception as e2:  # noqa: BLE001
                                        fail(flavor, profile, repeat, 'activation', direction, attempt, e2)
                                        break
    finally:
        shape(rate=0)
        parallel(lambda h: remote(h, STOP_ALL))
    print('RUN COMPLETE', flush=True)


if __name__ == '__main__':
    reps = [int(x) for x in sys.argv[1].split(',')] if len(sys.argv) > 1 else [1, 2, 3]
    fl = sys.argv[2].split(',') if len(sys.argv) > 2 else FLAVORS
    pr = [p for p in PROFILES if len(sys.argv) <= 3 or p[0] in sys.argv[3].split(',')]
    main(reps, fl, pr)
