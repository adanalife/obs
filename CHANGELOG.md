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

## [v2.13.0] — 2026-08-20

### Changed

- The per-platform noVNC tailnet Ingresses are served by the shared Tailscale ProxyGroup instead of a dedicated proxy pod each. The `*.ts.net` hostnames are unchanged. ([#123](https://github.com/adanalife/obs/pull/123))

### Fixed

- `obs-screenshot-check` skips audio-only sources instead of failing on them, and `OBS_SCREENSHOT_SKIP` excludes overlays that are legitimately blank ([#121](https://github.com/adanalife/obs/pull/121))

## [v2.12.0] — 2026-08-19

### Added

- `bin/obs-input-repoint` — reads an OBS source's current input URL (the rollback value), then repoints it at a new one over obs-websocket. Built for swapping the Dashcam source between the MediaMTX relay and vlc-server mid-incident. Runtime-only: the declarative default stays `cdk8s/contract.py`'s `dashcam_rtsp_url()`. ([#99](https://github.com/adanalife/obs/pull/99))
- `bin/obs-screenshot-check` — screenshots every source in the live OBS scene over obs-websocket and fails on the blank ones, turning the manual overlay-debugging trick into a release-acceptance gate. A crashed browser_source comes back as a transparent PNG of a couple hundred bytes; anything under `OBS_SCREENSHOT_MIN_BYTES` (default 1000) is a failure, as is a source that can't be screenshotted at all. Read-only, so it's safe against the live stream. Set `SCREENSHOT_DIR` to keep the PNGs. ([#119](https://github.com/adanalife/obs/pull/119))
- `bin/obs-stream-key-rotate` — swaps the stream key on a running OBS over obs-websocket, so a key rotation no longer needs a pod restart and the stream gap that comes with it. Prints the current settings with the key masked first (the rollback breadcrumb), takes the new key from `$NEW_STREAM_KEY` or stdin rather than argv, and reports whether the output was live during the swap. `--dry-run` reads without writing. Runtime-only: the seeded secret still wins on the next restart. ([#120](https://github.com/adanalife/obs/pull/120))

### CI / Tooling

- The changelog-fragment numbering workflow now fails loudly when it cannot diff against the base commit, instead of reporting success having numbered nothing. ([#116](https://github.com/adanalife/obs/pull/116))
- Schema-validate the synthed `cdk8s/dist/` manifests with kubeconform, in both the PR-time gate and the main-branch synth backstop. ([#118](https://github.com/adanalife/obs/pull/118))

### Misc

- Re-sync `contract.json` from tripbot main, which now carries the NATS Service name and client port. Nothing here reads them; the sync keeps the drift gate green. ([#110](https://github.com/adanalife/obs/pull/110))
- The weekly super-linter sweep reports each validator as its own commit status again, instead of 403ing on every one. ([#114](https://github.com/adanalife/obs/pull/114))
- Drop the last references to `vlc-server` from the README and the in-repo comments. It was retired 2026-07-17 and replaced by [playout](https://github.com/adanalife/playout); the Dashcam source has read from the per-platform MediaMTX relay since [#29](https://github.com/adanalife/obs/pull/29). The README also documented `VLC_URL_BASE`, which that same PR deleted — the env-var table now lists only `ONSCREENS_URL_BASE`. ([#115](https://github.com/adanalife/obs/pull/115))
- The synthed manifests now carry two asserted invariants, checked in CI: no Ingress may publish anything but noVNC, and every OBS Deployment must be born parked at `replicas: 0`. `cdk8s-synth` already proves `dist/` matches the code and says nothing about whether either is right — a flip in one of those values reads as a plausible one-word golden diff. Publishing obs-server would put `POST /admin/shutdown`, which restarts the container feeding the live stream, on a public hostname. ([#117](https://github.com/adanalife/obs/pull/117))

## [v2.11.0] — 2026-08-08

### Changed

- cdk8s now reads service names and OBS container ports from `contract.json`, synced from tripbot via `task contract:sync`, instead of restating them by hand. A CI gate asserts the synced copy matches tripbot main. ([#101](https://github.com/adanalife/obs/pull/101))
- Every platform except Twitch now starts on the licensed album bed rather than the car-hum drone — YouTube, Facebook and Instagram join TikTok, which already did. Twitch keeps SomaFM. The drone remains the automatic fallback when the music share has no tracks, so envs without the PVC are unaffected. Still switchable live from the console. ([#105](https://github.com/adanalife/obs/pull/105))
- cdk8s now reads the per-platform MediaMTX relay's Service name and RTSP port from `contract.json` instead of building them by hand. Rendered manifests are unchanged. ([#109](https://github.com/adanalife/obs/pull/109))

### Fixed

- OBS now waits for its onscreens backend's `/health/ready` before starting, via an initContainer on the Deployment. CEF browser sources paint the overlays once at startup, so painting against an unready backend cached a blank result that persisted until the next refresh. ([#96](https://github.com/adanalife/obs/pull/96))
- Set `runAsNonRoot` on the PreSync image gate pod, so it conforms to the `restricted` PodSecurity profile the pinned namespaces run. ([#97](https://github.com/adanalife/obs/pull/97))
- stage-1 OBS boots at the `low` quality preset (720p30) instead of `high`, so scaling a stage platform up for a rehearsal never costs prod's live encoders one of the minipc's two iGPU slots. Every stage `obs-*` Deployment sits at 0 replicas, so this only changes what a future scale-up comes up as. ([#100](https://github.com/adanalife/obs/pull/100))
- Pull the dashcam RTSP feed over TCP instead of ffmpeg's default UDP — lost RTP packets under node CPU contention truncated frames mid-bitstream, smearing the bottom of the dashcam video with concealment artifacts on every platform. ([#106](https://github.com/adanalife/obs/pull/106))

### CI / Tooling

- The release-please workflow can be triggered manually, so a release branch left on an old base by chore-only merges can be rebased off current `main` without waiting for the next releasable commit. ([#108](https://github.com/adanalife/obs/pull/108))

### Misc

- Describe tiktok's ingest as the account-stable Streamlabs portrait restream target in the cdk8s env config comment. ([#95](https://github.com/adanalife/obs/pull/95))
- Re-sync `contract.json` from tripbot main, which now carries the per-platform MediaMTX service names and the RTSP port. ([#107](https://github.com/adanalife/obs/pull/107))

## [v2.10.1] — 2026-08-05

### Changed

- The album bed now plays from a node-local volume instead of the NAS share. OBS composites video and plays the bed in one process, so a share that stopped answering blocked the whole render pipeline — on 2026-08-05 that took both prod streams dark for 9h41m while OBS reported itself as streaming and encoded zero frames. ([#93](https://github.com/adanalife/obs/pull/93))

### CI / Tooling

- ruff now covers the `bin/` helpers by an explicit path pattern rather than by pre-commit reading their shebangs, so they stay linted if one is ever converted to a `uv run --script` shebang. The comment claiming cdk8s was the only Python in the repo was already untrue — `script/obs_server.py` and both `bin/` helpers are Python too. ([#81](https://github.com/adanalife/obs/pull/81))

## [v2.10.0] — 2026-08-02

### Added

- `car-hum-fallback.flac`, a copy of the idle drone at a distinct path, so tripbot can tell an operator-selected Car Hum bed from the audio watchdog's fallback to it. ([#79](https://github.com/adanalife/obs/pull/79))

## [v2.9.0] — 2026-07-29

### Changed

- TikTok now starts on the licensed album bed instead of the car-hum drone. Still switchable live from the console. ([#76](https://github.com/adanalife/obs/pull/76))

### Fixed

- Park the `Background Audio` scene item off-canvas so album tracks' embedded cover art stops rendering over the dashcam. Hiding the item is not an option — it stops the audio with it. ([#76](https://github.com/adanalife/obs/pull/76))

## [v2.8.2] — 2026-07-29

### Fixed

- A PreSync hook now asserts the `obs-music` claim is mountable before a sync tears the running OBS down. OBS deploys with strategy Recreate, so an unbound claim previously left the replacement pod Pending and the stream off the air; the gate fails the sync instead, leaving the live pod untouched. ([#74](https://github.com/adanalife/obs/pull/74))

## [v2.8.1] — 2026-07-29

### Fixed

- The album background-audio bed now starts only on tracks from album subdirectories of the music share. Loose audio files at the share root — it also holds a 556MB `carsounds.m4a` — could be picked as the starting track. ([#72](https://github.com/adanalife/obs/pull/72))

## [v2.8.0] — 2026-07-29

### Changed

- Background audio is now a single `Background Audio` scene source that plays any of three beds — the SomaFM stream, the license-clean carhum drone, or an album from the mounted music share — replacing the two mutually-exclusive sources with one stripped per platform. `OBS_BACKGROUND_AUDIO` picks the starting bed (`somafm` on twitch, `carhum` elsewhere, as before) and every bed is now available on every platform. ([#70](https://github.com/adanalife/obs/pull/70))

## [v2.7.2] — 2026-07-28

### Fixed

- Pin the VAAPI stream encoder to an explicit H.264 level 4.2. Left on Auto the bitstream signalled level 4.0, which 1080p60 exceeds in either orientation — strict hardware decoders can reject the under-declared stream. ([#68](https://github.com/adanalife/obs/pull/68))

## [v2.7.1] — 2026-07-27

### Fixed

- Stream TikTok at 4000 kbps instead of the high preset's 6000. TikTok's portrait LIVE ingests 6000 kbps cleanly (OBS reports zero congestion) but plays it back black; 4000 renders. Resolution stays 1080p30. ([#65](https://github.com/adanalife/obs/pull/65))

### CI / Tooling

- Collapse the fast per-PR gates (conventional title, changelog fragment, platforms.json contract, cdk8s dist sync) into a single `gates` job in `pr-gates.yml`. Actions bills per job rounded up to the minute, so four short checks cost four minutes; as steps of one job they cost one. ([#67](https://github.com/adanalife/obs/pull/67))

## [v2.7.0] — 2026-07-26

### Changed

- TikTok now streams at 30fps (was 60): TikTok recommends 30fps for non-gaming content and its portrait LIVE is unstable at 60. Framerate is now overridable via `OBS_FPS_COMMON` independent of the quality preset, so TikTok keeps the high preset's 1080p resolution + bitrate at 30fps (a new per-platform `obs_fps` cdk8s override, mirroring `obs_video_bitrate_kbps`). ([#63](https://github.com/adanalife/obs/pull/63))

## [v2.6.0] — 2026-07-25

### Added

- TikTok streaming egress is now enabled on **prod-1** (`obs_streaming`): scaling up prod `obs-tiktok` emits the `obs-tiktok-stream-key` ExternalSecret, so a Streamlabs-minted per-session RTMP server + key (seeded into SSM `k8s/obs/tiktok-stream-{server,key}`, adanalife-prod) takes the prod dashcam live in the 9:16 `Vertical` scene. Mirrors the stage-1 enablement from #59. ([#61](https://github.com/adanalife/obs/pull/61))

## [v2.5.0] — 2026-07-25

### Added

- Portrait canvas support for `tiktok` and `instagram`: these platforms render a generated `Vertical` scene — the whole `Main` scene rotated 90° into a 1080×1920 canvas — so the dashcam stream fits phone-native feeds. `OBS_VERTICAL` forces the mode for testing any platform. ([#57](https://github.com/adanalife/obs/pull/57))
- **TikTok streaming egress.** OBS can now push to TikTok, whose ingest has no built-in OBS service and whose RTMP URL + key are minted per session (via the Streamlabs TikTok API): a new `tiktok` case streams as a raw `rtmp_custom` target, and both the server URL and key ride in the `obs-tiktok-stream-key` secret. Enabled on stage-1 (`obs_streaming`), alongside the existing 9:16 vertical scene. ([#59](https://github.com/adanalife/obs/pull/59))

### Fixed

- **Vertical platforms now actually stream the Vertical scene.** `start-obs.sh` launched OBS with a hardcoded `--scene Main`, which overrode the `current_scene = "Vertical"` the entrypoint sets for tiktok/instagram — so the portrait canvas rendered the landscape Main scene cropped instead of the 90°-rotated Vertical scene. It now selects `Vertical` when `OBS_VERTICAL=true`. ([#60](https://github.com/adanalife/obs/pull/60))

### CI / Tooling

- Harden shared CI workflows: scope token permissions, pin super-linter and setup-uv. ([#55](https://github.com/adanalife/obs/pull/55))

## [v2.4.0] — 2026-07-21

### Changed

- OBS now sources its supported-platform set from platform-gateway's `platforms.json` and synthesizes the full set (instagram/tiktok born parked at `replicas: 0` on prod + stage). ([#53](https://github.com/adanalife/obs/pull/53))

## [v2.3.0] — 2026-07-21

### Changed

- OBS Deployments now birth parked at `replicas: 0` for every platform and env — a platform comes online via the console's per-platform scale-up, which sticks because Argo ignores `.spec.replicas`. Replaces the `parked_platforms`/`manual_replicas` cdk8s knobs (replica count is now runtime-owned). ([#51](https://github.com/adanalife/obs/pull/51))

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
