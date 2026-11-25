FROM ubuntu:26.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends iperf3 iproute2 ethtool iputils-ping procps jq python3 linux-tools-common strace && rm -rf /var/lib/apt/lists/*
