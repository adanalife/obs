#!/usr/bin/env bash
# Called by Dockerfile{,.arm64} (HEALTHCHECK CMD) and by
# the OBS deployment's livenessProbe. Healthy when:
#   - the obs process is alive
#   - sway is reachable (compositor is up)
#   - the OBS-32 safe-mode crash dialog is NOT up
#   - OBS is on the program scene the entrypoint seeded, and it has sources
#
# Why specifically the safe-mode dialog: it appears on top of an empty
# desktop instead of the OBS main window, blocking everything until a
# human dismisses it. Other OBS modals ("Missing Files", etc.) leave
# the main window functional and are tolerable. Detection: walk the
# sway tree and fail when any node has a "Crash Detected" title.
#
# The first three are all negative — they pass just as happily on an OBS
# sitting on an empty canvas, which is what the scene check adds. It stays
# out of pixels on purpose: a blank source is bin/obs-screenshot-check's
# business, and restarting the pod does not repaint one.
set -u
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-root}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"
export SWAYSOCK="$XDG_RUNTIME_DIR/sway-ipc.sock"

pgrep -x obs >/dev/null || exit 1

# `swaymsg -t get_tree` proves the compositor is responsive. We don't
# need jq to walk the tree — a grep on the title field catches the
# safe-mode dialog without adding another package dep.
tree=$(swaymsg -t get_tree 2>/dev/null) || exit 1

if grep -qE '"name":\s*"[^"]*Crash Detected[^"]*"' <<<"$tree"; then
  echo "OBS safe-mode dialog blocking the main window" >&2
  exit 1
fi

# Positive check: the seeded scene is the one on air and it holds sources.
# Exits 0 when the WebSocket won't answer — that is unknown, not unhealthy,
# and the checks above are the ones qualified to say.
exec /opt/obs/venv/bin/python /opt/obs/bin/obs-scene-check
