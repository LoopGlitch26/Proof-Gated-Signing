#!/bin/bash
# usage: ./node.sh PORT   (starts/restarts a Hardhat node on PORT)
PORT=${1:-8545}
PID=$(cat /tmp/hh_$PORT.pid 2>/dev/null); [ -n "$PID" ] && kill $PID 2>/dev/null; sleep 1
cd "$(dirname "$0")" && setsid nohup npx hardhat node --port $PORT > /tmp/hh_$PORT.log 2>&1 &
echo $! > /tmp/hh_$PORT.pid
for i in $(seq 1 40); do
  curl -s -X POST -H 'content-type: application/json' \
    --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' http://127.0.0.1:$PORT >/dev/null && echo up && exit 0
  sleep 1
done
echo fail; exit 1
