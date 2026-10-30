#!/usr/bin/env bash
# Supervisor program: tiny Flask shim exposing /health/ready and /version
# on :8080. Lets the console treat OBS like
# the Go services (tripbot/onscreens-server) which each expose
# the same surface from their own HTTP listeners.
#
# Lives in the shared /opt/obs/venv (obsws-python + websockify + flask).
set -euo pipefail

exec /opt/obs/venv/bin/python /opt/obs/script/obs_server.py
