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
