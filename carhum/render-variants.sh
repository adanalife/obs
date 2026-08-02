#!/usr/bin/env bash
# Render the car-sound variant set to seamless-looping FLACs.
#
# Runs at Docker BUILD time (the obs image's `carhum` builder stage), never at
# runtime — generating in a throwaway stage keeps numpy/scipy out of the final
# image while still shipping procedurally-generated (not git-committed) audio.
# Needs python3 with numpy+scipy importable and ffmpeg on PATH.
#
# The file NAMES here are a contract with tripbot: `beds.CarHumFile` selects the
# idle drone and `beds.FallbackFile` the watchdog's copy of it, both by absolute
# path inside the OBS container. Renaming either one here silences the bed.
# Nothing selects the highway/backroad/mountain voicings.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="${1:?usage: render-variants.sh <out-dir>}"
mkdir -p "$out"

DURATION=240   # 4-minute bed
LOOP=6         # crossfade seconds -> seam-free loop (verified by splice test)

# "<preset-name>:<seed>" — the preset name doubles as the variant name; seeds
# are fixed so the image build is reproducible.
variants="idle:5 highway:2 backroad:7 mountain:9"

for v in $variants; do
  name="${v%%:*}"
  seed="${v##*:}"
  wav="$out/$name.wav"
  flac="$out/car-hum-$name.flac"
  echo ">> rendering $name (seed $seed)"
  python3 "$here/carhum.py" --preset "$name" --duration "$DURATION" \
    --loop "$LOOP" --seed "$seed" --out "$wav"
  # FLAC is gapless (no encoder padding), so the seam-free loop survives encode.
  ffmpeg -nostdin -loglevel error -y -i "$wav" -compression_level 8 "$flac"
  rm -f "$wav"
  echo "   -> $flac"
done

# The audio watchdog's fallback bed: the idle drone under a second name.
#
# Identical audio, deliberately distinct path. The OBS background-audio source
# records only what file it is playing, so that path is the only place able to
# distinguish "an operator selected Car Hum" from "the audio watchdog fell back
# to it during a SomaFM outage". Without the distinction a tripbot restart
# mid-outage reads the fallback back as a chosen Car Hum bed and never returns
# to SomaFM. A copy rather than a symlink because the COPY out of this stage
# globs files, and a dangling link in the final image is silence.
cp "$out/car-hum-idle.flac" "$out/car-hum-fallback.flac"
echo "   -> $out/car-hum-fallback.flac (copy of idle)"

echo "done: $(find "$out" -name '*.flac' | wc -l | tr -d ' ') files in $out"
