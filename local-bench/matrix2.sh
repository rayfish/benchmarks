#!/usr/bin/env bash
cd "$(dirname "$0")"
for b in ${BUILDS:-master fix2 fix-tun}; do
  DELAY=${DELAY-20} ./pair.sh up "${BENCH_BIN_DIR:?Set BENCH_BIN_DIR to the build directory}"/ray-$b 0 100mbit >/dev/null 2>&1 || { echo "$b setup failed"; continue; }
  for i in 1 2 3; do
    ( sleep 9; docker exec rb-a tc -s qdisc show dev eth0 | grep -A3 'parent 1:10' | grep backlog > /tmp/backlog ) &
    r=$(./flood.sh 2>&1 | tail -1); wait
    echo "$b run$i $r netem:$(cat /tmp/backlog)"
  done
  echo "   path=$(docker exec rb-a ray status --json | jq -r '.networks[0].peers[0].connection.conn_type')"
done
./pair.sh down
