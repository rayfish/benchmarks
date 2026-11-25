#!/usr/bin/env bash
# prof.sh <name> <seconds>: perf the sending daemon (rb-a) during an iperf3 run.
cd "$(dirname "$0")"
RP=$(docker top rb-a -eo pid,args | awk '/ray daemon/ && !/sh -c/ {print $1; exit}')
echo "daemon pid $RP"
./tput.sh 20 > /tmp/tp.out &
sleep 4
sudo -n perf record -F 999 -g --call-graph ${CG:-dwarf,16384} -p "$RP" -o "/tmp/$1.perf" -- sleep "${2:-8}" 2>&1 | tail -1
wait; cat /tmp/tp.out
sudo -n perf report -i "/tmp/$1.perf" --no-children --sort comm,dso,sym --stdio 2>/dev/null | grep -v '^#' | grep '%' | head -45 > "$1.top.txt"
sudo -n perf report -i "/tmp/$1.perf" --no-children --sort comm --stdio 2>/dev/null | grep '%' > "$1.threads.txt"
