"""The wire contract OBS shares with the rest of the tripbot streaming system.

OBS is fed by the per-platform MediaMTX relay (RTSP dashcam feed, published
into by playout) and onscreens-server (browser-source overlays), and each OBS
instance talks to the relay/onscreens of its OWN platform (obs-twitch →
mediamtx-twitch / onscreens-twitch). These names + ports are a hand-maintained
contract with the infra repo's MediaMTX construct and tripbot's `pkg/contract`.
If a service name or port changes there, change it here.
"""

from __future__ import annotations

# OBS's own container/service ports, in the order the Deployment + Service
# list them.
PORTS: list[tuple[str, int]] = [
    ("vnc", 5900),
    ("websocket", 4455),
    ("novnc", 6080),
    ("obs-server", 8080),
]
NOVNC_PORT = 6080

# Feeders OBS reaches (per-platform Services in the same namespace).
_MEDIAMTX_RTSP_PORT = 8554
_ONSCREENS_HTTP_PORT = 8080


def dashcam_rtsp_url(platform: str) -> str:
    return f"rtsp://mediamtx-{platform}:{_MEDIAMTX_RTSP_PORT}/dashcam"


def onscreens_url_base(platform: str) -> str:
    return f"http://onscreens-{platform}:{_ONSCREENS_HTTP_PORT}"
