# carhum — synthesized car-interior drone

`carhum.py` generates a calming car-interior ambience (road roar + faint engine
hum + cabin air) entirely from filtered noise and a few low sine harmonics. It's
100% synthesized, so it carries **no licensing risk** — unlike a ripped
recording, it can't earn a YouTube Content ID claim or copyright strike.

It's the source of the "Car Hum" background-audio bed the OBS **YouTube**
scene plays in place of the SomaFM source (which is stripped on YouTube — see
`entrypoint.sh`). Nothing is committed to git: `render-variants.sh` renders
four seamless-looping variant FLACs (`idle`, `highway`, `backroad`,
`mountain`) at Docker **build time** into `/opt/tripbot/assets/carhum/`, and
tripbot's `!carsound` command cycles among them live.

## Rendering the variants

`render-variants.sh <out-dir>` runs `carhum.py` once per preset with a fixed
seed (reproducible builds), then encodes each WAV to FLAC:

```sh
task carhum:render          # from the repo root → carhum/out/
```

- The variant names + count are a contract shared with the `carhum` builder
  stage in `Dockerfile{,.arm64}` and the `carSounds` registry in tripbot's
  `pkg/chatbot/carsound.go` — keep all three in sync (see the main README).
- `--loop 6` crossfades the tail back over the head so each file loops with
  **no audible seam** when OBS repeats it.
- FLAC keeps the loop gapless (no encoder padding) and compresses this
  low-frequency signal to a few MB.

`uv` reads the inline PEP 723 dependency block at the top of the script, so no
separate venv setup is needed.

## Why it doesn't sound digital

Each layer breathes on its own slow LFO, the engine fundamental wanders on a
smoothed random walk, and the two stereo channels use independent noise for
natural width — so nothing reads as a static, looping synth tone.
