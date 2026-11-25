#!/usr/bin/env bash
# matrix3b.sh <bin> <extra-env-prefix> <bufs...>: loadping per CPU layout x buffer, 2 setups each.
cd "$(dirname "$0")"
bin=$1; extra=$2; shift 2
for cpus in "2,3 4,5" "2 4"; do set -- ${cpus} "$@"; a=$1; b=$2; shift 2
  for r in 1 2; do for buf in "$@"; do
    CPUS_A=$a CPUS_B=$b ./pair.sh up "${BENCH_BIN_DIR:?Set BENCH_BIN_DIR to the build directory}"/$bin 0 "" "$extra EXP_DGRAM_BUF=$buf" > /tmp/pairup.log 2>&1 || { echo "setup failed: $(head -1 /tmp/pairup.log)"; continue; }
    echo "cpus=$a setup$r $bin buf=$buf $(./loadping.sh) retr=$(jq .end.sum_sent.retransmits /tmp/lp-iperf.json)"
  done; done
done
./pair.sh down
