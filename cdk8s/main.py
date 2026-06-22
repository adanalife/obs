"""Synthesizes cdk8s/dist/<env>-obs-<platform>.k8s.yaml for every OBS instance.

Run via `task cdk8s:synth` (uv run --group cdk8s python cdk8s/main.py). Plain
python — no cdk8s-cli needed; jsii brings its own node runtime requirement,
pinned in .tool-versions. One Chart per (env, platform) → one dist file each,
matching how Argo applies them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cdk8s

sys.path.insert(0, str(Path(__file__).parent))

from config import ENVS  # noqa: E402
from obs_app import ObsInstance  # noqa: E402


def main() -> None:
    app = cdk8s.App(outdir=str(Path(__file__).parent / "dist"))
    for env in ENVS.values():
        for platform in env.platforms:
            chart = cdk8s.Chart(app, f"{env.name}-obs-{platform}")
            streaming = platform in env.obs_streaming
            ObsInstance(
                chart,
                platform,
                env=env,
                streaming=streaming,
                stream_key_sm=(f"k8s/obs/{platform}-stream-key" if streaming else None),
                extra_config=(
                    {"STREAM_PLATFORM": "youtube"} if platform == "youtube" else None
                ),
            )
    app.synth()


if __name__ == "__main__":
    main()
