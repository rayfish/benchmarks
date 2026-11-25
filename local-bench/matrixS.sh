#!/usr/bin/env bash
cd "$(dirname "$0")"
for shape in "" 100mbit; do for b in master fix2 fix-tun; do
  DELAY=20 ./pair.sh up "${BENCH_BIN_DIR:?Set BENCH_BIN_DIR to the build directory}"/ray-$b 0 "$shape" > /tmp/pairup.log 2>&1 || { echo "setup failed"; continue; }
  for i in 1 2 3; do echo "shape=${shape:-none} $b trial$i $(./streamtest.sh)"; done
done; done
./pair.sh down
