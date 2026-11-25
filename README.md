# Rayfish benchmarks

Measure Rayfish packet processing, tunnel throughput, CPU cost and latency under load. [Release gate results](measurements/release-gates.md) include the two-VM comparison and a matched local comparison with 0.5.17.

## Run locally

`cargo bench --locked --bench forward` runs the Criterion benchmarks against the Rayfish revision in Cargo.toml. `cargo bench --locked --bench apple_bridge` prints the bridge measurements as CSV. [Microbenchmark groups](benches/README.md)

For a Linux tunnel test, build the Docker image and supply an absolute path to a Linux Rayfish binary. The pair uses a Docker bridge, privileged containers and two CPUs per peer. Override `CPUS_A` and `CPUS_B` to select available CPUs.

```sh
docker build -t raybench:local local-bench
cd local-bench
./pair.sh up /path/to/ray
trap './pair.sh down' EXIT
./loadping.sh
```

The loaded ping test uses one 12-second TCP stream and 100 pings per second. Use fresh pairs for independent repeats. For a byte-limited flood test on this bridge, start the pair with `LEAF=bfifo ./pair.sh up /path/to/ray 0 100mbit`, then run `./flood.sh`. The VM release harness also adds the 40 ms RTT.

## Two-guest release gates

Run from `local-bench/release-gates/` with Python 3.12 or later. Use disposable Linux guests. The harness installs a daemon and changes traffic control, firewall and NIC settings. The cloud runner accepts an existing pair.

1. Prepare two Ubuntu guests with root SSH access, systemd, iperf3, iproute2, iputils-ping, ethtool, iptables, jq, curl and Python. Use a pair with direct connectivity.
2. Set `RAY_GATE_CLOUD_OUT` to a private results directory. Create `.servers` inside it with one line per guest: `1 HOST_A bench-a` and `2 HOST_B bench-b`. Replace the host placeholders with SSH destinations. If the results path is long, set `RAY_GATE_CLOUD_SOCKETS` to a short existing directory.
3. Supply a Linux binary and select the forwarding engine and profiles.

```sh
python3 cloud_bench.py ray-candidate /path/to/ray standalone uncapped bad loss nic flood --runs 6
python3 cloud_bench.py ray-candidate-fq /path/to/ray fq-codel uncapped flood --runs 6
python3 cloud_bench.py ray-candidate /path/to/ray standalone cold stream-bad
python3 finecold.py /path/to/ray --setting on
```

Steady-state runs alternate direction and collect idle ping before the transfer. Cold transfers start immediately after daemon restart. The flood combines 150 Mbit/s UDP with 1 Mbit/s TCP and uses a 1.1 MB byte-limited queue. Set `--setting off` to test the runtime switch. Run `python3 check_harness.py` to verify the warm-up sequence without guests.

For local KVM, put the Ubuntu Noble amd64 cloud image named `noble-server-cloudimg-amd64.img` in `RAY_GATE_VM_DIR`. Install QEMU, cloud-image-utils, iproute2 and ethtool. The runner needs KVM, passwordless sudo for networking, and SSH. Run `bash vms.sh boot`, allow cloud-init to finish on both guests, then use `vm_bench.py` with the same binary and engine arguments. Run `bash vms.sh down` afterward. Use `resume` for existing guest disks.

## Other suites

| Suite | Usage and results |
|---|---|
| Cloud throughput and latency | Clone Rayfish and this repository side by side. From Rayfish run `tests/e2e.sh bench provision`, `tests/e2e.sh bench`, then `tests/e2e.sh bench teardown`. This uses the Rayfish test backend and its authenticated cloud CLI. Set `RAYFISH_BENCHMARKS` for a different checkout location. After provisioning, `cloud/ab.sh` compares two Rayfish checkouts on the same pair. |
| Tunnel comparisons | `tunnel-benchmark/` and `tunnel-benchmark-2/` compare direct, WireGuard, Tailscale and Rayfish. The second round adds historical offload variants. [Round one](measurements/tunnel-benchmark.csv) and [round two](measurements/tunnel-benchmark-2.csv) |
| Congestion control | `congestion-benchmark/` measures single-stream, multistream and UDP workloads on a preconfigured Rayfish pair. [Results](measurements/congestion-benchmark.csv) |
| FQ-CoDel | `fq-codel-benchmark/` measures the small TCP flow during overload. Supply `RAY_BINARY` to `setup.py` before running `run.py`. [Results](measurements/fq-codel-benchmark.csv) |
| BBR | `RAY_BINARY=/path/to/ray python3 bbr-bench/bench.py` compares BBR and CUBIC on the Docker bridge. Build the Docker image first. [Results](measurements/bbr.csv) |
| Local offloads | `local-bench/matrixT.sh` repeats the loaded ping test across builds. Set `BENCH_BIN_DIR` to a directory of executables named `ray-<label>`, and `BUILDS` to their space-separated labels. [Results](measurements/local-offloads.md) |

The historical SSH suites read `.servers` from their own directory. For tunnel round two, `setup.sh` expects `RAY_BASELINE_BINARY`, `RAY_BENCH_BINARY` and `RAY_LTO_BINARY` to point to Linux x86-64 executables. The original experiment binaries recognized the per-feature switches in `bench.py`. Run `python3 bench.py 1,2,3` and `python3 summarize.py` inside the suite. Each repeat covers both directions.

The Linux GSO probes in `local-bench/` can be built with `cc -shared -fPIC -o gso_eio.so gso_eio.c -ldl` and the same command for `gso_count.c`. Pass `1` or `count` as the second argument to `pair.sh up` to inject or count segmented sends.
