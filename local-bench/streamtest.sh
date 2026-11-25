#!/usr/bin/env bash
# streamtest.sh: reliability WebSocket burst transfer, server on rb-b, client on rb-a; prints the summary JSON.
cd "$(dirname "$0")"
B="$(cat /tmp/rb-bvpn)"
docker exec rb-b sh -c 'pkill -f stream.py; true'
docker exec -d rb-b python3 /bench/stream.py server "$B"
for _ in $(seq 50); do docker exec rb-b sh -c 'ss -ltnH | grep -q :38923' && break; sleep 0.2; done
docker exec rb-a timeout --kill-after=2s 180s python3 /bench/stream.py client "$B" 2>&1 | tail -1 | cut -c1-400
