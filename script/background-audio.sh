#!/usr/bin/env bash
# Background-audio bed selection, sourced by entrypoint.sh (and exercised
# directly by scripts/check-background-audio.sh).
#
# The scene ships ONE "Background Audio" ffmpeg_source and this writes the
# chosen starting bed's settings onto it. Two beds can start a stream:
#
#   - carhum — a locally-generated, license-clean drone (see carhum/), baked
#     into the image. The safe bed everywhere else, and what the audio watchdog
#     falls back to when the playing bed goes silent.
#   - album — a licensed album on the read-only music share, mounted from the
#     `obs-music-local` PVC (the infra repo owns the volume). tripbot shuffles
#     and advances tracks over the WebSocket once it connects.
#
# Only the STARTING bed is chosen here. The choice is live-switchable from the
# admin console (tripbot rewrites this same source's settings over the OBS
# WebSocket), and that is also the only way onto the internet-radio bed: its
# station URLs live in tripbot's private config, never in this image.

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
    \( -name '*.mp3' -o -name '*.flac' -o -name '*.m4a' -o -name '*.ogg' -o -name '*.opus' \) 2>/dev/null |
    shuf -n1 || true
}

# set_background_audio <bed> <scene-file>
#
# Each bed needs a different source shape (the drone loops; an album track ends
# so tripbot can advance it).
set_background_audio() {
  local bed="$1" scene_file="$2" settings volume
  case "$bed" in
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
      echo "unknown background audio bed '$bed' (want carhum|album)" >&2
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

# default_background_audio
#
# Where every platform starts when OBS_BACKGROUND_AUDIO is unset: the licensed
# album, which survives every platform's audio ID the way the drone does, and a
# slow-tv stream carrying music reads as a channel rather than as something
# broken. The drone is the safety net rather than a default — set_background_audio falls
# back to it when the share has no tracks, which is how the envs without the
# music PVC (development, local) boot.
default_background_audio() {
  echo album
}
