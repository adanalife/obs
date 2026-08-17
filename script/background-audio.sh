#!/usr/bin/env bash
# Background-audio bed selection, sourced by entrypoint.sh (and exercised
# directly by scripts/check-background-audio.sh).
#
# The scene ships ONE "Background Audio" ffmpeg_source and this writes the
# chosen bed's settings onto it. Three beds exist:
#
#   - somafm — the "Groove Salad Classic" internet-radio stream. Its music is
#     NOT cleared for our rebroadcast: it reliably trips YouTube's Content ID
#     and earns copyright strikes there, and Meta's Rights Manager and TikTok's
#     / Instagram's audio ID are just as strike-happy. Twitch hasn't struck it
#     so far, so it's the Twitch default — but that's empirical tolerance, not
#     a license, and could draw a DMCA claim at any time.
#   - carhum — a locally-generated, license-clean drone (see carhum/), baked
#     into the image. The safe bed everywhere SomaFM can't go, and what the
#     audio watchdog falls back to when SomaFM drops.
#   - album — a licensed album on the read-only music share, mounted from the
#     `obs-music` PVC (the infra repo owns the volume). tripbot shuffles and
#     advances tracks over the WebSocket once it connects.
#
# Only the STARTING bed is chosen here. Any bed can play on any platform, and
# the choice is live-switchable from the admin console (tripbot rewrites this
# same source's settings over the OBS WebSocket) — a default, not a policy.

# The OBS source name. Cross-repo contract with tripbot's
# BackgroundAudioInputName; must match the source "name" in Tripbot.json.tmpl.
BACKGROUND_AUDIO_INPUT="Background Audio"

MUSIC_DIR="${MUSIC_DIR:-/opt/tripbot/assets/music}"
CARHUM_BED="${CARHUM_BED:-/opt/tripbot/assets/carhum/car-hum-idle.flac}"

# Albums are subdirectories of the share; loose files at its root are NOT tracks.
# The share holds other audio alongside the albums (carsounds.m4a, a 556MB
# archive), and a flat scan would shuffle that into the rotation as one enormous
# "track". -mindepth 2 is the whole rule.
#
# Any track under any album is a valid answer, because this only decides which
# album the stream boots into: tripbot reads the playing file back off the source
# on connect, takes the album from its path, and builds the play order from that
# album alone. Picking across the whole share can't interleave albums.
#
# `|| true` because callers run under `set -euo pipefail` and an unmounted share
# makes find exit non-zero — an empty result is a valid answer, not a failure.
random_album_track() {
  find "$MUSIC_DIR" -mindepth 2 -type f \
    \( -name '*.mp3' -o -name '*.flac' -o -name '*.m4a' -o -name '*.ogg' \) 2>/dev/null |
    shuf -n1 || true
}

# set_background_audio <bed> <scene-file>
#
# Each bed needs a different source shape (a network stream buffers and
# reconnects; a local file loops) and a different level — SomaFM's 128k mix is
# mastered far hotter than the carhum drone, so one shared volume would leave
# whichever bed it wasn't tuned for wrong.
set_background_audio() {
  local bed="$1" scene_file="$2" settings volume
  case "$bed" in
    somafm)
      settings='{"input":"https://ice4.somafm.com/gsclassic-128-mp3","is_local_file":false,"reconnect_delay_sec":10,"buffering_mb":8}'
      volume=0.121619
      ;;
    album)
      local track
      track=$(random_album_track)
      if [[ -z "$track" ]]; then
        # The share is empty or unmounted (dev/local runs have no PVC). Silence
        # reads as a broken stream, so fall back to the image-baked bed.
        echo "background audio: no tracks under $MUSIC_DIR — falling back to carhum" >&2
        set_background_audio carhum "$scene_file"
        return
      fi
      # looping=false so the track ENDS: tripbot watches for the media-ended
      # state and points the source at the next track. clear_on_media_end stops
      # OBS holding the final buffer if tripbot is down.
      settings=$(jq -nc --arg f "$track" \
        '{local_file:$f,is_local_file:true,looping:false,clear_on_media_end:true,buffering_mb:2}')
      volume=0.2
      ;;
    carhum)
      settings=$(jq -nc --arg f "$CARHUM_BED" \
        '{local_file:$f,is_local_file:true,looping:true,buffering_mb:2}')
      volume=0.2
      ;;
    *)
      echo "unknown background audio bed '$bed' (want somafm|carhum|album)" >&2
      return 1
      ;;
  esac
  jq --arg n "$BACKGROUND_AUDIO_INPUT" --argjson s "$settings" --argjson v "$volume" '
    .sources |= map(
      if .name == $n
      then .settings += $s | .volume = $v
      else . end)
  ' "$scene_file" > "$scene_file.tmp" && mv "$scene_file.tmp" "$scene_file"
  echo "background audio: ${bed}"
}

# default_background_audio <platform>
#
# Where each platform starts when OBS_BACKGROUND_AUDIO is unset. Everywhere
# SomaFM can't go gets the licensed album: it survives every platform's audio ID
# the way the drone does, and a slow-tv stream carrying music reads as a channel
# rather than as something broken. Twitch keeps SomaFM, on the empirical
# tolerance described above. The drone is still the safety net rather than a
# default — set_background_audio falls back to it when the share has no tracks,
# which is how the envs without the music PVC (development, local) boot.
default_background_audio() {
  case "${1:-twitch}" in
    twitch) echo somafm ;;
    *) echo album ;;
  esac
}
