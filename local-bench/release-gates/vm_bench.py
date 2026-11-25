"""Repeat the tunnel benchmark on two local KVM guests."""
import argparse
import ast
import concurrent.futures
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
from statistics import median
import time

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("RAY_GATE_VM_OUT", HERE / "vm-runs")).resolve()
HOSTS = ["a", "b"]
VM = ["bash", str(HERE / "vms.sh"), "ssh"]

def remote(host, command, timeout=120):
    r = subprocess.run([*VM, host, "sudo bash -s"], input=command, text=True,
                       capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"guest command exited {r.returncode}: {r.stderr[-1200:]} {r.stdout[-400:]}")
    return r.stdout

# Use the same iperf durations, ping sampler and CPU math as the cloud runs.
original = HERE.parent.parent / "tunnel-benchmark-2" / "bench.py"
tree = ast.parse(original.read_text())
wanted = {"parallel", "other", "ray_status", "ray_conn", "path_state", "is_direct",
          "snapshot", "usage", "ping_stats", "received", "measure", "SNAP", "MEASURE"}
nodes = [
    n for n in tree.body
    if (isinstance(n, ast.FunctionDef) and n.name in wanted)
    or (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id in wanted)
]
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(original), "exec"))
SWITCHES = {}

def idle_ping(src, target):
    output = remote(src, f"ping -n -c 200 -i 0.01 -W 2 {shlex.quote(target)}")
    return ping_stats(output, skip=0)


def upload(node, local, target):
    with local.open("rb") as source:
        subprocess.run([*VM, node, "sudo tee " + shlex.quote(target)],
                       stdin=source, stdout=subprocess.DEVNULL, check=True, timeout=60)

def setup(node, binary):
    remote(node, """
set -e
iptables -C OUTPUT -o mgmt0 -p udp ! --dport 53 -j DROP 2>/dev/null ||
    iptables -I OUTPUT -o mgmt0 -p udp ! --dport 53 -j DROP
mkdir -p /opt/raygate
ln -sfn /opt/raygate/ray /usr/local/bin/ray
cat >/etc/systemd/system/raygate.service <<'UNIT'
[Unit]
Description=Local Rayfish release benchmark
[Service]
ExecStart=/opt/raygate/ray daemon
Environment=RUST_LOG=info
TimeoutStopSec=10
UNIT
systemctl daemon-reload
""")
    upload(node, HERE / binary, "/opt/raygate/ray.next")
    upload(node, HERE.parent / "tcp_window.py", "/tmp/tcp_window.py")
    remote(node, "set -e; chmod +x /opt/raygate/ray.next; mv /opt/raygate/ray.next /opt/raygate/ray; systemctl restart raygate")

def ready(node):
    remote(node, """
for _ in $(seq 200); do
    if ray status --json >/dev/null 2>&1; then exit 0; fi
    sleep .05
done
exit 1
""")

def direct():
    peer = ray_status("b")["networks"][0]["my_ipv6"]
    for attempt in range(200):
        if attempt % 8 == 0:
            remote("a", f"ping -q -c1 -w1 {peer} >/dev/null || true")
        states = parallel(lambda h: (ray_conn(h) or {}))
        if all(s.get("conn_type") == "Direct" and s.get("remote_addr", "").startswith("ip:198.51.100.")
               for s in states):
            return
        time.sleep(.25)
    raise RuntimeError(f"the local link did not become direct: {[s.get('conn_type') for s in states]}")

def configure(binary, engine, setting):
    parallel(lambda h: setup(h, binary))
    parallel(ready)
    networks = ray_status("a")["networks"]
    if not networks:
        result = remote("a", "ray create --open --name bench --hostname gate-a")
        room = re.search(r"ray join ([A-Za-z0-9]{20,})", result)[1]
        remote("b", f"ray join {room} --name bench --hostname gate-b")
    def configure_node(node):
        remote(node, "ray firewall off")
        old_engine = remote(node, "ray config get quic-engine").strip()
        if old_engine != engine or setting is not None:
            remote(node, f"ray config set quic-engine {engine}")
            if setting is not None:
                remote(node, f"ray config set mesh-offload {setting}")
            remote(node, "systemctl restart raygate")
        ready(node)
    parallel(configure_node)
    targets = parallel(lambda h: ray_status(h)["networks"][0]["my_ipv6"])
    remote("a", f"ping -q -c1 -w15 {targets[1]} >/dev/null")
    direct()
    return targets

def shape(profile):
    def apply(node):
        offload = "off" if profile in ("bad", "loss") else "on"
        cmd = f"set -e\ntc qdisc del dev link0 root 2>/dev/null || true\nethtool -K link0 tso {offload} gso {offload} tx {offload} >/dev/null\n"
        if profile != "uncapped":
            cmd += """tc qdisc add dev link0 root handle 1: htb default 10
tc class add dev link0 parent 1: classid 1:10 htb rate 100mbit ceil 100mbit
"""
            if profile == "flood":
                cmd += "tc qdisc add dev link0 parent 1:10 handle 10: bfifo limit 1100000\n"
            else:
                loss = 1 if profile == "loss" else 0
                cmd += f"tc qdisc add dev link0 parent 1:10 handle 10: netem delay 20ms loss random {loss}% limit 1000\n"
        settings = remote(node, cmd + "ethtool -k link0 | grep -E '^(tx-checksumming|tcp-segmentation-offload|generic-segmentation-offload):'")
        if not all(line.endswith(offload) for line in settings.splitlines()):
            raise RuntimeError(f"failed to set NIC offloads to {offload}: {settings}")
    parallel(apply)
    when_flood = "add" if profile == "flood" else "del"
    for h in HOSTS:
        command = ["sudo", "-n", "tc", "qdisc", when_flood, "dev", f"rayg-tap-{h}", "root"]
        subprocess.run(command + (["netem", "delay", "20ms", "limit", "65536"] if when_flood == "add" else []),
                       check=when_flood == "add", stderr=None if when_flood == "add" else subprocess.DEVNULL)

def summarize(flavor, profile, rows):
    def cpu(row):
        return sum(x["busy_s"] / x["elapsed_s"] for x in row["cpu"].values())
    results = {
        "name": flavor, "profile": profile, "n": len(rows),
        "mbps": median(r["bulk"]["mbps"] for r in rows),
        "p99_ms": median(r["loaded"]["p99_ms"] for r in rows if r["loaded"]["p99_ms"] is not None),
        "cores": median(cpu(r) for r in rows),
        "cores_per_gbit": median(cpu(r) * 1000 / r["bulk"]["mbps"] for r in rows),
    }
    if profile == "flood":
        results["small_mbps"] = median(r["small"]["mbps"] for r in rows)
    (OUT / f"{flavor}-{profile}.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results), flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("flavor")
    parser.add_argument("binary")
    parser.add_argument("engine", choices=["standalone", "fq-codel"])
    parser.add_argument("profiles", nargs="+", choices=["uncapped", "bad", "loss", "nic", "flood"])
    parser.add_argument("--setting", choices=["on", "off"])
    parser.add_argument("--runs", type=int, default=6)
    parser.add_argument("--restart-every-pair", action="store_true")
    parser.add_argument("--first-side", choices=("a", "b"), default="a")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    assert args.flavor.startswith("ray-")
    shape("uncapped")
    targets = configure(args.binary, args.engine, args.setting)
    for profile in args.profiles:
        shape(profile)
        parallel(lambda h: remote(h, "systemctl restart raygate"))
        parallel(ready)
        remote("a", f"ping -q -c1 -w15 {targets[1]} >/dev/null")
        direct()
        rows = []
        for i in range(args.runs):
            src_idx = (i + (args.first_side == "b")) % 2
            if args.restart_every_pair and i and i % 2 == 0:
                parallel(lambda h: remote(h, "systemctl restart raygate"))
                parallel(ready)
            direct()
            idle = idle_ping(HOSTS[src_idx], targets[1 - src_idx])
            row = measure(HOSTS[src_idx], HOSTS[1 - src_idx], targets[1 - src_idx],
                          args.flavor, profile, int(profile == "loss"), i + 1,
                          "mixed-kernel" if profile == "flood" else "tcp",
                          f"{HOSTS[src_idx]}-{HOSTS[1 - src_idx]}", idle)
            direct()
            rows.append(row)
        summarize(args.flavor, profile, rows)
        if profile in ("bad", "loss"):
            failures = parallel(lambda h: remote(h, "journalctl -u raygate --since '3 minutes ago' --no-pager | grep -Ec 'halting segmentation offload' || true").strip())
            print("kernel refusals", failures, flush=True)

if __name__ == "__main__":
    main()
