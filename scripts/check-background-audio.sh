#!/usr/bin/env bash
# Asserts script/background-audio.sh writes the right settings onto the real
# scene template for every bed. Needs only bash + jq — no image build, no OBS.
#
#   scripts/check-background-audio.sh
set -euo pipefail

cd "$(dirname "$0")/.."
source script/background-audio.sh

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# The template carries ${VARS} envsubst fills at boot; none of them are inside
# the Background Audio source, so a placeholder pass is enough to get valid JSON.
render_scene() {
  sed 's/\${[A-Za-z_][A-Za-z0-9_]*}/0/g' config/Tripbot.json.tmpl > "$1"
}

fail() { echo "FAIL: $*" >&2; exit 1; }

# Reads one field off the Background Audio source of a rendered scene.
bg() { jq -r --arg n "$BACKGROUND_AUDIO_INPUT" \
  ".sources[] | select(.name == \$n) | $1" "$2"; }

# The scene must ship exactly one Background Audio source, and neither of the
# two sources it replaced — a leftover would double up on the mix.
scene=$work/base.json
render_scene "$scene"
[[ $(jq --arg n "$BACKGROUND_AUDIO_INPUT" \
  '[.sources[] | select(.name == $n)] | length' "$scene") == 1 ]] ||
  fail "expected exactly one '$BACKGROUND_AUDIO_INPUT' source in the scene"
for gone in "Groove Salad Classic" "Car Hum"; do
  [[ $(jq --arg n "$gone" '[.. | objects | select(.name? == $n)] | length' "$scene") == 0 ]] ||
    fail "'$gone' still referenced in the scene (source or scene item)"
done

# The bed is audio-only, but a media source renders whatever video its file
# carries — and every album track has a 360x360 embedded cover art frame, which
# OBS drew over the dashcam. Hiding the scene item stops the audio with it, so
# the item stays visible and parked past the far corner of both canvases
# (1920x1080 landscape, 1080x1920 portrait, which the Vertical scene derives by
# mapping (x,y) -> (1080-y, x)). Both coordinates past 1920 is off-canvas either
# way round.
item() { jq -r --arg n "$BACKGROUND_AUDIO_INPUT" \
  '.sources[] | select(.name == "Main") | .settings.items[] | select(.name == $n) | '"$1" "$2"; }
[[ $(item '.visible' "$scene") == true ]] ||
  fail "background audio item must stay visible (hidden means silent)"
(( $(item '.pos.x' "$scene") > 1920 && $(item '.pos.y' "$scene") > 1920 )) ||
  fail "background audio item is on-canvas — its cover art will render on the stream"

# somafm — a network stream: no local file, and it must keep the reconnect
# settings that let it ride out a SomaFM edge blip.
scene=$work/somafm.json
render_scene "$scene"
set_background_audio somafm "$scene" >/dev/null
[[ $(bg '.settings.is_local_file' "$scene") == false ]] || fail "somafm: is_local_file should be false"
[[ $(bg '.settings.input' "$scene") == https://ice4.somafm.com/* ]] || fail "somafm: wrong input url"
[[ $(bg '.settings.reconnect_delay_sec' "$scene") == 10 ]] || fail "somafm: lost reconnect_delay_sec"

# carhum — a local file that must LOOP; a drone that stops leaves dead air.
scene=$work/carhum.json
render_scene "$scene"
CARHUM_BED=/opt/tripbot/assets/carhum/car-hum-idle.flac \
  set_background_audio carhum "$scene" >/dev/null
[[ $(bg '.settings.is_local_file' "$scene") == true ]] || fail "carhum: is_local_file should be true"
[[ $(bg '.settings.looping' "$scene") == true ]] || fail "carhum: must loop"
[[ $(bg '.settings.local_file' "$scene") == *car-hum-idle.flac ]] || fail "carhum: wrong file"

# album — picks a track off the share, and must NOT loop: tripbot advances to
# the next track when OBS reports the media ended, which never fires on a loop.
scene=$work/album.json
render_scene "$scene"
mkdir -p "$work/music/fifty-horizons"
touch "$work/music/001 Maine - Atlantic Dawn.mp3"  # decoy: see the root-file check below
touch "$work/music/fifty-horizons/001 Maine - Atlantic Dawn.mp3"
MUSIC_DIR=$work/music set_background_audio album "$scene" >/dev/null
[[ $(bg '.settings.is_local_file' "$scene") == true ]] || fail "album: is_local_file should be true"
[[ $(bg '.settings.looping' "$scene") == false ]] || fail "album: must not loop (blocks track advance)"
[[ $(bg '.settings.local_file' "$scene") == "$work/music/fifty-horizons/001 Maine - Atlantic Dawn.mp3" ]] ||
  fail "album: wrong track (paths with spaces must survive)"

# Loose files at the share root are not tracks. The real share keeps a 556MB
# carsounds.m4a next to the album directories; picking it would put a
# nine-hour file on the stream and stall the rotation.
scene=$work/album-root-only.json
render_scene "$scene"
mkdir -p "$work/rootonly"
touch "$work/rootonly/carsounds.m4a"
MUSIC_DIR=$work/rootonly set_background_audio album "$scene" 2>/dev/null >/dev/null
[[ $(bg '.settings.local_file' "$scene") == *car-hum* ]] ||
  fail "album: a loose file at the share root must not be treated as a track"

# album with no share mounted (dev/local) — degrades to the looping carhum bed
# rather than going silent.
scene=$work/album-empty.json
render_scene "$scene"
MUSIC_DIR=$work/nonexistent set_background_audio album "$scene" 2>/dev/null >/dev/null
[[ $(bg '.settings.looping' "$scene") == true ]] || fail "empty share: should fall back to carhum"
[[ $(bg '.settings.local_file' "$scene") == *car-hum* ]] || fail "empty share: should fall back to carhum"

# An unknown bed must fail loudly at boot, not silently leave the source blank.
scene=$work/bogus.json
render_scene "$scene"
if set_background_audio wurlitzer "$scene" 2>/dev/null; then
  fail "unknown bed should be rejected"
fi

# Platform defaults. Twitch is the one platform SomaFM is tolerated on; every
# other platform's audio ID would strike it, so they start on the album.
[[ $(default_background_audio twitch) == somafm ]] || fail "twitch should default to somafm"
for p in youtube facebook tiktok instagram; do
  [[ $(default_background_audio "$p") == album ]] || fail "$p should default to album"
done

echo "background-audio: all beds OK"
