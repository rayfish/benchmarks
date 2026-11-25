"""Run the release gates on the two explicitly provisioned benchmark guests."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

import vm_bench as bench

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("RAY_GATE_CLOUD_OUT", HERE / "cloud")).resolve()
SOCKETS = Path(os.environ.get("RAY_GATE_CLOUD_SOCKETS", OUT)).resolve()
bench.OUT = OUT
def load_hosts():
    return {
        name.rsplit("-", 1)[1]: host
        for _, host, name in (
            line.split() for line in (OUT / ".servers").read_text().splitlines()
        )
    }

HOSTS = {}
SSH = [
    "ssh", "-o", "StrictHostKeyChecking=accept-new",
    "-o", f"UserKnownHostsFile={OUT / 'known_hosts'}",
    "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-o", "LogLevel=ERROR",
    "-o", "ControlMaster=auto", "-o", f"ControlPath={SOCKETS / '.ssh-%h'}",
    "-o", "ControlPersist=300",
]

def remote(node, command, timeout=120):
    result = subprocess.run([*SSH, f"root@{HOSTS[node]}", "bash -s"],
                            input=command, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"guest command failed ({result.returncode}): {result.stderr[-1000:]} {result.stdout[-500:]}")
    return result.stdout

bench.remote = remote

def upload(node, filename, target):
    with filename.open("rb") as source:
        subprocess.run([*SSH, f"root@{HOSTS[node]}", "tee " + shlex.quote(target)],
                       stdin=source, stdout=subprocess.DEVNULL, check=True, timeout=120)

def setup(node, binary):
    reference_environment = """
Environment=RAYFISH_BENCH_TUN_OFFLOAD=1
Environment=RAYFISH_BENCH_QUIC_GSO=1
Environment=RAYFISH_BENCH_ACK_FREQ=1
""" if binary == "ray-reference-all" else ""
    remote(node, """
set -e
mkdir -p /opt/raygate
ln -sfn /opt/raygate/ray /usr/local/bin/ray
cat >/etc/systemd/system/raygate.service <<'UNIT'
[Unit]
Description=Rayfish release benchmark
[Service]
ExecStart=/opt/raygate/ray daemon
Environment=RUST_LOG=info
""" + reference_environment + """
TimeoutStopSec=10
UNIT
systemctl daemon-reload
""")
    upload(node, HERE / binary, "/opt/raygate/ray.next")
    upload(node, HERE.parent / "tcp_window.py", "/tmp/tcp_window.py")
    remote(node, """
set -e
chmod 755 /opt/raygate/ray.next
mv /opt/raygate/ray.next /opt/raygate/ray
/opt/raygate/ray --version
systemctl restart raygate
""")

bench.setup = setup

def interfaces(node):
    data = json.loads(remote(node, "ip -j address show"))
    devices = [d["ifname"] for d in data if d["ifname"].startswith(("eth", "ens"))]
    addrs = {
        str(ipaddress.ip_address(a["local"]))
        for d in data if d["ifname"] in devices
        for a in d["addr_info"] if a["scope"] == "global"
    }
    if not devices or not addrs:
        raise RuntimeError("could not identify the benchmark guest interfaces")
    return devices, addrs

def direct():
    target = bench.ray_status("b")["networks"][0]["my_ipv6"]
    for i in range(50):
        if i % 5 == 0:
            remote("a", f"ping -q -c1 -w1 {target} >/dev/null || true")
        paths = bench.path_state("ray-cloud")
        if bench.is_direct("ray-cloud", paths):
            return
        time.sleep(0.5)
    raise RuntimeError("the two guests did not establish a direct mesh connection")

bench.direct = direct

def shape(profile):
    info = dict(zip(bench.HOSTS, bench.parallel(interfaces)))
    def apply(node):
        devices, _ = info[node]
        _, peers = info[bench.other(node)]
        offloads = "off" if profile in ("bad", "loss") else "on"
        commands = ["set -e"]
        if profile == "flood":
            commands += [
                "modprobe ifb",
                "ip link del ifb-raygate 2>/dev/null || true",
                "ip link add ifb-raygate type ifb",
                "ip link set ifb-raygate up",
                "tc qdisc add dev ifb-raygate root netem delay 20ms limit 65536",
            ]
        for device in devices:
            commands += [
                f"tc qdisc del dev {device} root 2>/dev/null || true",
                f"tc qdisc del dev {device} clsact 2>/dev/null || true",
                f"ethtool -K {device} tso {offloads} gso {offloads} tx {offloads} >/dev/null",
                f"ethtool -k {device} | grep -E '^(tx-checksumming|tcp-segmentation-offload|generic-segmentation-offload):'",
            ]
            if profile == "uncapped":
                continue
            commands += [
                f"tc qdisc add dev {device} root handle 1: htb default 20",
                f"tc class add dev {device} parent 1: classid 1:20 htb rate 10gbit",
                f"tc class add dev {device} parent 1: classid 1:10 htb rate 100mbit ceil 100mbit",
            ]
            if profile == "flood":
                commands += [
                    f"tc qdisc add dev {device} parent 1:10 handle 10: bfifo limit 1100000",
                    f"tc qdisc add dev {device} clsact",
                ]
            else:
                loss = 1 if profile == "loss" else 0
                commands.append(
                    f"tc qdisc add dev {device} parent 1:10 handle 10: netem delay 20ms loss random {loss}% limit 1000"
                )
            for i, addr in enumerate(sorted(peers), start=1):
                is_v6 = ":" in addr
                proto, match, width = ("ipv6", "ip6", 128) if is_v6 else ("ip", "ip", 32)
                dest = f"{addr}/{width}"
                commands.append(
                    f"tc filter add dev {device} protocol {proto} parent 1: prio {i} "
                    f"u32 match {match} dst {dest} flowid 1:10"
                )
                if profile == "flood":
                    commands.append(
                        f"tc filter add dev {device} ingress protocol {proto} prio {i} "
                        f"flower src_ip {dest} action mirred egress redirect dev ifb-raygate"
                    )
        result = remote(node, "\n".join(commands))
        for line in result.splitlines():
            if not line.endswith(offloads):
                raise RuntimeError(f"expected {offloads} from NIC checks: {line}")
        return result
    bench.parallel(apply)

def cold(name, targets, count):
    shape("bad")
    rows = []
    for i in range(count):
        src_idx = i % 2
        src, dst = bench.HOSTS[src_idx], bench.HOSTS[1 - src_idx]
        target = targets[1 - src_idx]
        remote(dst, f"pkill -x iperf3 2>/dev/null || true; iperf3 -s -D -1 -J -B {shlex.quote(target)}")
        bench.parallel(lambda node: remote(node, "systemctl restart raygate"))
        bench.parallel(bench.ready)
        begin = time.monotonic()
        try:
            result = json.loads(remote(
                src, f"iperf3 -c {shlex.quote(target)} -J -t 6 -i .1 "
                "--connect-timeout 10000 --get-server-output", timeout=32,
            ))
            elapsed = time.monotonic() - begin
            intervals = result["server_output_json"]["intervals"]
            current = longest = 0.0
            for interval in intervals:
                block = interval["sum"]
                current = 0.0 if block["bytes"] else current + block["seconds"]
                longest = max(longest, current)
            row = {
                "run": i + 1, "elapsed_s": elapsed, "longest_zero_receive_s": longest,
                "mbps": result["end"]["sum_received"]["bits_per_second"] / 1e6,
                "iperf": result,
            }
        except (KeyError, RuntimeError, subprocess.TimeoutExpired) as error:
            row = {"run": i + 1, "elapsed_s": time.monotonic() - begin, "error": str(error)}
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "iperf"}), flush=True)
        (OUT / f"{name}-cold.json").write_text(json.dumps(rows, indent=2) + "\n")
    return rows

def stream(name, targets, profile):
    shape(profile)
    code = (HERE.parent / "stream.py")
    bench.parallel(lambda node: upload(node, code, "/opt/raygate/stream.py"))
    bench.parallel(lambda node: remote(node, "systemctl restart raygate"))
    bench.parallel(bench.ready)
    direct()
    rows = []
    for i in range(4):
        a, b = bench.HOSTS[i % 2], bench.HOSTS[1 - (i % 2)]
        peer = targets[1 - (i % 2)]
        server = subprocess.Popen(
            [*SSH, f"root@{HOSTS[b]}", f"python3 /opt/raygate/stream.py server {shlex.quote(peer)}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        try:
            remote(b, 'timeout 5s bash -c \'until ss -ltnH "sport = :38923" | grep -q .; do sleep .02; done\'')
            result = json.loads(remote(a, f"python3 /opt/raygate/stream.py client {peer}", timeout=120))
            if server.wait(timeout=5):
                raise RuntimeError(server.stderr.read())
            rows.append(result)
            print(json.dumps(result), flush=True)
            (OUT / f"{name}-stream-{profile}.json").write_text(json.dumps(rows, indent=2) + "\n")
        finally:
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=5)

def main():
    args = argparse.ArgumentParser()
    args.add_argument("flavor")
    args.add_argument("binary")
    args.add_argument("engine", choices=("standalone", "fq-codel"))
    args.add_argument("profiles", nargs="+",
                      choices=("uncapped", "bad", "loss", "nic", "flood", "cold", "stream", "stream-bad"))
    args.add_argument("--runs", type=int, default=6)
    args.add_argument("--setting", choices=("on", "off"))
    args.add_argument("--first-side", choices=("a", "b"), default="a")
    options = args.parse_args()
    HOSTS.update(load_hosts())
    assert options.flavor.startswith("ray-")
    shape("uncapped")
    targets = bench.configure(options.binary, options.engine, options.setting)
    if options.setting is not None:
        def snapshot_settings(node):
            return remote(node, """set -e
ray config get mesh-offload
sha256sum /opt/raygate/ray | cut -d ' ' -f1
curl --fail --silent http://127.0.0.1:9090/metrics |
    grep -E '^rayfish_(tun_tcp_offload|tun_udp_offload|mesh_quic_segmentation_offload)_enabled '
""").splitlines()
        settings = bench.parallel(snapshot_settings)
        (OUT / f"{options.flavor}-settings.json").write_text(json.dumps(settings, indent=2) + "\n")
        print("settings snapshots", json.dumps(settings), flush=True)
    for profile in options.profiles:
        try:
            if profile == "cold":
                cold(options.flavor, targets, 10)
                continue
            if profile.startswith("stream"):
                stream(options.flavor, targets, "bad" if profile == "stream-bad" else "uncapped")
                continue
            shape(profile)
            bench.parallel(lambda node: remote(node, "systemctl restart raygate"))
            bench.parallel(bench.ready)
            direct()
            rows = []
            for i in range(options.runs):
                src_idx = (i + (options.first_side == "b")) % 2
                if i and i % 2 == 0:
                    bench.parallel(lambda node: remote(node, "systemctl restart raygate"))
                    bench.parallel(bench.ready)
                direct()
                idle = bench.idle_ping(bench.HOSTS[src_idx], targets[1 - src_idx])
                rows.append(bench.measure(
                    bench.HOSTS[src_idx], bench.HOSTS[1 - src_idx], targets[1 - src_idx],
                    options.flavor, profile, int(profile == "loss"), i + 1,
                    "mixed-kernel" if profile == "flood" else "tcp",
                    f"{bench.HOSTS[src_idx]}-{bench.HOSTS[1 - src_idx]}", idle,
                ))
                direct()
            bench.summarize(options.flavor, profile, rows)
            if profile in ("bad", "loss"):
                refusal_counts = bench.parallel(lambda h: remote(
                    h, "journalctl -u raygate --since '4 minutes ago' --no-pager | grep -Ec 'halting segmentation offload' || true",
                ).strip())
                print("kernel refusals", refusal_counts, flush=True)
        except (RuntimeError, subprocess.SubprocessError, KeyError, ValueError) as error:
            record = {"flavor": options.flavor, "profile": profile, "error": str(error)}
            print(json.dumps(record), flush=True)
            with (OUT / "errors.jsonl").open("a") as failed:
                failed.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
