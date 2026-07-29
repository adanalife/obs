# obs

The OBS container that streams the [A Dana Life](https://twitch.tv/ADanaLife_)
dashcam slow-TV broadcast. It runs a headless [OBS Studio](https://obsproject.com)
that composites the VLC-served dashcam video plus the onscreen overlays and
pushes the result to Twitch / YouTube.

This image was extracted from the [tripbot](https://github.com/adanalife/tripbot)
monorepo (with full git history) and published as `ghcr.io/adanalife/obs`. The
Go side of the system — the chat bot, the `vlc-server`, the `onscreens-server`,
and the OBS **watchdog/websocket client** (`pkg/obs` in tripbot) — stays in
tripbot. This repo owns only the OBS *image* and its deployment.

## What's in the image

A headless OBS Studio, driven entirely by baked-in config and supervised helper
processes:

- **OBS Studio** — PPA build on amd64 (CEF-bundled, for `browser_source`);
  compiled from source against the aarch64 CEF tarball on arm64.
- **Display stack** — `sway` (headless Wayland compositor) + `wayvnc` + noVNC, so
  OBS's OpenGL composite hits the host iGPU (VAAPI encode) and the desktop is
  reachable in a browser.
- **`supervisor`** manages OBS, sway, wayvnc, noVNC, the hourly browser-source
  refresh (a workaround for CEF's per-frame memory leak), and a small Flask
  `obs-server` exposing `/health/ready`, `/version`, and `POST /admin/shutdown`.
- **Scene/profile templates** (`config/`) rendered at startup from env vars.
- **Background audio beds** — one `Background Audio` scene source, playing one of
  three beds: the SomaFM stream, a license-clean car-interior drone rendered at
  build time (`carhum/`), or an album from the mounted music share. Selected at
  startup by `OBS_BACKGROUND_AUDIO` and switchable live from the admin console.
  The source is audio-only in intent but OBS renders any video its file carries
  (album tracks embed cover art), so its scene item is parked off-canvas —
  hiding it would stop the audio too.

## Layout

| Path | What |
| --- | --- |
| `Dockerfile` | amd64 image (OBS from the obsproject PPA) |
| `Dockerfile.arm64` | arm64 image (OBS from source, via the CEF base below) |
| `Dockerfile.arm64-base` | the arm64 CEF compile base → `ghcr.io/adanalife/obs-cef-base` |
| `config/` | OBS scene + profile templates (`*.tmpl` rendered by `entrypoint.sh`) |
| `script/` | in-image startup scripts (sway, wayvnc, noVNC, obs-server) |
| `scripts/` | repo tooling, not baked into the image (`check-changelog-fragment.sh`, the pre-push changelog guard) — note the near-identical name to `script/` above |
| `supervisor/` | per-process supervisord configs |
| `bin/` | `obs-browser-refresh`, `obs-media-restart` (host/in-image Python helpers) |
| `carhum/` | car-hum FLAC generator (build-time only) |
| `assets/` | Twitch overlay PNGs baked into the image |
| `desktop-profiles/` | reference OBS Studio profiles for local desktop (macOS/Windows) |
| `cdk8s/` | the Kubernetes deployment (synthesized into `cdk8s/dist/`) |

## Build & smoke-test locally

Needs Docker running.

```sh
task image:build          # amd64 image → obs:dev
task image:smoke          # build, run (no STREAM_KEY), wait for the healthcheck
task image:build:arm64    # arm64 image (pulls ghcr.io/adanalife/obs-cef-base)
task carhum:render        # render the FLAC variants into carhum/out/ (numpy/scipy/ffmpeg)
```

## Configuration

The deployment sets these at runtime (see `cdk8s/`); the image runs headless
without them (the healthcheck only needs OBS + the Wayland session up):

| Env var | Purpose |
| --- | --- |
| `STREAM_KEY` | Twitch/YouTube ingest key (per env + platform) |
| `STREAM_PLATFORM` | `twitch` (default), `youtube`, `facebook`, `tiktok`, or `instagram` — selects the ingest service, the default background-audio bed, and the canvas orientation (`tiktok`/`instagram` are portrait); see `entrypoint.sh` |
| `OBS_BACKGROUND_AUDIO` | starting background-audio bed: `somafm`, `carhum`, or `album`. Unset → `somafm` on twitch, `album` on tiktok, `carhum` elsewhere. Only the *starting* bed — tripbot rewrites the source live |
| `OBS_VERTICAL` | force portrait output (`true`/`false`); overrides the per-platform default so any platform can be tested vertical. Portrait renders a generated `Vertical` scene — `Main` rotated 90° CW into a 1080×1920 canvas |
| `OBS_WEBSOCKET_PASSWD` | obs-websocket auth (tripbot's watchdog connects with it) |
| `OBS_QUALITY_PRESET` | encoder quality preset (`low` on stage) |
| `OBS_STREAM_ENCODER` | encoder selection (e.g. VAAPI vs x264) |
| `DASHCAM_RTSP_URL` | the VLC-served dashcam RTSP source |
| `VLC_URL_BASE` / `ONSCREENS_URL_BASE` | the VLC + onscreens HTTP bases for browser sources |

## The tripbot contract (the one coupling that survives the split)

tripbot drives the `Background Audio` source over the OBS WebSocket — the audio
watchdog swaps it to a local bed when SomaFM drops, `!carsound` picks a drone,
and the console's bed selector switches between all three. Three
**hand-maintained contracts** hold that together; change one side → update the
other:

| Contract | Here | In tripbot |
| --- | --- | --- |
| Source name | `Background Audio` in `config/Tripbot.json.tmpl` | `BackgroundAudioInputName` |
| Car-hum variants | `carhum/render-variants.sh` + the Dockerfiles' `COPY` | the `carSound` list in `pkg/chatbot/carsound.go` |
| Bed names + paths | `set_background_audio` in `entrypoint.sh` | the bed registry in `pkg/obs/beds` |

(Same shape as the eventbus contracts shared with tripbot-console.)

## Releasing

Trunk-based `main` + [release-please](https://github.com/googleapis/release-please), with towncrier changelog fragments:

1. Feature PRs target `main` (squash-merge, conventional title); each adds a
   fragment (`task changelog:add TYPE=<type>` — no PR number needed, CI fills it
   in on push) or carries the `skip-changelog` label.
2. `dev-image.yml` floats `ghcr.io/adanalife/obs:main` (amd64) on every main
   push — what stage deploys.
3. `release-please.yml` maintains a standing release PR that bumps the version +
   the prod pin (`cdk8s/versions.yaml`) from the conventional commits, collates
   the `changelog.d/` fragments into `CHANGELOG.md`, and re-synths `cdk8s/dist/`
   on the PR branch.
4. **To ship: squash-merge the release PR.** That tags `vX.Y.Z`, creates the
   GitHub Release, and dispatches `release.yml` to build the multi-arch image to
   GHCR. No manual version/changelog steps — the version follows from the commit
   types (`feat:` → minor, `fix:` → patch, `feat!:`/`BREAKING CHANGE` → major).

The arm64 CEF base (`Dockerfile.arm64-base`) is rebuilt only when it changes, by
`obs-base.yml` (a ~90-min compile) — see that workflow's header.
