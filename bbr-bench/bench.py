#!/usr/bin/env python3
"""Inner TCP congestion control (cubic vs bbr) through Rayfish, local containers.

Two privileged containers on one Docker bridge, CPU-pinned. Each container's
eth0 is shaped on egress: HTB 100 Mbit/s, netem 20 ms, random loss, limit 1000
packets, with TSO/GSO/GRO/tx checksum off so netem handles single packets.
Per cell: 10 s iperf3 with a 100 pps ping alongside, 3 repeats x 2 directions.

  bench.py [flavors]      flavors: direct,ray,ray-fq (default all)
"""
import json, subprocess, sys, time, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
RAY = os.environ['RAY_BINARY']
NET = 'bbr-bench'
NAMES = ['bbr-a', 'bbr-b']
CPUS = [os.environ.get('CPUS_A', '0,1'), os.environ.get('CPUS_B', '2,3')]
LOSSES = ['0', '0.1', '1', '2']
CCS = ['cubic', 'bbr']
REPS = 3
OUT = os.path.join(HERE, 'raw.jsonl')


def sh(cmd, check=True, timeout=120):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f'{cmd}\n{r.stdout}\n{r.stderr}')
    return r.stdout


def c(i, cmd, check=True, timeout=120, detach=False):
    flag = '-d ' if detach else ''
    return sh(f"docker exec {flag}{NAMES[i]} sh -c {json.dumps(cmd)}", check=check, timeout=timeout)


def down():
    sh(f"docker rm -f {' '.join(NAMES)}", check=False)
    sh(f'docker network rm {NET}', check=False)


def shape(loss):
    for i in (0, 1):
        c(i, 'tc qdisc del dev eth0 root 2>/dev/null; '
             'ethtool -K eth0 tso off gso off gro off tx off >/dev/null 2>&1; '
             'tc qdisc add dev eth0 root handle 1: htb default 10 && '
             'tc class add dev eth0 parent 1: classid 1:10 htb rate 100mbit ceil 100mbit && '
             f'tc qdisc add dev eth0 parent 1:10 handle 10: netem delay 20ms loss random {loss}% limit 1000')


def wait(pred, tries, pause=1.0):
    for _ in range(tries):
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(pause)
    return False


def start_daemon(i):
    c(i, 'RUST_LOG=info ray daemon >> /var/log/ray.log 2>&1', detach=True)
    assert wait(lambda: c(i, 'ray status >/dev/null 2>&1 && echo ok', check=False).strip() == 'ok', 60, 0.3)


def up(flavor):
    down()
    sh(f'docker network create {NET}')
    for i in (0, 1):
        sh(f'docker run -d --name {NAMES[i]} --hostname {NAMES[i]} --network {NET} --privileged '
           f'--device /dev/net/tun --cpuset-cpus {CPUS[i]} -v {RAY}:/usr/local/bin/ray:ro '
           f'raybench:local sleep infinity')
    shape('0')
    if flavor == 'direct':
        return [sh("docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' " + n).strip() for n in NAMES]
    for i in (0, 1):
        start_daemon(i)
    if flavor == 'ray-fq':
        for i in (0, 1):
            c(i, 'ray config set quic-engine fq-codel >/dev/null; pkill -x ray; sleep 1')
            start_daemon(i)
    out = c(0, 'ray create --open --name bench --hostname bbr-a 2>&1')
    m = re.search(r'ray join ([A-Za-z0-9]{20,})', out)
    assert m, out
    c(1, f'ray join {m.group(1)} --name bench --hostname bbr-b >/dev/null 2>&1', timeout=180)
    assert wait(lambda: 'bbr-b' in c(0, 'ray status'), 120)
    addrs = [json.loads(c(i, 'ray status --json'))['networks'][0]['my_ipv6'] for i in (0, 1)]
    assert wait(lambda: c(0, f'ping -c1 -W1 {addrs[1]} >/dev/null && echo ok', check=False).strip() == 'ok', 60)
    for i in (0, 1):
        c(i, 'ray firewall off >/dev/null')
    # Trials only count on a direct path, and the first transfer after a start
    # is thrown away.
    assert wait(lambda: conn_type() == 'Direct', 90), conn_type()
    trial(addrs, 0, 'cubic')
    trial(addrs, 1, 'cubic')
    return addrs


def conn_type():
    try:
        return json.loads(c(0, 'ray status --json'))['networks'][0]['peers'][0]['connection']['conn_type']
    except Exception as e:
        return f'unknown: {e}'[:60]


def ping_stats(text):
    times = sorted(float(x) for x in re.findall(r'time=([0-9.]+) ms', text))
    m = re.search(r'(\d+) packets transmitted, (\d+) received', text)
    sent, got = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    if not times:
        return {'ping_p50': None, 'ping_p99': None, 'ping_loss': 100.0}
    return {'ping_p50': times[len(times) // 2], 'ping_p99': times[min(len(times) - 1, int(len(times) * 0.99))],
            'ping_loss': round(100 * (1 - got / sent), 2) if sent else None}


def trial(addrs, src, cc):
    dst = 1 - src
    c(dst, 'pkill -x iperf3; true', check=False)
    c(dst, 'iperf3 -s -D -1')
    time.sleep(0.3)
    p = subprocess.Popen(f"docker exec {NAMES[src]} iperf3 -c {addrs[dst]} -t 10 -C {cc} -J",
                         shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    ping = c(src, f'ping -n -i 0.01 -c 800 -W 2 {addrs[dst]}', check=False, timeout=60)
    out, err = p.communicate(timeout=60)
    j = json.loads(out)
    if 'error' in j:
        raise RuntimeError(j['error'])
    end = j['end']
    r = {'mbps': round(end['sum_received']['bits_per_second'] / 1e6, 2),
         'retr': end['sum_sent'].get('retransmits'),
         'sender_cc': end.get('sender_tcp_congestion')}
    r.update(ping_stats(ping))
    return r


def main():
    flavors = sys.argv[1].split(',') if len(sys.argv) > 1 else ['direct', 'ray', 'ray-fq']
    for flavor in flavors:
        addrs = up(flavor)
        if flavor == 'direct':
            trial(addrs, 0, 'cubic')
        print(f'== {flavor} up {addrs}', flush=True)
        for rep in range(REPS):
            for loss in LOSSES:
                shape(loss)
                time.sleep(1)
                for cc in (CCS if rep % 2 == 0 else CCS[::-1]):
                    for src in (0, 1):
                        row = {'flavor': flavor, 'loss': loss, 'cc': cc, 'rep': rep, 'src': src, 'ts': time.time()}
                        try:
                            row.update(trial(addrs, src, cc))
                            if flavor != 'direct':
                                row['conn'] = conn_type()
                        except Exception as e:
                            row['error'] = str(e)[:300]
                        with open(OUT, 'a') as f:
                            f.write(json.dumps(row) + '\n')
                        print(json.dumps(row), flush=True)
            shape('0')
    down()


if __name__ == '__main__':
    try:
        main()
    finally:
        down()
