#!/usr/bin/env bash
# usage: r.sh <a|b> ; command on stdin
DIR="$(cd "$(dirname "$0")" && pwd)"
ip="$(awk -v l="bench-$1" '$3==l{print $2}' "$DIR/.servers")"
exec ssh -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$DIR/known_hosts" -o ConnectTimeout=8 -o BatchMode=yes -o LogLevel=ERROR "root@$ip" bash -s
