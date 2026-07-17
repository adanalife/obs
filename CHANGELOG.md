# Changelog

All notable changes to the adanalife OBS image are recorded here. The format is
managed by [towncrier](https://towncrier.readthedocs.io): every PR into `develop`
adds a fragment under `changelog.d/` (`task changelog:add PR=<n> TYPE=<type>`),
and `task changelog:build VERSION=x.y.z` collates them into this file at release
time.

This image was extracted from the [tripbot](https://github.com/adanalife/tripbot)
monorepo (full git history preserved) and starts a fresh version line at v1.0.0,
published as `ghcr.io/adanalife/obs`. The image content continues from tripbot's
former `adanalife/obs:3.4.1`.

<!-- towncrier release notes start -->

## [v2.2.0] — 2026-07-17

### Added

- Add a parked prod-1 obs-facebook instance (replicas:0) that VAAPI-encodes to the prod Facebook Live ingest via a persistent stream key when unparked. ([#48](https://github.com/adanalife/obs/pull/48))

## [v2.1.0] — 2026-07-17

### Added

- Facebook Live streaming support: `STREAM_PLATFORM=facebook` points the canvas at Facebook's RTMPS ingest (Car Hum audio bed, like YouTube); stage emits obs-facebook for the ADL Staging Page, parked at replicas:0 like every stage platform (console scale-up brings it online) ([#46](https://github.com/adanalife/obs/pull/46))
- Stage emits playout-facebook (publishing to the mediamtx-facebook relay), parked at replicas:0 like every stage platform (console scale-up brings it online) ([#71](https://github.com/adanalife/obs/pull/71))

### Changed

- Park prod obs-youtube at replicas:0 while the YouTube Data API quota extension is pending, freeing its iGPU VAAPI encoder slot ([#45](https://github.com/adanalife/obs/pull/45))

### Fixed

- The arm64 image installs `flask` in the obs-server venv, matching the amd64 image — obs-server would otherwise crash-loop on arm64 (it imports flask but the venv lacked it). ([#38](https://github.com/adanalife/obs/pull/38))
- Add an Argo PreSync hook that verifies the pinned image exists in the registry before a sync tears down the running pod, preventing an ImagePullBackOff outage when a deploy is synced ahead of its image build. ([#42](https://github.com/adanalife/obs/pull/42))

### Misc

- Release Discord notification links the version to the tagged `CHANGELOG.md` instead of an empty URL. ([#41](https://github.com/adanalife/obs/pull/41))

## [v2.0.0] — 2026-07-16

### Changed

- The Dashcam source reads from the per-platform MediaMTX relay (`rtsp://mediamtx-<platform>:8554/dashcam`, published into by playout) instead of vlc-server, making the A/B's runtime repoint permanent. The vlc-only "Next-frame preview" cover layer (and its `VLC_URL_BASE` config) is removed — playout's concat pipeline has no clip-swap gap to paper over. ([#29](https://github.com/adanalife/obs/pull/29))

### Misc

- Drop `--edit` from the `changelog:add` task so it no longer opens $EDITOR and hangs in non-interactive (Claude/CI) sessions. ([#39](https://github.com/adanalife/obs/pull/39))
- Standardize the towncrier bug-fix fragment type on `fix` (was `fixed`). ([#40](https://github.com/adanalife/obs/pull/40))

## [v1.2.0] — 2026-07-15

### Changed

- Lifted the prod YouTube 3000 kbps bitrate cap — back to the `high` preset's 6000 kbps. The cap targeted a suspected uplink bottleneck; the actual stutter cause was iGPU VAAPI-slot contention from stray stage OBS instances. ([#34](https://github.com/adanalife/obs/pull/34))

## [v1.1.0] — 2026-07-15

### Changed

- **Changelog fragments can be created without knowing the PR number** — write a `+`-placeholder fragment and CI numbers it on push. ([#28](https://github.com/adanalife/obs/pull/28))
- obs-youtube reads the dashcam feed from the MediaMTX relay (`rtsp://mediamtx-youtube:8554/dashcam`, published into by playout) instead of vlc-server; twitch stays on vlc-server until its playout re-cutover. ([#30](https://github.com/adanalife/obs/pull/30))
- Per-platform video bitrate override (`obs_video_bitrate_kbps` in cdk8s → `OBS_VIDEO_BITRATE` env; quality presets now default-if-unset). prod youtube is capped at 3000 kbps — below platform max, deliberately: two full-rate RTMP uploads saturate the home uplink and stutter the viewing path. Lift the cap when the uplink has headroom. ([#32](https://github.com/adanalife/obs/pull/32))

## [v1.0.5] — 2026-07-12

### Removed

- Removed the flag onscreen element from the scene config — it was rendering a 404 page on the stream. ([#26](https://github.com/adanalife/obs/pull/26))

## [v1.0.4] — 2026-07-11

### Changed

- Dashcam RTSP feed in OBS now uses software decoding (`hw_decode` disabled) to avoid the hardware decoder resetting its context on every clip boundary, which showed as a per-clip flash at video transitions. ([#15](https://github.com/adanalife/obs/pull/15))

## [v1.0.3] — 2026-07-03

### Changed

- Stream-key Secrets now sync from SSM Parameter Store instead of AWS Secrets Manager.

### Fixed

- Emoji in browser-source overlays (e.g. the 📍 live-location rotator line) rendered as tofu boxes — the image now ships `fonts-noto-color-emoji` as the fontconfig emoji fallback. ([#17](https://github.com/adanalife/obs/pull/17))
- Raise the SomaFM background-audio source (`Groove Salad Classic`) network buffer from 2 MB to 8 MB so normal edge jitter no longer overruns it and restarts the source mid-stream.

## [v1.0.2] — 2026-06-22

### Added

- Activate prod-1 YouTube streaming (unparked obs-youtube, streaming to the prod YouTube ingest). ([#6](https://github.com/adanalife/obs/pull/6))

## [v1.0.0] — 2026-06-22

### Added

- Standalone cdk8s deployment for the OBS image: self-contained per-env manifests (prod/stage twitch+youtube, dev, local) that no longer depend on the tripbot cdk8s framework. Synth output is parity-verified against the pre-split manifests; the image now resolves from `ghcr.io/adanalife/obs`. ([#1](https://github.com/adanalife/obs/pull/1))
