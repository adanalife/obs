"""Per-environment OBS deployment config.

A slim, self-contained EnvConfig holding only the fields the OBS deployment
needs — extracted from tripbot's adanalife_k8s.config.EnvConfig (which carried
config for every workload). The per-env values reproduce tripbot's OBS overlays
exactly; the synth output is diffed against the manifests that came over with
the split to guarantee parity.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_VERSIONS_FILE = Path(__file__).resolve().parent / "versions.yaml"


@lru_cache(maxsize=1)
def _versions() -> dict:
    return yaml.safe_load(_VERSIONS_FILE.read_text()) or {}


@dataclass(frozen=True)
class EnvConfig:
    name: str
    namespace: str
    cluster: str  # minipc | k3d | local
    image_tag: str  # floating tag (develop | latest) for components without a pin
    dns_base: str  # prod.whereisdana.today | stage… | dev…  ("" for local → no Ingress)

    platforms: tuple[str, ...] = ("twitch",)
    # Platforms whose stream-key ExternalSecret is emitted (the live streamer).
    obs_streaming: tuple[str, ...] = ()
    # Platforms rendered with replicas=0 (present but parked).
    parked_platforms: tuple[str, ...] = ()
    # When True, omit spec.replicas entirely (hand-scaled only — stage).
    manual_replicas: bool = False

    gpu: bool = False  # node has the Intel iGPU (request gpu.intel.com/i915)
    obs_gpu: bool = True  # OBS claims the iGPU (gated on gpu and obs_gpu)
    obs_encoder: str = "obs_x264"  # ffmpeg_vaapi_tex on GPU envs
    obs_quality: str = "low"  # low | high
    obs_cpu_request: str = "200m"
    priority_class: str = ""  # prod-stream on prod; "" elsewhere
    prefer_rpi5: bool = (
        False  # bias to the rpi5 worker (only when OBS is software-encoded)
    )
    tailscale: bool = False  # emit the tailscale Ingress alongside the traefik one

    def tag_for(self, component: str) -> str:
        """Pinned release tag from versions.yaml when present, else the floating tag."""
        return _versions().get(self.name, {}).get(component, self.image_tag)

    def pull_policy_for(self, component: str) -> str:
        """Pinned tags are immutable → IfNotPresent; floating tags → Always."""
        pinned = component in _versions().get(self.name, {})
        return "IfNotPresent" if pinned else "Always"

    @property
    def replicas(self) -> int | None:
        return None if self.manual_replicas else 1

    def replicas_for(self, platform: str) -> int | None:
        return 0 if platform in self.parked_platforms else self.replicas


ENVS: dict[str, EnvConfig] = {
    "prod-1": EnvConfig(
        name="prod-1",
        namespace="prod-1",
        cluster="minipc",
        image_tag="latest",  # overridden by the versions.yaml pin
        dns_base="prod.whereisdana.today",
        platforms=("twitch", "youtube"),
        obs_streaming=("twitch",),
        parked_platforms=("youtube",),
        gpu=True,
        obs_gpu=True,
        obs_encoder="ffmpeg_vaapi_tex",
        obs_quality="high",
        obs_cpu_request="2",
        priority_class="prod-stream",
        tailscale=True,
    ),
    "stage-1": EnvConfig(
        name="stage-1",
        namespace="stage-1",
        cluster="minipc",
        image_tag="develop",
        dns_base="stage.whereisdana.today",
        platforms=("twitch", "youtube"),
        obs_streaming=("youtube",),
        manual_replicas=True,
        gpu=True,
        obs_gpu=True,
        obs_encoder="ffmpeg_vaapi_tex",
        obs_quality="high",
        obs_cpu_request="200m",
        prefer_rpi5=True,
        tailscale=True,
    ),
    "development": EnvConfig(
        name="development",
        namespace="development",
        cluster="k3d",
        image_tag="develop",
        dns_base="dev.whereisdana.today",
        platforms=("twitch",),
        gpu=False,
        obs_encoder="obs_x264",
        obs_quality="low",
    ),
    "local": EnvConfig(
        name="local",
        namespace="default",
        cluster="local",
        image_tag="latest",
        dns_base="",
        platforms=("twitch",),
        gpu=False,
        obs_encoder="obs_x264",
        obs_quality="low",
    ),
}
