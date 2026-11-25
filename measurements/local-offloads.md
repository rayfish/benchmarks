# Local offload comparisons

Historical bulk TCP measurements on a shared Linux kernel with two CPU-pinned containers.

| Build | Mbit/s | ping p99 under load |
|---|---|---|
| master | 960 | 1.7 ms |
| GSO (fix/quic-gso-fallback) | 1,830 | 2.0 ms |
| TUN offload only | 1,170 | 3.8 ms |
| GSO + TUN (perf/tun-offload) | 4,290 | 3.1 ms |
| GSO + TUN + fat LTO | 4,800 | 2.4 ms |
| GSO + TUN, fq-codel engine, before | 2,730 | 1.2 ms |
| GSO + TUN, fq-codel engine, perf/fq-codel-throughput | 3,475 | 1.2 ms |
