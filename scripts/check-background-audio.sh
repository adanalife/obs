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
mkdir -p "$work/music"
touch "$work/music/001 Maine - Atlantic Dawn.mp3"
MUSIC_DIR=$work/music set_background_audio album "$scene" >/dev/null
[[ $(bg '.settings.is_local_file' "$scene") == true ]] || fail "album: is_local_file should be true"
[[ $(bg '.settings.looping' "$scene") == false ]] || fail "album: must not loop (blocks track advance)"
[[ $(bg '.settings.local_file' "$scene") == "$work/music/001 Maine - Atlantic Dawn.mp3" ]] ||
  fail "album: wrong track (paths with spaces must survive)"

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

# Platform defaults — the behaviour the old per-platform source strip had.
[[ $(default_background_audio twitch) == somafm ]] || fail "twitch should default to somafm"
for p in youtube facebook tiktok instagram; do
  [[ $(default_background_audio "$p") == carhum ]] || fail "$p should default to carhum"
done

echo "background-audio: all beds OK"
