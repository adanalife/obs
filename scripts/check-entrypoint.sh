#!/usr/bin/env bash
# Asserts entrypoint.sh renders the right OBS config for each (preset,
# platform, encoder) combination, against the real templates in config/.
# Needs only bash + jq + envsubst — no image build, no OBS.
#
#   scripts/check-entrypoint.sh
#
# Every assertion here is something a pod restart would otherwise discover on
# air: a canvas the wrong way round, a stream pointed at the wrong ingest, a
# VAAPI profile OBS silently drops back to x264, or a tiktok instance pushing
# to an ingest URL it was never given.
set -euo pipefail

cd "$(dirname "$0")/.."
repo=$PWD

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# entrypoint.sh ends in `exec supervisord`. Stub it so the boot sequence runs
# to completion and returns, leaving the rendered config behind to inspect.
mkdir -p "$work/bin"
printf '#!/bin/sh\nexit 0\n' > "$work/bin/supervisord"
chmod +x "$work/bin/supervisord"

fail() { echo "FAIL: $*" >&2; exit 1; }

# Runs a boot with the given environment and echoes the resulting OBS config
# root. Each case gets its own HOME so nothing leaks between them.
#
#   boot <case-name> [VAR=value ...]
boot() {
  local name=$1; shift
  local home="$work/$name"
  mkdir -p "$home"
  if ! env -i \
    PATH="$work/bin:$PATH" \
    HOME="$home" \
    XDG_RUNTIME_DIR="$home/runtime" \
    OBS_ASSETS="$repo" \
    "$@" \
    bash "$repo/entrypoint.sh" > "$home/boot.log" 2>&1; then
    cat "$home/boot.log" >&2
    fail "$name: entrypoint exited non-zero"
  fi
  echo "$home/.config/obs-studio"
}

profile() { echo "$1/basic/profiles/ADanaLife"; }

# Reads a key out of the rendered basic.ini. The file is INI with [sections],
# and the video keys are unique across sections, so a plain match is enough.
ini() { sed -n "s/^$2=//p" "$(profile "$1")/basic.ini" | head -1; }

# --- Canvas: preset x orientation ------------------------------------------
# The base canvas is what the scene is authored against; the output is what
# gets encoded. Landscape keeps them equal at the preset's size; portrait
# swaps the output and pins the base to 1080x1920 so the rotated composite
# fills it. Getting these out of step is how a stream goes out letterboxed.
scene_root=$(boot twitch-high STREAM_PLATFORM=twitch)
[[ $(ini "$scene_root" BaseCX) == 1920 && $(ini "$scene_root" BaseCY) == 1080 ]] ||
  fail "twitch/high: base canvas should be 1920x1080"
[[ $(ini "$scene_root" OutputCX) == 1920 && $(ini "$scene_root" OutputCY) == 1080 ]] ||
  fail "twitch/high: output should be 1920x1080"
[[ $(ini "$scene_root" FPSCommon) == 60 ]] || fail "twitch/high: should be 60fps"

scene_root=$(boot twitch-low STREAM_PLATFORM=twitch OBS_QUALITY_PRESET=low)
[[ $(ini "$scene_root" OutputCX) == 1280 && $(ini "$scene_root" OutputCY) == 720 ]] ||
  fail "twitch/low: output should be 1280x720"
[[ $(ini "$scene_root" FPSCommon) == 30 ]] || fail "twitch/low: should be 30fps"

# tiktok and instagram are portrait-native, so they rotate without being asked.
for p in tiktok instagram; do
  scene_root=$(boot "$p-portrait" STREAM_PLATFORM="$p")
  [[ $(ini "$scene_root" BaseCX) == 1080 && $(ini "$scene_root" BaseCY) == 1920 ]] ||
    fail "$p: base canvas should be the 1080x1920 portrait canvas"
  [[ $(ini "$scene_root" OutputCX) == 1080 && $(ini "$scene_root" OutputCY) == 1920 ]] ||
    fail "$p: output should be portrait"
done

# A landscape platform forced vertical rotates too — the lever exists for
# testing a portrait canvas without a portrait platform.
scene_root=$(boot twitch-forced-vertical STREAM_PLATFORM=twitch OBS_VERTICAL=true)
[[ $(ini "$scene_root" OutputCY) == 1920 ]] || fail "OBS_VERTICAL=true should rotate any platform"

# The framerate override is independent of the resolution preset: tiktok runs
# the high preset's 1080p at 30fps because its portrait LIVE is unstable at 60.
scene_root=$(boot tiktok-30 STREAM_PLATFORM=tiktok OBS_FPS_COMMON=30)
[[ $(ini "$scene_root" FPSCommon) == 30 ]] || fail "OBS_FPS_COMMON should override the preset"
[[ $(ini "$scene_root" OutputCX) == 1080 ]] || fail "an fps override must not change the resolution"

# --- The generated Vertical scene ------------------------------------------
# Portrait platforms get "Main" rotated 90° CW into a second scene, and OBS
# must boot onto it — a portrait canvas showing the landscape scene is a
# pillarboxed stream.
scene_root=$(boot tiktok-scene STREAM_PLATFORM=tiktok)
scene=$scene_root/basic/scenes/Tripbot.json
[[ $(jq -r '.current_program_scene' "$scene") == Vertical ]] ||
  fail "portrait: should boot onto the Vertical scene"
[[ $(jq '[.sources[] | select(.name == "Vertical")] | length' "$scene") == 1 ]] ||
  fail "portrait: expected exactly one generated Vertical scene"
# (x,y) -> (1080-y, x), and every item gains 90° of its own rotation. Checked
# against Main so the mapping is pinned to the real authored coordinates
# rather than to a number copied out of this file.
jq -e '
  (.sources[] | select(.name == "Main") | .settings.items) as $m
  | (.sources[] | select(.name == "Vertical") | .settings.items) as $v
  | ($m | length) == ($v | length)
    and ([range(0; $m | length)] | all(
      . as $i
      | ($v[$i].pos.x == 1080 - $m[$i].pos.y)
        and ($v[$i].pos.y == $m[$i].pos.x)
        and ($v[$i].rot == ((($m[$i].rot // 0) + 90) % 360))
    ))
' "$scene" >/dev/null || fail "portrait: Vertical items are not Main rotated 90° CW"

# Landscape platforms must NOT get one — a stray Vertical scene in the
# collection is one misclick from going out sideways.
scene_root=$(boot twitch-scene STREAM_PLATFORM=twitch)
scene=$scene_root/basic/scenes/Tripbot.json
[[ $(jq '[.sources[] | select(.name == "Vertical")] | length' "$scene") == 0 ]] ||
  fail "landscape: should not generate a Vertical scene"
[[ $(jq -r '.current_program_scene' "$scene") == Main ]] ||
  fail "landscape: should boot onto Main"

# --- Stream target ----------------------------------------------------------
# service.json is only rendered when there is somewhere real to push to;
# start-obs.sh keys off its existence to decide whether to pass
# --startstreaming, so its absence is what keeps a keyless pod parked.
svc() { jq -r "$2" "$(profile "$1")/service.json"; }

scene_root=$(boot twitch-keyed STREAM_PLATFORM=twitch STREAM_KEY=live_abc)
[[ $(svc "$scene_root" .type) == rtmp_common ]] || fail "twitch: should be a built-in service"
[[ $(svc "$scene_root" .settings.service) == Twitch ]] || fail "twitch: wrong service name"
[[ $(svc "$scene_root" .settings.server) == auto ]] || fail "twitch: server should be auto"
[[ $(svc "$scene_root" .settings.key) == live_abc ]] || fail "twitch: key not substituted"

scene_root=$(boot youtube-keyed STREAM_PLATFORM=youtube STREAM_KEY=yt_abc)
[[ $(svc "$scene_root" .settings.service) == "YouTube - RTMPS" ]] || fail "youtube: wrong service name"
[[ $(svc "$scene_root" .settings.server) == rtmps://a.rtmps.youtube.com:443/live2 ]] ||
  fail "youtube: wrong ingest"

scene_root=$(boot facebook-keyed STREAM_PLATFORM=facebook STREAM_KEY=fb_abc)
[[ $(svc "$scene_root" .settings.service) == "Facebook Live" ]] || fail "facebook: wrong service name"

# tiktok has no built-in OBS service: it pushes to a per-session ingest URL
# that arrives alongside the key, as raw custom RTMP with no service lookup.
scene_root=$(boot tiktok-keyed STREAM_PLATFORM=tiktok STREAM_KEY=tt_abc \
  OBS_STREAM_SERVER=rtmp://ingest.tiktok.example/live)
[[ $(svc "$scene_root" .type) == rtmp_custom ]] || fail "tiktok: should be custom RTMP"
[[ $(svc "$scene_root" .settings.server) == rtmp://ingest.tiktok.example/live ]] ||
  fail "tiktok: wrong per-session ingest"

# A tiktok key with no ingest URL yet means the session hasn't been minted.
# Starting idle is right; pushing to an empty target is not.
scene_root=$(boot tiktok-parked STREAM_PLATFORM=tiktok STREAM_KEY=tt_abc)
[[ ! -e "$(profile "$scene_root")/service.json" ]] ||
  fail "tiktok with no ingest URL should start idle, not render a service"

# No key at all — the stage-1 / local-dev shape.
scene_root=$(boot twitch-keyless STREAM_PLATFORM=twitch)
[[ ! -e "$(profile "$scene_root")/service.json" ]] ||
  fail "no STREAM_KEY should leave OBS parked"

# --- Encoder profile --------------------------------------------------------
# Advanced Output reads encoder settings out of streamEncoder.json, and the
# two encoders take disjoint key sets. Shipping x264's shape to VAAPI is how a
# hardware encode silently becomes a CPU one.
enc() { jq -r "$2" "$(profile "$1")/streamEncoder.json"; }

scene_root=$(boot x264 STREAM_PLATFORM=twitch)
[[ $(ini "$scene_root" Encoder) == obs_x264 ]] || fail "x264: basic.ini should name obs_x264"
[[ $(enc "$scene_root" .profile) == high ]] || fail "x264: profile is a string"
[[ $(enc "$scene_root" .preset) == veryfast ]] || fail "x264: high preset should be veryfast"
[[ $(enc "$scene_root" .vaapi_device) == null ]] || fail "x264: should carry no VAAPI keys"

scene_root=$(boot x264-low STREAM_PLATFORM=twitch OBS_QUALITY_PRESET=low)
[[ $(enc "$scene_root" .preset) == ultrafast ]] || fail "x264: low preset should be ultrafast"
[[ $(enc "$scene_root" .bitrate) == 1500 ]] || fail "x264: low preset bitrate"

scene_root=$(boot vaapi STREAM_PLATFORM=twitch OBS_STREAM_ENCODER=ffmpeg_vaapi_tex)
[[ $(ini "$scene_root" Encoder) == ffmpeg_vaapi_tex ]] || fail "vaapi: basic.ini should name the VAAPI encoder"
[[ $(enc "$scene_root" .profile) == 100 ]] || fail "vaapi: profile is the integer AV_PROFILE_H264_HIGH"
[[ $(enc "$scene_root" .vaapi_device) == /dev/dri/renderD128 ]] || fail "vaapi: wrong DRM node"
# Both orientations run 1080p60 = 489,600 MB/s, past level 4.0's 245,760
# ceiling. Left on Auto the driver signals 4.0, which strict hardware decoders
# reject — so the level has to be declared, and 42 is the one that fits.
[[ $(enc "$scene_root" .level) == 42 ]] || fail "vaapi: must declare H.264 level 4.2"
[[ $(enc "$scene_root" .bf) == 0 ]] || fail "vaapi: B-frames must stay off"

# Twitch-friendly defaults hold across both encoders.
for case_dir in x264 vaapi; do
  [[ $(enc "$work/$case_dir/.config/obs-studio" .rate_control) == CBR ]] || fail "$case_dir: should be CBR"
  [[ $(enc "$work/$case_dir/.config/obs-studio" .keyint_sec) == 2 ]] || fail "$case_dir: 2s keyframe interval"
done

# --- Per-pod runtime config -------------------------------------------------
# The websocket config is what the console and tripbot connect through; a pod
# that renders it wrong is reachable by nobody.
scene_root=$(boot websocket STREAM_PLATFORM=twitch)
jq -e . "$scene_root/plugin_config/obs-websocket/config.json" >/dev/null ||
  fail "obs-websocket config is not valid JSON"

echo "entrypoint: all boot paths OK"
