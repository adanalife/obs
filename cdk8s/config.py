"""Per-environment OBS deployment config.

A slim, self-contained EnvConfig holding only the fields the OBS deployment
needs.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_VERSIONS_FILE = Path(__file__).resolve().parent / "versions.yaml"


@lru_cache(maxsize=1)
def _versions() -> dict:
    return yaml.safe_load(_VERSIONS_FILE.read_text()) or {}


# The fleet-wide supported-platform set, owned by platform-gateway (its Go
# adapter registry is the source of truth) and synced into this repo's
# platforms.json via `task platforms:sync`. Every env's `platforms` must be a
# subset of it (validated below). Never hand-edit platforms.json — add an
# adapter in the gateway + re-sync.
_PLATFORMS_FILE = Path(__file__).resolve().parents[1] / "platforms.json"


def _load_supported_platforms() -> tuple[str, ...]:
    import json

    with _PLATFORMS_FILE.open() as f:
        return tuple(json.load(f)["platforms"])


SUPPORTED_PLATFORMS = _load_supported_platforms()


@dataclass(frozen=True)
class EnvConfig:
    name: str
    namespace: str
    cluster: str  # minipc | k3d | local
    image_tag: str  # floating tag (main | latest) for components without a pin
    dns_base: str  # prod.whereisdana.today | stage… | dev…  ("" for local → no Ingress)

    platforms: tuple[str, ...] = ("twitch",)
    # Platforms whose stream-key ExternalSecret is emitted (the live streamer).
    obs_streaming: tuple[str, ...] = ()

    gpu: bool = False  # node has the Intel iGPU (request gpu.intel.com/i915)
    obs_gpu: bool = True  # OBS claims the iGPU (gated on gpu and obs_gpu)
    obs_encoder: str = "obs_x264"  # ffmpeg_vaapi_tex on GPU envs
    obs_quality: str = "low"  # low | high
    # Per-platform video bitrate override, kbps ({} = the preset's value,
    # which is the platform max we want everywhere). Platform guidance for
    # 1080p60 H.264: YouTube recommends 6800 kbps (Studio warns below that);
    # Twitch's ingest max is 6000.
    obs_video_bitrate_kbps: dict[str, int] = dataclasses.field(default_factory=dict)
    # Per-platform framerate override, fps ({} = the preset's value — 60 on
    # high, 30 on low). tiktok runs 30 even on the high 1080p preset: its
    # portrait LIVE is unstable at 60fps.
    obs_fps: dict[str, int] = dataclasses.field(default_factory=dict)
    obs_cpu_request: str = "200m"
    # Which background-audio bed each platform's OBS *starts* on: somafm (the
    # Groove Salad Classic stream), carhum (the image-baked license-clean drone),
    # or album (the mounted music share). Platforms left out fall back to
    # entrypoint.sh's own default — somafm on twitch, album on tiktok, carhum
    # everywhere else. The bed is live-switchable from the console, so this only
    # picks the starting point, not a policy.
    obs_background_audio: dict[str, str] = dataclasses.field(default_factory=dict)
    # Mount the read-only `obs-music` PVC (the NFS-backed album share the infra
    # repo provisions). Only the minipc envs have that volume; k3d/local run
    # without it and entrypoint.sh falls back to the carhum bed if asked for the
    # album anyway.
    music_share: bool = False
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
        return "IfNotPresent" if self.is_pinned(component) else "Always"

    def is_pinned(self, component: str) -> bool:
        """True when this env deploys an immutable release tag (from
        versions.yaml) rather than the floating tag. A pinned tag can be a
        brand-new version whose image isn't built yet — the case the PreSync
        image gate guards."""
        return component in _versions().get(self.name, {})


ENVS: dict[str, EnvConfig] = {
    "prod-1": EnvConfig(
        name="prod-1",
        namespace="prod-1",
        cluster="minipc",
        image_tag="latest",  # overridden by the versions.yaml pin
        dns_base="prod.whereisdana.today",
        # Every platform's OBS births parked at replicas:0; a console scale-up
        # brings one live and sticks (Argo ignores .spec.replicas). Only twitch
        # runs today. youtube waits on the pending YouTube Data API quota
        # extension — when scaled up it streams unlisted (stream-key SM
        # k8s/obs/youtube-stream-key, adanalife-prod). facebook VAAPI-encodes to
        # the prod Facebook Live ingest (SM k8s/obs/facebook-stream-key) once
        # scaled up; Dana takes it public from Facebook Live Producer. The iGPU
        # budget is two live encoders, so mind what holds a VAAPI slot before
        # scaling a second one up. tiktok streams the 9:16 vertical scene to a
        # per-session RTMP target — its ingest URL + key come from the Streamlabs
        # TikTok API, seeded into the stream-key secret (SM
        # k8s/obs/tiktok-stream-{server,key}, adanalife-prod). instagram
        # synthesizes here too (born parked, no obs_streaming) — it waits on its
        # own stream key before a console scale-up brings it live.
        platforms=SUPPORTED_PLATFORMS,
        obs_streaming=("twitch", "youtube", "facebook", "tiktok"),
        gpu=True,
        obs_gpu=True,
        obs_encoder="ffmpeg_vaapi_tex",
        obs_quality="high",
        # tiktok runs the high preset's 1080p at 30fps and 4000 kbps. TikTok
        # recommends 30fps for non-gaming/lifestyle content and its portrait
        # LIVE is unstable at 60. The preset's 6000 kbps ingests cleanly (OBS
        # reports zero congestion) but TikTok plays it back black; 4000 is a
        # known-good value — the exact ceiling in (4000, 6000] is untested.
        obs_fps={"tiktok": 30},
        obs_video_bitrate_kbps={"tiktok": 4000},
        obs_cpu_request="2",
        music_share=True,
        priority_class="prod-stream",
        tailscale=True,
    ),
    "stage-1": EnvConfig(
        name="stage-1",
        namespace="stage-1",
        cluster="minipc",
        image_tag="main",
        dns_base="stage.whereisdana.today",
        # Every stage platform births parked at replicas:0 — a platform comes
        # online via the console's scale-up button (Argo ignores .spec.replicas,
        # so the hand scale sticks). facebook is the current burn-in target
        # (streams to the ADL Staging Page); it's 16:9 and reuses the twitch
        # canvas — no per-platform scene work needed. Its stream-key
        # ExternalSecret stays emitted (obs_streaming) so a scale-up is
        # test-ready. tiktok streams too: the 9:16 vertical scene ships and its
        # ingest URL + key come from the Streamlabs TikTok API (seeded into the
        # stream-key secret). instagram synthesizes here parked and doesn't
        # stream yet — it waits on its own stream key.
        platforms=SUPPORTED_PLATFORMS,
        obs_streaming=("facebook", "tiktok"),
        gpu=True,
        obs_gpu=True,
        obs_encoder="ffmpeg_vaapi_tex",
        obs_quality="high",
        obs_fps={"tiktok": 30},  # 1080p30 vertical — see prod note
        obs_video_bitrate_kbps={
            "tiktok": 4000
        },  # known-good; 6000 plays back black on TikTok — see prod note
        obs_cpu_request="200m",
        music_share=True,
        prefer_rpi5=True,
        tailscale=True,
    ),
    "development": EnvConfig(
        name="development",
        namespace="development",
        cluster="k3d",
        image_tag="main",
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


# The beds entrypoint.sh knows how to write onto the "Background Audio" source.
# A value outside this set makes OBS exit 1 at boot, so it's caught at synth.
BACKGROUND_AUDIO_BEDS = ("somafm", "carhum", "album")


# Guard: an env can only run platforms the gateway has an adapter for.
for _name, _env in ENVS.items():
    _bad_beds = {
        p: b
        for p, b in _env.obs_background_audio.items()
        if b not in BACKGROUND_AUDIO_BEDS
    }
    if _bad_beds:
        raise ValueError(
            f"{_name}: obs_background_audio {_bad_beds} not in "
            f"{BACKGROUND_AUDIO_BEDS} — see set_background_audio in entrypoint.sh"
        )
    _unknown = tuple(p for p in _env.platforms if p not in SUPPORTED_PLATFORMS)
    if _unknown:
        raise ValueError(
            f"{_name}: platforms {_unknown} not in SUPPORTED_PLATFORMS "
            f"{SUPPORTED_PLATFORMS} — add an adapter in platform-gateway + run `task platforms:sync`"
        )
