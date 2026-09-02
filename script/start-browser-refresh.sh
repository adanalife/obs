#!/usr/bin/env bash
# Supervisor program: periodic dead-browser-source reload.
#
# Runs as its own supervisor program so failures get restarted and its
# logs land in kubectl logs alongside the other programs. Each cycle
# screenshots every browser_source and respawns the renderer of any that
# shows a blank frame — a crashed CEF page, or one that loaded before
# onscreens-server was up. Sources with content on them are left alone.
#
# Connects via obs-websocket on localhost:4455, so it depends on OBS being
# up and listening. supervisor's autorestart handles the case where this
# script starts before OBS is ready (it'll just fail fast on the first
# attempt and retry).
set -euo pipefail

# The first check waits for OBS and the overlays to come up, so a page still
# loading isn't mistaken for a dead one; after that, every five minutes.
sleep 120
while :; do
  OBS_WEBSOCKET_HOST=localhost OBS_WEBSOCKET_PORT=4455 \
    timeout 60 /opt/obs/venv/bin/python /opt/obs/bin/obs-browser-refresh \
    || echo "[browser-refresh] failed (will retry next cycle)" >&2
  sleep 300
done
