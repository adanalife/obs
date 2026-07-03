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
- **Car-hum audio beds** (`carhum/`) — license-clean car-interior drones rendered
  at build time, cycled live on the YouTube scene by tripbot's `!carsound`
  command.

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
| `STREAM_PLATFORM` | `twitch` (default) or `youtube` — selects the ingest service and which background-audio source is stripped (`entrypoint.sh`); the youtube cdk8s overlay sets `youtube` |
| `OBS_WEBSOCKET_PASSWD` | obs-websocket auth (tripbot's watchdog connects with it) |
| `OBS_QUALITY_PRESET` | encoder quality preset (`low` on stage) |
| `OBS_STREAM_ENCODER` | encoder selection (e.g. VAAPI vs x264) |
| `DASHCAM_RTSP_URL` | the VLC-served dashcam RTSP source |
| `VLC_URL_BASE` / `ONSCREENS_URL_BASE` | the VLC + onscreens HTTP bases for browser sources |

## The tripbot contract (the one coupling that survives the split)

tripbot's `!carsound` command (`pkg/chatbot/carsound.go`) selects among the FLAC
variants this image bakes into `/opt/tripbot/assets/carhum/`. The variant names
are a **hand-maintained contract** between `carhum/render-variants.sh` here and
the `carSound` list in tripbot. Change the variants in one place → update the
other. (Same shape as the eventbus contracts shared with tripbot-console.)

## Releasing

Standard adanalife `develop → master` flow with towncrier changelog fragments:

1. Feature PRs target `develop`; each adds a fragment
   (`task changelog:add PR=<n> TYPE=<type>`) or carries the `skip-changelog` label.
2. `release-development.yml` floats `ghcr.io/adanalife/obs:develop` on every
   develop push — what stage deploys.
3. To ship: `task changelog:build VERSION=x.y.z` on `develop`, bump the prod pin
   in `cdk8s/versions.yaml` + re-synth, then open the `Release vX.Y.Z` PR
   (`develop → master`, **merge commit**, with a `#patch`/`#minor`/`#major` token).
4. `auto-tag.yml` tags master; `release.yml` builds the multi-arch image to GHCR
   and `backmerge.yml` opens the master→develop back-merge.

The arm64 CEF base (`Dockerfile.arm64-base`) is rebuilt only when it changes, by
`obs-base.yml` (a ~90-min compile) — see that workflow's header.
