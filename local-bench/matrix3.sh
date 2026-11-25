#!/usr/bin/env bash
cd "$(dirname "$0")"
for b in ${BUILDS:-master tun fix2 fix-tun}; do
  ./pair.sh up "${BENCH_BIN_DIR:?Set BENCH_BIN_DIR to the build directory}"/ray-$b 0 "" >/dev/null 2>&1 || { echo "$b setup failed"; continue; }
  for i in 1 2 3; do echo "$b ${ENGINE:-standalone} run$i $(./loadping.sh)"; done
done
./pair.sh down
