"""The wire contract OBS shares with the rest of the tripbot streaming system.

OBS is fed by tripbot's vlc-server (RTSP dashcam feed) and onscreens-server
(browser-source overlays), and each OBS instance talks to the vlc/onscreens of
its OWN platform (obs-twitch → vlc-twitch / onscreens-twitch). These names +
ports are a hand-maintained contract with tripbot's `pkg/contract` — the same
duplicate-by-hand model tripbot-console and platform-gateway use for the
eventbus envelopes. If a service name or port changes in tripbot, change it here.
"""

from __future__ import annotations

# OBS's own container/service ports, in the order the Deployment + Service list
# them (matches the original construct).
PORTS: list[tuple[str, int]] = [
    ("vnc", 5900),
    ("websocket", 4455),
    ("novnc", 6080),
    ("obs-server", 8080),
]
NOVNC_PORT = 6080

# tripbot feeders OBS reaches (per-platform Services in the same namespace).
_VLC_RTSP_PORT = 8554
_VLC_HTTP_PORT = 8080
_ONSCREENS_HTTP_PORT = 8080


def dashcam_rtsp_url(platform: str) -> str:
    return f"rtsp://vlc-{platform}:{_VLC_RTSP_PORT}/dashcam"


def vlc_url_base(platform: str) -> str:
    return f"http://vlc-{platform}:{_VLC_HTTP_PORT}"


def onscreens_url_base(platform: str) -> str:
    return f"http://onscreens-{platform}:{_ONSCREENS_HTTP_PORT}"
