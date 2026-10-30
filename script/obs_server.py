"""obs-server: tiny HTTP listener exposing OBS's health and version.

OBS itself doesn't expose an HTTP surface (only obs-websocket on :4455).
obs-server serves `/health/ready` and `/version` in the same shape the Go
services expose from their own listeners, so the console can treat OBS like
its sibling backend services in the status table. Named for symmetry with
onscreens-server.

Flask was picked over stdlib http.server because the route DSL reads
obvious and we already have a Python venv in the image for obsws-python
+ websockify. The traffic shape is one-request-every-few-seconds at
peak (panel render polls), so Flask's built-in development server is
plenty — no need for gunicorn/waitress.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify

# Bake-time version files written by the OBS Dockerfile, matching the
# convention the Go services use (their /version reads VCS info from
# the binary itself; we mirror via files).
VERSION_FILE = Path("/etc/tripbot/version")
SHA_FILE = Path("/etc/tripbot/sha")

app = Flask(__name__)
started_at = time.time()


def _read_or_default(path: Path, default: str) -> str:
    """Best-effort file read — returns default if the file's missing.

    Local docker builds without the /etc/tripbot/* bake step land here.
    """
    try:
        return path.read_text(encoding="utf-8").strip() or default
    except OSError:
        return default


@app.get("/health/ready")
def ready() -> tuple[str, int]:
    """Liveness/readiness — if Flask is answering, the shim is up.

    We deliberately don't poke obs-websocket here: that surface lives
    on :4455 and OBS readiness is its own signal. This shim's job is
    just to expose health and version.
    """
    return "OK\n", 200


@app.get("/version")
def version():
    """Build metadata as JSON. Mirrors the Go services' /version shape:
    {tag, sha, built_at, started_at} — the console parses this
    identically across all four services.
    """
    return jsonify(
        tag=_read_or_default(VERSION_FILE, "dev"),
        sha=_read_or_default(SHA_FILE, ""),
        # built_at is the file mtime of /etc/tripbot/version — close
        # enough to "when this image was assembled" for the panel's needs.
        built_at=_built_at(),
        started_at=datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat(),
    )


def _built_at() -> str:
    """Image-build time as RFC3339, or '' if the version file is missing
    (local builds without the bake step)."""
    try:
        mtime = VERSION_FILE.stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def main() -> None:
    """Run the Flask app under supervisord (foreground, no debug/reload).

    Binds to all interfaces inside the pod so the k8s Service can reach
    it. Port matches the `EXPOSE 8080` directive in the OBS Dockerfile +
    the cdk8s Service's obs-server port.
    """
    app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
