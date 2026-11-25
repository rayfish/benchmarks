# Linux data plane release gates

Rayfish candidate `89bf0b26a` on two dedicated 2-vCPU Linux 6.8 VMs in November 2026. Throughput, CPU, loaded ping and flood rows use medians of six direct transfers, alternating direction. CPU sums both guests. The flood used a 1.1 MB byte-limited queue. Cold transfers restarted both daemons before each run.

| Gate | Target | Measured | Result |
|---|---|---|---|
| Uncapped throughput | >= 1400 Mbit/s | 1,268.37 Mbit/s | Fail |
| Uncapped CPU | <= 1.9 cores/Gbit/s | 1.857 cores/Gbit/s | Pass |
| Uncapped loaded ping p99 | <= 6.5 ms | 8.43 ms | Fail |
| Uncapped p99 stretch | <= 3.2 ms | 8.43 ms | Fail |
| Checksum off, 100 Mbit/s, 40 ms RTT | >= 88 Mbit/s | 87.67 Mbit/s | Fail |
| Checksum off, loaded ping p99 | <= 108 ms | 107.00 ms | Pass |
| Checksum off, 1% loss | >= 2.4 Mbit/s | 2.59 Mbit/s | Pass |
| NIC offloads on, shaped CPU | <= 0.44 cores | 0.501 cores | Fail |
| FQ-CoDel uncapped throughput | >= 1046 Mbit/s | 856.75 Mbit/s | Fail |
| Standalone small TCP flow under UDP flood | >= 0.0125632 Mbit/s | 0.03672 Mbit/s | Pass |
| FQ-CoDel small TCP flow under UDP flood | >= 0.00997751 Mbit/s | 0.02223 Mbit/s | Pass |
| First transfer after daemon start | 10 consecutive without a one-second pause | 10/10, median 79.43 Mbit/s | Pass |
| Offload switch | Same binary disables QUIC and TUN | All three gauges zero on both peers, 87.67 Mbit/s | Pass |
| Established TCP socket across NIC offload change (local KVM) | Continue in both directions | 101.8 and 175.6 MB, both kernels refused GSO | Pass |
| Cold transfers, fine sampling | 10/10 with data in every full 100 ms interval | 0/10, longest empty span 0.300 s | Fail |

## Platform builds

| Gate | Target | Measured | Result |
|---|---|---|---|
| Windows | x86-64 and ARM64 CLI | Both release binaries linked, x86-64 version and help passed on Windows | Pass |
| FreeBSD | x86-64 CLI | Release binary linked | Pass |
| Android | Android NDK build | ARM64 and x86-64 release libraries linked, debug APK assembled, Android unit tests passed | Pass |
| macOS | Apple cross-build | ARM64 and x86-64 CLI and bridge linked, unsigned ARM64 release app built, migration, IPC and UI checks passed | Pass |
| iOS | Apple cross-build | ARM64 device and simulator bridges linked, unsigned simulator release app built | Pass |

## Matched local comparison

Two CPU-pinned Docker peers, three fresh starts per build, one 12-second TCP flow and 100 pings per second. Rayfish 0.5.17 reached 953 Mbit/s with 2.23 ms loaded p99. The candidate reached 4,673 Mbit/s with 2.95 ms loaded p99.
