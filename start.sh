#!/bin/sh
cd /app/agent && python main.py &
AGENT_PID=$!
node /app/frontend/.output/server/index.mjs &
NODE_PID=$!

trap 'kill $AGENT_PID $NODE_PID; wait $AGENT_PID $NODE_PID' TERM INT

wait $AGENT_PID
wait $NODE_PID
