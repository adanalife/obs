#!/usr/bin/env bash
# Supervisor program: hourly browser-source refresh.
#
# Runs as its own supervisor program so failures get restarted and its
# logs land in kubectl logs alongside the other programs. CEF has a
# per-frame memory leak (the capped browser-source render rate slows the
# bleed, but the leak is still there); refreshing every browser_source
# once an hour drops the accumulated render state so RSS stays bounded
# across multi-day uptimes.
#
# Connects via obs-websocket on localhost:4455, so it depends on OBS being
# up and listening. supervisor's autorestart handles the case where this
# script starts before OBS is ready (it'll just fail fast on the first
# attempt and retry).
set -euo pipefail

# Long sleep at startup so the first refresh happens an hour after pod
# start, not immediately on boot.
while sleep 3600; do
  OBS_WEBSOCKET_HOST=localhost OBS_WEBSOCKET_PORT=4455 \
    timeout 60 /opt/obs/venv/bin/python /opt/obs/bin/obs-browser-refresh \
    || echo "[browser-refresh] failed (will retry next cycle)" >&2
done
