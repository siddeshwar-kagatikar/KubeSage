#!/bin/sh
# Requires --cap-add NET_ADMIN. RATE uses tc units, e.g. 160mbit (~20 MB/s) or 16mbit (~2 MB/s).
set -e
RATE="${RATE:-160mbit}"
tc qdisc replace dev eth0 root tbf rate "$RATE" burst 512kb latency 500ms
echo "registry-proxy: egress on eth0 limited to $RATE"
exec nginx -g 'daemon off;'
