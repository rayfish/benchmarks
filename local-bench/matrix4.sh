#!/usr/bin/env bash
# matrix4.sh <bin> <envvar> <values...>: fq-codel engine, loadping per value, 2 setups.
cd "$(dirname "$0")"
bin=$1; var=$2; shift 2
for r in 1 2; do for v in "$@"; do
  ENGINE=fq-codel ./pair.sh up "${BENCH_BIN_DIR:?Set BENCH_BIN_DIR to the build directory}"/$bin 0 "" "$var=$v" > /tmp/pairup.log 2>&1 || { echo "setup failed: $(head -1 /tmp/pairup.log)"; continue; }
  echo "setup$r $bin $var=$v $(./loadping.sh) retr=$(jq .end.sum_sent.retransmits /tmp/lp-iperf.json)"
done; done
./pair.sh down
