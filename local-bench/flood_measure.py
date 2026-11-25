# Runs inside rb-a. Round 2's mixed-kernel workload: UDP 150M bulk, 1M TCP small flow, 100 pps ping.
import json, subprocess, signal, sys, tempfile, time
TARGET = sys.argv[1]; DURATION = 10
BULK = ['iperf3', '-c', TARGET, '-p', '5201', '-J', '-t', str(DURATION), '-O', '2', '--get-server-output', '-u', '-b', '150M', '-l', '1000']
SMALL = ['iperf3', '-c', TARGET, '-p', '5202', '-J', '-t', '20', '-b', '1M', '-l', '1024']
pf = tempfile.TemporaryFile(mode='w+t')
small = subprocess.Popen(SMALL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
time.sleep(2)
ping = subprocess.Popen(['ping', '-n', '-i', '0.01', '-c', '600', '-W', '2', TARGET], stdout=pf, stderr=subprocess.PIPE, text=True)
bulk = subprocess.Popen(BULK, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    ping.communicate(timeout=22)
    bout, _ = bulk.communicate(timeout=DURATION + 30)
    small.communicate(timeout=30)
finally:
    for p in (ping, bulk, small):
        if p.poll() is None: p.kill(); p.wait()
pf.seek(0); out = pf.read()
import re
rtts = sorted(float(x) for x in re.findall(r'time=([0-9.]+) ms', out))
loss = re.search(r'([0-9.]+)% packet loss', out)
def pct(q): return rtts[min(len(rtts) - 1, int(round(q * len(rtts) + 0.5)) - 1)] if rtts else None
srv = json.loads(bout)['server_output_json']
rows = [x['sum'] for x in srv['intervals'] if not x['sum'].get('omitted')]
rows = rows[1:-1] if len(rows) > 4 else rows
bulk_mbps = sum(x['bytes'] for x in rows) * 8 / sum(x['seconds'] for x in rows) / 1e6
print(json.dumps({'bulk_mbps': round(bulk_mbps, 1), 'p50': pct(.5), 'p99': pct(.99), 'ping_loss': float(loss.group(1)) if loss else None}))
