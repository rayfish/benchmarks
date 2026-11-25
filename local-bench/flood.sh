#!/usr/bin/env bash
# flood.sh: one mixed-kernel run a->b; prints small-flow Mbit/s, bulk, ping p50/p99/loss.
cd "$(dirname "$0")"
B="${TARGET:-$(cat /tmp/rb-bvpn)}"
docker exec rb-b sh -c 'pkill -x iperf3; pkill -f tcp_window.py; true'
docker exec rb-b iperf3 -s -D -1 -J -p 5201
docker exec rb-b iperf3 -s -D -1 -J -p 5202
docker exec -d rb-b sh -c 'python3 /bench/tcp_window.py > /tmp/sw.json 2>/tmp/sw.err'
sleep 0.5
m=$(docker exec rb-a python3 /bench/flood_measure.py "$B")
sw=$(docker exec rb-b cat /tmp/sw.json | jq -r '.mbps*1000|round/1000')
echo "small=$sw $m"
