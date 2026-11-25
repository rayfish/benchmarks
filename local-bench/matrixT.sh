#!/usr/bin/env bash
# Throughput + ping under load, 3 fresh pairs x 1 loadping per build.
cd "$(dirname "$0")"
for r in 1 2 3; do for b in ${BUILDS:-master fix2 tun fix-tun}; do
  ./pair.sh up "${BENCH_BIN_DIR:?Set BENCH_BIN_DIR to the build directory}"/ray-$b 0 "" >/dev/null 2>&1 || { echo "$b setup failed"; continue; }
  echo "setup$r $b ${ENGINE:-standalone} $(./loadping.sh)"
done; done
./pair.sh down
