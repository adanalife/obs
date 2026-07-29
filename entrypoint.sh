#!/usr/bin/env bash
# Called by Dockerfile{,.arm64} (ENTRYPOINT via tini):
# OBS container entrypoint — seeds OBS config from templates, then hands off
# to supervisor which manages sway/wayvnc/obs/browser-refresh.
#
# Display stack: sway (headless Wayland compositor on /dev/dri/card0) +
# wayvnc (VNC server, attaches to sway) + OBS as a Wayland-native Qt6
# client — so OBS's OpenGL composite hits the iGPU instead of Mesa
# llvmpipe (an Xvfb/X11 stack would land on the CPU rasterizer).
set -euo pipefail

echo "OBS container, tripbot version $(cat /etc/tripbot/version 2>/dev/null || echo dev) (sha: $(cat /etc/tripbot/sha 2>/dev/null || echo unknown))"

OBS_HOME="${HOME:-/root}/.config/obs-studio"
mkdir -p "$OBS_HOME/basic/profiles/ADanaLife" "$OBS_HOME/basic/scenes"

# Expand quality preset into individual encoder params before envsubst.
# OBS_QUALITY_PRESET=low  → 720p30, 1500 kbps (staging / dev laptops)
# OBS_QUALITY_PRESET=high → 1080p60, 6000 kbps (production, Twitch max)
# OBS_FPS_COMMON and OBS_VIDEO_BITRATE take the preset's value unless the
# environment already set one — so a platform can override framerate/bitrate
# independently of the resolution preset (e.g. tiktok runs the high preset's
# 1080p but at 30fps, since its portrait LIVE is unstable at 60).
case "${OBS_QUALITY_PRESET:-high}" in
  low)
    export OBS_OUTPUT_WIDTH=1280
    export OBS_OUTPUT_HEIGHT=720
    export OBS_FPS_COMMON="${OBS_FPS_COMMON:-30}"
    export OBS_VIDEO_BITRATE="${OBS_VIDEO_BITRATE:-1500}"
    export OBS_AUDIO_BITRATE=128
    export OBS_ENCODER_PRESET=ultrafast
    echo "OBS quality preset: low (720p${OBS_FPS_COMMON}, ${OBS_VIDEO_BITRATE} kbps)"
    ;;
  *)
    export OBS_OUTPUT_WIDTH=1920
    export OBS_OUTPUT_HEIGHT=1080
    export OBS_FPS_COMMON="${OBS_FPS_COMMON:-60}"
    export OBS_VIDEO_BITRATE="${OBS_VIDEO_BITRATE:-6000}"
    export OBS_AUDIO_BITRATE=160
    export OBS_ENCODER_PRESET=veryfast
    echo "OBS quality preset: high (1080p${OBS_FPS_COMMON}, ${OBS_VIDEO_BITRATE} kbps)"
    ;;
esac

# Canvas orientation. The scene collection is authored landscape (1920x1080),
# so that's the base canvas for the 16:9 platforms. tiktok and instagram are
# portrait-native, so their instances rotate the whole scene 90° into a
# 1080x1920 canvas (the "Vertical" scene, generated below). OBS_VERTICAL
# overrides the per-platform default — set it to force any platform vertical
# for testing. The output resolution is the preset's dimensions swapped to
# portrait; the base canvas is 1080x1920 so the rotated composite fills it.
case "${STREAM_PLATFORM:-twitch}" in
  tiktok | instagram) export OBS_VERTICAL="${OBS_VERTICAL:-true}" ;;
  *) export OBS_VERTICAL="${OBS_VERTICAL:-false}" ;;
esac
if [[ "${OBS_VERTICAL}" == "true" ]]; then
  landscape_width=$OBS_OUTPUT_WIDTH
  export OBS_OUTPUT_WIDTH=$OBS_OUTPUT_HEIGHT
  export OBS_OUTPUT_HEIGHT=$landscape_width
  export OBS_BASE_WIDTH=1080
  export OBS_BASE_HEIGHT=1920
  echo "OBS orientation: vertical (${OBS_OUTPUT_WIDTH}x${OBS_OUTPUT_HEIGHT} portrait, ${OBS_BASE_WIDTH}x${OBS_BASE_HEIGHT} canvas)"
else
  export OBS_BASE_WIDTH=1920
  export OBS_BASE_HEIGHT=1080
  echo "OBS orientation: landscape (${OBS_OUTPUT_WIDTH}x${OBS_OUTPUT_HEIGHT})"
fi

# Stream encoder selection. Default obs_x264 (software) keeps stage-1 /
# local dev / Mac k3d working without /dev/dri exposed. Override via
# OBS_STREAM_ENCODER=ffmpeg_vaapi_tex (k8s configmap in prod-1) to use
# the host's Intel iGPU for hardware H.264 encode. OBS Simple Output
# mode silently falls back to x264 for VAAPI encoder values, so we run
# in Advanced Output mode (basic.ini.tmpl) and ship a per-encoder
# streamEncoder.json profile below.
#
# Note: Advanced Output's [AdvOut] Encoder field reads the literal
# encoder ID — no friendly-name aliases (unlike Simple Output). The
# x264 plugin registers as "obs_x264", so that's the string we write.
export OBS_STREAM_ENCODER="${OBS_STREAM_ENCODER:-obs_x264}"
echo "OBS stream encoder: ${OBS_STREAM_ENCODER}"

# Streaming target platform. Default `twitch` streams to Twitch
# (service "Twitch", server "auto" — OBS resolves "auto" via Twitch's
# ingest API at connect time). Set
# STREAM_PLATFORM=youtube (k8s configmap in the obs-youtube overlay) to
# point the same canvas/encoder at YouTube's RTMPS ingest. service.json.tmpl
# consumes OBS_STREAM_SERVICE / OBS_STREAM_SERVER via envsubst below.
# Most platforms are OBS built-in services (rtmp_common: OBS resolves the
# ingest by service name). TikTok isn't a built-in service, so it pushes to a
# raw ingest URL (rtmp_custom) with no service lookup.
export OBS_STREAM_TYPE="rtmp_common"
case "${STREAM_PLATFORM:-twitch}" in
  youtube)
    export OBS_STREAM_SERVICE="YouTube - RTMPS"
    export OBS_STREAM_SERVER="rtmps://a.rtmps.youtube.com:443/live2"
    echo "OBS stream platform: youtube (YouTube - RTMPS)"
    ;;
  facebook)
    export OBS_STREAM_SERVICE="Facebook Live"
    export OBS_STREAM_SERVER="rtmps://live-api-s.facebook.com:443/rtmp/"
    echo "OBS stream platform: facebook (Facebook Live RTMPS)"
    ;;
  tiktok)
    # TikTok has no built-in OBS service and mints its ingest URL + key together
    # per session (via the Streamlabs TikTok API), so both arrive through the
    # stream-key secret: STREAM_KEY plus OBS_STREAM_SERVER. Push as a raw custom
    # RTMP target and leave the service name unset.
    export OBS_STREAM_TYPE="rtmp_custom"
    export OBS_STREAM_SERVICE=""
    export OBS_STREAM_SERVER="${OBS_STREAM_SERVER:-}" # seeded with the key; empty when parked
    echo "OBS stream platform: tiktok (custom RTMP, per-session ingest)"
    ;;
  *)
    export OBS_STREAM_SERVICE="Twitch"
    export OBS_STREAM_SERVER="auto"
    echo "OBS stream platform: twitch (server auto)"
    ;;
esac

# OBS 32 split the legacy global.ini in two: app-level settings stayed in
# global.ini (BrowserHWAccel etc.) and user-preference settings moved to
# user.ini. Seed both so OBS sees a complete config and never prompts about
# migration.
cp /opt/obs/config/global.ini "$OBS_HOME/global.ini"
cp /opt/obs/config/user.ini   "$OBS_HOME/user.ini"
envsubst < /opt/obs/config/basic.ini.tmpl > "$OBS_HOME/basic/profiles/ADanaLife/basic.ini"
envsubst < /opt/obs/config/Tripbot.json.tmpl > "$OBS_HOME/basic/scenes/Tripbot.json"

scene_file="$OBS_HOME/basic/scenes/Tripbot.json"

# Portrait platforms get a generated "Vertical" scene: the whole "Main" scene
# rotated 90° clockwise into the 1080x1920 canvas. A point (x,y) on the
# 1920x1080 landscape maps to (1080 - y, x), and every item gains 90° of its
# own rotation. Groups rotate as one unit (their members ride the group's
# transform), so only the scene-level items are transformed — the group
# definitions and all sources are shared with Main untouched. Generating from
# Main (rather than hand-authoring a second scene) keeps the two in lockstep as
# Main evolves. Viewers are prompted to tilt their phone; TikTok can also flag
# the live as landscape to nudge the same.
if [[ "${OBS_VERTICAL}" == "true" ]]; then
  jq '
    (.sources | map(select(.name == "Main")) | .[0]) as $main
    | ($main.settings.items | map(
        .pos = { "x": (1080 - .pos.y), "y": .pos.x }
        | .rot = (((.rot // 0) + 90) % 360)
      )) as $vitems
    | .sources += [ ($main | .name = "Vertical" | .settings.items = $vitems) ]
    | .scene_order += [ { "name": "Vertical" } ]
    | .current_scene = "Vertical"
    | .current_program_scene = "Vertical"
  ' "$scene_file" > "$scene_file.tmp" && mv "$scene_file.tmp" "$scene_file"
  echo "generated portrait 'Vertical' scene (Main rotated 90° CW)"
fi

# Background audio: write the starting bed onto the single "Background Audio"
# source. The bed defaults per platform (SomaFM on Twitch, the licensed album on
# TikTok, the license-clean carhum drone everywhere else) and
# cdk8s overrides it per (env, platform) with OBS_BACKGROUND_AUDIO. Any bed runs
# on any platform, and the console switches it live over the WebSocket, so this
# only picks where the stream starts.
# shellcheck source=script/background-audio.sh
source /opt/obs/script/background-audio.sh
set_background_audio \
  "${OBS_BACKGROUND_AUDIO:-$(default_background_audio "${STREAM_PLATFORM:-twitch}")}" \
  "$scene_file"

# Advanced Output mode reads encoder-specific settings from streamEncoder.json
# in the profile dir. VAAPI's keys (vaapi_device, integer profile) don't
# overlap with x264's, so we case on OBS_STREAM_ENCODER to ship the right
# shape. Keep Twitch-friendly defaults: CBR, 2s keyframe interval, no B-frames.
case "${OBS_STREAM_ENCODER}" in
  ffmpeg_vaapi_tex)
    # profile=100 == AV_PROFILE_H264_HIGH (libavcodec). vaapi_device picks the
    # iGPU's renderD128 node — the only DRM node the Intel device plugin
    # exposes inside the pod.
    #
    # level=42 == H.264 level 4.2 (OBS takes the level as its level_idc
    # integer). Both orientations run 1080p60 — 1920x1080 and 1080x1920 are
    # 8160 macroblocks, 489,600 MB/s at 60fps — which is well past level 4.0's
    # 245,760 MB/s ceiling and inside 4.2's 522,240. Left on Auto the driver
    # signals 4.0, an under-declaration strict hardware decoders can reject.
    cat > "$OBS_HOME/basic/profiles/ADanaLife/streamEncoder.json" <<EOF
{
    "bf": 0,
    "bitrate": ${OBS_VIDEO_BITRATE},
    "keyint_sec": 2,
    "level": 42,
    "profile": 100,
    "rate_control": "CBR",
    "vaapi_device": "/dev/dri/renderD128"
}
EOF
    ;;
  *)
    # x264 (and any other software encoders) — profile is a string here.
    cat > "$OBS_HOME/basic/profiles/ADanaLife/streamEncoder.json" <<EOF
{
    "bitrate": ${OBS_VIDEO_BITRATE},
    "keyint_sec": 2,
    "preset": "${OBS_ENCODER_PRESET}",
    "profile": "high",
    "rate_control": "CBR"
}
EOF
    ;;
esac

mkdir -p "$OBS_HOME/plugin_config/obs-websocket"
envsubst < /opt/obs/config/obs-websocket.json.tmpl > "$OBS_HOME/plugin_config/obs-websocket/config.json"

# Render service.json only when STREAM_KEY is set. start-obs.sh keys off
# this file's existence to decide whether to pass --startstreaming.
if [[ -n "${STREAM_KEY:-}" && ( "$OBS_STREAM_TYPE" != "rtmp_custom" || -n "${OBS_STREAM_SERVER:-}" ) ]]; then
  echo "STREAM_KEY set; configuring ${OBS_STREAM_SERVICE:-$OBS_STREAM_SERVER} and starting stream."
  envsubst < /opt/obs/config/service.json.tmpl \
    > "$OBS_HOME/basic/profiles/ADanaLife/service.json"
elif [[ -n "${STREAM_KEY:-}" ]]; then
  # Custom-RTMP platform (tiktok) with a key but no ingest URL yet — the
  # per-session server hasn't been seeded. Start idle rather than push to a
  # bad target.
  echo "STREAM_KEY set but OBS_STREAM_SERVER empty for custom RTMP (${STREAM_PLATFORM}); OBS will start idle." >&2
  rm -f "$OBS_HOME/basic/profiles/ADanaLife/service.json"
else
  echo "STREAM_KEY not set; OBS will start idle. VNC into :5900 to inspect."
  rm -f "$OBS_HOME/basic/profiles/ADanaLife/service.json"
fi

# Shared Wayland runtime dir. start-sway.sh creates it with 0700; export
# it here so any debugging exec'd in the container picks it up too.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/runtime-root}"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR"

# Render the wayvnc cfg into the per-pod tmpfs runtime dir. The template has
# no env vars to substitute — auth is off (wayvnc offers RFB "None" inside
# the pod; access control lives at the traefik Ingress in front of noVNC) —
# so envsubst is a passthrough copy, kept so the cfg lands at the same spot
# as the rest of the per-pod runtime config.
envsubst < /opt/obs/config/wayvnc.cfg.tmpl > "$XDG_RUNTIME_DIR/wayvnc.cfg"

# Hand off to supervisord. It manages sway, wayvnc, obs, noVNC/websockify,
# obs-server, and the hourly browser-source refresh (with each program's
# start order + Wayland-socket dependency handled in script/start-*.sh).
exec supervisord -n -c /etc/supervisor/supervisord.conf
