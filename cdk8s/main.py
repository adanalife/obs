"""Synthesizes cdk8s/dist/<env>-obs-<platform>.k8s.yaml for every OBS instance.

Run via `task cdk8s:synth` (uv run --group cdk8s python cdk8s/main.py). Plain
python — no cdk8s-cli needed; jsii brings its own node runtime requirement,
pinned in .tool-versions. One Chart per (env, platform) → one dist file each,
matching how Argo applies them.
"""

from __future__ import annotations

import json
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
                stream_key_sm=(
                    f"/k8s/obs/{platform}-stream-key" if streaming else None
                ),
                # twitch is the container's default platform, so only
                # non-twitch instances carry the key (same idiom as tripbot).
                extra_config=(
                    {"STREAM_PLATFORM": platform} if platform != "twitch" else None
                ),
            )
    app.synth()

    # Discovery index for infra's obs ApplicationSet: one tiny JSON per deploy
    # unit at dist/apps/<env>-<app>.json. infra's git-files generator globs these
    # to self-discover one Application per unit. Byte-identical to what the
    # gateway/tripbot emit.
    apps_dir = Path(__file__).parent / "dist" / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    for env in ENVS.values():
        for platform in env.platforms:
            entry = {"env": env.name, "app": f"obs-{platform}"}
            (apps_dir / f"{env.name}-obs-{platform}.json").write_text(
                json.dumps(entry, indent=2, sort_keys=True) + "\n"
            )


if __name__ == "__main__":
    main()
